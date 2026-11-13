"""Inloggen, eerste-keer-setup, kiosk koppelen en de instellingen-API.

Er zijn twee soorten toegang:
- 'admin': ingelogd met gebruikersnaam en wachtwoord; volledig dashboard en alle acties.
- 'kiosk': een gekoppeld scherm (cookie), of een browser op deze computer zelf als
  'localhost_auto' aan staat. Ziet alleen de kioskweergave; acties alleen via de
  kiosk-API, eventueel met pincode.
"""
import logging
import re
import secrets
import time
from datetime import timedelta
from urllib.parse import urlparse

from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request, session

from .settings import PIN_ACTION_KEYS, Limiter

log = logging.getLogger(__name__)
bp = Blueprint("auth", __name__)

KIOSK_COOKIE = "pvef_kiosk"
PIN_RE = re.compile(r"^\d{4,6}$")
login_limiter = Limiter(5)
pair_limiter = Limiter(5)
PAIR_CODES = {}   # code -> (naam, verloopt)
PAIR_TOKENS = {}  # eenmalige sleutel uit de QR-code -> code


def store():
    return current_app.extensions["settings"]


def collector():
    return current_app.extensions["collector"]


def _ip():
    return request.remote_addr or "?"


def _is_local():
    return _ip() in ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def _safe_next(target):
    if not target:
        return "/"
    p = urlparse(target)
    if p.scheme or p.netloc or not target.startswith("/") or target.startswith("//"):
        return "/"
    return target


def resolve_identity():
    """Bepaal wie er kijkt: g.scope ('admin' / 'kiosk' / None), g.who, g.client_key."""
    st = store()
    g.scope, g.who, g.client_key, g.device = None, None, None, None
    if session.get("user") and session.get("user") == st.admin_username() and session.get("stamp") == st.admin_stamp():
        if "kid" not in session:
            session["kid"] = secrets.token_hex(6)
        g.scope, g.who, g.client_key = "admin", session["user"], "admin:" + session["kid"]
        return
    dev = st.device_from_cookie(request.cookies.get(KIOSK_COOKIE))
    if dev:
        st.touch_device(dev, _ip())
        g.scope, g.who, g.client_key, g.device = "kiosk", "kiosk: " + dev["name"], "dev:" + dev["id"], dev
        return
    if _is_local() and st.kiosk.get("localhost_auto"):
        g.scope, g.who, g.client_key = "kiosk", "kiosk: deze computer", "local"


def install(app):
    @app.before_request
    def _gate():
        path = request.path
        if path.startswith("/static/") or path == "/healthz":
            return None
        # Schrijfverzoeken naar de API moeten deze header hebben (bescherming tegen CSRF).
        if path.startswith("/api/") and request.method != "GET" and request.headers.get("X-PVEF") != "1":
            return jsonify({"error": "verzoek geweigerd"}), 400
        st = store()
        if not st.has_admin():
            if path == "/setup":
                return None
            if path.startswith("/api/") or path.startswith("/kiosk/"):
                return jsonify({"error": "setup", "message": "Het dashboard is nog niet ingesteld"}), 401
            return redirect("/setup")
        resolve_identity()
        if path in ("/login", "/logout", "/kiosk", "/kiosk/pair", "/setup"):
            return None
        if path == "/api/fleet" or path.startswith("/api/kiosk/"):
            if g.scope in ("admin", "kiosk"):
                return None
            return jsonify({"error": "unpaired", "message": "Dit scherm is niet gekoppeld"}), 401
        if g.scope == "admin":
            return None
        if path.startswith("/api/"):
            return jsonify({"error": "login", "message": "Log opnieuw in"}), 401
        return redirect("/login?next=" + path)

    app.register_blueprint(bp)


