"""Automatic rolling update (0.9.7): node script 'nodeupgrade' and the runner, against the demo cluster.
Runs a full automatic rolling update over the 3 demo nodes (about 5 minutes)."""
import os, subprocess, sys, tempfile, time, logging
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)
logging.disable(logging.CRITICAL)
bad = 0
def t(n, ok):
    global bad
    print(("OK  " if ok else "FOUT"), n, flush=True); bad += 0 if ok else 1

from app import appdeploy
t("script version 4", appdeploy.SCRIPT_VERSION == 4 and "PVEFLEET-DEPLOY 4" in appdeploy.DEPLOY_SH and "ctshell)" in appdeploy.DEPLOY_SH)
t("deploy.sh knows nodeupgrade", "  nodeupgrade)" in appdeploy.DEPLOY_SH and "--force-confold dist-upgrade" in appdeploy.DEPLOY_SH)
t("deploy.sh has valid bash syntax", subprocess.run(["bash", "-n"], input=appdeploy.DEPLOY_SH, text=True).returncode == 0)

os.environ["PVEF_DATA"] = tempfile.mkdtemp(); os.environ["DEMO"] = "1"
from app import create_app, autorolling
autorolling.TIMING.update(poll=1.0, settle=1)
app = create_app(); col = app.extensions["collector"]
time.sleep(5)
grp = col.group("cluster-home")
home = {x["vmid"]: x["node"] for x in grp["guests"] if x["status"] == "running"}
stay = ["lxc/100"]
run = {"id": "t", "server": "cluster-home", "status": "running", "started": time.time(), "finished": None, "who": "test",
       "current": None, "error": None, "stop": False, "stay": stay,
       "nodes": [{"name": n["name"], "state": "wait", "step": None, "msg": None} for n in grp["nodes"]], "log": []}
autorolling.RUNS["cluster-home"] = run
autorolling.run_all(app, run)
t("run finished: %s %s" % (run["status"], run["error"]), run["status"] == "done")
msgs = [l["msg"] for l in run["log"]]
t("stayed guest started again", "Starten: CT 100 proxy" in msgs)
grp = col.group("cluster-home")
now = {x["vmid"]: (x["node"], x["status"]) for x in grp["guests"]}
t("every guest back on its own node and running", all(now[v] == (n, "running") for v, n in home.items()))
from app.updates import _node_row
t("no node needs work any more", not any(_node_row(grp, n)["count"] or _node_row(grp, n)["reboot_needed"] for n in grp["nodes"]))
with app.app_context():
    from app.updates import _plan
    t("plan: all nodes done", all(v.get("done") for v in _plan("cluster-home")["nodes"].values()))
sys.exit(1 if bad else 0)
