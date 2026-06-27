"""Email verdict derivation: structured block first, negation-aware heuristic
fallback. Guards the 'keine kritischen Probleme -> crit' false positive."""

from app import notifications as n


# --- structured block parsing --------------------------------------------

def test_parse_llm_verdict_block():
    content = "body\n[VERDICT: crit]\n[CRITICAL_FINDINGS: 2]\n[WARNINGS: 1]\n"
    parsed = n._parse_llm_verdict(content)
    assert parsed == ("crit", 2, 1)


def test_parse_llm_verdict_absent():
    assert n._parse_llm_verdict("no block here") is None


# --- negation-aware heuristic fallback -----------------------------------

def test_heuristic_negated_critical_is_ok():
    # the exact false-positive that shipped a bogus "critical" email
    verdict, crit, warn = n._heuristic_verdict("Keine kritischen Probleme erkannt.")
    assert verdict == "ok"
    assert crit == 0


def test_heuristic_standalone_legend_lines_ignored():
    # standalone legend/glossary lines (one marker per line) must not be
    # counted as findings -- this is what the heuristic special-cases.
    report = "✅ OK\n⚠️ Warnung\n❌ Kritisch"
    verdict, crit, warn = n._heuristic_verdict(report)
    assert verdict == "ok"
    assert crit == 0 and warn == 0


def test_heuristic_real_critical_counts():
    verdict, crit, warn = n._heuristic_verdict("Pool rpool ist DEGRADED - kritischer Fehler!")
    assert verdict == "crit"
    assert crit >= 1


def test_heuristic_warning_only():
    verdict, crit, warn = n._heuristic_verdict("Achtung: Belegung ueber 80%, Warnung.")
    assert verdict == "warn"


# --- _summarize_ai_report -- block wins over prose -----------------------

def test_summary_uses_block_over_prose():
    # green prose but an explicit crit block -> block is authoritative here
    content = "Alles gruen.\n[VERDICT: crit]\n[CRITICAL_FINDINGS: 1]\n[WARNINGS: 0]"
    verdict, text = n._summarize_ai_report(content, lang="de")
    assert verdict == "crit"
    assert "Handlung" in text or "kritische" in text.lower()


def test_summary_all_clear_de():
    verdict, text = n._summarize_ai_report("Keine kritischen Probleme.", lang="de")
    assert verdict == "ok"
    assert "grünen" in text or "grun" in text.lower() or "All clear" in text


def test_summary_all_clear_en():
    verdict, text = n._summarize_ai_report("Everything healthy, no findings.", lang="en")
    assert verdict == "ok"
    assert "clear" in text.lower()