# ------------------------------------------------------------------ pagina's
@bp.route("/setup", methods=["GET", "POST"])
def setup():
    st = store()
    if st.has_admin():
        return redirect("/")
    error, form = None, {"username": "admin"}
    if request.method == "POST":
        form = request.form
        user = (form.get("username") or "").strip()
        pw, pw2 = form.get("password") or "", form.get("password2") or ""
        pin, pin2 = form.get("pin") or "", form.get("pin2") or ""
        if not re.match(r"^[A-Za-z0-9._-]{2,32}$", user):
            error = "Gebruikersnaam: 2 tot 32 tekens, alleen letters, cijfers, punt, streepje of underscore."
        elif len(pw) < 8:
            error = "Kies een wachtwoord van minstens 8 tekens."
        elif pw != pw2:
            error = "De wachtwoorden zijn niet gelijk."
        elif not PIN_RE.match(pin):
            error = "De pincode moet uit 4 tot 6 cijfers bestaan."
        elif pin != pin2:
            error = "De pincodes zijn niet gelijk."
        else:
            st.set_admin(user, pw)
            st.set_pin(pin)
            session.clear()
            session.permanent = True
            session["user"], session["stamp"] = user, st.admin_stamp()
            log.info("beheerder '%s' aangemaakt", user)
            return redirect("/")
    return render_template("setup.html", error=error, form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    st = store()
    nxt = _safe_next(request.values.get("next"))
    if g.get("scope") == "admin":
        return redirect(nxt)
    error = None
    if request.method == "POST":
        wait = login_limiter.locked_for(_ip())
        if wait:
            error = "Te veel mislukte pogingen. Probeer het over {} seconden opnieuw.".format(int(wait) + 1)
        elif st.check_admin(request.form.get("username", ""), request.form.get("password", "")):
            login_limiter.reset(_ip())
            session.clear()
            session.permanent = bool(request.form.get("remember"))
            session["user"], session["stamp"] = st.admin_username(), st.admin_stamp()
            return redirect(nxt)
        else:
            left, locked = login_limiter.fail(_ip(), 60)
            log.warning("mislukte login vanaf %s", _ip())
            error = "Onjuiste gebruikersnaam of wachtwoord." + (" Even geblokkeerd (1 minuut)." if locked else "")
    return render_template("login.html", error=error, next=nxt)


@bp.route("/logout")
def logout():
    session.clear()
    from . import rootsession
    rootsession.end()          # uitloggen beëindigt ook alle root-sessies van de console
    return redirect("/login")


@bp.post("/kiosk/pair")
def kiosk_pair():
    if request.headers.get("X-PVEF") != "1":
        return jsonify({"error": "verzoek geweigerd"}), 400
    ip = _ip()
    wait = pair_limiter.locked_for(ip)
    if wait:
        return jsonify({"error": "Te veel pogingen. Probeer het over {} s opnieuw.".format(int(wait) + 1)}), 429
    body = request.get_json(silent=True) or {}
    code = re.sub(r"\D", "", str(body.get("code", "")))
    token = str(body.get("token", ""))[:64]
    if token:
        code = PAIR_TOKENS.pop(token, "") or ""
    now = time.time()
    for k in [k for k, v in PAIR_CODES.items() if v[1] < now]:
        PAIR_CODES.pop(k, None)
    entry = PAIR_CODES.pop(code, None) if code else None
    if not entry:
        left, locked = pair_limiter.fail(ip, 300)
        return jsonify({"error": "Onbekende of verlopen koppelcode." + (" Even geblokkeerd." if locked else "")}), 403
    pair_limiter.reset(ip)
    dev_id, cookie = store().add_device(entry[0], ip)
    log.info("scherm '%s' gekoppeld vanaf %s", entry[0], ip)
    resp = jsonify({"ok": True, "name": entry[0]})
    resp.set_cookie(KIOSK_COOKIE, cookie, max_age=10 * 365 * 86400, httponly=True, samesite="Lax")
    return resp


@bp.get("/settings")
def settings_page():
    return render_template("settings.html", page="settings")


# ------------------------------------------------------------------ instellingen-API (alleen admin)
@bp.get("/api/settings")
def api_settings():
    st = store()
    from .actions import pin_limiter
    return jsonify({"username": st.admin_username(), "kiosk": st.public_kiosk(), "devices": st.public_devices(),
                    "pin_actions_all": PIN_ACTION_KEYS, "locked": len(pin_limiter.locked_keys())})


@bp.post("/api/settings/kiosk")
def api_settings_kiosk():
    try:
        store().update_kiosk(request.get_json(silent=True) or {})
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "kiosk": store().public_kiosk()})


