"""Security stage 2, the small findings (review of 18.10., re-checked 07.11.).

9   ``zfs set {prop}={value} {dataset}`` went to the shell unquoted, and the
    value pattern allows spaces, ``=`` and ``,``.
13  Telegram (parse_mode HTML), Matrix (formatted_body) and the e-mail HTML
    part carried the message unescaped; Gotify's token sat in the URL.
N   A failed task handed the last 1500 characters of its traceback to the
    polling client.
10  Fixed names under /tmp on the host, written by root.
11  A host-config backup member ``etc/../root/.ssh/authorized_keys`` passed
    every prefix check as ``etc/...`` and was restored to where ``..`` points.
"""

import io
import tarfile
import time
import urllib.request

import pytest

from app import dr, hostbackup, tasks
from app import notifications as n
from app import zfs_commands as z


# --- 9: zfs set / zpool set / zfs inherit are quoted ------------------------------

@pytest.fixture
def cmds(monkeypatch):
    seen = []

    def fake(host, cmd, **k):
        seen.append(cmd)
        return {"success": True, "stdout": "", "stderr": ""}
    monkeypatch.setattr(z, "run_command", fake)
    monkeypatch.setattr(z, "_invalidate", lambda h: None)
    return seen


def test_a_property_value_with_spaces_is_one_shell_word(cmds):
    z.set_dataset_property({"address": "h"}, "tank/share", "sharenfs", "rw=@10.0.0.0/24,no_root_squash x")
    assert cmds == ["zfs set sharenfs='rw=@10.0.0.0/24,no_root_squash x' tank/share"]


def test_auto_snapshot_set_and_inherit_are_quoted(cmds):
    z.set_auto_snapshot({"address": "h"}, "rpool/data", True, "hourly")
    z.inherit_auto_snapshot({"address": "h"}, "rpool/data", "hourly")
    assert cmds == ["zfs set com.sun:auto-snapshot:hourly=true rpool/data",
                    "zfs inherit com.sun:auto-snapshot:hourly rpool/data"]


def test_no_unquoted_zfs_set_is_left_in_the_module():
    import inspect
    import re
    src = inspect.getsource(z)
    lines = re.findall(r'f"z(?:fs|pool) (?:set|inherit) [^"]*"', src)
    assert lines
    for ln in lines:
        # zpool set keeps {prop}={value}: both are checked against a fixed
        # allowlist (EDITABLE_POOL_PROPS, on/off) before the call.
        rest = ln.replace("{prop}={value}", "") if ln.startswith('f"zpool set') else ln
        assert not re.search(r"\{(?!shlex\.quote\()[A-Za-z_]+\}", rest), ln


# --- 13: the HTML channels show text, they don't render it -------------------------

@pytest.fixture
def channels(monkeypatch):
    sent = {}
    monkeypatch.setattr(n, "load_config", lambda: {
        "events": {"pool_error": True},
        "telegram": {"enabled": True, "bot_token": "t", "chat_id": "c"},
        "matrix": {"enabled": True, "homeserver": "https://m", "access_token": "a", "room_id": "!r"},
        "email": {"enabled": True, "smtp_host": "s", "to_addresses": "x@y"},
    })
    monkeypatch.setattr(n, "_send_telegram", lambda tok, chat, text: sent.setdefault("telegram", text) and {"success": True})
    monkeypatch.setattr(n, "_send_matrix", lambda hs, tok, room, plain, html=None: sent.update(matrix_plain=plain, matrix_html=html) or {"success": True})
    monkeypatch.setattr(n, "_send_email", lambda cfg, subj, text, html=None, att=None: sent.update(email_html=html, email_text=text) or {"success": True})
    return sent


EVIL = 'pool <b>tank</b> & <a href="http://x">click</a>'


def test_telegram_matrix_and_email_escape_the_message(channels):
    n.send_notification("pool_error", "Pool <i>tank</i>", EVIL, host=None)
    tg = channels["telegram"]
    assert tg.startswith("<b>ZFS Tool – Pool &lt;i&gt;tank&lt;/i&gt;</b>")
    assert "&lt;a href=\"http://x\"&gt;" in tg and "<a href" not in tg
    assert "&amp;" in tg
    mh = channels["matrix_html"]
    assert mh.startswith("<b>ZFS Tool") and "<a href" not in mh and "&lt;b&gt;tank" in mh
    assert EVIL in channels["matrix_plain"]            # the plain body stays as written
    eh = channels["email_html"]
    assert "<a href" not in eh and "&lt;a href" in eh and "Pool &lt;i&gt;" in eh
    assert EVIL in channels["email_text"]


