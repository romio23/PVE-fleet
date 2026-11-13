"""DNS-providers voor de DNS-01-controle van Let's Encrypt: TransIP en Cloudflare.

Elke provider heeft add_txt(naam, waarde) -> handle, remove_txt(handle) en test().
wait_txt() wacht tot het record op de autoritatieve nameservers van het domein staat;
die vraagt Let's Encrypt ook.
"""
import base64
import json
import secrets
import time

import requests


class DnsError(Exception):
    pass


def _fqdn(name):
    return name.rstrip(".").lower()


# ------------------------------------------------------------------ Cloudflare
class Cloudflare:
    API = "https://api.cloudflare.com/client/v4"

    def __init__(self, token, api=None):
        if not token:
            raise DnsError("Vul het API-token van Cloudflare in")
        self.api = api or self.API
        self.s = requests.Session()
        self.s.headers.update({"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        self._zones = {}

    def _req(self, method, path, **kw):
        try:
            r = self.s.request(method, self.api + path, timeout=20, **kw)
        except requests.RequestException as e:
            raise DnsError("Cloudflare niet bereikbaar: {}".format(e))
        try:
            data = r.json()
        except ValueError:
            raise DnsError("Cloudflare gaf een onverwacht antwoord ({})".format(r.status_code))
        if not data.get("success"):
            msg = "; ".join(e.get("message", "") for e in data.get("errors") or []) or r.reason
            if r.status_code in (401, 403):
                msg += ". Controleer het token: het heeft Zone › DNS › Edit nodig voor dit domein."
            raise DnsError("Cloudflare: " + msg)
        return data

    def zone_for(self, name):
        labels = _fqdn(name).split(".")
        for i in range(len(labels) - 1):
            cand = ".".join(labels[i:])
            if cand in self._zones:
                return cand, self._zones[cand]
            res = self._req("GET", "/zones", params={"name": cand}).get("result") or []
            if res:
                self._zones[cand] = res[0]["id"]
                return cand, res[0]["id"]
        raise DnsError("Geen Cloudflare-zone gevonden voor {}. Staat het domein in dit Cloudflare-account?".format(name))

    def add_txt(self, name, value):
        zone, zid = self.zone_for(name)
        res = self._req("POST", "/zones/{}/dns_records".format(zid),
                        data=json.dumps({"type": "TXT", "name": _fqdn(name), "content": value, "ttl": 60}))
        return {"zone": zid, "id": res["result"]["id"]}

    def remove_txt(self, h):
        self._req("DELETE", "/zones/{}/dns_records/{}".format(h["zone"], h["id"]))

    def test(self):
        res = self._req("GET", "/zones", params={"per_page": 50}).get("result") or []
        return [z["name"] for z in res]


# ------------------------------------------------------------------ TransIP
class TransIP:
    API = "https://api.transip.nl/v6"

    def __init__(self, login, private_key, global_key=True, api=None):
        if not login or not private_key:
            raise DnsError("Vul de TransIP-gebruikersnaam en de private key in")
        from cryptography.hazmat.primitives import serialization
        try:
            self.key = serialization.load_pem_private_key(private_key.strip().encode(), password=None)
        except (ValueError, TypeError):
            raise DnsError("De TransIP-sleutel is geen geldige private key. Plak de hele sleutel, "
                           "inclusief -----BEGIN PRIVATE KEY----- en -----END PRIVATE KEY-----.")
        self.login, self.global_key = login, bool(global_key)
        self.api = api or self.API
        self._token, self._exp = None, 0
        self._domains = None

    def _auth(self):
        if self._token and time.time() < self._exp:
            return self._token
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        body = json.dumps({"login": self.login, "nonce": secrets.token_hex(16), "read_only": False,
                           "expiration_time": "30 minutes", "label": "pve-fleet-" + secrets.token_hex(4),
                           "global_key": self.global_key})
        sig = base64.b64encode(self.key.sign(body.encode(), padding.PKCS1v15(), hashes.SHA512())).decode()
        try:
            r = requests.post(self.api + "/auth", data=body, timeout=20,
                              headers={"Content-Type": "application/json", "Signature": sig})
        except requests.RequestException as e:
            raise DnsError("TransIP niet bereikbaar: {}".format(e))
        if r.status_code != 201 and r.status_code != 200:
            msg = self._err(r)
            if "whitelist" in msg.lower() or "ip" in msg.lower():
                msg += (". Maak in het TransIP-controlepaneel een sleutel zonder IP-whitelist "
                        "('Alleen whitelisted IP-adressen' uit), of zet het IP-adres van je internetverbinding op de whitelist.")
            raise DnsError("TransIP-aanmelding mislukt: " + msg)
        self._token = r.json().get("token")
        self._exp = time.time() + 25 * 60
        return self._token

    @staticmethod
    def _err(r):
        try:
            return r.json().get("error") or r.reason
        except ValueError:
            return "{} {}".format(r.status_code, r.reason)

    def _req(self, method, path, body=None):
        try:
            r = requests.request(method, self.api + path, timeout=20, data=json.dumps(body) if body is not None else None,
                                 headers={"Authorization": "Bearer " + self._auth(), "Content-Type": "application/json"})
        except requests.RequestException as e:
            raise DnsError("TransIP niet bereikbaar: {}".format(e))
        if r.status_code >= 400:
            raise DnsError("TransIP: {}".format(self._err(r)))
        return r.json() if r.content else {}

    def domains(self):
        if self._domains is None:
            self._domains = [d["name"].lower() for d in self._req("GET", "/domains").get("domains") or []]
        return self._domains

    def zone_for(self, name):
        fq = _fqdn(name)
        best = None
        for d in self.domains():
            if fq == d or fq.endswith("." + d):
                if not best or len(d) > len(best):
                    best = d
        if not best:
            raise DnsError("Het domein van {} staat niet in dit TransIP-account".format(name))
        return best

    def add_txt(self, name, value):
        dom = self.zone_for(name)
        rel = _fqdn(name)[:-len(dom)].rstrip(".") or "@"
        entry = {"name": rel, "expire": 60, "type": "TXT", "content": value}
        self._req("POST", "/domains/{}/dns".format(dom), {"dnsEntry": entry})
        return {"domain": dom, "entry": entry}

    def remove_txt(self, h):
        self._req("DELETE", "/domains/{}/dns".format(h["domain"]), {"dnsEntry": h["entry"]})

    def test(self):
        return self.domains()


# ------------------------------------------------------------------ testen (Pebble) en demo
class ChallTestSrv:
    """Alleen voor de eigen tests met Pebble/challtestsrv."""
    def __init__(self, url):
        self.url = url.rstrip("/")

    def add_txt(self, name, value):
        requests.post(self.url + "/set-txt", json={"host": _fqdn(name) + ".", "value": value}, timeout=10)
        return name

    def remove_txt(self, h):
        requests.post(self.url + "/clear-txt", json={"host": _fqdn(h) + "."}, timeout=10)

    def test(self):
        return ["challtestsrv"]


def make(settings, demo=False):
    p = (settings or {}).get("provider") or {}
    t = p.get("type")
    if demo or t == "demo":
        return None
    if t == "cloudflare":
        return Cloudflare(p.get("token"), api=p.get("api"))
    if t == "transip":
        return TransIP(p.get("login"), p.get("private_key"), p.get("global_key", True), api=p.get("api"))
    if t == "challtestsrv":
        return ChallTestSrv(p.get("url", "http://127.0.0.1:8055"))
    raise DnsError("Kies eerst een DNS-provider (TransIP of Cloudflare) onder Certificaten › DNS-provider")


# ------------------------------------------------------------------ wachten tot het record zichtbaar is
def _authoritative_ips(name):
    import dns.resolver
    labels = _fqdn(name).split(".")
    for i in range(1, len(labels) - 1):
        zone = ".".join(labels[i:])
        try:
            ns = dns.resolver.resolve(zone, "NS", lifetime=8)
        except Exception:  # noqa: BLE001
            continue
        ips = []
        for rr in ns:
            try:
                ips += [a.to_text() for a in dns.resolver.resolve(rr.target.to_text(), "A", lifetime=8)]
            except Exception:  # noqa: BLE001
                pass
        if ips:
            return ips
    return []


def wait_txt(name, value, log=None, timeout=600, server=None):
    """Wacht tot TXT 'name' de waarde 'value' heeft op alle autoritatieve nameservers.
    server: 'host:port' om een vaste DNS-server te vragen (tests)."""
    import dns.resolver
    log = log or (lambda m: None)
    if server:
        host, _, port = server.partition(":")
        targets = [(host, int(port or 53))]
    else:
        ips = _authoritative_ips(name)
        if not ips:
            log("Kon de nameservers niet vinden; 120 seconden wachten en dan doorgaan.")
            time.sleep(120)
            return
        targets = [(ip, 53) for ip in ips]
    end = time.time() + timeout
    last_log = 0
    while time.time() < end:
        ok = 0
        for host, port in targets:
            r = dns.resolver.Resolver(configure=False)
            r.nameservers, r.port, r.lifetime = [host], port, 6
            try:
                vals = ["".join(p.decode() for p in rr.strings) for rr in r.resolve(name, "TXT")]
                if value in vals:
                    ok += 1
            except Exception:  # noqa: BLE001 - nog niet aanwezig of tijdelijk niet bereikbaar
                pass
        if ok == len(targets):
            log("Zichtbaar op {} nameserver{}.".format(ok, "" if ok == 1 else "s"))
            return
        if time.time() - last_log > 60:
            log("Nog niet overal zichtbaar ({} van {}); even wachten…".format(ok, len(targets)))
            last_log = time.time()
        time.sleep(10)
    raise DnsError("Het DNS-record {} werd niet binnen {} minuten zichtbaar. Probeer het later opnieuw.".format(name, timeout // 60))
