"""Product detection: is a host PVE, PBS, both, or none of the tool's business.

The classification is a pure function over the probe's KEY=value output, so all
the cases that matter -- including the ones that would be embarrassing in
production -- are covered without touching SSH.
"""

import paramiko
import pytest

from app import host_identity as hid
from app import ssh_manager as sm


def _probe(*lines):
    """Probe output with the PROBE_OK sentinel the real command always ends on."""
    return "\n".join(list(lines) + ["PROBE_OK=1"]) + "\n"


# --- classification -------------------------------------------------------

def test_pve_node():
    i = hid.parse_identity(_probe(
        "PVE_BIN=1", "PVE_DIR=1",
        "PVE_VERSION=pve-manager/8.2.4/faa83925c9641325 (running kernel: 6.8.4-2-pve)",
        "PVE_PKG=8.2.4-1", "PBS_CLIENT=1", "HOSTNAME=pve251",
        "OS=Debian GNU/Linux 12 (bookworm)",
    ))
    assert i["role"] == hid.ROLE_PVE
    assert i["pve_version"] == "8.2.4"
    assert i["pbs_version"] is None
    assert i["hostname"] == "pve251"
    assert hid.is_supported(i)


def test_pbs_server():
    i = hid.parse_identity(_probe(
        "PBS_BIN=1", "PBS_DIR=1", "PBS_DATASTORE_CFG=1", "PBS_PKG=3.2.7-1",
        "PBS_CLIENT=1", "HOSTNAME=pbs01",
    ))
    assert i["role"] == hid.ROLE_PBS
    assert i["pbs_version"] == "3.2.7"
    assert i["pve_version"] is None
    assert i["has_datastore_cfg"] is True
    # Correctly identified as Proxmox software, but not something this tool
    # manages: every feature reads through a PVE node. See admission().
    assert hid.is_proxmox(i) is True
    assert hid.is_supported(i) is False


def test_pve_with_backup_server_installed_is_both():
    # A perfectly normal setup: proxmox-backup-server on a PVE node.
    i = hid.parse_identity(_probe(
        "PVE_BIN=1", "PVE_DIR=1", "PVE_PKG=8.2.4-1",
        "PBS_BIN=1", "PBS_DIR=1", "PBS_PKG=3.2.7-1",
    ))
    assert i["role"] == hid.ROLE_BOTH
    assert i["pve_version"] == "8.2.4"
    assert i["pbs_version"] == "3.2.7"


def test_backup_client_alone_is_not_a_backup_server():
    # proxmox-backup-client ships on ordinary PVE nodes so they can write to a
    # PBS. Counting it would label nearly every PVE node "PVE+PBS".
    i = hid.parse_identity(_probe(
        "PVE_BIN=1", "PVE_DIR=1", "PVE_PKG=8.2.4-1", "PBS_CLIENT=1",
    ))
    assert i["role"] == hid.ROLE_PVE
    assert i["backup_client"] is True


def test_plain_debian_is_not_supported():
    i = hid.parse_identity(_probe("HOSTNAME=fileserver",
                                  "OS=Debian GNU/Linux 12 (bookworm)"))
    assert i["reachable"] is True            # it answered ...
    assert i["role"] == hid.ROLE_UNKNOWN    # ... it is just not Proxmox
    assert hid.is_supported(i) is False


def test_no_answer_is_not_the_same_as_not_proxmox():
    # Without the sentinel the probe never finished, so nothing may be concluded
    # about the host -- this is what keeps an unreachable host from being
    # reported as "not a Proxmox host".
    i = hid.parse_identity("")
    assert i["reachable"] is False
    assert i["role"] == hid.ROLE_UNKNOWN
    assert hid.is_supported(i) is False


def test_truncated_output_is_not_trusted():
    # Markers present but the probe was cut off mid-run: refuse to classify.
    i = hid.parse_identity("PVE_BIN=1\nPVE_DIR=1\n")
    assert i["reachable"] is False
    assert i["pve"] is False
    assert i["pve_version"] is None


# --- version parsing ------------------------------------------------------

def test_pve_version_falls_back_to_the_package():
    i = hid.parse_identity(_probe("PVE_DIR=1", "PVE_PKG=8.1.10-1"))
    assert i["role"] == hid.ROLE_PVE
    assert i["pve_version"] == "8.1.10"


def test_unexpected_pveversion_output_is_kept_verbatim():
    # Better a strange string in the UI than a silently empty version.
    i = hid.parse_identity(_probe("PVE_BIN=1", "PVE_VERSION=something-else"))
    assert i["pve_version"] == "something-else"


def test_epoch_and_suffix_are_stripped_from_package_versions():
    i = hid.parse_identity(_probe("PBS_PKG=1:3.2.7-1+deb12u2"))
    assert i["pbs_version"] == "3.2.7"


