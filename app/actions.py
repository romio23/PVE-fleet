"""Beheeracties op Proxmox: VM's & containers, nodes, snapshots, back-ups, migratie, Wake-on-LAN.

Elke actie wordt eerst gecontroleerd tegen de laatst bekende toestand (bestaat de
VM/CT, staat de node online, is het doel geldig) en daarna naar Proxmox gestuurd.
Alles komt in data/actions.log.
"""
import json
import logging
import os
import re
import socket
import threading
import time

from flask import Blueprint, current_app, g, jsonify, request

from . import demo as demo_mod
from .pve import PveError, PvePermissionError, q
from .settings import Limiter

log = logging.getLogger(__name__)
bp = Blueprint("actions", __name__)

SNAP_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,39}$")
GUEST_POWER = {"start": "Starten", "shutdown": "Afsluiten", "reboot": "Herstarten", "stop": "Geforceerd stoppen"}
GUEST_ACTIONS = set(GUEST_POWER) | {"snapshot", "rollback", "delsnapshot", "backup", "migrate"}
NODE_ACTIONS = {"node_reboot": "Node herstarten", "node_shutdown": "Node afsluiten", "node_wol": "Wake-on-LAN"}
KIOSK_ACTIONS = {"node_reboot", "node_shutdown", "node_wol", "refresh"}

pin_limiter = Limiter(5)
_unlocked = {}        # client_key -> tijdstip tot wanneer de pincode niet opnieuw nodig is
_log_lock = threading.Lock()


class ActionError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def col():
    return current_app.extensions["collector"]


def settings():
    return current_app.extensions["settings"]


# ------------------------------------------------------------------ logboek
def _log_path():
    return os.path.join(current_app.config["PVEF_DATA"], "actions.log")


def audit(entry):
    entry = dict(entry, time=int(time.time()))
    path = _log_path()
    with _log_lock:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            if os.path.getsize(path) > 1024 * 1024:   # houd het klein: laatste 2000 regels
                with open(path, encoding="utf-8") as fh:
                    lines = fh.readlines()[-2000:]
                with open(path, "w", encoding="utf-8") as fh:
                    fh.writelines(lines)
        except OSError as e:
            log.warning("kon actions.log niet schrijven: %s", e)


def read_audit(limit=50):
    try:
        with open(_log_path(), encoding="utf-8") as fh:
            lines = fh.readlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


# ------------------------------------------------------------------ hulpfuncties
def _group(server):
    g_ = col().group(server)
    if not g_:
        raise ActionError("Onbekende server '{}'".format(server), 404)
    return g_


def _node(group, node):
    n = next((x for x in group["nodes"] if x["name"] == node), None)
    if not n:
        raise ActionError("Onbekende node '{}'".format(node), 404)
    return n


def _online_node(group, node):
    if not group["reachable"]:
        raise ActionError("{} is op dit moment niet bereikbaar".format(group["name"]), 409)
    n = _node(group, node)
    if n["status"] != "online":
        raise ActionError("Node {} is offline".format(node), 409)
    return n


def _guest(group, node, gtype, vmid):
    row = next((x for x in group["guests"] if x["vmid"] == vmid and x["node"] == node and x["type"] == gtype), None)
    if not row:
        raise ActionError("VM/CT {} niet gevonden op {} (misschien net verplaatst; ververs de pagina)".format(vmid, node), 404)
    return row


def _label(row):
    return "{} {} {}".format("VM" if row["type"] == "qemu" else "CT", row["vmid"], row["name"])


def magic_packet(mac, broadcast):
    raw = bytes.fromhex(re.sub(r"[^0-9A-Fa-f]", "", mac))
    pkt = b"\xff" * 6 + raw * 16
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for port in (9, 7):
            s.sendto(pkt, (broadcast, port))
    finally:
        s.close()


