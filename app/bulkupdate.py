"""Alle containers bijwerken: pakketupdates onbeheerd, één container tegelijk.

Per container: snapshot via de API (optioneel), daarna via SSH naar de node met de app-sleutel
(deploy.sh, opdracht 'upgrade'): apt-get dist-upgrade (of apk/dnf) in de container, waarbij eigen
configuratiebestanden blijven staan. Daarna kijkt PVE Fleet opnieuw welke updates er nog zijn.
App-updates (community-scripts 'update') zitten hier bewust niet in: die stellen soms vragen.
"""
import logging
import secrets
import threading
import time

from flask import Blueprint, abort, current_app, g, jsonify, request

from . import appdeploy
from .pve import PveError, q

log = logging.getLogger(__name__)
bp = Blueprint("bulkupdate", __name__)

_lock = threading.Lock()
JOB = {"job": None}


def _admin():
    if g.get("scope") != "admin":
        abort(403)


def _public(job, with_log=True):
    if not job:
        return None
    out = {k: job[k] for k in ("id", "status", "started", "finished", "who", "snapshot")}
    out["items"] = [dict(i) for i in job["items"]]
    if with_log:
        out["log"] = list(job["log"])
    return out


def _jl(job, msg):
    with _lock:
        job["log"].append("{}  {}".format(time.strftime("%H:%M:%S"), msg))
        if len(job["log"]) > 1500:
            job["log"] = job["log"][:20] + ["…"] + job["log"][-1400:]


def _snapshot(c, node, vmid, label):
    snap = appdeploy.SNAP_PREFIX + time.strftime("%y%m%d-%H%M%S")
    try:
        taken = {x.get("name") for x in (c.get("/nodes/{}/lxc/{}/snapshot".format(q(node), int(vmid))) or [])}
    except PveError:
        taken = set()
    base, i = snap, 2
    while snap in taken:
        snap, i = "{}-{}".format(base, i), i + 1
    appdeploy._wait_task(c, node, c.post("/nodes/{}/lxc/{}/snapshot".format(q(node), int(vmid)),
                                         {"snapname": snap, "description": "PVE Fleet: voor {}".format(label)}))
    return snap


