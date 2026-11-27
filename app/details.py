"""Detailgegevens die pas worden opgehaald als iemand een node of VM/CT opent."""
import re
import time
from urllib.parse import quote

from . import guestip
from .pve import PveError, PvePermissionError


class NotFound(Exception):
    pass


_CACHE = {}


def _cached(key, ttl, fn):
    """Trage of zware calls (SMART, pakketlijst) niet bij elke verversing opnieuw doen."""
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _CACHE[key] = (time.time(), val)
    return val


def _try(fn, default=None):
    """Voer een API-call uit; geef (data, foutmelding) terug zonder te crashen."""
    try:
        return fn(), None
    except PvePermissionError:
        return default, "geen rechten"
    except PveError as e:
        return default, str(e)[:160]


def _rrd(rows, keys):
    out = {"time": []}
    for k in keys:
        out[k] = []
    for r in rows or []:
        out["time"].append(r.get("time"))
        for k in keys:
            v = r.get(k)
            out[k].append(None if v is None else float(v))
    return out


def console_url(base, kind, node, vmid=0, name=""):
    if not base:
        return None
    if kind == "shell":
        q = "console=shell&xtermjs=1&vmid=0&vmname=&node={}&cmd=".format(quote(node))
    elif kind == "qemu":
        q = "console=kvm&novnc=1&vmid={}&vmname={}&node={}&resize=off&cmd=".format(vmid, quote(name), quote(node))
    else:
        q = "console=lxc&xtermjs=1&vmid={}&vmname={}&node={}&cmd=".format(vmid, quote(name), quote(node))
    return base + "?" + q


def node_detail(collector, server, node):
    group = collector.group(server)
    if not group:
        raise NotFound("server")
    n = next((x for x in group["nodes"] if x["name"] == node), None)
    if not n:
        raise NotFound("node")
    c = collector.client(server)
    result = {
        "server": server, "group": {k: group[k] for k in ("name", "kind", "reachable", "error", "since", "quorate",
                                                           "nodes_online", "nodes_total", "web_url")},
        "node": n,
        "peers": [x["name"] for x in group["nodes"] if x["name"] != node],
        "wol_possible": bool(collector.cfg.get("demo") or (collector.server_cfg(server) or {}).get("wol")
                             or (group["kind"] == "cluster" and any(x["status"] == "online" for x in group["nodes"] if x["name"] != node))),
        "guests": sorted([g for g in group["guests"] if g["node"] == node], key=lambda g: g["vmid"] or 0),
        "storage": [s for s in group["storage"] if s["node"] == node],
        "tasks": [t for t in group["tasks"] if t["node"] == node][:10],
        "errors": {},
        "shell_url": console_url(group.get("web_url"), "shell", node),
    }
    if n["status"] != "online":
        result.update({"rrd": None, "disks": None, "packages": None})
        return result

    st, err = _try(lambda: c.get("/nodes/{}/status".format(node)), {})
    if err:
        result["errors"]["status"] = err
    st = st or {}
    mem = st.get("memory") or {}
    result["status"] = {"loadavg": st.get("loadavg"), "wait": st.get("wait"), "mem_total": mem.get("total"),
                        "mem_used": mem.get("used"), "cpuinfo": st.get("cpuinfo"),
                        "pveversion": st.get("pveversion"), "kversion": st.get("kversion")}

    rrd, err = _try(lambda: c.get("/nodes/{}/rrddata".format(node), {"timeframe": "hour", "cf": "AVERAGE"}), [])
    if err:
        result["errors"]["rrd"] = err
    result["rrd"] = _rrd(rrd, ["cpu", "memused", "memtotal", "netin", "netout", "iowait", "loadavg"])

    disks, err = _cached(("disks", server, node), 300,
                         lambda: _try(lambda: c.get("/nodes/{}/disks/list".format(node)), None))
    if err:
        result["errors"]["disks"] = err
    result["disks"] = [{"dev": d.get("devpath"), "model": d.get("model"), "size": d.get("size"),
                        "health": d.get("health"), "wearout": d.get("wearout"), "type": d.get("type")}
                       for d in (disks or [])]

    pk, err = _cached(("apt", server, node), 300,
                      lambda: _try(lambda: c.get("/nodes/{}/apt/update".format(node)), None))
    if err:
        result["errors"]["packages"] = err
    result["packages"] = [{"name": p.get("Package"), "old": p.get("OldVersion"), "new": p.get("Version"),
                           "origin": p.get("Origin")} for p in (pk or [])] if pk is not None else None
    from .collector import apt_net_failure
    result["apt_error"], _e = _cached(("aptfail", server, node), 300, lambda: (apt_net_failure(c, node), None))
    return result


