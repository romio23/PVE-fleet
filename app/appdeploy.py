"""Apps: een nieuwe versie van een eigen app uploaden en in een LXC-container zetten.

Werkwijze
- Elke app heeft een beschrijving 'pvefleet-app.json' (in de zip, of ingevuld bij het uploaden):
  naam, versie, soort (static / systemd / compose), map in de container, wat bewaard blijft,
  welke dienst herstart, en optioneel een adres om te controleren of de app weer werkt.
- PVE Fleet pakt de zip opnieuw in als tar.gz (zonder de bovenste map) en bewaart de laatste versies.
- Bijwerken (meteen of op een tijdstip): snapshot van de container via de API, daarna via SSH naar
  de node met een eigen sleutel die alleen deploy.sh mag starten:
    command="bash /etc/pve/pvefleet/deploy.sh",restrict ssh-ed25519 ... pve-fleet-deploy
  deploy.sh zet het pakket met 'pct push' in de container en draait daar een klein installatiescript.
  Daarna controleert PVE Fleet of de app reageert; zo niet, dan terug naar de snapshot.
"""
import io
import json
import logging
import os
import re
import secrets
import tarfile
import threading
import time
import zipfile

import requests
from flask import Blueprint, abort, current_app, g, jsonify, render_template, request

from .pve import PveError, q

log = logging.getLogger(__name__)
bp = Blueprint("appdeploy", __name__)

KEY_TAG = "pve-fleet-deploy"
SCRIPT_VERSION = 3      # deploy.sh: 2 = 'upgrade' (alle containers bijwerken), 3 = 'nodeupgrade' (automatische rolling update)


def script_version(out):
    """Versie uit het antwoord op 'ping' (PVEFLEET-DEPLOY <versie> <node>); 0 als onbekend."""
    m = re.search(r"PVEFLEET-DEPLOY (\d+)", out or "")
    return int(m.group(1)) if m else 0
SCRIPT_PATH = "/etc/pve/pvefleet/deploy.sh"
KEEP_RELEASES = 5
KEEP_SNAPSHOTS = 2
SNAP_PREFIX = "pvefleet-"
MAX_UPLOAD = 1024 * 1024 * 1024

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
GH_RE = re.compile(r"^[A-Za-z0-9_.-]{1,39}/[A-Za-z0-9_.-]{1,100}$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+-]{0,99}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,39}$")
DIR_RE = re.compile(r"^/(opt|srv|var/www|home)(/[A-Za-z0-9._-]+)+$")
REL_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*/?$")
SERVICE_RE = re.compile(r"^[A-Za-z0-9@._-]{1,80}$")
OWNER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}(:[a-z_][a-z0-9_-]{0,31})?$")
TYPES = ("static", "systemd", "compose")
JUNK = ("__pycache__/", ".git/", "__MACOSX/", ".DS_Store", "node_modules/.cache/")

# ------------------------------------------------------------------ script op de node
INSTALL_SH = r'''#!/bin/sh
# PVE Fleet: zet een nieuwe versie van een app neer (draait in de container).
set -e
DIR=$1; TYPE=$2; KEEP=$3; SERVICE=$4; VENV=$5; REQ=$6; OWNER=$7; POST=$8
case "$KEEP" in -) KEEP="" ;; esac; case "$SERVICE" in -) SERVICE="" ;; esac; case "$VENV" in -) VENV="" ;; esac
case "$REQ" in -) REQ="" ;; esac; case "$OWNER" in -) OWNER="" ;; esac; case "$POST" in -) POST="" ;; esac
ST=$(mktemp -d /tmp/pvefleet-new.XXXXXX)
trap 'rm -rf "$ST" /tmp/pvefleet-release.tar.gz' EXIT
tar -xzf /tmp/pvefleet-release.tar.gz -C "$ST"
mkdir -p "$DIR"
OLDIFS=$IFS; IFS=','
for k in $KEEP; do
  k=${k%/}; [ -n "$k" ] || continue
  if [ -e "$DIR/$k" ] || [ -L "$DIR/$k" ]; then rm -rf "$ST/$k"; echo "Bewaard: $k"; fi
done
IFS=$OLDIFS
n=$(cd "$ST" && find . -type f | wc -l)
cp -a "$ST/." "$DIR/"
echo "Bestanden neergezet: $n"
if [ -n "$OWNER" ]; then
  (cd "$ST" && find .) | while IFS= read -r f; do chown -h "$OWNER" "$DIR/$f" 2>/dev/null || true; done
  echo "Eigenaar gezet: $OWNER"
fi
if [ "$TYPE" = systemd ] && [ -n "$VENV" ]; then
  if [ ! -x "$DIR/$VENV/bin/pip" ]; then echo "Python-omgeving maken: $VENV"; python3 -m venv "$DIR/$VENV"; fi
  if [ -n "$REQ" ] && [ -f "$DIR/$REQ" ]; then
    echo "Python-pakketten bijwerken ($REQ)..."
    "$DIR/$VENV/bin/pip" install -q --disable-pip-version-check -r "$DIR/$REQ"
  fi
fi
if [ -n "$POST" ]; then
  echo "Nastap: $POST"
  cd "$DIR"
  if command -v bash >/dev/null 2>&1; then bash "$POST"; else sh "$POST"; fi
fi
case "$TYPE" in
  systemd)
    echo "Dienst herstarten: $SERVICE"
    systemctl restart "$SERVICE"
    sleep 2
    if ! systemctl is-active --quiet "$SERVICE"; then
      systemctl status "$SERVICE" --no-pager -n 15 2>&1 || true
      echo "De dienst $SERVICE draait niet na het herstarten"; exit 3
    fi ;;
  compose)
    cd "$DIR"
    echo "docker compose up -d --build"
    if docker compose version >/dev/null 2>&1; then docker compose up -d --build --remove-orphans 2>&1
    else docker-compose up -d --build 2>&1; fi ;;
  static)
    if [ -n "$SERVICE" ]; then echo "Herladen: $SERVICE"; systemctl reload "$SERVICE" 2>/dev/null || systemctl restart "$SERVICE"; fi ;;
esac
echo "PVEFLEET-OK"
'''

