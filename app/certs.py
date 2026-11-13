"""Certificaten: https voor PVE Fleet zelf en certificaten voor de Proxmox-nodes.

- Let's Encrypt via de DNS-01-controle (TransIP of Cloudflare): er hoeft niets open naar internet.
- Of een eigen certificaat inlezen (van een andere provider), als PEM met sleutel.
- PVE Fleet vernieuwt Let's Encrypt-certificaten zelf, 30 dagen voor ze verlopen.

Bestanden in data/tls/ (map alleen voor PVE Fleet leesbaar):
  settings.json   provider, e-mail, instellingen (bevat de API-sleutel van de DNS-provider)
  account.key     ACME-account
  fleet.crt/.key  certificaat van PVE Fleet zelf
  nodes/<server>__<node>.crt/.key  laatst aangevraagde node-certificaten
"""
import json
import logging
import os
import re
import secrets
import threading
import time

from flask import Blueprint, abort, current_app, g, jsonify, render_template, request

from . import acme as acme_mod
from . import dnsprov
from .pve import PveError, PvePermissionError, q

log = logging.getLogger(__name__)
bp = Blueprint("certs", __name__)
RENEW_DAYS = 30
DOMAIN_RE = re.compile(r"^(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


# ------------------------------------------------------------------ opslag
class TlsStore:
    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, "tls")
        os.makedirs(os.path.join(self.dir, "nodes"), exist_ok=True)
        for d in (self.dir, os.path.join(self.dir, "nodes")):
            try:
                os.chmod(d, 0o700)
            except OSError:
                pass
        self.path = os.path.join(self.dir, "settings.json")
        self.lock = threading.RLock()
        self.data = {"email": "", "staging": False, "provider": {}, "accounts": {},
                     "fleet": {"https_port": 8443, "redirect": False, "domains": [], "source": None, "auto_renew": True},
                     "nodes": {}}
        try:
            with open(self.path, encoding="utf-8") as fh:
                d = json.load(fh)
            for k, v in d.items():
                if isinstance(v, dict) and isinstance(self.data.get(k), dict):
                    self.data[k].update(v)
                else:
                    self.data[k] = v
        except (OSError, ValueError):
            pass
        self.jobs = {}

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=1)
            os.replace(tmp, self.path)

    def file(self, name):
        return os.path.join(self.dir, name)

    def write(self, name, text):
        path = self.file(name)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)

    def read(self, name):
        try:
            with open(self.file(name), encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return None

    # ---------- PVE Fleet-certificaat
    def fleet_paths(self):
        return self.file("fleet.crt"), self.file("fleet.key")

    def fleet_info(self):
        pem = self.read("fleet.crt")
        if not pem:
            return None
        try:
            return acme_mod.cert_info(pem)
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}


STORE = None


def _node_key(server, node):
    return "{}__{}".format(re.sub(r"[^A-Za-z0-9_.-]", "_", server), re.sub(r"[^A-Za-z0-9_.-]", "_", node))


# ------------------------------------------------------------------ taken op de achtergrond
class Job:
    def __init__(self, title):
        self.id = secrets.token_hex(6)
        self.title = title
        self.lines = []
        self.status = "running"
        self.error = None
        self.started = time.time()

    def log(self, msg):
        self.lines.append("{}  {}".format(time.strftime("%H:%M:%S"), msg))
        log.info("cert: %s", msg)

    def public(self):
        return {"id": self.id, "title": self.title, "status": self.status, "error": self.error, "log": self.lines[-200:]}


def _start_job(app, title, fn):
    store = app.extensions["certs"]
    job = Job(title)
    store.jobs[job.id] = job
    for k in [k for k, j in store.jobs.items() if time.time() - j.started > 3600 and j.status != "running"]:
        store.jobs.pop(k, None)

    def run():
        with app.app_context():
            try:
                fn(job)
                job.status = "ok"
                job.log("Klaar.")
            except Exception as e:  # noqa: BLE001
                job.status = "fail"
                job.error = str(e)
                job.log("Mislukt: {}".format(e))
                log.warning("cert-taak '%s' mislukt: %s", title, e)
    threading.Thread(target=run, daemon=True).start()
    return job


# ------------------------------------------------------------------ Let's Encrypt
def _demo(app):
    return bool(app.extensions["collector"].cfg.get("demo"))


