"""Automatische rolling update: alle nodes van een cluster na elkaar bijwerken, onbeheerd.

Per node:
  1. de gekozen draaiende VM's & containers naar andere nodes verplaatsen (de rest blijft staan);
  2. de node bijwerken via SSH met de app-sleutel (deploy.sh, opdracht 'nodeupgrade': apt-get dist-upgrade,
     eigen configuratiebestanden blijven staan);
  3. herstarten als er een nieuwe kernel is, en wachten tot de node terug is;
  4. VM's & containers die bleven staan weer starten, en de verplaatste terugzetten;
  5. door naar de volgende node.
Bij elke fout stopt PVE Fleet bij die node en gaat niet verder. Wat er verplaatst is, staat in het
gewone rolling-plan (rolling.json), zodat je stap voor stap verder kunt op de pagina Rolling update.
"""
import json
import logging
import os
import secrets
import threading
import time

from flask import Blueprint, abort, current_app, g, jsonify, request

from . import appdeploy
from .pve import PveError, q

log = logging.getLogger(__name__)
bp = Blueprint("autorolling", __name__)

_lock = threading.Lock()
RUNS = {}            # server -> run
TIMING = {"poll": 5.0, "down_wait": 300, "up_wait": 900, "settle": 20, "migrate": 3600, "upgrade": 3600}


class Stopped(Exception):
    pass


def _admin():
    if g.get("scope") != "admin":
        abort(403)


# ------------------------------------------------------------------ opslag
def _path(app):
    return os.path.join(app.config["PVEF_DATA"], "autorolling.json")


def _save(app):
    with _lock:
        data = {k: _public(v) for k, v in RUNS.items()}
    try:
        tmp = _path(app) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, _path(app))
    except OSError:
        log.warning("kon autorolling.json niet opslaan")


