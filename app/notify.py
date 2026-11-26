"""Push notifications (0.10.0): Prowl, ntfy, Pushover and a generic webhook.

Settings live in data/notify.json (chmod 600: it holds API keys), what was already announced in
data/notify_state.json (so a restart does not repeat old news).

Events (each can be switched off):
  updates  new updates for a node or container (also when more arrive)
  reboot   a node must restart (new kernel)
  rolling  an automatic rolling update finished, stopped or failed
  offline  node / cluster unreachable, quorum lost (and back again)
  backup   a backup failed

The collector calls observe() after every poll; the automatic rolling update calls rolling_finished().
Sending happens in a background thread and never blocks or breaks a poll.
"""
import json
import logging
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import Blueprint, current_app, g, jsonify, render_template, request

log = logging.getLogger("pvef.notify")
bp = Blueprint("notify", __name__)

CHANNELS = ("prowl", "ntfy", "pushover", "webhook")
EVENTS = ("updates", "reboot", "rolling", "offline", "backup")
SECRETS = {"prowl": ("apikey",), "ntfy": ("token",), "pushover": ("user", "token"), "webhook": ("secret",)}
DEFAULTS = {
    "lang": "nl",
    "offline_delay": 60,    # seconds an alert must last before it is announced (no blips)
    "events": {"updates": True, "reboot": True, "rolling": True, "offline": True, "backup": True},
    "channels": {
        "prowl": {"enabled": False, "apikey": ""},
        "ntfy": {"enabled": False, "server": "https://ntfy.sh", "topic": "", "token": ""},
        "pushover": {"enabled": False, "user": "", "token": "", "device": ""},
        "webhook": {"enabled": False, "url": "", "secret": "", "verify_tls": True},
    },
}
TIMEOUT = 10
MAX_LIST = 8

# message texts per language; {…} are filled in
T = {
    "nl": {
        "test_title": "PVE Fleet: testmelding", "test_msg": "Dit is een testmelding. Als je dit leest, werkt deze verbinding.",
        "upd_title": "PVE Fleet: nieuwe updates", "upd_sec": "{n} security",
        "more": "en {n} meer", "node": "node", "ct": "CT", "app": "app-update",
        "reboot_title": "PVE Fleet: herstart nodig", "reboot_msg": "{names}: herstarten voor een nieuwe kernel of bibliotheken.",
        "roll_done": "Rolling update klaar", "roll_done_msg": "{server}: alle nodes zijn bijgewerkt.",
        "roll_stop": "Rolling update gestopt", "roll_stop_msg": "{server}: gestopt op verzoek{where}.",
        "roll_fail": "Rolling update mislukt", "roll_fail_msg": "{server}: gestopt door een fout{where}: {error}",
        "where": " bij {node}",
        "back_title": "PVE Fleet: weer bereikbaar", "back_msg": "{text}: weer in orde.",
        "bk_title": "PVE Fleet: back-up mislukt",
    },
    "en": {
        "test_title": "PVE Fleet: test notification", "test_msg": "This is a test notification. If you can read this, this connection works.",
        "upd_title": "PVE Fleet: new updates", "upd_sec": "{n} security",
        "more": "and {n} more", "node": "node", "ct": "CT", "app": "app update",
        "reboot_title": "PVE Fleet: restart needed", "reboot_msg": "{names}: restart for a new kernel or libraries.",
        "roll_done": "Rolling update finished", "roll_done_msg": "{server}: all nodes are updated.",
        "roll_stop": "Rolling update stopped", "roll_stop_msg": "{server}: stopped on request{where}.",
        "roll_fail": "Rolling update failed", "roll_fail_msg": "{server}: stopped by an error{where}: {error}",
        "where": " at {node}",
        "back_title": "PVE Fleet: reachable again", "back_msg": "{text}: back to normal.",
        "bk_title": "PVE Fleet: backup failed",
    },
}

NOTIFIER = None   # set in init_app; the collector and the automatic rolling update use it