def _disk_line(val):
    store, _, rest = val.partition(":")
    size = re.search(r"size=([\w.]+)", val)
    vol = rest.split(",")[0]
    return "{} · {}{}".format(store, vol, (" · " + size.group(1)) if size else "")


def _net_line(val):
    bits = dict(p.split("=", 1) for p in val.split(",") if "=" in p)
    parts = []
    model = next((k for k in ("virtio", "e1000", "vmxnet3", "rtl8139") if k in bits), None)
    if model:
        parts.append(model)
    if "name" in bits:
        parts.append(bits["name"])
    if "bridge" in bits:
        parts.append(bits["bridge"])
    if "tag" in bits:
        parts.append("VLAN " + bits["tag"])
    if "ip" in bits:
        parts.append(bits["ip"])
    if bits.get("firewall") == "1":
        parts.append("firewall aan")
    return " · ".join(parts) or val


def config_rows(kind, cfg):
    rows = []
    cores = cfg.get("cores", 1)
    sockets = cfg.get("sockets", 1)
    rows.append(("Processor", "{} cores{}".format(int(cores) * int(sockets), "" if int(sockets) == 1 else " ({} sockets)".format(sockets))))
    mem = cfg.get("memory")
    if mem:
        extra = []
        if kind == "qemu" and str(cfg.get("balloon", "")) != "0":
            extra.append("ballooning aan")
        if kind == "lxc" and cfg.get("swap") is not None:
            extra.append("swap {} MB".format(cfg["swap"]))
        rows.append(("Geheugen", "{} MB{}".format(mem, (" · " + ", ".join(extra)) if extra else "")))
    if kind == "qemu":
        if cfg.get("bios") or cfg.get("machine"):
            rows.append(("BIOS / machine", " · ".join(x for x in [
                "OVMF (UEFI)" if cfg.get("bios") == "ovmf" else "SeaBIOS", cfg.get("machine")] if x)))
        for k in sorted(cfg):
            if re.match(r"^(scsi|virtio|sata|ide)\d+$", k) and "media=cdrom" not in str(cfg[k]):
                rows.append(("Schijf " + k, _disk_line(str(cfg[k]))))
        if cfg.get("agent"):
            rows.append(("QEMU guest agent", "aan" if str(cfg["agent"]).startswith("1") or "enabled=1" in str(cfg["agent"]) else "uit"))
        if cfg.get("boot"):
            rows.append(("Opstartvolgorde", str(cfg["boot"]).replace("order=", "").replace(";", ", ")))
        for k in sorted(cfg):
            if re.match(r"^(hostpci|usb)\d+$", k):
                rows.append(("Passthrough " + k, str(cfg[k])))
    else:
        if cfg.get("rootfs"):
            rows.append(("Root-schijf", _disk_line(str(cfg["rootfs"]))))
        for k in sorted(cfg):
            if re.match(r"^mp\d+$", k):
                val = str(cfg[k])
                mp = re.search(r"mp=([^,]+)", val)
                rows.append(("Koppelpunt " + k, "{} → {}".format(val.split(",")[0], mp.group(1) if mp else "?")))
        rows.append(("Type", ("unprivileged" if str(cfg.get("unprivileged", "0")) == "1" else "privileged") +
                     ((" · " + cfg["features"]) if cfg.get("features") else "")))
    for k in sorted(cfg):
        if re.match(r"^net\d+$", k):
            rows.append(("Netwerk " + k, _net_line(str(cfg[k]))))
    if cfg.get("ostype"):
        rows.append(("OS-type", str(cfg["ostype"])))
    return [{"label": a, "value": b} for a, b in rows]


