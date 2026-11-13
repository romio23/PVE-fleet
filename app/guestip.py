"""IP-adressen van VM's en containers opzoeken.

Volgorde:
1. Draaiende container: /lxc/<id>/interfaces (wat de container nu echt heeft).
2. Draaiende VM: QEMU guest agent (network-get-interfaces). Werkt alleen als de agent
   in de VM draait en het token VM.GuestAgent.Audit (Proxmox 9) of VM.Monitor (8) heeft.
3. Anders: een vast IP-adres uit de configuratie (ip=... bij een container, ipconfig0 bij cloud-init).
"""
import ipaddress
import re

from .pve import PveError, q

SKIP_IFACE = re.compile(r"^(lo|docker\d*|br-|veth|virbr|cni|flannel|cali|vxlan|kube|tun|tap|zt|lxcbr|lxdbr)", re.I)
CFG_IP = re.compile(r"(?:^|,)ip=(\d+\.\d+\.\d+\.\d+)(?:/\d+)?")


def _usable(addr, v6=False):
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return False
    return (ip.version == 6) == v6


def _pick(pairs):
    """pairs: [(interface, adres)] -> lijst met bruikbare IPv4-adressen; 'echte' interfaces eerst."""
    main = [a for i, a in pairs if not SKIP_IFACE.match(i or "") and _usable(a)]
    other = [a for i, a in pairs if SKIP_IFACE.match(i or "") and _usable(a) and not (i or "").lower().startswith("lo")]
    out = []
    for a in main + other:
        if a not in out:
            out.append(a)
    if not out:   # alleen IPv6? Dan het eerste globale IPv6-adres
        out = [a for i, a in pairs if not SKIP_IFACE.match(i or "") and _usable(a, v6=True)][:1]
    return out


def from_config(cfg):
    out = []
    for k in sorted((cfg or {}).keys()):
        if re.match(r"^(net|ipconfig)\d+$", k):
            m = CFG_IP.search(str(cfg[k]))
            if m and m.group(1) not in out:
                out.append(m.group(1))
    return out


def lookup(c, node, kind, vmid, running, cfg=None):
    """Geeft (ips, bron, fout). bron: 'live' of 'config'."""
    base = "/nodes/{}/{}/{}".format(q(node), kind, int(vmid))
    err = None
    if running:
        try:
            if kind == "lxc":
                data = c.get(base + "/interfaces") or []
                pairs = []
                for iface in data:
                    for key in ("inet", "inet6"):
                        for a in str(iface.get(key) or "").split():
                            pairs.append((iface.get("name"), a.split("/")[0]))
            else:
                data = c.get(base + "/agent/network-get-interfaces") or {}
                pairs = [(iface.get("name"), a.get("ip-address"))
                         for iface in (data.get("result") or [])
                         for a in (iface.get("ip-addresses") or []) if a.get("ip-address")]
            ips = _pick(pairs)
            if ips:
                return ips, "live", None
        except PveError as e:
            err = str(e)
    if cfg is None:
        try:
            cfg = c.get(base + "/config") or {}
        except PveError as e:
            return [], None, err or str(e)
    ips = from_config(cfg)
    return ips, ("config" if ips else None), err