# ------------------------------------------------------------------ providers
def _post(url, data=None, json_body=None, headers=None, verify=True):
    hdr = {"User-Agent": "PVE-Fleet"}
    hdr.update(headers or {})
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        hdr["Content-Type"] = "application/json"
    else:
        body = urllib.parse.urlencode(data or {}).encode("utf-8")
        hdr["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=hdr, method="POST")
    ctx = None
    if url.lower().startswith("https") and not verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
        return r.status, r.read(2000).decode("utf-8", "replace")


def _http_error(e):
    """A short message a person can act on (no raw Python errors)."""
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = e.read(200).decode("utf-8", "replace").strip()
        except Exception:  # noqa: BLE001
            body = ""
        if e.code in (401, 403):
            return "geweigerd (HTTP {}): controleer de sleutel of het token".format(e.code)
        if e.code == 404:
            return "niet gevonden (HTTP 404): controleer het adres of het topic"
        if e.code == 429:
            return "te veel berichten (HTTP 429): probeer het later opnieuw"
        return "HTTP {} {}".format(e.code, body[:150]).strip()
    if isinstance(e, urllib.error.URLError):
        r = getattr(e, "reason", e)
        if isinstance(r, ConnectionRefusedError):
            return "geen verbinding: de server weigert de verbinding (staat hij aan, klopt de poort?)"
        if isinstance(r, socket.gaierror):
            return "geen verbinding: het adres is niet gevonden (controleer de naam en de DNS-instelling)"
        if isinstance(r, (socket.timeout, TimeoutError)):
            return "geen verbinding: geen antwoord binnen {} seconden".format(TIMEOUT)
        if isinstance(r, ssl.SSLError):
            return "geen verbinding: het certificaat wordt niet vertrouwd (zet bij een webhook 'Certificaat controleren' uit voor een eigen certificaat)"
        return "geen verbinding: {}".format(r)
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "geen verbinding: geen antwoord binnen {} seconden".format(TIMEOUT)
    return str(e)


def _send_prowl(c, m):
    if not c.get("apikey"):
        raise ValueError("geen API-sleutel ingevuld")
    _post("https://api.prowlapp.com/publicapi/add",
          data={"apikey": c["apikey"], "application": "PVE Fleet", "event": m["title"][:1024],
                "description": m["message"][:10000], "priority": 1 if m["level"] == "crit" else 0})


def _send_ntfy(c, m):
    topic = (c.get("topic") or "").strip().strip("/")
    server = (c.get("server") or "https://ntfy.sh").strip().rstrip("/")
    if not topic:
        raise ValueError("geen topic ingevuld")
    if not server.lower().startswith(("http://", "https://")):
        raise ValueError("het serveradres moet met http:// of https:// beginnen")
    body = {"topic": topic, "title": m["title"], "message": m["message"],
            "priority": 4 if m["level"] == "crit" else 3,
            "tags": {"crit": ["rotating_light"], "warn": ["warning"], "ok": ["white_check_mark"]}.get(m["level"], ["information_source"])}
    hdr = {"Authorization": "Bearer " + c["token"]} if c.get("token") else {}
    _post(server, json_body=body, headers=hdr)   # JSON publish: also works for non-ASCII titles


def _send_pushover(c, m):
    if not c.get("user") or not c.get("token"):
        raise ValueError("gebruikerssleutel en applicatietoken zijn allebei nodig")
    data = {"token": c["token"], "user": c["user"], "title": m["title"][:250], "message": m["message"][:1024],
            "priority": 1 if m["level"] == "crit" else 0}
    if c.get("device"):
        data["device"] = c["device"]
    _post("https://api.pushover.net/1/messages.json", data=data)


def _send_webhook(c, m):
    url = (c.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("het adres moet met http:// of https:// beginnen")
    text = m["title"] + "\n" + m["message"]
    body = {"source": "pve-fleet", "event": m["event"], "level": m["level"], "title": m["title"], "message": m["message"],
            "time": int(m["time"]), "server": m.get("server"), "node": m.get("node"), "version": m.get("version"),
            "text": text, "content": text}   # text: Slack/Mattermost, content: Discord
    hdr = {"X-PVEFleet-Secret": c["secret"]} if c.get("secret") else {}
    _post(url, json_body=body, headers=hdr, verify=c.get("verify_tls", True) is not False)


SENDERS = {"prowl": _send_prowl, "ntfy": _send_ntfy, "pushover": _send_pushover, "webhook": _send_webhook}


# ------------------------------------------------------------------ notifier
class Notifier:
    def __init__(self, data_dir, version=""):
        self.dir = data_dir
        self.version = version
        self.lock = threading.RLock()
        self.cfg = self._load(os.path.join(data_dir, "notify.json"), None)
        self.cfg = self._merge(self.cfg or {})
        self.state = self._load(os.path.join(data_dir, "notify_state.json"), None)   # None = first run: seed silently
        self.last = {}            # channel -> {"ok", "time", "error"}
        self.first_seen = {}      # alert id -> first time seen
        self.announced = set()    # alert ids that were announced (to announce recovery)
        self._texts = {}          # alert id -> text, for the 'back to normal' message
        self.seeded = False

    # ---- files
    @staticmethod
    def _load(path, default):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return default

    def _write(self, name, data, private=False):
        try:
            path = os.path.join(self.dir, name)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp, path)
            if private:
                os.chmod(path, 0o600)
        except OSError as e:
            log.warning("could not write %s: %s", name, e)

    @staticmethod
    def _merge(saved):
        out = json.loads(json.dumps(DEFAULTS))
        if saved.get("lang") in T:
            out["lang"] = saved["lang"]
        try:
            out["offline_delay"] = max(0, min(3600, int(saved.get("offline_delay", out["offline_delay"]))))
        except (TypeError, ValueError):
            pass
        for k in EVENTS:
            if k in (saved.get("events") or {}):
                out["events"][k] = bool(saved["events"][k])
        for ch in CHANNELS:
            src = (saved.get("channels") or {}).get(ch) or {}
            for k, dv in out["channels"][ch].items():
                if k in src:
                    out["channels"][ch][k] = bool(src[k]) if isinstance(dv, bool) else str(src[k]).strip()
        return out

    # ---- settings (API)
    def public(self):
        with self.lock:
            cfg = json.loads(json.dumps(self.cfg))
            for ch, keys in SECRETS.items():
                for k in keys:
                    cfg["channels"][ch][k + "_set"] = bool(cfg["channels"][ch].get(k))
                    cfg["channels"][ch][k] = ""
            cfg["last"] = dict(self.last)
            return cfg

    def update(self, body):
        """Save. A secret field that is left empty keeps the saved value."""
        with self.lock:
            new = json.loads(json.dumps(self.cfg))
            if body.get("lang") in T:
                new["lang"] = body["lang"]
            if "offline_delay" in body:
                new["offline_delay"] = self._merge({"offline_delay": body["offline_delay"]})["offline_delay"]
            for k, v in (body.get("events") or {}).items():
                if k in EVENTS:
                    new["events"][k] = bool(v)
            for ch in CHANNELS:
                src = (body.get("channels") or {}).get(ch)
                if not isinstance(src, dict):
                    continue
                for k, cur in new["channels"][ch].items():
                    if k not in src:
                        continue
                    v = src[k]
                    if isinstance(cur, bool):
                        new["channels"][ch][k] = bool(v)
                    else:
                        v = str(v).strip()
                        if k in SECRETS[ch] and not v:
                            continue            # keep the old secret
                        new["channels"][ch][k] = v
                for k in SECRETS[ch]:
                    if src.get(k + "_clear"):
                        new["channels"][ch][k] = ""
            self.cfg = new
            self._write("notify.json", new, private=True)

    def channel_cfg(self, ch, override=None):
        """The saved settings of a channel, with values from the form (not yet saved) on top."""
        with self.lock:
            c = dict(self.cfg["channels"][ch])
        for k, v in (override or {}).items():
            if k not in c or k == "enabled":
                continue
            if isinstance(c[k], bool):
                c[k] = bool(v)
            else:
                v = str(v).strip()
                if v or k not in SECRETS[ch]:
                    c[k] = v
        return c

    # ---- sending
    def _message(self, event, title, message, level="info", server=None, node=None):
        return {"event": event, "title": title, "message": message, "level": level, "time": time.time(),
                "server": server, "node": node, "version": self.version}

    def _deliver(self, ch, cfg, m):
        try:
            SENDERS[ch](cfg, m)
            res = {"ok": True, "time": time.time(), "error": None}
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "time": time.time(), "error": _http_error(e)[:300]}
            log.warning("notification via %s failed: %s", ch, res["error"])
        self.last[ch] = res
        return res

    def test(self, ch, override=None):
        t = T[self.cfg["lang"]]
        m = self._message("test", t["test_title"], t["test_msg"], "info")
        return self._deliver(ch, self.channel_cfg(ch, override), m)

    def send(self, event, title, message, level="info", server=None, node=None):
        """Send to every enabled channel, in the background. Returns False if nothing will be sent."""
        with self.lock:
            if not self.cfg["events"].get(event):
                return False
            chans = [ch for ch in CHANNELS if self.cfg["channels"][ch]["enabled"]]
        if not chans:
            return False
        m = self._message(event, title, message, level, server, node)
        for ch in chans:
            threading.Thread(target=self._deliver, args=(ch, self.channel_cfg(ch), m), daemon=True, name="notify-" + ch).start()
        return True

    # ---- events
    def t(self, key, **kw):
        return T[self.cfg["lang"]][key].format(**kw)

    def rolling_finished(self, run):
        st = run.get("status")
        server = run.get("server")
        node = run.get("current")
        where = self.t("where", node=node) if node else ""
        if st == "done":
            self.send("rolling", self.t("roll_done"), self.t("roll_done_msg", server=server), "ok", server)
        elif st == "stopped":
            self.send("rolling", self.t("roll_stop"), self.t("roll_stop_msg", server=server, where=where), "warn", server, node)
        elif st == "failed":
            self.send("rolling", self.t("roll_fail"),
                      self.t("roll_fail_msg", server=server, where=where, error=run.get("error") or "?"), "crit", server, node)

    def _rolling_active(self):
        try:
            from . import autorolling
            return any(r.get("status") == "running" for r in list(autorolling.RUNS.values()))
        except Exception:  # noqa: BLE001
            return False

    def observe(self, snap):
        """Called after every poll with the new snapshot (including the "_full" groups)."""
        if not snap.get("ready") or snap.get("demo"):
            return
        now = time.time()
        first = not self.seeded
        self.seeded = True
        self._observe_alerts(snap.get("alerts") or [], now, first)
        self._observe_updates(snap.get("_full") or [], first)

    # alerts -> offline / backup
    def _observe_alerts(self, alerts, now, first):
        delay = self.cfg["offline_delay"]
        active = set()
        for a in alerts:
            aid = a["id"]
            if aid.startswith(("unreachable:", "quorum:", "offline:")):
                ev = "offline"
            elif aid.startswith("backup:"):
                ev = "backup"
            else:
                continue
            active.add(aid)
            self._texts[aid] = a["text"]
            seen = self.first_seen.setdefault(aid, now)
            if aid in self.announced:
                continue
            if first:                       # already there at startup: do not announce again
                self.announced.add(aid)
                continue
            if ev == "offline" and now - seen < delay:
                continue
            self.announced.add(aid)
            title = self.t("bk_title") if ev == "backup" else "PVE Fleet: " + a["text"]
            self.send(ev, title, a["text"], "crit" if a["level"] == "crit" else "warn", a.get("server"), a.get("node"))
        for aid in list(self.first_seen):
            if aid not in active:
                self.first_seen.pop(aid, None)
                if aid in self.announced:
                    self.announced.discard(aid)
                    text = self._texts.pop(aid, None)
                    if text and aid.startswith(("unreachable:", "quorum:", "offline:")):
                        self.send("offline", self.t("back_title"), self.t("back_msg", text=text), "ok")

    # updates and restarts
    def _collect(self, groups):
        """-> (updates, reboot, known_zero_updates, known_zero_reboot)."""
        from . import guestupdates
        upd, reb, zu, zr = {}, {}, set(), set()
        for gr in groups:
            if not gr.get("reachable"):
                continue
            srv = gr["server"]
            for n in gr.get("nodes") or []:
                if n.get("status") != "online":
                    continue
                u = n.get("updates")
                if u is None:
                    continue
                k = "node:{}:{}".format(srv, n["name"])
                cnt = int(u.get("count") or 0)
                if cnt > 0:
                    upd[k] = {"label": "{} {}".format(self.t("node"), n["name"]), "count": cnt,
                              "security": int(u.get("security") or 0), "app": False}
                else:
                    zu.add(k)
                rk = "reboot:{}:{}".format(srv, n["name"])
                if u.get("reboot_needed"):
                    reb[rk] = n["name"]
                else:
                    zr.add(rk)
            for x in gr.get("guests") or []:
                if x.get("type") != "lxc" or not x.get("vmid"):
                    continue
                s = guestupdates.summary(srv, x["vmid"])
                if not s or s.get("error") or s.get("stale"):
                    continue
                k = "ct:{}:{}".format(srv, x["vmid"])
                cnt = int(s.get("count") or 0)
                if cnt > 0 or s.get("app_update"):
                    upd[k] = {"label": "{} {}{}".format(self.t("ct"), x["vmid"], " " + x["name"] if x.get("name") else ""),
                              "count": cnt, "security": int(s.get("security") or 0), "app": bool(s.get("app_update"))}
                else:
                    zu.add(k)
        return upd, reb, zu, zr

    def _observe_updates(self, groups, first):
        try:
            upd, reb, zu, zr = self._collect(groups)
        except Exception:  # noqa: BLE001
            log.exception("collecting update state")
            return
        with self.lock:
            old = self.state
            seed = old is None
            old = old or {"updates": {}, "reboot": {}}
            quiet = seed or self._rolling_active()
            new_u = {k: v for k, v in old.get("updates", {}).items() if k not in zu}
            new_r = {k: v for k, v in old.get("reboot", {}).items() if k not in zr}
            fresh, fresh_sec, fresh_r = [], 0, []
            for k, v in upd.items():
                o = old.get("updates", {}).get(k)
                if o is None or v["count"] > o.get("count", 0) or v["security"] > o.get("security", 0) or (v["app"] and not o.get("app")):
                    fresh.append(v)
                    fresh_sec += max(0, v["security"] - (o or {}).get("security", 0))
                new_u[k] = {"count": v["count"], "security": v["security"], "app": v["app"]}
            for k, name in reb.items():
                if k not in old.get("reboot", {}):
                    fresh_r.append(name)
                new_r[k] = name
            changed = (new_u != old.get("updates")) or (new_r != old.get("reboot")) or seed
            self.state = {"updates": new_u, "reboot": new_r}
            if changed:
                self._write("notify_state.json", self.state, private=True)
        if quiet:
            return
        if fresh:
            parts = []
            for v in fresh[:MAX_LIST]:
                bit = str(v["count"]) if v["count"] else self.t("app")
                if v["security"]:
                    bit += ", " + self.t("upd_sec", n=v["security"])
                parts.append("{} ({})".format(v["label"], bit))
            if len(fresh) > MAX_LIST:
                parts.append(self.t("more", n=len(fresh) - MAX_LIST))
            self.send("updates", self.t("upd_title"), "; ".join(parts), "warn" if fresh_sec else "info")
        if fresh_r:
            self.send("reboot", self.t("reboot_title"), self.t("reboot_msg", names=", ".join(sorted(fresh_r))), "warn")


