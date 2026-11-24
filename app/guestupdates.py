"""Updates in VM's & containers: pakketten van het besturingssysteem en app-versies.

Proxmox weet niet wat er in een container geïnstalleerd is. Daarom kijkt PVE Fleet via
SSH op elke node, met een sleutel die alleen één controlescript mag starten:
  command="bash /etc/pve/pvefleet/check.sh",restrict ssh-ed25519 ... pve-fleet-check
Het script draait in elke draaiende container (pct exec) een simulatie van de upgrade
('apt-get -s dist-upgrade', 'apk version', 'dnf check-update'). Er wordt niets geïnstalleerd.

App-versies: de community-scripts bewaren de geïnstalleerde versie in /root/.<app> (of
/opt/<app>_version.txt). Welke GitHub-repository daarbij hoort, staat in
app/data/community_apps.json (overgenomen uit de update-scripts van community-scripts).
PVE Fleet vergelijkt de versie met de nieuwste release op GitHub.
"""
import json
import logging
import os
import re
import threading
import time

import requests
from flask import Blueprint, abort, current_app, g, jsonify, request

from .pve import PveError, q

log = logging.getLogger(__name__)
bp = Blueprint("guestupdates", __name__)

KEY_TAG = "pve-fleet-check"
SCRIPT_PATH = "/etc/pve/pvefleet/check.sh"
GITHUB_CACHE = 12 * 3600

CHECK_SH = r'''#!/bin/bash
# PVE Fleet: kijkt welke updates er klaarstaan in de draaiende containers op deze node.
# Installeert niets. Wordt gestart via een beperkte SSH-sleutel (zie authorized_keys).
export LC_ALL=C
echo "PVEFLEET 1 $(hostname)"
INNER=$(cat <<'EOS'
export LC_ALL=C
os=""; [ -r /etc/os-release ] && os=$(. /etc/os-release; echo "$PRETTY_NAME")
pm=none; n=-1; sec=0
if command -v apt-get >/dev/null 2>&1; then
  pm=apt
  now=$(date +%s); st=$(stat -c %Y /var/cache/apt/pkgcache.bin 2>/dev/null || echo 0)
  if [ $((now - st)) -gt 21600 ]; then apt-get update -qq >/dev/null 2>&1; fi
  list=$(apt-get -s -o Debug::NoLocking=1 dist-upgrade 2>/dev/null | grep '^Inst ')
  if [ -n "$list" ]; then n=$(printf '%s\n' "$list" | wc -l); sec=$(printf '%s\n' "$list" | grep -ci 'security'); else n=0; fi
elif command -v apk >/dev/null 2>&1; then
  pm=apk
  apk update -q >/dev/null 2>&1
  n=$(apk version -l '<' 2>/dev/null | grep -c ' < ')
elif command -v dnf >/dev/null 2>&1; then
  pm=dnf
  n=$(dnf -q check-update 2>/dev/null | grep -cE '^[A-Za-z0-9_.+-]+\.[a-z0-9_]+ ')
fi
echo "pm=$pm n=$n sec=$sec"
echo "os=$os"
for f in /root/.[A-Za-z0-9]* /opt/*_version.txt; do
  [ -f "$f" ] || continue
  sz=$(wc -c < "$f" 2>/dev/null); [ "${sz:-999}" -lt 80 ] || continue
  v=$(head -n1 "$f" | tr -d ' \r\n\t')
  case "$v" in *[0-9]*) ;; *) continue ;; esac
  b=$(basename "$f"); b=${b#.}; b=${b%_version.txt}
  echo "app $b=$v"
done
EOS
)
pct list 2>/dev/null | awk 'NR>1 && $2=="running" {print $1}' | while read -r id; do
  echo "CT $id"
  timeout 180 pct exec "$id" -- /bin/sh -c "$INNER" 2>/dev/null || echo "err exit=$?"
  echo "END"
done
echo "DONE"
'''


# ------------------------------------------------------------------ opslag
class Store:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "guest_updates.json")
        self.ssh_dir = os.path.join(data_dir, "ssh")
        self.lock = threading.RLock()
        self.data = {"settings": {}, "results": {}, "repos": {}, "schedule": {}}
        try:
            with open(self.path, encoding="utf-8") as fh:
                d = json.load(fh)
                if isinstance(d, dict):
                    self.data.update({k: d.get(k) or {} for k in ("settings", "results", "repos", "schedule")})
        except (OSError, ValueError):
            pass
        self.running = set()      # servers die nu gecontroleerd worden

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=1)
            os.replace(tmp, self.path)

    def settings(self, server):
        return self.data["settings"].get(server) or {}

    def result(self, server):
        return self.data["results"].get(server) or {}


