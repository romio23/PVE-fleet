"""Haalt periodiek de toestand van alle servers op en bouwt één 'fleet'-overzicht.

Per ronde: per server 3 API-calls (cluster/status, cluster/resources,
cluster/tasks) plus nodes/<node>/status per online node. Pakketupdates worden
veel minder vaak opgevraagd (updates_interval).
"""
import json
import logging
import os
import re
import threading
import time
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

from . import guestip
from .demo import FakeClient
from .pve import PveClient, PveError, PvePermissionError

log = logging.getLogger(__name__)


def pct(a, b):
    return round(100.0 * a / b, 1) if b else 0.0


def _version(pveversion):
    m = re.search(r"pve-manager/([\d.]+)", pveversion or "")
    return m.group(1) if m else None


def _verkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


KERNEL_PKG = re.compile(r"^(?:proxmox|pve)-kernel-(\d+\.\d+\.\d+-\d+-pve)(?:-signed)?$")


def kernel_state(versions, running):
    """Uit /apt/versions: nieuwste geïnstalleerde kernel en of een herstart nodig is."""
    installed = []
    for p in versions or []:
        m = KERNEL_PKG.match(p.get("Package") or "")
        if m and (p.get("CurrentState") in (None, "", "Installed")):
            installed.append(m.group(1))
        if p.get("RunningKernel") and not running:
            running = p["RunningKernel"]
    latest = max(installed, key=_verkey) if installed else None
    need = bool(latest and running and _verkey(latest) > _verkey(running))
    return {"kernel_running": running, "kernel_latest": latest, "reboot_needed": need}


def nodes_needing_update(nodes):
    """Nodes met updates, een herstart nodig, of die achterlopen op de nieuwste node van hun cluster."""
    on = [n for n in nodes if n["status"] == "online"]
    newest_v = max((n["version"] for n in on if n.get("version")), key=_verkey, default=None)
    kern = lambda n: ((n.get("updates") or {}).get("kernel_latest") or n.get("kernel"))
    newest_k = max((kern(n) for n in on if kern(n)), key=_verkey, default=None)
    out = []
    for n in on:
        u = n.get("updates") or {}
        behind = (newest_v and n.get("version") and _verkey(n["version"]) < _verkey(newest_v)) or \
                 (newest_k and kern(n) and _verkey(kern(n)) < _verkey(newest_k))
        if u.get("count") or u.get("reboot_needed") or behind:
            out.append(n)
    return out


def _guest_work():
    from . import guestupdates
    try:
        return guestupdates.work_stats()
    except Exception:  # pragma: no cover
        return {"containers": 0, "security": 0, "packages": 0, "apps": 0}


def _kernel(kversion):
    m = re.search(r"Linux (\S+)", kversion or "")
    return m.group(1) if m else None