DEPLOY_SH = r'''#!/bin/bash
# PVE Fleet: zet een nieuwe versie van een eigen app in een container op deze node.
# Wordt alleen gestart via een beperkte SSH-sleutel (zie authorized_keys). Mag alleen
# in mappen onder /opt, /srv, /var/www of /home van een container schrijven.
set -u
export LC_ALL=C
read -r -a A <<< "${SSH_ORIGINAL_COMMAND:-}"
fail() { echo "PVEFLEET-FAIL $*"; exit 2; }
okid() { [[ "$1" =~ ^[0-9]{3,9}$ ]] || fail "ongeldig container-ID"; pct config "$1" >/dev/null 2>&1 || fail "container $1 staat niet op node $(hostname)"; }
okdir() { [[ "$1" =~ ^/(opt|srv|var/www|home)(/[A-Za-z0-9._-]+)+$ && "$1" != *..* ]] || fail "map niet toegestaan: $1"; }
okrel() { [ "$1" = "-" ] || { [[ "$1" =~ ^[A-Za-z0-9._/-]+$ && "$1" != *..* && "$1" != /* ]] || fail "ongeldig pad: $1"; }; }
case "${A[0]:-}" in
  ping)
    echo "PVEFLEET-DEPLOY 3 $(hostname)" ;;
  upgrade)
    # pakketten in een container onbeheerd bijwerken; eigen configuratiebestanden blijven staan
    okid "${A[1]:-}"
    [ "$(pct status "${A[1]}" | awk '{print $2}')" = running ] || fail "container ${A[1]} draait niet"
    pct exec "${A[1]}" -- sh -c '
      export LC_ALL=C DEBIAN_FRONTEND=noninteractive
      if command -v apt-get >/dev/null 2>&1; then
        echo "apt-get update"; apt-get update -q || exit 4
        echo "apt-get dist-upgrade"
        apt-get -y -q -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold dist-upgrade || exit 5
        apt-get -y -q autoremove >/dev/null 2>&1 || true
      elif command -v apk >/dev/null 2>&1; then
        echo "apk upgrade"; apk update && apk upgrade || exit 5
      elif command -v dnf >/dev/null 2>&1; then
        echo "dnf upgrade"; dnf -y upgrade || exit 5
      else
        echo "geen apt, apk of dnf gevonden"; exit 6
      fi
      [ -e /var/run/reboot-required ] && echo "PVEFLEET-REBOOT"
      echo "PVEFLEET-OK"
    ' 2>&1
    exit $? ;;
  nodeupgrade)
    # deze node zelf bijwerken (automatische rolling update); eigen configuratiebestanden blijven staan
    export DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none
    echo "apt-get update"
    apt-get update -q 2>&1 || echo "apt-get update gaf fouten (bijvoorbeeld de enterprise-repository zonder abonnement); toch doorgaan"
    echo "apt-get dist-upgrade"
    apt-get -y -q -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold dist-upgrade 2>&1 || fail "apt-get dist-upgrade mislukt"
    newest=$(ls /boot/vmlinuz-* 2>/dev/null | sed 's|^/boot/vmlinuz-||' | sort -V | tail -n 1)
    if [ -e /var/run/reboot-required ] || { [ -n "$newest" ] && [ "$newest" != "$(uname -r)" ]; }; then
      echo "PVEFLEET-REBOOT"
    fi
    echo "PVEFLEET-OK" ;;
  info)
    okid "${A[1]:-}"; okdir "${A[2]:-}"
    st=$(pct status "${A[1]}" | awk '{print $2}')
    echo "status=$st"
    if [ "$st" = running ]; then pct exec "${A[1]}" -- cat "${A[2]}/pvefleet-app.json" 2>/dev/null; echo; fi
    echo "PVEFLEET-OK" ;;
  deploy)
    id=${A[1]:-}; dir=${A[2]:-}; type=${A[3]:-}; keep=${A[4]:--}; svc=${A[5]:--}; venv=${A[6]:--}; req=${A[7]:--}; owner=${A[8]:--}; post=${A[9]:--}
    okid "$id"; okdir "$dir"
    [[ "$type" =~ ^(static|systemd|compose)$ ]] || fail "onbekend soort app: $type"
    [[ "$keep" = "-" || ( "$keep" =~ ^[A-Za-z0-9._/,-]+$ && "$keep" != *..* ) ]] || fail "ongeldige lijst met te bewaren paden"
    [[ "$svc" =~ ^[A-Za-z0-9@._-]+$ ]] || fail "ongeldige dienstnaam"
    [[ "$owner" = "-" || "$owner" =~ ^[a-z_][a-z0-9_-]*(:[a-z_][a-z0-9_-]*)?$ ]] || fail "ongeldige eigenaar"
    okrel "$venv"; okrel "$req"; okrel "$post"
    [ "$(pct status "$id" | awk '{print $2}')" = running ] || fail "container $id draait niet"
    tmp=$(mktemp /tmp/pvefleet-release.XXXXXX); helper=$(mktemp /tmp/pvefleet-install.XXXXXX)
    trap 'rm -f "$tmp" "$helper"' EXIT
    cat > "$tmp"
    gzip -t "$tmp" 2>/dev/null || fail "het pakket is niet goed aangekomen"
    echo "Pakket ontvangen op $(hostname): $(du -h "$tmp" | cut -f1)"
    cat > "$helper" <<'EOS'
__INSTALL_SH__
EOS
    pct push "$id" "$tmp" /tmp/pvefleet-release.tar.gz || fail "pakket in container zetten mislukt"
    pct push "$id" "$helper" /tmp/pvefleet-install.sh || fail "installatiescript in container zetten mislukt"
    pct exec "$id" -- sh /tmp/pvefleet-install.sh "$dir" "$type" "$keep" "$svc" "$venv" "$req" "$owner" "$post" 2>&1
    rc=$?
    pct exec "$id" -- rm -f /tmp/pvefleet-install.sh /tmp/pvefleet-release.tar.gz >/dev/null 2>&1
    exit $rc ;;
  *)
    fail "onbekende opdracht" ;;
esac
'''.replace("__INSTALL_SH__", INSTALL_SH.rstrip("\n"))


def setup_command(pubkey):
    line = 'command="bash {}",restrict {}'.format(SCRIPT_PATH, pubkey)
    return ("mkdir -p /etc/pve/pvefleet && cat > {path} <<'PVEFLEETDEPLOY'\n{script}PVEFLEETDEPLOY\n"
            "KEY='{line}'\n"
            "mkdir -p /root/.ssh && touch /root/.ssh/authorized_keys\n"
            "for f in /etc/pve/priv/authorized_keys /root/.ssh/authorized_keys; do\n"
            "  [ -e \"$f\" ] || continue\n"
            "  grep -q '{tag}' \"$f\" || echo \"$KEY\" >> \"$f\"\n"
            "done\n"
            "echo \"PVE Fleet apps: klaar op $(hostname)\"\n").format(path=SCRIPT_PATH, script=DEPLOY_SH, line=line, tag=KEY_TAG)


def remove_command():
    return ("sed -i '/{tag}/d' /etc/pve/priv/authorized_keys /root/.ssh/authorized_keys 2>/dev/null; "
            "rm -f {path}; echo 'PVE Fleet apps: sleutel en script verwijderd'").format(tag=KEY_TAG, path=SCRIPT_PATH)


# ------------------------------------------------------------------ opslag
class Store:
    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, "apps")
        self.ssh_dir = os.path.join(data_dir, "ssh")
        for d in (self.dir, os.path.join(self.dir, "releases"), os.path.join(self.dir, "incoming")):
            os.makedirs(d, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass
        self.path = os.path.join(self.dir, "apps.json")
        self.lock = threading.RLock()
        self.data = {"settings": {}, "apps": {}, "releases": {}, "jobs": []}
        try:
            with open(self.path, encoding="utf-8") as fh:
                d = json.load(fh)
            if isinstance(d, dict):
                for k in self.data:
                    if isinstance(d.get(k), type(self.data[k])):
                        self.data[k] = d[k]
        except (OSError, ValueError):
            pass
        for j in self.data["jobs"]:    # na een herstart: een taak die liep is afgebroken
            if j.get("status") == "running":
                j["status"] = "fail"
                j["error"] = "Onderbroken doordat PVE Fleet opnieuw startte. Controleer de container."
        self.wake = threading.Event()

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=1)
            os.replace(tmp, self.path)

    def release_file(self, rid):
        return os.path.join(self.dir, "releases", rid + ".tar.gz")

    def incoming_file(self, uid):
        return os.path.join(self.dir, "incoming", uid + ".zip")

    def job(self, jid):
        return next((j for j in self.data["jobs"] if j["id"] == jid), None)