@bp.post("/api/settings/pin")
def api_settings_pin():
    body = request.get_json(silent=True) or {}
    pin = str(body.get("pin", ""))
    if not PIN_RE.match(pin):
        return jsonify({"error": "De pincode moet uit 4 tot 6 cijfers bestaan."}), 400
    if pin != str(body.get("pin2", "")):
        return jsonify({"error": "De pincodes zijn niet gelijk."}), 400
    store().set_pin(pin)
    return jsonify({"ok": True})


@bp.post("/api/settings/password")
def api_settings_password():
    st = store()
    body = request.get_json(silent=True) or {}
    if not st.check_admin(st.admin_username(), str(body.get("current", ""))):
        return jsonify({"error": "Het huidige wachtwoord klopt niet."}), 400
    new = str(body.get("new", ""))
    if len(new) < 8:
        return jsonify({"error": "Kies een wachtwoord van minstens 8 tekens."}), 400
    if new != str(body.get("new2", "")):
        return jsonify({"error": "De nieuwe wachtwoorden zijn niet gelijk."}), 400
    st.set_admin(st.admin_username(), new)
    session["stamp"] = st.admin_stamp()
    return jsonify({"ok": True})


@bp.post("/api/settings/pair-code")
def api_pair_code():
    name = str((request.get_json(silent=True) or {}).get("name", "")).strip()[:40] or "Scherm"
    code = "{:06d}".format(secrets.randbelow(1000000))
    PAIR_CODES[code] = (name, time.time() + 600)
    # QR-code: een lange, eenmalige sleutel in de link (niet te raden), gekoppeld aan dezelfde code
    token = secrets.token_urlsafe(18)
    PAIR_TOKENS[token] = code
    base, warn = _public_base()
    return jsonify({"code": code, "expires_in": 600, "name": name, "base": base, "host_warning": warn,
                    "qr_url": "{}/kiosk?pair={}".format(base, token)})


def _public_base():
    """Adres waarop andere apparaten het dashboard bereiken. Op de Pi zelf (localhost) het netwerkadres."""
    host = request.host
    hostname = host.rsplit(":", 1)[0] if not host.startswith("[") else host
    port = host.rsplit(":", 1)[1] if ":" in host and not host.endswith("]") else None
    if hostname in ("localhost", "127.0.0.1", "[::1]"):
        import socket
        ip = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("192.0.2.1", 9))          # er wordt niets verstuurd; alleen de route bepalen
            ip = s.getsockname()[0]
            s.close()
        except OSError:
            pass
        if ip and not ip.startswith("127."):
            return "http://{}{}".format(ip, ":" + port if port else ""), None
        return "http://" + host, ("Je opent het dashboard via localhost; een ander apparaat kan dit adres niet gebruiken. "
                                  "Open het dashboard via het IP-adres van de Pi en maak de code opnieuw.")
    return "{}://{}".format(request.scheme, host), None


@bp.post("/api/settings/devices/<dev_id>")
def api_device_rename(dev_id):
    name = str((request.get_json(silent=True) or {}).get("name", "")).strip()
    if not name:
        return jsonify({"error": "Naam is leeg"}), 400
    return jsonify({"ok": store().rename_device(dev_id, name)})


@bp.delete("/api/settings/devices/<dev_id>")
def api_device_remove(dev_id):
    return jsonify({"ok": store().remove_device(dev_id)})


@bp.post("/api/settings/unblock")
def api_unblock():
    from .actions import pin_limiter
    n = pin_limiter.clear()
    for aid in [a for a in list(collector().extra_alerts) if a.startswith("kiosk-lock:")]:
        collector().clear_extra_alert(aid)
    return jsonify({"ok": True, "released": n})


def session_lifetime():
    return timedelta(days=30)
