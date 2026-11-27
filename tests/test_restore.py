"""Restore (1.0.0): helpers, the HTTP API in demo mode, and a restore to another cluster with a differently named storage."""
import logging, os, sys, tempfile, time
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)
logging.disable(logging.CRITICAL)
bad = 0
def t(n, ok):
    global bad
    print(("OK  " if ok else "FOUT"), n, flush=True); bad += 0 if ok else 1

os.environ["PVEF_DATA"] = tempfile.mkdtemp(); os.environ["DEMO"] = "1"
from app import create_app, restore as R
from app.pve import PveError

# ---- pure helpers
t("type from file name", R.volid_type("nfs:backup/vzdump-lxc-100-2026_11_27-02_00_00.tar.zst") == "lxc"
  and R.volid_type("nfs:backup/vzdump-qemu-101-2026_11_27.vma.zst") == "qemu"
  and R.volid_type("pbs:backup/ct/100/2026-11-27T01:00:00Z") == "lxc" and R.volid_type("pbs:backup/vm/101/2026-11-27T01:00:00Z") == "qemu"
  and R.volid_type("x:iso/foo.iso") is None and R.volid_type("x", "qemu") == "qemu")
t("relative volid ignores the storage name", R.rel_volid("syno:backup/vzdump-lxc-1-a.tar.zst") == R.rel_volid("nas-backup:backup/vzdump-lxc-1-a.tar.zst"))
conf = R.parse_config("#my note\narch: amd64\ncores: 2\nhostname: web\nmemory: 512\nnet0: name=eth0,bridge=vmbr0,hwaddr=BC:24:11:00:00:01,ip=192.168.1.5/24,tag=20,type=veth\n"
                      "net1: name=eth1,bridge=vmbr1,ip=dhcp\nrootfs: local-zfs:subvol-100-disk-0,size=8G\nunprivileged: 1\n[snap1]\nnet0: name=eth0,bridge=OLD\n")
t("config: stops at the first snapshot section, keeps the comment", conf["net0"].endswith("type=veth") and conf["hostname"] == "web" and conf["#comment"] == "my note")
s = R.summarize(conf, "lxc")
t("summary: nets, disks, privilege", [n["key"] for n in s["nets"]] == ["net0", "net1"] and s["nets"][0]["bridge"] == "vmbr0" and s["nets"][0]["tag"] == "20"
  and s["nets"][0]["ip"] == "192.168.1.5/24" and s["disks"] == [{"key": "rootfs", "size": "8G", "storage": "local-zfs"}] and s["unprivileged"] is True and s["memory"] == 512)
t("edit_net: only the bridge changes, MAC/IP/tag kept", R.edit_net(conf["net0"], "vmbr9") == "name=eth0,bridge=vmbr9,hwaddr=BC:24:11:00:00:01,ip=192.168.1.5/24,tag=20,type=veth")
t("edit_net: new tag, tag removed, tag added", "tag=30" in R.edit_net(conf["net0"], None, "30") and "tag=" not in R.edit_net(conf["net0"], None, "")
  and R.edit_net("name=eth0,bridge=vmbr0", None, "40") == "name=eth0,bridge=vmbr0,tag=40")
q = "virtio=BC:24:11:2A:01:01,bridge=vmbr0,firewall=1"
t("edit_net on a VM line keeps the model and MAC", R.edit_net(q, "vmbr2") == "virtio=BC:24:11:2A:01:01,bridge=vmbr2,firewall=1"
  and R.net_info("net0", q)["mac"] == "BC:24:11:2A:01:01" and R.net_info("net0", q)["name"] == "virtio")
t("bind mounts are recognised", R.summarize({"mp0": "/srv/data,mp=/data", "rootfs": "local:1,size=2G"}, "lxc")["bind_mounts"] == ["mp0"])
try:
    R._clean_tag("5000"); ok = False
except ValueError:
    ok = True
t("tag out of range is refused", ok and R._clean_tag("") == "" and R._clean_tag(None) is None and R._clean_tag("20") == "20")