STORE = None


def ensure_key(store):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    os.makedirs(store.ssh_dir, exist_ok=True)
    try:
        os.chmod(store.ssh_dir, 0o700)
    except OSError:
        pass
    priv = os.path.join(store.ssh_dir, "deploy_ed25519")
    pub = priv + ".pub"
    if not os.path.exists(priv):
        k = ed25519.Ed25519PrivateKey.generate()
        pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption())
        line = k.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
        fd = os.open(priv, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(pem)
        with open(pub, "w", encoding="utf-8") as fh:
            fh.write(line + " " + KEY_TAG + "\n")
    with open(pub, encoding="utf-8") as fh:
        return priv, fh.read().strip()


# ------------------------------------------------------------------ beschrijving (manifest)
def _rel(v):
    v = str(v).strip()
    while v.startswith("./"):
        v = v[2:]
    return v


def validate_manifest(m):
    """Controleer en normaliseer een app-beschrijving. Gooit ValueError met een Nederlandse melding."""
    if not isinstance(m, dict):
        raise ValueError("De beschrijving van de app is ongeldig")
    out = {}
    name = str(m.get("name") or "").strip().lower()
    if not NAME_RE.match(name):
        raise ValueError("Naam: kleine letters, cijfers, - of _ (bijvoorbeeld modelarr)")
    out["name"] = name
    out["title"] = str(m.get("title") or name).strip()[:60]
    ver = str(m.get("version") or "").strip()
    if not VERSION_RE.match(ver):
        raise ValueError("Versie ontbreekt of is ongeldig (bijvoorbeeld 1.4.0)")
    out["version"] = ver
    typ = str(m.get("type") or "").strip()
    if typ not in TYPES:
        raise ValueError("Soort app: kies static, systemd of compose")
    out["type"] = typ
    d = str(m.get("dir") or "").strip().rstrip("/")
    if not DIR_RE.match(d) or ".." in d:
        raise ValueError("Map in de container: moet onder /opt, /srv, /var/www of /home liggen (bijvoorbeeld /opt/{})".format(name))
    out["dir"] = d
    keep = m.get("keep") or []
    if isinstance(keep, str):
        keep = [k for k in re.split(r"[,\n]", keep)]
    keep = [_rel(k) for k in keep if str(k).strip()]
    for k in keep:
        if not REL_RE.match(k) or ".." in k:
            raise ValueError("Bewaren: '{}' is geen geldig pad binnen de app-map".format(k))
    out["keep"] = keep
    svc = str(m.get("service") or "").strip()
    if typ == "systemd" and not svc:
        raise ValueError("Vul de naam van de dienst in (systemd), bijvoorbeeld {}".format(name))
    if svc and not SERVICE_RE.match(svc):
        raise ValueError("Ongeldige dienstnaam")
    out["service"] = svc
    for k in ("venv", "requirements", "post_install"):
        v = _rel(m.get(k) or "")
        if v and (not REL_RE.match(v) or ".." in v):
            raise ValueError("{}: ongeldig pad".format(k))
        out[k] = v
    if out["venv"] and not out["requirements"]:
        out["requirements"] = "requirements.txt"
    owner = str(m.get("owner") or "").strip()
    if owner and not OWNER_RE.match(owner):
        raise ValueError("Eigenaar: gebruiker of gebruiker:groep, bijvoorbeeld www-data:www-data")
    out["owner"] = owner
    h = m.get("health") or {}
    if isinstance(h, dict) and h.get("port"):
        try:
            port = int(h.get("port"))
            assert 1 <= port <= 65535
        except (TypeError, ValueError, AssertionError):
            raise ValueError("Controle: ongeldige poort")
        path = str(h.get("path") or "/").strip()
        if not path.startswith("/"):
            path = "/" + path
        scheme = "https" if str(h.get("scheme") or "http").lower() == "https" else "http"
        try:
            to = max(10, min(600, int(h.get("timeout") or 90)))
        except (TypeError, ValueError):
            to = 90
        out["health"] = {"port": port, "path": path[:200], "scheme": scheme, "timeout": to}
    else:
        out["health"] = None
    gh = re.sub(r"^https?://github\.com/", "", str(m.get("github") or "").strip()).strip("/")
    gh = re.sub(r"\.git$", "", gh)
    if gh and not GH_RE.match(gh):
        raise ValueError("GitHub: gebruik het formaat eigenaar/repository (bijvoorbeeld romio23/modelarr)")
    out["github"] = gh
    if m.get("description"):
        out["description"] = str(m["description"])[:200]
    return out


def _vkey(v):
    return [(0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"[.+_-]", str(v or "").lstrip("vV"))]


def guess_from_filename(fname):
    base = os.path.basename(fname or "")
    base = re.sub(r"\.zip$", "", base, flags=re.I)
    m = re.match(r"^(.*?)[-_ ]v?(\d[\w.+-]*)$", base)
    if m:
        return re.sub(r"[^a-z0-9_-]", "-", m.group(1).lower()).strip("-"), m.group(2)
    return re.sub(r"[^a-z0-9_-]", "-", base.lower()).strip("-"), ""


def inspect_zip(path):
    """Geeft (prefix, manifest of None, aantal bestanden). prefix = bovenste map (met /) of ''."""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if not n.startswith("__MACOSX/")]
        files = [n for n in names if not n.endswith("/")]
        if not files:
            raise ValueError("De zip is leeg")
        tops = {n.split("/", 1)[0] for n in names}
        prefix = ""
        if len(tops) == 1 and all("/" in n for n in files):
            prefix = next(iter(tops)) + "/"
        manifest = None
        for cand in (prefix + "pvefleet-app.json", "pvefleet-app.json"):
            if cand in names:
                try:
                    manifest = json.loads(z.read(cand).decode("utf-8-sig"))
                except (ValueError, UnicodeDecodeError):
                    raise ValueError("pvefleet-app.json in de zip is geen geldige JSON")
                break
        return prefix, manifest, len(files)


def build_release(zip_path, prefix, manifest, out_path):
    """Pak de zip opnieuw in als tar.gz zonder de bovenste map; met de (gecontroleerde) beschrijving erin."""
    n = 0
    with zipfile.ZipFile(zip_path) as z, tarfile.open(out_path, "w:gz") as tar:
        for info in z.infolist():
            name = info.filename
            if not name.startswith(prefix):
                continue
            rel = name[len(prefix):]
            if not rel or rel == "pvefleet-app.json":
                continue
            if rel.startswith("/") or "\\" in rel or any(p == ".." for p in rel.split("/")):
                raise ValueError("Ongeldig pad in de zip: {}".format(name))
            if any(rel.startswith(j) or ("/" + j) in ("/" + rel) for j in JUNK):
                continue
            mode = (info.external_attr >> 16) & 0o777
            ti = tarfile.TarInfo(rel.rstrip("/"))
            ti.mtime = int(time.mktime(info.date_time + (0, 0, -1))) if info.date_time[0] >= 1980 else int(time.time())
            if info.is_dir():
                ti.type = tarfile.DIRTYPE
                ti.mode = mode or 0o755
                tar.addfile(ti)
                continue
            data = z.read(info)
            ti.size = len(data)
            ti.mode = mode or 0o644
            tar.addfile(ti, io.BytesIO(data))
            n += 1
        blob = json.dumps(manifest, indent=2, ensure_ascii=False).encode()
        ti = tarfile.TarInfo("pvefleet-app.json")
        ti.size, ti.mode, ti.mtime = len(blob), 0o644, int(time.time())
        tar.addfile(ti, io.BytesIO(blob))
    return n


