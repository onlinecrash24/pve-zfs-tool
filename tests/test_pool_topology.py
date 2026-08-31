"""How a pool is built, and whether one device would take it all with it.

The monitor asks "is anything broken right now?". These answer a question it
never asks: is this pool built so that a single failure destroys everything?
An unmirrored special vdev reports a cheerful ONLINE until the moment it kills
the pool, which is precisely why nobody notices it on their own.
"""

from app import pool_topology as pt


def _status(body, pool="tank", state="ONLINE"):
    """A zpool status document around a config block, tabs and all."""
    return (f"  pool: {pool}\n"
            f" state: {state}\n"
            "  scan: scrub repaired 0B in 00:12:31 with 0 errors\n"
            "config:\n"
            "\n"
            "\tNAME        STATE     READ WRITE CKSUM\n"
            + body +
            "\nerrors: No known data errors\n")


# --- the layouts that are fine -------------------------------------------

def test_a_mirror_is_redundant():
    topo = pt.parse_topology(_status(
        "\ttank          ONLINE       0     0     0\n"
        "\t  mirror-0    ONLINE       0     0     0\n"
        "\t    sda       ONLINE       0     0     0\n"
        "\t    sdb       ONLINE       0     0     0\n"))
    assert topo["tank"] == [{"tier": "data", "name": "mirror-0",
                             "redundancy": "mirror", "state": "ONLINE",
                             "devices": ["sda", "sdb"]}]
    assert pt.redundancy_findings(topo) == []


def test_raidz_levels_are_recognised():
    for name, expected in (("raidz1-0", "raidz1"), ("raidz2-0", "raidz2"),
                           ("raidz3-0", "raidz3")):
        topo = pt.parse_topology(_status(
            "\ttank          ONLINE       0     0     0\n"
            f"\t  {name}     ONLINE       0     0     0\n"
            "\t    sda       ONLINE       0     0     0\n"
            "\t    sdb       ONLINE       0     0     0\n"
            "\t    sdc       ONLINE       0     0     0\n"))
        assert topo["tank"][0]["redundancy"] == expected
        assert pt.redundancy_findings(topo) == []


def test_a_plain_pool_with_no_special_tiers_reports_nothing():
    topo = pt.parse_topology(_status(
        "\ttank          ONLINE       0     0     0\n"
        "\t  mirror-0    ONLINE       0     0     0\n"
        "\t    sda       ONLINE       0     0     0\n"
        "\t    sdb       ONLINE       0     0     0\n"))
    assert pt.redundancy_findings(topo) == []


# --- the layouts that cost you everything --------------------------------

def test_a_bare_special_vdev_is_critical():
    # The finding this module exists for: that one NVMe holds the metadata, so
    # losing it loses every dataset on the mirrored disks too.
    topo = pt.parse_topology(_status(
        "\ttank          ONLINE       0     0     0\n"
        "\t  mirror-0    ONLINE       0     0     0\n"
        "\t    sda       ONLINE       0     0     0\n"
        "\t    sdb       ONLINE       0     0     0\n"
        "\tspecial\n"
        "\t  nvme0n1     ONLINE       0     0     0\n"))
    special = [v for v in topo["tank"] if v["tier"] == "special"]
    assert special == [{"tier": "special", "name": "nvme0n1",
                        "redundancy": "none", "state": "ONLINE",
                        "devices": ["nvme0n1"]}]
    assert pt.redundancy_findings(topo) == [
        {"pool": "tank", "vdev": "nvme0n1", "tier": "special",
         "severity": "crit", "reason": "pool_loss"}]


def test_a_mirrored_special_vdev_is_fine():
    topo = pt.parse_topology(_status(
        "\ttank          ONLINE       0     0     0\n"
        "\t  mirror-0    ONLINE       0     0     0\n"
        "\t    sda       ONLINE       0     0     0\n"
        "\t    sdb       ONLINE       0     0     0\n"
        "\tspecial\n"
        "\t  mirror-1    ONLINE       0     0     0\n"
        "\t    nvme0n1   ONLINE       0     0     0\n"
        "\t    nvme1n1   ONLINE       0     0     0\n"))
    assert pt.redundancy_findings(topo) == []
    special = [v for v in topo["tank"] if v["tier"] == "special"][0]
    assert special["devices"] == ["nvme0n1", "nvme1n1"]


def test_a_bare_dedup_vdev_is_critical_too():
    topo = pt.parse_topology(_status(
        "\ttank          ONLINE       0     0     0\n"
        "\t  mirror-0    ONLINE       0     0     0\n"
        "\t    sda       ONLINE       0     0     0\n"
        "\t    sdb       ONLINE       0     0     0\n"
        "\tdedup\n"
        "\t  nvme0n1     ONLINE       0     0     0\n"))
    assert pt.redundancy_findings(topo)[0]["severity"] == "crit"


