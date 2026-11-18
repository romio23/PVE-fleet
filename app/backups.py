"""Stap 3: back-ups. Back-upjobs bekijken, maken, wijzigen en meteen uitvoeren,
de laatste back-up per VM/CT en welke VM/CT's in geen enkele job zitten.

Rechten: lezen kan met PVEAuditor. Een job meteen uitvoeren gebruikt VM.Backup en
Datastore.AllocateSpace (zoals 'Back-up nu'). Jobs maken of wijzigen vraagt in Proxmox
het recht Sys.Modify op '/'; dat is optioneel.
"""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, abort, current_app, g, jsonify, render_template, request

from .pve import PveError, PvePermissionError, q

bp = Blueprint("backups", __name__)
_pool = ThreadPoolExecutor(max_workers=6)
_cache = {}            # server -> (tijd, data)
_cache_lock = threading.Lock()
CACHE_SECONDS = 300

SCHEDULE_RE = re.compile(r"^[A-Za-z0-9 :,.*/~\-]{1,100}$")
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,60}$")
PRUNE_KEYS = ("keep-last", "keep-hourly", "keep-daily", "keep-weekly", "keep-monthly", "keep-yearly")


def col():
    return current_app.extensions["collector"]


def _admin():
    if g.get("scope") != "admin":
        abort(403)


PERM_RE = re.compile(r"\((/[^,()]*), ([A-Za-z.]+)\)")


def _perm_msg(e, what):
    return _perm_error(e, what)["error"]


def _perm_error(e, what, server=None):
    """Nette melding bij 'Permission check failed (/pad, Recht)': noem het recht dat echt ontbreekt,
    met pveum-commando's die passen bij het token van deze server."""
    m = PERM_RE.search(str(e))
    if not m or (m.group(1) == "/" and m.group(2) == "Sys.Modify"):
        return {"error": ("Het API-token mag geen {}. In Proxmox is daarvoor het recht Sys.Modify op '/' nodig "
                          "(zie Servers & verbinding › Verbinding testen, onderdeel 'Back-upjobs aanpassen'). ({})").format(what, e),
                "commands": None}
    path, priv = m.group(1), m.group(2)
    token = None
    for s in col().cfg.get("servers") or []:
        if s.get("name") == server:
            token = s.get("token_id")
    user = token.split("!", 1)[0] if token and "!" in token else "dashboard@pve"
    token = token or "dashboard@pve!fleet"
    if priv.startswith("Datastore."):
        role, privs = "FleetBackupTarget", "Datastore.Allocate Datastore.AllocateSpace Datastore.Audit"
    else:
        role, privs = "Fleet" + priv.replace(".", ""), priv
    cmds = ("pveum role add {role} --privs \"{privs}\"\n"
            "pveum acl modify {path} --users {user} --roles {role}\n"
            "pveum acl modify {path} --tokens '{token}' --roles {role}").format(role=role, privs=privs, path=path, user=user, token=token)
    where = "de opslag {}".format(path.split("/", 2)[2]) if path.startswith("/storage/") else path
    return {"error": "Het API-token mag geen {}: Proxmox mist het recht {} op {}. Plak de commando's hieronder één keer "
                     "in de shell van een node en probeer het opnieuw.".format(what, priv, where),
            "commands": cmds}


# ------------------------------------------------------------------ pagina
@bp.get("/backups")
def backups_page():
    return render_template("backups.html", page="backups")


# ------------------------------------------------------------------ verzamelen
def _parse_ids(v):
    if v in (None, ""):
        return []
    out = []
    for part in re.split(r"[,\s]+", str(v)):
        if part.isdigit():
            out.append(int(part))
    return out


def _parse_prune(v):
    out = {}
    for part in str(v or "").split(","):
        k, _, val = part.partition("=")
        k = k.strip()
        if k in PRUNE_KEYS and val.strip().isdigit():
            out[k] = int(val)
        elif k == "keep-all" and val.strip() == "1":
            out["keep-all"] = 1
    return out


def _job_row(j):
    if j.get("all") in (1, "1", True):
        sel = "all"
    elif j.get("pool"):
        sel = "pool"
    else:
        sel = "include"
    return {"id": j.get("id"), "enabled": str(j.get("enabled", 1)) not in ("0", "false"),
            "schedule": j.get("schedule") or (" ".join(x for x in (j.get("dow"), j.get("starttime")) if x) or None),
            "next_run": j.get("next-run"), "storage": j.get("storage"), "node": j.get("node"),
            "selection": sel, "vmids": _parse_ids(j.get("vmid")), "exclude": _parse_ids(j.get("exclude")),
            "pool": j.get("pool"), "mode": j.get("mode") or "snapshot", "compress": j.get("compress"),
            "prune": _parse_prune(j.get("prune-backups")), "comment": j.get("comment") or "",
            "notes_template": j.get("notes-template") or "", "type": j.get("type")}


def _contents(c, node, storage):
    items = c.get("/nodes/{}/storage/{}/content".format(q(node), q(storage)), {"content": "backup"}) or []
    return [{"vmid": int(i["vmid"]) if str(i.get("vmid", "")).isdigit() else None, "ctime": i.get("ctime"),
             "size": i.get("size"), "volid": i.get("volid"), "storage": storage, "node": node,
             "notes": i.get("notes"), "protected": bool(i.get("protected")),
             "verified": (i.get("verification") or {}).get("state")} for i in items]


def _collect(server):
    c = col().client(server)
    grp = col().group(server)
    out = {"server": server, "name": grp["name"], "kind": grp["kind"], "reachable": grp["reachable"],
           "jobs": [], "errors": {}, "not_backed_up": None, "latest": {}, "storages": [], "checked": time.time()}
    if not grp["reachable"]:
        out["errors"]["server"] = grp.get("error") or "niet bereikbaar"
        return out
    # jobs
    try:
        jobs = c.get("/cluster/backup") or []
        out["jobs"] = sorted((_job_row(j) for j in jobs), key=lambda j: (j["schedule"] or "", j["id"] or ""))
    except PveError as e:
        out["errors"]["jobs"] = str(e)
    # volgende keer, als Proxmox die niet meestuurt
    for j in out["jobs"]:
        if j["next_run"] is None and j["enabled"] and j["schedule"]:
            try:
                res = c.get("/cluster/jobs/schedule-analyze", {"schedule": j["schedule"], "iterations": 1}) or []
                j["next_run"] = res[0].get("timestamp") if res else None
            except PveError:
                pass
    try:
        nb = c.get("/cluster/backup-info/not-backed-up") or []
        out["not_backed_up"] = [{"vmid": int(x.get("vmid")), "name": x.get("name"), "type": x.get("type")} for x in nb]
    except PveError as e:
        out["errors"]["not_backed_up"] = str(e)
    # back-upopslag: gedeelde één keer, lokale per node
    online = {n["name"] for n in grp["nodes"] if n["status"] == "online"}
    seen, todo = set(), []
    for s in grp["storage"]:
        if "backup" not in (s.get("content") or "") or s.get("status") != "available" or s["node"] not in online:
            continue
        key = s["name"] if s["shared"] else (s["name"], s["node"])
        if key in seen:
            continue
        seen.add(key)
        out["storages"].append({"name": s["name"], "node": None if s["shared"] else s["node"], "type": s["type"],
                                "pct": s["pct"], "used": s["used"], "total": s["total"], "shared": s["shared"]})
        todo.append((s["node"], s["name"]))
    futs = {(n, st): _pool.submit(_contents, c, n, st) for n, st in todo}
    latest, counts = {}, {}
    for (n, st), f in futs.items():
        try:
            items = f.result(timeout=40)
        except Exception as e:  # noqa: BLE001 - PBS kan traag zijn of weigeren
            out["errors"]["storage:" + st] = str(e)[:200]
            continue
        for it in items:
            if it["vmid"] is None:
                continue
            counts[it["vmid"]] = counts.get(it["vmid"], 0) + 1
            cur = latest.get(it["vmid"])
            if not cur or (it["ctime"] or 0) > (cur["ctime"] or 0):
                latest[it["vmid"]] = it
    for vmid, it in latest.items():
        it["count"] = counts.get(vmid, 0)
    out["latest"] = {str(k): v for k, v in latest.items()}
    return out


