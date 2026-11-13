"""Kleine Proxmox VE API-client op basis van API-tokens.

Praat met https://<host>:8006/api2/json. Voor een cluster kun je meerdere
hosts opgeven: valt de eerste weg, dan probeert de client de volgende.
Schrijfacties (POST/DELETE) schakelen alleen door als de verbinding niet eens
tot stand kwam, zodat een actie nooit per ongeluk twee keer wordt uitgevoerd.
"""
import logging
from urllib.parse import quote

import requests
import urllib3

log = logging.getLogger(__name__)


class PveError(Exception):
    """Server niet bereikbaar of onverwacht antwoord."""


class PvePermissionError(PveError):
    """Het token mist rechten voor dit endpoint (401/403)."""


def q(part):
    """Eén padsegment veilig coderen (bijv. een UPID met dubbele punten)."""
    return quote(str(part), safe="")


def _message(r):
    try:
        body = r.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        errs = body.get("errors")
        if isinstance(errs, dict) and errs:
            return "; ".join("{}: {}".format(k, str(v).strip()) for k, v in errs.items())
        if body.get("message"):
            return str(body["message"]).strip()
    return (r.reason or r.text or "").strip()[:200]


class PveClient:
    def __init__(self, hosts, token_id, token_secret, port=8006, verify_ssl=False, timeout=10):
        if isinstance(hosts, str):
            hosts = [hosts]
        self.hosts = [h for h in hosts if h]
        self.port = port
        self.verify = verify_ssl
        self.timeout = timeout
        self.active_host = self.hosts[0] if self.hosts else None
        self.session = requests.Session()
        self.session.trust_env = False   # geen systeemproxy voor servers in je eigen netwerk
        self.session.headers["Authorization"] = "PVEAPIToken={}={}".format(token_id, token_secret)
        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _url(self, host, path):
        return "https://{}:{}/api2/json{}".format(host, self.port, path)

    def _request(self, method, path, params=None, data=None, timeout=None):
        order = [self.active_host] + [h for h in self.hosts if h != self.active_host]
        write = method != "GET"
        last_err = None
        for host in order:
            try:
                r = self.session.request(method, self._url(host, path), params=params, data=data,
                                         verify=self.verify, timeout=(2.5, timeout or self.timeout))
            except requests.ConnectTimeout as e:
                last_err = e
            except requests.ConnectionError as e:
                # Bij lezen altijd doorschakelen; bij schrijven alleen als er zeker niets is verstuurd.
                if write and "Failed to establish" not in str(e) and "Name or service" not in str(e) \
                        and "getaddrinfo" not in str(e):
                    raise PveError("verbinding met {} verbroken tijdens de actie; controleer in Proxmox of "
                                   "die is uitgevoerd ({})".format(host, e))
                last_err = e
            except requests.Timeout as e:
                if write:
                    raise PveError("{} reageerde niet op tijd; de actie kan toch gestart zijn. "
                                   "Controleer de taken in Proxmox.".format(host))
                last_err = e
            else:
                if r.status_code == 401:
                    raise PvePermissionError("aanmelden bij Proxmox mislukt: token_id of token_secret klopt niet "
                                             "({})".format(_message(r)))
                if r.status_code == 403:
                    raise PvePermissionError("geen rechten: {}".format(_message(r)))
                if r.status_code >= 400:
                    raise PveError("{} {}".format(r.status_code, _message(r)))
                self.active_host = host
                try:
                    return r.json().get("data")
                except ValueError as e:
                    raise PveError("ongeldig antwoord van {}: {}".format(host, e))
            log.debug("host %s onbereikbaar: %s", host, last_err)
        reason = "time-out" if isinstance(last_err, requests.Timeout) else (
            "verbinding geweigerd" if "refused" in str(last_err).lower() or "10061" in str(last_err) else str(last_err)[:160])
        raise PveError("geen van de hosts bereikbaar op poort {} ({}): {}".format(self.port, ", ".join(self.hosts), reason))

    def get(self, path, params=None):
        return self._request("GET", path, params=params)

    def post(self, path, data=None):
        return self._request("POST", path, data=data or {}, timeout=max(self.timeout, 20))

    def put(self, path, data=None):
        return self._request("PUT", path, data=data or {}, timeout=max(self.timeout, 20))

    def delete(self, path, params=None):
        return self._request("DELETE", path, params=params, timeout=max(self.timeout, 20))

    def web_url(self):
        return "https://{}:{}/".format(self.active_host, self.port)