def test_a_striped_pool_of_bare_disks_is_critical():
    # No mirror, no raidz: every disk is a single point of total failure, and
    # the pool still says ONLINE.
    topo = pt.parse_topology(_status(
        "\ttank          ONLINE       0     0     0\n"
        "\t  sda         ONLINE       0     0     0\n"
        "\t  sdb         ONLINE       0     0     0\n"))
    findings = pt.redundancy_findings(topo)
    assert [f["vdev"] for f in findings] == ["sda", "sdb"]
    assert all(f["severity"] == "crit" and f["tier"] == "data" for f in findings)


def test_only_the_unmirrored_special_of_several_is_reported():
    topo = pt.parse_topology(_status(
        "\ttank          ONLINE       0     0     0\n"
        "\t  mirror-0    ONLINE       0     0     0\n"
        "\t    sda       ONLINE       0     0     0\n"
        "\t    sdb       ONLINE       0     0     0\n"
        "\tspecial\n"
        "\t  mirror-1    ONLINE       0     0     0\n"
        "\t    nvme0n1   ONLINE       0     0     0\n"
        "\t    nvme1n1   ONLINE       0     0     0\n"
        "\t  nvme2n1     ONLINE       0     0     0\n"))
    findings = pt.redundancy_findings(topo)
    assert [f["vdev"] for f in findings] == ["nvme2n1"]


# --- the layouts that are a warning, not a catastrophe --------------------

def test_a_bare_slog_warns_but_does_not_claim_pool_loss():
    # On any current ZFS the pool survives a lost SLOG; only synchronous writes
    # that were acknowledged but not yet written out are at risk. Calling that
    # critical would be the same overstatement the tool avoids elsewhere.
    topo = pt.parse_topology(_status(
        "\ttank          ONLINE       0     0     0\n"
        "\t  mirror-0    ONLINE       0     0     0\n"
        "\t    sda       ONLINE       0     0     0\n"
        "\t    sdb       ONLINE       0     0     0\n"
        "\tlogs\n"
        "\t  nvme0n1     ONLINE       0     0     0\n"))
    assert pt.redundancy_findings(topo) == [
        {"pool": "tank", "vdev": "nvme0n1", "tier": "log",
         "severity": "warn", "reason": "sync_writes"}]


def test_cache_and_spares_are_never_a_finding():
    # L2ARC holds nothing but copies; a spare holds nothing at all.
    topo = pt.parse_topology(_status(
        "\ttank          ONLINE       0     0     0\n"
        "\t  mirror-0    ONLINE       0     0     0\n"
        "\t    sda       ONLINE       0     0     0\n"
        "\t    sdb       ONLINE       0     0     0\n"
        "\tcache\n"
        "\t  nvme0n1     ONLINE       0     0     0\n"
        "\tspares\n"
        "\t  sdc         AVAIL\n"))
    assert pt.redundancy_findings(topo) == []
    tiers = {v["tier"] for v in topo["tank"]}
    assert "cache" in tiers and "spare" in tiers      # shown, just not flagged


def test_singular_section_headers_are_accepted():
    # The header is "logs"/"spares" on some versions and "log"/"spare" on
    # others; missing that would silently file a SLOG as a data vdev and call
    # it critical.
    topo = pt.parse_topology(_status(
        "\ttank          ONLINE       0     0     0\n"
        "\t  mirror-0    ONLINE       0     0     0\n"
        "\t    sda       ONLINE       0     0     0\n"
        "\t    sdb       ONLINE       0     0     0\n"
        "\tlog\n"
        "\t  nvme0n1     ONLINE       0     0     0\n"
        "\tspare\n"
        "\t  sdc         AVAIL\n"))
    assert [f["tier"] for f in pt.redundancy_findings(topo)] == ["log"]


# --- output that is not the happy path ------------------------------------

def test_a_resilvering_pool_still_parses():
    text = ("  pool: tank\n"
            " state: DEGRADED\n"
            "status: One or more devices is currently being resilvered.\n"
            "action: Wait for the resilver to complete.\n"
            "  scan: resilver in progress since Sat Aug  8 10:00:00 2026\n"
            "\t1.20T scanned at 1.1G/s, 800G issued at 720M/s, 2.40T total\n"
            "config:\n"
            "\n"
            "\tNAME             STATE     READ WRITE CKSUM\n"
            "\ttank             DEGRADED     0     0     0\n"
            "\t  mirror-0       DEGRADED     0     0     0\n"
            "\t    sda          ONLINE       0     0     0\n"
            "\t    replacing-1  DEGRADED     0     0     0\n"
            "\t      sdb        OFFLINE      0     0     0\n"
            "\t      sdc        ONLINE       0     0     0\n"
            "\nerrors: No known data errors\n")
    topo = pt.parse_topology(text)
    assert topo["tank"][0]["name"] == "mirror-0"
    assert topo["tank"][0]["redundancy"] == "mirror"
    assert pt.redundancy_findings(topo) == []      # a mirror mid-repair is not a design flaw


