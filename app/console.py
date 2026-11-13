"""Ingebouwde console: PVE Fleet geeft de console van Proxmox door aan je browser.

Werking:
1. De browser vraagt POST /api/console/start. PVE Fleet vraagt met het API-token
   een console aan bij Proxmox (termproxy voor containers en de node-shell,
   vncproxy voor VM's) en bewaart de sessie kort in het geheugen.
2. De browser opent een websocket naar /ws/console/<id>. PVE Fleet maakt zelf de
   websocket naar Proxmox (met het token) en geeft alles in beide richtingen door.

Zo hoef je niet apart bij Proxmox in te loggen. Alleen de beheerder mag dit.
"""
import logging
import re
import secrets
import ssl
import threading
import time
from urllib.parse import quote, urlparse

import websocket   # websocket-client
from flask import Blueprint, current_app, g, jsonify, request
from flask_sock import Sock

from . import rootsession
from .pve import PveError, PvePermissionError, q
from .settings import Limiter

log = logging.getLogger(__name__)
bp = Blueprint("console", __name__)
sock = Sock()

SESSIONS = {}
_lock = threading.Lock()
root_limiter = Limiter(5)   # 5 foute root-wachtwoorden -> even pauze
TTL = 60   # seconden om de websocket te openen na het starten


def _col():
    return current_app.extensions["collector"]


def _cleanup():
    now = time.time()
    with _lock:
        for k in [k for k, v in SESSIONS.items() if now - v["created"] > TTL]:
            SESSIONS.pop(k, None)


def _audit(entry):
    from .actions import audit
    audit(entry)


# ------------------------------------------------------------------ starten
@bp.post("/api/console/start")
def api_console_start():
    if g.get("scope") != "admin":
        return jsonify({"error": "De console is alleen voor de beheerder"}), 403
    body = request.get_json(silent=True) or {}
    server, node, kind = str(body.get("server", "")), str(body.get("node", "")), str(body.get("type", ""))
    if kind not in ("qemu", "lxc", "shell"):
        return jsonify({"error": "Ongeldig type"}), 400
    col = _col()
    c, group = col.client(server), col.group(server)
    if not c or not group:
        return jsonify({"error": "Onbekende server"}), 404
    n = next((x for x in group["nodes"] if x["name"] == node), None)
    if not group["reachable"] or not n or n["status"] != "online":
        return jsonify({"error": "Node {} is niet bereikbaar".format(node)}), 409
    vmid, title = None, "Shell: {}".format(node)
    if kind != "shell":
        try:
            vmid = int(body.get("vmid"))
        except (TypeError, ValueError):
            return jsonify({"error": "Ongeldig ID"}), 400
        row = next((x for x in group["guests"] if x["vmid"] == vmid and x["node"] == node and x["type"] == kind), None)
        if not row:
            return jsonify({"error": "VM/CT {} niet gevonden op {}".format(vmid, node)}), 404
        if row["status"] != "running":
            return jsonify({"error": "{} {} draait niet. Start hem eerst.".format("VM" if kind == "qemu" else "CT", vmid)}), 409
        title = "{} {} {}".format("VM" if kind == "qemu" else "CT", vmid, row["name"])

    _cleanup()
    sid = secrets.token_urlsafe(18)
    sess = {"created": time.time(), "server": server, "node": node, "kind": kind, "vmid": vmid, "title": title,
            "who": g.who, "demo": bool(col.cfg.get("demo"))}
    rs = rootsession.get(server, c) if kind == "shell" else None
    sess["root"] = bool(rs)
    if sess["demo"]:
        if kind == "qemu":
            return jsonify({"error": "In de demomodus is het scherm van een VM niet beschikbaar. "
                                     "Probeer de console van een container of de shell van een node."}), 409
        sess["proto"] = "term"
        sess["needs_login"] = kind == "shell" and not rs
    elif rs:
        # Shell met de root-sessie: Proxmox opent dan meteen een root-shell, zonder inlogprompt.
        base = "/nodes/{}".format(q(node))
        try:
            res = rootsession.post(c, rs, base + "/termproxy") or {}
        except PvePermissionError:
            rootsession.end(server)
            return jsonify({"error": "De root-sessie is verlopen. Log opnieuw in als root.", "code": "root_expired"}), 401
        except PveError as e:
            return jsonify({"error": "Proxmox: {}".format(e)}), 502
        srv = col.server_cfg(server) or {}
        sess.update({"proto": "term", "port": res.get("port"), "ticket": res.get("ticket"), "user": res.get("user") or rs["user"],
                     "ws_path": "/api2/json" + base + "/vncwebsocket", "host": rs.get("host") or c.active_host,
                     "api_port": srv.get("port", 8006), "verify_ssl": srv.get("verify_ssl", False),
                     "header": "Cookie: " + rootsession.cookie(rs)})
        if not sess["port"] or not sess["ticket"]:
            return jsonify({"error": "Proxmox gaf geen console terug"}), 502
    else:
        base = "/nodes/{}".format(q(node)) + ("" if kind == "shell" else "/{}/{}".format(kind, vmid))
        try:
            if kind == "qemu":
                res = c.post(base + "/vncproxy", {"websocket": 1}) or {}
                sess["proto"] = "vnc"
            else:
                res = c.post(base + "/termproxy") or {}
                sess["proto"] = "term"
        except PvePermissionError as e:
            need = "VM.Console" if kind != "shell" else "Sys.Console"
            return jsonify({"error": "Het API-token mist het recht {} voor de console ({}). Oplossen: Servers & verbinding › "
                                     "Bewerken › Verbinding testen.".format(need, e)}), 403
        except PveError as e:
            return jsonify({"error": "Proxmox: {}".format(e)}), 502
        srv = col.server_cfg(server) or {}
        sess.update({"port": res.get("port"), "ticket": res.get("ticket"), "user": res.get("user"),
                     "ws_path": "/api2/json" + base + "/vncwebsocket", "host": c.active_host,
                     "api_port": srv.get("port", 8006), "verify_ssl": srv.get("verify_ssl", False),
                     "header": "Authorization: PVEAPIToken={}={}".format(srv.get("token_id"), srv.get("token_secret"))})
        if not sess["port"] or not sess["ticket"]:
            return jsonify({"error": "Proxmox gaf geen console terug"}), 502
    with _lock:
        SESSIONS[sid] = sess
    _audit({"who": g.who, "action": "console", "server": server, "node": node, "vmid": vmid,
            "label": "Console geopend: " + title + (" (als root)" if sess["root"] else ""), "ok": True})
    out = {"id": sid, "proto": sess["proto"], "title": title, "root": sess["root"]}
    if sess["proto"] == "vnc":
        out["password"] = sess["ticket"]   # noVNC gebruikt het ticket als wachtwoord
    return jsonify(out)