def _load(app):
    try:
        with open(_path(app), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    for server, run in data.items():
        if not isinstance(run, dict):
            continue
        if run.get("status") == "running":    # PVE Fleet is herstart terwijl het liep
            run["status"] = "interrupted"
            run["finished"] = run.get("finished") or time.time()
            run["error"] = "PVE Fleet is herstart terwijl de rolling update liep. Kijk bij de node die bezig was en ga stap voor stap verder."
            for n in run.get("nodes", []):
                if n.get("state") == "busy":
                    n["state"] = "fail"
                    n["msg"] = "onderbroken"
        run["stop"] = False
        RUNS[server] = run


def _public(run):
    if not run:
        return None
    out = {k: run.get(k) for k in ("id", "server", "status", "started", "finished", "who", "current", "step", "error", "stop")}
    out["nodes"] = [dict(n) for n in run.get("nodes", [])]
    out["log"] = list(run.get("log", []))
    return out


def _log(app, run, node, msg):
    with _lock:
        run["log"].append({"t": time.time(), "node": node, "msg": msg})
        if len(run["log"]) > 3000:
            run["log"] = run["log"][:50] + run["log"][-2800:]
    _save(app)


def _set(app, run, **kw):
    with _lock:
        run.update(kw)
    _save(app)


def _nset(app, run, node, **kw):
    with _lock:
        for n in run["nodes"]:
            if n["name"] == node:
                n.update(kw)
    _save(app)


def _check_stop(run):
    if run.get("stop"):
        raise Stopped()


def _sleep(run, secs, stoppable=True):
    end = time.time() + secs
    while time.time() < end:
        if stoppable:
            _check_stop(run)
        time.sleep(min(1.0, max(0.05, end - time.time())))


# ------------------------------------------------------------------ plan (rolling.json)
def _plan_edit(fn):
    from . import updates
    with updates._lock:
        plans = updates._load_plans()
        fn(plans)
        updates._save_plans(plans)


def _plan_start(server, order):
    def f(plans):
        plan = plans.get(server) or {"nodes": {}}
        plan.setdefault("nodes", {})
        plan["started"] = plan.get("started") or int(time.time())
        plan["order"] = list(order)
        plans[server] = plan
    _plan_edit(f)


def _plan_node(server, node, fn):
    def f(plans):
        plan = plans.setdefault(server, {"nodes": {}, "started": int(time.time())})
        fn(plan.setdefault("nodes", {}).setdefault(node, {}))
    _plan_edit(f)


# ------------------------------------------------------------------ Proxmox-hulpjes
def _rows(col, server):
    from .updates import _annotate, _node_row
    grp = col.group(server)
    if not grp:
        raise PveError("cluster niet gevonden")
    nodes = [_node_row(grp, n) for n in grp["nodes"]]
    _annotate(nodes)
    return grp, {n["name"]: n for n in nodes}


def _needs_work(n):
    return bool(n.get("count") or n.get("reboot_needed") or n.get("behind"))


def _guest_now(col, server, gtype, vmid):
    grp = col.group(server)
    return next((x for x in (grp or {}).get("guests", []) if x["type"] == gtype and int(x["vmid"]) == int(vmid)), None)


def _wait_task(run, c, node, upid, timeout):
    """Wachten op een Proxmox-taak. Korte API-storingen (bijv. pveproxy die herstart) zijn geen fout."""
    if not isinstance(upid, str) or not upid.startswith("UPID"):
        return
    end, errors = time.time() + timeout, 0
    while time.time() < end:
        try:
            st = c.get("/nodes/{}/tasks/{}/status".format(q(node), q(upid))) or {}
            errors = 0
        except PveError:
            errors += 1
            if errors > 20:
                raise
            st = {}
        if st.get("status") == "stopped":
            ex = str(st.get("exitstatus") or "")
            if ex not in ("OK", "") and not ex.startswith("WARNINGS"):
                raise PveError(ex)
            return
        time.sleep(min(2.0, TIMING["poll"]))
    raise PveError("de Proxmox-taak duurde te lang")


def _migrate(app, run, server, g_, src, target):
    col = app.extensions["collector"]
    c = col.client(server)
    data = {"target": target}
    if g_["type"] == "qemu":
        data["online"] = 1
        data["with-local-disks"] = 1
    else:
        data["restart"] = 1
    upid = c.post("/nodes/{}/{}/{}/migrate".format(q(src), g_["type"], int(g_["vmid"])), data)
    _wait_task(run, c, src, upid, TIMING["migrate"])
    # bij HA-beheerde VM's is de taak al klaar voordat de migratie echt gedaan is: even nakijken
    end = time.time() + 600
    while True:
        col.refresh(server)
        cur = _guest_now(col, server, g_["type"], g_["vmid"])
        if cur and cur["node"] == target:
            return
        if time.time() > end:
            raise PveError("{} {} staat na het verplaatsen nog niet op {}".format(
                "VM" if g_["type"] == "qemu" else "CT", g_["vmid"], target))
        time.sleep(TIMING["poll"])


def _start_guest(app, run, server, node, g_):
    col = app.extensions["collector"]
    c = col.client(server)
    upid = c.post("/nodes/{}/{}/{}/status/start".format(q(node), g_["type"], int(g_["vmid"])), {})
    _wait_task(run, c, node, upid, 600)


def _gname(g_):
    return "{} {} {}".format("VM" if g_["type"] == "qemu" else "CT", g_["vmid"], g_.get("name") or "").strip()


def _cluster_ok(col, server, allow_down=None):
    grp, rows = _rows(col, server)
    if grp.get("quorate") is False:
        raise PveError("het cluster heeft geen quorum")
    off = [n for n, r in rows.items() if r["status"] != "online" and n != allow_down]
    if off:
        raise PveError("node {} is offline; PVE Fleet herstart geen node zolang er een andere niet online is".format(", ".join(off)))
    return grp, rows


# ------------------------------------------------------------------ één node
def _upgrade_node(app, run, server, node):
    """apt-get dist-upgrade op de node. Geeft True terug als de node wil herstarten."""
    col = app.extensions["collector"]
    store = app.extensions["appdeploy"]
    if col.cfg.get("demo"):
        from . import demo as demo_mod
        for line in ("apt-get update", "Reading package lists...", "apt-get dist-upgrade",
                     "Setting up proxmox-kernel-6.14.11-1-pve-signed (demo)", "Setting up pve-manager (demo)"):
            _sleep(run, 0.6, False)
            _log(app, run, node, "│ " + line)
        demo_mod.upgrade(server, node)
        return False
    grp = col.group(server)
    port, user = appdeploy._ssh_opts(app, server)
    host = appdeploy._node_host(app, grp, server, node)
    flags = {"reboot": False, "n": 0}

    def on_line(s):
        t = s.strip()
        if not t or t == "PVEFLEET-OK":
            return
        if t == "PVEFLEET-REBOOT":
            flags["reboot"] = True
            return
        flags["n"] += 1
        # apt is spraakzaam: de eerste 400 regels tonen, daarna alleen de belangrijke
        if flags["n"] <= 400 or t.startswith(("Setting up", "E:", "W:", "Err", "PVEFLEET-FAIL", "apt-get")):
            _log(app, run, node, "│ " + t.replace("PVEFLEET-FAIL ", ""))
    rc, out = appdeploy.ssh_run(store, host, "nodeupgrade", on_line=on_line, port=port, user=user, timeout=TIMING["upgrade"])
    if "onbekende opdracht" in out:
        raise PveError("het script op node {} is te oud. Plak het installatiecommando van de pagina Apps (Instellen) opnieuw op een node.".format(node))
    if rc != 0 or "PVEFLEET-OK" not in out:
        reason = out.split("PVEFLEET-FAIL", 1)[1].strip().splitlines()[0] if "PVEFLEET-FAIL" in out else "foutcode {}".format(rc)
        raise PveError(reason)
    return flags["reboot"]


def _reboot_and_wait(app, run, server, node):
    col = app.extensions["collector"]
    c = col.client(server)
    t0 = time.time()
    c.post("/nodes/{}/status".format(q(node)), {"command": "reboot"})
    _log(app, run, node, "Herstart gevraagd; wachten tot {} weg is…".format(node))
    seen_down = False
    end = time.time() + TIMING["down_wait"]
    while time.time() < end:
        _sleep(run, TIMING["poll"], False)     # een herstart maken we altijd af
        col.refresh(server)
        grp, rows = _rows(col, server)
        r = rows.get(node) or {}
        if r.get("status") != "online":
            seen_down = True
            break
        up = r.get("uptime")
        if up is not None and up < (time.time() - t0) + 5 and time.time() - t0 > 20:
            seen_down = True     # al terug voordat we het zagen
            break
    if not seen_down:
        raise PveError("{} is na {} minuten nog niet herstart".format(node, TIMING["down_wait"] // 60))
    _log(app, run, node, "{} is aan het herstarten; wachten tot hij terug is…".format(node))
    end = time.time() + TIMING["up_wait"]
    while time.time() < end:
        _sleep(run, TIMING["poll"], False)
        col.refresh(server)
        grp, rows = _rows(col, server)
        r = rows.get(node) or {}
        if r.get("status") == "online" and grp.get("quorate") is not False:
            try:
                c.get("/nodes/{}/status".format(q(node)))
            except PveError:
                continue
            _log(app, run, node, "{} is terug (na {} s). Even laten opstarten…".format(node, int(time.time() - t0)))
            _sleep(run, TIMING["settle"], False)
            col.refresh(server)
            return
    raise PveError("{} is na {} minuten nog niet terug".format(node, TIMING["up_wait"] // 60))


def _do_node(app, run, server, node, stay_keys):
    col = app.extensions["collector"]
    col.refresh(server)
    grp, rows = _cluster_ok(col, server)
    row = rows[node]
    # al eerder verplaatst (bijv. een vorige run die gestopt is) en nog niet terug
    from .updates import _plan
    earlier = [m for m in (_plan(server)["nodes"].get(node) or {}).get("moved", []) if not m.get("returned")]
    earlier = [dict(m, name=m.get("name") or "") for m in earlier
               if (_guest_now(col, server, m["type"], m["vmid"]) or {}).get("node") not in (None, node)]
    work = _needs_work(row) or not row.get("checked") or row.get("error")
    if not work and not earlier:
        _log(app, run, node, "Niets te doen: {} is al bijgewerkt.".format(node))
        _plan_node(server, node, lambda np: np.update(done=True))
        return "skip"
    if not work:
        _log(app, run, node, "{} is al bijgewerkt; alleen terugzetten wat er nog elders staat.".format(node))
        return _back(app, run, server, node, earlier)

    # 1. verplaatsen
    _nset(app, run, node, step="move")
    running = [x for x in grp["guests"] if x["node"] == node and x["status"] == "running"]
    stay = [x for x in running if "{}/{}".format(x["type"], x["vmid"]) in stay_keys]
    move = [x for x in running if x not in stay]
    _plan_node(server, node, lambda np: np.update(stay=[{"vmid": int(x["vmid"]), "type": x["type"], "name": x["name"]} for x in stay]))
    if stay:
        _log(app, run, node, "Blijven hier (gaan bij de herstart even uit): {}".format(", ".join(_gname(x) for x in stay)))
    targets = {n: r for n, r in rows.items() if n != node and r["status"] == "online"}
    if move and not targets:
        raise PveError("er is geen andere node online om naartoe te verplaatsen")
    extra = {n: 0.0 for n in targets}
    for gx in sorted(move, key=lambda x: -(x.get("maxmem") or 0)):
        _check_stop(run)
        to = min(targets, key=lambda n: (targets[n].get("mem_pct") or 0) + extra[n])
        extra[to] += 100.0 * (gx.get("maxmem") or 0) / max(1, targets[to].get("mem_total") or 64 * 1024 ** 3)
        _log(app, run, node, "Verplaatsen: {} → {}".format(_gname(gx), to))
        item = {"vmid": int(gx["vmid"]), "type": gx["type"], "name": gx["name"], "to": to}

        def add(np, item=item):
            have = [m for m in np.setdefault("moved", []) if not (m["type"] == item["type"] and m["vmid"] == item["vmid"])]
            np["moved"] = have + [item]
        _plan_node(server, node, add)
        try:
            _migrate(app, run, server, gx, node, to)
        except PveError as e:
            raise PveError("verplaatsen van {} naar {} mislukt: {}".format(_gname(gx), to, e))
    if move:
        _log(app, run, node, "Verplaatst: {} VM/CT's.".format(len(move)))

    # 2. bijwerken
    _check_stop(run)
    _nset(app, run, node, step="upgrade")
    _log(app, run, node, "Bijwerken: apt-get dist-upgrade op {}…".format(node))
    want_reboot = _upgrade_node(app, run, server, node)
    cur, _ = col.check_updates(server, node)
    cur = cur or {}
    if cur.get("count"):
        _log(app, run, node, "Let op: Proxmox meldt nog {} updates (vaak pakketten die apt bewust tegenhoudt).".format(cur["count"]))
    reboot = want_reboot or bool(cur.get("reboot_needed"))
    _log(app, run, node, "Bijgewerkt." + (" Er is een nieuwe kernel: herstarten." if reboot else " Geen herstart nodig."))

    # 3. herstarten
    if reboot:
        _check_stop(run)
        _nset(app, run, node, step="reboot")
        _cluster_ok(col, server)
        _reboot_and_wait(app, run, server, node)

    # 4. weer starten wat bleef staan
    _nset(app, run, node, step="restart")
    for gx in stay:
        _check_stop(run)
        cur = _guest_now(col, server, gx["type"], gx["vmid"])
        if cur and cur["status"] == "running":
            continue
        _log(app, run, node, "Starten: {}".format(_gname(gx)))
        try:
            _start_guest(app, run, server, node, gx)
        except PveError as e:
            if "already running" in str(e) or "draait al" in str(e):
                continue
            raise PveError("starten van {} mislukt: {}".format(_gname(gx), e))
    if stay:
        col.refresh(server)

    # 5. terugzetten
    have = {(x["type"], int(x["vmid"])) for x in move}
    return _back(app, run, server, node, move + [m for m in earlier if (m["type"], int(m["vmid"])) not in have])


def _back(app, run, server, node, move):
    col = app.extensions["collector"]
    _nset(app, run, node, step="back")
    for gx in move:
        _check_stop(run)
        cur = _guest_now(col, server, gx["type"], gx["vmid"])
        if not cur or cur["node"] == node:
            continue
        _log(app, run, node, "Terugzetten: {} ← {}".format(_gname(gx), cur["node"]))
        try:
            _migrate(app, run, server, gx, cur["node"], node)
        except PveError as e:
            raise PveError("terugzetten van {} mislukt: {}".format(_gname(gx), e))

        def ret(np, gx=gx):
            for m in np.get("moved", []):
                if m["type"] == gx["type"] and m["vmid"] == int(gx["vmid"]):
                    m["returned"] = True
        _plan_node(server, node, ret)
    _plan_node(server, node, lambda np: np.update(done=True))
    return "ok"


def run_all(app, run):
    server = run["server"]
    col = app.extensions["collector"]
    stay_keys = set(run.get("stay") or [])
    with app.app_context():
        try:
            # vooraf: werkt de app-sleutel en kent het script 'nodeupgrade'?
            _cluster_ok(col, server)
            if not col.cfg.get("demo"):
                store = app.extensions["appdeploy"]
                grp = col.group(server)
                port, user = appdeploy._ssh_opts(app, server)
                for n in grp["nodes"]:
                    host = appdeploy._node_host(app, grp, server, n["name"])
                    _, pong = appdeploy.ssh_run(store, host, "ping", port=port, user=user, timeout=30)
                    v = appdeploy.script_version(pong)
                    if v < 3:
                        raise PveError("het script op node {} is te oud (versie {}). Plak het installatiecommando van de pagina Apps "
                                       "(Instellen) opnieuw op een node en druk daar op 'Inschakelen en testen'.".format(n["name"], v or "?"))
                _log(app, run, None, "App-sleutel werkt op alle nodes.")
            _plan_start(server, [n["name"] for n in run["nodes"]])
            for n in run["nodes"]:
                if n["state"] in ("ok", "skip"):
                    continue
                _check_stop(run)
                name = n["name"]
                _set(app, run, current=name)
                _nset(app, run, name, state="busy", started=time.time(), msg=None)
                _log(app, run, name, "── {} ──".format(name))
                try:
                    res = _do_node(app, run, server, name, stay_keys)
                except Stopped:
                    _nset(app, run, name, state="stopped", msg="gestopt")
                    raise
                except (PveError, OSError) as e:
                    _nset(app, run, name, state="fail", msg=str(e)[:300])
                    raise
                _nset(app, run, name, state=res, step=None, finished=time.time(),
                      msg="al bijgewerkt" if res == "skip" else "klaar")
                _log(app, run, name, "{} is klaar.".format(name) if res == "ok" else "Overgeslagen.")
            _set(app, run, status="done", finished=time.time(), current=None)
            _log(app, run, None, "Alle nodes zijn bijgewerkt.")
            ok = True
        except Stopped:
            _set(app, run, status="stopped", finished=time.time())
            _log(app, run, run.get("current"), "Gestopt op verzoek. Ga stap voor stap verder op deze pagina als dat nodig is.")
            ok = False
        except (PveError, OSError) as e:
            _set(app, run, status="failed", finished=time.time(), error=str(e)[:400])
            _log(app, run, run.get("current"), "Gestopt door een fout: {}".format(e))
            ok = False
        except Exception as e:  # noqa: BLE001
            log.exception("autorolling")
            _set(app, run, status="failed", finished=time.time(), error="onverwachte fout: {}".format(e))
            _log(app, run, run.get("current"), "Onverwachte fout: {}".format(e))
            ok = False
        try:
            from .actions import audit
            audit({"who": run.get("who"), "action": "rolling_auto", "server": server, "ok": ok,
                   "label": "Automatische rolling update: {}".format({"done": "klaar", "stopped": "gestopt", "failed": "mislukt"}.get(run["status"], run["status"]))})
        except Exception:  # noqa: BLE001
            pass
        try:
            col.refresh(server)
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------ API
def init_app(app):
    app.register_blueprint(bp)
    _load(app)


@bp.get("/api/rolling/<server>/auto")
def api_status(server):
    _admin()
    with _lock:
        run = _public(RUNS.get(server))
    return jsonify({"run": run, "enabled": appdeploy._enabled(current_app.extensions["appdeploy"]),
                    "demo": bool(current_app.extensions["collector"].cfg.get("demo"))})


@bp.post("/api/rolling/<server>/auto")
def api_start(server):
    _admin()
    app = current_app._get_current_object()
    col = app.extensions["collector"]
    grp = col.group(server)
    if not grp:
        return jsonify({"error": "onbekende server"}), 404
    if grp.get("kind") != "cluster":
        return jsonify({"error": "Dit kan alleen bij een cluster"}), 400
    if not col.cfg.get("demo") and not appdeploy._enabled(app.extensions["appdeploy"]):
        return jsonify({"error": "Hiervoor is de app-sleutel nodig. Stel die eerst in op de pagina Apps (Instellen)."}), 409
    with _lock:
        cur = RUNS.get(server)
        if cur and cur.get("status") == "running":
            return jsonify({"error": "De automatische rolling update loopt al"}), 409
    body = request.get_json(silent=True) or {}
    names = [n["name"] for n in grp["nodes"]]
    order = [x for x in (body.get("order") or []) if x in names]
    order += [x for x in names if x not in order]
    stay = [str(x) for x in (body.get("stay") or []) if isinstance(x, str) and "/" in x][:2000]
    run = {"id": secrets.token_hex(6), "server": server, "status": "running", "started": time.time(), "finished": None,
           "who": g.who, "current": None, "error": None, "stop": False, "stay": stay,
           "nodes": [{"name": n, "state": "wait", "step": None, "msg": None} for n in order], "log": []}
    with _lock:
        RUNS[server] = run
    _log(app, run, None, "Gestart door {}: {} nodes, volgorde {}.".format(g.who, len(order), ", ".join(order)))
    threading.Thread(target=run_all, args=(app, run), daemon=True, name="autorolling-" + server).start()
    return jsonify({"ok": True, "run": _public(run)})


@bp.post("/api/rolling/<server>/auto/stop")
def api_stop(server):
    _admin()
    with _lock:
        run = RUNS.get(server)
        if not run or run.get("status") != "running":
            return jsonify({"error": "Er loopt geen automatische rolling update"}), 409
        run["stop"] = True
    _log(current_app._get_current_object(), run, run.get("current"),
         "Stoppen gevraagd door {}. PVE Fleet maakt de lopende stap af en stopt dan.".format(g.who))
    return jsonify({"ok": True})


@bp.post("/api/rolling/<server>/auto/clear")
def api_clear(server):
    _admin()
    with _lock:
        run = RUNS.get(server)
        if run and run.get("status") == "running":
            return jsonify({"error": "De automatische rolling update loopt nog"}), 409
        RUNS.pop(server, None)
    _save(current_app._get_current_object())
    return jsonify({"ok": True})
