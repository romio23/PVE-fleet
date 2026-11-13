"""Instellingen die je in het dashboard aanpast (data/settings.json).

Bevat het beheerdersaccount, de kiosk-pincode en gekoppelde schermen. Wachtwoorden,
pincodes en kiosk-tokens worden alleen als hash opgeslagen.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from werkzeug.security import check_password_hash, generate_password_hash

HASH = "pbkdf2:sha256:260000"

# Acties waarvoor de kiosk om de pincode kan vragen
PIN_ACTION_KEYS = ["node_reboot", "node_shutdown", "node_wol", "ack"]
DEFAULT_KIOSK = {
    "pin_enabled": True,
    "pin_hash": None,
    "pin_changed": None,
    "pin_len": 4,
    "unlock_seconds": 120,        # 0 = bij elke actie opnieuw vragen
    "lockout_seconds": 60,        # na 5 foute pogingen; -1 = tot vrijgave in Instellingen
    "pin_actions": ["node_reboot", "node_shutdown"],
    "readonly": False,            # kiosk zonder actieknoppen
    "allow_ack": True,            # meldingen bevestigen vanaf de kiosk
    "localhost_auto": True,       # browser op deze computer zelf mag zonder koppelen de kiosk tonen
}


def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class SettingsStore:
    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, "settings.json")
        self.lock = threading.RLock()
        self.data = {}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fh:
                self.data = json.load(fh)
        changed = False
        if not self.data.get("secret_key"):
            self.data["secret_key"] = secrets.token_hex(32)
            changed = True
        kiosk = dict(DEFAULT_KIOSK)
        kiosk.update(self.data.get("kiosk") or {})
        self.data["kiosk"] = kiosk
        self.data.setdefault("devices", [])
        self.data.setdefault("admin", None)
        if changed:
            self.save()

    def save(self):
        with self.lock:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2)
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    # ---------- beheerder ----------
    @property
    def secret_key(self):
        return self.data["secret_key"]

    def has_admin(self):
        return bool(self.data.get("admin"))

    def admin_username(self):
        return (self.data.get("admin") or {}).get("username")

    def set_admin(self, username, password):
        with self.lock:
            self.data["admin"] = {"username": username, "password_hash": generate_password_hash(password, method=HASH),
                                  "changed": int(time.time())}
            self.save()

    def check_admin(self, username, password):
        a = self.data.get("admin")
        if not a:
            return False
        ok_user = hmac.compare_digest(username.strip().lower(), a["username"].lower())
        return check_password_hash(a["password_hash"], password) and ok_user

    def admin_stamp(self):
        """Verandert na een wachtwoordwijziging; oude sessies worden dan ongeldig."""
        a = self.data.get("admin") or {}
        return _sha(a.get("password_hash", ""))[:12]

    # ---------- kiosk / pincode ----------
    @property
    def kiosk(self):
        return self.data["kiosk"]

    def set_pin(self, pin):
        with self.lock:
            self.kiosk["pin_hash"] = generate_password_hash(pin, method=HASH)
            self.kiosk["pin_changed"] = int(time.time())
            self.kiosk["pin_len"] = len(pin)
            self.save()

    def check_pin(self, pin):
        h = self.kiosk.get("pin_hash")
        return bool(h) and check_password_hash(h, pin)

    def update_kiosk(self, values):
        k = self.kiosk
        with self.lock:
            for key in ("pin_enabled", "readonly", "allow_ack", "localhost_auto"):
                if key in values:
                    k[key] = bool(values[key])
            if "unlock_seconds" in values:
                v = int(values["unlock_seconds"])
                if v not in (0, 30, 120, 300):
                    raise ValueError("ongeldige ontgrendeltijd")
                k["unlock_seconds"] = v
            if "lockout_seconds" in values:
                v = int(values["lockout_seconds"])
                if v not in (60, 300, -1):
                    raise ValueError("ongeldige blokkeertijd")
                k["lockout_seconds"] = v
            if "pin_actions" in values:
                acts = [a for a in values["pin_actions"] if a in PIN_ACTION_KEYS]
                k["pin_actions"] = acts
            self.save()

    def public_kiosk(self):
        k = dict(self.kiosk)
        k["pin_set"] = bool(k.pop("pin_hash", None))
        return k

    # ---------- gekoppelde schermen ----------
    def add_device(self, name, ip=None):
        dev_id = secrets.token_hex(4)
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.data["devices"].append({"id": dev_id, "name": name[:40] or "Scherm", "token_hash": _sha(token),
                                         "created": int(time.time()), "last_seen": int(time.time()), "last_ip": ip})
            self.save()
        return dev_id, "{}.{}".format(dev_id, token)

    def device_from_cookie(self, value):
        if not value or "." not in value:
            return None
        dev_id, token = value.split(".", 1)
        for d in self.data["devices"]:
            if d["id"] == dev_id and hmac.compare_digest(d["token_hash"], _sha(token)):
                return d
        return None

    def touch_device(self, dev, ip):
        now = int(time.time())
        if now - (dev.get("last_seen") or 0) > 120 or dev.get("last_ip") != ip:
            with self.lock:
                dev["last_seen"], dev["last_ip"] = now, ip
                self.save()

    def rename_device(self, dev_id, name):
        with self.lock:
            for d in self.data["devices"]:
                if d["id"] == dev_id:
                    d["name"] = name[:40] or d["name"]
                    self.save()
                    return True
        return False

    def remove_device(self, dev_id):
        with self.lock:
            before = len(self.data["devices"])
            self.data["devices"] = [d for d in self.data["devices"] if d["id"] != dev_id]
            self.save()
            return len(self.data["devices"]) < before

    def public_devices(self):
        return [{k: d.get(k) for k in ("id", "name", "created", "last_seen", "last_ip")} for d in self.data["devices"]]


class Limiter:
    """Telt mislukte pogingen per sleutel (IP-adres of scherm) en blokkeert tijdelijk."""

    def __init__(self, max_fails=5):
        self.max_fails = max_fails
        self.state = {}
        self.lock = threading.Lock()

    def locked_for(self, key):
        st = self.state.get(key)
        if not st or not st.get("until"):
            return 0
        left = st["until"] - time.time()
        if left <= 0:
            self.state.pop(key, None)
            return 0
        return left

    def fail(self, key, lock_seconds):
        """Registreer een mislukte poging; geeft (resterende pogingen, blokkeerduur) terug."""
        with self.lock:
            st = self.state.setdefault(key, {"fails": 0, "until": 0})
            st["fails"] += 1
            if st["fails"] >= self.max_fails:
                st["fails"] = 0
                st["until"] = float("inf") if lock_seconds < 0 else time.time() + lock_seconds
                return 0, lock_seconds
            return self.max_fails - st["fails"], 0

    def reset(self, key):
        self.state.pop(key, None)

    def clear(self):
        n = sum(1 for k in list(self.state) if self.locked_for(k))
        self.state.clear()
        return n

    def locked_keys(self):
        return [k for k in list(self.state) if self.locked_for(k)]