# ------------------------------------------------------------------ module hooks
def observe(snap):
    n = NOTIFIER
    if n is not None:
        try:
            n.observe(snap)
        except Exception:  # noqa: BLE001
            log.exception("notify.observe")


def rolling_finished(run):
    n = NOTIFIER
    if n is not None:
        try:
            n.rolling_finished(run)
        except Exception:  # noqa: BLE001
            log.exception("notify.rolling_finished")


# ------------------------------------------------------------------ routes (admin only; auth gate in auth.py)
def _admin():
    if g.get("scope") != "admin":
        from flask import abort
        abort(403)


@bp.get("/notifications")
def page():
    _admin()
    return render_template("notifications.html", page="notifications")


@bp.get("/api/notify")
def api_get():
    _admin()
    return jsonify(NOTIFIER.public())


@bp.post("/api/notify")
def api_save():
    _admin()
    NOTIFIER.update(request.get_json(silent=True) or {})
    return jsonify({"ok": True, "config": NOTIFIER.public()})


@bp.post("/api/notify/test/<channel>")
def api_test(channel):
    _admin()
    if channel not in CHANNELS:
        return jsonify({"error": "onbekend kanaal"}), 404
    body = request.get_json(silent=True) or {}
    res = NOTIFIER.test(channel, body)
    return jsonify(res)


def init_app(app):
    global NOTIFIER
    from . import __version__
    NOTIFIER = Notifier(app.config["PVEF_DATA"], __version__)
    app.extensions["notify"] = NOTIFIER
    app.register_blueprint(bp)
