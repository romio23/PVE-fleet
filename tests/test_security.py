"""Security headers, CSP nonce, cross-site request refusal and Secure cookies behind https."""
import os, re, sys, tempfile
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)
os.environ["PVEF_DATA"] = tempfile.mkdtemp(prefix="pvef-sec-")
os.environ["DEMO"] = "1"
from app import create_app
bad = 0
def t(n, ok, extra=""):
    global bad
    print(("OK  " if ok else "FOUT"), n, extra if not ok else ""); bad += 0 if ok else 1
app = create_app(start_collector=False)
c = app.test_client()
r = c.post("/setup", data={"username": "admin", "password": "demo-pass-123", "password2": "demo-pass-123", "pin": "1234", "pin2": "1234"})
r = c.get("/")
h = r.headers
csp = h.get("Content-Security-Policy", "")
t("page has a CSP with frame-ancestors, object-src and base-uri", all(x in csp for x in ("frame-ancestors 'self'", "object-src 'none'", "base-uri 'none'")), csp)
m = re.search(r"'nonce-([^']+)'", csp)
t("script-src has a nonce and no unsafe-inline", bool(m) and not re.search(r"script-src[^;]*'unsafe-", csp), csp)
html = r.get_data(as_text=True)
inline = re.findall(r"<script(?![^>]*\bsrc=)([^>]*)>", html)
t("every inline script carries the nonce of this response", inline and all(m and ('nonce="%s"' % m.group(1)) in a for a in inline), inline)
r2 = c.get("/")
m2 = re.search(r"'nonce-([^']+)'", r2.headers.get("Content-Security-Policy", ""))
t("the nonce changes per request", m and m2 and m.group(1) != m2.group(1))
t("other headers", h.get("X-Content-Type-Options") == "nosniff" and h.get("X-Frame-Options") == "SAMEORIGIN" and "camera=()" in h.get("Permissions-Policy", ""))
t("no HSTS over plain http", "Strict-Transport-Security" not in h)
t("JSON gets the sandbox policy", c.get("/api/fleet").headers.get("Content-Security-Policy") == "default-src 'none'; sandbox")
r = c.get("/", headers={"X-Forwarded-Proto": "https"})
t("behind https: HSTS", "max-age" in r.headers.get("Strict-Transport-Security", ""))
for site in ("cross-site", "same-site"):
    r = c.post("/api/settings/password", json={}, headers={"X-PVEF": "1", "Sec-Fetch-Site": site})
    t("POST with Sec-Fetch-Site: %s is refused" % site, r.status_code == 403, r.status_code)
r = c.post("/api/settings/password", json={}, headers={"X-PVEF": "1", "Sec-Fetch-Site": "same-origin"})
t("POST same-origin passes the check", r.status_code != 403, r.status_code)
r = c.post("/api/settings/password", json={}, headers={"X-PVEF": "1"})
t("POST without Sec-Fetch-Site (curl, scripts) passes the check", r.status_code != 403, r.status_code)
c3 = app.test_client()
r = c3.post("/login", data={"username": "admin", "password": "demo-pass-123"}, headers={"X-Forwarded-Proto": "https"})
t("session cookie is Secure behind https", "Secure" in " ".join(r.headers.getlist("Set-Cookie")), r.headers.getlist("Set-Cookie"))
r = app.test_client().post("/login", data={"username": "admin", "password": "demo-pass-123"})
t("session cookie is not Secure over plain http", "Secure" not in " ".join(r.headers.getlist("Set-Cookie")))
sys.exit(1 if bad else 0)