def collect(server, force=False):
    with _cache_lock:
        hit = _cache.get(server)
    if hit and not force and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    data = _collect(server)
    with _cache_lock:
        _cache[server] = (time.time(), data)
    return data


def invalidate(server):
    with _cache_lock:
        _cache.pop(server, None)


@bp.get("/api/backups")
def api_backups():
    force = request.args.get("force") == "1"
    with col().lock:
        groups = list(col().snapshot.get("_full", []))
    futs = []
    app = current_app._get_current_object()

    def run(server):
        with app.app_context():
            return collect(server, force)
    for grp in groups:
        futs.append((grp, _pool.submit(run, grp["server"])))
    out = []
    now = time.time()
    for grp, f in futs:
        try:
            data = f.result(timeout=60)
        except Exception as e:  # noqa: BLE001
            data = {"server": grp["server"], "name": grp["name"], "kind": grp["kind"], "reachable": False,
                    "jobs": [], "errors": {"server": str(e)}, "not_backed_up": None, "latest": {}, "storages": []}
        data = dict(data)
        # VM's & containers met hun laatste back-up
        rows = []
        for x in grp["guests"]:
            lb = data["latest"].get(str(x["vmid"]))
            rows.append({"vmid": x["vmid"], "name": x["name"], "type": x["type"], "node": x["node"], "status": x["status"],
                         "last": lb["ctime"] if lb else None, "size": lb["size"] if lb else None,
                         "storage": lb["storage"] if lb else None, "count": lb["count"] if lb else 0,
                         "age_h": round((now - lb["ctime"]) / 3600, 1) if lb and lb.get("ctime") else None})
        data["guests"] = sorted(rows, key=lambda r: r["vmid"])
        data["nodes"] = [{"name": n["name"], "status": n["status"]} for n in grp["nodes"]]
        data["tasks"] = [t for t in grp["tasks"] if t["type"] == "vzdump"][:20]
        data.pop("latest", None)
        out.append(data)
    return jsonify({"groups": out})


