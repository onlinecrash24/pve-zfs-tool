"""destroy_snapshot must NOT blindly `zfs destroy -R` when a snapshot has
dependent clones: that would recursively destroy user clones (live datasets/VMs
built from the snapshot). It may only auto-remove the tool's OWN restore clones
(<pool>/restore-*); any foreign clone must abort with an explanatory error."""

from app import zfs_commands as zc

SNAP = "tank/data@snap1"


def _fake(clones_stdout, clones_ok=True):
    calls = []

    def run(host, cmd, **kw):
        calls.append(cmd)
        if cmd.startswith("zfs get -H -o value clones"):
            return {"success": clones_ok, "stdout": clones_stdout, "stderr": ""}
        if cmd.startswith("zfs destroy -R"):
            return {"success": True, "stdout": "", "stderr": ""}
        if cmd.startswith("zfs destroy"):
            return {"success": False, "stderr": "cannot destroy: snapshot has dependent clones"}
        return {"success": True, "stdout": "", "stderr": ""}

    return run, calls


def test_refuses_foreign_clone(monkeypatch):
    run, calls = _fake("tank/vm-9-disk-0\n")
    monkeypatch.setattr(zc, "run_command", run)
    res = zc.destroy_snapshot({"address": "h"}, SNAP)
    assert res["success"] is False
    assert "vm-9-disk-0" in res["stderr"]
    assert not any(c.startswith("zfs destroy -R") for c in calls)   # never escalated


def test_removes_own_restore_clone(monkeypatch):
    run, calls = _fake("tank/restore-vm-1-abc\n")
    monkeypatch.setattr(zc, "run_command", run)
    monkeypatch.setattr(zc, "_invalidate", lambda h: None)
    res = zc.destroy_snapshot({"address": "h"}, SNAP)
    assert res["success"] is True
    assert any(c.startswith("zfs destroy -R") for c in calls)


def test_mixed_clones_refuse(monkeypatch):
    run, calls = _fake("tank/restore-x,tank/vm-9-disk-0")
    monkeypatch.setattr(zc, "run_command", run)
    res = zc.destroy_snapshot({"address": "h"}, SNAP)
    assert res["success"] is False
    assert not any(c.startswith("zfs destroy -R") for c in calls)


def test_clone_list_unavailable_does_not_escalate(monkeypatch):
    run, calls = _fake("", clones_ok=False)
    monkeypatch.setattr(zc, "run_command", run)
    res = zc.destroy_snapshot({"address": "h"}, SNAP)
    assert res["success"] is False
    assert not any(c.startswith("zfs destroy -R") for c in calls)


def test_clean_destroy_no_clones(monkeypatch):
    # snapshot destroys fine on the first try -> no clone lookup, no -R
    calls = []

    def run(host, cmd, **kw):
        calls.append(cmd)
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(zc, "run_command", run)
    monkeypatch.setattr(zc, "_invalidate", lambda h: None)
    res = zc.destroy_snapshot({"address": "h"}, SNAP)
    assert res["success"] is True
    assert calls == ["zfs destroy tank/data@snap1"]
