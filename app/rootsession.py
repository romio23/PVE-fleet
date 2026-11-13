"""Root-sessie voor de console: één keer inloggen per cluster, net als in de Proxmox-webinterface.

Een API-token is geen root, dus de shell van een node vraagt anders steeds om een login.
Met het root-wachtwoord vraagt PVE Fleet een ticket aan bij Proxmox (POST /access/ticket).
Het wachtwoord wordt meteen weer vergeten; alleen het ticket blijft in het geheugen
(nooit op schijf). Een ticket is 2 uur geldig; PVE Fleet verlengt het als je de console
gebruikt. Uitloggen bij PVE Fleet of een herstart van PVE Fleet beëindigt alle root-sessies.
"""
import logging
import threading
import time
from urllib.parse import quote

import requests
import urllib3

from .pve import PveError, PvePermissionError

log = logging.getLogger(__name__)
_lock = threading.Lock()
SESSIONS = {}            # server -> {"ticket", "csrf", "user", "since", "renewed", "who", "host"}
LIFETIME = 2 * 3600      # zo lang is een Proxmox-ticket geldig
RENEW_AFTER = 15 * 60    # bij gebruik verlengen als het ticket ouder is dan dit


def _hosts(c):
    hosts = list(getattr(c, "hosts", []) or [])
    act = getattr(c, "active_host", None)
    return ([act] if act else []) + [h for h in hosts if h != act]


def _post_ticket(c, data):
    """POST /access/ticket zonder API-token. Probeert alle hosts van de server."""
    if hasattr(c, "ticket_login"):       # demo
        return c.ticket_login(data), getattr(c, "active_host", None)
    last = None
    if not c.verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    for host in _hosts(c):
        url = "https://{}:{}/api2/json/access/ticket".format(host, c.port)
        try:
            r = requests.post(url, data=data, verify=c.verify, timeout=(2.5, 15))
        except requests.RequestException as e:
            last = e
            continue
        if r.status_code == 401:
            raise PvePermissionError("inloggen mislukt: gebruikersnaam, wachtwoord of code klopt niet")
        if r.status_code >= 400:
            raise PveError("{} {}".format(r.status_code, r.reason))
        return (r.json() or {}).get("data") or {}, host
    raise PveError("geen van de hosts bereikbaar: {}".format(last))


def login(c, server, username, password, otp=None, who=None):
    """Geeft {'ok': True} of {'need_tfa': True, 'types': [...]}. Gooit PveError bij een fout."""
    username = (username or "root@pam").strip()
    if "@" not in username:
        username += "@pam"
    data, host = _post_ticket(c, {"username": username, "password": password})
    if data.get("NeedTFA"):
        if not otp:
            return {"need_tfa": True}
        code = str(otp).strip().replace(" ", "")
        data, host = _post_ticket(c, {"username": username, "tfa-challenge": data["ticket"],
                                      "password": "totp:" + code})
        if data.get("NeedTFA"):
            raise PvePermissionError("de 2FA-code klopt niet")
    if not data.get("ticket"):
        raise PveError("Proxmox gaf geen ticket terug")
    now = time.time()
    with _lock:
        SESSIONS[server] = {"ticket": data["ticket"], "csrf": data.get("CSRFPreventionToken"),
                            "user": data.get("username") or username, "since": now, "renewed": now,
                            "who": who, "host": host}
    return {"ok": True, "user": SESSIONS[server]["user"]}


def end(server=None):
    with _lock:
        if server is None:
            SESSIONS.clear()
        else:
            SESSIONS.pop(server, None)


def status(server):
    s = get(server, renew=False)
    if not s:
        return None
    return {"user": s["user"], "since": int(s["since"]), "expires": int(s["renewed"] + LIFETIME)}


def get(server, c=None, renew=True):
    """Geldige sessie (verlengd als dat nodig is) of None."""
    with _lock:
        s = SESSIONS.get(server)
    if not s:
        return None
    now = time.time()
    if now - s["renewed"] > LIFETIME - 60:
        end(server)
        return None
    if renew and c is not None and now - s["renewed"] > RENEW_AFTER:
        try:
            data, host = _post_ticket(c, {"username": s["user"], "password": s["ticket"]})
            if data.get("ticket"):
                with _lock:
                    s.update(ticket=data["ticket"], csrf=data.get("CSRFPreventionToken") or s["csrf"], renewed=now, host=host)
        except PveError as e:
            log.info("root-ticket verlengen mislukt (%s): %s", server, e)
    return s


def cookie(s):
    return "PVEAuthCookie=" + quote(s["ticket"], safe="")


def post(c, s, path, data=None):
    """POST met het root-ticket (cookie + CSRF-token)."""
    if hasattr(c, "ticket_post"):        # demo
        return c.ticket_post(path, data or {})
    host = s.get("host") or c.active_host
    url = "https://{}:{}/api2/json{}".format(host, c.port, path)
    try:
        r = requests.post(url, data=data or {}, verify=c.verify, timeout=(2.5, 20),
                          headers={"Cookie": cookie(s), "CSRFPreventionToken": s["csrf"] or ""})
    except requests.RequestException as e:
        raise PveError("{} niet bereikbaar: {}".format(host, e))
    if r.status_code == 401:
        raise PvePermissionError("root-sessie verlopen; log opnieuw in")
    if r.status_code == 403:
        raise PvePermissionError("geen rechten ({})".format(r.reason))
    if r.status_code >= 400:
        raise PveError("{} {}".format(r.status_code, r.reason))
    return (r.json() or {}).get("data")
