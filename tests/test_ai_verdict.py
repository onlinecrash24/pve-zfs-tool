"""Fact-based section status + verdict for AI reports -- the logic that fixed
the 'green report, critical email' contradiction."""

import pytest
from app import ai_reports as ai


def _host(**kw):
    base = {"pools": [], "smart": {}, "retention_analysis": {}, "errors": []}
    base.update(kw)
    return base


# --- _pct ----------------------------------------------------------------

def test_pct_parses_percent_strings():
    assert ai._pct("49%") == 49.0
    assert ai._pct("0%") == 0.0
    assert ai._pct(None) is None
    assert ai._pct("n/a") is None


# --- _compute_section_statuses -------------------------------------------

def test_all_green_host():
    data = {"hosts": [_host(
        pools=[{"name": "rpool", "cap": "49%", "health": "ONLINE"}],
        smart={"pools": {"rpool": [{"status": "PASSED"}]}},
        retention_analysis={"per_label": {"daily": {"stale_datasets": [], "count_mismatches": [], "gaps": []}}},
    )]}
    statuses, overall = ai._compute_section_statuses(data)
    assert overall == "ok"
    assert all(statuses[k] == "ok" for k in (1, 2, 3, 4, 5, 6, 7))


def test_capacity_warn_and_crit_thresholds():
    warn = {"hosts": [_host(pools=[{"name": "p", "cap": "85%", "health": "ONLINE"}])]}
    crit = {"hosts": [_host(pools=[{"name": "p", "cap": "96%", "health": "ONLINE"}])]}
    assert ai._compute_section_statuses(warn)[0][2] == "warn"
    assert ai._compute_section_statuses(crit)[0][2] == "crit"


def test_degraded_pool_is_anomaly_crit():
    data = {"hosts": [_host(pools=[{"name": "p", "cap": "10%", "health": "DEGRADED"}])]}
    statuses, overall = ai._compute_section_statuses(data)
    assert statuses[6] == "crit"
    assert overall == "crit"


def test_failed_smart_is_crit():
    data = {"hosts": [_host(smart={"pools": {"p": [{"status": "FAILED"}]}})]}
    statuses, overall = ai._compute_section_statuses(data)
    assert statuses[5] == "crit"
    assert overall == "crit"


def test_retention_issue_is_snapshot_warn():
    data = {"hosts": [_host(
        retention_analysis={"per_label": {"hourly": {"gaps": [{"x": 1}]}}})]}
    statuses, _ = ai._compute_section_statuses(data)
    assert statuses[4] == "warn"


def test_collection_errors_warn_anomalies():
    data = {"hosts": [_host(errors=["Pools: boom"])]}
    statuses, _ = ai._compute_section_statuses(data)
    assert statuses[6] == "warn"


def test_overall_is_worst_section():
    data = {"hosts": [_host(
        pools=[{"name": "p", "cap": "85%", "health": "ONLINE"}],      # warn
        smart={"pools": {"p": [{"status": "FAILED"}]}},               # crit
    )]}
    _, overall = ai._compute_section_statuses(data)
    assert overall == "crit"


# --- _inject_section_tags -------------------------------------------------

def test_inject_adds_tags_by_section_number():
    statuses = {1: "ok", 2: "warn", 5: "crit", 7: "ok"}
    body = "## 1. Gesamtstatus\ntext\n## 2. Kapazitaet\ntext\n## 5. SMART\ntext\n## 7. Empfehlungen\n"
    out = ai._inject_section_tags(body, statuses)
    assert "## [OK] 1. Gesamtstatus" in out
    assert "## [WARN] 2. Kapazitaet" in out
    assert "## [CRIT] 5. SMART" in out
    assert "## [OK] 7. Empfehlungen" in out


def test_inject_replaces_existing_tag():
    statuses = {1: "crit"}
    out = ai._inject_section_tags("## [OK] 1. Gesamtstatus\n", statuses)
    assert "## [CRIT] 1. Gesamtstatus" in out
    assert "[OK]" not in out


def test_inject_leaves_non_section_lines_untouched():
    out = ai._inject_section_tags("- a bullet\nplain text\n", {1: "ok"})
    assert out == "- a bullet\nplain text\n"


# --- _extract_and_strip_verdict_block ------------------------------------

def test_verdict_block_extracted_and_stripped():
    content = "Report body.\n\n[VERDICT: warn]\n[CRITICAL_FINDINGS: 0]\n[WARNINGS: 1]\n"
    cleaned, meta = ai._extract_and_strip_verdict_block(content)
    assert meta["verdict"] == "warn"
    assert meta["critical_findings"] == 0
    assert meta["warnings"] == 1
    assert "[VERDICT" not in cleaned
    assert "Report body." in cleaned


def test_missing_verdict_block_returns_empty_meta():
    cleaned, meta = ai._extract_and_strip_verdict_block("Just a report, no block.\n")
    assert meta == {}
    assert "Just a report" in cleaned
