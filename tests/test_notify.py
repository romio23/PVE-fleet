"""Notifications (0.10.0): providers, settings API, and the event logic (no double messages, seeding, recovery)."""
import json, os, sys, tempfile, threading, time, logging
from http.server import BaseHTTPRequestHandler, HTTPServer
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)
logging.disable(logging.CRITICAL)
bad = 0
def t(n, ok):
    global bad
    print(("OK  " if ok else "FOUT"), n, flush=True); bad += 0 if ok else 1

GOT = []
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        GOT.append({"path": self.path, "body": body, "hdr": {k.lower(): v for k, v in self.headers.items()}})
        code = 401 if self.path.startswith("/bad") else 200
        self.send_response(code); self.end_headers(); self.wfile.write(b"{}")
    def log_message(self, *a): pass
srv = HTTPServer(("127.0.0.1", 0), H); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % port

os.environ["PVEF_DATA"] = tempfile.mkdtemp(); os.environ["DEMO"] = "1"
from app import create_app, notify
app = create_app(start_collector=False)
n = app.extensions["notify"]
t("defaults: nothing enabled, all events on", not any(c["enabled"] for c in n.cfg["channels"].values()) and all(n.cfg["events"].values()))

def wait(k, to=3):
    end = time.time() + to
    while time.time() < end and len(GOT) < k: time.sleep(0.05)
    return len(GOT) >= k

# ---- providers (prowl and pushover have fixed hosts: capture the call instead)
calls = []
real_post = notify._post
notify._post = lambda url, **kw: calls.append((url, kw)) or (200, "")
n.update({"channels": {"prowl": {"apikey": "PK"}, "pushover": {"user": "U", "token": "T", "device": "iphone"}}})
r = n.test("prowl"); t("prowl test ok", r["ok"] and calls[-1][0].endswith("/publicapi/add") and calls[-1][1]["data"]["apikey"] == "PK" and calls[-1][1]["data"]["application"] == "PVE Fleet")
r = n.test("pushover"); d = calls[-1][1]["data"]
t("pushover test ok", r["ok"] and calls[-1][0].endswith("/1/messages.json") and d["token"] == "T" and d["user"] == "U" and d["device"] == "iphone")
r = n.test("prowl", {"apikey": "OTHER"}); t("test uses unsaved form values", calls[-1][1]["data"]["apikey"] == "OTHER")
r = n.test("prowl", {"apikey": ""}); t("empty secret in form keeps saved one", calls[-1][1]["data"]["apikey"] == "PK")
notify._post = real_post
n.update({"channels": {"pushover": {"user_clear": True}}})
r = n.test("pushover"); t("missing key gives a clear error", not r["ok"] and "nodig" in r["error"])
t("public() never returns secrets", all(not c.get(k) for ch, ks in notify.SECRETS.items() for c in [n.public()["channels"][ch]] for k in ks) and n.public()["channels"]["prowl"]["apikey_set"] is True)
mode = oct(os.stat(os.path.join(os.environ["PVEF_DATA"], "notify.json")).st_mode & 0o777)
t("notify.json is private (%s)" % mode, mode == "0o600")

# ---- ntfy + webhook against the local server
GOT.clear()
r = n.test("ntfy", {"server": base, "topic": "abc", "token": "tok"})
t("ntfy ok, JSON publish with bearer token", r["ok"] and wait(1) and json.loads(GOT[0]["body"])["topic"] == "abc" and GOT[0]["hdr"].get("authorization") == "Bearer tok")
t("ntfy title may contain non-ASCII (JSON, no header)", True)
GOT.clear()
r = n.test("webhook", {"url": base + "/hook", "secret": "s3"})
b = json.loads(GOT[0]["body"]) if wait(1) else {}
t("webhook ok: json with event/title/message/text/content", r["ok"] and b.get("source") == "pve-fleet" and b.get("event") == "test" and b.get("text") and b.get("content") and GOT[0]["hdr"].get("x-pvefleet-secret") == "s3")
r = n.test("webhook", {"url": base + "/bad"}); t("HTTP error is reported", not r["ok"] and "401" in r["error"])
r = n.test("webhook", {"url": "ftp://x"}); t("only http(s) addresses", not r["ok"])
r = n.test("ntfy", {"server": "http://127.0.0.1:1", "topic": "x"}); t("no connection is reported", not r["ok"] and "verbinding" in r["error"])

