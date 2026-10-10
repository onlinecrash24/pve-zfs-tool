"""The replication key on the source is restricted, and the restriction points
the right way.

Replication is pull-based: the key pair lives on the target and its public
half is installed on the source. Unrestricted, that line is a full root
shell for whoever holds the target. `restrict` and `from=<target>` narrow it
to what the five callers over that key actually do -- non-interactive
`ssh host 'cmd'` from one address.
"""

import pytest

from app import replication as r
from app import ssh_manager as sm

PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOTEST bashclub-zsync-pve2"
TARGET = {"address": "10.0.0.9", "name": "pve2"}
SOURCE = {"address": "10.0.0.5", "name": "pve1"}


# --- the authorized_keys line ------------------------------------------------

def test_options_become_a_prefix_on_the_exact_line(monkeypatch):
    cmds = []
    monkeypatch.setattr(sm, "run_command", lambda h, c, **k: cmds.append(c) or {"success": True})
    sm._append_authorized_key(SOURCE, PUB, options='restrict,from="10.0.0.9"')
    line = f'restrict,from="10.0.0.9" {PUB}'
    assert f"grep -qxF '{line}'" in cmds[0]
    assert f"echo '{line}' >> ~/.ssh/authorized_keys" in cmds[0]


def test_without_options_the_line_is_the_bare_key_as_before(monkeypatch):
    # The tool's own key keeps the full shell; only the replication key is narrowed.
    cmds = []
    monkeypatch.setattr(sm, "run_command", lambda h, c, **k: cmds.append(c) or {"success": True})
    sm._append_authorized_key(SOURCE, PUB)
    assert f"grep -qxF '{PUB}'" in cmds[0] and "restrict" not in cmds[0]


@pytest.mark.parametrize("addr,expected", [
    ("10.0.0.9", 'restrict,from="10.0.0.9"'),
    ("pve2.lan", 'restrict,from="pve2.lan"'),
    ("fd00::9", 'restrict,from="fd00::9"'),
    ('10.0.0.9" ssh-rsa EVIL', ""),            # would break out of the quotes
    ("10.0.0.9 10.0.0.10", ""),
    ("", ""),
])
def test_only_a_plain_address_is_embedded_in_from(addr, expected):
    assert r.authorized_key_options({"address": addr}) == expected


# --- bootstrap_ssh ------------------------------------------------------------

@pytest.fixture
def bootstrap(monkeypatch):
    calls = []

    def run(host, cmd, timeout=30, **k):
        if "ssh-keygen" in cmd:
            return {"success": True, "stdout": PUB + "\n", "stderr": ""}
        if "ssh-keyscan" in cmd:
            return {"success": True, "stdout": "__KH_OK__\n", "stderr": ""}
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(r, "run_command", run)
    monkeypatch.setattr("app.ssh_manager._append_authorized_key",
                        lambda host, pub, options=None: calls.append(("append", host["address"], pub, options)) or {"success": True})
    monkeypatch.setattr("app.ssh_manager._remove_authorized_key",
                        lambda host, pub: calls.append(("remove", host["address"], pub, None)) or {"success": True})
    monkeypatch.setattr(r, "probe_ssh_trust", lambda t, s: {"probe_ok": True, "output": "__PROBE_OK__"})
    return calls


def test_the_key_is_installed_restricted_to_the_targets_address(bootstrap):
    out = r.bootstrap_ssh(TARGET, SOURCE)
    assert out["success"] is True
    appends = [c for c in bootstrap if c[0] == "append"]
    assert appends == [("append", "10.0.0.5", PUB, 'restrict,from="10.0.0.9"')]
    # from= names the TARGET (who connects), on the SOURCE (who is connected to).
    assert '"10.0.0.9"' in appends[0][3] and "10.0.0.5" not in appends[0][3]
    assert out["key_options"] == 'restrict,from="10.0.0.9"'


def test_the_old_unrestricted_line_is_removed_after_the_new_one_exists(bootstrap):
    r.bootstrap_ssh(TARGET, SOURCE)
    kinds = [c[0] for c in bootstrap]
    assert kinds == ["append", "remove"]
    assert bootstrap[1] == ("remove", "10.0.0.5", PUB, None)   # exact bare line


def test_an_unembeddable_target_address_stops_before_touching_the_source(bootstrap):
    out = r.bootstrap_ssh({"address": '10.0.0.9" ssh-rsa EVIL'}, SOURCE)
    assert out["success"] is False and "from= restriction" in out["error"]
    assert bootstrap == []


def test_a_failed_probe_explains_the_nat_case(bootstrap, monkeypatch):
    monkeypatch.setattr(r, "probe_ssh_trust", lambda t, s: {"probe_ok": False, "output": "Permission denied"})
    out = r.bootstrap_ssh(TARGET, SOURCE)
    assert out["success"] is False
    assert "from=" in out["error"] and "NAT" in out["error"]