# ------------------------------------------------------------------ SSH
def _connect(store, host, port, user):
    import paramiko
    from .guestupdates import _TofuPolicy
    priv, _ = ensure_key(store)
    kh = os.path.join(store.ssh_dir, "known_hosts")
    if not os.path.exists(kh):
        open(kh, "a").close()
    cl = paramiko.SSHClient()
    cl.load_host_keys(kh)
    pol = _TofuPolicy(kh)
    cl.set_missing_host_key_policy(type("P", (paramiko.MissingHostKeyPolicy,), {"missing_host_key": lambda s, c, h, k: pol.missing_host_key(c, h, k)})())
    try:
        cl.connect(host, port=port, username=user, pkey=paramiko.Ed25519Key.from_private_key_file(priv),
                   timeout=8, banner_timeout=10, auth_timeout=10, allow_agent=False, look_for_keys=False)
    except paramiko.BadHostKeyException:
        cl.close()
        raise PveError("de SSH-hostsleutel van {} is veranderd. Verwijder de regel voor {} uit data/ssh/known_hosts.".format(host, host))
    except paramiko.AuthenticationException:
        cl.close()
        raise PveError("de node {} weigert de app-sleutel van PVE Fleet. Plak het installatiecommando (Apps › Instellen) in de shell van een node.".format(host))
    except (paramiko.SSHException, OSError) as e:
        cl.close()
        raise PveError("SSH naar {}:{} mislukt: {}".format(host, port, e))
    return cl


def ssh_run(store, host, cmd, stdin_path=None, on_line=None, port=22, user="root", timeout=1800, on_start=None):
    """Voert 'cmd' uit (als SSH_ORIGINAL_COMMAND voor deploy.sh). Geeft (exitcode, alle uitvoer)."""
    cl = _connect(store, host, port, user)
    try:
        chan = cl.get_transport().open_session()
        chan.set_combine_stderr(True)
        chan.settimeout(timeout)
        chan.exec_command(cmd)
        if on_start:
            on_start()
        if stdin_path:
            with open(stdin_path, "rb") as fh:
                while True:
                    chunk = fh.read(65536)
                    if not chunk:
                        break
                    chan.sendall(chunk)
        chan.shutdown_write()
        buf, text = b"", []
        while True:
            data = chan.recv(4096)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                s = line.decode("utf-8", "replace").rstrip("\r")
                text.append(s)
                if on_line:
                    on_line(s)
        if buf:
            s = buf.decode("utf-8", "replace")
            text.append(s)
            if on_line:
                on_line(s)
        return chan.recv_exit_status(), "\n".join(text)
    except OSError as e:
        raise PveError("verbinding met {} verbroken: {}".format(host, e))
    finally:
        cl.close()


# ------------------------------------------------------------------ hulpjes
def _col(app):
    return app.extensions["collector"]


def _demo(app):
    return bool(_col(app).cfg.get("demo"))


def _guest(app, server, vmid):
    grp = _col(app).group(server)
    if not grp:
        return None, None
    row = next((x for x in grp["guests"] if x["type"] == "lxc" and int(x["vmid"]) == int(vmid)), None)
    if not row:
        return grp, None
    return grp, row


def _node_host(app, grp, server, node):
    n = next((x for x in grp["nodes"] if x["name"] == node), None)
    if not n or n.get("status") != "online":
        raise PveError("node {} is niet online".format(node))
    return n.get("ip") or _col(app).client(server).active_host


def _ssh_opts(app, server):
    gst = (app.extensions.get("guestupdates").settings(server) if app.extensions.get("guestupdates") else {}) or {}
    st = app.extensions["appdeploy"].data["settings"].get(server) or {}
    return int(st.get("port") or gst.get("port") or 22), st.get("user") or gst.get("user") or "root"


def _wait_task(c, node, upid, timeout=300):
    if not isinstance(upid, str) or not upid.startswith("UPID"):
        return
    end = time.time() + timeout
    while time.time() < end:
        st = c.get("/nodes/{}/tasks/{}/status".format(q(node), q(upid))) or {}
        if st.get("status") == "stopped":
            if st.get("exitstatus") not in ("OK", None) and not str(st.get("exitstatus", "")).startswith("WARNINGS"):
                raise PveError(str(st.get("exitstatus")))
            return
        time.sleep(1.5)
    raise PveError("Proxmox-taak duurde te lang")


DEMO_INSTALLED = {}   # demo: (server, vmid) -> beschrijving


def installed_info(app, server, vmid, d):
    """Wat staat er nu in de container? Geeft {'status', 'manifest'}."""
    store = app.extensions["appdeploy"]
    grp, row = _guest(app, server, vmid)
    if not row:
        raise PveError("container {} niet gevonden".format(vmid))
    if _demo(app):
        return {"status": row["status"], "manifest": DEMO_INSTALLED.get((server, int(vmid)))}
    port, user = _ssh_opts(app, server)
    rc, out = ssh_run(store, _node_host(app, grp, server, row["node"]), "info {} {}".format(int(vmid), d), port=port, user=user, timeout=60)
    if "PVEFLEET-FAIL" in out:
        raise PveError(out.split("PVEFLEET-FAIL", 1)[1].strip().splitlines()[0])
    if "PVEFLEET-OK" not in out:
        raise PveError("onverwacht antwoord van de node: {}".format(out.strip()[:200] or "(leeg)"))
    status = re.search(r"status=(\w+)", out)
    body = out.split("\n", 1)[1].rsplit("PVEFLEET-OK", 1)[0].strip() if "\n" in out else ""
    man = None
    if body:
        try:
            man = json.loads(body)
        except ValueError:
            man = None
    return {"status": status.group(1) if status else None, "manifest": man}


# ------------------------------------------------------------------ uitvoeren
def _jlog(store, job, msg):
    with store.lock:
        job.setdefault("log", []).append("{}  {}".format(time.strftime("%H:%M:%S"), msg))
        if len(job["log"]) > 400:
            job["log"] = job["log"][:20] + ["…"] + job["log"][-370:]


def _cleanup_snapshots(c, node, vmid, job_log):
    try:
        snaps = c.get("/nodes/{}/lxc/{}/snapshot".format(q(node), int(vmid))) or []
    except PveError:
        return
    ours = sorted([s for s in snaps if str(s.get("name", "")).startswith(SNAP_PREFIX)], key=lambda s: s.get("snaptime") or 0)
    for s in ours[:-KEEP_SNAPSHOTS]:
        try:
            _wait_task(c, node, c.delete("/nodes/{}/lxc/{}/snapshot/{}".format(q(node), int(vmid), q(s["name"]))))
            job_log("Oude snapshot opgeruimd: {}".format(s["name"]))
        except PveError as e:
            job_log("Let op: oude snapshot {} opruimen lukte niet: {}".format(s["name"], e))