def test_error_counters_do_not_confuse_the_parser():
    topo = pt.parse_topology(_status(
        "\ttank          ONLINE       0     0  1.2K\n"
        "\t  mirror-0    ONLINE       0     0   600\n"
        "\t    sda       ONLINE       0     0   600\n"
        "\t    sdb       ONLINE       0     0     0\n"))
    assert topo["tank"][0]["devices"] == ["sda", "sdb"]


def test_several_pools_in_one_listing_stay_apart():
    text = (_status("\trpool         ONLINE       0     0     0\n"
                    "\t  mirror-0    ONLINE       0     0     0\n"
                    "\t    sda       ONLINE       0     0     0\n"
                    "\t    sdb       ONLINE       0     0     0\n", pool="rpool")
            + _status("\ttank          ONLINE       0     0     0\n"
                      "\t  sdc         ONLINE       0     0     0\n", pool="tank"))
    topo = pt.parse_topology(text)
    assert set(topo) == {"rpool", "tank"}
    assert [f["pool"] for f in pt.redundancy_findings(topo)] == ["tank"]


def test_garbage_and_empty_input_yield_nothing():
    assert pt.parse_topology("") == {}
    assert pt.parse_topology("no pools available\n") == {}
    assert pt.redundancy_findings({}) == []
    assert pt.redundancy_findings(pt.parse_topology("")) == []


# --- the per-pool summary the UI renders ----------------------------------

def test_summarize_reports_the_worst_severity():
    topo = pt.parse_topology(_status(
        "\ttank          ONLINE       0     0     0\n"
        "\t  mirror-0    ONLINE       0     0     0\n"
        "\t    sda       ONLINE       0     0     0\n"
        "\t    sdb       ONLINE       0     0     0\n"
        "\tlogs\n"
        "\t  nvme0n1     ONLINE       0     0     0\n"
        "\tspecial\n"
        "\t  nvme1n1     ONLINE       0     0     0\n"))
    s = pt.summarize(topo, "tank")
    assert s["worst"] == "crit"                 # special outranks the log warning
    assert len(s["findings"]) == 2
    assert len(s["vdevs"]) == 3


def test_summarize_of_a_healthy_pool():
    topo = pt.parse_topology(_status(
        "\ttank          ONLINE       0     0     0\n"
        "\t  mirror-0    ONLINE       0     0     0\n"
        "\t    sda       ONLINE       0     0     0\n"
        "\t    sdb       ONLINE       0     0     0\n"))
    s = pt.summarize(topo, "tank")
    assert s["worst"] == "ok" and s["findings"] == []


def test_summarize_of_an_unknown_pool_is_empty_not_an_error():
    assert pt.summarize({}, "nope") == {"vdevs": [], "findings": [], "worst": "ok"}


# --- verbatim output from four live hosts ---------------------------------
#
# Every fixture above indents with tabs; real `zpool status` on these PVE nodes
# indents with eight spaces. The parser reads relative depth, so both work --
# but nothing held that down, and a parser that only ever saw tabs is one
# formatting change away from silently finding no vdevs at all. These are
# pasted unmodified from the hosts, including the alignment padding that grows
# with the longest device name.

REAL_MIRRORED_BOOT = """  pool: rpool
 state: ONLINE
  scan: scrub repaired 0B in 00:16:03 with 0 errors on Sun Aug  9 00:40:04 2026
config:

        NAME                                                   STATE     READ WRITE CKSUM
        rpool                                                  ONLINE       0     0     0
          mirror-0                                             ONLINE       0     0     0
            ata-KINGSTON_SEDC600ME960G_50026B72836874A3-part3  ONLINE       0     0     0
            ata-KINGSTON_SEDC600ME960G_50026B72836875DE-part3  ONLINE       0     0     0

errors: No known data errors
"""

REAL_SINGLE_DISK_BOOT = """  pool: rpool
 state: ONLINE
  scan: scrub repaired 0B in 00:05:46 with 0 errors on Sun Aug  9 00:29:47 2026
config:

        NAME        STATE     READ WRITE CKSUM
        rpool       ONLINE       0     0     0
          sdb3      ONLINE       0     0     0

errors: No known data errors
"""