def test_junk_lines_are_ignored():
    i = hid.parse_identity(_probe("", "not a marker", "PVE_BIN=1", "=novalue"))
    assert i["role"] == hid.ROLE_PVE
    assert "PVE_BIN" in i["markers"]
    assert "PROBE_OK" not in i["markers"]


# --- probe / detect -------------------------------------------------------

def test_probe_asks_for_the_server_not_the_client():
    # The command itself must look for the server side; a probe that only knew
    # about proxmox-backup-client could never find a PBS.
    assert "proxmox-backup-manager" in hid.IDENTITY_PROBE
    assert "proxmox-backup-server" in hid.IDENTITY_PROBE
    assert "/etc/proxmox-backup" in hid.IDENTITY_PROBE
    assert "PROBE_OK" in hid.IDENTITY_PROBE


def test_dpkg_query_format_asks_for_a_newline():
    # dpkg-query prints NO trailing newline by default, so without the \n in the
    # format string its output glues itself to whatever the probe emits next:
    # "PVE_PKG=8.2.4-1PBS_PKG=3.2.7-1HOSTNAME=pve251" on one line. The PBS
    # version and hostname are then lost, and -- worse -- the mangled PVE
    # version still parses to a plausible-looking "8.2.4".
    for line in hid.IDENTITY_PROBE.splitlines():
        if "dpkg-query" in line:
            assert r"${Version}\n" in line, line


def test_fields_glued_onto_one_line_are_recovered():
    # The parser side of the same trap. Before this, the run-together line lost
    # the PBS version and the hostname outright, while the mangled PVE version
    # still trimmed to a plausible-looking "8.2.4" -- a wrong answer that looked
    # right. Splitting the line back apart keeps the data instead.
    i = hid.parse_identity(
        "PVE_BIN=1\nPVE_PKG=8.2.4-1PBS_PKG=3.2.7-1HOSTNAME=pve251\nPROBE_OK=1\n")
    assert i["pve_version"] == "8.2.4"
    assert i["pbs_version"] == "3.2.7"
    assert i["hostname"] == "pve251"
    assert i["role"] == hid.ROLE_BOTH


def test_recovery_does_not_chop_up_ordinary_values():
    i = hid.parse_identity(_probe(
        "PVE_BIN=1",
        "PVE_VERSION=pve-manager/8.2.4/faa8 (running kernel: 6.8.4-2-pve)",
        "OS=Debian GNU/Linux 12 (bookworm)", "HOSTNAME=pve-node-1",
    ))
    assert i["os"] == "Debian GNU/Linux 12 (bookworm)"
    assert i["hostname"] == "pve-node-1"
    assert i["pve_version"] == "8.2.4"


def test_detect_reports_the_ssh_error_instead_of_a_verdict(monkeypatch):
    monkeypatch.setattr(hid, "run_command", lambda h, c, timeout=0, cache_ttl=0: {
        "success": False, "stdout": "",
        "stderr": "SSH host key verification failed\nsecond line",
    })
    i = hid.detect({"address": "10.0.0.9"})
    assert i["reachable"] is False
    assert i["role"] == hid.ROLE_UNKNOWN
    assert i["error"] == "SSH host key verification failed"
    assert i["checked"] > 0


def test_detect_classifies_a_live_host(monkeypatch):
    monkeypatch.setattr(hid, "run_command", lambda h, c, timeout=0, cache_ttl=0: {
        "success": True, "stdout": _probe("PVE_BIN=1", "PVE_PKG=8.2.4-1"), "stderr": "",
    })
    i = hid.detect({"address": "10.0.0.1"})
    assert i["role"] == hid.ROLE_PVE
    assert i["error"] is None


# --- admission policy ------------------------------------------------------

def _identity(role, reachable=True):
    return {"role": role, "reachable": reachable}


def test_pve_hosts_are_admitted_with_or_without_pbs_alongside():
    for role in (hid.ROLE_PVE, hid.ROLE_BOTH):
        assert hid.admission(_identity(role)) == (True, None)


def test_a_standalone_backup_server_is_refused():
    # Nothing here reads from a backup server -- the backup state comes from
    # the PVE node's own storage layer -- so registering one would buy only
    # root SSH into the machine that should survive the compromise of the
    # systems it backs up.
    assert hid.admission(_identity(hid.ROLE_PBS)) == (False, "pbs_only")


def test_force_does_not_override_a_standalone_backup_server():
    # Deliberate policy, not a connection problem: there is nothing to retry.
    assert hid.admission(_identity(hid.ROLE_PBS), force=True) == (False, "pbs_only")


def test_a_non_proxmox_host_is_refused():
    assert hid.admission(_identity(hid.ROLE_UNKNOWN)) == (False, "not_proxmox")


def test_force_does_not_override_a_proven_non_proxmox_host():
    # The host answered and has neither product. That is an answer, not a
    # failure, so there is nothing for the user to override.
    assert hid.admission(_identity(hid.ROLE_UNKNOWN), force=True) == (False, "not_proxmox")