# ------------------------------------------------------------------ jobs aanpassen
def _job_params(body, grp, creating):
    """Formulier -> Proxmox-parameters. Geeft (data, delete-lijst)."""
    data, delete = {}, []
    sched = str(body.get("schedule", "")).strip()
    if not sched or not SCHEDULE_RE.match(sched):
        raise ValueError("Ongeldig schema. Voorbeelden: 02:00, sun 03:00, mon..fri 21:00")
    data["schedule"] = sched
    storages = {s["name"] for s in grp["storage"] if "backup" in (s.get("content") or "")}
    storage = str(body.get("storage", ""))
    if storage not in storages:
        raise ValueError("Kies een opslag waarop back-ups mogen")
    data["storage"] = storage
    node = str(body.get("node") or "")
    if node:
        if node not in [n["name"] for n in grp["nodes"]]:
            raise ValueError("Onbekende node")
        data["node"] = node
    elif not creating:
        delete.append("node")
    mode = str(body.get("mode", "snapshot"))
    if mode not in ("snapshot", "suspend", "stop"):
        raise ValueError("Ongeldige modus")
    data["mode"] = mode
    comp = str(body.get("compress", "zstd"))
    if comp not in ("zstd", "lzo", "gzip", "0"):
        raise ValueError("Ongeldige compressie")
    data["compress"] = comp
    known = {x["vmid"] for x in grp["guests"]}
    sel = body.get("selection")
    ids = sorted({int(v) for v in body.get("vmids") or [] if str(v).isdigit() and int(v) in known})
    if sel == "all":
        data["all"] = 1
        if ids:
            data["exclude"] = ",".join(str(i) for i in ids)
        elif not creating:
            delete.append("exclude")
        if not creating:
            delete += ["vmid", "pool"]
    elif sel == "include":
        if not ids:
            raise ValueError("Kies minstens één VM of container")
        data["vmid"] = ",".join(str(i) for i in ids)
        if creating:
            pass
        else:
            data["all"] = 0
            delete += ["exclude", "pool"]
    elif sel == "pool" and not creating:
        pass   # pool-selectie laten we zoals hij is (bewerken in Proxmox)
    else:
        raise ValueError("Kies welke VM's & containers")
    prune = []
    for k in PRUNE_KEYS:
        v = body.get("prune", {}).get(k) if isinstance(body.get("prune"), dict) else None
        if v not in (None, "") and str(v).isdigit() and int(v) > 0:
            prune.append("{}={}".format(k, int(v)))
    if prune:
        data["prune-backups"] = ",".join(prune)
    elif not creating:
        delete.append("prune-backups")
    comment = str(body.get("comment", "")).strip()[:200]
    if comment:
        data["comment"] = comment
    elif not creating:
        delete.append("comment")
    notes = str(body.get("notes_template", "")).strip()[:200]
    if notes:
        data["notes-template"] = notes
    elif not creating:
        delete.append("notes-template")
    data["enabled"] = 1 if body.get("enabled", True) else 0
    return data, delete


@bp.post("/api/backups/<server>/job")
def api_job_save(server):
    _admin()
    grp = col().group(server)
    c = col().client(server)
    if not grp or not c:
        return jsonify({"error": "onbekende server"}), 404
    body = request.get_json(silent=True) or {}
    job_id = str(body.get("id") or "")
    creating = not job_id
    try:
        data, delete = _job_params(body, grp, creating)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    from .actions import audit
    try:
        if creating:
            c.post("/cluster/backup", data)
            label = "Back-upjob gemaakt ({})".format(data["schedule"])
        else:
            if not JOB_ID_RE.match(job_id):
                return jsonify({"error": "ongeldige job"}), 400
            if delete:
                data["delete"] = ",".join(delete)
            c.put("/cluster/backup/{}".format(q(job_id)), data)
            label = "Back-upjob {} gewijzigd".format(job_id)
    except PvePermissionError as e:
        return jsonify(_perm_error(e, "back-upjobs maken of wijzigen", server)), 403
    except PveError as e:
        return jsonify({"error": "Proxmox: {}".format(e)}), 502
    audit({"who": g.who, "action": "backupjob", "server": server, "label": label, "ok": True})
    invalidate(server)
    return jsonify({"ok": True, "message": label})


@bp.post("/api/backups/<server>/job/<job_id>/enabled")
def api_job_enabled(server, job_id):
    _admin()
    c = col().client(server)
    if not c or not JOB_ID_RE.match(job_id):
        return jsonify({"error": "onbekende job"}), 404
    on = bool((request.get_json(silent=True) or {}).get("enabled"))
    from .actions import audit
    try:
        c.put("/cluster/backup/{}".format(q(job_id)), {"enabled": 1 if on else 0})
    except PvePermissionError as e:
        return jsonify({"error": _perm_msg(e, "back-upjobs aan- of uitzetten")}), 403
    except PveError as e:
        return jsonify({"error": "Proxmox: {}".format(e)}), 502
    label = "Back-upjob {} {}".format(job_id, "aangezet" if on else "uitgezet")
    audit({"who": g.who, "action": "backupjob", "server": server, "label": label, "ok": True})
    invalidate(server)
    return jsonify({"ok": True, "message": label})