def _health(app, row, health, job_log):
    if _demo(app):
        job_log("Controle: {}://{}:{}{} reageert (demo)".format(health["scheme"], row.get("ip") or "10.0.0.x", health["port"], health["path"]))
        return True
    ip = row.get("ip")
    if not ip:
        job_log("Controle overgeslagen: het IP-adres van de container is niet bekend.")
        return True
    url = "{}://{}:{}{}".format(health["scheme"], "[" + ip + "]" if ":" in ip else ip, health["port"], health["path"])
    job_log("Controleren of de app reageert: {}".format(url))
    end, last = time.time() + health["timeout"], None
    while time.time() < end:
        try:
            r = requests.get(url, timeout=5, verify=False, allow_redirects=False)
            if r.status_code < 500:
                job_log("De app reageert (HTTP {}).".format(r.status_code))
                return True
            last = "HTTP {}".format(r.status_code)
        except requests.RequestException as e:
            last = type(e).__name__
        time.sleep(3)
    job_log("De app reageert niet binnen {} s ({}).".format(health["timeout"], last or "geen antwoord"))
    return False


def run_job(app, job):
    store = app.extensions["appdeploy"]
    col = _col(app)

    def jl(m):
        _jlog(store, job, m)
    rel = store.data["releases"].get(job["release"])
    with store.lock:
        job.update(status="running", started=time.time())
        store.save()
    snap, node, c, row = None, None, None, None
    started = False   # vanaf hier kan de container half bijgewerkt zijn
    try:
        if not rel or not os.path.exists(store.release_file(job["release"])):
            raise PveError("het pakket van deze versie bestaat niet meer")
        m = rel["manifest"]
        c = col.client(job["server"])
        if not c:
            raise PveError("onbekende server {}".format(job["server"]))
        grp, row = _guest(app, job["server"], job["vmid"])
        if not row:
            raise PveError("container {} niet gevonden".format(job["vmid"]))
        if row["status"] != "running":
            raise PveError("container {} ({}) draait niet".format(row["name"], job["vmid"]))
        node = row["node"]
        jl("{} {} → container {} ({}) op {}".format(m["title"], m["version"], row["name"], job["vmid"], node))
        if job.get("snapshot", True):
            snap = SNAP_PREFIX + time.strftime("%y%m%d-%H%M%S")
            try:
                taken = {x.get("name") for x in (c.get("/nodes/{}/lxc/{}/snapshot".format(q(node), int(job["vmid"]))) or [])}
            except PveError:
                taken = set()
            base, i = snap, 2
            while snap in taken:
                snap, i = "{}-{}".format(base, i), i + 1
            jl("Snapshot maken: {}".format(snap))
            _wait_task(c, node, c.post("/nodes/{}/lxc/{}/snapshot".format(q(node), int(job["vmid"])),
                                       {"snapname": snap, "description": "PVE Fleet: voor {} {}".format(m["name"], m["version"])}))
            with store.lock:
                job["snapname"] = snap
        else:
            jl("Geen snapshot (zo ingesteld).")
        # uitrollen
        ok = False

        def mark_started():
            nonlocal started
            started = True
        if _demo(app):
            started = True
            for line in ["Pakket ontvangen op {}: {:.1f}M".format(node, rel["size"] / 1048576.0)] + \
                        ["Bewaard: {}".format(k.rstrip("/")) for k in m["keep"]] + \
                        ["Bestanden neergezet: {}".format(rel.get("files", 0))] + \
                        (["Python-pakketten bijwerken (requirements.txt)..."] if m.get("venv") else []) + \
                        ({"systemd": ["Dienst herstarten: {}".format(m["service"])], "compose": ["docker compose up -d --build"],
                          "static": ["Herladen: {}".format(m["service"])] if m["service"] else []}[m["type"]]):
                time.sleep(0.6)
                jl(line)
            DEMO_INSTALLED[(job["server"], int(job["vmid"]))] = m
            ok = True
        else:
            port, user = _ssh_opts(app, job["server"])
            host = _node_host(app, grp, job["server"], node)
            args = [str(int(job["vmid"])), m["dir"], m["type"], ",".join(k.rstrip("/") for k in m["keep"]) or "-",
                    m["service"] or "-", m["venv"] or "-", m["requirements"] or "-", m["owner"] or "-", m["post_install"] or "-"]
            jl("Verbinden met {} en pakket versturen ({:.1f} MB)…".format(host, rel["size"] / 1048576.0))
            rc, out = ssh_run(store, host, "deploy " + " ".join(args), stdin_path=store.release_file(job["release"]),
                              on_line=lambda s: s.strip() and s.strip() != "PVEFLEET-OK" and jl("│ " + s.replace("PVEFLEET-FAIL ", "")),
                              port=port, user=user, on_start=mark_started)
            ok = rc == 0 and "PVEFLEET-OK" in out
            if not ok:
                reason = out.split("PVEFLEET-FAIL", 1)[1].strip().splitlines()[0] if "PVEFLEET-FAIL" in out else "foutcode {}".format(rc)
                raise _DeployFailed(reason)
        if m.get("health"):
            if not _health(app, row, m["health"], jl):
                raise _DeployFailed("de app reageert niet na het bijwerken")
        with store.lock:
            job.update(status="ok", finished=time.time())
            a = store.data["apps"].setdefault(m["name"], {})
            a.update(title=m["title"], server=job["server"], vmid=int(job["vmid"]), installed=m["version"], dir=m["dir"],
                     installed_at=time.time(), last_status="ok", last_job=job["id"])
            store.save()
        jl("Klaar: {} {} staat erop.".format(m["title"], m["version"]))
        if snap:
            _cleanup_snapshots(c, node, job["vmid"], jl)
        _audit(job, True, "{} {} bijgewerkt in {}".format(m["title"], m["version"], job["vmid"]))
    except (_DeployFailed, PveError, OSError) as e:
        jl("Mislukt: {}".format(e))
        status = "fail"
        if not started:
            jl("Er is niets veranderd in de container.")
        elif not snap:
            jl("Er was geen snapshot; controleer de app in de container zelf.")
        if snap and started and job.get("rollback", True):
            try:
                jl("Terugzetten naar snapshot {}…".format(snap))
                _wait_task(c, node, c.post("/nodes/{}/lxc/{}/snapshot/{}/rollback".format(q(node), int(job["vmid"]), q(snap)), {"start": 1}))
                st = c.get("/nodes/{}/lxc/{}/status/current".format(q(node), int(job["vmid"]))) or {}
                if st.get("status") != "running":
                    _wait_task(c, node, c.post("/nodes/{}/lxc/{}/status/start".format(q(node), int(job["vmid"]))))
                jl("Teruggezet: de container draait weer de vorige versie.")
                status = "rolledback"
            except PveError as e2:
                jl("Terugzetten mislukt: {}. Zet de snapshot zelf terug op de pagina van de container.".format(e2))
        with store.lock:
            job.update(status=status, finished=time.time(), error=str(e))
            mname = (rel or {}).get("manifest", {}).get("name")
            if mname and mname in store.data["apps"]:
                store.data["apps"][mname].update(last_status=status, last_job=job["id"])
            store.save()
        _audit(job, False, "App-update {} mislukt: {}".format(job.get("label") or job["release"], e))
    except Exception as e:  # noqa: BLE001
        log.exception("app-taak")
        jl("Onverwachte fout: {}".format(e))
        with store.lock:
            job.update(status="fail", finished=time.time(), error=str(e)[:300])
            store.save()


