"""Demomodus: nep-Proxmox die dezelfde API-antwoorden geeft als een echte server.

Zo draait de rest van de app (collector, alerts, schermen, acties) precies zoals
in het echt, zonder dat je API-tokens nodig hebt. Acties werken ook: VM's starten
en stoppen, snapshots en back-ups verschijnen, migraties verplaatsen VM's & containers en
een herstarte node is even offline. Alles zit in het geheugen en is na een
herstart van het dashboard weer terug bij af.
"""
import itertools
import math
import re
import threading
import time

from .pve import PveError

GB = 1024 ** 3
START = time.time()
_lock = threading.RLock()
_pid = itertools.count(0x1A2B00)

# server -> (is_cluster, {node: spec})
NODES = {
    "cluster-home": (True, {
        "pve-01": dict(ip="192.168.1.11", cpus=12, mem=64, cpu=0.22, used=0.54, up=41, model="Intel Core i5-12500", ver="9.0.10", updates=5),
        "pve-02": dict(ip="192.168.1.12", cpus=16, mem=64, cpu=0.47, used=0.83, up=41, model="AMD Ryzen 7 5700G", ver="9.0.10", updates=3),
        "pve-03": dict(ip="192.168.1.13", cpus=12, mem=64, cpu=0.18, used=0.49, up=12, model="Intel Core i5-12500", ver="9.0.10", updates=0),
    }),
    "cluster-lab": (True, {
        "pve-lab-01": dict(ip="10.0.20.21", cpus=12, mem=32, cpu=0.64, used=0.71, up=6, model="Intel Xeon E-2236", ver="9.0.6", updates=9),
        "pve-lab-02": dict(ip="10.0.20.22", cpus=12, mem=32, cpu=0.02, used=0.12, up=0.13, model="Intel Xeon E-2236", ver="9.0.10", updates=0),
    }),
    "pve-garage": (False, {
        "pve-garage": dict(ip="192.168.1.40", cpus=4, mem=16, cpu=0.09, used=0.37, up=58, model="Intel N100", ver="8.4.14", updates=6),
    }),
    "pve-mini": (False, {}),  # staat uit; met Wake-on-LAN komt hij na ~20 s online
}
MINI = {"pve-mini": dict(ip="192.168.1.41", cpus=8, mem=32, cpu=0.05, used=0.31, up=0, model="Intel N305", ver="9.0.10", updates=0)}

# (vmid, naam, type, node, running, cpu-fractie, mem GB gebruikt, mem GB max, tags)
GUESTS = [
    (100, "proxy", "lxc", "pve-01", True, 0.02, 0.3, 1, ""),
    (102, "nextcloud", "qemu", "pve-01", True, 0.09, 6.1, 8, "web"),
    (103, "postgres", "lxc", "pve-01", True, 0.04, 3.8, 4, "db"),
    (106, "grafana", "lxc", "pve-01", True, 0.02, 0.7, 2, "monitoring"),
    (101, "homeassistant", "qemu", "pve-02", True, 0.06, 3.1, 4, "home-automation"),
    (104, "modelarr", "lxc", "pve-02", True, 0.02, 0.6, 2, "3d;web"),
    (105, "bambuddy", "lxc", "pve-02", True, 0.01, 0.4, 1, "3d"),
    (110, "docker-host", "qemu", "pve-02", True, 0.31, 12.4, 16, "docker"),
    (112, "jellyfin", "lxc", "pve-02", True, 0.18, 5.2, 8, "media"),
    (115, "pihole", "lxc", "pve-02", True, 0.01, 0.2, 0.5, "dns"),
    (120, "win11-test", "qemu", "pve-02", False, 0.12, 5.5, 8, "test"),
    (130, "gitea", "lxc", "pve-02", True, 0.03, 0.9, 2, "dev"),
    (140, "minecraft", "qemu", "pve-03", True, 0.14, 7.8, 10, "games"),
    (141, "paperless", "lxc", "pve-03", True, 0.02, 1.1, 2, ""),
    (142, "immich", "qemu", "pve-03", True, 0.06, 5.4, 8, "media"),
    (144, "k3s-test", "qemu", "pve-03", False, 0.2, 2.5, 4, "test"),
    (200, "opnsense-lab", "qemu", "pve-lab-01", True, 0.08, 2.2, 4, "net"),
    (202, "ansible", "lxc", "pve-lab-01", True, 0.01, 0.4, 1, ""),
    (203, "k8s-node-a", "qemu", "pve-lab-01", True, 0.41, 7.6, 8, "k8s"),
    (204, "k8s-node-b", "qemu", "pve-lab-01", False, 0.3, 6.0, 8, "k8s"),
    (201, "scratch", "lxc", "pve-garage", True, 0.01, 0.3, 1, ""),
    (210, "frigate", "qemu", "pve-garage", True, 0.22, 4.9, 6, "camera"),
    (301, "adguard", "lxc", "pve-mini", True, 0.01, 0.2, 0.5, "dns"),
    (302, "wireguard", "lxc", "pve-mini", True, 0.01, 0.1, 0.5, "net"),
    (303, "mqtt", "lxc", "pve-mini", True, 0.01, 0.3, 1, "home-automation"),
]
INFO = {g[0]: dict(name=g[1], kind=g[2], cpu=g[5], mem=g[6], maxmem=g[7], tags=g[8]) for g in GUESTS}

STORAGE = {
    "pve-01": [("local", "dir", 26, 94, 0), ("local-zfs", "zfspool", 1100, 1800, 0), ("synology-nfs", "nfs", 9800, 16000, 1)],
    "pve-02": [("local", "dir", 32, 94, 0), ("local-zfs", "zfspool", 1480, 1800, 0), ("synology-nfs", "nfs", 9800, 16000, 1)],
    "pve-03": [("local", "dir", 21, 94, 0), ("local-zfs", "zfspool", 790, 1800, 0), ("synology-nfs", "nfs", 9800, 16000, 1)],
    "pve-lab-01": [("local", "dir", 44, 94, 0), ("local-lvm", "lvmthin", 700, 920, 0), ("synology-nfs", "nfs", 9800, 16000, 1)],
    "pve-lab-02": [("local", "dir", 29, 94, 0), ("local-lvm", "lvmthin", 170, 920, 0), ("synology-nfs", "nfs", 9800, 16000, 1)],
    "pve-garage": [("local", "dir", 96, 234, 0), ("local-usb", "dir", 980, 1860, 0)],
    "pve-mini": [("local", "dir", 36, 94, 0), ("local-zfs", "zfspool", 270, 920, 0)],
}
BACKUP_STORES = {"synology-nfs", "local-usb"}

