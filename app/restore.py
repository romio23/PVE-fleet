"""Back-ups terugzetten (restore), op hetzelfde of op een ander cluster.

Stappen in de pagina "Terugzetten":
  1. kies een back-up (alle back-ups op de back-upopslag van een server),
  2. kies het doel (server/cluster en node), een vrij ID, de opslag voor de schijven en per netwerkkaart de bridge,
  3. PVE Fleet laat Proxmox terugzetten en past daarna (als je dat vroeg) de bridge aan en start de VM/CT.

Een back-up van een ander cluster zie je alleen als dat cluster dezelfde opslag kent (bijvoorbeeld dezelfde
NFS-share of dezelfde Proxmox Backup Server). De opslagnaam mag anders heten: PVE Fleet zoekt het bestand op het doel.
Een bestaande VM/CT overschrijven kan hier niet (er wordt nooit 'force' gebruikt).

Rechten (op het doel): VM.Allocate, Datastore.AllocateSpace op de doelopslag, VM.Config.* en SDN.Use voor een andere bridge.
"""
import logging
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, abort, current_app, g, jsonify, render_template, request

from .pve import PveError, PvePermissionError, q

log = logging.getLogger(__name__)
bp = Blueprint("restore", __name__)

POLL_SECONDS = 2.0          # hoe vaak de taak wordt bekeken (tests zetten dit lager)
MAX_WAIT = 6 * 3600         # een restore van een grote VM kan lang duren
MAX_JOBS = 20
BRIDGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
STORAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
NET_KEY_RE = re.compile(r"^net\d{1,2}$")
DISK_KEY_RE = re.compile(r"^(rootfs|mp\d+|scsi\d+|virtio\d+|sata\d+|ide\d+|efidisk\d+|tpmstate\d+)$")
NET_TYPES = {"nfs", "cifs", "cephfs", "glusterfs"}

_pool = ThreadPoolExecutor(max_workers=6)
_jobs = OrderedDict()
_jobs_lock = threading.Lock()


def col():
    return current_app.extensions["collector"]


def _admin():
    if g.get("scope") != "admin":
        abort(403)


# ------------------------------------------------------------------ hulpfuncties (zonder Proxmox, goed te testen)
def volid_type(volid, subtype=None):
    """'lxc' of 'qemu' uit de naam van de back-up (bestand of Proxmox Backup Server)."""
    v = volid or ""
    if subtype in ("lxc", "qemu"):
        return subtype
    if "vzdump-lxc-" in v or "/ct/" in v:
        return "lxc"
    if "vzdump-qemu-" in v or "/vm/" in v:
        return "qemu"
    return None


def rel_volid(volid):
    """Het deel na 'opslagnaam:' (dat is hetzelfde op elk cluster dat dezelfde opslag kent)."""
    return (volid or "").split(":", 1)[1] if ":" in (volid or "") else (volid or "")


def parse_config(text):
    """Proxmox-configtekst (extractconfig) naar {sleutel: waarde}; alleen het hoofdstuk vóór een [snapshot]."""
    out, comment = {}, []
    for line in (text or "").splitlines():
        s = line.rstrip()
        if s.startswith("["):
            break
        if s.startswith("#"):
            comment.append(s[1:].strip())
            continue
        if ":" not in s:
            continue
        k, v = s.split(":", 1)
        k = k.strip()
        if k and k not in out:
            out[k] = v.strip()
    if comment:
        out["#comment"] = "\n".join(comment)
    return out


def parse_net(value):
    """'name=eth0,bridge=vmbr0,ip=dhcp' -> lijst van (sleutel, waarde); volgorde blijft behouden."""
    parts = []
    for p in str(value or "").split(","):
        if not p:
            continue
        k, _, v = p.partition("=")
        parts.append((k, v))
    return parts


def net_info(key, value):
    d = dict(parse_net(value))
    first = parse_net(value)[0] if parse_net(value) else ("", "")
    model = first[0] if first[0] not in ("name", "bridge") and first[1] else None   # qemu: virtio=MAC
    return {"key": key, "raw": value, "bridge": d.get("bridge", ""), "tag": d.get("tag", ""),
            "ip": d.get("ip", ""), "mac": d.get("hwaddr") or (first[1] if model else ""),
            "name": d.get("name") or model or "", "firewall": d.get("firewall", "") == "1"}