# ---- HTTP API in demo mode
R.POLL_SECONDS = 0.3
app = create_app(start_collector=False); app.testing = True
col = app.extensions["collector"]; store = app.extensions["settings"]; store.set_admin("admin", "pw-1234567")
col.poll_all()
c = app.test_client(); H = {"X-PVEF": "1"}
t("page needs login", c.get("/restore").status_code == 302 and c.get("/api/restore/targets").status_code == 401)
with c.session_transaction() as s:
    s["user"] = "admin"; s["stamp"] = store.admin_stamp()
t("page loads", c.get("/restore").status_code == 200 and b"restore.js" in c.get("/restore").data)
tg = c.get("/api/restore/targets").get_json()["targets"]
home = [x for x in tg if x["server"] == "cluster-home"][0]
t("targets list the online nodes", home["reachable"] and len(home["nodes"]) >= 2)
bk = c.get("/api/restore/backups/cluster-home").get_json()
lxc = [b for b in bk["backups"] if b["type"] == "lxc"]
t("backups: more than the latest per guest, typed, newest first", len(bk["backups"]) > 10 and lxc and all(b["volid"] for b in bk["backups"])
  and [b["ctime"] for b in bk["backups"] if b["vmid"] == lxc[0]["vmid"]] == sorted([b["ctime"] for b in bk["backups"] if b["vmid"] == lxc[0]["vmid"]], reverse=True))
b = lxc[0]
node = home["nodes"][0]
r = c.get("/api/restore/prepare", query_string={"src": "cluster-home", "volid": b["volid"], "vmid": b["vmid"], "type": "lxc", "target": "cluster-home", "node": node})
p = r.get_json()
t("prepare: config, bridges, storages, free id", r.status_code == 200 and p["config"]["nets"][0]["bridge"] == "vmbr0" and "vmbr1" in p["bridges"] and p["storages"]
  and p["nextid"] not in {int(k) for k in p["used_ids"]} and p["original"] and p["original"]["name"] == b["name"])
r = c.get("/api/restore/prepare", query_string={"src": "cluster-home", "volid": "nowhere:backup/vzdump-lxc-100-x.tar.zst", "target": "cluster-home", "node": node})
t("prepare: a backup the target cannot see gives a clear message", r.status_code == 404 and "dezelfde back-upopslag" in r.get_json()["error"])
body = {"src": "cluster-home", "volid": b["volid"], "type": "lxc", "src_vmid": b["vmid"], "name": b["name"], "target": "cluster-home", "node": node,
        "vmid": b["vmid"], "storage": p["storages"][0]["name"], "unique": True, "start": False, "nets": {}}
r = c.post("/api/restore/start", json=body, headers=H)
t("ID in use is refused (never overwrites)", r.status_code == 409 and "overschrijft nooit" in r.get_json()["error"])
r = c.post("/api/restore/start", json=dict(body, vmid=50), headers=H); t("ID below 100 refused", r.status_code == 400)
r = c.post("/api/restore/start", json=dict(body, vmid=p["nextid"], storage="bestaat-niet"), headers=H); t("unknown storage refused", r.status_code == 400)
r = c.post("/api/restore/start", json=dict(body, vmid=p["nextid"], nets={"net0": {"bridge": "vmbr99"}}), headers=H)
t("unknown bridge refused", r.status_code == 400 and "vmbr99" in r.get_json()["error"])
r = c.post("/api/restore/start", json=dict(body, vmid=p["nextid"], nets={"net0": {"tag": "9999"}}), headers=H); t("bad VLAN tag refused", r.status_code == 400)
t("without X-PVEF refused", c.post("/api/restore/start", json=body).status_code == 400)
new_id = p["nextid"]
r = c.post("/api/restore/start", json=dict(body, vmid=new_id, nets={"net0": {"bridge": "vmbr1", "tag": "30"}}, start=True), headers=H)
j = r.get_json()
t("restore started: task id returned", r.status_code == 200 and j["upid"].startswith("UPID") and j["node"] == node)
end = time.time() + 30
while time.time() < end:
    jobs = c.get("/api/restore/jobs").get_json()["jobs"]
    if jobs and jobs[0]["state"] in ("done", "warn", "error"): break
    time.sleep(0.3)