def _issue(app, domains, job):
    """Vraag een certificaat aan voor 'domains'. Geeft (cert_pem, key_pem)."""
    store = app.extensions["certs"]
    st = store.data
    key_pem = acme_mod.new_cert_key_pem()
    if _demo(app) or (st.get("provider") or {}).get("type") == "demo":
        job.log("Demo: er wordt geen echte aanvraag gedaan; PVE Fleet maakt een demo-certificaat.")
        time.sleep(2)
        return _demo_cert(domains, key_pem), key_pem
    dns = dnsprov.make(st)
    directory = st.get("directory") or (acme_mod.LE_STAGING if st.get("staging") else acme_mod.LE_PROD)
    verify = st.get("acme_verify", True)
    acc_pem = store.read("account.key")
    if not acc_pem:
        acc_pem = acme_mod.new_account_key_pem()
        store.write("account.key", acc_pem)
    kid = (st.get("accounts") or {}).get(directory)
    client = acme_mod.Acme(directory, acc_pem, kid=kid, verify=verify, log=job.log)
    if not kid:
        job.log("Account aanmaken bij {}".format("Let's Encrypt (test)" if st.get("staging") else "Let's Encrypt"))
        kid = client.register(st.get("email") or None)
        with store.lock:
            st.setdefault("accounts", {})[directory] = kid
            store.save()
    check_server = (st.get("provider") or {}).get("dns_server")
    pem = client.issue(domains, key_pem, dns,
                       lambda n, v: dnsprov.wait_txt(n, v, log=job.log, server=check_server))
    return pem, key_pem


def _demo_cert(domains, key_pem):
    from datetime import datetime, timedelta, timezone
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509.oid import NameOID
    key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domains[0])])
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PVE Fleet demo (geen echt certificaat)")])
    now = datetime.now(timezone.utc)
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(issuer).public_key(key.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
         .not_valid_after(now + timedelta(days=90))
         .add_extension(x509.SubjectAlternativeName([x509.DNSName(d) for d in domains]), critical=False)
         .sign(key, hashes.SHA256()))
    return c.public_bytes(serialization.Encoding.PEM).decode()


def _check_domains(domains):
    out = []
    for d in domains:
        d = str(d).strip().lower().rstrip(".")
        if not d:
            continue
        if not DOMAIN_RE.match(d):
            raise ValueError("'{}' is geen geldige domeinnaam (bijvoorbeeld fleet.romioinc.nl)".format(d))
        if d not in out:
            out.append(d)
    if not out:
        raise ValueError("Vul minstens één domeinnaam in")
    return out


# ------------------------------------------------------------------ PVE Fleet zelf
def install_fleet(store, cert_pem, key_pem, source, domains=None):
    acme_mod.check_pair(cert_pem, key_pem)
    store.write("fleet.key", key_pem)
    store.write("fleet.crt", cert_pem)
    with store.lock:
        f = store.data["fleet"]
        f["source"] = source
        info = acme_mod.cert_info(cert_pem)
        f["domains"] = domains or info["sans"] or f.get("domains") or []
        f["installed"] = int(time.time())
        store.save()
    from . import tlsserver
    tlsserver.reload()


# ------------------------------------------------------------------ Proxmox-nodes
def node_cert_info(c, node):
    """Huidig certificaat van de webinterface (pveproxy) van een node."""
    items = c.get("/nodes/{}/certificates/info".format(q(node))) or []
    custom = next((i for i in items if i.get("filename") == "pveproxy-ssl.pem"), None)
    cur = custom or next((i for i in items if i.get("filename") == "pve-ssl.pem"), None)
    if not cur:
        return None
    san = cur.get("san") or []
    return {"custom": bool(custom), "subject": cur.get("subject"), "issuer": cur.get("issuer"),
            "not_after": cur.get("notafter"), "not_before": cur.get("notbefore"), "sans": san,
            "self_signed": not custom}


def upload_node(app, server, node, cert_pem, key_pem, job=None):
    """Zet het certificaat op de node (pveproxy herstart vanzelf). Recht: Sys.Modify op /nodes/<node>."""
    acme_mod.check_pair(cert_pem, key_pem)
    col = app.extensions["collector"]
    c = col.client(server)
    data = {"certificates": cert_pem, "key": key_pem, "force": 1, "restart": 1}
    path = "/nodes/{}/certificates/custom".format(q(node))
    try:
        c.post(path, data)
    except PvePermissionError:
        from . import rootsession
        rs = rootsession.get(server, c)
        if not rs:
            raise PveError("Het API-token mag geen certificaten op de node zetten (recht Sys.Modify). Voeg het optionele recht toe "
                           "(Servers & verbinding › Verbinding testen › Optioneel), of open eerst een shell met de root-sessie en probeer opnieuw.")
        if job:
            job.log("Het token mist Sys.Modify; via de root-sessie.")
        rootsession.post(c, rs, path, data)
    if job:
        job.log("Certificaat geplaatst op {}; de webinterface van de node herstart (een paar seconden).".format(node))


