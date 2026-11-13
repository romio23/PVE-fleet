"""Servers beheren vanuit het dashboard: toevoegen, bewerken, testen, verwijderen.

Opslaan schrijft data/config.yml (de vorige versie blijft als config.yml.bak) en
de nieuwe instellingen worden meteen gebruikt, zonder herstart.
"""
import logging
import threading

from flask import Blueprint, current_app, jsonify, request

from . import config as config_mod
from .pve import PveClient, PveError, PvePermissionError

log = logging.getLogger(__name__)
bp = Blueprint("servers_api", __name__)
_save_lock = threading.Lock()


def _col():
    return current_app.extensions["collector"]


def _cfg():
    return _col().cfg


def _public(s):
    wol = dict(s.get("wol") or {})
    return {"name": s["name"], "type": s.get("type"), "hosts": s["hosts"], "port": s["port"], "token_id": s["token_id"],
            "has_secret": bool(s.get("token_secret")), "verify_ssl": s["verify_ssl"],
            "wol_mac": wol.pop("*", ""), "wol": wol}


def _find(name):
    return next((s for s in _cfg().get("real_servers", []) if s["name"] == name), None)


def _form_to_server(body):
    """Formulier -> ruwe serverdict; een leeg geheim betekent: het bestaande houden."""
    srv = dict(body.get("server") or {})
    original = body.get("original")
    if not str(srv.get("token_secret") or "").strip() and original:
        old = _find(original)
        if old:
            srv["token_secret"] = old["token_secret"]
    if isinstance(srv.get("wol"), str):   # "pve-01=AA:BB:..." per regel
        pairs = {}
        for line in srv["wol"].splitlines():
            key, sep, mac = line.partition("=")
            if sep and key.strip() and mac.strip():
                pairs[key.strip()] = mac.strip()
        srv["wol"] = pairs
    if srv.get("type") not in ("cluster", "standalone"):
        srv["type"] = None
    return srv, original


# ------------------------------------------------------------------ lezen
@bp.get("/api/servers")
def api_servers():
    cfg = _cfg()
    return jsonify({"servers": [_public(s) for s in cfg.get("real_servers", [])], "demo": cfg["demo"],
                    "config_path": config_mod.config_path(), "config_error": cfg.get("config_error")})


# ------------------------------------------------------------------ testen
def _probe_host(srv, host, out):
    c = PveClient([host], srv["token_id"], srv["token_secret"], port=srv["port"], verify_ssl=srv["verify_ssl"], timeout=6)
    try:
        v = c.get("/version") or {}
        out[host] = {"host": host, "ok": True, "version": v.get("version")}
    except PveError as e:
        out[host] = {"host": host, "ok": False, "error": str(e)}


def test_server(srv):
    # Elke host apart proberen (tegelijk), zodat je ziet welke wel/niet antwoordt.
    results, threads = {}, []
    for h in srv["hosts"]:
        t = threading.Thread(target=_probe_host, args=(srv, h, results))
        t.start()
        threads.append(t)
    for t in threads:
        t.join(15)
    hosts = [results.get(h, {"host": h, "ok": False, "error": "geen antwoord"}) for h in srv["hosts"]]
    good = [h["host"] for h in hosts if h["ok"]]
    out = {"ok": False, "hosts": hosts, "warnings": []}
    if not good:
        first = hosts[0].get("error", "") if hosts else ""
        auth = next((h["error"] for h in hosts if "aanmelden" in h.get("error", "") or "rechten" in h.get("error", "")), None)
        out["error"] = auth or first or "geen enkele host antwoordt"
        return out
    c = PveClient(good, srv["token_id"], srv["token_secret"], port=srv["port"], verify_ssl=srv["verify_ssl"], timeout=8)
    try:
        status = c.get("/cluster/status") or []
    except PvePermissionError as e:
        out["error"] = "{}. Het token kan wel aanmelden, maar mag niets lezen: geef het de rol PVEAuditor op pad /.".format(e)
        return out
    except PveError as e:
        out["error"] = str(e)
        return out
    cluster = next((x for x in status if x.get("type") == "cluster"), None)
    nodes = [{"name": x.get("name"), "ip": x.get("ip"), "online": x.get("online", 1) == 1}
             for x in status if x.get("type") == "node"]
    out.update({"ok": True, "kind": "cluster" if cluster else "standalone",
                "cluster_name": cluster.get("name") if cluster else None,
                "quorate": bool(cluster.get("quorate")) if cluster else None,
                "nodes": nodes, "version": hosts[[h["host"] for h in hosts].index(good[0])].get("version")})
    try:
        res = c.get("/cluster/resources") or []
        n_guests = sum(1 for r in res if r.get("type") in ("qemu", "lxc"))
        out["guests"] = n_guests
        if not any(r.get("type") == "node" for r in res):
            out["warnings"].append("Het token ziet geen nodes. Waarschijnlijk staat Privilege Separation aan zonder rechten op het token zelf.")
    except PveError as e:
        out["warnings"].append("Kon de lijst met VM's niet lezen: {}".format(e))
    out.update(check_permissions(c, srv["token_id"], out.get("version")))
    bad = [h for h in hosts if not h["ok"]]
    if bad:
        out["warnings"].append("Niet bereikbaar: " + ", ".join(h["host"] for h in bad) + ". Het dashboard gebruikt de andere hosts.")
    if cluster:
        missing = [n["ip"] for n in nodes if n.get("ip") and n["ip"] not in srv["hosts"]]
        if missing:
            out["suggest_hosts"] = [n["ip"] for n in nodes if n.get("ip")]
    return out