def edit_net(value, bridge=None, tag=None):
    """Zet een andere bridge (en eventueel VLAN-tag) in een netX-regel en laat de rest ongemoeid.
    tag: None = laten staan; '' of 'none' = VLAN-tag weghalen; getal = nieuwe tag."""
    parts = parse_net(value)
    out, seen_b, seen_t = [], False, False
    for k, v in parts:
        if k == "bridge" and bridge:
            out.append((k, bridge))
            seen_b = True
        elif k == "tag" and tag is not None:
            seen_t = True
            if str(tag) not in ("", "none", "0"):
                out.append((k, str(tag)))
        else:
            out.append((k, v))
            seen_b = seen_b or k == "bridge"
            seen_t = seen_t or k == "tag"
    if bridge and not any(k == "bridge" for k, _ in out):
        out.append(("bridge", bridge))
    if tag is not None and str(tag) not in ("", "none", "0") and not seen_t:
        out.append(("tag", str(tag)))
    return ",".join(k + "=" + v if v != "" or k in ("bridge", "tag") else k for k, v in out)


def summarize(conf, kind):
    """Wat er in de back-up zit, kort genoeg voor in het venster."""
    nets = [net_info(k, conf[k]) for k in sorted((k for k in conf if NET_KEY_RE.match(k)), key=lambda x: int(x[3:]))]
    disks, bind = [], []
    for k in sorted(k for k in conf if DISK_KEY_RE.match(k)):
        v = conf[k]
        if v.startswith("/"):
            bind.append(k)
        if "media=cdrom" in v:
            continue
        m = re.search(r"size=([0-9.]+[KMGT]?)", v)
        disks.append({"key": k, "size": m.group(1) if m else "", "storage": v.split(":", 1)[0] if ":" in v and not v.startswith("/") else ""})
    mem = conf.get("memory")
    return {"type": kind, "name": conf.get("hostname") or conf.get("name") or "",
            "cores": conf.get("cores"), "memory": int(mem) if str(mem or "").isdigit() else None,
            "unprivileged": conf.get("unprivileged") in ("1", 1) if kind == "lxc" else None,
            "features": conf.get("features") or "", "nets": nets, "disks": disks, "bind_mounts": bind,
            "description": conf.get("#comment") or conf.get("description") or ""}


def _clean_tag(v):
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("", "none", "geen", "0"):
        return ""
    if s.isdigit() and 1 <= int(s) <= 4094:
        return s
    raise ValueError("ongeldige VLAN-tag '{}' (1 tot 4094, of leeg)".format(v))


# ------------------------------------------------------------------ opzoeken
def _online_nodes(grp):
    return [n["name"] for n in grp.get("nodes") or [] if n["status"] == "online"]


def _group(server):
    grp = col().group(server)
    c = col().client(server)
    if not grp or not c:
        abort(404)
    return c, grp


def _backup_storages(grp, node=None):
    """(node, opslagnaam) van de back-upopslag die bereikbaar is; gedeelde opslag één keer, lokale per node."""
    online = set(_online_nodes(grp))
    seen, res = set(), []
    for s in grp.get("storage") or []:
        if "backup" not in (s.get("content") or "") or s.get("status") != "available" or s["node"] not in online:
            continue
        if node and s["node"] != node:
            continue
        key = s["name"] if s["shared"] and not node else (s["name"], s["node"])
        if key in seen:
            continue
        seen.add(key)
        res.append((s["node"], s["name"], s))
    return res


def _items(c, node, storage, vmid=None):
    params = {"content": "backup"}
    if vmid:
        params["vmid"] = int(vmid)
    return c.get("/nodes/{}/storage/{}/content".format(q(node), q(storage)), params) or []


