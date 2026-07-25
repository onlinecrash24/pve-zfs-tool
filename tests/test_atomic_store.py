"""The JSON stores (reports/config/hosts) are written by concurrent threads --
the scheduler and UI async report threads both call _add_report. Writes must be
atomic (temp file + os.replace, so a reader never sees a partial file) and the
read-modify-write must hold the lock across the whole sequence, or a concurrent
completion silently drops a report / blanks the history."""

import json
import threading

from app import ai_reports as ar


def test_atomic_write_json_no_temp_leftover(tmp_path):
    p = tmp_path / "x.json"
    ar._atomic_write_json(str(p), {"a": 1, "ü": "ö"})
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1, "ü": "ö"}
    assert list(tmp_path.glob("*.tmp*")) == []      # temp file was renamed away


def test_concurrent_add_report_keeps_all(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "AI_REPORTS_FILE", str(tmp_path / "reports.json"))
    monkeypatch.setattr(ar, "_ensure_data_dir", lambda: None)
    monkeypatch.setattr(ar, "load_config", lambda: {"max_reports": 10_000})

    n = 60
    threads = [threading.Thread(target=ar._add_report, args=({"id": i},)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    reports = ar.load_reports()
    assert sorted(r["id"] for r in reports) == list(range(n))   # none lost


def test_add_report_respects_max(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "AI_REPORTS_FILE", str(tmp_path / "reports.json"))
    monkeypatch.setattr(ar, "_ensure_data_dir", lambda: None)
    monkeypatch.setattr(ar, "load_config", lambda: {"max_reports": 3})
    for i in range(5):
        ar._add_report({"id": i})
    reports = ar.load_reports()
    assert [r["id"] for r in reports] == [4, 3, 2]   # newest first, capped at 3
