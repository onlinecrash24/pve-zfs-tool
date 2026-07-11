"""SMART parsing: normalise smartctl -j across HDD/SSD/NVMe, split the
multi-disk collect blob, and the enumeration/install scripts."""

import json
from app import smart


# --- fixtures: realistic smartctl -j shapes -------------------------------

HDD = {
    "device": {"name": "/dev/sda", "type": "sat", "protocol": "ATA"},
    "model_name": "WDC WD40EFRX-68N32N0",
    "serial_number": "WD-AAA",
    "rotation_rate": 5400,
    "smart_status": {"passed": True},
    "temperature": {"current": 41},
    "power_on_time": {"hours": 26280},
    "ata_smart_attributes": {"table": [
        {"id": 5, "name": "Reallocated_Sector_Ct", "value": 200, "raw": {"value": 8}},
        {"id": 197, "name": "Current_Pending_Sector", "value": 200, "raw": {"value": 2}},
    ]},
}

SSD = {
    "device": {"name": "/dev/sdb", "type": "sat", "protocol": "ATA"},
    "model_name": "Samsung SSD 860 EVO 1TB",
    "rotation_rate": 0,
    "smart_status": {"passed": True},
    "temperature": {"current": 33},
    "power_on_time": {"hours": 8000},
    "ata_smart_attributes": {"table": [
        {"id": 5, "raw": {"value": 0}},
        {"id": 231, "name": "SSD_Life_Left", "value": 90, "raw": {"value": 0}},
    ]},
}

NVME = {
    "device": {"name": "/dev/nvme0", "type": "nvme", "protocol": "NVMe"},
    "model_name": "Samsung SSD 980 PRO 1TB",
    "smart_status": {"passed": True},
    "temperature": {"current": 44},
    "power_on_time": {"hours": 5000},
    "nvme_smart_health_information_log": {"percentage_used": 3, "media_errors": 0},
}

RAID_HIDDEN = {"device": {"name": "/dev/bus/0", "type": "megaraid"},
               "smartctl": {"exit_status": 2}}


# --- parse_smart_json -----------------------------------------------------

def test_parse_hdd():
    r = smart.parse_smart_json("sda", HDD)
    assert r["type"] == "hdd"
    assert r["temp_c"] == 41
    assert r["power_on_hours"] == 26280
    assert r["health_passed"] is True
    assert r["realloc_sectors"] == 8
    assert r["pending_sectors"] == 2
    assert r["wear_pct"] is None


def test_parse_ssd_wear_from_life_left():
    r = smart.parse_smart_json("sdb", SSD)
    assert r["type"] == "ssd"
    assert r["temp_c"] == 33
    assert r["realloc_sectors"] == 0
    assert r["pending_sectors"] is None      # no id 197 present
    assert r["wear_pct"] == 10               # 100 - normalised 90


def test_parse_nvme_percentage_used():
    r = smart.parse_smart_json("nvme0", NVME)
    assert r["type"] == "nvme"
    assert r["temp_c"] == 44
    assert r["wear_pct"] == 3
    assert r["realloc_sectors"] is None
    assert r["health_passed"] is True


def test_parse_skips_device_without_signal():
    # RAID-hidden / virtual disks expose no temp and no health -> dropped
    assert smart.parse_smart_json("bus0", RAID_HIDDEN) is None


def test_parse_rejects_non_dict():
    assert smart.parse_smart_json("x", None) is None
    assert smart.parse_smart_json("x", "garbage") is None


# --- parse_collect_output -------------------------------------------------

def _blob(*objs):
    parts = []
    for name, obj in objs:
        parts.append(f"<<<DEV:{name}>>>")
        parts.append(obj if isinstance(obj, str) else json.dumps(obj))
        parts.append("<<<END>>>")
    return "\n".join(parts)


def test_collect_output_parses_multiple_disks():
    raw = _blob(("sda", HDD), ("nvme0", NVME))
    out = smart.parse_collect_output(raw)
    assert out["installed"] is True
    assert [d["device"] for d in out["disks"]] == ["sda", "nvme0"]


def test_collect_output_skips_unparseable_block():
    raw = _blob(("sda", HDD), ("sdb", "{not valid json"))
    out = smart.parse_collect_output(raw)
    assert [d["device"] for d in out["disks"]] == ["sda"]


def test_collect_output_not_installed():
    out = smart.parse_collect_output("<<<NOSMARTCTL>>>\n")
    assert out["installed"] is False
    assert out["disks"] == []


# --- scripts --------------------------------------------------------------

def test_collect_script_enumerates_and_guards():
    s = smart._build_collect_script()
    assert "smartctl -j -a" in s
    assert "<<<NOSMARTCTL>>>" in s
    assert "lsblk" in s
    assert "zd*" in s and "dm-*" in s        # zvols / device-mapper skipped


def test_install_script_installs_smartmontools():
    s = smart._build_install_script()
    assert "apt-get install -y smartmontools" in s
    assert "apt-get update -qq || true" in s
    assert "__ALREADY__" in s
    assert "command -v smartctl" in s