def test_an_unreachable_host_is_never_refused_as_pbs_only():
    # The role field can say "pbs" on a host that never answered (a stored role
    # from an earlier probe). Refusing THAT as a standalone backup server would
    # be a verdict on data nobody just collected -- it stays the overridable
    # "unverified" case.
    unreachable_pbs = _identity(hid.ROLE_PBS, reachable=False)
    assert hid.admission(unreachable_pbs) == (False, "unverified")
    assert hid.admission(unreachable_pbs, force=True) == (True, None)


def test_an_unreachable_host_is_refused_but_overridable():
    unreachable = _identity(hid.ROLE_UNKNOWN, reachable=False)
    assert hid.admission(unreachable) == (False, "unverified")
    # Nothing was established about it -- the key may just not be installed yet,
    # and a host that is powered off most of the time is a supported case.
    assert hid.admission(unreachable, force=True) == (True, None)


# --- storage ---------------------------------------------------------------

@pytest.fixture
def hosts_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sm, "HOSTS_FILE", str(tmp_path / "hosts.json"))
    monkeypatch.setattr(sm, "KNOWN_HOSTS", str(tmp_path / "known_hosts"))
    monkeypatch.setattr(sm, "get_host_fingerprint",
                        lambda addr, port=22: {"success": False, "error": "skip"})
    return tmp_path


def test_add_host_stores_the_detected_role(hosts_file):
    i = hid.parse_identity(_probe("PBS_BIN=1", "PBS_PKG=3.2.7-1"))
    i["checked"] = 1700000000
    ok, _ = sm.add_host("pbs01", "10.0.0.5", 22, "root",
                        identity=hid.persisted_fields(i))
    assert ok
    stored = [h for h in sm.load_hosts() if h["address"] == "10.0.0.5"][0]
    assert stored["role"] == "pbs"
    assert stored["pbs_version"] == "3.2.7"
    assert stored["identity_checked"] == 1700000000
    # The identity must not have overwritten the connection details.
    assert stored["name"] == "pbs01" and stored["port"] == 22


def test_add_host_without_identity_keeps_the_old_shape(hosts_file):
    # Hosts added by other code paths (and every host added before this feature)
    # simply carry no role.
    assert sm.add_host("pve1", "10.0.0.6", 22, "root")[0]
    stored = [h for h in sm.load_hosts() if h["address"] == "10.0.0.6"][0]
    assert "role" not in stored


def test_update_host_identity_upgrades_a_host_in_place(hosts_file):
    sm.add_host("pve1", "10.0.0.7", 22, "root",
                identity={"role": "pve", "pve_version": "8.1.10",
                          "pbs_version": None, "identity_checked": 1})
    # PBS gets installed on the node later.
    i = hid.parse_identity(_probe("PVE_BIN=1", "PVE_PKG=8.2.4-1",
                                  "PBS_BIN=1", "PBS_PKG=3.2.7-1"))
    i["checked"] = 1700000001
    ok, _ = sm.update_host_identity("10.0.0.7", hid.persisted_fields(i))
    assert ok
    stored = [h for h in sm.load_hosts() if h["address"] == "10.0.0.7"][0]
    assert stored["role"] == "pve+pbs"
    assert stored["pve_version"] == "8.2.4" and stored["pbs_version"] == "3.2.7"


def test_update_host_identity_reports_an_unknown_address(hosts_file):
    assert sm.update_host_identity("10.0.0.99", {"role": "pve"})[0] is False


def test_a_refused_host_leaves_no_trusted_key(hosts_file):
    kh = str(hosts_file / "known_hosts")
    hk = paramiko.HostKeys()
    hk.add("10.0.0.8", "ssh-rsa", paramiko.RSAKey.generate(2048))
    hk.save(kh)
    assert sm.discard_unregistered("10.0.0.8") is True
    after = paramiko.HostKeys()
    after.load(kh)
    assert "10.0.0.8" not in after


def test_discard_refuses_to_touch_a_registered_host(hosts_file):
    # A mistaken call must never drop a working host's host key -- that would
    # turn a live host into an SSH verification failure.
    kh = str(hosts_file / "known_hosts")
    sm.add_host("pve1", "10.0.0.9", 22, "root")
    hk = paramiko.HostKeys()
    hk.add("10.0.0.9", "ssh-rsa", paramiko.RSAKey.generate(2048))
    hk.save(kh)
    assert sm.discard_unregistered("10.0.0.9") is False
    after = paramiko.HostKeys()
    after.load(kh)
    assert "10.0.0.9" in after


def test_persisted_fields_are_only_role_and_versions():
    i = hid.parse_identity(_probe("PVE_BIN=1", "PVE_PKG=8.2.4-1", "HOSTNAME=x",
                                  "OS=Debian"))
    i["checked"] = 1700000000
    assert hid.persisted_fields(i) == {
        "role": "pve", "pve_version": "8.2.4", "pbs_version": None,
        "identity_checked": 1700000000,
    }