# ---- veranderlijke toestand ----
STATE = {
    "guests": {g[0]: {"running": g[4], "node": g[3], "since": START - (9 if g[0] in (104, 105) else 41) * 86400} for g in GUESTS},
    "snaps": {},          # vmid -> [ {name, description, snaptime} ]
    "backups": {},        # vmid -> [ {storage, ctime, size} ]
    "tasks": [],          # dynamische taken
    "down_until": {},     # node -> tijdstip (inf = uitgezet)
    "up_since": {},       # node -> tijdstip na herstart
    "mini_up_at": None,
    "upgraded": set(),    # nodes waarop de demo-upgrade is gedaan
    "upgraded_cts": {},   # node -> set(vmid) na een demo-upgrade in de container
    "kernel": {},         # node -> draaiende kernel (na herstart)
    "jobs": {
        "cluster-home": [
            {"id": "backup-6c1f2a9e", "type": "vzdump", "schedule": "02:00", "storage": "synology-nfs", "all": 1,
             "exclude": "120,144", "mode": "snapshot", "compress": "zstd", "enabled": 1,
             "prune-backups": "keep-daily=7,keep-weekly=4,keep-monthly=3", "notes-template": "{{guestname}}",
             "comment": "Everything, every night"},
            {"id": "backup-0d7b44c1", "type": "vzdump", "schedule": "sun 03:30", "storage": "synology-nfs",
             "vmid": "101,110", "mode": "stop", "compress": "zstd", "enabled": 0,
             "prune-backups": "keep-last=4", "comment": "Weekly with stop (off)"},
        ],
        "cluster-lab": [
            {"id": "backup-9a3e11f0", "type": "vzdump", "schedule": "sat 04:00", "storage": "synology-nfs", "all": 1,
             "mode": "snapshot", "compress": "zstd", "enabled": 1, "prune-backups": "keep-last=3", "comment": "Lab, weekly"},
        ],
        "pve-garage": [
            {"id": "backup-41be7c22", "type": "vzdump", "schedule": "01:00", "storage": "local-usb", "vmid": "210",
             "mode": "snapshot", "compress": "zstd", "enabled": 1, "prune-backups": "keep-daily=5", "node": "pve-garage",
             "comment": "Camera's"},
        ],
    },
}
KERNEL_OLD, KERNEL_NEW = "6.14.8-2-pve", "6.14.11-1-pve"
KERNEL_PVE8 = "6.8.12-13-pve"
STATE["upgraded"].add("pve-03")        # pve-03 is al bijgewerkt maar nog niet herstart


def _kernel_running(node, spec):
    if spec["ver"].startswith("8"):
        return KERNEL_PVE8
    return STATE["kernel"].get(node, KERNEL_OLD)


def _kernel_installed(node, spec):
    if spec["ver"].startswith("8"):
        return [KERNEL_PVE8]
    return [KERNEL_OLD, KERNEL_NEW] if node in STATE["upgraded"] else [KERNEL_OLD]


def upgrade(server, node):
    """Demo: 'apt dist-upgrade' op een node. Daarna zijn er geen updates meer, maar wel een nieuwe kernel."""
    with _lock:
        STATE["upgraded"].add(node)


def _job_vmids(job, guests):
    if str(job.get("all", 0)) == "1":
        excl = {int(x) for x in str(job.get("exclude") or "").split(",") if x.strip().isdigit()}
        return {v for v in guests if v not in excl}
    return {int(x) for x in str(job.get("vmid") or "").split(",") if x.strip().isdigit()}


def _schedule_next(schedule, n=3):
    """Heel eenvoudige versie van Proxmox' kalender: '[dag[..dag]] HH:MM'."""
    days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    m = re.match(r"^\s*(?:([a-z]{3})(?:\.\.([a-z]{3}))?\s+)?(\d{1,2}):(\d{2})\s*$", schedule or "")
    if not m:
        raise PveError("400 schedule: unable to parse calendar event '{}' (demo)".format(schedule))
    d1, d2, hh, mm = m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
    if (d1 and d1 not in days) or (d2 and d2 not in days) or hh > 23 or mm > 59:
        raise PveError("400 schedule: unable to parse calendar event '{}' (demo)".format(schedule))
    allowed = set(range(7))
    if d1:
        a, b = days.index(d1), days.index(d2 or d1)
        allowed = set(range(a, b + 1)) if a <= b else set(range(a, 7)) | set(range(0, b + 1))
    out, t = [], time.time()
    lt = time.localtime(t)
    base = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0, 0, 0, -1))
    for k in range(0, 400):
        cand = base + k * 86400
        if cand > t and time.localtime(cand).tm_wday in allowed:
            out.append(int(cand))
            if len(out) >= n:
                break
    return out



def _wiggle(base, seed, amp=0.05):
    t = time.time() / 30.0
    return max(0.0, min(1.0, base + amp * math.sin(t + seed) + amp * 0.4 * math.sin(t * 2.7 + seed * 1.3)))


def _node_down(node):
    return time.time() < STATE["down_until"].get(node, 0)


def _snaps(vmid):
    if vmid not in STATE["snaps"]:
        now = time.time()
        STATE["snaps"][vmid] = [{"name": "after-install", "description": "clean base", "snaptime": int(now - 52 * 86400)},
                                {"name": "before-update-oct", "description": "before apt upgrade", "snaptime": int(now - 7 * 86400)}]
    return STATE["snaps"][vmid]


def _tick():
    """Taken afronden waarvan de tijd om is."""
    now = time.time()
    for t in STATE["tasks"]:
        if t["endtime"] is None and now >= t["finish_at"]:
            t["endtime"] = int(t["finish_at"])
            if t.get("fail"):
                t["status"] = t["fail"]
                t["log"].append("TASK ERROR: " + t["fail"])
            else:
                t["status"] = "OK"
                if t.get("on_done"):
                    t["on_done"]()
                t["log"].append("TASK OK")
    STATE["tasks"] = STATE["tasks"][-200:]


