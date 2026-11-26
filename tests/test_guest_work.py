"""The Updates badge in the menu must count only what the Updates page shows (no leftovers of removed servers or containers)."""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import guestupdates
bad = 0
def t(n, ok):
    global bad; print(("OK  " if ok else "FOUT"), n); bad += 0 if ok else 1
def g(count=0, app=False): return {"count": count, "security": 1 if count else 0, "apps": [{"update": app}]}
guestupdates.STORE = types.SimpleNamespace(
    data={"results": {
        "proxs": {"guests": {"100": g(3), "101": g(0), "102": g(0, True), "999": g(5), "103": g(4)}},   # 999: container no longer exists; 103: is a VM now
        "oud": {"guests": {"100": g(9)}},                                                            # server no longer in the config
    }},
    settings=lambda s: {"enabled": True})
t("without a list everything counts (old behaviour)", guestupdates.work_stats()["containers"] == 5)
valid = {"proxs": {100, 101, 102}}
r = guestupdates.work_stats(valid)
t("only existing containers on configured servers: 100 (packages) + 102 (app)", r["containers"] == 2 and r["packages"] == 3 and r["apps"] == 1 and r["security"] == 1)
t("a server with no containers counts nothing", guestupdates.work_stats({"proxs": set()})["containers"] == 0)
sys.exit(1 if bad else 0)