def migrate_info(server, node, gtype, vmid):
    group = _group(server)
    _online_node(group, node)
    row = _guest(group, node, gtype, vmid)
    peers = [n for n in group["nodes"] if n["name"] != node and n["status"] == "online"]
    info = {"running": row["status"] == "running", "local_disks": [], "local_resources": [], "allowed": None}
    if gtype == "qemu":
        try:
            pre = col().client(server).get("/nodes/{}/qemu/{}/migrate".format(q(node), vmid)) or {}
            info["allowed"] = pre.get("allowed_nodes")
            info["local_disks"] = [d.get("volid") for d in pre.get("local_disks") or [] if not d.get("is_unused")]
            info["local_resources"] = pre.get("local_resources") or []
            info["not_allowed"] = pre.get("not_allowed_nodes") or {}
        except PveError as e:
            info["warning"] = "Kon migratie-voorcontrole niet ophalen: {}".format(e)
    targets = []
    for n in sorted(peers, key=lambda x: (x["mem_pct"] or 0)):
        ok = info["allowed"] is None or n["name"] in info["allowed"]
        reason = None
        if not ok:
            na = (info.get("not_allowed") or {}).get(n["name"]) or {}
            reason = "niet toegestaan" + (": " + ", ".join(na.get("unavailable_storages", [])) if na.get("unavailable_storages") else "")
        targets.append({"name": n["name"], "mem_pct": n["mem_pct"], "cpu": n["cpu"], "allowed": ok, "reason": reason})
    info["targets"] = targets
    info["cluster"] = group["kind"] == "cluster"
    return info


def backup_storages(server, node):
    group = _group(server)
    return [{"name": s["name"], "type": s["type"], "pct": s["pct"], "shared": s["shared"]}
            for s in group["storage"] if s["node"] == node and "backup" in (s["content"] or "") and s["status"] == "available"]


# ------------------------------------------------------------------ uitvoeren
def execute(body, who):
    """Voert één actie uit. Geeft {'ok', 'upid', 'node', 'server', 'label', 'message'} terug."""
    action = str(body.get("action", ""))
    server = str(body.get("server", ""))
    node = str(body.get("node", ""))
    params = body.get("params") or {}
    cfg = col().cfg
    c = col().client(server)
    if not c:
        raise ActionError("Onbekende server '{}'".format(server), 404)

    if action == "refresh":
        col().refresh(server)
        return {"ok": True, "server": server, "node": node, "upid": None, "label": "Opnieuw verbinden", "message": "Opnieuw opgehaald"}

    group = _group(server)

    if action in NODE_ACTIONS:
        label = "{}: {}".format(NODE_ACTIONS[action], node)
        if action == "node_wol":
            return _wol(group, server, node, label, cfg)
        _online_node(group, node)
        command = "reboot" if action == "node_reboot" else "shutdown"
        res = c.post("/nodes/{}/status".format(q(node)), {"command": command})
        return {"ok": True, "server": server, "node": node, "upid": res if isinstance(res, str) and res.startswith("UPID") else None,
                "label": label, "message": "Opdracht verstuurd: {} {}".format(node, "herstart" if command == "reboot" else "gaat uit")}

    if action not in GUEST_ACTIONS:
        raise ActionError("Onbekende actie '{}'".format(action))
    gtype = str(body.get("type", ""))
    if gtype not in ("qemu", "lxc"):
        raise ActionError("Ongeldig type")
    try:
        vmid = int(body.get("vmid"))
    except (TypeError, ValueError):
        raise ActionError("Ongeldig ID")
    _online_node(group, node)
    row = _guest(group, node, gtype, vmid)
    base = "/nodes/{}/{}/{}".format(q(node), gtype, vmid)
    running = row["status"] == "running"

    if action in GUEST_POWER:
        if action == "start" and running:
            raise ActionError("{} draait al".format(_label(row)), 409)
        if action != "start" and not running:
            raise ActionError("{} draait niet".format(_label(row)), 409)
        upid = c.post(base + "/status/" + action)
        label = "{}: {}".format(GUEST_POWER[action], _label(row))
    elif action == "snapshot":
        name = str(params.get("snapname", "")).strip()
        if not SNAP_RE.match(name):
            raise ActionError("Snapshotnaam: begin met een letter, daarna letters, cijfers, - of _ (2-40 tekens)")
        data = {"snapname": name}
        desc = str(params.get("description", "")).strip()[:200]
        if desc:
            data["description"] = desc
        if gtype == "qemu" and running and params.get("vmstate"):
            data["vmstate"] = 1
        upid = c.post(base + "/snapshot", data)
        label = "Snapshot '{}': {}".format(name, _label(row))
    elif action in ("rollback", "delsnapshot"):
        name = str(params.get("snapname", ""))
        if not SNAP_RE.match(name):
            raise ActionError("Ongeldige snapshotnaam")
        if action == "rollback":
            upid = c.post(base + "/snapshot/{}/rollback".format(q(name)))
            label = "Terugdraaien naar '{}': {}".format(name, _label(row))
        else:
            upid = c.delete(base + "/snapshot/{}".format(q(name)))
            label = "Snapshot '{}' verwijderen: {}".format(name, _label(row))
    elif action == "backup":
        storage = str(params.get("storage", ""))
        allowed = [s["name"] for s in backup_storages(server, node)]
        if storage not in allowed:
            raise ActionError("Opslag '{}' is geen beschikbare back-upopslag op {}".format(storage, node))
        mode = params.get("mode", "snapshot")
        if mode not in ("snapshot", "suspend", "stop"):
            raise ActionError("Ongeldige back-upmodus")
        upid = c.post("/nodes/{}/vzdump".format(q(node)), {"vmid": vmid, "storage": storage, "mode": mode, "compress": "zstd"})
        label = "Back-up naar {}: {}".format(storage, _label(row))
    elif action == "migrate":
        if group["kind"] != "cluster":
            raise ActionError("Migreren kan alleen binnen een cluster")
        target = str(params.get("target", ""))
        tn = next((n for n in group["nodes"] if n["name"] == target), None)
        if not tn or target == node or tn["status"] != "online":
            raise ActionError("Doel-node '{}' is niet beschikbaar".format(target))
        data = {"target": target}
        if gtype == "qemu":
            if running and params.get("online", True):
                data["online"] = 1
            if params.get("with_local_disks"):
                data["with-local-disks"] = 1
        elif running:
            data["restart"] = 1
        upid = c.post(base + "/migrate", data)
        label = "Migreren naar {}: {}".format(target, _label(row))
    else:  # pragma: no cover
        raise ActionError("Onbekende actie")
    return {"ok": True, "server": server, "node": node, "upid": upid if isinstance(upid, str) else None,
            "label": label, "message": "Gestart: " + label}