class _DeployFailed(Exception):
    pass


def _audit(job, ok, label):
    try:
        from .actions import audit
        audit({"who": job.get("who") or "planner", "action": "app_deploy", "server": job["server"], "vmid": job["vmid"],
               "ok": ok, "label": label})
    except Exception:  # noqa: BLE001
        pass


def _worker(app):
    store = app.extensions["appdeploy"]
    while True:
        store.wake.wait(20)
        store.wake.clear()
        try:
            while True:
                now = time.time()
                with store.lock:
                    due = sorted([j for j in store.data["jobs"] if j["status"] == "pending" and (j.get("when") or 0) <= now],
                                 key=lambda j: j.get("when") or 0)
                if not due:
                    break
                run_job(app, due[0])
        except Exception:  # pragma: no cover
            log.exception("app-planner")


def _prune(store):
    """Oude versies en taken opruimen (laatste KEEP_RELEASES per app; versies van geplande taken blijven)."""
    with store.lock:
        needed = {j["release"] for j in store.data["jobs"] if j["status"] in ("pending", "running")}
        by_app = {}
        for rid, r in store.data["releases"].items():
            by_app.setdefault(r["manifest"]["name"], []).append((r["uploaded"], rid))
        for name, lst in by_app.items():
            lst.sort(reverse=True)
            for _, rid in lst[KEEP_RELEASES:]:
                if rid in needed:
                    continue
                store.data["releases"].pop(rid, None)
                try:
                    os.remove(store.release_file(rid))
                except OSError:
                    pass
        done = [j for j in store.data["jobs"] if j["status"] not in ("pending", "running")]
        if len(done) > 60:
            drop = {j["id"] for j in sorted(done, key=lambda j: j.get("finished") or j.get("created") or 0)[:len(done) - 60]}
            store.data["jobs"] = [j for j in store.data["jobs"] if j["id"] not in drop]
        inc = os.path.join(store.dir, "incoming")
        for f in os.listdir(inc):
            p = os.path.join(inc, f)
            try:
                if time.time() - os.path.getmtime(p) > 24 * 3600:
                    os.remove(p)
            except OSError:
                pass


def init_app(app):
    global STORE
    store = Store(app.config["PVEF_DATA"])
    app.extensions["appdeploy"] = store
    STORE = store
    if _demo(app):
        store.data["settings"].setdefault("_global", {"enabled": True})
        if "modelarr" not in store.data["apps"]:
            store.data["apps"]["modelarr"] = {"title": "Modelarr", "server": "cluster-home", "vmid": 104, "installed": "1.3.2",
                                              "dir": "/opt/modelarr", "installed_at": time.time() - 9 * 86400, "last_status": "ok"}
            store.data["apps"]["homelab-proxy"] = {"title": "Homelab Proxy", "server": "cluster-home", "vmid": 100, "installed": "0.9.1",
                                                   "dir": "/opt/homelab-proxy", "installed_at": time.time() - 3 * 86400, "last_status": "ok"}
        for k, a in store.data["apps"].items():
            DEMO_INSTALLED[(a["server"], int(a["vmid"]))] = {"name": k, "title": a["title"], "version": a["installed"]}
    if app.config.get("MAX_CONTENT_LENGTH") is None:
        app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD + 1024 * 1024
    app.register_blueprint(bp)
    threading.Thread(target=_worker, args=(app,), daemon=True, name="apps").start()


# ------------------------------------------------------------------ API
def _store():
    return current_app.extensions["appdeploy"]


def _admin():
    if g.get("scope") != "admin":
        abort(403)


def _enabled(store):
    return bool((store.data["settings"].get("_global") or {}).get("enabled"))


@bp.get("/apps")
def apps_page():
    _admin()
    return render_template("apps.html", page="apps")


def _public_job(j):
    return {k: j.get(k) for k in ("id", "release", "app", "title", "version", "server", "vmid", "guest", "when", "status",
                                  "created", "started", "finished", "error", "snapshot", "rollback", "snapname", "who")}


@bp.get("/api/apps")
def api_apps():
    _admin()
    app = current_app._get_current_object()
    store = _store()
    col = _col(app)
    containers = []
    for s in col.cfg["servers"]:
        grp = col.group(s["name"])
        if not grp:
            continue
        for x in grp["guests"]:
            if x["type"] == "lxc":
                containers.append({"server": s["name"], "group": grp.get("name", s["name"]), "vmid": x["vmid"], "name": x["name"],
                                   "node": x["node"], "status": x["status"], "ip": x.get("ip")})
    containers.sort(key=lambda x: (x["group"], x["vmid"]))
    names = {(x["server"], int(x["vmid"])): x for x in containers}
    with store.lock:
        apps = {}
        for k, a in store.data["apps"].items():
            ct = names.get((a.get("server"), int(a.get("vmid") or 0)))
            apps[k] = dict(a, container=ct)
        releases = sorted([dict(r, id=rid) for rid, r in store.data["releases"].items()], key=lambda r: -r["uploaded"])
        for r in releases:
            r.pop("file", None)
            apps.setdefault(r["manifest"]["name"], {"title": r["manifest"]["title"], "container": None})
        jobs = [_public_job(j) for j in sorted(store.data["jobs"], key=lambda j: -(j.get("when") or j.get("created") or 0))]
    if not _demo(app):  # per app met een GitHub-adres: wat is daar de nieuwste versie (10 min onthouden)
        from .guestupdates import github_latest
        tok = _col(app).cfg.get("github_token")
        for k in list(apps):
            gh = next((r["manifest"].get("github") for r in releases if r["manifest"]["name"] == k and r["manifest"].get("github")), "")
            if gh:
                apps[k]["github"] = gh
                apps[k]["github_latest"] = github_latest(gh, tok, False, 600)
    return jsonify({"enabled": _enabled(store), "tested": store.data["settings"].get("_global", {}).get("tested"),
                    "apps": apps, "releases": releases, "jobs": jobs, "containers": containers, "demo": _demo(app),
                    "now": time.time()})


@bp.get("/api/apps/setup")
def api_setup():
    _admin()
    _, pub = ensure_key(_store())
    return jsonify({"public_key": pub, "command": setup_command(pub), "remove": remove_command()})


