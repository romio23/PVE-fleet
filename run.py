"""Starten zonder Docker (Windows, macOS of Linux).

    python run.py            -> http://localhost:8080
    python run.py --demo     -> altijd met voorbeelddata
    python run.py --port 9000 --no-browser

In Docker gebruikt gunicorn 'run:app'; dit bestand werkt voor beide.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# De map 'data' naast dit bestand, ook als je het script vanuit een andere map start.
os.environ.setdefault("PVEF_DATA", os.path.join(HERE, "data"))


def _args():
    p = argparse.ArgumentParser(description="PVE Fleet dashboard")
    p.add_argument("--demo", action="store_true", help="start met voorbeelddata, ook als er een config.yml is")
    p.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"),
                   help="0.0.0.0 = bereikbaar vanaf andere apparaten (iPad); 127.0.0.1 = alleen deze pc")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    p.add_argument("--no-browser", action="store_true", help="niet automatisch de browser openen")
    return p.parse_args()


if __name__ == "__main__":
    args = _args()
    if args.demo:
        os.environ["DEMO"] = "1"

from app import create_app  # noqa: E402  (na het zetten van de omgeving)

app = create_app()

if __name__ == "__main__":
    url = "http://localhost:{}".format(args.port)
    print("")
    print("  PVE Fleet draait op {}".format(url))
    if args.host == "0.0.0.0":
        print("  Vanaf een ander apparaat (iPad): http://<ip-van-deze-pc>:{}/kiosk".format(args.port))
    print("  Config: {}".format(os.path.join(os.environ["PVEF_DATA"], "config.yml")))
    print("  Stoppen: Ctrl+C of dit venster sluiten")
    print("")
    if not args.no_browser:
        import threading
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    # Threaded server van Werkzeug: ondersteunt websockets (nodig voor de ingebouwde console).
    # Prima voor gebruik in je eigen netwerk; in Docker draait gunicorn.
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)   # geen regel per verzoek in het venster
    from werkzeug.serving import run_simple
    from app import tlsserver
    tlsserver.enable(app, args.host)      # https naast http, zodra er een certificaat is (Certificaten)
    if tlsserver.running():
        print("  https: https://<naam>:{}".format(tlsserver.port()))
    run_simple(args.host, args.port, app, threaded=True, use_reloader=False, use_debugger=False)