# Wat het dashboard per onderdeel nodig heeft (rechten op pad / of dieper)
PERM_CHECKS = [
    ("view", "Bekijken (nodes, VM's & containers, opslag)", ["Sys.Audit", "VM.Audit", "Datastore.Audit"]),
    ("power", "VM/CT starten, afsluiten, herstarten", ["VM.PowerMgmt"]),
    ("snapshot", "Snapshots maken en verwijderen", ["VM.Snapshot"]),
    ("rollback", "Snapshot terugdraaien", ["VM.Snapshot.Rollback"]),
    ("backup", "Back-up nu", ["VM.Backup", "Datastore.AllocateSpace"]),
    ("migrate", "Migreren", ["VM.Migrate"]),
    ("node_power", "Node herstarten, afsluiten, Wake-on-LAN", ["Sys.PowerMgmt"]),
    ("console", "Console van VM's & containers", ["VM.Console"]),
    ("shell", "Shell van een node", ["Sys.Console"]),
    ("agent", "IP-adressen van VM's (QEMU guest agent)", ["VM.GuestAgent.Audit"]),
]
# Optioneel: zonder deze rechten werkt alles, behalve dit ene onderdeel.
OPTIONAL_CHECKS = [
    ("jobs", "Openstaande updates lezen, back-upjobs aanpassen, certificaten op nodes zetten (optioneel)", ["Sys.Modify"]),
]
ACTION_PRIVS = "VM.PowerMgmt VM.Snapshot VM.Snapshot.Rollback VM.Backup VM.Migrate Datastore.AllocateSpace Sys.PowerMgmt VM.Console Sys.Console VM.GuestAgent.Audit"


def _major(version):
    try:
        return int(str(version).split(".")[0])
    except (TypeError, ValueError):
        return None


def agent_priv(version):
    """Proxmox 9 heeft VM.GuestAgent.Audit; Proxmox 8 en ouder gebruiken VM.Monitor."""
    m = _major(version)
    return "VM.Monitor" if m is not None and m < 9 else "VM.GuestAgent.Audit"