job = jobs[0]
t("job finished ok: %s %s" % (job["state"], job["message"]), job["state"] == "done" and "netwerk aangepast" in job["message"] and "gestart" in job["message"])
with app.app_context():
    from app import demo
    cfg = col.client("cluster-home").get("/nodes/{}/lxc/{}/config".format(node, new_id))
    t("bridge and VLAN were changed, MAC/IP kept", "bridge=vmbr1" in cfg["net0"] and "tag=30" in cfg["net0"] and "ip=192.168.1." in cfg["net0"] and "hwaddr=" in cfg["net0"])
    st = col.client("cluster-home").get("/nodes/{}/lxc/{}/status/current".format(node, new_id))
    t("and the new guest was started", st["status"] == "running")
r = c.post("/api/restore/start", json=dict(body, vmid=new_id), headers=H)
t("the new ID is now in use too", r.status_code == 409)

# ---- another cluster with a differently named storage: the file is found by its path
class Other:
    """Minimal fake of a second cluster that mounts the same NFS share under another storage name."""
    def __init__(self, items): self.items, self.posts, self.puts = items, [], []
    def get(self, path, params=None):
        if path.endswith("/content"): return [dict(i, volid="nas-backup:" + R.rel_volid(i["volid"])) for i in self.items if not (params or {}).get("vmid") or i["vmid"] == params["vmid"]]
        if path == "/cluster/nextid": return "300"
        if path.endswith("/network"): return [{"iface": "vmbr0", "type": "bridge"}, {"iface": "vmbr5", "type": "bridge"}]
        if path.endswith("/extractconfig"): return "hostname: web\nnet0: name=eth0,bridge=vmbr0,ip=dhcp\nrootfs: x:1,size=2G\nunprivileged: 1\n"
        if "/tasks/" in path and path.endswith("/status"): return {"status": "stopped", "exitstatus": "OK"}
        if path.endswith("/config"): return {"net0": "name=eth0,bridge=vmbr0,ip=dhcp"}
        raise PveError("nope " + path)
    def post(self, path, data=None):
        self.posts.append((path, data)); return "UPID:other:00000001:00000001:00000001:vzrestore:300:root@pam:"
    def put(self, path, data=None):
        self.puts.append((path, data)); return None
src_items = [i for i in bk["backups"] if i["vmid"] == b["vmid"]]
other = Other([{"volid": i["volid"], "vmid": i["vmid"], "ctime": i["ctime"]} for i in src_items])
grp_other = {"server": "other", "name": "other", "kind": "cluster", "reachable": True, "nodes": [{"name": "o1", "status": "online"}],
             "guests": [{"vmid": 105, "name": "x", "node": "o1", "type": "lxc", "status": "running"}],
             "storage": [{"name": "nas-backup", "node": "o1", "content": "backup", "status": "available", "shared": True, "type": "nfs", "pct": 1, "used": 1, "total": 10},
                         {"name": "local-lvm", "node": "o1", "content": "images,rootdir", "status": "available", "shared": False, "type": "lvmthin", "pct": 1, "used": 1, "total": 100}]}
orig_group, orig_client = col.group, col.client
col.group = lambda s: grp_other if s == "other" else orig_group(s)
col.client = lambda s: other if s == "other" else orig_client(s)
col.cfg["servers"] = list(col.cfg["servers"]) + [{"name": "other"}]
r = c.get("/api/restore/prepare", query_string={"src": "cluster-home", "volid": b["volid"], "vmid": b["vmid"], "type": "lxc", "target": "other", "node": "o1"})
p2 = r.get_json()
t("other cluster: found under its own storage name", r.status_code == 200 and p2["volid"].startswith("nas-backup:") and p2["bridges"] == ["vmbr0", "vmbr5"] and p2["nextid"] == 300)
r = c.post("/api/restore/start", json={"src": "cluster-home", "volid": b["volid"], "type": "lxc", "src_vmid": b["vmid"], "name": "web", "target": "other",
                                       "node": "o1", "vmid": 300, "storage": "local-lvm", "unique": True, "start": False, "nets": {"net0": {"bridge": "vmbr5"}}}, headers=H)