def test_gotify_token_travels_in_a_header(monkeypatch):
    seen = []

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"

    def fake(req, timeout=None):
        seen.append(req)
        return _Resp()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    n._send_gotify("https://gotify.example.test/", "SECRET-TOKEN", "t", "m")
    req = seen[0]
    assert req.full_url == "https://gotify.example.test/message"
    assert "SECRET-TOKEN" not in req.full_url
    assert {k.lower(): v for k, v in req.header_items()}["x-gotify-key"] == "SECRET-TOKEN"


# --- N: the traceback stays in the server log -------------------------------------

def test_a_failed_task_reports_the_message_not_the_traceback():
    def boom(progress):
        raise RuntimeError("zfs said no")
    tid = tasks.start_task("t-boom", boom)
    for _ in range(200):
        rec = tasks.get_task(tid)
        if rec["status"] != "running":
            break
        time.sleep(0.01)
    assert rec["status"] == "error"
    assert rec["error"] == "zfs said no"
    assert "Traceback" not in rec["error"] and ".py" not in rec["error"]


# --- 10: no fixed names under /tmp on the host ---------------------------------------

def test_the_package_reinstall_script_works_in_a_private_temp_dir():
    import inspect
    src = inspect.getsource(dr)
    assert "/tmp/.pvezfs_" not in src
    assert "mktemp -d" in src and "trap 'rm -rf" in src


def test_the_host_backup_archive_is_not_built_under_tmp():
    script = hostbackup._build_backup_script(False, hostbackup.REMOTE_BACKUP_DIR + "/x.tar.gz")
    assert "/tmp/" not in script
    assert 'mkdir -m 700 -p "$(dirname /root/.pve-zfs-tool/x.tar.gz)"' in script


# --- 11: path traversal in the config restore -----------------------------------------

@pytest.mark.parametrize("member,rel", [
    ("./etc/network/interfaces", "etc/network/interfaces"),
    ("./etc/./network//interfaces", "etc/network/interfaces"),
    ("/etc/hosts", "etc/hosts"),
    ("././etc/hosts", "etc/hosts"),
    (".bashrc", ".bashrc"),                       # a dot file keeps its dot now
    ("etc/../root/.ssh/authorized_keys", "root/.ssh/authorized_keys"),
    ("etc/../../root/.ssh/authorized_keys", ""),
    ("../etc/shadow", ""),
    ("./..", ""),
    ("", ""),
])
def test_member_names_are_normalised_or_refused(member, rel):
    assert dr._rel(member) == rel


def test_a_climbing_segment_never_maps_to_a_target():
    assert dr._backup_target_path("etc/../root/.ssh/authorized_keys", "n") is None
    assert dr._backup_target_path("etc/pve/../../root/x", "n") is None
    assert dr._backup_target_path("/etc/hosts", "n") is None


def _archive(tmp_path, members):
    p = tmp_path / "pve-backup-20261107-000000.tar.gz"
    with tarfile.open(p, "w:gz") as tf:
        for name, content in members.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return str(p)


def test_a_prepared_archive_cannot_climb_out_and_nothing_reaches_the_host(tmp_path, monkeypatch):
    evil = "etc/../../tmp/../root/.ssh/authorized_keys"
    path = _archive(tmp_path, {evil: "ssh-ed25519 AAAA attacker\n",
                               "./etc/hosts": "127.0.0.1 localhost\n"})
    calls = []
    monkeypatch.setattr(dr, "run_command", lambda h, c, **k: calls.append(c) or {"success": True, "stdout": "pve"})
    res = dr.restore_backup_file({"address": "h"}, path, evil)
    assert res["success"] is False and calls == []
    listed = {f["member"]: f for f in dr.list_backup_contents(path)["files"]}
    assert evil not in listed                                # not even offered
    assert listed["./etc/hosts"]["restorable"] is True
