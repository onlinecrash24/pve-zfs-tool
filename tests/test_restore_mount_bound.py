"""The file browser may only look inside mounts this tool made.

_verify_under_mount proves a path stays under the mount it was handed --
which, when the mount is "/", is every path on the host. Nothing bounded the
mount itself, so ``mount_path=/&file=etc/hostname`` sent ``cat /etc/hostname``
to the host without a single ``..``, while README and FEATURES advertised
"path-traversal protection on the file browser".

The important assertion throughout is not the error message: it is that the
command list stays EMPTY. A refusal that has already talked to the host is a
different, weaker thing.
"""

import pytest

import app.main as m
import app.zfs_commands as z

R = z.RESTORE_MOUNT_BASE
V = z.ZVOL_MOUNT_BASE


@pytest.fixture
def issued(monkeypatch):
    """Record every command; answer realpath with the path itself."""
    cmds = []

    def run(host, cmd, timeout=30, cache_ttl=0):
        cmds.append(cmd)
        if cmd.startswith("realpath "):
            return {"success": True, "stdout": cmd.split(" ", 1)[1].strip("'") + "\n", "stderr": ""}
        if cmd.startswith("stat "):
            return {"success": True, "stdout": "12\n", "stderr": ""}
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(z, "run_command", run)
    return cmds


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(m, "audit_log", lambda *a, **k: None)
    monkeypatch.setattr(m, "send_notification", lambda *a, **k: None)
    monkeypatch.setattr(m, "_find_host", lambda a: {"address": "h", "name": "h"})
    c = m.app.test_client()
    with c.session_transaction() as s:
        s["authenticated"] = True
        s["csrf_token"] = "t"
    return c


ESCAPES = ["/", "/etc", "/tmp", R + "-evil/x", V + "-evil/x", R, V]


@pytest.mark.parametrize("mount", ESCAPES)
def test_browse_refuses_a_mount_outside_the_tool_s_bases(client, issued, mount):
    r = client.get(f"/api/restore/browse?host=h&mount_path={mount}&path=etc")
    assert r.get_json()["success"] is False
    assert issued == [], f"talked to the host for mount_path={mount!r}: {issued}"


@pytest.mark.parametrize("mount", ESCAPES)
def test_preview_refuses_a_mount_outside_the_tool_s_bases(client, issued, mount):
    r = client.get(f"/api/restore/preview?host=h&mount_path={mount}&file=etc/hostname")
    assert r.get_json()["success"] is False
    assert issued == []


@pytest.mark.parametrize("mount", ESCAPES)
def test_file_restore_refuses_a_mount_outside_the_tool_s_bases(client, issued, mount):
    r = client.post("/api/restore/file", json={"host": "h", "mount_path": mount,
                                               "file_path": "etc/hostname", "dest_path": "/root/x"},
                    headers={"X-CSRF-Token": "t"})
    assert r.get_json()["success"] is False
    assert issued == []


@pytest.mark.parametrize("mount", ESCAPES)
def test_directory_restore_refuses_a_mount_outside_the_tool_s_bases(client, issued, mount):
    r = client.post("/api/restore/directory", json={"host": "h", "mount_path": mount,
                                                    "dir_path": "etc", "dest_path": "/root/x"},
                    headers={"X-CSRF-Token": "t"})
    assert r.get_json()["success"] is False
    assert issued == []


@pytest.mark.parametrize("base", [R, V])
def test_a_mount_the_tool_made_still_works(client, issued, base):
    # Both bases: the zvol browser reuses the same endpoints.
    r = client.get(f"/api/restore/preview?host=h&mount_path={base}/restore-x&file=etc/hostname")
    assert r.get_json()["success"] is True
    assert any(c.startswith("cat ") for c in issued)


def test_a_sibling_mount_that_merely_shares_the_prefix_is_outside(monkeypatch):
    # ".../restore-a" must not vouch for ".../restore-ab": that is a
    # different clone, and a string prefix cannot tell them apart.
    monkeypatch.setattr(z, "run_command", lambda h, c, timeout=30, cache_ttl=0: {
        "success": True, "stdout": f"{R}/restore-ab/secret\n", "stderr": ""})
    esc = z._verify_under_mount({"address": "h"}, f"{R}/restore-a/../restore-ab/secret",
                                f"{R}/restore-a")
    assert esc is not None and "escapes" in esc["stderr"]


def test_the_mount_root_itself_is_inside(monkeypatch):
    monkeypatch.setattr(z, "run_command", lambda h, c, timeout=30, cache_ttl=0: {
        "success": True, "stdout": f"{R}/restore-a\n", "stderr": ""})
    assert z._verify_under_mount({"address": "h"}, f"{R}/restore-a/", f"{R}/restore-a") is None


def test_an_unreadable_realpath_still_fails_closed(monkeypatch):
    monkeypatch.setattr(z, "run_command", lambda h, c, timeout=30, cache_ttl=0: {
        "success": False, "stdout": "", "stderr": "ssh gone"})
    esc = z._verify_under_mount({"address": "h"}, f"{R}/restore-a/x", f"{R}/restore-a")
    assert esc is not None