def find_on_node(c, grp, node, volid, vmid=None):
    """De naam van deze back-up zoals die op `node` heet (andere opslagnaam, zelfde bestand). None als de node hem niet ziet."""
    rel = rel_volid(volid)
    for n, st, _row in _backup_storages(grp, node):
        try:
            for it in _items(c, n, st, vmid):
                if rel_volid(it.get("volid")) == rel:
                    return it["volid"]
        except PveError:
            continue
    return None


# ------------------------------------------------------------------ pagina en lijsten
@bp.get("/restore")
def restore_page():
    _admin()
    return render_template("restore.html", page="restore")


@bp.get("/api/restore/backups/<server>")
def api_backups(server):
    """Alle back-ups op de back-upopslag van deze server (niet alleen de laatste)."""
    _admin()
    c, grp = _group(server)
    if not grp.get("reachable"):
        return jsonify({"error": grp.get("error") or "server niet bereikbaar"}), 502
    names = {x["vmid"]: x for x in grp["guests"]}
    todo = [(n, st, row) for n, st, row in _backup_storages(grp)]
    futs = [(n, st, row, _pool.submit(_items, c, n, st)) for n, st, row in todo]
    out, errors = [], {}
    for n, st, row, f in futs:
        try:
            items = f.result(timeout=60)
        except Exception as e:  # noqa: BLE001 - PBS kan traag zijn of weigeren
            errors[st] = str(e)[:200]
            continue
        for it in items:
            kind = volid_type(it.get("volid"), it.get("subtype"))
            if not kind or not str(it.get("vmid", "")).isdigit():
                continue
            vmid = int(it["vmid"])
            ex = names.get(vmid)
            out.append({"volid": it["volid"], "vmid": vmid, "type": kind, "ctime": it.get("ctime"), "size": it.get("size"),
                        "storage": st, "node": None if row.get("shared") else n, "shared": bool(row.get("shared")),
                        "network": row.get("type") in NET_TYPES, "pbs": row.get("type") == "pbs",
                        "name": (ex or {}).get("name") or (it.get("notes") or "").split("\n")[0][:60],
                        "exists": bool(ex), "protected": bool(it.get("protected")),
                        "verified": (it.get("verification") or {}).get("state")})
    out.sort(key=lambda x: (x["vmid"], -(x["ctime"] or 0)))
    return jsonify({"server": server, "name": grp["name"], "backups": out, "errors": errors,
                    "storages": [{"name": st, "node": None if row.get("shared") else n} for n, st, row in todo]})


@bp.get("/api/restore/targets")
def api_targets():
    """Alle servers/clusters waar je naartoe kunt terugzetten, met hun online nodes."""
    _admin()
    res = []
    for s in col().cfg.get("servers") or []:
        grp = col().group(s["name"])
        if not grp:
            continue
        res.append({"server": grp["server"], "name": grp["name"], "kind": grp["kind"], "reachable": grp["reachable"],
                    "nodes": _online_nodes(grp) if grp["reachable"] else []})
    return jsonify({"targets": res})


