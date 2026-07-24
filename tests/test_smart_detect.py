"""SMART detection for the AI report: recognise real PASSED/FAILED via
device-type fallbacks, resolve whole disks correctly (esp. whole-NVMe vdevs),
and treat missing SMART data as informational -- not a false warning.
Regression for Hetzner disks behind HBA/SAT layers showing an '⚠ Unknown'
that then tagged the whole SMART section [WARN]."""

from app.zfs_commands import (
    _smart_base_disk, _smart_device_types, _classify_smart_output,
)
from app.ai_reports import _compute_section_statuses


# --- _smart_base_disk: partition -> whole disk -----------------------------

def test_base_disk_partition_uses_lsblk_parent():
    assert _smart_base_disk("/dev/sda3", "sda", True) == "/dev/sda"


def test_base_disk_whole_sata_lsblk_empty_stays():
    # whole SATA disk has no parent -> PKNAME empty -> unchanged
    assert _smart_base_disk("/dev/sdc", "", True) == "/dev/sdc"


def test_base_disk_whole_nvme_not_truncated():
    # THE bug: a whole-disk NVMe vdev must NOT become /dev/nvme0n
    assert _smart_base_disk("/dev/nvme0n1", "", True) == "/dev/nvme0n1"


def test_base_disk_nvme_partition_uses_lsblk_parent():
    assert _smart_base_disk("/dev/nvme0n1p3", "nvme0n1", True) == "/dev/nvme0n1"


# --- _smart_base_disk: lsblk unavailable -> conservative strip --------------

def test_base_disk_lsblk_failed_strips_sata_partition():
    assert _smart_base_disk("/dev/sda3", "", False) == "/dev/sda"


def test_base_disk_lsblk_failed_strips_nvme_partition():
    assert _smart_base_disk("/dev/nvme0n1p3", "", False) == "/dev/nvme0n1"


def test_base_disk_lsblk_failed_keeps_whole_nvme():
    assert _smart_base_disk("/dev/nvme0n1", "", False) == "/dev/nvme0n1"


def test_base_disk_lsblk_failed_keeps_whole_sata():
    assert _smart_base_disk("/dev/sdc", "", False) == "/dev/sdc"


# --- _smart_device_types ---------------------------------------------------

def test_device_types_nvme():
    assert _smart_device_types("/dev/nvme0n1") == ["", "-d nvme"]


def test_device_types_sata_tries_bridges():
    types = _smart_device_types("/dev/sdb")
    assert types[0] == ""              # auto-detect first
    assert "-d sat" in types           # HBA/USB bridge
    assert "-d scsi" in types          # SAS


# --- _classify_smart_output ------------------------------------------------

def test_classify_ata_passed():
    out = "SMART overall-health self-assessment test result: PASSED\n"
    assert _classify_smart_output(out) == "PASSED"


def test_classify_ata_failed():
    out = "SMART overall-health self-assessment test result: FAILED!\n"
    assert _classify_smart_output(out) == "FAILED"


def test_classify_scsi_health_ok():
    assert _classify_smart_output("SMART Health Status: OK\n") == "PASSED"


def test_classify_needs_device_type_is_none():
    # disk behind an HBA/USB bridge without -d yields no verdict -> try next type
    out = ("/dev/sdb: Unknown USB bridge [0x1234:0x5678 (0x100)]\n"
           "Please specify device type with the -d option.\n")
    assert _classify_smart_output(out) is None


def test_classify_smart_unavailable_is_none():
    out = "SMART support is: Unavailable - device lacks SMART capability.\n"
    assert _classify_smart_output(out) is None


def test_classify_missing_tool():
    assert _classify_smart_output("bash: smartctl: command not found\n") == "NOTOOL"


# --- report classification: missing data must not warn ---------------------

def _data_with_smart(*statuses):
    return {"hosts": [{"smart": {"pools": {"tank": [
        {"id": f"disk{i}", "dev": f"/dev/sd{chr(97 + i)}", "status": s}
        for i, s in enumerate(statuses)
    ]}}}]}


def test_section5_ok_when_all_passed():
    statuses, _ = _compute_section_statuses(_data_with_smart("PASSED", "PASSED"))
    assert statuses[5] == "ok"


def test_section5_not_warned_by_missing_data():
    # N/A / Unknown / "smartctl fehlt" are informational, never a warning
    statuses, _ = _compute_section_statuses(
        _data_with_smart("PASSED", "N/A", "Unknown", "smartctl fehlt"))
    assert statuses[5] == "ok"


def test_section5_crit_on_failed():
    statuses, overall = _compute_section_statuses(_data_with_smart("PASSED", "FAILED"))
    assert statuses[5] == "crit"
    assert overall == "crit"
