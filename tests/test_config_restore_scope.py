"""PVE Config Restore scope:

- corosync.conf must NEVER be part of the bulk "restore all configs": on a
  freshly installed node it makes pve-cluster expect a cluster, and without
  quorum /etc/pve turns read-only -> host no longer configurable.
- the paths added for a complete rebuild (sshd_config, systemd units, sysctl,
  modprobe, timezone, postfix/aliases, exports/samba) must be captured by the
  backup script, land in a sensible category, and be restorable.
- secrets must stay out of the backup (postfix sasl_passwd, samba *.tdb,
  apt auth.conf, SSH host private keys).
- `zpool import` output parsing for the pool-import step.
"""

from app.dr import (_categorize, _backup_target_path, _NON_CONFIG_CATEGORIES,
                    parse_importable_pools)
from app.hostbackup import _build_backup_script


# --- 1: corosync excluded from the bulk restore ----------------------------

def test_corosync_is_its_own_category():
    assert _categorize("etc/pve/corosync.conf") == "cluster"


def test_cluster_category_excluded_from_bulk():
    assert "cluster" in _NON_CONFIG_CATEGORIES


def test_corosync_still_individually_restorable():
    # excluded from bulk, but the single-file / per-category restore still works
    assert _backup_target_path("etc/pve/corosync.conf", "node1") == "/etc/pve/corosync.conf"


# --- 2 + 4: new paths categorized and restorable ---------------------------

def test_sshd_config_is_ssh_category():
    assert _categorize("etc/ssh/sshd_config") == "ssh"
    assert _categorize("etc/ssh/sshd_config.d/10-custom.conf") == "ssh"
    assert _categorize("root/.ssh/authorized_keys") == "ssh"


def test_system_category():
    for rel in ("etc/systemd/system/myunit.service", "etc/sysctl.d/99-tune.conf",
                "etc/modprobe.d/zfs.conf", "etc/sysctl.conf", "etc/timezone"):
        assert _categorize(rel) == "system", rel


def test_mail_and_share_categories():
    assert _categorize("etc/postfix/main.cf") == "mail"
    assert _categorize("etc/aliases") == "mail"
    assert _categorize("etc/exports") == "storage"
    assert _categorize("etc/samba/smb.conf") == "storage"


def test_new_paths_are_all_restorable():
    for rel in ("etc/ssh/sshd_config", "etc/systemd/system/x.service",
                "etc/sysctl.d/99.conf", "etc/modprobe.d/kvm.conf", "etc/timezone",
                "etc/postfix/main.cf", "etc/aliases", "etc/exports",
                "etc/samba/smb.conf"):
        assert _backup_target_path(rel, "n") == "/" + rel, rel


def test_new_paths_are_in_the_bulk_restore():
    for rel in ("etc/ssh/sshd_config", "etc/systemd/system/x.service",
                "etc/postfix/main.cf", "etc/exports"):
        assert _categorize(rel) not in _NON_CONFIG_CATEGORIES, rel


# --- backup script actually captures them, without secrets -----------------

def test_backup_script_captures_new_paths():
    s = _build_backup_script(include_priv=False, dest="/tmp/x.tar.gz")
    for needle in ("/etc/ssh/sshd_config", "/etc/ssh/sshd_config.d",
                   "/etc/systemd/system", "/etc/sysctl.d", "/etc/timezone",
                   "/etc/modprobe.d", "/etc/postfix", "/etc/aliases",
                   "/etc/exports", "/etc/samba"):
        assert needle in s, needle


def test_backup_script_excludes_secrets():
    s = _build_backup_script(include_priv=False, dest="/tmp/x.tar.gz")
    assert "sasl_passwd" in s and "--exclude='sasl_passwd*'" in s
    assert "--exclude='*.tdb'" in s
    assert "--exclude=auth.conf" in s
    # never capture SSH host private keys
    assert "ssh_host_" not in s
    assert "--exclude=priv" in s          # /etc/pve/priv opt-in only


def test_modprobe_no_longer_limited_to_zfs_conf():
    s = _build_backup_script(include_priv=False, dest="/tmp/x.tar.gz")
    assert "/etc/modprobe.d/zfs.conf" not in s   # whole dir is captured instead


# --- 3: zpool import parsing ----------------------------------------------

_IMPORT_OUT = """
   pool: tank
     id: 12345678901234567890
  state: ONLINE
 action: The pool can be imported using its name or numeric identifier.
 config:

\ttank        ONLINE
\t  mirror-0  ONLINE
\t    sdc     ONLINE
\t    sdd     ONLINE

   pool: oldpool
     id: 98765432109876543210
  state: ONLINE (DESTROYED)
 action: The pool can be imported using its name or numeric identifier.
"""


def test_parse_importable_pools():
    pools = parse_importable_pools(_IMPORT_OUT)
    assert [p["name"] for p in pools] == ["tank", "oldpool"]
    assert pools[0]["state"] == "ONLINE"
    assert pools[0]["id"] == "12345678901234567890"
    assert pools[1]["state"] == "ONLINE (DESTROYED)"


def test_parse_importable_pools_empty():
    assert parse_importable_pools("no pools available to import") == []
    assert parse_importable_pools("") == []
