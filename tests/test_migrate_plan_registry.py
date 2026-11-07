"""The migration plan stays on the server (review of 18.10., findings 5 and 8).

The preflight decides which datasets move where. That plan used to go to the
browser and come back with every later step, and the server acted on what came
back: `zfs recv -F` into any dataset, `zfs destroy -r` on any list of valid
names, a storage/bridge mapping pasted into the guest config as is. Now the
browser holds a plan id; each step looks the plan up for exactly the host pair
it was made for, and the storage mapping is derived, the bridge mapping
checked against what the preflight saw.
"""

import pytest

import app.main as m
from app import migrate as mg

SRC = {"address": "10.0.0.1", "name": "pve1"}
TGT = {"address": "10.0.0.2", "name": "pve2"}
HOSTS = {"10.0.0.1": SRC, "10.0.0.2": TGT, "10.0.0.3": {"address": "10.0.0.3"}}

QEMU_CFG = """scsi0: local-zfs:vm-100-disk-0,size=32G
net0: virtio=AA:BB:CC:11:22:33,bridge=vmbr0
"""


@pytest.fixture(autouse=True)
def clean_registry():
    mg._PLANS.clear()
    yield
    mg._PLANS.clear()


@pytest.fixture
def world(monkeypatch):
    """A preflight against stubbed hosts: one disk, empty target, SSH pull."""
    monkeypatch.setattr(mg, "read_guest_config",
                        lambda h, v, g: {"success": True, "content": QEMU_CFG,
                                         "path": "/etc/pve/qemu-server/100.conf"})
    monkeypatch.setattr(mg, "discover_guest_disks",
                        lambda h, v, g, config_text=None: {
                            "success": True, "unresolved": [],
                            "disks": [{"key": "scsi0", "volid": "local-zfs:vm-100-disk-0",
                                       "dataset": "rpool/data/vm-100-disk-0", "used": "10G"}]})
    monkeypatch.setattr(mg, "guest_is_running", lambda h, v, g: True)
    monkeypatch.setattr(mg, "vmid_in_use", lambda h, v: False)
    monkeypatch.setattr(mg, "dataset_exists", lambda h, d: d == "tank/data")
    monkeypatch.setattr(mg, "read_zfs_storages",
                        lambda h: [{"storage": "tank-zfs", "pool": "tank/data"}])
    monkeypatch.setattr(mg, "host_bridges", lambda h: ["vmbr0", "vmbr1"])
    monkeypatch.setattr(mg, "probe_ssh", lambda a, b: True)
    monkeypatch.setattr(mg, "list_snapshots", lambda h, d: [])


def _preflight(**over):
    kw = dict(new_vmid=None, target_storage="tank-zfs")
    kw.update(over)
    return mg.preflight(SRC, TGT, "100", "qemu", "tank/data", **kw)


# --- the registry -------------------------------------------------------------------

def test_the_preflight_hands_out_a_plan_id_and_keeps_the_plan(world):
    pf = _preflight()
    assert pf["can_precopy"] is True and pf["plan_id"]
    rec = mg.get_plan(pf["plan_id"], SRC, TGT)
    assert [p["target_dataset"] for p in rec["plan"]] == ["tank/data/vm-100-disk-0"]
    assert rec["vmid"] == "100" and rec["gtype"] == "qemu" and rec["pull"] is True
    assert rec["source_storages"] == ["local-zfs"] and rec["target_storage"] == "tank-zfs"


def test_a_plan_is_bound_to_its_host_pair(world):
    pid = _preflight()["plan_id"]
    assert mg.get_plan(pid, SRC, {"address": "10.0.0.3"}) is None
    assert mg.get_plan(pid, TGT, SRC) is None
    assert mg.get_plan("nope", SRC, TGT) is None


def test_a_plan_expires(world, monkeypatch):
    pid = _preflight()["plan_id"]
    import time as _t
    now = _t.time()
    monkeypatch.setattr(mg.time, "time", lambda: now + mg.PLAN_TTL_SECONDS + 1)
    assert mg.get_plan(pid, SRC, TGT) is None