STORE = None   # wordt gezet in init_app; de collector leest hier de tellingen uit


def _apps_db():
    if not hasattr(_apps_db, "cache"):
        path = os.path.join(os.path.dirname(__file__), "data", "community_apps.json")
        try:
            with open(path, encoding="utf-8") as fh:
                db = json.load(fh)
        except (OSError, ValueError) as e:
            log.warning("app/data/community_apps.json missing or unreadable (%s): app versions in containers cannot be checked", e)
            db = {}
        by_name = {}
        for slug, pairs in db.items():
            for name, repo in pairs:
                by_name.setdefault(name, set()).add(repo)
        _apps_db.cache = (db, {k: next(iter(v)) for k, v in by_name.items() if len(v) == 1})
    return _apps_db.cache


# ------------------------------------------------------------------ SSH-sleutel
def ensure_key(store):
    """Maakt (eenmalig) een ed25519-sleutelpaar in data/ssh. Geeft (pad, publieke sleutel)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    os.makedirs(store.ssh_dir, exist_ok=True)
    try:
        os.chmod(store.ssh_dir, 0o700)
    except OSError:
        pass
    priv = os.path.join(store.ssh_dir, "id_ed25519")
    pub = priv + ".pub"
    if not os.path.exists(priv):
        k = ed25519.Ed25519PrivateKey.generate()
        pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption())
        line = k.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
        fd = os.open(priv, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(pem)
        with open(pub, "w", encoding="utf-8") as fh:
            fh.write(line + " " + KEY_TAG + "\n")
    with open(pub, encoding="utf-8") as fh:
        return priv, fh.read().strip()


def setup_command(pubkey):
    line = 'command="bash {}",restrict {}'.format(SCRIPT_PATH, pubkey)
    return ("mkdir -p /etc/pve/pvefleet && cat > {path} <<'PVEFLEET'\n{script}PVEFLEET\n"
            "KEY='{line}'\n"
            "mkdir -p /root/.ssh && touch /root/.ssh/authorized_keys\n"
            "for f in /etc/pve/priv/authorized_keys /root/.ssh/authorized_keys; do\n"
            "  [ -e \"$f\" ] || continue\n"
            "  T=$(mktemp); grep -v '{tag}' \"$f\" > \"$T\"; echo \"$KEY\" >> \"$T\"; cat \"$T\" > \"$f\"; rm -f \"$T\"\n"
            "done\n"
            "echo \"PVE Fleet: klaar op $(hostname)\"\n").format(path=SCRIPT_PATH, script=CHECK_SH, line=line, tag=KEY_TAG)


def remove_command():
    return ("sed -i '/{tag}/d' /etc/pve/priv/authorized_keys /root/.ssh/authorized_keys 2>/dev/null; "
            "rm -f {path}; echo 'PVE Fleet: sleutel en script verwijderd'").format(tag=KEY_TAG, path=SCRIPT_PATH)


# ------------------------------------------------------------------ controleren
class _TofuPolicy:
    """Eerste keer: hostsleutel onthouden. Daarna moet hij gelijk blijven (anders weigert paramiko)."""
    def __init__(self, path):
        self.path = path

    def missing_host_key(self, client, hostname, key):
        client.get_host_keys().add(hostname, key.get_name(), key)
        try:
            client.save_host_keys(self.path)
        except OSError:
            pass


def _ssh_run(store, host, port, user, timeout=900):
    import paramiko
    priv, _ = ensure_key(store)
    kh = os.path.join(store.ssh_dir, "known_hosts")
    if not os.path.exists(kh):
        open(kh, "a").close()
    cl = paramiko.SSHClient()
    cl.load_host_keys(kh)
    pol = _TofuPolicy(kh)
    cl.set_missing_host_key_policy(type("P", (paramiko.MissingHostKeyPolicy,), {"missing_host_key": lambda s, c, h, k: pol.missing_host_key(c, h, k)})())
    try:
        cl.connect(host, port=port, username=user, pkey=paramiko.Ed25519Key.from_private_key_file(priv),
                   timeout=8, banner_timeout=10, auth_timeout=10, allow_agent=False, look_for_keys=False)
        _, out, err = cl.exec_command("pvefleet-check", timeout=timeout)
        text = out.read().decode("utf-8", "replace")
        return text
    except paramiko.BadHostKeyException:
        raise PveError("de SSH-hostsleutel van {} is veranderd. Is de node opnieuw geïnstalleerd? Verwijder dan de regel "
                       "voor {} uit data/ssh/known_hosts.".format(host, host))
    except paramiko.AuthenticationException:
        raise PveError("de node {} weigert de sleutel van PVE Fleet. Plak het installatiecommando ook in de shell van "
                       "deze node.".format(host))
    except (paramiko.SSHException, OSError) as e:
        raise PveError("SSH naar {}:{} mislukt: {}".format(host, port, e))
    finally:
        cl.close()


def parse_output(text):
    if "PVEFLEET 1" not in text:
        raise PveError("onverwacht antwoord; staat het controlescript er? ({})".format(text.strip()[:120] or "geen uitvoer"))
    guests, cur = {}, None
    for line in text.splitlines():
        line = line.rstrip()
        if line.startswith("CT "):
            try:
                cur = {"vmid": int(line[3:]), "apps": {}, "pm": None, "count": None, "security": None, "os": None, "error": None}
            except ValueError:
                cur = None
            continue
        if cur is None:
            continue
        if line == "END":
            guests[cur["vmid"]] = cur
            cur = None
        elif line.startswith("pm="):
            parts = dict(p.split("=", 1) for p in line.split() if "=" in p)
            cur["pm"] = parts.get("pm")
            try:
                cur["count"] = int(parts.get("n", -1))
                cur["security"] = int(parts.get("sec", 0))
            except ValueError:
                pass
            if cur["pm"] == "none" or (cur["count"] is not None and cur["count"] < 0):
                cur["count"] = None
        elif line.startswith("os="):
            cur["os"] = line[3:][:80] or None
        elif line.startswith("app ") and "=" in line:
            name, ver = line[4:].split("=", 1)
            if re.match(r"^[a-z0-9_.-]{1,40}$", name.lower()):
                cur["apps"][name.lower()] = ver[:60]
        elif line.startswith("err "):
            cur["error"] = "pct exec mislukt ({})".format(line[4:])
    return guests


# ------------------------------------------------------------------ GitHub
_gh_cache = {}
_gh_lock = threading.Lock()


def _norm(v):
    v = (v or "").strip()
    v = re.sub(r"^(release-|version-|v)", "", v, flags=re.I)
    return v


def _verkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", _norm(v))[:6])


def github_latest(repo, token=None, demo=False, max_age=None):
    if demo:
        from . import demo as demo_mod
        return demo_mod.DEMO_LATEST.get(repo)
    with _gh_lock:
        hit = _gh_cache.get(repo)
    if hit and time.time() - hit[0] < (max_age or GITHUB_CACHE):
        return hit[1]
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "pve-fleet"}
    if token:
        headers["Authorization"] = "Bearer " + token
    tag = None
    try:
        r = requests.get("https://api.github.com/repos/{}/releases/latest".format(repo), headers=headers, timeout=10)
        if r.status_code == 200:
            tag = (r.json() or {}).get("tag_name")
        elif r.status_code == 404:
            r = requests.get("https://api.github.com/repos/{}/tags".format(repo), headers=headers, timeout=10, params={"per_page": 1})
            if r.status_code == 200 and r.json():
                tag = r.json()[0].get("name")
        elif r.status_code in (403, 429):
            log.info("GitHub-limiet bereikt bij %s", repo)
            return hit[1] if hit else None
    except requests.RequestException as e:
        log.info("GitHub %s: %s", repo, e)
        return hit[1] if hit else None
    with _gh_lock:
        _gh_cache[repo] = (time.time(), tag)
    return tag


SLUG_RE = re.compile(r"(?:community-scripts\.org/scripts/|helper-scripts\.com/scripts\?id=|community-scripts\.github\.io/ProxmoxVE/scripts\?id=)([A-Za-z0-9_-]+)", re.I)


def describe_apps(found, slug, override, token=None, demo=False):
    """found: {naam: versie} uit de container. Geeft een lijst met bekende apps (hoofdapp eerst)."""
    db, by_name = _apps_db()
    pairs = db.get(slug or "", [])
    names_for_slug = {n: r for n, r in pairs}
    out = []
    for name, ver in found.items():
        repo = names_for_slug.get(name) or by_name.get(name)
        main = bool(pairs) and name == pairs[0][0]
        if override and (main or (not pairs and len(found) == 1)):
            repo, main = override, True
        if not repo:
            continue
        latest = github_latest(repo, token, demo)
        upd = None
        if latest:
            a, b = _verkey(ver), _verkey(latest)
            upd = (b > a) if (a and b) else (_norm(latest) != _norm(ver))
        out.append({"name": name, "repo": repo, "installed": ver, "latest": latest, "update": upd, "main": main})
    if not out and override:
        out.append({"name": slug or "app", "repo": override, "installed": None,
                    "latest": github_latest(override, token, demo), "update": None, "main": True,
                    "note": "geen versiebestand gevonden in de container"})
    out.sort(key=lambda a: (not a["main"], a["name"]))
    return out


# ------------------------------------------------------------------ uitvoeren
def check_server(app, server):
    col = app.extensions["collector"]
    store = app.extensions["guestupdates"]
    st = store.settings(server)
    grp = col.group(server)
    c = col.client(server)
    if not grp or not c:
        return
    demo = bool(col.cfg.get("demo"))
    token = col.cfg.get("github_token")
    res = {"checked": time.time(), "nodes": {}, "guests": {}}
    prev = store.result(server).get("guests") or {}
    for n in grp["nodes"]:
        if n["status"] != "online":
            res["nodes"][n["name"]] = {"ok": False, "error": "node is offline", "checked": time.time()}
            continue
        host = n.get("ip") or c.active_host
        try:
            if demo:
                text = c.guest_check(n["name"])
            else:
                text = _ssh_run(store, host, int(st.get("port") or 22), st.get("user") or "root")
            guests = parse_output(text)
            res["nodes"][n["name"]] = {"ok": True, "error": None, "checked": time.time(), "count": len(guests)}
        except PveError as e:
            res["nodes"][n["name"]] = {"ok": False, "error": str(e), "checked": time.time()}
            continue
        except Exception as e:  # noqa: BLE001
            log.exception("controle %s/%s", server, n["name"])
            res["nodes"][n["name"]] = {"ok": False, "error": str(e)[:200], "checked": time.time()}
            continue
        for vmid, gres in guests.items():
            slug = None
            try:
                cfg = c.get("/nodes/{}/lxc/{}/config".format(q(n["name"]), vmid)) or {}
                m = SLUG_RE.search(cfg.get("description") or "")
                slug = m.group(1).lower() if m else None
            except PveError:
                pass
            override = store.data["repos"].get("{}/{}".format(server, vmid))
            gres["slug"] = slug
            gres["node"] = n["name"]
            gres["checked"] = time.time()
            gres["apps"] = describe_apps(gres["apps"], slug, override, token, demo)
            res["guests"][str(vmid)] = gres
    # gestopte containers: laatste bekende uitslag bewaren (gemarkeerd als oud)
    for vmid, old in prev.items():
        if vmid not in res["guests"] and any(x["vmid"] == int(vmid) and x["type"] == "lxc" for x in grp["guests"]):
            old = dict(old, stale=True)
            res["guests"][vmid] = old
    with store.lock:
        store.data["results"][server] = res
        store.save()


def _run_bg(app, server):
    store = app.extensions["guestupdates"]
    with store.lock:
        if server in store.running:
            return False
        store.running.add(server)

    def work():
        try:
            check_server(app, server)
        except Exception:  # pragma: no cover
            log.exception("controle %s", server)
        finally:
            store.running.discard(server)
    threading.Thread(target=work, daemon=True).start()
    return True


HOURS_CHOICES = (1, 2, 3, 6, 12, 24)
AT_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def schedule(col, store):
    """Wanneer controleren: {'mode': 'interval', 'hours': N} of {'mode': 'daily', 'at': 'HH:MM'}.
    Zonder eigen instelling: guest_updates_hours uit config.yml (standaard 6)."""
    sc = store.data.get("schedule") or {}
    if sc.get("mode") == "daily" and AT_RE.match(str(sc.get("at") or "")):
        return {"mode": "daily", "at": sc["at"]}
    try:
        hours = float(sc.get("hours") or col.cfg.get("guest_updates_hours") or 6)
    except (TypeError, ValueError):
        hours = 6.0
    return {"mode": "interval", "hours": max(0.25, hours)}


def _daily_slot(at, now=None):
    """Tijdstip (epoch) van vandaag om 'at' in lokale tijd."""
    now = time.time() if now is None else now
    hh, mm = [int(x) for x in at.split(":")]
    lt = time.localtime(now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0, 0, 0, -1))


def is_due(sc, last, now=None):
    now = time.time() if now is None else now
    if sc["mode"] == "daily":
        slot = _daily_slot(sc["at"], now)
        return now >= slot and (last or 0) < slot
    return now - (last or 0) > sc["hours"] * 3600


def next_run(sc, last, now=None):
    now = time.time() if now is None else now
    if sc["mode"] == "daily":
        slot = _daily_slot(sc["at"], now)
        if now >= slot and (last or 0) >= slot:
            slot = _daily_slot(sc["at"], now + 86400)
        return slot
    return max(now, (last or 0) + sc["hours"] * 3600)


def _scheduler(app):
    while True:
        time.sleep(60)
        try:
            col = app.extensions["collector"]
            store = app.extensions["guestupdates"]
            sc = schedule(col, store)
            for server, st in list(store.data["settings"].items()):
                if not st.get("enabled") or not col.client(server):
                    continue
                if is_due(sc, store.result(server).get("checked") or 0):
                    _run_bg(app, server)
        except Exception:  # pragma: no cover
            log.exception("planner")


def init_app(app):
    global STORE
    store = Store(app.config["PVEF_DATA"])
    if app.extensions["collector"].cfg.get("demo") and "settings" in store.data:
        # demo: meteen aan, zodat je het kunt zien
        for s in app.extensions["collector"].cfg["servers"]:
            store.data["settings"].setdefault(s["name"], {"enabled": True, "demo": True})
    app.extensions["guestupdates"] = store
    STORE = store
    app.register_blueprint(bp)
    threading.Thread(target=_scheduler, args=(app,), daemon=True).start()
    if app.extensions["collector"].cfg.get("demo"):
        for s in app.extensions["collector"].cfg["servers"]:
            threading.Timer(4, _run_bg, args=(app, s["name"])).start()


# ------------------------------------------------------------------ samenvatting (voor lijsten en kpi's)
def summary(server, vmid):
    if STORE is None:
        return None
    gres = (STORE.result(server).get("guests") or {}).get(str(vmid))
    if not gres:
        return None
    apps = gres.get("apps") or []
    main = next((a for a in apps if a.get("update")), None) or next((a for a in apps if a.get("main")), None) or (apps[0] if apps else None)
    return {"count": gres.get("count"), "security": gres.get("security") or 0, "pm": gres.get("pm"),
            "app": main.get("name") if main else None, "app_update": bool(main and main.get("update")),
            "app_latest": main.get("latest") if main else None, "app_installed": main.get("installed") if main else None,
            "stale": bool(gres.get("stale")), "checked": gres.get("checked"), "error": gres.get("error")}


def work_stats():
    """Containers met updates (pakketten of app), en het aantal security-updates daarin."""
    out = {"containers": 0, "security": 0, "packages": 0, "apps": 0}
    if STORE is None:
        return out
    for server, res in list(STORE.data["results"].items()):
        if not STORE.settings(server).get("enabled"):
            continue
        for gres in (res.get("guests") or {}).values():
            pk = gres.get("count") or 0
            app_upd = any(a.get("update") for a in gres.get("apps") or [])
            if pk > 0 or app_upd:
                out["containers"] += 1
                out["packages"] += max(0, pk)
                out["security"] += gres.get("security") or 0
                out["apps"] += 1 if app_upd else 0
    return out


def work_count():
    """Aantal containers met updates (pakketten of app)."""
    return work_stats()["containers"]


# ------------------------------------------------------------------ API
def _store():
    return current_app.extensions["guestupdates"]


def _admin():
    if g.get("scope") != "admin":
        abort(403)


@bp.get("/api/guest-updates")
def api_list():
    col = current_app.extensions["collector"]
    store = _store()
    servers = {}
    for s in col.cfg["servers"]:
        name = s["name"]
        st, res = store.settings(name), store.result(name)
        grp = col.group(name) or {}
        servers[name] = {"enabled": bool(st.get("enabled")), "checking": name in store.running,
                         "checked": res.get("checked"), "nodes": res.get("nodes") or {},
                         "group": grp.get("name", name), "kind": grp.get("kind")}
    guests = {}
    for name, res in store.data["results"].items():
        grp = col.group(name)
        if not grp:
            continue
        names = {x["vmid"]: x for x in grp["guests"]}
        for vmid, gres in (res.get("guests") or {}).items():
            x = names.get(int(vmid))
            if not x:
                continue
            guests["{}/{}".format(name, vmid)] = dict(gres, server=name, name=x["name"], status=x["status"], node=x["node"],
                                                   repo_override=store.data["repos"].get("{}/{}".format(name, vmid)))
    sc = schedule(col, store)
    lasts = [store.result(n).get("checked") or 0 for n in servers if servers[n]["enabled"]]
    return jsonify({"servers": servers, "guests": guests, "hours": sc.get("hours"), "schedule": sc,
                    "next": next_run(sc, min(lasts)) if lasts else None, "admin": g.get("scope") == "admin",
                    "demo": bool(col.cfg.get("demo"))})


@bp.post("/api/guest-updates/schedule")
def api_schedule():
    _admin()
    body = request.get_json(silent=True) or {}
    store = _store()
    if body.get("mode") == "daily":
        at = str(body.get("at") or "").strip()
        if not AT_RE.match(at):
            return jsonify({"error": "Kies een tijdstip, bijvoorbeeld 05:00"}), 400
        sc = {"mode": "daily", "at": at}
        label = "dagelijks om " + at
    else:
        try:
            hours = int(body.get("hours"))
        except (TypeError, ValueError):
            return jsonify({"error": "Kies hoe vaak"}), 400
        if hours not in HOURS_CHOICES:
            return jsonify({"error": "Kies 1, 2, 3, 6, 12 of 24 uur"}), 400
        sc = {"mode": "interval", "hours": hours}
        label = "elke {} uur".format(hours)
    with store.lock:
        store.data["schedule"] = sc
        store.save()
    from .actions import audit
    audit({"who": g.who, "action": "guest_updates", "ok": True, "label": "Updates in containers controleren: " + label})
    return jsonify({"ok": True, "schedule": sc})


@bp.get("/api/guest-updates/setup")
def api_setup():
    _admin()
    _, pub = ensure_key(_store())
    return jsonify({"public_key": pub, "command": setup_command(pub), "remove": remove_command()})


@bp.post("/api/guest-updates/<server>/settings")
def api_settings(server):
    _admin()
    col = current_app.extensions["collector"]
    if not col.client(server):
        return jsonify({"error": "onbekende server"}), 404
    body = request.get_json(silent=True) or {}
    store = _store()
    with store.lock:
        st = dict(store.settings(server))
        st["enabled"] = bool(body.get("enabled", True))
        try:
            st["port"] = max(1, min(65535, int(body.get("port") or st.get("port") or 22)))
        except ValueError:
            return jsonify({"error": "ongeldige poort"}), 400
        store.data["settings"][server] = st
        if not st["enabled"]:
            store.data["results"].pop(server, None)
        store.save()
    from .actions import audit
    audit({"who": g.who, "action": "guest_updates", "server": server, "ok": True,
           "label": "Updates in containers {}: {}".format("aangezet" if st["enabled"] else "uitgezet", server)})
    if st["enabled"]:
        _run_bg(current_app._get_current_object(), server)
    return jsonify({"ok": True})


@bp.post("/api/guest-updates/<server>/check")
def api_check(server):
    _admin()
    if not _store().settings(server).get("enabled"):
        return jsonify({"error": "Zet eerst 'Updates in containers' aan voor deze server"}), 409
    started = _run_bg(current_app._get_current_object(), server)
    return jsonify({"ok": True, "started": started})


@bp.post("/api/guest-updates/repo")
def api_repo():
    _admin()
    body = request.get_json(silent=True) or {}
    server, vmid = str(body.get("server", "")), str(body.get("vmid", ""))
    repo = str(body.get("repo") or "").strip()
    repo = re.sub(r"^https?://github\.com/", "", repo).strip("/")
    if repo and not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repo):
        return jsonify({"error": "Vul in als eigenaar/repository, bijvoorbeeld jellyfin/jellyfin"}), 400
    store = _store()
    with store.lock:
        key = "{}/{}".format(server, vmid)
        if repo:
            store.data["repos"][key] = repo
        else:
            store.data["repos"].pop(key, None)
        store.save()
    if store.settings(server).get("enabled"):
        _run_bg(current_app._get_current_object(), server)
    return jsonify({"ok": True})