def _task(node, ttype, tid, seconds, lines, on_done=None, fail=None):
    now = time.time()
    upid = "UPID:{}:{:08X}:{:08X}:{:08X}:{}:{}:root@pam!fleet:".format(node, next(_pid), int(now) & 0xFFFFFF, int(now), ttype, tid)
    STATE["tasks"].append({"upid": upid, "node": node, "type": ttype, "id": str(tid), "user": "root@pam!fleet",
                           "starttime": int(now), "endtime": None, "status": None, "finish_at": now + seconds,
                           "log": list(lines), "on_done": on_done, "fail": fail})
    return upid


def wake(server, node):
    """Wake-on-LAN in de demo: een uitgezette node komt na ~20 s terug."""
    with _lock:
        if server == "pve-mini" and not STATE["mini_up_at"]:
            STATE["mini_up_at"] = time.time() + 20
            STATE["up_since"]["pve-mini"] = time.time() + 20
            return True
        if STATE["down_until"].get(node) == float("inf"):
            STATE["down_until"][node] = time.time() + 20
            STATE["up_since"][node] = time.time() + 20
            return True
    return False


# Updates in containers (demo): wat 'apt-get -s dist-upgrade' en de versiebestanden zouden laten zien
DEMO_CT_UPDATES = {
    100: ("apt", 4, 1, "Debian GNU/Linux 12 (bookworm)", {}),
    103: ("apt", 0, 0, "Debian GNU/Linux 12 (bookworm)", {}),
    104: ("apt", 2, 0, "Debian GNU/Linux 13 (trixie)", {}),
    105: ("apt", 0, 0, "Debian GNU/Linux 13 (trixie)", {}),
    106: ("apt", 7, 2, "Debian GNU/Linux 12 (bookworm)", {}),
    112: ("apt", 11, 3, "Ubuntu 24.04.3 LTS", {}),
    115: ("apt", 0, 0, "Debian GNU/Linux 12 (bookworm)", {}),
    130: ("apt", 1, 0, "Debian GNU/Linux 13 (trixie)", {"gitea": "1.22.3"}),
    141: ("apt", 3, 1, "Debian GNU/Linux 12 (bookworm)", {"paperless": "v2.14.7"}),
    202: ("apk", 0, 0, "Alpine Linux v3.22", {}),
    201: ("apt", 5, 2, "Debian GNU/Linux 12 (bookworm)", {"vaultwarden": "1.34.3", "vaultwarden_webvault": "v2025.7.0"}),
}
DEMO_LATEST = {"go-gitea/gitea": "v1.24.6", "paperless-ngx/paperless-ngx": "v2.14.7", "dani-garcia/vaultwarden": "1.34.3",
               "dani-garcia/bw_web_builds": "v2025.9.1"}
DEMO_SLUG = {130: "gitea", 141: "paperless-ngx", 201: "vaultwarden"}


DEMO_NOTE = """<div align='center'>
  <a href='https://community-scripts.org' target='_blank' rel='noopener noreferrer'><img src='https://raw.githubusercontent.com/community-scripts/core/main/images/logo-81x112.png' alt='Logo' style='width:81px;height:112px;'/></a>

  <h2 style='font-size: 24px; margin: 20px 0;'>{name} LXC</h2>

  <p style='margin: 16px 0;'><a href='https://community-scripts.org/donate' target='_blank'><img src='https://img.shields.io/badge/%E2%9D%A4%EF%B8%8F-Sponsoring%20%26%20Donations-FF5E5B' alt='Sponsoring and donations' /></a></p>
</div>

## Onderhoud
- Updates: elke **zondag** om 03:00
- Back-up naar `pbs-garage`, 14 dagen bewaard
- Beheer via <https://192.168.1.50:8443>

<script>alert('dit mag nooit uitgevoerd worden')</script>
"""