def _node_issue(app, server, node, domain, job):
    store = app.extensions["certs"]
    domains = _check_domains([domain])
    cert, key = _issue(app, domains, job)
    store.write("nodes/{}.crt".format(_node_key(server, node)), cert)
    store.write("nodes/{}.key".format(_node_key(server, node)), key)
    upload_node(app, server, node, cert, key, job)
    with store.lock:
        n = store.data["nodes"].setdefault("{}/{}".format(server, node), {})
        n.update({"domain": domains[0], "source": "letsencrypt", "installed": int(time.time()),
                  "not_after": acme_mod.cert_info(cert)["not_after"], "auto_renew": n.get("auto_renew", True)})
        store.save()


# ------------------------------------------------------------------ automatisch vernieuwen
def renew_due(app):
    """Start vernieuwtaken voor Let's Encrypt-certificaten die binnen RENEW_DAYS verlopen. Geeft de gestarte taken."""
    store = app.extensions["certs"]
    started = []
    if any(j.status == "running" for j in store.jobs.values()):
        return started
    f = store.data["fleet"]
    info = store.fleet_info()
    if f.get("source") == "letsencrypt" and f.get("auto_renew", True) and info and info.get("not_after") \
            and info["not_after"] - time.time() < RENEW_DAYS * 86400 and f.get("domains"):
        def renew_fleet(job, domains=list(f["domains"])):
            cert, key = _issue(app, domains, job)
            install_fleet(store, cert, key, "letsencrypt", domains)
        started.append(_start_job(app, "PVE Fleet vernieuwen", renew_fleet))
    for k, n in list(store.data["nodes"].items()):
        if n.get("source") != "letsencrypt" or not n.get("auto_renew", True) or not n.get("not_after"):
            continue
        if n["not_after"] - time.time() < RENEW_DAYS * 86400:
            server, node = k.split("/", 1)
            if app.extensions["collector"].client(server):
                started.append(_start_job(app, "{} vernieuwen".format(node),
                                          lambda job, s=server, nd=node, d=n["domain"]: _node_issue(app, s, nd, d, job)))
    return started


def _renew_loop(app):
    time.sleep(90)
    while True:
        try:
            with app.app_context():
                renew_due(app)
        except Exception:  # pragma: no cover
            log.exception("vernieuwen")
        time.sleep(12 * 3600)


def init_app(app):
    global STORE
    store = TlsStore(app.config["PVEF_DATA"])
    app.extensions["certs"] = store
    STORE = store
    app.register_blueprint(bp)
    threading.Thread(target=_renew_loop, args=(app,), daemon=True).start()

    def _https_redirect():
        from . import tlsserver
        f = store.data["fleet"]
        if not f.get("redirect") or request.is_secure or not tlsserver.running():
            return None
        if request.path in ("/healthz",) or request.headers.get("X-Forwarded-Proto") == "https":
            return None
        # Een browser op de Pi zelf (kiosk op localhost) niet doorsturen: op de domeinnaam
        # zou hij niet meer als 'deze computer' gelden en om koppelen vragen.
        hostname = request.host.rsplit(":", 1)[0] if not request.host.endswith("]") else request.host
        if hostname in ("localhost", "127.0.0.1", "[::1]") and request.remote_addr in ("127.0.0.1", "::1"):
            return None
        doms = [d for d in (f.get("domains") or []) if not d.startswith("*.")]
        host = doms[0] if doms else (request.host.rsplit(":", 1)[0] if not request.host.endswith("]") else request.host)
        port = int(f.get("https_port") or 8443)
        from flask import redirect
        return redirect("https://{}{}{}".format(host, "" if port == 443 else ":" + str(port), request.full_path.rstrip("?")), 307)
    # vóór de inlogcontrole, zodat je meteen op https inlogt
    app.before_request_funcs.setdefault(None, []).insert(0, _https_redirect)


# ------------------------------------------------------------------ API
def _store():
    return current_app.extensions["certs"]


def _admin():
    if g.get("scope") != "admin":
        abort(403)


def _mask(p):
    out = {k: v for k, v in (p or {}).items() if k not in ("token", "private_key")}
    out["has_token"] = bool((p or {}).get("token"))
    out["has_key"] = bool((p or {}).get("private_key"))
    return out