REAL_MIRROR_WITH_LOG_AND_CACHE = """  pool: tank
 state: ONLINE
  scan: scrub repaired 0B in 02:27:59 with 0 errors on Sun Aug  9 02:52:01 2026
config:

        NAME                                                STATE     READ WRITE CKSUM
        tank                                                ONLINE       0     0     0
          mirror-0                                          ONLINE       0     0     0
            sdc                                             ONLINE       0     0     0
            sdd                                             ONLINE       0     0     0
        logs
          ata-INTEL_SSDSC2BB480G4_CVWL422101EU480QGN-part3  ONLINE       0     0     0
        cache
          ata-INTEL_SSDSC2BB480G4_CVWL422101EU480QGN-part4  ONLINE       0     0     0

errors: No known data errors
"""

REAL_TWO_POOLS_ONE_DOCUMENT = """  pool: rpool
 state: ONLINE
  scan: scrub repaired 0B in 00:26:11 with 0 errors on Sun Aug  9 00:50:12 2026
config:

        NAME                                                   STATE     READ WRITE CKSUM
        rpool                                                  ONLINE       0     0     0
          mirror-0                                             ONLINE       0     0     0
            ata-KINGSTON_SEDC600ME960G_50026B7283687597-part3  ONLINE       0     0     0
            ata-KINGSTON_SEDC600ME960G_50026B7283687619-part3  ONLINE       0     0     0
        cache
          nvme-eui.49313736333832374ce0001837312020-part1      ONLINE       0     0     0

errors: No known data errors

  pool: tankhdd
 state: ONLINE
  scan: scrub repaired 0B in 00:04:08 with 0 errors on Sun Aug  9 00:28:10 2026
config:

        NAME                                               STATE     READ WRITE CKSUM
        tankhdd                                            ONLINE       0     0     0
          mirror-0                                         ONLINE       0     0     0
            ata-ST4000VN006-3CW104_ZW63AS2Z                ONLINE       0     0     0
            ata-ST4000VN006-3CW104_ZW63AJWW                ONLINE       0     0     0
        cache
          nvme-eui.49313736333832374ce0001837312020-part2  ONLINE       0     0     0

errors: No known data errors
"""


def test_space_indented_output_parses_like_tab_indented():
    topo = pt.parse_topology(REAL_MIRRORED_BOOT)
    assert topo["rpool"] == [{"tier": "data", "name": "mirror-0",
                              "redundancy": "mirror", "state": "ONLINE",
                              "devices": [
                                  "ata-KINGSTON_SEDC600ME960G_50026B72836874A3-part3",
                                  "ata-KINGSTON_SEDC600ME960G_50026B72836875DE-part3"]}]
    assert pt.redundancy_findings(topo) == []


def test_a_real_single_disk_boot_pool_is_critical():
    # Two of the four hosts boot from one disk. It is the single most common
    # Proxmox install and it is still a pool that one device failure ends --
    # the fleet's other two nodes mirror theirs, so it is not an unreachable bar.
    topo = pt.parse_topology(REAL_SINGLE_DISK_BOOT)
    assert topo["rpool"][0]["redundancy"] == "none"
    findings = pt.redundancy_findings(topo)
    assert [(f["severity"], f["tier"], f["vdev"]) for f in findings] == [
        ("crit", "data", "sdb3")]


def test_a_real_slog_and_l2arc_on_one_ssd():
    # part3 is the SLOG, part4 the L2ARC -- the same physical SSD serving both.
    # The bare SLOG warns (the pool survives losing it); the cache never does.
    topo = pt.parse_topology(REAL_MIRROR_WITH_LOG_AND_CACHE)
    assert [(v["tier"], v["redundancy"]) for v in topo["tank"]] == [
        ("data", "mirror"), ("log", "none"), ("cache", "none")]
    findings = pt.redundancy_findings(topo)
    assert [(f["severity"], f["tier"]) for f in findings] == [("warn", "log")]
    assert pt.summarize(topo, "tank")["worst"] == "warn"


def test_two_real_pools_in_one_document_stay_apart():
    topo = pt.parse_topology(REAL_TWO_POOLS_ONE_DOCUMENT)
    assert sorted(topo) == ["rpool", "tankhdd"]
    for pool in ("rpool", "tankhdd"):
        assert [v["tier"] for v in topo[pool]] == ["data", "cache"]
        assert pt.summarize(topo, pool)["worst"] == "ok"
    # The same NVMe serves both pools; that is two cache vdevs, not one shared.
    assert (topo["rpool"][1]["devices"][0]
            != topo["tankhdd"][1]["devices"][0])
