"""Inlezen en opslaan van config.yml (pad via PVEF_CONFIG, standaard ./data/config.yml).

Je kunt servers beheren in het dashboard (Servers & verbinding); dat schrijft dit
bestand. Met de hand bewerken mag ook; herstart dan het dashboard.
"""
import copy
import logging
import os
import re
import shutil
import time

import yaml

log = logging.getLogger(__name__)

DEFAULTS = {
    "poll_interval": 10,        # seconden tussen rondes langs alle servers
    "updates_interval": 1800,   # seconden tussen controles op pakketupdates
    "demo": False,
    "wol_broadcast": "255.255.255.255",
    "thresholds": {"ram_warn": 80, "ram_crit": 90, "cpu_warn": 90, "storage_warn": 80, "storage_crit": 90},
    "servers": [],
}
DEMO_SERVERS = [{"name": n, "wol": {}, "hosts": [], "type": None} for n in ("cluster-home", "cluster-lab", "pve-garage", "pve-mini")]

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,252})$|^\[?[0-9A-Fa-f:]+\]?$")


class ConfigError(Exception):
    pass


def config_path():
    return os.environ.get("PVEF_CONFIG", os.path.join(os.environ.get("PVEF_DATA", "data"), "config.yml"))


def clean_host(h):
    """'https://192.168.1.90:8006/' -> ('192.168.1.90', 8006)."""
    h = str(h or "").strip()
    h = re.sub(r"^[a-z]+://", "", h, flags=re.I).split("/")[0]
    port = None
    m = re.match(r"^(.*?):(\d{2,5})$", h)
    if m and h.count(":") == 1:
        h, port = m.group(1), int(m.group(2))
    return h, port


def parse_server(s, index=0):
    """Controleer en normaliseer één server uit config.yml of uit het formulier."""
    if not isinstance(s, dict):
        raise ConfigError("server #{} is geen geldige lijst-entry".format(index + 1))
    raw_hosts = s.get("hosts") or ([s["host"]] if s.get("host") else [])
    if isinstance(raw_hosts, str):
        raw_hosts = re.split(r"[\s,;]+", raw_hosts)
    hosts, port = [], s.get("port")
    for h in raw_hosts:
        h, p = clean_host(h)
        if not h:
            continue
        if not HOST_RE.match(h):
            raise ConfigError("'{}' is geen geldig IP-adres of hostnaam".format(h))
        if p and not port:
            port = p
        if h not in hosts:
            hosts.append(h)
    label = s.get("name") or (hosts[0] if hosts else "#{}".format(index + 1))
    if not hosts:
        raise ConfigError("server '{}' heeft geen IP-adres of hostnaam".format(label))
    name = str(s.get("name") or hosts[0]).strip()
    if not NAME_RE.match(name):
        raise ConfigError("naam '{}': gebruik letters, cijfers, punt, - of _ (max. 40 tekens)".format(name))
    token_id = str(s.get("token_id") or "").strip()
    if not token_id:
        raise ConfigError("server '{}' mist token_id".format(name))
    if not re.match(r"^[^@!\s]+@[^@!\s]+![^@!\s]+$", token_id):
        raise ConfigError("token_id '{}' moet de vorm gebruiker@realm!tokennaam hebben (bijv. dashboard@pve!fleet)".format(token_id))
    secret = str(s.get("token_secret") or "").strip()
    if not secret:
        raise ConfigError("server '{}' mist token_secret".format(name))
    try:
        port = int(port or 8006)
    except (TypeError, ValueError):
        raise ConfigError("ongeldige poort bij '{}'".format(name))
    if not 1 <= port <= 65535:
        raise ConfigError("ongeldige poort bij '{}'".format(name))
    wol = {}
    raw = s.get("wol") or {}
    if isinstance(raw, dict):
        wol.update({str(k).strip(): str(v).strip() for k, v in raw.items() if str(v).strip()})
    if s.get("wol_mac"):
        wol["*"] = str(s["wol_mac"]).strip()   # geldt voor de (enige) node van een losse server
    for node, mac in wol.items():
        if not MAC_RE.match(mac):
            raise ConfigError("ongeldig MAC-adres '{}' bij '{}'".format(mac, name))
    stype = s.get("type")
    if stype not in ("cluster", "standalone"):
        stype = "cluster" if len(hosts) > 1 else None
    return {"name": name, "type": stype, "hosts": hosts, "port": port, "token_id": token_id, "token_secret": secret,
            "verify_ssl": bool(s.get("verify_ssl", False)), "wol": wol}