# ------------------------------------------------------------------ root-sessie
@bp.get("/api/console/root/<server>")
def api_root_status(server):
    if g.get("scope") != "admin":
        return jsonify({"error": "alleen voor beheerder"}), 403
    grp = _col().group(server)
    return jsonify({"session": rootsession.status(server), "name": grp["name"] if grp else server,
                    "kind": grp["kind"] if grp else None})


@bp.post("/api/console/root/<server>")
def api_root_login(server):
    if g.get("scope") != "admin":
        return jsonify({"error": "alleen voor beheerder"}), 403
    c = _col().client(server)
    if not c:
        return jsonify({"error": "Onbekende server"}), 404
    key = "root:" + server
    wait = root_limiter.locked_for(key)
    if wait:
        return jsonify({"error": "Te vaak een fout wachtwoord. Probeer het over {} s opnieuw.".format(int(wait) + 1)}), 429
    body = request.get_json(silent=True) or {}
    user = str(body.get("username") or "root@pam")[:64]
    if not body.get("password"):
        return jsonify({"error": "Vul het wachtwoord in"}), 400
    try:
        res = rootsession.login(c, server, user, str(body["password"]), body.get("otp"), who=g.who)
    except PvePermissionError as e:
        left, locked = root_limiter.fail(key, 60)
        _audit({"who": g.who, "action": "root_login", "server": server, "ok": False, "error": str(e)})
        return jsonify({"error": "{}.".format(str(e)[:1].upper() + str(e)[1:]) + ("" if locked else " Nog {} {}.".format(left, "poging" if left == 1 else "pogingen"))}), 401
    except PveError as e:
        return jsonify({"error": "Proxmox: {}".format(e)}), 502
    if res.get("need_tfa"):
        return jsonify({"need_tfa": True})
    root_limiter.reset(key)
    _audit({"who": g.who, "action": "root_login", "server": server, "ok": True,
            "label": "Root-sessie gestart voor {} ({})".format(server, res.get("user"))})
    return jsonify({"ok": True, "session": rootsession.status(server)})