@bp.post("/api/apps/enable")
def api_enable():
    """Aanzetten en testen: elke online node van elke server moet de sleutel accepteren."""
    _admin()
    app = current_app._get_current_object()
    store = _store()
    body = request.get_json(silent=True) or {}
    if body.get("enabled") is False:
        with store.lock:
            store.data["settings"]["_global"] = {"enabled": False}
            store.save()
        return jsonify({"ok": True})
    col = _col(app)
    results = []
    for s in col.cfg["servers"]:
        grp = col.group(s["name"])
        if not grp:
            continue
        port, user = _ssh_opts(app, s["name"])
        for n in grp["nodes"]:
            if n["status"] != "online":
                results.append({"server": s["name"], "node": n["name"], "ok": None, "msg": "offline, overgeslagen"})
                continue
            if _demo(app):
                results.append({"server": s["name"], "node": n["name"], "ok": True, "msg": "klaar (demo)"})
                continue
            try:
                host = n.get("ip") or col.client(s["name"]).active_host
                rc, out = ssh_run(store, host, "ping", port=port, user=user, timeout=30)
                ver = script_version(out)
                good = ver >= SCRIPT_VERSION
                if ver and not good:
                    msg = "het script op deze node is verouderd (versie {}). Plak het commando hierboven opnieuw op een node.".format(ver)
                else:
                    msg = "klaar" if good else "onverwacht antwoord: {}".format(out.strip()[:120] or rc)
                results.append({"server": s["name"], "node": n["name"], "ok": good, "msg": msg})
            except PveError as e:
                results.append({"server": s["name"], "node": n["name"], "ok": False, "msg": str(e)})
    ok = any(r["ok"] for r in results) and not any(r["ok"] is False for r in results)
    with store.lock:
        store.data["settings"]["_global"] = {"enabled": any(r["ok"] for r in results), "tested": time.time() if ok else None}
        store.save()
    return jsonify({"ok": ok, "results": results})


@bp.post("/api/apps/upload")
def api_upload():
    _admin()
    store = _store()
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "Kies een zip-bestand"}), 400
    if not f.filename.lower().endswith(".zip"):
        return jsonify({"error": "Alleen .zip-bestanden"}), 400
    uid = secrets.token_hex(8)
    path = store.incoming_file(uid)
    f.save(path)
    return _ingest(store, uid, path, f.filename)


def _ingest(store, uid, path, filename):
    """Een binnengekomen zip (upload of GitHub) lezen en een voorstel voor de beschrijving teruggeven."""
    try:
        prefix, manifest, nfiles = inspect_zip(path)
    except (zipfile.BadZipFile, ValueError) as e:
        os.remove(path)
        return jsonify({"error": "Kan de zip niet lezen: {}".format(e)}), 400
    gname, gver = guess_from_filename(filename)
    known = None
    name = (manifest or {}).get("name") or gname
    with store.lock:
        prev = sorted([r for r in store.data["releases"].values() if r["manifest"]["name"] == name], key=lambda r: -r["uploaded"])
        known = prev[0]["manifest"] if prev else None
    with open(path + ".meta", "w", encoding="utf-8") as fh:
        json.dump({"filename": filename, "prefix": prefix, "files": nfiles}, fh)
    proposal = dict(known or {}, **(manifest or {}))
    proposal.setdefault("name", gname)
    if not manifest:
        proposal["version"] = gver or ""
    return jsonify({"upload": uid, "filename": filename, "files": nfiles, "size": os.path.getsize(path),
                    "has_manifest": bool(manifest), "known": bool(known), "manifest": proposal,
                    "folder": prefix.rstrip("/")})


def _gh_headers(app, accept="application/vnd.github+json"):
    h = {"Accept": accept, "User-Agent": "pve-fleet", "X-GitHub-Api-Version": "2022-11-28"}
    tok = _col(app).cfg.get("github_token")
    if tok:
        h["Authorization"] = "Bearer " + tok
    return h


def github_latest_ref(app, repo):
    """(tag, bron) van de nieuwste release, of anders de nieuwste tag. Ruim 'geen release' en fouten op in ValueError."""
    try:
        r = requests.get("https://api.github.com/repos/{}/releases/latest".format(repo), headers=_gh_headers(app), timeout=15)
        if r.status_code == 200 and (r.json() or {}).get("tag_name"):
            return r.json()["tag_name"], "release"
        if r.status_code in (401, 403, 429):
            raise ValueError("GitHub weigert de aanvraag (HTTP {}). Is de repository privé? Vul dan een token in bij Instellingen.".format(r.status_code))
        r = requests.get("https://api.github.com/repos/{}/tags".format(repo), headers=_gh_headers(app), timeout=15, params={"per_page": 1})
    except requests.RequestException as e:
        raise ValueError("Geen verbinding met GitHub: {}".format(e))
    if r.status_code == 404:
        raise ValueError("Repository {} niet gevonden (of privé: vul een token in bij Instellingen)".format(repo))
    if r.status_code != 200:
        raise ValueError("GitHub gaf HTTP {}".format(r.status_code))
    tags = r.json() or []
    if not tags:
        raise ValueError("{} heeft nog geen release of tag. Maak er een op GitHub (bijvoorbeeld v1.4.1) of geef zelf een tak op, zoals main.".format(repo))
    return tags[0]["name"], "tag"


@bp.post("/api/apps/github")
def api_github_fetch():
    """De nieuwste release (of tag, of een opgegeven tak/tag) van een GitHub-repository ophalen als zip en laten controleren
    zoals bij een upload. Er wordt niets in een container gezet: dat doet de gebruiker daarna met Bijwerken."""
    _admin()
    app = current_app._get_current_object()
    store = _store()
    body = request.get_json(silent=True) or {}
    repo = re.sub(r"\.git$", "", re.sub(r"^https?://github\.com/", "", str(body.get("repo") or "").strip()).strip("/"))
    if not GH_RE.match(repo):
        return jsonify({"error": "Vul de repository in als eigenaar/naam, bijvoorbeeld romio23/modelarr"}), 400
    ref = str(body.get("ref") or "").strip()
    if ref and (not REF_RE.match(ref) or ".." in ref):
        return jsonify({"error": "Ongeldige tag of tak"}), 400
    if _demo(app):
        return jsonify({"error": "Ophalen van GitHub werkt niet in de demo"}), 400
    try:
        what = "tak/tag" if ref else None
        if not ref:
            ref, what = github_latest_ref(app, repo)
        r = requests.get("https://api.github.com/repos/{}/zipball/{}".format(repo, ref), headers=_gh_headers(app, "application/vnd.github+json"),
                         timeout=(10, 60), stream=True)
    except ValueError as e:
        return jsonify({"error": str(e)}), 502
    except requests.RequestException as e:
        return jsonify({"error": "Geen verbinding met GitHub: {}".format(e)}), 502
    if r.status_code == 404:
        return jsonify({"error": "{} bestaat niet in {}".format(ref, repo)}), 404
    if r.status_code != 200:
        return jsonify({"error": "GitHub gaf HTTP {} bij het ophalen van {}".format(r.status_code, ref)}), 502
    uid = secrets.token_hex(8)
    path = store.incoming_file(uid)
    size = 0
    try:
        with open(path, "wb") as fh:
            for chunk in r.iter_content(1024 * 256):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise ValueError("De download is groter dan 1 GB")
                fh.write(chunk)
    except (ValueError, requests.RequestException, OSError) as e:
        try:
            os.remove(path)
        except OSError:
            pass
        return jsonify({"error": "Ophalen mislukt: {}".format(e)}), 502
    log.info("GitHub: %s %s opgehaald (%d bytes) door %s", repo, ref, size, g.who)
    res = _ingest(store, uid, path, "{}-{}.zip".format(repo.split("/")[1], ref.replace("/", "-")))
    resp, code = (res if isinstance(res, tuple) else (res, 200))
    if code == 200:
        d = resp.get_json()
        d["manifest"].setdefault("github", repo)
        if not d["manifest"].get("github"):
            d["manifest"]["github"] = repo
        d["github"] = {"repo": repo, "ref": ref, "source": what}
        return jsonify(d)
    return res


