"""De lijst met community-scripts-apps moet in de repository staan (eerder stond hij ten onrechte in .gitignore)."""
import json, os, subprocess, sys
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)
p = os.path.join(root, "app", "data", "community_apps.json")
bad = 0
def t(n, ok):
    global bad
    print(("OK  " if ok else "FOUT"), n); bad += 0 if ok else 1
d = json.load(open(p, encoding="utf-8"))
t("community_apps.json bestaat en heeft meer dan 300 apps", len(d) > 300)
from app import guestupdates as g
r = g.describe_apps({"2fauth": "5.0.0"}, "2fauth", None, demo=True)
t("een bekende app wordt herkend", bool(r) and r[0]["repo"])
try:
    ig = subprocess.run(["git", "check-ignore", "-q", p], cwd=root).returncode
    t("het bestand wordt niet door .gitignore genegeerd", ig == 1)
except FileNotFoundError:
    pass
sys.exit(1 if bad else 0)