@bp.delete("/api/console/root/<server>")
def api_root_end(server):
    if g.get("scope") != "admin":
        return jsonify({"error": "alleen voor beheerder"}), 403
    rootsession.end(server)
    _audit({"who": g.who, "action": "root_logout", "server": server, "ok": True, "label": "Root-sessie beëindigd: " + server})
    return jsonify({"ok": True})


# ------------------------------------------------------------------ websocket
def _origin_ok():
    origin = request.headers.get("Origin")
    if not origin:
        return True
    return urlparse(origin).netloc == request.host


def _upstream(sess):
    url = "wss://{}:{}{}?port={}&vncticket={}".format(sess["host"], sess["api_port"], sess["ws_path"],
                                                      sess["port"], quote(sess["ticket"], safe=""))
    sslopt = {} if sess["verify_ssl"] else {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}
    return websocket.create_connection(url, header=[sess["header"]], sslopt=sslopt,
                                       timeout=10, subprotocols=["binary"], enable_multithread=True)


def _keepalive(up, proto, stop):
    """Houd de verbinding met Proxmox levend, ook als er niemand typt."""
    while not stop.wait(20):
        try:
            if proto == "term":
                up.send("2")          # ping volgens het termproxy-protocol
            else:
                up.ping()             # websocket-ping voor het VM-scherm
        except Exception:  # verbinding is al weg; de pomp ruimt op
            return


def _pump_up_to_browser(up, ws, stop):
    try:
        while not stop.is_set():
            opcode, data = up.recv_data()
            if opcode == websocket.ABNF.OPCODE_CLOSE:
                break
            if opcode in (websocket.ABNF.OPCODE_BINARY, websocket.ABNF.OPCODE_TEXT):
                ws.send(bytes(data))
    except Exception as e:  # verbinding dicht of verbroken
        log.debug("console upstream gestopt: %s", e)
    finally:
        stop.set()
        try:
            ws.close(reason=1000, message="Console gesloten")
        except Exception:
            pass


@sock.route("/ws/console/<sid>")
def ws_console(ws, sid):
    if g.get("scope") != "admin" or not _origin_ok():
        ws.close(reason=1008, message="Niet toegestaan")
        return
    with _lock:
        sess = SESSIONS.pop(sid, None)   # elke sessie maar één keer te gebruiken
    if not sess or time.time() - sess["created"] > TTL:
        ws.close(reason=1008, message="Console-sessie verlopen; open de console opnieuw")
        return
    if sess["demo"]:
        return demo_terminal(ws, sess)
    try:
        up = _upstream(sess)
    except Exception as e:
        log.warning("console: kan geen verbinding maken met %s: %s", sess["host"], e)
        ws.close(reason=1011, message="Proxmox-console niet bereikbaar: {}".format(str(e)[:80]))
        return
    try:
        if sess["proto"] == "term":
            up.send("{}:{}\n".format(sess["user"], sess["ticket"]))
            first = up.recv()   # nog met time-out van 10 s: Proxmox moet meteen OK zeggen
            if isinstance(first, bytes):
                first = first.decode("utf-8", "replace")
            if not str(first).startswith("OK"):
                ws.close(reason=1011, message="Proxmox weigerde de console")
                return
        # Daarna géén time-out meer: een console kan minutenlang stil zijn (inlogprompt, lezen, apt die rekent).
        up.settimeout(None)
        stop = threading.Event()
        threading.Thread(target=_keepalive, args=(up, sess["proto"], stop), daemon=True).start()
        t = threading.Thread(target=_pump_up_to_browser, args=(up, ws, stop), daemon=True)
        t.start()
        while not stop.is_set():
            msg = ws.receive(timeout=1)
            if msg is None:
                continue
            if isinstance(msg, (bytes, bytearray)):
                up.send_binary(bytes(msg))
            else:
                up.send(msg)
    except Exception as e:
        log.debug("console browser gestopt: %s", e)
    finally:
        try:
            up.close()
        except Exception:
            pass