@bp.post("/api/apps/upload/<uid>/save")
def api_upload_save(uid):
    _admin()
    store = _store()
    if not re.match(r"^[0-9a-f]{16}$", uid):
        abort(404)
    path = store.incoming_file(uid)
    if not os.path.exists(path):
        return jsonify({"error": "De upload is verlopen; upload de zip opnieuw"}), 404
    body = request.get_json(silent=True) or {}
    try:
        m = validate_manifest(body.get("manifest") or {})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    with open(path + ".meta", encoding="utf-8") as fh:
        meta = json.load(fh)
    rid = "{}-{}-{}".format(m["name"], re.sub(r"[^A-Za-z0-9._-]", "_", m["version"]), secrets.token_hex(3))
    try:
        n = build_release(path, meta["prefix"], m, store.release_file(rid))
    except (zipfile.BadZipFile, ValueError, OSError) as e:
        return jsonify({"error": str(e)}), 400
    with store.lock:
        store.data["releases"][rid] = {"manifest": m, "uploaded": time.time(), "filename": meta["filename"], "files": n,
                                       "size": os.path.getsize(store.release_file(rid)), "who": g.who}
        store.save()
    for p in (path, path + ".meta"):
        try:
            os.remove(p)
        except OSError:
            pass
    _prune(store)
    store.save()
    return jsonify({"ok": True, "release": rid})


@bp.delete("/api/apps/releases/<rid>")
def api_release_delete(rid):
    _admin()
    store = _store()
    with store.lock:
        if any(j["release"] == rid and j["status"] in ("pending", "running") for j in store.data["jobs"]):
            return jsonify({"error": "Deze versie is nog ingepland. Annuleer eerst die taak."}), 409
        if store.data["releases"].pop(rid, None) is None:
            abort(404)
        store.save()
    try:
        os.remove(store.release_file(rid))
    except OSError:
        pass
    return jsonify({"ok": True})


@bp.get("/api/apps/installed")
def api_installed():
    _admin()
    app = current_app._get_current_object()
    server = request.args.get("server", "")
    d = request.args.get("dir", "")
    try:
        vmid = int(request.args.get("vmid", ""))
    except ValueError:
        return jsonify({"error": "ongeldig ID"}), 400
    if not DIR_RE.match(d) or ".." in d:
        return jsonify({"error": "ongeldige map"}), 400
    if not _enabled(_store()):
        return jsonify({"error": "Stel eerst de app-sleutel in (Apps › Instellen)"}), 409
    try:
        return jsonify(installed_info(app, server, vmid, d))
    except PveError as e:
        return jsonify({"error": str(e)}), 502


@bp.post("/api/apps/jobs")
def api_job_create():
    _admin()
    app = current_app._get_current_object()
    store = _store()
    if not _enabled(store):
        return jsonify({"error": "Stel eerst de app-sleutel in (Apps › Instellen)"}), 409
    body = request.get_json(silent=True) or {}
    rid = str(body.get("release", ""))
    rel = store.data["releases"].get(rid)
    if not rel:
        return jsonify({"error": "Onbekende versie"}), 404
    server = str(body.get("server", ""))
    try:
        vmid = int(body.get("vmid"))
    except (TypeError, ValueError):
        return jsonify({"error": "Kies een container"}), 400
    _, row = _guest(app, server, vmid)
    if not row:
        return jsonify({"error": "Container niet gevonden"}), 404
    when = body.get("when")
    if when:
        try:
            when = float(when)
        except (TypeError, ValueError):
            return jsonify({"error": "Ongeldig tijdstip"}), 400
        if when < time.time() - 60:
            return jsonify({"error": "Dat tijdstip is al voorbij"}), 400
    with store.lock:
        if any(j["status"] in ("pending", "running") and j["server"] == server and int(j["vmid"]) == vmid
               and j["app"] == rel["manifest"]["name"] for j in store.data["jobs"]):
            return jsonify({"error": "Er staat al een update van deze app voor deze container klaar. Annuleer die eerst."}), 409
        m = rel["manifest"]
        job = {"id": secrets.token_hex(6), "release": rid, "app": m["name"], "title": m["title"], "version": m["version"],
               "server": server, "vmid": vmid, "guest": row["name"], "when": when or time.time(), "status": "pending",
               "created": time.time(), "snapshot": body.get("snapshot", True) is not False,
               "rollback": body.get("rollback", True) is not False, "who": g.who, "log": []}
        store.data["jobs"].append(job)
        a = store.data["apps"].setdefault(m["name"], {"title": m["title"]})
        a.update(server=server, vmid=vmid, dir=m["dir"])
        store.save()
    _jlog(store, job, "Ingepland voor {}".format(time.strftime("%d-%m-%Y %H:%M", time.localtime(job["when"])))
          if when else "Gestart door {}".format(g.who))
    if not when:
        store.wake.set()
    return jsonify({"ok": True, "job": _public_job(job)})


@bp.get("/api/apps/jobs/<jid>")
def api_job(jid):
    _admin()
    j = _store().job(jid)
    if not j:
        abort(404)
    return jsonify(dict(_public_job(j), log=j.get("log") or []))


@bp.post("/api/apps/jobs/<jid>/cancel")
def api_job_cancel(jid):
    _admin()
    store = _store()
    with store.lock:
        j = store.job(jid)
        if not j:
            abort(404)
        if j["status"] != "pending":
            return jsonify({"error": "Deze taak is al gestart of klaar"}), 409
        j["status"] = "cancelled"
        j["finished"] = time.time()
        store.save()
    _jlog(store, j, "Geannuleerd door {}".format(g.who))
    return jsonify({"ok": True})


@bp.post("/api/apps/jobs/<jid>/now")
def api_job_now(jid):
    _admin()
    store = _store()
    with store.lock:
        j = store.job(jid)
        if not j or j["status"] != "pending":
            return jsonify({"error": "Deze taak staat niet meer klaar"}), 409
        j["when"] = time.time()
        store.save()
    _jlog(store, j, "Nu gestart door {}".format(g.who))
    store.wake.set()
    return jsonify({"ok": True})


@bp.delete("/api/apps/<name>")
def api_app_forget(name):
    """App uit de lijst halen (met zijn bewaarde versies). In de container verandert niets."""
    _admin()
    store = _store()
    with store.lock:
        if any(j["app"] == name and j["status"] in ("pending", "running") for j in store.data["jobs"]):
            return jsonify({"error": "Er staat nog een update van deze app klaar"}), 409
        store.data["apps"].pop(name, None)
        for rid in [rid for rid, r in store.data["releases"].items() if r["manifest"]["name"] == name]:
            store.data["releases"].pop(rid, None)
            try:
                os.remove(store.release_file(rid))
            except OSError:
                pass
        store.save()
    return jsonify({"ok": True})