@bp.delete("/api/backups/<server>/job/<job_id>")
def api_job_delete(server, job_id):
    _admin()
    c = col().client(server)
    if not c or not JOB_ID_RE.match(job_id):
        return jsonify({"error": "onbekende job"}), 404
    from .actions import audit
    try:
        c.delete("/cluster/backup/{}".format(q(job_id)))
    except PvePermissionError as e:
        return jsonify({"error": _perm_msg(e, "back-upjobs verwijderen")}), 403
    except PveError as e:
        return jsonify({"error": "Proxmox: {}".format(e)}), 502
    label = "Back-upjob {} verwijderd".format(job_id)
    audit({"who": g.who, "action": "backupjob", "server": server, "label": label, "ok": True})
    invalidate(server)
    return jsonify({"ok": True, "message": label})


@bp.post("/api/backups/<server>/job/<job_id>/run")
def api_job_run(server, job_id):
    """Job nu uitvoeren, zoals 'Nu uitvoeren' in Proxmox: per node een vzdump met de instellingen van de job."""
    _admin()
    c = col().client(server)
    grp = col().group(server)
    if not c or not grp or not JOB_ID_RE.match(job_id):
        return jsonify({"error": "onbekende job"}), 404
    try:
        job = c.get("/cluster/backup/{}".format(q(job_id))) or {}
    except PveError as e:
        return jsonify({"error": "Proxmox: {}".format(e)}), 502
    row = _job_row(dict(job, id=job_id))
    base = {"storage": row["storage"], "mode": row["mode"]}
    for k in ("compress", "prune-backups", "notes-template", "protected", "pbs-change-detection-mode",
              "fleecing", "performance", "bwlimit", "zstd", "ionice", "remove"):
        if job.get(k) not in (None, ""):
            base[k] = job[k]
    online = [n["name"] for n in grp["nodes"] if n["status"] == "online"]
    nodes = [row["node"]] if row["node"] else online
    started, skipped, errors = [], [], []
    from .actions import audit
    for node in nodes:
        if node not in online:
            skipped.append("{} is offline".format(node))
            continue
        data = dict(base)
        if row["selection"] == "all":
            data["all"] = 1
            if row["exclude"]:
                data["exclude"] = ",".join(str(i) for i in row["exclude"])
        elif row["selection"] == "pool":
            data["pool"] = row["pool"]
        else:
            ids = [x["vmid"] for x in grp["guests"] if x["node"] == node and x["vmid"] in row["vmids"]]
            if not ids:
                continue
            data["vmid"] = ",".join(str(i) for i in ids)
        try:
            upid = c.post("/nodes/{}/vzdump".format(q(node)), data)
            started.append({"node": node, "upid": upid if isinstance(upid, str) else None})
        except PvePermissionError as e:
            errors.append("{}: geen rechten ({})".format(node, e))
        except PveError as e:
            errors.append("{}: {}".format(node, e))
    label = "Back-upjob {} nu uitgevoerd".format(job_id)
    audit({"who": g.who, "action": "backupjob_run", "server": server, "label": label, "ok": bool(started),
           "error": "; ".join(errors) or None})
    invalidate(server)
    if not started and errors:
        return jsonify({"error": "; ".join(errors)}), 502
    return jsonify({"ok": True, "started": started, "skipped": skipped, "errors": errors, "label": label,
                    "message": "Gestart op {}".format(", ".join(s["node"] for s in started)) if started else "Niets te doen"})


@bp.get("/api/backups/<server>/schedule")
def api_schedule(server):
    """Voorbeeld van de volgende keren voor een schema (Proxmox rekent het uit)."""
    c = col().client(server)
    sched = request.args.get("schedule", "")
    if not c or not SCHEDULE_RE.match(sched or ""):
        return jsonify({"error": "ongeldig schema"}), 400
    try:
        res = c.get("/cluster/jobs/schedule-analyze", {"schedule": sched, "iterations": 3}) or []
    except PveError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"next": [r.get("timestamp") for r in res]})
