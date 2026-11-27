"""apt over IPv4 and a clear message when the package sources cannot be reached (1.0.0)."""
import logging, os, stat, subprocess, sys, tempfile
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)
logging.disable(logging.CRITICAL)
bad = 0
def t(n, ok):
    global bad
    print(("OK  " if ok else "FOUT"), n, flush=True); bad += 0 if ok else 1

from app import appdeploy, collector, guestupdates
from app.pve import PveError

# ---- the scripts
sh = appdeploy.DEPLOY_SH
t("deploy.sh: every apt-get in upgrade and nodeupgrade is forced to IPv4",
  sh.count("Acquire::ForceIPv4=true") == 4 and all("Acquire::ForceIPv4=true" in ln for ln in sh.splitlines()
      if ("apt-get " in ln and ("update -q" in ln or "dist-upgrade" in ln) and not ln.strip().startswith(("echo", "[")))))
t("check.sh (containers) refreshes its package list over IPv4", "apt-get -o Acquire::ForceIPv4=true update" in guestupdates.CHECK_SH)
t("script version bumped so nodes get the new script", appdeploy.SCRIPT_VERSION == 5 and "PVEFLEET-DEPLOY 5" in sh)

def run_nodeupgrade(fake_apt):
    d = tempfile.mkdtemp(); bin_ = os.path.join(d, "bin"); os.makedirs(bin_)
    log = os.path.join(d, "calls.log")
    for name, body in (("apt-get", fake_apt.replace("@LOG@", log)), ("pct", "#!/bin/sh\nexit 0\n")):
        f = os.path.join(bin_, name); open(f, "w").write(body); os.chmod(f, 0o755)
    script = os.path.join(d, "deploy.sh"); open(script, "w").write(sh)
    env = dict(os.environ, PATH=bin_ + ":" + os.environ["PATH"], SSH_ORIGINAL_COMMAND="nodeupgrade")
    r = subprocess.run(["bash", script], env=env, capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout + r.stderr, open(log).read() if os.path.exists(log) else ""

NET_FAIL = """#!/bin/sh
echo "$@" >> @LOG@
case "$*" in
  *update*) echo "Err:1 http://download.proxmox.com/debian/pve trixie InRelease"
            echo "  Cannot initiate the connection to download.proxmox.com:80 (2a0b:7140:5:100::167). - connect (101: Network is unreachable)"
            exit 100 ;;
esac
exit 0
"""
rc, out, calls = run_nodeupgrade(NET_FAIL)
t("unreachable package sources: stops with a clear message, nothing is upgraded",
  rc == 2 and "PVEFLEET-FAIL pakketlijst verversen mislukt" in out and "dist-upgrade" not in calls and "Acquire::ForceIPv4=true" in calls and "PVEFLEET-OK" not in out)
REPO_401 = """#!/bin/sh
echo "$@" >> @LOG@
case "$*" in
  *update*) echo "Err:2 https://enterprise.proxmox.com/debian/pve trixie InRelease"; echo "  401  Unauthorized [IP: 1.2.3.4 443]"; exit 100 ;;
esac
exit 0
"""
rc, out, calls = run_nodeupgrade(REPO_401)
t("a repository error (enterprise without subscription) still continues, as before",
  rc == 0 and "toch doorgaan" in out and "dist-upgrade" in calls and "PVEFLEET-OK" in out)
OK = "#!/bin/sh\necho \"$@\" >> @LOG@\nexit 0\n"
rc, out, calls = run_nodeupgrade(OK)
t("normal run: update and dist-upgrade both over IPv4", rc == 0 and "PVEFLEET-OK" in out and calls.count("Acquire::ForceIPv4=true") == 2)

# ---- the dashboard notices a failed package-list refresh
class C:
    def __init__(self, tasks, log, fail=False): self.tasks, self.log, self.fail = tasks, log, fail
    def get(self, path, params=None):
        if self.fail: raise PveError("boom")
        if path.endswith("/tasks"): return self.tasks
        if path.endswith("/log"): return [{"n": i, "t": ln} for i, ln in enumerate(self.log)]
        raise PveError("nope")
NETLOG = ["starting apt-get update", "Err:1 http://download.proxmox.com/debian/pve trixie InRelease",
          "  Cannot initiate the connection to download.proxmox.com:80 (2a0b:7140:5:100::167). - connect (101: Network is unreachable)", "TASK ERROR: command 'apt-get update' failed: exit code 100"]
bad_task = [{"upid": "UPID:n:1", "starttime": 200, "status": "command 'apt-get update' failed: exit code 100"}, {"upid": "UPID:n:0", "starttime": 100, "status": "OK"}]
t("newest apt update failed on the network: flagged", collector.apt_net_failure(C(bad_task, NETLOG), "pvehp01") == collector.APT_NET_MSG)
t("newest apt update OK (an older one failed): not flagged", collector.apt_net_failure(C([dict(bad_task[0], starttime=50), dict(bad_task[1], starttime=100)], NETLOG), "pvehp01") is None)
t("401 from the enterprise repo is not a network problem", collector.apt_net_failure(C(bad_task, ["Err:2 https://enterprise.proxmox.com", "  401  Unauthorized"]), "n") is None)
t("no tasks, or the API fails: no flag, no crash", collector.apt_net_failure(C([], []), "n") is None and collector.apt_net_failure(C(bad_task, NETLOG, fail=True), "n") is None)
t("the task named in a refresh is checked directly", collector.apt_net_failure(C([], NETLOG), "n", "UPID:n:9") == collector.APT_NET_MSG)

print("\n%d fout(en)" % bad if bad else "\nAlles OK")
sys.exit(1 if bad else 0)