def run(app, job):
    store = app.extensions["appdeploy"]
    col = app.extensions["collector"]
    demo = bool(col.cfg.get("demo"))
    servers = set()
    checked = {}     # node-host -> foutmelding of None: eerst kijken of deploy.sh 'upgrade' kent, vóór een snapshot
    for it in job["items"]:
        with _lock:
            it["state"] = "busy"
        name = "{} ({})".format(it.get("name") or "?", it["vmid"])
        _jl(job, "── {} ──".format(name))
        try:
            c = col.client(it["server"])
            grp, row = appdeploy._guest(app, it["server"], it["vmid"])
            if not c or not row:
                raise PveError("container niet gevonden")
            if row["status"] != "running":
                with _lock:
                    it.update(state="skip", msg="draait niet")
                _jl(job, "Overgeslagen: de container draait niet.")
                continue
            node = row["node"]
            servers.add(it["server"])
            if not demo:
                port, user = appdeploy._ssh_opts(app, it["server"])
                host = appdeploy._node_host(app, grp, it["server"], node)
                if host not in checked:
                    try:
                        _, pong = appdeploy.ssh_run(store, host, "ping", port=port, user=user, timeout=30)
                        v = appdeploy.script_version(pong)
                        checked[host] = None if v >= appdeploy.SCRIPT_VERSION else (
                            "het script op node {} is te oud (versie {}). Plak het installatiecommando van de pagina Apps (Instellen) "
                            "opnieuw op een node; dat is een ander commando dan dat van de controle op updates.".format(node, v or "?"))
                    except PveError as e:
                        checked[host] = str(e)
                if checked[host]:
                    raise PveError(checked[host])
            if job["snapshot"]:
                snap = _snapshot(c, node, it["vmid"], "pakketupdates")
                with _lock:
                    it["snapname"] = snap
                _jl(job, "Snapshot gemaakt: {}".format(snap))
            reboot = False
            if demo:
                for line in ("apt-get update", "apt-get dist-upgrade", "Setting up … (demo)", "PVEFLEET-OK"):
                    time.sleep(0.5)
                    if line != "PVEFLEET-OK":
                        _jl(job, "│ " + line)
                from . import demo as demo_mod
                demo_mod.STATE["upgraded_cts"].setdefault(node, set()).add(int(it["vmid"]))
                ok = True
            else:
                port, user = appdeploy._ssh_opts(app, it["server"])
                host = appdeploy._node_host(app, grp, it["server"], node)
                flags = {"reboot": False}

                def on_line(s, flags=flags):
                    t = s.strip()
                    if not t or t == "PVEFLEET-OK":
                        return
                    if t == "PVEFLEET-REBOOT":
                        flags["reboot"] = True
                        return
                    _jl(job, "│ " + t.replace("PVEFLEET-FAIL ", ""))
                rc, out = appdeploy.ssh_run(store, host, "upgrade {}".format(int(it["vmid"])), on_line=on_line,
                                            port=port, user=user, timeout=3600)
                if "onbekende opdracht" in out:
                    raise PveError("het script op de node is te oud. Plak het installatiecommando van de pagina Apps (Instellen) opnieuw op een node.")
                ok = rc == 0 and "PVEFLEET-OK" in out
                reboot = flags["reboot"]
                if not ok:
                    reason = out.split("PVEFLEET-FAIL", 1)[1].strip().splitlines()[0] if "PVEFLEET-FAIL" in out else "foutcode {}".format(rc)
                    raise PveError(reason)
            with _lock:
                it.update(state="ok", msg="herstart nodig" if reboot else "bijgewerkt", reboot=reboot)
            _jl(job, "Klaar" + (" (de container wil herstarten)" if reboot else "") + ".")
            if job["snapshot"]:
                appdeploy._cleanup_snapshots(c, node, it["vmid"], lambda m: _jl(job, m))
        except (PveError, OSError) as e:
            with _lock:
                it.update(state="fail", msg=str(e)[:300])
            _jl(job, "Mislukt: {}".format(e))
            if it.get("snapname"):
                _jl(job, "De snapshot {} staat klaar als je terug wilt (pagina van de container).".format(it["snapname"]))
        except Exception as e:  # noqa: BLE001
            log.exception("bulk-update")
            with _lock:
                it.update(state="fail", msg=str(e)[:300])
            _jl(job, "Onverwachte fout: {}".format(e))
    ok = sum(1 for i in job["items"] if i["state"] == "ok")
    bad = sum(1 for i in job["items"] if i["state"] == "fail")
    with _lock:
        job.update(status="done", finished=time.time())
    _jl(job, "Klaar: {} bijgewerkt{}.".format(ok, ", {} mislukt".format(bad) if bad else ""))
    try:
        from .actions import audit
        audit({"who": job.get("who"), "action": "guest_upgrade_all", "ok": bad == 0,
               "label": "Containers bijgewerkt: {} gelukt, {} mislukt".format(ok, bad)})
    except Exception:  # noqa: BLE001
        pass
    from . import guestupdates
    for server in servers:     # opnieuw kijken wat er nog openstaat
        guestupdates._run_bg(app, server)


def init_app(app):
    app.register_blueprint(bp)


@bp.get("/api/guest-updates/upgrade-all")
def api_status():
    _admin()
    return jsonify({"job": _public(JOB["job"]), "enabled": appdeploy._enabled(current_app.extensions["appdeploy"])})


@bp.post("/api/guest-updates/upgrade-all")
def api_start():
    _admin()
    app = current_app._get_current_object()
    if not appdeploy._enabled(app.extensions["appdeploy"]):
        return jsonify({"error": "Hiervoor is de app-sleutel nodig. Stel die eerst in op de pagina Apps (Instellen); "
                                 "had je dat al gedaan, plak het installatiecommando daar dan opnieuw."}), 409
    with _lock:
        cur = JOB["job"]
        if cur and cur["status"] == "running":
            return jsonify({"error": "Er worden al containers bijgewerkt", "job": cur["id"]}), 409
    body = request.get_json(silent=True) or {}
    items = []
    for t in body.get("targets") or []:
        try:
            server, vmid = str(t["server"]), int(t["vmid"])
        except (KeyError, TypeError, ValueError):
            continue
        _, row = appdeploy._guest(app, server, vmid)
        if row:
            items.append({"server": server, "vmid": vmid, "name": row["name"], "state": "wait", "msg": None})
    if not items:
        return jsonify({"error": "Kies minstens één container"}), 400
    job = {"id": secrets.token_hex(6), "status": "running", "started": time.time(), "finished": None, "who": g.who,
           "snapshot": body.get("snapshot", True) is not False, "items": items, "log": []}
    with _lock:
        JOB["job"] = job
    _jl(job, "Gestart door {}: {} container(s){}".format(g.who, len(items), ", met snapshot vooraf" if job["snapshot"] else ""))
    threading.Thread(target=run, args=(app, job), daemon=True, name="bulk-update").start()
    return jsonify({"ok": True, "job": _public(job, False)})