@bp.get("/certificates")
def certs_page():
    _admin()
    return render_template("certs.html", page="certs")


@bp.get("/api/certs")
def api_certs():
    _admin()
    from . import tlsserver
    store = _store()
    col = current_app.extensions["collector"]
    st = store.data
    nodes = []
    for s in col.cfg["servers"]:
        grp = col.group(s["name"])
        c = col.client(s["name"])
        if not grp:
            continue
        for n in grp["nodes"]:
            key = "{}/{}".format(s["name"], n["name"])
            info, err = None, None
            if n["status"] == "online" and grp["reachable"]:
                try:
                    info = node_cert_info(c, n["name"])
                except PveError as e:
                    err = str(e)
            nodes.append({"server": s["name"], "group": grp["name"], "node": n["name"], "status": n["status"], "ip": n.get("ip"),
                          "cert": info, "error": err, "settings": st["nodes"].get(key) or {}})
    f = st["fleet"]
    return jsonify({"email": st.get("email"), "staging": bool(st.get("staging")), "provider": _mask(st.get("provider")),
                    "base_domain": st.get("base_domain") or "",
                    "fleet": {"info": store.fleet_info(), "source": f.get("source"), "domains": f.get("domains") or [],
                              "https_port": f.get("https_port", 8443), "redirect": bool(f.get("redirect")),
                              "auto_renew": f.get("auto_renew", True), "running": tlsserver.running(),
                              "listen_error": tlsserver.error()},
                    "nodes": nodes, "demo": _demo(current_app), "secure": request.is_secure,
                    "jobs": [j.public() for j in sorted(store.jobs.values(), key=lambda j: -j.started)][:5]})


@bp.post("/api/certs/settings")
def api_settings():
    _admin()
    store = _store()
    b = request.get_json(silent=True) or {}
    with store.lock:
        st = store.data
        if "email" in b:
            email = str(b.get("email") or "").strip()
            if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
                return jsonify({"error": "Ongeldig e-mailadres"}), 400
            st["email"] = email
        if "staging" in b:
            st["staging"] = bool(b["staging"])
        if "base_domain" in b:
            bd = str(b.get("base_domain") or "").strip().lower().strip(".")
            if bd and not DOMAIN_RE.match(bd):
                return jsonify({"error": "Ongeldig basisdomein"}), 400
            st["base_domain"] = bd
        if "provider" in b:
            p = b["provider"] or {}
            t = p.get("type")
            if t not in ("transip", "cloudflare", "demo", ""):
                return jsonify({"error": "Onbekende provider"}), 400
            old = st.get("provider") or {}
            new = {"type": t}
            if t == "cloudflare":
                new["token"] = (p.get("token") or "").strip() or (old.get("token") if old.get("type") == "cloudflare" else "")
            if t == "transip":
                new["login"] = (p.get("login") or "").strip()
                new["private_key"] = (p.get("private_key") or "").strip() or (old.get("private_key") if old.get("type") == "transip" else "")
                new["global_key"] = bool(p.get("global_key", True))
            st["provider"] = new
        store.save()
    return jsonify({"ok": True})


@bp.post("/api/certs/provider-test")
def api_provider_test():
    _admin()
    if _demo(current_app):
        return jsonify({"ok": True, "zones": ["voorbeeld.nl (demo)"]})
    try:
        prov = dnsprov.make(_store().data)
        zones = prov.test()
    except dnsprov.DnsError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "zones": zones})


@bp.get("/api/certs/jobs/<jid>")
def api_job(jid):
    _admin()
    j = _store().jobs.get(jid)
    if not j:
        return jsonify({"error": "onbekende taak"}), 404
    return jsonify(j.public())


@bp.post("/api/certs/fleet/issue")
def api_fleet_issue():
    _admin()
    b = request.get_json(silent=True) or {}
    try:
        domains = _check_domains(re.split(r"[,\s]+", str(b.get("domains") or "")))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    app = current_app._get_current_object()
    store = _store()

    def work(job):
        cert, key = _issue(app, domains, job)
        install_fleet(store, cert, key, "letsencrypt", domains)
        job.log("Geïnstalleerd voor PVE Fleet.")
    from .actions import audit
    audit({"who": g.who, "action": "cert", "ok": True, "label": "Certificaat aangevraagd voor PVE Fleet: " + ", ".join(domains)})
    return jsonify({"ok": True, "job": _start_job(app, "Let's Encrypt voor PVE Fleet", work).id})