def _read_raw(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8-sig") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ConfigError("config.yml heeft geen geldige opbouw")
    return raw


def build(raw, force_demo=False):
    """Maak de werkconfig. Fouten in servers worden verzameld in 'config_error' in plaats van te crashen."""
    cfg = copy.deepcopy(DEFAULTS)
    cfg["config_error"] = None
    if raw is None:
        cfg["demo"] = True
        raw = {}
    for k, v in raw.items():
        if k == "thresholds" and isinstance(v, dict):
            cfg["thresholds"].update(v)
        elif k != "servers":
            cfg[k] = v
    real = []
    errors = []
    for i, s in enumerate(raw.get("servers") or []):
        try:
            real.append(parse_server(s, i))
        except ConfigError as e:
            errors.append(str(e))
    names = [s["name"] for s in real]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        errors.append("servernamen moeten uniek zijn: {}".format(", ".join(dup)))
        seen, uniq = set(), []
        for s in real:
            if s["name"] not in seen:
                seen.add(s["name"])
                uniq.append(s)
        real = uniq
    if errors:
        cfg["config_error"] = "; ".join(errors)
    if force_demo or os.environ.get("DEMO", "").lower() in ("1", "true", "yes", "ja"):
        cfg["demo"] = True
    cfg["demo"] = bool(cfg["demo"])
    cfg["real_servers"] = real
    cfg["servers"] = copy.deepcopy(DEMO_SERVERS) if cfg["demo"] else real
    try:
        cfg["poll_interval"] = max(3, int(cfg.get("poll_interval", 10)))
    except (TypeError, ValueError):
        cfg["poll_interval"] = 10
    return cfg


def load():
    path = config_path()
    try:
        raw = _read_raw(path)
    except (yaml.YAMLError, ConfigError) as e:
        log.error("config.yml kan niet gelezen worden: %s", e)
        cfg = build({})
        cfg["config_error"] = "config.yml kan niet gelezen worden: {}".format(str(e).replace("\n", " ")[:300])
        return cfg
    if raw is None:
        log.warning("Geen config gevonden op %s: start in demomodus", path)
    return build(raw)


def _server_to_yaml(s):
    out = {"name": s["name"]}
    if s.get("type"):
        out["type"] = s["type"]
    if len(s["hosts"]) == 1:
        out["host"] = s["hosts"][0]
    else:
        out["hosts"] = list(s["hosts"])
    if s.get("port", 8006) != 8006:
        out["port"] = s["port"]
    out["token_id"] = s["token_id"]
    out["token_secret"] = s["token_secret"]
    out["verify_ssl"] = bool(s.get("verify_ssl"))
    wol = dict(s.get("wol") or {})
    if "*" in wol:
        out["wol_mac"] = wol.pop("*")
    if wol:
        out["wol"] = wol
    return out


HEADER = """# PVE Fleet - instellingen
# Dit bestand wordt bijgewerkt door het dashboard (Servers & verbinding).
# Met de hand bewerken mag ook; herstart daarna het dashboard.
# Laatst opgeslagen: {when}

"""


def save(cfg, servers=None, demo=None):
    """Schrijf config.yml met de huidige algemene instellingen en de opgegeven servers."""
    path = config_path()
    servers = cfg["real_servers"] if servers is None else servers
    data = {
        "demo": bool(cfg["demo"] if demo is None else demo),
        "poll_interval": cfg.get("poll_interval", 10),
        "updates_interval": cfg.get("updates_interval", 1800),
        "wol_broadcast": cfg.get("wol_broadcast", "255.255.255.255"),
        "thresholds": dict(cfg.get("thresholds") or DEFAULTS["thresholds"]),
        "servers": [_server_to_yaml(s) for s in servers],
    }
    for k in ("guest_updates_hours", "github_token"):   # optionele instellingen bewaren
        if cfg.get(k) not in (None, ""):
            data[k] = cfg[k]
    text = HEADER.format(when=time.strftime("%Y-%m-%d %H:%M")) + yaml.safe_dump(
        data, sort_keys=False, allow_unicode=True, default_flow_style=None, width=120)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")   # vorige versie bewaren
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path