class Collector:
    def __init__(self, cfg, data_dir="data"):
        self.cfg = cfg
        self.data_dir = data_dir
        self.lock = threading.Lock()
        self.clients = self._make_clients(cfg)
        self.groups = {}          # server -> laatst gebouwde groep
        self.last_ok = {}         # server -> laatste geslaagde groep (voor offline-weergave)
        self.fail_since = {}      # server -> tijd van eerste mislukte poll
        self.updates = {}         # (server, node) -> dict
        self.alert_since = {}
        self.acks = self._load_acks()
        self.snapshot = {"ready": False, "demo": cfg["demo"], "groups": [], "alerts": [], "kpis": {}}
        self._gen = 0             # verhoogd bij herconfiguratie; oude rondes publiceren dan niet meer
        self.extra_alerts = {}    # id -> (level, tekst, sinds, server); bijv. geblokkeerde kiosk
        self._build_lock = threading.Lock()
        self._stop = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=8)
        self.guest_ips = {}       # (server, type, vmid) -> {"ips", "src", "checked", "running", "node"}
        self._ip_pool = ThreadPoolExecutor(max_workers=4)
        self._ip_busy = set()
        self.guest_cache = self._load_guest_cache()   # "server|type|vmid" -> laatst bekende gegevens
        self._cache_saved = 0
        self._cache_lock = threading.RLock()

    @staticmethod
    def _make_clients(cfg):
        clients = {}
        for s in cfg["servers"]:
            if cfg["demo"]:
                clients[s["name"]] = FakeClient(s["name"])
            else:
                clients[s["name"]] = PveClient(s["hosts"], s["token_id"], s["token_secret"],
                                               port=s["port"], verify_ssl=s["verify_ssl"])
        return clients

    def reconfigure(self, new_cfg):
        """Nieuwe config overnemen zonder herstart (na opslaan in Servers & verbinding)."""
        old_names = set(self.clients)
        self._gen += 1
        self.cfg.clear()
        self.cfg.update(new_cfg)          # zelfde dict-object: de rest van de app ziet het meteen
        self.clients = self._make_clients(self.cfg)
        for name in old_names | set(self.clients):
            # alles van deze server vergeten; bij de volgende ronde begint hij schoon
            self.last_ok.pop(name, None)
            self.fail_since.pop(name, None)
            for key in [k for k in self.updates if k[0] == name]:
                self.updates.pop(key, None)
        with self.lock:
            self.snapshot = {"ready": False, "demo": self.cfg["demo"], "groups": [], "alerts": [], "kpis": {}}
        threading.Thread(target=self._safe_poll, name="reconfigure-poll", daemon=True).start()

    def _safe_poll(self):
        try:
            self.poll_all()
        except Exception:  # pragma: no cover
            log.exception("poll na herconfiguratie")

    # ---------- levenscyclus ----------
    def start(self):
        t = threading.Thread(target=self._loop, name="collector", daemon=True)
        t.start()

    def _loop(self):
        while not self._stop.is_set():
            started = time.time()
            try:
                self.poll_all()
            except Exception:  # nooit de thread laten sterven
                log.exception("poll-ronde mislukt")
            self._stop.wait(max(1.0, self.cfg["poll_interval"] - (time.time() - started)))

    def client(self, server):
        return self.clients.get(server)

    # ---------- polling ----------
    def poll_all(self):
        gen = self._gen
        clients = self.clients
        servers = list(self.cfg["servers"])
        futures = {name: self._pool.submit(self.poll_server, name) for name in clients}
        groups = []
        for s in servers:
            try:
                groups.append(futures[s["name"]].result(timeout=60))
            except Exception as e:
                log.exception("server %s", s["name"])
                groups.append(self._offline_group(s["name"], str(e)))
        if gen == self._gen:
            self._publish(groups)

    def _publish(self, groups):
        with self._build_lock:
            snap = self._build_snapshot(groups)
            with self.lock:
                self.snapshot = snap

    def refresh(self, server):
        """Eén server meteen opnieuw ophalen (na een actie of 'Opnieuw verbinden')."""
        if server not in self.clients:
            return
        try:
            g = self.poll_server(server)
        except Exception as e:  # pragma: no cover
            g = self._offline_group(server, str(e))
        with self.lock:
            groups = list(self.snapshot.get("_full", []))
        groups = [g if x["server"] == server else x for x in groups] or [g]
        self._publish(groups)

    def server_cfg(self, server):
        return next((s for s in self.cfg["servers"] if s["name"] == server), None)

    def set_extra_alert(self, aid, level, text, server=None):
        if aid not in self.extra_alerts:
            self.extra_alerts[aid] = (level, text, time.time(), server)

    def clear_extra_alert(self, aid):
        self.extra_alerts.pop(aid, None)

    def poll_server(self, name):
        c = self.clients[name]
        try:
            status = c.get("/cluster/status") or []
            resources = c.get("/cluster/resources") or []
        except PveError as e:
            self.fail_since.setdefault(name, time.time())
            return self._offline_group(name, str(e))
        self.fail_since.pop(name, None)
        try:
            tasks = c.get("/cluster/tasks") or []
        except PveError:
            tasks = []

        cluster = next((x for x in status if x.get("type") == "cluster"), None)
        node_entries = {x["name"]: x for x in status if x.get("type") == "node"}
        res_nodes = {x["node"]: x for x in resources if x.get("type") == "node"}
        guests = [x for x in resources if x.get("type") in ("qemu", "lxc") and not x.get("template")]
        storage = [x for x in resources if x.get("type") == "storage"]

        nodes = []
        for nname in sorted(set(node_entries) | set(res_nodes)):
            ent = node_entries.get(nname, {})
            r = res_nodes.get(nname, {})
            online = (ent.get("online", 1) == 1) and r.get("status", "online") == "online"
            mine = [g for g in guests if g.get("node") == nname]
            node = {
                "server": name, "name": nname, "status": "online" if online else "offline", "stale": False,
                "ip": ent.get("ip"),
                "cpu": round(100 * (r.get("cpu") or 0), 1) if online else None,
                "cpus": r.get("maxcpu"),
                "mem_used": r.get("mem") if online else None, "mem_total": r.get("maxmem"),
                "mem_pct": pct(r.get("mem") or 0, r.get("maxmem") or 0) if online else None,
                "uptime": r.get("uptime") if online else None,
                "guests_running": sum(1 for g in mine if g.get("status") == "running"),
                "guests_total": len(mine),
                "version": None, "kernel": None, "model": None, "loadavg": None,
                "updates": self.updates.get((name, nname)),
            }
            if online:
                try:
                    st = c.get("/nodes/{}/status".format(nname)) or {}
                    node["version"] = _version(st.get("pveversion"))
                    node["kernel"] = _kernel(st.get("kversion"))
                    node["model"] = (st.get("cpuinfo") or {}).get("model")
                    node["loadavg"] = st.get("loadavg")
                except PveError as e:
                    log.debug("status %s/%s: %s", name, nname, e)
                node["updates"] = self._maybe_updates(c, name, nname, node["kernel"])
            nodes.append(node)

        group = {
            "server": name,
            "name": cluster["name"] if cluster else (nodes[0]["name"] if nodes else name),
            "kind": "cluster" if cluster else "standalone",
            "reachable": True, "error": None, "since": None,
            "quorate": bool(cluster.get("quorate")) if cluster else None,
            "nodes_total": len(nodes), "nodes_online": sum(1 for n in nodes if n["status"] == "online"),
            "web_url": c.web_url(), "active_host": c.active_host,
            "nodes": nodes,
            "guests": self._apply_guest_cache(name, [self._with_ip(self._guest_row(name, g)) for g in guests],
                                             {n["name"] for n in nodes if n["status"] != "online"}),
            "storage": [self._storage_row(name, s) for s in storage],
            "tasks": [self._task_row(name, t) for t in tasks[:40]],
            "polled": time.time(),
        }
        if not cluster and nodes and not nodes[0]["ip"]:
            nodes[0]["ip"] = c.active_host   # losse node: het adres waarmee we verbinden
        self._schedule_ips(name, c, guests)
        self.last_ok[name] = group
        return group

    # ---------- IP-adressen van VM's & containers (op de achtergrond, met cache) ----------
    IP_EVERY_RUNNING = 120   # seconden
    IP_EVERY_STOPPED = 900

    def _with_ip(self, row):
        hit = self.guest_ips.get((row["server"], row["type"], row["vmid"]))
        row["ips"] = hit["ips"] if hit else []
        row["ip"] = row["ips"][0] if row["ips"] else None
        row["ip_src"] = hit["src"] if hit else None
        return row

    def _schedule_ips(self, server, c, guests):
        now = time.time()
        seen = set()
        for g in guests:
            key = (server, g.get("type"), g.get("vmid"))
            seen.add(key)
            running = g.get("status") == "running"
            hit = self.guest_ips.get(key)
            fresh = hit and hit["running"] == running and hit["node"] == g.get("node") and \
                now - hit["checked"] < (self.IP_EVERY_RUNNING if running else self.IP_EVERY_STOPPED)
            if fresh or key in self._ip_busy:
                continue
            self._ip_busy.add(key)
            self._ip_pool.submit(self._fetch_ip, c, key, g.get("node"), running)
        for key in [k for k in self.guest_ips if k[0] == server and k not in seen]:
            self.guest_ips.pop(key, None)

    def _fetch_ip(self, c, key, node, running):
        try:
            ips, src, _err = guestip.lookup(c, node, key[1], key[2], running)
            self.guest_ips[key] = {"ips": ips, "src": src, "checked": time.time(), "running": running, "node": node}
        except Exception as e:  # pragma: no cover
            log.debug("ip %s: %s", key, e)
        finally:
            self._ip_busy.discard(key)

    def _maybe_updates(self, c, server, node, running_kernel=None, force=False):
        key = (server, node)
        cur = self.updates.get(key)
        if cur and not force and time.time() - cur["checked"] < self.cfg.get("updates_interval", 1800) \
                and (not running_kernel or cur.get("kernel_running") in (None, running_kernel)):
            return cur
        try:
            pk = c.get("/nodes/{}/apt/update".format(node)) or []
            cur = {"kernel_pending": any(re.match(r"^(proxmox|pve)-kernel-", p.get("Package") or "") for p in pk),
                   "count": len(pk), "security": sum(1 for p in pk if "security" in (p.get("Origin") or "").lower()),
                   "checked": time.time(), "error": None}
        except PvePermissionError:
            cur = {"count": None, "security": None, "checked": time.time(),
                   "error": "geen rechten om de lijst met updates te lezen (Proxmox vraagt hiervoor Sys.Modify)"}
        except PveError as e:
            cur = {"count": None, "security": None, "checked": time.time(), "error": str(e)[:120]}
        try:
            cur.update(kernel_state(c.get("/nodes/{}/apt/versions".format(node)) or [], running_kernel))
        except PveError:
            cur.update({"kernel_running": running_kernel, "kernel_latest": None, "reboot_needed": False})
        self.updates[key] = cur
        return cur

    def check_updates(self, server, node, refresh_list=False):
        """Meteen opnieuw kijken naar updates van één node (na bijwerken).
        refresh_list: eerst de pakketlijst laten vernieuwen (apt update, recht Sys.Modify)."""
        c = self.clients.get(server)
        if not c:
            return None, None
        note = None
        if refresh_list:
            try:
                upid = c.post("/nodes/{}/apt/update".format(node))
                if isinstance(upid, str) and upid.startswith("UPID"):
                    for _ in range(60):   # wacht tot apt update klaar is (max ~60 s)
                        st = c.get("/nodes/{}/tasks/{}/status".format(node, quote(upid, safe=""))) or {}
                        if st.get("status") == "stopped":
                            break
                        time.sleep(1)
            except PvePermissionError:
                note = ("De pakketlijst is opnieuw gelezen. Proxmox vernieuwt die lijst zelf elke dag; om dat meteen te doen "
                        "heeft het token het recht Sys.Modify nodig.")
            except PveError as e:
                note = "Pakketlijst vernieuwen lukte niet: {}".format(e)
        kernel = None
        try:
            kernel = _kernel((c.get("/nodes/{}/status".format(node)) or {}).get("kversion"))
        except PveError:
            pass
        cur = self._maybe_updates(c, server, node, kernel, force=True)
        self.refresh(server)
        return cur, note

    def _offline_group(self, name, error):
        prev = self.last_ok.get(name)
        since = self.fail_since.get(name, time.time())
        if prev:
            g = json.loads(json.dumps(prev))  # diepe kopie
            for n in g["nodes"]:
                n["status"] = "offline"
                n["stale"] = True
            for gu in g["guests"]:
                gu["status"] = "unknown"
                gu["node_offline"] = True
                hit = self.guest_cache.get(self._gkey(name, gu["type"], gu["vmid"]))
                if hit:
                    gu["last_seen"] = hit.get("seen")
            g["nodes_online"] = 0
        else:
            cfg = self.server_cfg(name) or {}
            is_cluster = cfg.get("type") == "cluster" or (cfg.get("type") is None and len(cfg.get("hosts") or []) > 1)
            placeholder = [] if is_cluster else [{
                "server": name, "name": name, "status": "offline", "stale": False, "ip": (cfg.get("hosts") or [None])[0],
                "cpu": None, "cpus": None, "mem_used": None, "mem_total": None, "mem_pct": None, "uptime": None,
                "guests_running": 0, "guests_total": 0, "version": None, "kernel": None, "model": None,
                "loadavg": None, "updates": None}]
            guests = self._guests_from_cache(name)
            if is_cluster:     # nodes uit de cache, zodat de containers ergens bij horen
                for nn in sorted({x["node"] for x in guests if x.get("node")}):
                    placeholder.append({
                        "server": name, "name": nn, "status": "offline", "stale": True, "ip": None,
                        "cpu": None, "cpus": None, "mem_used": None, "mem_total": None, "mem_pct": None, "uptime": None,
                        "guests_running": 0, "guests_total": sum(1 for x in guests if x["node"] == nn), "version": None,
                        "kernel": None, "model": None, "loadavg": None, "updates": None})
            elif placeholder:
                placeholder[0]["guests_total"] = len(guests)
                for x in guests:
                    x["node"] = placeholder[0]["name"] if not x.get("node") else x["node"]
            g = {"server": name, "name": name, "kind": "cluster" if is_cluster else "standalone", "quorate": None,
                 "nodes_total": len(placeholder), "nodes_online": 0, "web_url": None, "active_host": None,
                 "guests": guests, "storage": [], "tasks": [], "nodes": placeholder}
        g.update({"reachable": False, "error": error, "since": since, "polled": time.time()})
        return g

    # ---------- laatst bekende gegevens van VM's & containers (voor nodes die offline zijn) ----------
    CACHE_FIELDS = ("name", "node", "maxmem", "maxdisk", "cpus", "tags", "ips", "status")

    @staticmethod
    def _gkey(server, kind, vmid):
        return "{}|{}|{}".format(server, kind, vmid)

    def _cache_path(self):
        return os.path.join(self.data_dir, "guest_cache.json")

    def _load_guest_cache(self):
        try:
            with open(self._cache_path(), encoding="utf-8") as fh:
                d = json.load(fh)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_guest_cache(self):
        if self.cfg.get("demo") and not self.cfg.get("demo_cache"):
            return
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            tmp = self._cache_path() + ".tmp"
            with self._cache_lock, open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.guest_cache, fh)
            os.replace(tmp, self._cache_path())
            self._cache_saved = time.time()
        except OSError as e:  # pragma: no cover
            log.warning("guest_cache.json: %s", e)

    def _apply_guest_cache(self, server, rows, offline_nodes):
        """Onthoud de gegevens van gasten op online nodes; vul gasten op offline nodes aan uit de cache."""
        with self._cache_lock:
            return self._apply_guest_cache_locked(server, rows, offline_nodes)

    def _apply_guest_cache_locked(self, server, rows, offline_nodes):
        now, seen, changed = time.time(), set(), False
        for r in rows:
            key = self._gkey(server, r["type"], r["vmid"])
            seen.add(key)
            hit = self.guest_cache.get(key)
            if r["node"] in offline_nodes or r["status"] == "unknown":
                r["node_offline"] = True
                if hit:
                    if r.get("noname") and hit.get("name"):
                        r["name"] = hit["name"]
                    for f in ("maxmem", "maxdisk", "cpus"):
                        if not r.get(f) and hit.get(f):
                            r[f] = hit[f]
                    if not r.get("tags") and hit.get("tags"):
                        r["tags"] = list(hit["tags"])
                    if not r.get("ips") and hit.get("ips"):
                        r["ips"], r["ip"], r["ip_src"] = list(hit["ips"]), hit["ips"][0], "cache"
                    r["last_seen"] = hit.get("seen")
                    r["cached"] = True
                if r.get("ips"):
                    r["ip_src"] = "cache"
                continue
            if r.get("noname"):
                continue
            entry = {"name": r["name"], "node": r["node"], "maxmem": r.get("maxmem"), "maxdisk": r.get("maxdisk"),
                     "cpus": r.get("cpus"), "tags": r.get("tags") or [], "status": r.get("status"),
                     "ips": r.get("ips") or (hit or {}).get("ips") or [], "seen": now}
            if not hit or any(hit.get(k) != entry[k] for k in self.CACHE_FIELDS):
                changed = True
            self.guest_cache[key] = entry
        for key in [k for k in self.guest_cache if k.startswith(server + "|") and k not in seen]:
            self.guest_cache.pop(key, None)          # verwijderd uit Proxmox
            changed = True
        if changed or now - self._cache_saved > 600:
            self._save_guest_cache()
        return rows

    def _guests_from_cache(self, server):
        out = []
        with self._cache_lock:
            items = list(self.guest_cache.items())
        for key, c in items:
            srv, kind, vmid = key.rsplit("|", 2)
            if srv != server:
                continue
            ips = list(c.get("ips") or [])
            out.append({"server": server, "node": c.get("node"), "type": kind, "vmid": int(vmid), "name": c.get("name"),
                        "status": "unknown", "cpu": 0, "cpus": c.get("cpus"), "mem": None, "maxmem": c.get("maxmem"),
                        "maxdisk": c.get("maxdisk"), "uptime": None, "tags": list(c.get("tags") or []),
                        "ips": ips, "ip": ips[0] if ips else None, "ip_src": "cache" if ips else None,
                        "node_offline": True, "cached": True, "last_seen": c.get("seen")})
        out.sort(key=lambda x: x["vmid"])
        return out

    @staticmethod
    def _guest_row(server, g):
        return {"server": server, "node": g.get("node"), "type": g.get("type"), "vmid": g.get("vmid"),
                "name": g.get("name") or "{}-{}".format(g.get("type"), g.get("vmid")),
                "status": g.get("status"), "cpu": round(100 * (g.get("cpu") or 0), 1), "cpus": g.get("maxcpu"),
                "mem": g.get("mem"), "maxmem": g.get("maxmem"), "maxdisk": g.get("maxdisk"), "uptime": g.get("uptime"),
                "noname": not g.get("name"),
                "tags": [t for t in re.split(r"[;, ]", g.get("tags") or "") if t]}

    @staticmethod
    def _storage_row(server, s):
        return {"server": server, "node": s.get("node"), "name": s.get("storage"), "type": s.get("plugintype"),
                "used": s.get("disk"), "total": s.get("maxdisk"), "pct": pct(s.get("disk") or 0, s.get("maxdisk") or 0),
                "shared": bool(s.get("shared")), "content": s.get("content") or "", "status": s.get("status")}

    @staticmethod
    def _task_row(server, t):
        st = t.get("status")
        return {"server": server, "node": t.get("node"), "type": t.get("type"), "id": t.get("id"),
                "user": t.get("user"), "start": t.get("starttime"), "end": t.get("endtime"),
                "status": st, "ok": st == "OK", "running": not t.get("endtime")}

    # ---------- overzicht + alerts ----------
    def _build_snapshot(self, groups):
        th = self.cfg["thresholds"]
        now = time.time()
        alerts = []

        def alert(aid, level, text, server, node=None):
            since = self.alert_since.setdefault(aid, now)
            alerts.append({"id": aid, "level": level, "text": text, "server": server, "node": node,
                           "since": since, "acked": aid in self.acks})

        all_nodes, all_guests, seen_storage = [], [], {}
        for g in groups:
            all_nodes += g["nodes"]
            all_guests += [x for x in g["guests"] if g["reachable"]]
            if not g["reachable"]:
                alert("unreachable:" + g["server"], "crit", "{} onbereikbaar".format(g["name"]), g["server"])
                continue
            if g["kind"] == "cluster":
                if not g["quorate"]:
                    alert("quorum:" + g["server"], "crit", "{}: geen quorum".format(g["name"]), g["server"])
            for n in g["nodes"]:
                if n["status"] != "online":
                    alert("offline:{}:{}".format(g["server"], n["name"]), "crit", "{} is offline".format(n["name"]), g["server"], n["name"])
                    continue
                if n["mem_pct"] is not None and n["mem_pct"] >= th["ram_warn"]:
                    lvl = "crit" if n["mem_pct"] >= th["ram_crit"] else "warn"
                    alert("ram:{}:{}".format(g["server"], n["name"]), lvl, "RAM op {} {:.0f}%".format(n["name"], n["mem_pct"]), g["server"], n["name"])
                if n["cpu"] is not None and n["cpu"] >= th["cpu_warn"]:
                    alert("cpu:{}:{}".format(g["server"], n["name"]), "warn", "CPU op {} {:.0f}%".format(n["name"], n["cpu"]), g["server"], n["name"])
            for s in g["storage"]:
                key = (g["server"], s["name"]) if s["shared"] else (g["server"], s["node"], s["name"])
                if key in seen_storage:
                    continue
                seen_storage[key] = s
                if s["total"] and s["pct"] >= th["storage_warn"]:
                    lvl = "crit" if s["pct"] >= th["storage_crit"] else "warn"
                    where = s["name"] if s["shared"] else "{} op {}".format(s["name"], s["node"])
                    alert("storage:{}:{}".format(g["server"], "/".join(key[1:])), lvl, "{} {:.0f}% vol".format(where, s["pct"]), g["server"], s["node"])
            for t in g["tasks"]:
                if t["type"] == "vzdump" and t["end"] and not t["ok"] and t["start"] and now - t["start"] < 86400:
                    label = "VM/CT {}".format(t["id"]) if t["id"] else "job"
                    alert("backup:{}:{}:{}".format(g["server"], t["node"], t["start"]), "warn",
                          "Back-up mislukt: {} op {}".format(label, t["node"]), g["server"], t["node"])

        for aid, (lvl, text, since, server) in list(self.extra_alerts.items()):
            self.alert_since.setdefault(aid, since)
            alert(aid, lvl, text, server)

        active = {a["id"] for a in alerts}
        for aid in list(self.alert_since):
            if aid not in active:
                self.alert_since.pop(aid, None)
        if self.acks - active:
            self.acks &= active
            self._save_acks()
        alerts.sort(key=lambda a: (a["acked"], 0 if a["level"] == "crit" else 1, -a["since"]))

        online = [n for n in all_nodes if n["status"] == "online"]
        cpu_cap = sum(n["cpus"] or 0 for n in online)
        mem_tot = sum(n["mem_total"] or 0 for n in online)
        st_used = sum(s["used"] or 0 for s in seen_storage.values())
        st_tot = sum(s["total"] or 0 for s in seen_storage.values())
        running = [x for x in all_guests if x["status"] == "running"]
        tasks = sorted([t for g in groups for t in g["tasks"]], key=lambda t: -(t["start"] or 0))
        backups = [t for t in tasks if t["type"] == "vzdump" and t["start"] and now - t["start"] < 86400 and t["end"]]
        gw = _guest_work()
        kpis = {
            "nodes_online": len(online), "nodes_total": len(all_nodes),
            "guests_running": len(running), "guests_total": len(all_guests),
            "vm_running": sum(1 for x in running if x["type"] == "qemu"),
            "lxc_running": sum(1 for x in running if x["type"] == "lxc"),
            "cpu": pct(sum((n["cpu"] or 0) * (n["cpus"] or 0) / 100.0 for n in online), cpu_cap), "cores": cpu_cap,
            "mem": pct(sum(n["mem_used"] or 0 for n in online), mem_tot), "mem_used": sum(n["mem_used"] or 0 for n in online), "mem_total": mem_tot,
            "storage": pct(st_used, st_tot), "storage_used": st_used, "storage_total": st_tot,
            "alerts_crit": sum(1 for a in alerts if a["level"] == "crit" and not a["acked"]),
            "alerts_warn": sum(1 for a in alerts if a["level"] == "warn" and not a["acked"]),
            "backups_ok": sum(1 for t in backups if t["ok"]), "backups_failed": sum(1 for t in backups if not t["ok"]),
            "updates": sum((n["updates"] or {}).get("count") or 0 for n in all_nodes),
            "updates_security": sum((n["updates"] or {}).get("security") or 0 for n in all_nodes),
            "updates_nodes": sum(1 for n in all_nodes if ((n["updates"] or {}).get("count") or 0) > 0),
            "reboot_nodes": sum(1 for n in all_nodes if (n["updates"] or {}).get("reboot_needed")),
            "update_work": sum(len(nodes_needing_update(g["nodes"])) for g in groups if g["reachable"]),
            "guest_update_work": gw["containers"],
            "guest_update_security": gw["security"],
            "guest_update_apps": gw["apps"],
            "backup_failed": sum(1 for a in alerts if a["id"].startswith("backup:")),
        }
        slim = []
        for g in groups:
            sg = {k: v for k, v in g.items() if k not in ("guests", "storage", "tasks")}
            slim.append(sg)
        return {"ready": True, "demo": self.cfg["demo"], "time": now, "poll_interval": self.cfg["poll_interval"],
                "groups": slim, "alerts": alerts, "kpis": kpis, "tasks": tasks[:40], "_full": groups}

    # ---------- publieke helpers ----------
    def get_snapshot(self):
        with self.lock:
            snap = dict(self.snapshot)
        snap.pop("_full", None)
        return snap

    def group(self, server):
        with self.lock:
            for g in self.snapshot.get("_full", []):
                if g["server"] == server:
                    return g
        return None

    def ack(self, aid, on=True):
        if on:
            self.acks.add(aid)
        else:
            self.acks.discard(aid)
        self._save_acks()
        with self.lock:
            for a in self.snapshot.get("alerts", []):
                if a["id"] == aid:
                    a["acked"] = on
            k = self.snapshot.get("kpis", {})
            if k:
                k["alerts_crit"] = sum(1 for a in self.snapshot["alerts"] if a["level"] == "crit" and not a["acked"])
                k["alerts_warn"] = sum(1 for a in self.snapshot["alerts"] if a["level"] == "warn" and not a["acked"])

    def _acks_file(self):
        return os.path.join(self.data_dir, "state.json")

    def _load_acks(self):
        try:
            with open(self._acks_file(), encoding="utf-8") as fh:
                return set(json.load(fh).get("acked_alerts", []))
        except (OSError, ValueError):
            return set()

    def _save_acks(self):
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            tmp = self._acks_file() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"acked_alerts": sorted(self.acks)}, fh)
            os.replace(tmp, self._acks_file())
        except OSError as e:
            log.warning("kon state.json niet schrijven: %s", e)