def _wol(group, server, node, label, cfg):
    srv = col().server_cfg(server) or {}
    if cfg.get("demo"):
        if demo_mod.wake(server, node):
            return {"ok": True, "server": server, "node": node, "upid": None, "label": label,
                    "message": "Wake-on-LAN verstuurd naar {} (demo: online over ~20 s)".format(node)}
        raise ActionError("{} staat niet uit".format(node), 409)
    wol = srv.get("wol") or {}
    mac = wol.get(node) or (wol.get("*") if group["kind"] != "cluster" or len(group["nodes"]) <= 1 else None)
    others = [n for n in group["nodes"] if n["name"] != node and n["status"] == "online"]
    if group["kind"] == "cluster" and group["reachable"] and others:
        try:
            col().client(server).post("/nodes/{}/wakeonlan".format(q(node)))
            return {"ok": True, "server": server, "node": node, "upid": None, "label": label,
                    "message": "Wake-on-LAN verstuurd via het cluster naar {}".format(node)}
        except PveError as e:
            if not mac:
                raise ActionError("Proxmox kon {} niet wekken: {}. Stel het MAC-adres in op de node "
                                  "(pvenode config set -wakeonlan <MAC>) of zet het in config.yml.".format(node, e))
    if not mac:
        raise ActionError("Geen MAC-adres bekend voor {}. Zet 'wol_mac' bij deze server in config.yml.".format(node))
    try:
        magic_packet(mac, cfg.get("wol_broadcast") or "255.255.255.255")
    except OSError as e:
        raise ActionError("Kon het Wake-on-LAN-pakket niet versturen: {}".format(e), 500)
    return {"ok": True, "server": server, "node": node, "upid": None, "label": label,
            "message": "Wake-on-LAN verstuurd naar {} ({})".format(node, mac)}