# ------------------------------------------------------------------ demo
DEMO_FILES = "docker-compose.yml  data  backups  notes.txt"


def _demo_apt(sess, cmd):
    """Nagebootste apt in de demo. Geeft (tekst, wacht_op_bevestiging)."""
    from . import demo as demo_mod
    node = sess["node"]
    out = ""
    first = re.split(r"&&|;", cmd)[0]
    if "update" in first and "upgrade" not in first:
        out += ("Hit:1 http://deb.debian.org/debian trixie InRelease\n"
                "Hit:2 http://download.proxmox.com/debian/pve trixie InRelease\n"
                "Reading package lists... Done\n")
    if "upgrade" not in cmd:
        return out, False
    if sess["kind"] == "lxc":
        vmid = sess["vmid"]
        with demo_mod._lock:
            pm, n, sec, os_, apps = demo_mod.DEMO_CT_UPDATES.get(vmid, ("apt", 0, 0, "", {}))
            if vmid in demo_mod.STATE["upgraded_cts"].get(node, set()):
                n = 0
        if not n:
            return out + "Calculating upgrade... Done\n0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.\n", False
        return out + ("Reading package lists... Done\nCalculating upgrade... Done\n{} upgraded, 0 newly installed, 0 to remove and 0 not upgraded.\n"
                      "Do you want to continue? [Y/n] ").format(n), True
    if sess["kind"] != "shell":
        return out + "E: niet beschikbaar in de demo\n", False
    with demo_mod._lock:
        spec = demo_mod.NODES.get(sess["server"], (False, {}))[1].get(node) or {}
        n = 0 if node in demo_mod.STATE["upgraded"] else spec.get("updates", 0)
    if not n:
        return out + "Reading package lists... Done\nCalculating upgrade... Done\n0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.\n", False
    return out + ("Reading package lists... Done\nBuilding dependency tree... Done\nCalculating upgrade... Done\n"
                  "The following packages will be upgraded:\n  pve-manager proxmox-kernel-6.14 openssl libc6 qemu-server ...\n"
                  "{} upgraded, 1 newly installed, 0 to remove and 0 not upgraded.\n"
                  "Need to get 182 MB of archives.\nDo you want to continue? [Y/n] ").format(n), True


def _demo_upgrade_run(sess):
    from . import demo as demo_mod
    if sess["kind"] == "lxc":
        with demo_mod._lock:
            demo_mod.STATE["upgraded_cts"].setdefault(sess["node"], set()).add(sess["vmid"])
        return "Unpacking ...\nSetting up ...\n\x1b[33mDemo: pakketten bijgewerkt.\x1b[0m\n"
    demo_mod.upgrade(sess["server"], sess["node"])
    return ("Get:1 http://download.proxmox.com/debian/pve trixie/pve-no-subscription amd64 proxmox-kernel-6.14.11-1-pve-signed\n"
            "Fetched 182 MB in 6s (30.3 MB/s)\n"
            "Setting up proxmox-kernel-6.14.11-1-pve-signed (6.14.11-1) ...\n"
            "Setting up pve-manager (9.0.11) ...\n"
            "Processing triggers for initramfs-tools ...\n"
            "\x1b[33mDemo: klaar. Er is een nieuwe kernel geïnstalleerd; herstart de node om die te gebruiken.\x1b[0m\n")


