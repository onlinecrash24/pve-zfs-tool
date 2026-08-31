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