def guest_detail(collector, server, node, kind, vmid):
    if kind not in ("qemu", "lxc"):
        raise NotFound("type")
    group = collector.group(server)
    if not group:
        raise NotFound("server")
    row = next((g for g in group["guests"] if g["vmid"] == vmid and g["node"] == node), None)
    if not row:
        raise NotFound("VM/CT")
    c = collector.client(server)
    base = "/nodes/{}/{}/{}".format(node, kind, vmid)
    out = {"server": server, "node": node, "type": kind, "vmid": vmid, "row": row,
           "group": {"name": group["name"], "kind": group["kind"], "reachable": group["reachable"]},
           "node_status": next((n["status"] for n in group["nodes"] if n["name"] == node), "unknown"),
           "errors": {}, "console_url": console_url(group.get("web_url"), kind, node, vmid, row["name"])}
    if not group["reachable"] or out["node_status"] != "online":
        out.update({"config": [], "rrd": None, "snapshots": [], "backups": [], "ip": row.get("ip"), "ips": row.get("ips") or [], "ha": None,
                    "onboot": None, "protection": None, "description": None})
        return out

    cfg, err = _try(lambda: c.get(base + "/config"), {})
    if err:
        out["errors"]["config"] = err
    cfg = cfg or {}
    out["config"] = config_rows(kind, cfg)
    out["onboot"] = str(cfg.get("onboot", "0")) == "1"
    out["protection"] = str(cfg.get("protection", "0")) == "1"
    out["description"] = cfg.get("description")
    out["ostype"] = cfg.get("ostype")

    cur, err = _try(lambda: c.get(base + "/status/current"), {})
    cur = cur or {}
    out["ha"] = bool((cur.get("ha") or {}).get("managed"))

    rrd, err = _try(lambda: c.get(base + "/rrddata", {"timeframe": "hour", "cf": "AVERAGE"}), [])
    if err:
        out["errors"]["rrd"] = err
    out["rrd"] = _rrd(rrd, ["cpu", "mem", "maxmem", "netin", "netout", "diskread", "diskwrite"])

    snaps, err = _try(lambda: c.get(base + "/snapshot"), [])
    if err:
        out["errors"]["snapshots"] = err
    out["snapshots"] = sorted([{"name": s.get("name"), "description": s.get("description"), "time": s.get("snaptime")}
                               for s in (snaps or []) if s.get("name") != "current"],
                              key=lambda s: -(s["time"] or 0))

    running = row["status"] == "running"
    ips, src, ip_err = guestip.lookup(c, node, kind, vmid, running, cfg)
    collector.guest_ips[(server, kind, int(vmid))] = {"ips": ips, "src": src, "checked": time.time(),
                                                      "running": running, "node": node}
    out["ip"] = ips[0] if ips else None
    out["ips"] = ips
    out["ip_src"] = src
    out["ip_hint"] = None
    from . import guestupdates
    st = guestupdates.STORE
    out["gupd"] = None
    if st is not None and kind == "lxc":
        out["gupd"] = {"enabled": bool(st.settings(server).get("enabled")),
                       "result": (st.result(server).get("guests") or {}).get(str(vmid)),
                       "repo_override": st.data["repos"].get("{}/{}".format(server, vmid)),
                       "checking": server in st.running}
    if running and kind == "qemu" and src != "live":
        if ip_err and ("403" in ip_err or "rechten" in ip_err.lower() or "permission" in ip_err.lower()):
            out["ip_hint"] = "Het token mag de guest agent niet uitlezen (recht VM.GuestAgent.Audit, bij Proxmox 8 VM.Monitor)."
        else:
            out["ip_hint"] = "Geen IP-adres van de QEMU guest agent. Staat de agent aan in Opties en draait qemu-guest-agent in de VM?"

    backups = []
    stores = [s for s in group["storage"] if s["node"] == node and "backup" in s["content"]]
    for s in stores:
        items, err = _try(lambda: c.get("/nodes/{}/storage/{}/content".format(node, s["name"]),
                                        {"content": "backup", "vmid": vmid}), [])
        if err:
            out["errors"]["backups"] = err
        for b in items or []:
            if b.get("vmid") in (vmid, str(vmid)):
                backups.append({"storage": s["name"], "time": b.get("ctime"), "size": b.get("size"),
                                "volid": b.get("volid"), "notes": b.get("notes")})
    out["backups"] = sorted(backups, key=lambda b: -(b["time"] or 0))[:8]
    return out