def demo_terminal(ws, sess):
    """Een nagebootste shell, zodat je de console in de demomodus kunt uitproberen."""
    host = sess["node"] if sess["kind"] == "shell" else (sess["title"].split(" ", 2)[-1] or "ct")
    prompt = "\x1b[1;32mroot@{}\x1b[0m:\x1b[1;34m~\x1b[0m# ".format(host)
    started = time.time()

    def out(text):
        ws.send(text.replace("\n", "\r\n").encode("utf-8"))

    login = "user" if sess.get("needs_login") else None   # demo: zonder root-sessie eerst een Linux-login
    if login:
        out("\nDebian GNU/Linux 13 {0} pts/0\n\n{0} login: ".format(host))
    else:
        out("\x1b[33mPVE Fleet demo-console\x1b[0m — {}{}\nDit is een nagebootste shell. Probeer: help, uptime, ls, df -h, free -h, exit\n\n".format(
            sess["title"], " (root-sessie, geen login nodig)" if sess.get("root") else ""))
        out(prompt)
    line = ""
    confirm = None    # wacht op Y/n bij de demo-upgrade
    while True:
        try:
            msg = ws.receive(timeout=60)
        except Exception:
            return
        if msg is None:
            continue
        if isinstance(msg, (bytes, bytearray)):
            msg = msg.decode("utf-8", "replace")
        if not msg.startswith("0:"):
            continue   # 1:cols:rows: (formaat) en 2 (ping) negeren
        try:
            data = msg.split(":", 2)[2]
        except IndexError:
            continue
        data = re.sub(r"\x1b(\[[0-9;]*[A-Za-z~]|O[A-Za-z])", "", data)   # pijltjes e.d. negeren
        for ch in data:
            if ch in ("\r", "\n"):
                out("\n")
                cmd = line.strip()
                line = ""
                if login == "user":
                    login = "pass"
                    out("Password: ")
                    continue
                if login == "pass":
                    login = None
                    out("Linux {} 6.14.8-2-pve (demo)\n\n\x1b[33mPVE Fleet demo-console\x1b[0m — ingelogd. Tip: met een root-sessie sla je deze login over.\n\n".format(host))
                    out(prompt)
                    continue
                if confirm is not None:
                    if cmd.lower() in ("", "y", "j", "yes", "ja"):
                        out(_demo_upgrade_run(sess))
                    else:
                        out("Abort.\n")
                    confirm = None
                    out(prompt)
                    continue
                if re.match(r"^(apt(-get)? update)?( *(?:&&|;) *)?(apt(-get)? (dist-upgrade|full-upgrade|upgrade))?$", cmd) and cmd:
                    text, ask = _demo_apt(sess, cmd)
                    out(text)
                    if ask:
                        confirm = True
                        continue
                    out(prompt)
                    continue
                if cmd == "update" and sess["kind"] == "lxc":
                    from . import demo as demo_mod
                    with demo_mod._lock:
                        ent = demo_mod.DEMO_CT_UPDATES.get(sess["vmid"])
                        changed = []
                        if ent:
                            for app_name in list(ent[4]):
                                for repo, latest in demo_mod.DEMO_LATEST.items():
                                    if repo.split("/")[1].lower().startswith(app_name.split("_")[0][:5]) and latest != ent[4][app_name]:
                                        ent[4][app_name] = latest
                                        changed.append("{} -> {}".format(app_name, latest))
                    out(("\x1b[32m  ✔ Updated {}\x1b[0m\n".format(", ".join(changed))) if changed else "  No update required. Already at latest version.\n")
                    out(prompt)
                    continue
                if cmd == "exit":
                    out("logout\n")
                    ws.close(reason=1000, message="Afgemeld")
                    return
                reply = {
                    "": "",
                    "help": "Beschikbaar in de demo: help, uptime, hostname, ls, df -h, free -h, whoami, date, exit\n",
                    "hostname": host + "\n",
                    "whoami": "root\n",
                    "date": time.strftime("%a %d %b %H:%M:%S %Z %Y") + "\n",
                    "ls": DEMO_FILES + "\n",
                    "uptime": " {}  up 41 days,  6:02,  1 user,  load average: 0.42, 0.51, 0.48\n".format(time.strftime("%H:%M:%S")),
                    "df -h": "Filesystem      Size  Used Avail Use% Mounted on\n/dev/root        16G  5.1G   11G  33% /\n",
                    "free -h": "               total        used        free\nMem:           2.0Gi       612Mi       1.4Gi\nSwap:          512Mi          0B       512Mi\n",
                }.get(cmd, "bash: {}: command not found (demo)\n".format(cmd.split()[0] if cmd else ""))
                out(reply)
                out(prompt)
            elif ch in ("\x7f", "\b"):
                if line:
                    line = line[:-1]
                    out("\b \b")
            elif ch == "\x03":
                line = ""
                out("^C\n" + prompt)
            elif ch == "\x1b":
                continue
            elif ch.isprintable():
                line += ch
                if login != "pass":
                    out(ch)
        if time.time() - started > 3600:
            ws.close(reason=1000, message="Demo-console na een uur gesloten")
            return