def _run(body, who):
    try:
        res = execute(body, who)
    except ActionError as e:
        audit({"who": who, "action": body.get("action"), "server": body.get("server"), "node": body.get("node"),
               "vmid": body.get("vmid"), "ok": False, "error": str(e)})
        return jsonify({"ok": False, "error": str(e)}), e.status
    except PvePermissionError as e:
        m = re.search(r"\(([^,()]+),\s*([A-Za-z.]+)\)", str(e))
        if m:
            msg = ("Het API-token mist het recht {} (op {}). Oplossen: Servers & verbinding › Bewerken › "
                   "Verbinding testen; daar staan de commando's die je in de shell van een node plakt.").format(m.group(2), m.group(1))
        else:
            msg = "Het API-token mist rechten voor deze actie ({}). Kijk bij Servers & verbinding › Verbinding testen.".format(e)
        audit({"who": who, "action": body.get("action"), "server": body.get("server"), "node": body.get("node"),
               "vmid": body.get("vmid"), "ok": False, "error": msg})
        return jsonify({"ok": False, "error": msg}), 403
    except PveError as e:
        audit({"who": who, "action": body.get("action"), "server": body.get("server"), "node": body.get("node"),
               "vmid": body.get("vmid"), "ok": False, "error": str(e)})
        return jsonify({"ok": False, "error": "Proxmox: {}".format(e)}), 502
    audit({"who": who, "action": body.get("action"), "server": res["server"], "node": res["node"],
           "vmid": body.get("vmid"), "label": res["label"], "upid": res["upid"], "ok": True})
    if body.get("action") != "refresh":
        threading.Timer(1.5, _safe_refresh, args=(current_app._get_current_object(), res["server"])).start()
    return jsonify(res)


def _safe_refresh(app, server):
    with app.app_context():
        try:
            app.extensions["collector"].refresh(server)
        except Exception:  # pragma: no cover
            log.exception("verversen na actie")


# ------------------------------------------------------------------ API: dashboard (admin)
@bp.post("/api/action")
def api_action():
    if g.scope != "admin":
        return jsonify({"error": "alleen voor beheerder"}), 403
    return _run(request.get_json(silent=True) or {}, g.who)


@bp.get("/api/task/<server>/<node>/<path:upid>")
def api_task(server, node, upid):
    c = col().client(server)
    if not c:
        return jsonify({"error": "onbekende server"}), 404
    try:
        st = c.get("/nodes/{}/tasks/{}/status".format(q(node), q(upid))) or {}
    except PveError as e:
        return jsonify({"error": str(e)}), 502
    lines = []
    try:
        logrows = c.get("/nodes/{}/tasks/{}/log".format(q(node), q(upid)), {"start": 0, "limit": 500}) or []
        lines = [r.get("t", "") for r in logrows][-80:]
    except PveError:
        pass
    done = st.get("status") == "stopped"
    return jsonify({"running": not done, "ok": done and st.get("exitstatus") == "OK",
                    "exitstatus": st.get("exitstatus"), "log": lines})


@bp.get("/api/migrate-info/<server>/<node>/<gtype>/<int:vmid>")
def api_migrate_info(server, node, gtype, vmid):
    try:
        return jsonify(migrate_info(server, node, gtype, vmid))
    except ActionError as e:
        return jsonify({"error": str(e)}), e.status


@bp.get("/api/backup-storages/<server>/<node>")
def api_backup_storages(server, node):
    try:
        return jsonify({"storages": backup_storages(server, node)})
    except ActionError as e:
        return jsonify({"error": str(e)}), e.status


@bp.post("/api/refresh/<server>")
def api_refresh(server):
    if not col().client(server):
        return jsonify({"error": "onbekende server"}), 404
    col().refresh(server)
    return jsonify({"ok": True})