def check_permissions(c, token_id, version=None):
    """Lees de rechten van het token uit en maak passende pveum-commando's."""
    ap = agent_priv(version)
    checks = [(k, l, [ap if x == "VM.GuestAgent.Audit" else x for x in pr]) for k, l, pr in PERM_CHECKS]
    action_privs = ACTION_PRIVS.replace("VM.GuestAgent.Audit", ap)
    try:
        perms = c.get("/access/permissions") or {}
    except PveError as e:
        return {"perms": None, "perms_error": str(e)}
    have = set()
    for path, privs in perms.items():
        if isinstance(privs, dict) and (path == "/" or path.startswith(("/vms", "/nodes", "/storage"))):
            have.update(k for k, v in privs.items() if v)
    result = [{"key": key, "label": label, "privs": privs, "ok": all(p in have for p in privs),
               "missing": [p for p in privs if p not in have]} for key, label, privs in checks]
    optional = [{"key": key, "label": label, "privs": privs, "ok": all(p in have for p in privs), "optional": True,
                 "missing": [p for p in privs if p not in have]} for key, label, privs in OPTIONAL_CHECKS]
    user, _, token = token_id.partition("!")
    privsep = None
    try:
        info = c.get("/access/users/{}/token/{}".format(user, token)) or {}
        if "privsep" in info:
            privsep = bool(int(info["privsep"]))
    except PveError:
        pass
    view_missing = not result[0]["ok"]
    act_missing = any(not r["ok"] for r in result[1:])
    lines = []
    if act_missing:
        lines.append('pveum role add FleetOperator --privs "{0}" 2>/dev/null || pveum role modify FleetOperator --privs "{0}"'.format(action_privs))
    if view_missing or act_missing:
        roles = "PVEAuditor,FleetOperator" if act_missing else "PVEAuditor"
        lines.append("pveum acl modify / --users {} --roles {}".format(user, roles))
        if privsep is not False:
            if privsep is None:
                lines.append("# alleen nodig als het token 'Privilege Separation' aan heeft:")
            lines.append("pveum acl modify / --tokens '{}' --roles {}".format(token_id, roles))
    opt_lines = []
    if any(not o["ok"] for o in optional):
        opt_lines.append('pveum role add FleetJobs --privs "Sys.Modify" 2>/dev/null || pveum role modify FleetJobs --privs "Sys.Modify"')
        opt_lines.append("pveum acl modify / --users {} --roles FleetJobs".format(user))
        if privsep is not False:
            opt_lines.append("pveum acl modify / --tokens '{}' --roles FleetJobs".format(token_id))
    return {"perms": result + optional, "privsep": privsep, "commands": "\n".join(lines),
            "optional_commands": "\n".join(opt_lines)}


@bp.post("/api/servers/test")
def api_servers_test():
    srv, _orig = _form_to_server(request.get_json(silent=True) or {})
    try:
        parsed = config_mod.parse_server(srv)
    except config_mod.ConfigError as e:
        return jsonify({"ok": False, "error": str(e), "invalid": True})
    return jsonify(test_server(parsed))


# ------------------------------------------------------------------ opslaan
def _write_and_reload(servers, demo):
    cfg = _cfg()
    try:
        path = config_mod.save(cfg, servers=servers, demo=demo)
    except OSError as e:
        raise config_mod.ConfigError("Kan {} niet opslaan: {}. Controleer of de map 'data' schrijfbaar is.".format(
            config_mod.config_path(), e.strerror or e))
    new = config_mod.load()
    _col().reconfigure(new)
    log.info("config opgeslagen in %s (%d servers, demo=%s)", path, len(new["real_servers"]), new["demo"])
    return new


@bp.post("/api/servers/save")
def api_servers_save():
    body = request.get_json(silent=True) or {}
    srv, original = _form_to_server(body)
    with _save_lock:
        try:
            parsed = config_mod.parse_server(srv)
        except config_mod.ConfigError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        current = list(_cfg().get("real_servers", []))
        others = [s for s in current if s["name"] != original]
        if any(s["name"] == parsed["name"] for s in others):
            return jsonify({"ok": False, "error": "Er is al een server met de naam '{}'".format(parsed["name"])}), 400
        if original and _find(original):
            servers = [parsed if s["name"] == original else s for s in current]
        else:
            servers = current + [parsed]
        was_demo = _cfg()["demo"]
        try:
            new = _write_and_reload(servers, demo=False)   # wie echte servers invult, wil uit de demo
        except config_mod.ConfigError as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "demo_turned_off": was_demo and not new["demo"], "config_error": new.get("config_error")})


@bp.delete("/api/servers/<name>")
def api_servers_delete(name):
    with _save_lock:
        current = list(_cfg().get("real_servers", []))
        if not any(s["name"] == name for s in current):
            return jsonify({"ok": False, "error": "Onbekende server"}), 404
        try:
            _write_and_reload([s for s in current if s["name"] != name], demo=_cfg()["demo"])
        except config_mod.ConfigError as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True})


@bp.post("/api/servers/demo")
def api_servers_demo():
    on = bool((request.get_json(silent=True) or {}).get("demo"))
    with _save_lock:
        try:
            new = _write_and_reload(list(_cfg().get("real_servers", [])), demo=on)
        except config_mod.ConfigError as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "demo": new["demo"]})
