"""Kleine ACME-client (RFC 8555) voor Let's Encrypt met de DNS-01-controle.

Waarom zelf: zo is er geen extra programma (certbot, acme.sh) nodig op de Pi of onder
Windows. De client doet precies wat nodig is: account, bestelling, DNS-01, afronden,
certificaat ophalen. Sleutels worden gemaakt met 'cryptography'.
"""
import base64
import hashlib
import json
import time

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.x509.oid import NameOID

LE_PROD = "https://acme-v02.api.letsencrypt.org/directory"
LE_STAGING = "https://acme-staging-v02.api.letsencrypt.org/directory"


class AcmeError(Exception):
    pass


def b64u(data):
    if isinstance(data, str):
        data = data.encode()
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def new_account_key_pem():
    k = ec.generate_private_key(ec.SECP256R1())
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()


def new_cert_key_pem():
    """RSA 2048: werkt overal (ook in Proxmox en op oudere iPads)."""
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                           serialization.NoEncryption()).decode()


def make_csr(domains, key_pem):
    key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domains[0])]))
           .add_extension(x509.SubjectAlternativeName([x509.DNSName(d) for d in domains]), critical=False)
           .sign(key, hashes.SHA256()))
    return csr.public_bytes(serialization.Encoding.DER)


def _retry_after(v):
    """Retry-After is een aantal seconden of een HTTP-datum."""
    try:
        sec = int(v)
    except (TypeError, ValueError):
        sec = 3
        if v:
            from email.utils import parsedate_to_datetime
            try:
                sec = int(parsedate_to_datetime(v).timestamp() - time.time())
            except (TypeError, ValueError):
                pass
    return min(10, max(2, sec))


class Acme:
    def __init__(self, directory, account_key_pem, kid=None, verify=True, log=None):
        self.directory_url = directory
        self.key = serialization.load_pem_private_key(account_key_pem.encode(), password=None)
        self.kid = kid
        self.s = requests.Session()
        self.verify = verify     # per verzoek meegeven: requests negeert session.verify als REQUESTS_CA_BUNDLE gezet is
        self.s.headers["User-Agent"] = "pve-fleet-acme"
        self.log = log or (lambda m: None)
        self._dir = None
        self._nonces = []

    # ---------------------------------------------------------------- basis
    @property
    def dir(self):
        if self._dir is None:
            r = self.s.get(self.directory_url, timeout=20, verify=self.verify)
            if r.status_code != 200:
                raise AcmeError("ACME-directory niet bereikbaar ({})".format(r.status_code))
            self._dir = r.json()
        return self._dir

    def jwk(self):
        n = self.key.public_key().public_numbers()
        return {"crv": "P-256", "kty": "EC", "x": b64u(n.x.to_bytes(32, "big")), "y": b64u(n.y.to_bytes(32, "big"))}

    def thumbprint(self):
        j = self.jwk()
        data = json.dumps({"crv": j["crv"], "kty": j["kty"], "x": j["x"], "y": j["y"]}, separators=(",", ":"), sort_keys=True)
        return b64u(hashlib.sha256(data.encode()).digest())

    def _nonce(self):
        if self._nonces:
            return self._nonces.pop()
        r = self.s.head(self.dir["newNonce"], timeout=20, verify=self.verify)
        n = r.headers.get("Replay-Nonce")
        if not n:
            raise AcmeError("geen nonce van de ACME-server")
        return n

    def _sign(self, data):
        der = self.key.sign(data, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")

    def post(self, url, payload, use_jwk=False, accept=None):
        """payload None = POST-as-GET (lege payload)."""
        for attempt in range(4):
            protected = {"alg": "ES256", "nonce": self._nonce(), "url": url}
            if use_jwk or not self.kid:
                protected["jwk"] = self.jwk()
            else:
                protected["kid"] = self.kid
            p64 = b64u(json.dumps(protected))
            pl64 = "" if payload is None else b64u(json.dumps(payload))
            body = {"protected": p64, "payload": pl64, "signature": b64u(self._sign("{}.{}".format(p64, pl64).encode()))}
            headers = {"Content-Type": "application/jose+json"}
            if accept:
                headers["Accept"] = accept
            r = self.s.post(url, data=json.dumps(body), headers=headers, timeout=30, verify=self.verify)
            if r.headers.get("Replay-Nonce"):
                self._nonces.append(r.headers["Replay-Nonce"])
            if r.status_code >= 400:
                try:
                    err = r.json()
                except ValueError:
                    err = {"detail": r.text[:200]}
                if err.get("type", "").endswith(":badNonce") and attempt < 3:
                    continue
                if err.get("type", "").endswith(":rateLimited"):
                    raise AcmeError("Let's Encrypt-limiet bereikt: {}".format(err.get("detail")))
                raise AcmeError("{} ({})".format(err.get("detail") or r.reason, err.get("type", r.status_code)))
            return r
        raise AcmeError("te vaak een ongeldige nonce")

    # ---------------------------------------------------------------- stappen
    def register(self, email=None):
        payload = {"termsOfServiceAgreed": True}
        if email:
            payload["contact"] = ["mailto:" + email]
        r = self.post(self.dir["newAccount"], payload, use_jwk=True)
        self.kid = r.headers.get("Location")
        if not self.kid:
            raise AcmeError("geen account-ID van de ACME-server")
        return self.kid

    def poll(self, url, done=("valid",), fail=("invalid",), timeout=300):
        end = time.time() + timeout
        while True:
            r = self.post(url, None)
            obj = r.json()
            if obj.get("status") in done:
                return obj
            if obj.get("status") in fail:
                return obj
            if time.time() > end:
                raise AcmeError("geen antwoord van Let's Encrypt binnen {} s (status {})".format(timeout, obj.get("status")))
            time.sleep(_retry_after(r.headers.get("Retry-After")))

    def issue(self, domains, cert_key_pem, dns, wait_txt):
        """Vraag een certificaat aan. dns: provider met add_txt/remove_txt; wait_txt(naam, waarde) wacht tot DNS klopt.
        Geeft de certificaatketen (PEM) terug."""
        if not self.kid:
            self.register()
        r = self.post(self.dir["newOrder"], {"identifiers": [{"type": "dns", "value": d} for d in domains]})
        order, order_url = r.json(), r.headers.get("Location")
        self.log("Bestelling geplaatst voor {}".format(", ".join(domains)))
        pending, records = [], []
        try:
            for az_url in order["authorizations"]:
                az = self.post(az_url, None).json()
                if az.get("status") == "valid":
                    continue
                ch = next((c for c in az.get("challenges", []) if c["type"] == "dns-01"), None)
                if not ch:
                    raise AcmeError("geen DNS-01-controle aangeboden voor {}".format(az["identifier"]["value"]))
                ident = az["identifier"]["value"]
                value = b64u(hashlib.sha256("{}.{}".format(ch["token"], self.thumbprint()).encode()).digest())
                name = "_acme-challenge." + ident.lstrip("*.")
                self.log("DNS-record zetten: {} TXT {}".format(name, value))
                records.append(dns.add_txt(name, value))
                pending.append((az_url, ch["url"], name, value, ident))
            for _, _, name, value, _ in pending:
                self.log("Wachten tot {} zichtbaar is in DNS…".format(name))
                wait_txt(name, value)
            for az_url, ch_url, _, _, ident in pending:
                self.post(ch_url, {})
                az = self.poll(az_url)
                if az.get("status") != "valid":
                    detail = next((c.get("error", {}).get("detail") for c in az.get("challenges", []) if c.get("error")), None)
                    raise AcmeError("controle voor {} mislukt: {}".format(ident, detail or az.get("status")))
                self.log("Gecontroleerd: {}".format(ident))
        finally:
            for h in records:
                try:
                    dns.remove_txt(h)
                except Exception as e:  # noqa: BLE001 - opruimen mag niet het resultaat breken
                    self.log("Let op: DNS-record opruimen lukte niet: {}".format(e))
        self.post(order["finalize"], {"csr": b64u(make_csr(domains, cert_key_pem))})
        order = self.poll(order_url, done=("valid",), fail=("invalid",))
        if order.get("status") != "valid" or not order.get("certificate"):
            raise AcmeError("afronden mislukt (status {})".format(order.get("status")))
        pem = self.post(order["certificate"], None, accept="application/pem-certificate-chain").text
        self.log("Certificaat ontvangen")
        return pem


# ---------------------------------------------------------------- certificaten lezen
def cert_info(pem):
    """Samenvatting van het eerste certificaat in een PEM-keten."""
    certs = x509.load_pem_x509_certificates(pem.encode()) if hasattr(x509, "load_pem_x509_certificates") else [x509.load_pem_x509_certificate(pem.encode())]
    c = certs[0]
    try:
        sans = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        sans = []

    def name(n):
        for oid in (NameOID.COMMON_NAME, NameOID.ORGANIZATION_NAME):
            a = n.get_attributes_for_oid(oid)
            if a:
                return a[0].value
        return n.rfc4514_string()
    nb = getattr(c, "not_valid_before_utc", None) or c.not_valid_before
    na = getattr(c, "not_valid_after_utc", None) or c.not_valid_after
    return {"subject": name(c.subject), "issuer": name(c.issuer), "sans": sans, "chain": len(certs),
            "not_before": int(nb.timestamp()) if nb.tzinfo else int(time.mktime(nb.timetuple())),
            "not_after": int(na.timestamp()) if na.tzinfo else int(time.mktime(na.timetuple())),
            "fingerprint": c.fingerprint(hashes.SHA256()).hex(":").upper()[:47] + "…"}


def check_pair(cert_pem, key_pem):
    """Controleer dat de sleutel bij het certificaat hoort. Gooit ValueError met een duidelijke melding."""
    try:
        certs = x509.load_pem_x509_certificates(cert_pem.encode())
    except ValueError:
        raise ValueError("Het certificaat is geen geldig PEM-bestand (verwacht -----BEGIN CERTIFICATE-----)")
    try:
        key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    except (ValueError, TypeError):
        raise ValueError("De sleutel is geen geldige, onversleutelde PEM-sleutel (verwacht -----BEGIN PRIVATE KEY----- of RSA/EC PRIVATE KEY)")
    pub = lambda k: k.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)  # noqa: E731
    if pub(certs[0].public_key()) != pub(key.public_key()):
        raise ValueError("De sleutel hoort niet bij dit certificaat")
    return certs