t("other cluster: restore is sent with the target's volume name, restore=1, unique, never force",
  r.status_code == 200 and other.posts and other.posts[0][0] == "/nodes/o1/lxc" and other.posts[0][1]["ostemplate"].startswith("nas-backup:")
  and other.posts[0][1]["restore"] == 1 and other.posts[0][1]["unique"] == 1 and other.posts[0][1]["start"] == 0 and "force" not in other.posts[0][1] and other.posts[0][1]["vmid"] == 300)
end = time.time() + 10
while time.time() < end and not other.puts: time.sleep(0.2)
t("other cluster: bridge set afterwards on the restored config", other.puts and other.puts[0][0] == "/nodes/o1/lxc/300/config" and other.puts[0][1] == {"net0": "name=eth0,bridge=vmbr5,ip=dhcp"})
r = c.post("/api/restore/start", json={"src": "cluster-home", "volid": "x:backup/vzdump-lxc-1-nope.tar.zst", "type": "lxc", "target": "other", "node": "o1", "vmid": 301, "storage": "local-lvm"}, headers=H)
t("other cluster: a backup it cannot see is refused", r.status_code == 404)
r = c.post("/api/restore/start", json={"src": "cluster-home", "volid": b["volid"], "type": "lxc", "target": "other", "node": "o1", "vmid": 105, "storage": "local-lvm"}, headers=H)
t("other cluster: ID in use there is refused", r.status_code == 409)

# ---- a permission error names the missing right with pveum commands
def perm(path, data=None): raise PveError("Permission check failed (/vms/300, VM.Allocate)")
other.post = perm
r = c.post("/api/restore/start", json={"src": "cluster-home", "volid": b["volid"], "type": "lxc", "src_vmid": b["vmid"], "target": "other", "node": "o1", "vmid": 302, "storage": "local-lvm"}, headers=H)
j = r.get_json()
t("permission error: names the right and gives commands", r.status_code == 403 and "VM.Allocate" in j["error"] and "pveum" in (j.get("commands") or ""))

# a refusal without a parsable path (generic 403) still gives the FleetRestore commands
from app.pve import PvePermissionError
def perm2(path, data=None): raise PvePermissionError("Permission check failed")
other.post = perm2
r = c.post("/api/restore/start", json={"src": "cluster-home", "volid": b["volid"], "type": "lxc", "src_vmid": b["vmid"], "target": "other", "node": "o1", "vmid": 303, "storage": "local-lvm"}, headers=H)
j = r.get_json()
cm = j.get("commands") or ""
t("generic permission error: gives the FleetRestore commands", r.status_code == 403 and "FleetRestore" in cm and "VM.Allocate" in cm and "--tokens" in cm and "root@pam" in j["error"])


# ---- connection test: optional restore line with a ready-made role
from app import servers_api
class PC:
    def __init__(self, privs): self.privs = privs
    def get(self, path, params=None):
        if path == "/access/permissions": return {"/": {k: 1 for k in self.privs}}
        return {"privsep": 1}
full = ["Sys.Audit", "VM.Audit", "Datastore.Audit", "VM.PowerMgmt", "VM.Snapshot", "VM.Snapshot.Rollback", "VM.Backup", "VM.Migrate",
        "Datastore.AllocateSpace", "Sys.PowerMgmt", "VM.Console", "Sys.Console", "VM.GuestAgent.Audit", "Sys.Modify"]
r = servers_api.check_permissions(PC(full), "dashboard@pve!fleet", "9.0")
rest = [x for x in r["perms"] if x["key"] == "restore"][0]
t("restore permission line is optional and reports what is missing", rest["optional"] and not rest["ok"] and "VM.Allocate" in rest["missing"])
t("role commands for restore (and not for jobs, which is present)", "FleetRestore" in r["optional_commands"] and "SDN.Use" in r["optional_commands"]
  and "FleetJobs" not in r["optional_commands"] and "--tokens 'dashboard@pve!fleet' --roles FleetRestore" in r["optional_commands"])
r = servers_api.check_permissions(PC(full + ["VM.Allocate", "VM.Config.Disk", "VM.Config.Network", "VM.Config.Options"]), "dashboard@pve!fleet", "9.0")
t("with the rights there is nothing left to suggest", not r["optional_commands"])

print("\n%d fout(en)" % bad if bad else "\nAlles OK")
sys.exit(1 if bad else 0)