# ---- event logic (channel = webhook to the local server)
n.update({"channels": {"webhook": {"enabled": True, "url": base + "/ev"}}, "offline_delay": 60})
def sent(): time.sleep(0.4); x = [json.loads(g["body"]) for g in GOT if g["path"] == "/ev"]; return x
def snap(alerts=(), groups=()): return {"ready": True, "demo": False, "alerts": list(alerts), "_full": list(groups)}
def node(name, count=0, sec=0, reboot=False, status="online"): return {"name": name, "status": status, "updates": {"count": count, "security": sec, "reboot_needed": reboot}}
def grp(*nodes, guests=()): return {"server": "c1", "name": "c1", "reachable": True, "nodes": list(nodes), "guests": list(guests)}
def al(aid, text, lvl="crit"): return {"id": aid, "text": text, "level": lvl, "server": "c1", "node": "n1"}

GOT.clear(); n.state = None; n.seeded = False
n.observe(snap([al("offline:c1:n1", "n1 is offline")], [grp(node("n1", 5, 1, True))]))
t("first poll after start announces nothing (seeding)", sent() == [])
n.observe(snap([al("offline:c1:n1", "n1 is offline")], [grp(node("n1", 5, 1, True))]))
t("still nothing for the same situation", sent() == [])
n.observe(snap([], [grp(node("n1", 5, 1, True))]))
s = sent(); t("alert that was there at startup: only the 'back to normal' message", len(s) == 1 and s[0]["level"] == "ok"); GOT.clear()

n.observe(snap([al("offline:c1:n2", "n2 is offline")], [grp(node("n1", 5, 1, True))]))
t("offline alert waits for the delay (no blips)", sent() == [])
n.first_seen["offline:c1:n2"] -= 120
n.observe(snap([al("offline:c1:n2", "n2 is offline")], [grp(node("n1", 5, 1, True))]))
s = sent(); t("offline announced after the delay, level crit", len(s) == 1 and s[0]["event"] == "offline" and s[0]["level"] == "crit" and "n2" in s[0]["message"])
n.observe(snap([al("offline:c1:n2", "n2 is offline")], [grp(node("n1", 5, 1, True))]))
t("not announced twice", len(sent()) == 1)
n.observe(snap([], [grp(node("n1", 5, 1, True))]))
s = sent(); t("recovery message", len(s) == 2 and s[1]["level"] == "ok" and "n2" in s[1]["message"])

n.observe(snap([al("backup:c1:n1:1", "Back-up mislukt: VM/CT 100 op n1", "warn")], [grp(node("n1", 5, 1, True))]))
s = sent(); t("failed backup is announced at once", len(s) == 3 and s[2]["event"] == "backup")
n.observe(snap([], [grp(node("n1", 5, 1, True))])); t("no recovery message for backups", len(sent()) == 3)

# updates: seeded state had n1 with 5 updates and reboot; more updates -> message, same -> none, fewer -> none
n.observe(snap([], [grp(node("n1", 5, 1, True))])); t("same updates: nothing", len(sent()) == 3)
n.observe(snap([], [grp(node("n1", 7, 3, True))])); s = sent()
t("more updates: message with security, level warn", len(s) == 4 and s[3]["event"] == "updates" and "n1" in s[3]["message"] and "3" in s[3]["message"] and s[3]["level"] == "warn")
n.observe(snap([], [grp(node("n1", 2, 0, True))])); t("fewer updates: nothing", len(sent()) == 4)
n.observe(snap([], [grp(node("n1", 3, 0, True), node("n2", 1, 0, False))])); s = sent()
t("new node with updates + n1 grew: one combined message", len(s) == 5 and "n2" in s[4]["message"] and "n1" in s[4]["message"])
n.observe(snap([], [grp(node("n1", 0, 0, False), node("n2", 0, 0, False))]))
n.observe(snap([], [grp(node("n1", 4, 0, False))])); s = sent()
t("updates gone and back again: announced again", len(s) == 6 and s[5]["event"] == "updates")
n.observe(snap([], [grp(node("n1", 4, 0, True))])); s = sent()
t("restart needed: own message", len(s) == 7 and s[6]["event"] == "reboot" and "n1" in s[6]["message"])
n.observe(snap([], [grp({"name": "n1", "status": "online", "updates": None})])); t("unknown update state does not reset anything", len(sent()) == 7)
n.observe(snap([], [grp(node("n1", 4, 0, True))])); t("... and is not re-announced afterwards", len(sent()) == 7)