def test_a_failed_preflight_still_registers_but_cannot_run(world, monkeypatch):
    monkeypatch.setattr(mg, "vmid_in_use", lambda h, v: True)      # e.g. after the cutover
    pf = _preflight()
    assert pf["can_cutover"] is False
    assert mg.get_plan(pf["plan_id"], SRC, TGT)["can_run"] is False


# --- storage and bridge mappings ---------------------------------------------------------

def test_the_storage_map_is_derived_from_the_checked_storage(world):
    rec = mg.get_plan(_preflight()["plan_id"], SRC, TGT)
    assert mg.derive_storage_map(rec) == {"local-zfs": "tank-zfs"}


def test_a_storage_the_target_does_not_have_is_refused():
    with pytest.raises(ValueError):
        mg.derive_storage_map({"target_storage": "evil", "target_storage_ids": ["tank-zfs"],
                               "source_storages": ["local-zfs"]})


@pytest.mark.parametrize("bmap,ok", [
    ({"vmbr0": "vmbr1"}, {"vmbr0": "vmbr1"}),
    ({"vmbr0": "vmbr0"}, {}),
    ({"vmbr9": "vmbr1"}, None),                 # not a bridge of the guest
    ({"vmbr0": "vmbr7"}, None),                 # not on the target
    ({"vmbr0": "vmbr1\nhookscript: local:snippets/x.sh"}, None),
    (["vmbr0"], None),
])
def test_bridge_choices_are_checked(bmap, ok):
    rec = {"bridges": ["vmbr0"], "target_bridges": ["vmbr0", "vmbr1"]}
    if ok is None:
        with pytest.raises(ValueError):
            mg.validate_bridge_map(bmap, rec)
    else:
        assert mg.validate_bridge_map(bmap, rec) == ok


def test_the_config_rewrite_refuses_a_name_with_a_newline():
    with pytest.raises(ValueError):
        mg.rewrite_guest_config(QEMU_CFG, {}, {"vmbr0": "vmbr1\nhookscript: x"})
    with pytest.raises(ValueError):
        mg.rewrite_guest_config(QEMU_CFG, {"local-zfs": "tank\n"}, {})
    out = mg.rewrite_guest_config(QEMU_CFG, {"local-zfs": "tank-zfs"}, {"vmbr0": "vmbr1"})
    assert "tank-zfs:vm-100-disk-0" in out and "bridge=vmbr1" in out


# --- the routes take an id and nothing else ------------------------------------------------

@pytest.fixture
def client(monkeypatch, world):
    monkeypatch.setattr(m, "audit_log", lambda *a, **k: None)
    monkeypatch.setattr(m, "_find_host", lambda addr: HOSTS.get(addr))
    m.app.config["TESTING"] = True
    c = m.app.test_client()
    with c.session_transaction() as s:
        s["authenticated"] = True
        s["sid"] = m.session_open("admin")
        s["csrf_token"] = "tok"

    def post(path, body):
        return c.post(path, json=body, headers={"X-CSRF-Token": "tok"})
    return post


def _pid(post):
    r = post("/api/migrate/preflight", {"source": "10.0.0.1", "target": "10.0.0.2",
                                        "vmid": "100", "gtype": "qemu",
                                        "target_root": "tank/data", "target_storage": "tank-zfs"})
    return r.get_json()["plan_id"]


@pytest.mark.parametrize("path", ["/api/migrate/precopy", "/api/migrate/cutover",
                                  "/api/migrate/rollback", "/api/migrate/snapshots",
                                  "/api/migrate/cleanup-snapshots", "/api/migrate/cleanup-source"])
def test_every_step_needs_a_known_plan_and_runs_nothing_without(client, monkeypatch, path):
    monkeypatch.setattr(mg, "run_command", lambda *a, **k: pytest.fail("must not run"))
    monkeypatch.setattr(mg, "start_task", lambda *a, **k: pytest.fail("must not start"))
    r = client(path, {"source": "10.0.0.1", "target": "10.0.0.2", "plan_id": "forged",
                      "plan": [{"source_dataset": "rpool/ROOT", "target_dataset": "tank/x"}],
                      "datasets": ["rpool/ROOT"], "vmid": "100"})
    assert r.status_code == 409 and r.get_json()["plan_expired"] is True