class FakeClient:
    def __init__(self, server):
        self.server = server
        self.is_cluster = NODES.get(server, (False, {}))[0]
        self.port = 8006

    @property
    def nodes(self):
        if self.server == "pve-mini":
            at = STATE["mini_up_at"]
            return MINI if at and time.time() >= at else {}
        return NODES.get(self.server, (False, {}))[1]

    @property
    def active_host(self):
        n = self.nodes
        if not n:
            return "192.168.1.41"
        up = [s for k, s in n.items() if not _node_down(k)] or list(n.values())
        return up[0]["ip"]

    @property
    def hosts(self):
        return [self.active_host]

    def web_url(self):
        return "https://{}:8006/".format(self.active_host)

    def _check_reachable(self):
        nodes = self.nodes
        if not nodes or all(_node_down(n) for n in nodes):
            raise PveError("geen van de hosts bereikbaar ({}): time-out (demo)".format(self.active_host))

    # ------------------------------------------------------------ lezen
    def get(self, path, params=None):
        with _lock:
            _tick()
            self._check_reachable()
            if path == "/cluster/status":
                return self._cluster_status()
            if path == "/cluster/resources":
                return self._resources()
            if path == "/cluster/tasks":
                return self._tasks()
            if path == "/access/permissions":
                all_privs = ["Sys.Audit", "VM.Audit", "Datastore.Audit", "VM.PowerMgmt", "VM.Snapshot",
                             "VM.Snapshot.Rollback", "VM.Backup", "VM.Migrate", "Datastore.AllocateSpace", "Sys.PowerMgmt",
                             "VM.Console", "Sys.Console", "VM.GuestAgent.Audit", "Sys.Modify"]
                return {"/": {p: 1 for p in all_privs}}
            if path.startswith("/access/users/"):
                return {"privsep": 0}
            if path == "/cluster/backup":
                jobs = [dict(j) for j in STATE["jobs"].get(self.server, [])]
                for j in jobs:
                    if str(j.get("enabled", 1)) == "1":
                        j["next-run"] = _schedule_next(j["schedule"], 1)[0]
                return jobs
            m = re.match(r"^/cluster/backup/([^/]+)$", path)
            if m:
                j = next((x for x in STATE["jobs"].get(self.server, []) if x["id"] == m.group(1)), None)
                if not j:
                    raise PveError("500 job '{}' does not exist".format(m.group(1)))
                return dict(j)
            if path == "/cluster/backup-info/not-backed-up":
                mine = {v: s for v, s in STATE["guests"].items() if s["node"] in self.nodes}
                covered = set()
                for j in STATE["jobs"].get(self.server, []):
                    covered |= _job_vmids(j, mine)
                return [{"vmid": v, "name": INFO[v]["name"], "type": INFO[v]["kind"]} for v in sorted(mine) if v not in covered]
            if path == "/cluster/jobs/schedule-analyze":
                p = params or {}
                return [{"timestamp": ts, "utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts))}
                        for ts in _schedule_next(p.get("schedule"), int(p.get("iterations", 3)))]
            m = re.match(r"^/nodes/([^/]+)(/.*)?$", path)
            if m:
                node, rest = m.group(1), m.group(2) or ""
                if node not in self.nodes:
                    raise PveError("500 node '{}' bestaat niet".format(node))
                if _node_down(node) and not rest.startswith("/tasks"):
                    raise PveError("595 node {} niet bereikbaar".format(node))
                return self._node(node, rest, params or {})
            raise PveError("demo kent {} niet".format(path))

    def _cluster_status(self):
        out = []
        nodes = self.nodes
        if self.is_cluster:
            online = sum(1 for n in nodes if not _node_down(n))
            out.append({"type": "cluster", "id": "cluster", "name": self.server, "nodes": len(nodes),
                        "quorate": 1 if online * 2 > len(nodes) or (online and self.server == "cluster-lab") else 0})
        for i, (n, s) in enumerate(nodes.items()):
            out.append({"type": "node", "id": "node/" + n, "name": n, "nodeid": i + 1,
                        "online": 0 if _node_down(n) else 1, "ip": s["ip"]})
        return out

    def _uptime(self, n, s):
        since = STATE["up_since"].get(n)
        if since:
            return max(0, int(time.time() - since))
        return int(s["up"] * 86400 + time.time() - START)

    def _resources(self):
        out = []
        nodes = self.nodes
        for i, (n, s) in enumerate(nodes.items()):
            maxmem = s["mem"] * GB
            down = _node_down(n)
            out.append({"id": "node/" + n, "type": "node", "node": n, "status": "offline" if down else "online",
                        "cpu": 0 if down else _wiggle(s["cpu"], i), "maxcpu": s["cpus"],
                        "mem": 0 if down else int(maxmem * _wiggle(s["used"], i + 3, 0.01)), "maxmem": maxmem,
                        "disk": 30 * GB, "maxdisk": 94 * GB, "uptime": 0 if down else self._uptime(n, s)})
            for name, kind, used, size, shared in STORAGE.get(n, []):
                out.append({"id": "storage/{}/{}".format(n, name), "type": "storage", "node": n, "storage": name,
                            "plugintype": kind, "disk": used * GB, "maxdisk": size * GB,
                            "status": "unknown" if down else "available", "shared": shared,
                            "content": "backup,iso,vztmpl" if name in BACKUP_STORES else ("iso,vztmpl" if name == "local" else "images,rootdir")})
        for vmid, st in STATE["guests"].items():
            if st["node"] not in nodes:
                continue
            i = INFO[vmid]
            if _node_down(st["node"]):     # zo geeft Proxmox het terug: geen naam, geen geheugen, status unknown
                out.append({"id": "{}/{}".format(i["kind"], vmid), "type": i["kind"], "vmid": vmid, "node": st["node"],
                            "status": "unknown", "template": 0, "tags": i["tags"]})
                continue
            running = st["running"]
            out.append({"id": "{}/{}".format(i["kind"], vmid), "type": i["kind"], "vmid": vmid, "name": i["name"],
                        "node": st["node"], "status": "running" if running else "stopped", "template": 0, "tags": i["tags"],
                        "cpu": _wiggle(i["cpu"], vmid, 0.02) if running else 0, "maxcpu": 2 if i["kind"] == "lxc" else 4,
                        "mem": int(i["mem"] * GB) if running else 0, "maxmem": int(i["maxmem"] * GB),
                        "uptime": int(time.time() - st["since"]) if running else 0, "disk": 0, "maxdisk": 32 * GB})
        return out

    def _tasks(self):
        now = int(time.time())
        nodes = self.nodes
        out = []
        for i, (vmid, st) in enumerate(sorted(STATE["guests"].items())):
            if st["node"] not in nodes:
                continue
            start = now - 12 * 3600 - i * 120
            out.append({"upid": "UPID:{}:vzdump:{}".format(st["node"], vmid), "node": st["node"], "type": "vzdump",
                        "id": str(vmid), "user": "root@pam", "starttime": start, "endtime": start + 90,
                        "status": "job errors" if vmid == 204 else "OK"})
        # consoletaken zoals Proxmox ze aanmaakt bij het openen van een console
        for k, (typ, tid, node_) in enumerate([("vncproxy", "101", "pve-02"), ("termproxy", "104", "pve-02"), ("termproxy", "", "pve-01")]):
            if node_ in nodes:
                out.append({"upid": "UPID:{}:{}:{}".format(node_, typ, k), "node": node_, "type": typ, "id": tid, "user": "dashboard@pve!fleet",
                            "starttime": now - 300 - k * 60, "endtime": now - 120 - k * 60, "status": "OK"})
        for t in STATE["tasks"]:
            if t["node"] in nodes:
                out.append({k: t[k] for k in ("upid", "node", "type", "id", "user", "starttime", "endtime", "status")})
        out.sort(key=lambda t: -t["starttime"])
        return out

    def _node(self, node, rest, params):
        s = self.nodes[node]
        m = re.match(r"^/tasks/([^/]+)/(status|log)$", rest)
        if m:
            from urllib.parse import unquote
            upid = unquote(m.group(1))
            t = next((x for x in STATE["tasks"] if x["upid"] == upid), None)
            fm = re.match(r"^UPID:([^:]+):vzdump:(\d+)$", upid)
            if not t and fm and m.group(2) == "log":     # de vaste back-uptaken van de demo
                vmid = int(fm.group(2))
                if vmid != 204:
                    return [{"n": 1, "t": "INFO: Finished Backup of VM {}".format(vmid)}, {"n": 2, "t": "TASK OK"}]
                f = "/mnt/pve/synology-nfs/dump/vzdump-lxc-204-demo"
                lines = ["INFO: starting new backup job: vzdump 204 --storage synology-nfs --mode snapshot --compress zstd",
                         "INFO: Starting Backup of VM 204 (lxc)", "INFO: backup mode: snapshot",
                         "INFO: creating vzdump archive '{}.tar.zst'".format(f),
                         "INFO: tar: {}.tmp: Cannot open: Permission denied".format(f),
                         "INFO: tar: Error is not recoverable: exiting now",
                         "ERROR: Backup of VM 204 failed - command 'set -o pipefail && lxc-usernsexec -m u:0:100000:65536 -- tar cpf - ...' failed: exit code 2",
                         "INFO: Backup job finished with errors", "TASK ERROR: job errors"]
                return [{"n": i + 1, "t": ln} for i, ln in enumerate(lines)]
            if not t:
                raise PveError("500 taak niet gevonden")
            if m.group(2) == "status":
                return {"upid": upid, "status": "running" if t["endtime"] is None else "stopped",
                        "exitstatus": t["status"], "type": t["type"], "id": t["id"], "node": node, "starttime": t["starttime"]}
            if t["endtime"] is None:
                frac = (time.time() - t["starttime"]) / max(1, t["finish_at"] - t["starttime"])
                shown = t["log"][:max(1, int(len(t["log"]) * frac))]
            else:
                shown = t["log"]
            return [{"n": i + 1, "t": line} for i, line in enumerate(shown)]
        if rest == "/tasks":
            return [t for t in self._tasks() if t["node"] == node][:12]
        if rest in ("", "/status"):
            maxmem = s["mem"] * GB
            return {"pveversion": "pve-manager/{}/demo".format(s["ver"]),
                    "kversion": "Linux {} #1 SMP PREEMPT_DYNAMIC".format(_kernel_running(node, s)),
                    "cpuinfo": {"model": s["model"], "cpus": s["cpus"], "sockets": 1},
                    "loadavg": ["{:.2f}".format(s["cpus"] * s["cpu"] * f) for f in (0.9, 1.0, 1.05)],
                    "memory": {"total": maxmem, "used": int(maxmem * s["used"]), "free": int(maxmem * (1 - s["used"]))},
                    "uptime": self._uptime(node, s), "cpu": s["cpu"], "wait": 0.012}
        if rest == "/rrddata":
            now = int(time.time())
            return [{"time": now - (69 - k) * 60, "cpu": _wiggle(s["cpu"], k / 4.0, 0.06), "maxcpu": s["cpus"],
                     "memused": s["mem"] * GB * _wiggle(s["used"], k / 9.0, 0.015), "memtotal": s["mem"] * GB,
                     "netin": 2e6 * (1 + math.sin(k / 5.0)) + 4e6 * s["cpu"], "netout": 8e5 * (1 + math.cos(k / 6.0)),
                     "iowait": 0.01 + 0.008 * math.sin(k / 3.0), "loadavg": s["cpus"] * s["cpu"]} for k in range(70)]
        if rest == "/apt/update":
            pk = [("pve-manager", "9.0.10", "9.0.11", "Proxmox"), ("proxmox-kernel-6.14", "6.14.8-2", "6.14.11-1", "Proxmox"),
                  ("openssl", "3.5.1-1", "3.5.1-1+deb13u1", "Debian-Security"), ("libc6", "2.41-12", "2.41-12+deb13u1", "Debian-Security"),
                  ("qemu-server", "9.0.21", "9.0.22", "Proxmox"), ("zfsutils-linux", "2.3.3-pve1", "2.3.4-pve1", "Proxmox"),
                  ("curl", "8.14.1-2", "8.14.1-2+deb13u1", "Debian"), ("ceph-common", "19.2.2", "19.2.3", "Proxmox"),
                  ("lxc-pve", "6.0.4-1", "6.0.5-1", "Proxmox")]
            if node in STATE["upgraded"]:
                return []
            return [{"Package": p, "OldVersion": o, "Version": v, "Origin": org, "Title": p} for p, o, v, org in pk[:s["updates"]]]
        if rest == "/apt/versions":
            out = [{"Package": "proxmox-ve", "Version": s["ver"][:3] + ".0", "CurrentState": "Installed",
                    "RunningKernel": _kernel_running(node, s), "ManagerVersion": s["ver"]},
                   {"Package": "pve-manager", "Version": s["ver"], "CurrentState": "Installed"}]
            for k in _kernel_installed(node, s):
                out.append({"Package": "proxmox-kernel-{}-signed".format(k), "Version": k[:-4], "CurrentState": "Installed"})
            return out
        if rest == "/certificates/info":
            now = int(time.time())
            base = [{"filename": "pve-ssl.pem", "subject": "/OU=PVE Cluster Node/O=Proxmox Virtual Environment/CN={}.lan".format(node),
                     "issuer": "/CN=Proxmox Virtual Environment/OU=demo/O=PVE Cluster Manager CA", "notbefore": now - 400 * 86400,
                     "notafter": now + 300 * 86400, "san": ["127.0.0.1", node, s["ip"]]}]
            cust = STATE.setdefault("node_certs", {}).get(node)
            if cust:
                base.append(dict(cust, filename="pveproxy-ssl.pem"))
            return base
        if rest == "/disks/list":
            return [{"devpath": "/dev/nvme0n1", "model": "Samsung SSD 980 1TB", "size": 1000 * GB, "health": "PASSED", "wearout": 7, "type": "nvme"},
                    {"devpath": "/dev/sda", "model": "Crucial MX500 2TB", "size": 2000 * GB, "health": "PASSED", "wearout": 4, "type": "ssd"}]
        m = re.match(r"^/(qemu|lxc)/(\d+)(/.*)?$", rest)
        if m:
            return self._guest(node, m.group(1), int(m.group(2)), m.group(3) or "")
        m = re.match(r"^/storage/([^/]+)/content$", rest)
        if m:
            now = int(time.time())
            if not params.get("vmid"):
                return self._all_backups(m.group(1), now)
            vmid = int(params.get("vmid", 0))
            items = [{"volid": "{}:backup/vzdump-{}-{}.tar.zst".format(m.group(1), vmid, d), "vmid": vmid,
                      "ctime": now - d * 86400 - 13 * 3600, "size": int(2.1 * GB), "format": "tar.zst", "content": "backup"}
                     for d in (0, 1, 2)]
            for b in STATE["backups"].get(vmid, []):
                if b["storage"] == m.group(1):
                    items.append({"volid": "{}:backup/vzdump-{}-now-{}.tar.zst".format(b["storage"], vmid, b["ctime"]),
                                  "vmid": vmid, "ctime": b["ctime"], "size": b["size"], "format": "tar.zst", "content": "backup"})
            return items
        raise PveError("demo kent {} niet".format(rest))

    def _guest_or_fail(self, node, kind, vmid):
        st = STATE["guests"].get(vmid)
        if not st or INFO[vmid]["kind"] != kind or st["node"] != node:
            raise PveError("500 Configuration file 'nodes/{}/{}/{}.conf' does not exist".format(
                node, "qemu-server" if kind == "qemu" else "lxc", vmid))
        return st, INFO[vmid]

    def _guest(self, node, kind, vmid, rest):
        st, i = self._guest_or_fail(node, kind, vmid)
        name, running, cpu, mem, maxmem, tags = i["name"], st["running"], i["cpu"], i["mem"], i["maxmem"], i["tags"]
        if rest == "/config":
            if kind == "qemu":
                return {"name": name, "cores": 4, "sockets": 1, "memory": str(int(maxmem * 1024)), "bios": "ovmf",
                        "machine": "q35", "ostype": "l26", "scsi0": "local-zfs:vm-{}-disk-0,size=64G".format(vmid),
                        "net0": "virtio=BC:24:11:2A:{:02X}:01,bridge=vmbr0,firewall=1".format(vmid % 256),
                        "boot": "order=scsi0;net0", "onboot": 1 if vmid not in (120, 144, 204) else 0, "agent": "1", "tags": tags,
                        "protection": 1 if vmid in (101, 110) else 0}
            return {"hostname": name, "cores": 2, "memory": int(maxmem * 1024), "swap": 512, "ostype": "debian",
                    "rootfs": "local-zfs:subvol-{}-disk-0,size=16G".format(vmid), "unprivileged": 1, "features": "nesting=1",
                    "net0": "name=eth0,bridge=vmbr0,hwaddr=BC:24:11:3B:{:02X}:02,ip=192.168.1.{}/24,gw=192.168.1.1,type=veth".format(vmid % 256, vmid % 200 or 200),
                    "onboot": 1, "tags": tags, "description": DEMO_NOTE.format(name=name) +
                    ("\n[Script](https://community-scripts.org/scripts/{})".format(DEMO_SLUG[vmid]) if vmid in DEMO_SLUG else "")}
        if rest == "/status/current":
            return {"status": "running" if running else "stopped", "name": name, "cpu": cpu, "cpus": 4 if kind == "qemu" else 2,
                    "mem": int(mem * GB) if running else 0, "maxmem": int(maxmem * GB),
                    "uptime": int(time.time() - st["since"]) if running else 0, "ha": {"managed": 1 if vmid in (101, 110) else 0}}
        if rest == "/rrddata":
            now = int(time.time())
            return [{"time": now - (69 - k) * 60, "cpu": _wiggle(cpu, k / 4.0, 0.03) if running else 0,
                     "maxcpu": 4, "mem": mem * GB * _wiggle(0.95, k / 7.0, 0.02) if running else 0, "maxmem": maxmem * GB,
                     "netin": 3e5 * (1 + math.sin(k / 4.0)) if running else 0, "netout": 1e5 * (1 + math.cos(k / 5.0)) if running else 0,
                     "diskread": 2e5 if running else 0, "diskwrite": 4e5 * (1 + math.sin(k / 3.0)) if running else 0} for k in range(70)]
        if rest == "/snapshot":
            return list(reversed(_snaps(vmid))) + [{"name": "current", "description": "You are here!"}]
        if rest == "/migrate" and kind == "qemu":
            others = [n for n in self.nodes if n != node and not _node_down(n)]
            return {"running": 1 if running else 0, "allowed_nodes": others, "not_allowed_nodes": {},
                    "local_disks": [{"volid": "local-zfs:vm-{}-disk-0".format(vmid), "size": 64 * GB, "is_unused": 0}],
                    "local_resources": ["usb0"] if vmid == 101 else []}
        if rest in ("/agent/network-get-interfaces", "/interfaces"):
            if not running:
                raise PveError("500 VM {} is not running".format(vmid))
            ip = "192.168.1.{}".format(vmid % 200 or 200)
            if kind == "qemu":
                if vmid == 120:
                    raise PveError("500 QEMU guest agent is not running")
                res = [{"name": "lo", "ip-addresses": [{"ip-address": "127.0.0.1", "ip-address-type": "ipv4"}]},
                       {"name": "eth0", "ip-addresses": [{"ip-address": ip, "ip-address-type": "ipv4"},
                                                         {"ip-address": "fe80::be24:11ff:fe2a:101", "ip-address-type": "ipv6"}]}]
                if vmid == 101:   # VM met Docker: extra bridge, komt achteraan
                    res.insert(1, {"name": "docker0", "ip-addresses": [{"ip-address": "172.17.0.1", "ip-address-type": "ipv4"}]})
                return {"result": res}
            return [{"name": "lo", "inet": "127.0.0.1/8"}, {"name": "eth0", "inet": ip + "/24"}]
        raise PveError("demo kent {} niet".format(rest))

    # ------------------------------------------------------------ schrijven
    def post(self, path, data=None):
        data = data or {}
        with _lock:
            _tick()
            self._check_reachable()
            m = re.match(r"^/nodes/([^/]+)/(qemu|lxc)/(\d+)/(.+)$", path)
            if m:
                node, kind, vmid, rest = m.group(1), m.group(2), int(m.group(3)), m.group(4)
                if _node_down(node):
                    raise PveError("595 node {} niet bereikbaar".format(node))
                return self._guest_post(node, kind, vmid, rest, data)
            m = re.match(r"^/nodes/([^/]+)/status$", path)
            if m:
                return self._node_power(m.group(1), data.get("command"))
            if path == "/cluster/backup":
                jobs = STATE["jobs"].setdefault(self.server, [])
                job = dict(data, type="vzdump", id="backup-{:08x}".format(next(_pid) * 2654435761 % (1 << 32)))
                _schedule_next(job.get("schedule"))   # zelfde controle als Proxmox
                jobs.append(job)
                return None
            m = re.match(r"^/nodes/([^/]+)/certificates/custom$", path)
            if m:
                from . import acme as acme_mod
                info = acme_mod.cert_info(data.get("certificates", ""))
                STATE.setdefault("node_certs", {})[m.group(1)] = {"subject": "/CN=" + (info["sans"] or ["?"])[0], "issuer": "/CN=" + info["issuer"],
                                                                 "notbefore": info["not_before"], "notafter": info["not_after"], "san": info["sans"]}
                return {"filename": "pveproxy-ssl.pem"}
            m = re.match(r"^/nodes/([^/]+)/apt/update$", path)
            if m:
                return _task(m.group(1), "aptupdate", "", 4, ["starting apt-get update", "Hit:1 http://download.proxmox.com/debian/pve trixie InRelease",
                                                                 "Reading package lists...", "TASK OK"])
            m = re.match(r"^/nodes/([^/]+)/vzdump$", path)
            if m:
                return self._vzdump(m.group(1), data)
            m = re.match(r"^/nodes/([^/]+)/wakeonlan$", path)
            if m:
                if not wake(self.server, m.group(1)):
                    raise PveError("500 node {} staat niet uit".format(m.group(1)))
                return "BC:24:11:00:00:01"
        raise PveError("demo kent POST {} niet".format(path))

    # ------------------------------------------------------------ root-sessie (demo)
    def ticket_login(self, data):
        """Demo van POST /access/ticket. Het wachtwoord in de demo is 'demo'."""
        self._check_reachable()
        pw = data.get("password", "")
        if data.get("tfa-challenge"):
            raise PveError("500 demo kent geen 2FA")
        if pw == "demo" or pw.startswith("PVE:root@pam:DEMO"):
            return {"ticket": "PVE:root@pam:DEMO{}::sig".format(int(time.time())), "CSRFPreventionToken": "demo-csrf",
                    "username": data.get("username", "root@pam")}
        from .pve import PvePermissionError
        raise PvePermissionError("inloggen mislukt: gebruikersnaam, wachtwoord of code klopt niet (demo: wachtwoord 'demo')")

    def guest_check(self, node):
        """Demo van het controlescript via SSH: uitvoer voor de draaiende containers op deze node."""
        with _lock:
            if _node_down(node):
                raise PveError("SSH naar {} mislukt: time-out (demo)".format(node))
            lines = ["PVEFLEET 1 " + node]
            for vmid, st in sorted(STATE["guests"].items()):
                if st["node"] != node or not st["running"] or INFO[vmid]["kind"] != "lxc":
                    continue
                pm, n, sec, os_, apps = DEMO_CT_UPDATES.get(vmid, ("apt", 0, 0, "Debian GNU/Linux 12 (bookworm)", {}))
                if node in STATE["upgraded_cts"] and vmid in STATE["upgraded_cts"][node]:
                    n, sec = 0, 0
                lines += ["CT {}".format(vmid), "pm={} n={} sec={}".format(pm, n, sec), "os=" + os_]
                lines += ["app {}={}".format(k, v) for k, v in apps.items()]
                lines.append("END")
            lines.append("DONE")
            return "\n".join(lines) + "\n"

    def ticket_post(self, path, data):
        return {"port": 5900, "ticket": "PVEVNC:demo", "user": "root@pam"}

    def put(self, path, data=None):
        data = dict(data or {})
        with _lock:
            _tick()
            self._check_reachable()
            m = re.match(r"^/cluster/backup/([^/]+)$", path)
            if m:
                j = next((x for x in STATE["jobs"].get(self.server, []) if x["id"] == m.group(1)), None)
                if not j:
                    raise PveError("500 job '{}' does not exist".format(m.group(1)))
                if "schedule" in data:
                    _schedule_next(data["schedule"])
                for k in str(data.pop("delete", "") or "").split(","):
                    j.pop(k.strip(), None)
                j.update(data)
                if str(j.get("all", 0)) != "1":
                    j.pop("all", None)
                return None
        raise PveError("demo kent PUT {} niet".format(path))

    def delete(self, path, params=None):
        with _lock:
            _tick()
            self._check_reachable()
            m = re.match(r"^/cluster/backup/([^/]+)$", path)
            if m:
                jobs = STATE["jobs"].get(self.server, [])
                if not any(x["id"] == m.group(1) for x in jobs):
                    raise PveError("500 job '{}' does not exist".format(m.group(1)))
                STATE["jobs"][self.server] = [x for x in jobs if x["id"] != m.group(1)]
                return None
            m = re.match(r"^/nodes/([^/]+)/(qemu|lxc)/(\d+)/snapshot/([^/]+)$", path)
            if m:
                node, kind, vmid, snap = m.group(1), m.group(2), int(m.group(3)), m.group(4)
                self._guest_or_fail(node, kind, vmid)
                lst = _snaps(vmid)
                if not any(x["name"] == snap for x in lst):
                    raise PveError("500 snapshot '{}' does not exist".format(snap))

                def done():
                    STATE["snaps"][vmid] = [x for x in _snaps(vmid) if x["name"] != snap]
                return _task(node, "qmdelsnapshot" if kind == "qemu" else "vzdelsnapshot", vmid, 3,
                             ["delete snapshot '{}'".format(snap)], done)
        raise PveError("demo kent DELETE {} niet".format(path))

    def _guest_post(self, node, kind, vmid, rest, data):
        st, info = self._guest_or_fail(node, kind, vmid)
        pre = "qm" if kind == "qemu" else "vz"
        label = "VM" if kind == "qemu" else "CT"

        def set_running(on):
            def f():
                st["running"] = on
                if on:
                    st["since"] = time.time()
            return f

        m = re.match(r"^status/(start|shutdown|stop|reboot)$", rest)
        if m:
            act = m.group(1)
            if act == "start" and st["running"]:
                raise PveError("500 {} {} already running".format(label, vmid))
            if act in ("shutdown", "stop", "reboot") and not st["running"]:
                raise PveError("500 {} {} not running".format(label, vmid))
            secs = {"start": 3, "shutdown": 6, "stop": 2, "reboot": 8}[act]
            done = set_running(act != "shutdown" and act != "stop")
            lines = {"start": ["starting {} {}".format(label, vmid)],
                     "shutdown": ["requesting shutdown of {} {}".format(label, vmid), "waiting for guest to power off"],
                     "stop": ["stopping {} {} (hard)".format(label, vmid)],
                     "reboot": ["requesting reboot of {} {}".format(label, vmid), "guest is rebooting"]}[act]
            return _task(node, pre + act, vmid, secs, lines, done)
        if rest == "snapshot":
            name = data.get("snapname", "")
            if any(x["name"] == name for x in _snaps(vmid)):
                raise PveError("500 snapshot name '{}' already used".format(name))

            def done():
                _snaps(vmid).append({"name": name, "description": data.get("description", ""), "snaptime": int(time.time())})
            lines = ["create snapshot '{}'".format(name)]
            if data.get("vmstate"):
                lines.append("saving VM state and RAM")
            return _task(node, pre + "snapshot", vmid, 4 if data.get("vmstate") else 3, lines, done)
        m = re.match(r"^snapshot/([^/]+)/rollback$", rest)
        if m:
            snap = m.group(1)
            if not any(x["name"] == snap for x in _snaps(vmid)):
                raise PveError("500 snapshot '{}' does not exist".format(snap))
            return _task(node, pre + "rollback", vmid, 4, ["rollback to snapshot '{}'".format(snap)], set_running(False))
        if rest == "migrate":
            target = data.get("target")
            if target not in self.nodes or target == node or _node_down(target):
                raise PveError("500 target node '{}' is niet beschikbaar".format(target))
            was = st["running"]

            def done():
                st["node"] = target
                st["running"] = was
            mode = "online" if (kind == "qemu" and was) else ("restart" if was else "offline")
            lines = ["starting migration of {} {} to node '{}' ({})".format(label, vmid, target, mode)]
            if data.get("with-local-disks"):
                lines.append("copying local disk local-zfs:vm-{}-disk-0".format(vmid))
            lines += ["migration progress 50%", "migration finished successfully"]
            return _task(node, pre + "migrate", vmid, 10, lines, done)
        raise PveError("demo kent POST {} niet".format(rest))

    def _all_backups(self, storage, now):
        """Alle back-ups op een opslag (demo): per job een back-up per nacht, de laatste 3 dagen."""
        mine = {v: s for v, s in STATE["guests"].items() if s["node"] in self.nodes}
        items = []
        for j in STATE["jobs"].get(self.server, []):
            if j.get("storage") != storage:
                continue
            for v in sorted(_job_vmids(j, mine)):
                old = 8 if v == 204 else 0    # 204: laatste back-ups mislukt
                for d in (0, 1, 2):
                    ct = now - (d + old) * 86400 - 13 * 3600 - (v % 50) * 60
                    items.append({"volid": "{}:backup/vzdump-{}-{}-{}.tar.zst".format(storage, INFO[v]["kind"], v, ct), "vmid": v,
                                  "ctime": ct, "size": int((INFO[v]["maxmem"] * 0.6 + 1.5) * GB), "format": "tar.zst", "content": "backup"})
        for v, lst in STATE["backups"].items():
            if v in mine:
                for b in lst:
                    if b["storage"] == storage:
                        items.append({"volid": "{}:backup/vzdump-{}-now-{}.tar.zst".format(storage, v, b["ctime"]), "vmid": v,
                                      "ctime": b["ctime"], "size": b["size"], "format": "tar.zst", "content": "backup"})
        return items

    def _vzdump(self, node, data):
        if str(data.get("all", 0)) == "1" or "," in str(data.get("vmid", "")):
            here = {v: s for v, s in STATE["guests"].items() if s["node"] == node}
            ids = sorted(_job_vmids(data, here) & set(here))
            if not ids:
                raise PveError("500 no guests selected on node {}".format(node))
            storage = data.get("storage")

            def done_all():
                for v in ids:
                    STATE["backups"].setdefault(v, []).append({"storage": storage, "ctime": int(time.time()),
                                                               "size": int(INFO[v]["maxmem"] * 0.4 * GB) + GB})
            lines = ["INFO: starting new backup job: vzdump {} --storage {} --mode {}".format(
                "--all 1" if str(data.get("all")) == "1" else " ".join(map(str, ids)), storage, data.get("mode", "snapshot"))]
            for v in ids:
                lines += ["INFO: Starting Backup of VM {} ({})".format(v, INFO[v]["kind"]), "INFO: Finished Backup of VM {}".format(v)]
            lines.append("INFO: Backup job finished successfully")
            return _task(node, "vzdump", "", 4 + 2 * len(ids), lines, done_all)
        vmid = int(data.get("vmid", 0))
        storage = data.get("storage")
        st, info = self._guest_or_fail(node, INFO.get(vmid, {}).get("kind", "qemu"), vmid)

        def done():
            STATE["backups"].setdefault(vmid, []).append({"storage": storage, "ctime": int(time.time()), "size": int(info["maxmem"] * 0.4 * GB) + GB})
        lines = ["INFO: starting new backup job: vzdump {} --storage {} --mode {} --compress zstd".format(vmid, storage, data.get("mode", "snapshot")),
                 "INFO: Starting Backup of VM {} ({})".format(vmid, info["kind"]),
                 "INFO: creating vzdump archive",
                 "INFO: 42% done", "INFO: 100% done",
                 "INFO: Finished Backup of VM {}".format(vmid), "INFO: Backup job finished successfully"]
        return _task(node, "vzdump", vmid, 8, lines, done)

    def _node_power(self, node, command):
        if node not in self.nodes:
            raise PveError("500 node '{}' bestaat niet".format(node))
        if command == "reboot":
            if node in STATE["upgraded"]:
                STATE["kernel"][node] = KERNEL_NEW
            STATE["down_until"][node] = time.time() + 25
            STATE["up_since"][node] = time.time() + 25
        elif command == "shutdown":
            STATE["down_until"][node] = float("inf")
        else:
            raise PveError("400 onbekend commando")
        return None