@bp.post("/api/alerts/ack")
def api_ack():
    body = request.get_json(silent=True) or {}
    if not body.get("id"):
        return jsonify({"error": "id ontbreekt"}), 400
    col().ack(body["id"], bool(body.get("acked", True)))
    return jsonify({"ok": True})


@bp.get("/api/actions-log")
def api_actions_log():
    return jsonify({"entries": read_audit(60)})


# ------------------------------------------------------------------ API: kiosk
def kiosk_state():
    k = settings().kiosk
    key = g.client_key or "?"
    return {"scope": g.scope, "who": g.who, "is_admin": g.scope == "admin", "readonly": k["readonly"],
            "pin_enabled": k["pin_enabled"], "pin_set": bool(k.get("pin_hash")), "pin_len": k.get("pin_len") or 4, "pin_actions": k["pin_actions"],
            "unlock_seconds": k["unlock_seconds"], "allow_ack": k["allow_ack"],
            "unlocked_for": max(0, int(_unlocked.get(key, 0) - time.time())),
            "locked_for": int(min(pin_limiter.locked_for(key), 10 ** 9))}


def _check_pin(action_key, pin):
    """None als het mag; anders (json, status)."""
    k = settings().kiosk
    if not k["pin_enabled"] or action_key not in k["pin_actions"]:
        return None
    if not k.get("pin_hash"):
        return {"error": "Er is nog geen pincode ingesteld (Instellingen › Kiosk & schermen)", "code": "nopin"}, 403
    key = g.client_key or "?"
    if _unlocked.get(key, 0) > time.time():
        return None
    wait = pin_limiter.locked_for(key)
    if wait:
        return {"error": "Te vaak een foute pincode. Acties zijn geblokkeerd" + (
            "." if wait > 10 ** 8 else " (nog {} s).".format(int(wait) + 1)), "code": "locked",
            "locked_for": int(min(wait, 10 ** 9))}, 429
    if not pin:
        return {"error": "Pincode nodig", "code": "pin"}, 401
    if settings().check_pin(str(pin)):
        pin_limiter.reset(key)
        if k["unlock_seconds"] > 0:
            _unlocked[key] = time.time() + k["unlock_seconds"]
        return None
    left, locked = pin_limiter.fail(key, k["lockout_seconds"])
    audit({"who": g.who, "action": "pin", "ok": False, "error": "foute pincode"})
    if locked:
        col().set_extra_alert("kiosk-lock:" + key, "warn",
                              "Kiosk '{}' geblokkeerd na 5 foute pincodes".format((g.who or "").replace("kiosk: ", "")))
        return {"error": "Te vaak een foute pincode. Acties zijn geblokkeerd.", "code": "locked",
                "locked_for": int(min(pin_limiter.locked_for(key), 10 ** 9))}, 429
    return {"error": "Onjuiste pincode, nog {} {}".format(left, "poging" if left == 1 else "pogingen"),
            "code": "pin", "remaining": left}, 401


@bp.get("/api/kiosk/state")
def api_kiosk_state():
    return jsonify(kiosk_state())


@bp.post("/api/kiosk/action")
def api_kiosk_action():
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    if action not in KIOSK_ACTIONS:
        return jsonify({"error": "Deze actie kan niet vanaf de kiosk"}), 403
    if settings().kiosk["readonly"] and action != "refresh":
        return jsonify({"error": "De kiosk staat op 'alleen bekijken'"}), 403
    if action != "refresh":
        bad = _check_pin(action, body.get("pin"))
        if bad:
            return jsonify(bad[0]), bad[1]
    return _run({k: body.get(k) for k in ("action", "server", "node")}, g.who)


@bp.post("/api/kiosk/ack")
def api_kiosk_ack():
    body = request.get_json(silent=True) or {}
    if not settings().kiosk["allow_ack"]:
        return jsonify({"error": "Meldingen bevestigen staat uit voor de kiosk"}), 403
    bad = _check_pin("ack", body.get("pin"))
    if bad:
        return jsonify(bad[0]), bad[1]
    if not body.get("id"):
        return jsonify({"error": "id ontbreekt"}), 400
    col().ack(body["id"], bool(body.get("acked", True)))
    return jsonify({"ok": True})