def test_a_plan_id_for_another_pair_is_refused(client, monkeypatch):
    pid = _pid(client)
    monkeypatch.setattr(mg, "start_task", lambda *a, **k: pytest.fail("must not start"))
    r = client("/api/migrate/precopy", {"source": "10.0.0.1", "target": "10.0.0.3", "plan_id": pid})
    assert r.status_code == 409


def test_cleanup_source_destroys_the_plan_datasets_not_the_request_list(client, monkeypatch):
    pid = _pid(client)
    seen = {}
    monkeypatch.setattr(mg, "cleanup_source",
                        lambda src, vmid, gtype, datasets: seen.update(vmid=vmid, ds=datasets) or {"success": True})
    r = client("/api/migrate/cleanup-source", {"source": "10.0.0.1", "target": "10.0.0.2",
                                               "plan_id": pid, "vmid": "999",
                                               "datasets": ["rpool/ROOT", "rpool/data"]})
    assert r.status_code == 200
    assert seen == {"vmid": "100", "ds": ["rpool/data/vm-100-disk-0"]}


def test_precopy_sends_the_plan_the_server_kept(client, monkeypatch):
    pid = _pid(client)
    seen = {}
    monkeypatch.setattr(mg, "start_task", lambda name, fn, *a, **k: seen.update(args=a) or "t1")
    r = client("/api/migrate/precopy", {"source": "10.0.0.1", "target": "10.0.0.2", "plan_id": pid,
                                        "plan": [{"source_dataset": "rpool/ROOT",
                                                  "target_dataset": "tank/evil"}], "pull": False})
    assert r.status_code == 200
    _src, _tgt, plan, pull = seen["args"]
    assert [p["target_dataset"] for p in plan] == ["tank/data/vm-100-disk-0"] and pull is True


def test_cutover_derives_storage_and_checks_bridges(client, monkeypatch):
    pid = _pid(client)
    seen = {}
    monkeypatch.setattr(mg, "start_task", lambda name, fn, *a, **k: seen.update(args=a) or "t1")
    base = {"source": "10.0.0.1", "target": "10.0.0.2", "plan_id": pid}
    bad = client("/api/migrate/cutover", dict(base, bridge_map={"vmbr0": "vmbr1\nhookscript: x"}))
    assert bad.status_code == 400 and "args" not in seen
    ok = client("/api/migrate/cutover", dict(base, bridge_map={"vmbr0": "vmbr1"},
                                             storage_map={"local-zfs": "evil"}, vmid="555"))
    assert ok.status_code == 200
    _s, _t, vmid, gtype, plan, pull, new_vmid, smap, bmap, start, timeout = seen["args"]
    assert vmid == "100" and smap == {"local-zfs": "tank-zfs"} and bmap == {"vmbr0": "vmbr1"}


def test_a_plan_with_errors_cannot_start(client, monkeypatch):
    monkeypatch.setattr(mg, "vmid_in_use", lambda h, v: True)
    pid = _pid(client)
    monkeypatch.setattr(mg, "start_task", lambda *a, **k: pytest.fail("must not start"))
    r = client("/api/migrate/cutover", {"source": "10.0.0.1", "target": "10.0.0.2", "plan_id": pid})
    assert r.status_code == 409


def test_the_preflight_names_its_guest_for_the_confirm_texts(world):
    assert _preflight()["vmid"] == "100"


def test_the_ui_drops_the_plan_when_the_selection_changes():
    # Review finding: rollback/cleanup/cutover confirmed the guest in the
    # select box but acted on the guest of the earlier preflight.
    import os
    import re
    js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "app", "static", "js", "app.js"), encoding="utf-8").read()
    assert re.search(r"\[srcSel, tgtSel, guestSel, rootSel, storSel\]\.forEach\(el => el\.addEventListener\(\"change\", _dropPlan\)\)", js)
    assert 'newVmidInp.addEventListener("input", _dropPlan)' in js
    for key in ("mig_rollback_confirm", "mig_cleanup_confirm", "mig_cutover_confirm"):
        line = next(ln for ln in js.splitlines() if key in ln)
        assert "pf.vmid" in line and "_guest()" not in line, line