@bp.get("/api/restore/prepare")
def api_prepare():
    """Alles wat het terugzetvenster nodig heeft voor één back-up en één doelnode."""
    _admin()
    src, volid = request.args.get("src", ""), request.args.get("volid", "")
    tgt, node = request.args.get("target", ""), request.args.get("node", "")
    c, grp = _group(tgt)
    if not grp.get("reachable") or node not in _online_nodes(grp):
        return jsonify({"error": "De node {} is niet online".format(node)}), 400
    kind = volid_type(volid, request.args.get("type"))
    if not kind:
        return jsonify({"error": "onbekend type back-up"}), 400
    vmid_src = request.args.get("vmid", "")
    vmid_src = int(vmid_src) if vmid_src.isdigit() else None
    target_volid = find_on_node(c, grp, node, volid, vmid_src)
    if not target_volid:
        return jsonify({"error": "{} / {} kan deze back-up niet zien. Voeg in Proxmox dezelfde back-upopslag toe op dat "
                                 "cluster (Datacenter › Opslag › Toevoegen, met inhoud 'VZDump back-upbestand'), of kies een "
                                 "node die de opslag wel heeft.".format(grp["name"], node), "not_visible": True}), 404
    out = {"volid": target_volid, "type": kind, "config": None, "config_error": None}
    try:
        text = c.get("/nodes/{}/vzdump/extractconfig".format(q(node)), {"volume": target_volid})
        out["config"] = summarize(parse_config(text if isinstance(text, str) else ""), kind)
    except PveError as e:
        out["config_error"] = str(e)[:300]
    try:
        nets = c.get("/nodes/{}/network".format(q(node)), {"type": "any_bridge"}) or []
        out["bridges"] = sorted({n["iface"] for n in nets if n.get("type") in ("bridge", "OVSBridge") or n.get("iface", "").startswith("vmbr")})
    except PveError as e:
        out["bridges"], out["bridges_error"] = None, str(e)[:200]
    need = "rootdir" if kind == "lxc" else "images"
    seen, stores = set(), []
    for s in grp.get("storage") or []:
        if s["node"] != node or s.get("status") != "available" or need not in (s.get("content") or ""):
            continue
        if s["name"] in seen:
            continue
        seen.add(s["name"])
        stores.append({"name": s["name"], "type": s["type"], "shared": bool(s["shared"]), "pct": s.get("pct"),
                       "free": (s.get("total") or 0) - (s.get("used") or 0), "total": s.get("total")})
    out["storages"] = sorted(stores, key=lambda s: -s["free"])
    try:
        out["nextid"] = int(c.get("/cluster/nextid"))
    except (PveError, TypeError, ValueError):
        used = {x["vmid"] for x in grp["guests"]}
        out["nextid"] = next(i for i in range(100, 100000) if i not in used)
    out["used_ids"] = {str(x["vmid"]): x["name"] for x in grp["guests"]}
    sg = col().group(src)
    ex = next((x for x in (sg or {}).get("guests") or [] if x["vmid"] == vmid_src), None) if vmid_src else None
    out["original"] = {"server": src, "name": ex["name"], "node": ex["node"], "status": ex["status"]} if ex else None
    return jsonify(out)