# restart: state file is read, so nothing is announced again
n2 = notify.Notifier(os.environ["PVEF_DATA"], "x"); n2.update({"channels": {"webhook": {"enabled": True, "url": base + "/ev"}}})
n2.observe(snap([], [grp(node("n1", 4, 0, True))])); t("after a restart of PVE Fleet: no repeat", len(sent()) == 7)

# during an automatic rolling update: updates/reboot stay silent
from app import autorolling
autorolling.RUNS["c1"] = {"status": "running"}
n.observe(snap([], [grp(node("n1", 9, 9, True), node("n9", 2, 0, True))])); t("quiet while a rolling update runs", len(sent()) == 7)
autorolling.RUNS.clear()
n.rolling_finished({"status": "done", "server": "c1"}); s = sent(); t("rolling done message", len(s) == 8 and s[7]["event"] == "rolling" and s[7]["level"] == "ok")
n.rolling_finished({"status": "failed", "server": "c1", "current": "n2", "error": "geen quorum"}); s = sent()
t("rolling failure: crit with node and reason", len(s) == 9 and s[8]["level"] == "crit" and "n2" in s[8]["message"] and "geen quorum" in s[8]["message"])
n.rolling_finished({"status": "stopped", "server": "c1", "current": "n3"}); t("rolling stopped message", len(sent()) == 10)
n.update({"events": {"rolling": False}}); n.rolling_finished({"status": "done", "server": "c1"}); t("event switched off: nothing", len(sent()) == 10)
n.update({"events": {"rolling": True}, "lang": "en"}); n.rolling_finished({"status": "done", "server": "c1"}); s = sent()
t("English messages", len(s) == 11 and "finished" in s[10]["title"])
n.update({"lang": "nl", "channels": {"webhook": {"enabled": False}}}); n.rolling_finished({"status": "done", "server": "c1"}); t("channel off: nothing", len(sent()) == 11)
n.update({"channels": {"webhook": {"enabled": True}}})
n.observe({"ready": True, "demo": True, "alerts": [al("offline:c1:zz", "x")], "_full": []}); t("demo data never triggers messages", len(sent()) == 11)

# ---- HTTP API (as admin)
store = app.extensions["settings"]; store.set_admin("admin", "pw-1234567")
c = app.test_client()
t("page needs login", c.get("/notifications").status_code == 302 and c.get("/api/notify").status_code == 401)
with c.session_transaction() as s:
    s["user"] = "admin"; s["stamp"] = store.admin_stamp()
H1 = {"X-PVEF": "1"}
t("page loads", c.get("/notifications").status_code == 200 and b"notifications.js" in c.get("/notifications").data)
j = c.get("/api/notify").get_json(); t("GET hides secrets", j["channels"]["webhook"]["secret_set"] in (True, False) and j["channels"]["prowl"]["apikey"] == "")
r = c.post("/api/notify", json={"channels": {"ntfy": {"enabled": True, "topic": "t1", "server": "https://ntfy.sh"}}}, headers=H1)
t("POST saves", r.status_code == 200 and r.get_json()["config"]["channels"]["ntfy"]["topic"] == "t1")
t("POST without X-PVEF header is refused", c.post("/api/notify", json={}).status_code == 400)
r = c.post("/api/notify/test/webhook", json={"url": base + "/api"}, headers=H1); t("test endpoint", r.get_json()["ok"] is True)
t("unknown channel", c.post("/api/notify/test/nope", json={}, headers=H1).status_code == 404)
srv.shutdown()
sys.exit(1 if bad else 0)