@bp.post("/api/certs/fleet/upload")
def api_fleet_upload():
    _admin()
    b = request.get_json(silent=True) or {}
    cert = (str(b.get("cert") or "").strip() + "\n" + str(b.get("chain") or "").strip()).strip() + "\n"
    key = str(b.get("key") or "").strip() + "\n"
    try:
        install_fleet(_store(), cert, key, "upload")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    from .actions import audit
    audit({"who": g.who, "action": "cert", "ok": True, "label": "Eigen certificaat ingelezen voor PVE Fleet"})
    return jsonify({"ok": True})


@bp.post("/api/certs/fleet/settings")
def api_fleet_settings():
    _admin()
    b = request.get_json(silent=True) or {}
    store = _store()
    with store.lock:
        f = store.data["fleet"]
        if "https_port" in b:
            try:
                port = int(b["https_port"])
            except (TypeError, ValueError):
                return jsonify({"error": "Ongeldige poort"}), 400
            if not (1 <= port <= 65535) or port == int(os.environ.get("PORT", 8080)):
                return jsonify({"error": "Kies een vrije poort, bijvoorbeeld 8443"}), 400
            f["https_port"] = port
        if "redirect" in b:
            f["redirect"] = bool(b["redirect"])
        if "auto_renew" in b:
            f["auto_renew"] = bool(b["auto_renew"])
        store.save()
    from . import tlsserver
    tlsserver.reload()
    return jsonify({"ok": True})


@bp.delete("/api/certs/fleet")
def api_fleet_delete():
    _admin()
    store = _store()
    for n in ("fleet.crt", "fleet.key"):
        try:
            os.remove(store.file(n))
        except OSError:
            pass
    with store.lock:
        store.data["fleet"].update({"source": None, "redirect": False})
        store.save()
    from . import tlsserver
    tlsserver.reload()
    return jsonify({"ok": True})


@bp.post("/api/certs/node/issue")
def api_node_issue():
    _admin()
    b = request.get_json(silent=True) or {}
    server, node, domain = str(b.get("server", "")), str(b.get("node", "")), str(b.get("domain", "")).strip().lower()
    col = current_app.extensions["collector"]
    grp = col.group(server)
    if not grp or not any(n["name"] == node and n["status"] == "online" for n in grp["nodes"]):
        return jsonify({"error": "Node {} is niet bereikbaar".format(node)}), 409
    try:
        _check_domains([domain])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    app = current_app._get_current_object()
    store = _store()
    with store.lock:
        store.data["nodes"].setdefault("{}/{}".format(server, node), {})["domain"] = domain
        store.save()
    from .actions import audit
    audit({"who": g.who, "action": "cert", "server": server, "node": node, "ok": True,
           "label": "Certificaat aangevraagd voor {} ({})".format(node, domain)})
    job = _start_job(app, "Let's Encrypt voor {}".format(node), lambda j: _node_issue(app, server, node, domain, j))
    return jsonify({"ok": True, "job": job.id})


@bp.post("/api/certs/node/upload")
def api_node_upload():
    _admin()
    b = request.get_json(silent=True) or {}
    server, node = str(b.get("server", "")), str(b.get("node", ""))
    cert = (str(b.get("cert") or "").strip() + "\n" + str(b.get("chain") or "").strip()).strip() + "\n"
    key = str(b.get("key") or "").strip() + "\n"
    try:
        upload_node(current_app._get_current_object(), server, node, cert, key)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except PveError as e:
        return jsonify({"error": str(e)}), 502
    store = _store()
    with store.lock:
        n = store.data["nodes"].setdefault("{}/{}".format(server, node), {})
        n.update({"source": "upload", "installed": int(time.time()), "not_after": acme_mod.cert_info(cert)["not_after"]})
        store.save()
    from .actions import audit
    audit({"who": g.who, "action": "cert", "server": server, "node": node, "ok": True, "label": "Eigen certificaat geplaatst op " + node})
    return jsonify({"ok": True})


@bp.post("/api/certs/node/settings")
def api_node_settings():
    _admin()
    b = request.get_json(silent=True) or {}
    key = "{}/{}".format(b.get("server", ""), b.get("node", ""))
    store = _store()
    with store.lock:
        n = store.data["nodes"].setdefault(key, {})
        if "auto_renew" in b:
            n["auto_renew"] = bool(b["auto_renew"])
        if "domain" in b:
            n["domain"] = str(b["domain"] or "").strip().lower()
        store.save()
    return jsonify({"ok": True})
