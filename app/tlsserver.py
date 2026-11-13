"""https-server van PVE Fleet, naast de gewone http-poort.

Draait in hetzelfde proces (dus dezelfde collector en sessies) als een tweede
Werkzeug-server met TLS. Een nieuw certificaat wordt meteen gebruikt voor nieuwe
verbindingen; herstarten is niet nodig. Alleen actief als PVE Fleet via run.py start
(Windows en Docker); onder een andere WSGI-server doet dit niets.
"""
import logging
import os
import ssl
import threading

log = logging.getLogger(__name__)
_state = {"app": None, "host": "0.0.0.0", "server": None, "thread": None, "port": None, "ctx": None, "error": None}
_lock = threading.Lock()


def enable(app, host="0.0.0.0"):
    _state["app"], _state["host"] = app, host
    reload()


def running():
    return _state["server"] is not None


def error():
    return _state["error"]


def port():
    return _state["port"]


def _stop():
    srv = _state["server"]
    if srv is not None:
        try:
            srv.shutdown()
            srv.server_close()
        except Exception:  # pragma: no cover
            pass
    _state.update(server=None, thread=None, port=None, ctx=None)


def reload():
    app = _state["app"]
    if app is None:
        return
    with _lock:
        store = app.extensions["certs"]
        crt, key = store.fleet_paths()
        want = int(store.data["fleet"].get("https_port") or 8443)
        if not (os.path.exists(crt) and os.path.exists(key)):
            _stop()
            _state["error"] = None
            return
        try:
            if _state["server"] is not None and _state["port"] == want:
                _state["ctx"].load_cert_chain(crt, key)      # nieuw certificaat voor nieuwe verbindingen
                _state["error"] = None
                return
            _stop()
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(crt, key)
            from werkzeug.serving import make_server
            srv = make_server(_state["host"], want, app, threaded=True, ssl_context=ctx)
            t = threading.Thread(target=srv.serve_forever, name="https", daemon=True)
            t.start()
            _state.update(server=srv, thread=t, port=want, ctx=ctx, error=None)
            log.info("https actief op poort %s", want)
        except OSError as e:
            _stop()
            _state["error"] = "Poort {} is niet beschikbaar: {}".format(want, e.strerror or e)
            log.warning("https: %s", _state["error"])
        except ssl.SSLError as e:
            _stop()
            _state["error"] = "Certificaat kan niet geladen worden: {}".format(e)
            log.warning("https: %s", _state["error"])
