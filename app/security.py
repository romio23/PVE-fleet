"""Security headers and protection against cross-site requests.

* Content-Security-Policy with a nonce for the few inline scripts: scripts only run from this server,
  nothing can frame the pages (except this site itself), no plug-ins, forms only post back to this site.
* Requests that change something (POST, PUT, PATCH, DELETE) are refused when the browser says they come from
  another site (Sec-Fetch-Site: cross-site / same-site). Scripts and curl do not send this header and are not affected.
  This comes on top of the X-PVEF header the API already requires.
* When served over https (own certificate or a reverse proxy with X-Forwarded-Proto) the cookies get the Secure
  flag and Strict-Transport-Security is sent. Over plain http on a LAN nothing changes.
"""
import secrets

from flask import g, jsonify, request
from flask.sessions import SecureCookieSessionInterface

SAFE = ("GET", "HEAD", "OPTIONS")


def is_https():
    return request.is_secure or request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower() == "https"


def nonce():
    if not g.get("csp_nonce"):
        g.csp_nonce = secrets.token_urlsafe(16)
    return g.csp_nonce


def policy(n=None):
    script = "'self'" + (" 'nonce-%s'" % n if n else "")
    return "; ".join([
        "default-src 'self'",
        "script-src " + script,
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",   # style attributes in the pages and scripts; Google Fonts stylesheet
        "img-src 'self' data: blob:",
        "connect-src 'self' ws: wss:",          # the built-in console talks over a websocket to this server
        "font-src 'self' data: https://fonts.gstatic.com",
        "worker-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'self'",
    ])


class _Sessions(SecureCookieSessionInterface):
    def get_cookie_secure(self, app):
        try:
            return is_https()
        except RuntimeError:
            return False


def install(app):
    app.session_interface = _Sessions()

    @app.context_processor
    def _nonce():
        return {"csp_nonce": nonce()}

    @app.before_request
    def _cross_site():
        if request.method in SAFE:
            return None
        if request.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
            return jsonify({"error": "request from another site refused"}), 403
        return None

    @app.after_request
    def _headers(resp):
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault("X-Frame-Options", "SAMEORIGIN")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        if resp.mimetype == "text/html":
            h.setdefault("Content-Security-Policy", policy(g.get("csp_nonce")))
        else:
            h.setdefault("Content-Security-Policy", "default-src 'none'; sandbox")
        if is_https():
            h.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp
