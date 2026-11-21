"""Stap 3: updates van nodes en de begeleide rolling update voor een cluster.

Proxmox heeft geen API om pakketten te installeren met een API-token: de knop "Upgrade"
in Proxmox opent een console waarin je als root inlogt. PVE Fleet doet hetzelfde via de
ingebouwde console en typt het upgrade-commando voor je in; jij bevestigt.

De rolling update werkt node voor node: VM's & containers verplaatsen, bijwerken,
herstarten, terugzetten. Welke VM/CT's zijn verplaatst, onthoudt PVE Fleet in
data/rolling.json, zodat terugzetten ook na het verversen van de pagina werkt.
"""
import json
import os
import threading
import time

from flask import Blueprint, abort, current_app, g, jsonify, render_template, request

from .pve import PveError, q

bp = Blueprint("updates", __name__)
_lock = threading.Lock()


def col():
    return current_app.extensions["collector"]


def _admin():
    if g.get("scope") != "admin":
        abort(403)


# ------------------------------------------------------------------ plan (rolling.json)
def _plan_path():
    return os.path.join(current_app.config["PVEF_DATA"], "rolling.json")


def _load_plans():
    try:
        with open(_plan_path(), encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_plans(plans):
    path = _plan_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(plans, fh, indent=1)
    os.replace(tmp, path)


def _plan(server):
    p = _load_plans().get(server) or {}
    p.setdefault("nodes", {})
    return p


# ------------------------------------------------------------------ pagina's
@bp.get("/updates")
def updates_page():
    return render_template("updates.html", page="updates")


@bp.get("/updates/rolling/<server>")
def rolling_page(server):
    grp = col().group(server)
    if not grp:
        abort(404)
    return render_template("rolling.html", page="updates", server=server)


# ------------------------------------------------------------------ API
def _node_row(grp, n):
    u = n.get("updates") or {}
    running = sum(1 for x in grp["guests"] if x["node"] == n["name"] and x["status"] == "running")
    return {"name": n["name"], "status": n["status"], "ip": n.get("ip"), "version": n.get("version"),
            "kernel": n.get("kernel"), "uptime": n.get("uptime"), "mem_pct": n.get("mem_pct"), "cpu": n.get("cpu"),
            "mem_total": n.get("mem_total"),
            "count": u.get("count"), "security": u.get("security"), "checked": u.get("checked"), "error": u.get("error"),
            "kernel_latest": u.get("kernel_latest"), "reboot_needed": bool(u.get("reboot_needed")),
            "kernel_pending": bool(u.get("kernel_pending")),
            "guests_running": running,
            "guests_total": sum(1 for x in grp["guests"] if x["node"] == n["name"])}


@bp.get("/api/updates")
def api_updates():
    out = []
    with col().lock:
        groups = list(col().snapshot.get("_full", []))
    plans = _load_plans()
    for grp in groups:
        nodes = [_node_row(grp, n) for n in grp["nodes"]]
        newest_v, newest_k = _annotate(nodes)
        out.append({"server": grp["server"], "name": grp["name"], "kind": grp["kind"], "reachable": grp["reachable"],
                    "error": grp.get("error"), "quorate": grp.get("quorate"), "nodes": nodes,
                    "newest_version": newest_v, "newest_kernel": newest_k,
                    "rolling": bool((plans.get(grp["server"]) or {}).get("started"))})
    return jsonify({"groups": out, "ready": col().get_snapshot().get("ready", False)})


def _annotate(nodes):
    """Ook zonder rechten op de pakketlijst: loopt een node achter op de nieuwste node in het cluster?"""
    from .collector import _verkey
    on = [n for n in nodes if n["status"] == "online"]
    newest_v = max((n["version"] for n in on if n["version"]), key=_verkey, default=None)
    newest_k = max((n["kernel_latest"] or n["kernel"] for n in on if (n["kernel_latest"] or n["kernel"])), key=_verkey, default=None)
    for n in nodes:
        n["no_access"] = bool(n["error"] and "rechten" in n["error"])
        behind = []
        if newest_v and n["version"] and _verkey(n["version"]) < _verkey(newest_v):
            behind.append("PVE {} → {}".format(n["version"], newest_v))
        mine_k = n["kernel_latest"] or n["kernel"]
        if newest_k and mine_k and _verkey(mine_k) < _verkey(newest_k):
            behind.append("kernel {} → {}".format(mine_k, newest_k))
        n["behind"] = behind
        # Na bijwerken komt er een nieuwe kernel (dus straks een herstart)
        n["kernel_after_upgrade"] = bool(n.get("kernel_pending") or
                                         (newest_k and mine_k and _verkey(mine_k) < _verkey(newest_k)))
    return newest_v, newest_k


@bp.get("/api/updates/<server>/<node>/packages")
def api_packages(server, node):
    c = col().client(server)
    if not c:
        return jsonify({"error": "onbekende server"}), 404
    try:
        pk = c.get("/nodes/{}/apt/update".format(q(node))) or []
    except PveError as e:
        return jsonify({"error": str(e)}), 502
    pk = sorted(({"name": p.get("Package"), "old": p.get("OldVersion"), "new": p.get("Version"),
                  "origin": p.get("Origin"), "title": p.get("Title")} for p in pk),
                key=lambda p: (not ("security" in (p["origin"] or "").lower()), p["name"] or ""))
    return jsonify({"packages": pk})


@bp.post("/api/updates/<server>/<node>/check")
def api_check(server, node):
    _admin()
    if not col().client(server):
        return jsonify({"error": "onbekende server"}), 404
    body = request.get_json(silent=True) or {}
    cur, note = col().check_updates(server, node, refresh_list=bool(body.get("refresh_list")))
    return jsonify({"ok": True, "updates": cur, "note": note})


# ---------- rolling update
@bp.get("/api/rolling/<server>")
def api_rolling(server):
    grp = col().group(server)
    if not grp:
        return jsonify({"error": "onbekende server"}), 404
    plan = _plan(server)
    nodes = [_node_row(grp, n) for n in grp["nodes"]]
    _annotate(nodes)
    where = {(x["type"], x["vmid"]): x for x in grp["guests"]}
    for n in nodes:
        np = plan["nodes"].get(n["name"]) or {}
        moved = []
        for m in np.get("moved", []):
            cur = where.get((m["type"], m["vmid"]))
            moved.append(dict(m, now=cur["node"] if cur else None, status=cur["status"] if cur else None,
                              name=cur["name"] if cur else m.get("name")))
        n["moved"] = moved
        n["done"] = bool(np.get("done"))
        n["skip_move"] = bool(np.get("skip_move"))
        n["stay"] = [dict(m, now=where[(m["type"], m["vmid"])]["node"] if (m["type"], m["vmid"]) in where else None,
                          status=where[(m["type"], m["vmid"])]["status"] if (m["type"], m["vmid"]) in where else None)
                     for m in np.get("stay", [])]
        n["guests"] = [{"vmid": x["vmid"], "type": x["type"], "name": x["name"], "status": x["status"],
                        "maxmem": x.get("maxmem"), "mem": x.get("mem")}
                       for x in grp["guests"] if x["node"] == n["name"]]
    return jsonify({"server": server, "name": grp["name"], "kind": grp["kind"], "reachable": grp["reachable"],
                    "quorate": grp.get("quorate"), "nodes": nodes, "started": plan.get("started"),
                    "order": plan.get("order") or [n["name"] for n in nodes]})


@bp.post("/api/rolling/<server>")
def api_rolling_update(server):
    """Plan bijwerken. body: {op: start|moved|done|undone|reset, node, moved:[{vmid,type,name,to}]}"""
    _admin()
    grp = col().group(server)
    if not grp:
        return jsonify({"error": "onbekende server"}), 404
    body = request.get_json(silent=True) or {}
    op, node = body.get("op"), str(body.get("node", ""))
    if op != "start" and op != "reset" and node not in [n["name"] for n in grp["nodes"]]:
        return jsonify({"error": "onbekende node"}), 400
    from .actions import audit
    with _lock:
        plans = _load_plans()
        plan = plans.get(server) or {"nodes": {}}
        plan.setdefault("nodes", {})
        if op == "start":
            plan["started"] = plan.get("started") or int(time.time())
            order = body.get("order") or [n["name"] for n in grp["nodes"]]
            plan["order"] = [x for x in order if x in [n["name"] for n in grp["nodes"]]]
            audit({"who": g.who, "action": "rolling", "server": server, "label": "Rolling update gestart: " + grp["name"], "ok": True})
        elif op == "reset":
            audit({"who": g.who, "action": "rolling", "server": server, "label": "Rolling update afgesloten: " + grp["name"], "ok": True})
            plan = {"nodes": {}}
        elif op == "moved":
            np = plan["nodes"].setdefault(node, {})
            have = {(m["type"], m["vmid"]): m for m in np.get("moved", [])}
            for m in body.get("moved") or []:
                try:
                    item = {"vmid": int(m["vmid"]), "type": str(m["type"]), "name": str(m.get("name", ""))[:80],
                            "to": str(m.get("to", ""))}
                except (KeyError, TypeError, ValueError):
                    continue
                old = have.get((item["type"], item["vmid"]))
                if old:
                    old.update(item)
                    old.pop("returned", None)
                else:
                    np.setdefault("moved", []).append(item)
        elif op == "returned":
            np = plan["nodes"].setdefault(node, {})
            back = {(str(m.get("type")), int(m.get("vmid", 0))) for m in body.get("moved") or []}
            for m in np.get("moved", []):
                if (m["type"], m["vmid"]) in back:
                    m["returned"] = True
                    if body.get("kept"):
                        m["kept"] = True
        elif op in ("skipmove", "unskipmove", "stay"):
            # stay: deze VM/CT's niet verplaatsen (ze gaan bij de herstart even uit; daarna weer starten)
            np = plan["nodes"].setdefault(node, {})
            if op == "unskipmove":
                np["skip_move"] = False
                np["stay"] = []
            else:
                if op == "skipmove":
                    np["skip_move"] = True
                stay = []
                for m in body.get("stay") or []:
                    try:
                        stay.append({"vmid": int(m["vmid"]), "type": str(m["type"]), "name": str(m.get("name", ""))[:80]})
                    except (KeyError, TypeError, ValueError):
                        continue
                np["stay"] = stay
        elif op in ("done", "undone"):
            plan["nodes"].setdefault(node, {})["done"] = op == "done"
            if op == "done":
                audit({"who": g.who, "action": "rolling", "server": server, "node": node,
                       "label": "Rolling update: {} klaar".format(node), "ok": True})
        else:
            return jsonify({"error": "onbekende opdracht"}), 400
        if plan.get("nodes") or plan.get("started"):
            plans[server] = plan
        else:
            plans.pop(server, None)
        try:
            _save_plans(plans)
        except OSError as e:
            return jsonify({"error": "Kon rolling.json niet opslaan: {}".format(e)}), 500
    return jsonify({"ok": True})