# ------------------------------------------------------------------ terugzetten
def _wait_task(c, node, upid, job, what):
    """Wacht tot een Proxmox-taak klaar is. (ok, laatste regels)."""
    t0, errors = time.time(), 0
    while time.time() - t0 < MAX_WAIT:
        try:
            st = c.get("/nodes/{}/tasks/{}/status".format(q(node), q(upid))) or {}
            errors = 0
        except PveError as e:
            errors += 1
            if errors > 30:
                return False, ["Taak niet meer op te vragen: {}".format(e)]
            time.sleep(POLL_SECONDS)
            continue
        if st.get("status") == "stopped":
            ok = st.get("exitstatus") == "OK"
            try:
                rows = c.get("/nodes/{}/tasks/{}/log".format(q(node), q(upid)), {"start": 0, "limit": 500}) or []
            except PveError:
                rows = []
            return ok, [r.get("t", "") for r in rows][-8:] or [str(st.get("exitstatus"))]
        time.sleep(POLL_SECONDS)
    return False, ["{} duurde langer dan {} uur".format(what, MAX_WAIT // 3600)]


def _finish(job, c, collector):
    """Draait op de achtergrond: wacht op de restore, past de bridge(s) aan en start (als gevraagd)."""
    node, kind, vmid = job["node"], job["type"], job["vmid"]
    base = "/nodes/{}/{}/{}".format(q(node), kind, vmid)
    try:
        job["state"] = "restoring"
        ok, lines = _wait_task(c, node, job["upid"], job, "Terugzetten")
        if not ok:
            err = next((ln for ln in reversed(lines) if "error" in ln.lower() or "fail" in ln.lower()), lines[-1] if lines else "")
            _end(job, "error", "Terugzetten mislukt: {}".format(err.replace("TASK ERROR: ", "")))
            return
        if job["nets"]:
            job["state"] = "network"
            try:
                conf = c.get(base + "/config") or {}
                changes = {}
                for key, spec in job["nets"].items():
                    if conf.get(key):
                        changes[key] = edit_net(conf[key], spec.get("bridge"), spec.get("tag"))
                if changes:
                    res = c.put(base + "/config", changes)
                    if isinstance(res, str) and res.startswith("UPID"):
                        ok2, l2 = _wait_task(c, node, res, job, "Netwerk instellen")
                        if not ok2:
                            raise PveError(l2[-1] if l2 else "mislukt")
            except PveError as e:
                _end(job, "warn", "Teruggezet als {} {}, maar het netwerk is NIET aangepast ({}). {} is niet gestart. "
                                  "Controleer de rechten VM.Config.Network en SDN.Use, of pas de bridge zelf aan in Proxmox."
                     .format("CT" if kind == "lxc" else "VM", vmid, str(e)[:200], "De CT" if kind == "lxc" else "De VM"))
                return
        if job["start"]:
            job["state"] = "starting"
            try:
                up = c.post(base + "/status/start")
                if isinstance(up, str) and up.startswith("UPID"):
                    ok3, l3 = _wait_task(c, node, up, job, "Starten")
                    if not ok3:
                        raise PveError(l3[-1] if l3 else "mislukt")
            except PveError as e:
                _end(job, "warn", "Teruggezet als {} {}, maar starten lukte niet: {}".format("CT" if kind == "lxc" else "VM", vmid, str(e)[:200]))
                return
        _end(job, "done", "{} {} is teruggezet{}{}".format(
            "CT" if kind == "lxc" else "VM", vmid, ", netwerk aangepast" if job["nets"] else "", ", gestart" if job["start"] else ""))
    except Exception as e:  # noqa: BLE001 - een achtergrondtaak mag nooit stil sterven
        log.exception("restore")
        _end(job, "error", "Onverwachte fout: {}".format(e))
    finally:
        try:
            collector.refresh(job["server"])
        except Exception:  # noqa: BLE001
            log.exception("refresh na restore")


def _end(job, state, message):
    job["state"], job["message"], job["ended"] = state, message, int(time.time())
    try:
        from .actions import audit
        audit({"who": job["who"], "action": "restore_done", "server": job["server"], "node": job["node"], "vmid": job["vmid"],
               "label": message, "ok": state == "done", "error": None if state == "done" else message})
    except Exception:  # noqa: BLE001
        log.exception("audit")


def _public(job):
    return {k: job[k] for k in ("id", "server", "name", "node", "type", "vmid", "src_vmid", "state", "message", "started", "ended",
                                "upid", "volid", "storage", "start", "nets", "who")}


def _perm_or_error(e, server):
    from .backups import PERM_RE, _perm_error
    if PERM_RE.search(str(e)):      # "Permission check failed (/pad, Recht)": noem het recht en geef de pveum-commando's
        return jsonify(_perm_error(e, "terugzetten", server)), 403
    if isinstance(e, PvePermissionError):
        return jsonify({"error": "Proxmox weigert het terugzetten: {}. Controleer de rechten van het API-token "
                                 "(VM.Allocate, Datastore.AllocateSpace op de doelopslag). Sommige containers (bijvoorbeeld "
                                 "zonder 'unprivileged' of met bind mounts) mag Proxmox alleen root@pam terugzetten; doe dat dan "
                                 "in de Proxmox-interface.".format(e)}), 403
    return jsonify({"error": "Proxmox: {}".format(e)}), 502


@bp.post("/api/restore/start")
def api_start():
    _admin()
    body = request.get_json(silent=True) or {}
    src, tgt, node = body.get("src") or "", body.get("target") or "", body.get("node") or ""
    c, grp = _group(tgt)
    if not grp.get("reachable") or node not in _online_nodes(grp):
        return jsonify({"error": "De node {} is niet online".format(node)}), 400
    src_volid = str(body.get("volid") or "")
    kind = volid_type(src_volid, body.get("type"))
    if not kind or not src_volid:
        return jsonify({"error": "onbekend type back-up"}), 400
    try:
        vmid = int(body.get("vmid"))
        if not 100 <= vmid <= 999999999:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "Kies een geldig ID (een getal vanaf 100)"}), 400
    used = next((x for x in grp["guests"] if x["vmid"] == vmid), None)
    if used:
        return jsonify({"error": "ID {} is al in gebruik op {} ({}). PVE Fleet overschrijft nooit een bestaande VM of container; "
                                 "kies een ander ID.".format(vmid, grp["name"], used["name"])}), 409
    storage = str(body.get("storage") or "")
    need = "rootdir" if kind == "lxc" else "images"
    ok_st = {s["name"] for s in grp.get("storage") or []
             if s["node"] == node and s.get("status") == "available" and need in (s.get("content") or "")}
    if not STORAGE_RE.match(storage) or storage not in ok_st:
        return jsonify({"error": "Kies een opslag op {} voor de schijven".format(node)}), 400
    src_vmid = body.get("src_vmid")
    src_vmid = int(src_vmid) if str(src_vmid or "").isdigit() else None
    volid = find_on_node(c, grp, node, src_volid, src_vmid)
    if not volid:
        return jsonify({"error": "{} kan deze back-up niet zien; voeg dezelfde back-upopslag daar toe".format(node)}), 404
    nets = {}
    try:
        bridges = None
        if any((v or {}).get("bridge") for v in (body.get("nets") or {}).values()):
            try:
                lst = c.get("/nodes/{}/network".format(q(node)), {"type": "any_bridge"}) or []
                bridges = {n["iface"] for n in lst if n.get("type") in ("bridge", "OVSBridge") or n.get("iface", "").startswith("vmbr")}
            except PveError:
                bridges = None     # niet kunnen controleren: Proxmox zegt het zelf als de bridge niet bestaat
        for key, spec in (body.get("nets") or {}).items():
            if not NET_KEY_RE.match(key) or not isinstance(spec, dict):
                continue
            br = str(spec.get("bridge") or "").strip()
            if br and (not BRIDGE_RE.match(br) or (bridges is not None and br not in bridges)):
                return jsonify({"error": "De bridge '{}' bestaat niet op {}".format(br, node)}), 400
            tag = _clean_tag(spec.get("tag"))
            if br or tag is not None:
                nets[key] = {"bridge": br or None, "tag": tag}
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    unique = bool(body.get("unique"))
    data = {"vmid": vmid, "storage": storage, "start": 0}
    if kind == "lxc":
        data.update({"ostemplate": volid, "restore": 1})
    else:
        data["archive"] = volid
    if unique:
        data["unique"] = 1
    base = "/nodes/{}/{}".format(q(node), kind)
    try:
        upid = c.post(base, data)
    except PveError as e:
        return _perm_or_error(e, tgt)
    if not isinstance(upid, str) or not upid.startswith("UPID"):
        return jsonify({"error": "Proxmox gaf geen taak terug"}), 502
    name = str(body.get("name") or "")[:60]
    label = "Terugzetten als {} {}{}".format("CT" if kind == "lxc" else "VM", vmid, " " + name if name else "")
    job = {"id": "r{:x}".format(int(time.time() * 1000)), "server": tgt, "name": name, "node": node, "type": kind, "vmid": vmid,
           "src_vmid": src_vmid, "state": "restoring", "message": "", "started": int(time.time()), "ended": None, "upid": upid,
           "volid": volid, "storage": storage, "start": bool(body.get("start")), "nets": nets, "who": g.who}
    with _jobs_lock:
        _jobs[job["id"]] = job
        while len(_jobs) > MAX_JOBS:
            _jobs.popitem(last=False)
    try:
        from .actions import audit
        audit({"who": g.who, "action": "restore", "server": tgt, "node": node, "vmid": vmid, "label": label,
               "upid": upid, "ok": True})
    except Exception:  # noqa: BLE001
        log.exception("audit")
    threading.Thread(target=_finish, args=(job, c, col()), name="restore-" + job["id"], daemon=True).start()
    return jsonify({"ok": True, "job": job["id"], "server": tgt, "node": node, "upid": upid, "label": label,
                    "message": label + " gestart"})


@bp.get("/api/restore/jobs")
def api_jobs():
    _admin()
    with _jobs_lock:
        rows = [_public(j) for j in _jobs.values()]
    return jsonify({"jobs": list(reversed(rows))})
