"""PVE Fleet — dashboard voor meerdere Proxmox VE-servers en clusters."""
import logging
import os
from datetime import timedelta

from flask import Flask, abort, g, jsonify, render_template

from . import actions, appdeploy, auth, backups, certs, console, guestupdates, servers_api, updates
from . import config as config_mod
from .collector import Collector
from .details import NotFound, guest_detail, node_detail
from .settings import SettingsStore

__version__ = "0.9.4"


def create_app(start_collector=True):
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    data_dir = os.environ.get("PVEF_DATA", "data")
    os.makedirs(data_dir, exist_ok=True)
    cfg = config_mod.load()
    store = SettingsStore(data_dir)

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=store.secret_key,
        PVEF_DATA=data_dir,
        SESSION_COOKIE_NAME="pvef_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        # websockets voor de ingebouwde console; noVNC vraagt om subprotocol 'binary'
        SOCK_SERVER_OPTIONS={"subprotocols": ["binary"], "ping_interval": 25, "max_message_size": 4 * 1024 * 1024},
    )
    collector = Collector(cfg, data_dir=data_dir)
    app.extensions["collector"] = collector
    app.extensions["settings"] = store
    auth.install(app)
    app.register_blueprint(actions.bp)
    app.register_blueprint(servers_api.bp)
    app.register_blueprint(console.bp)
    app.register_blueprint(updates.bp)
    app.register_blueprint(backups.bp)
    guestupdates.init_app(app)
    certs.init_app(app)
    appdeploy.init_app(app)
    console.sock.init_app(app)
    if start_collector:
        collector.start()      # eerste ronde start meteen op de achtergrond

    @app.context_processor
    def globals_():
        return {"version": __version__, "demo": cfg["demo"], "poll_ms": cfg["poll_interval"] * 1000,
                "user": g.get("who") if g.get("scope") == "admin" else None}

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    # ---------- pagina's ----------
    @app.get("/")
    def overview():
        return render_template("overview.html", page="overview")

    @app.get("/node/<server>/<node>")
    def node_page(server, node):
        if not collector.client(server):
            abort(404)
        return render_template("node.html", page="node", server=server, node=node,
                               is_cluster=(collector.group(server) or {}).get("kind") == "cluster")

    @app.get("/guest/<server>/<node>/<kind>/<int:vmid>")
    def guest_page(server, node, kind, vmid):
        if not collector.client(server) or kind not in ("qemu", "lxc"):
            abort(404)
        return render_template("guest.html", page="guest", server=server, node=node, kind=kind, vmid=vmid)

    @app.get("/servers")
    def servers_page():
        return render_template("servers.html", page="servers", servers=cfg["servers"],
                               config_path=config_mod.config_path())

    @app.get("/nodes")
    def nodes_page():
        return render_template("nodes.html", page="nodes")

    @app.get("/vms")
    def vms_page():
        return render_template("vms.html", page="vms")

    @app.get("/storage")
    def storage_page():
        return render_template("storage.html", page="storage")

    @app.get("/console/<server>/<node>")
    def console_page(server, node):
        if not collector.client(server):
            abort(404)
        from flask import request as _rq
        kind = _rq.args.get("type", "shell")
        vmid = _rq.args.get("vmid", "")
        if kind not in ("qemu", "lxc", "shell"):
            abort(404)
        cmds = {"apt": "apt update; apt dist-upgrade", "apk": "apk update && apk upgrade", "dnf": "dnf upgrade", "app": "update"}
        cmd = _rq.args.get("cmd", "")
        upcmd = None
        if kind == "shell" and _rq.args.get("upgrade") == "1":
            upcmd = cmds["apt"]
        elif kind == "lxc" and cmd in cmds:
            upcmd = cmds[cmd]
        return render_template("console.html", page="console", server=server, node=node, kind=kind, vmid=vmid,
                               upgrade=bool(upcmd), upcmd=upcmd, appupdate=(cmd == "app"))

    @app.get("/kiosk")
    def kiosk_page():
        return render_template("kiosk.html", page="kiosk")

    # ---------- API ----------
    @app.get("/api/fleet")
    def api_fleet():
        return jsonify(collector.get_snapshot())

    def _full_groups():
        with collector.lock:
            return list(collector.snapshot.get("_full", []))

    @app.get("/api/vms")
    @app.get("/api/kiosk/vms")   # ook voor gekoppelde schermen (alleen lezen)
    def api_vms():
        """Alle VM's & containers over alle servers heen."""
        out = []
        for g in _full_groups():
            node_status = {n["name"]: n["status"] for n in g["nodes"]}
            for x in g["guests"]:
                row = dict(x)
                row["group"] = g["name"]
                row["kind"] = g["kind"]
                row["reachable"] = g["reachable"] and node_status.get(x["node"]) == "online"
                row["upd"] = guestupdates.summary(g["server"], x["vmid"]) if x["type"] == "lxc" else None
                if not row["reachable"]:
                    row["status"] = "unknown"
                out.append(row)
        out.sort(key=lambda r: (r["group"], r["vmid"] or 0))
        return jsonify({"vms": out, "ready": collector.get_snapshot().get("ready", False)})

    @app.get("/api/storage")
    def api_storage():
        """Alle opslag; gedeelde opslag (bijv. NFS) één keer per cluster."""
        out, seen = [], set()
        for g in _full_groups():
            for st in g["storage"]:
                key = (g["server"], st["name"]) if st["shared"] else (g["server"], st["node"], st["name"])
                if key in seen:
                    continue
                seen.add(key)
                row = dict(st)
                row["group"] = g["name"]
                row["kind"] = g["kind"]
                row["where"] = g["name"] if st["shared"] else st["node"]
                row["reachable"] = g["reachable"]
                out.append(row)
        out.sort(key=lambda r: -(r["pct"] or 0))
        return jsonify({"storage": out, "ready": collector.get_snapshot().get("ready", False)})

    @app.get("/api/node/<server>/<node>")
    def api_node(server, node):
        try:
            return jsonify(node_detail(collector, server, node))
        except NotFound as e:
            return jsonify({"error": "niet gevonden: {}".format(e)}), 404

    @app.get("/api/guest/<server>/<node>/<kind>/<int:vmid>")
    def api_guest(server, node, kind, vmid):
        try:
            return jsonify(guest_detail(collector, server, node, kind, vmid))
        except NotFound as e:
            grp = collector.group(server) or {}
            other = next((x for x in grp.get("guests", []) if x["vmid"] == vmid and x["type"] == kind), None)
            if other and other["node"] != node:   # net gemigreerd
                return jsonify({"error": "verplaatst", "moved_to": other["node"]}), 409
            return jsonify({"error": "niet gevonden: {}".format(e)}), 404

    @app.get("/healthz")
    def healthz():
        snap = collector.get_snapshot()
        return jsonify({"ok": snap.get("ready", False), "version": __version__, "demo": cfg["demo"]})

    @app.errorhandler(404)
    def not_found(e):
        return render_template("message.html", title="Niet gevonden",
                               text="Deze pagina bestaat niet (meer). Misschien is een VM of container net verplaatst."), 404

    return app
