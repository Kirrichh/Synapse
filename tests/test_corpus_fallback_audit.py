"""Corpus-level static fallback audit regression tests."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.corpus_fallback_audit import build_report, write_report


def test_corpus_fallback_audit_runs_on_current_corpus(tmp_path):
    """The telemetry sprint script must parse/audit the corpus without aborting."""
    report = build_report(["examples", "tests"], base_dir=Path.cwd())

    assert report["schema_version"] == "2"
    assert report["version"] == "2.2.0-alpha3e"
    assert report["routing_model"] == "static_ast_plus_lowering_status_v22"
    assert report["files_scanned"] > 0
    assert report["files_parse_ok"] > 0
    assert report["files_parse_failed"] >= 0
    assert report["total_ast_nodes"] == report["total_cvm_compilable"] + report["total_fallback"]
    assert 0.0 <= report["corpus_coverage_ratio"] <= 1.0
    assert isinstance(report["corpus_fallback_by_node_type"], dict)
    assert isinstance(report["corpus_fallback_by_reason"], dict)

    out = write_report(report, str(tmp_path / "corpus_fallback.json"), base_dir=Path.cwd())
    loaded = json.loads(out.read_text())
    assert loaded["total_ast_nodes"] == report["total_ast_nodes"]
    assert loaded["corpus_fallback_by_node_type"] == report["corpus_fallback_by_node_type"]


def test_committed_alpha3e_corpus_report_is_present_and_actionable():
    """The sprint must commit the generated report used for prioritization."""
    report_path = Path("reports/corpus_fallback_alpha3e.json")
    assert report_path.exists()
    report = json.loads(report_path.read_text())

    assert report["version"] == "2.2.0-alpha3e"
    assert report["files_scanned"] >= report["files_parse_ok"]
    assert report["total_fallback"] > 0
    assert report["corpus_fallback_by_node_type"], "fallback distribution must not be empty"

    # The report is intentionally frequency-ranked.  The first entry is the
    # current largest blocker in the static corpus audit and drives the next RFC.
    first_count = next(iter(report["corpus_fallback_by_node_type"].values()))
    assert all(first_count >= count for count in report["corpus_fallback_by_node_type"].values())


def test_audit_distinguishes_lowerable_from_runtime_only_fallbacks():
    report = build_report(["examples", "tests"], base_dir=Path.cwd())

    assert report["total_fallback"] == 107
    assert report["runtime_only_fallbacks"] == 103
    assert report["corpus_lowerable_to_cvm_by_node_type"] == {"GovernedMemoryWrite": 4}
    assert "GovernedMemoryWrite" not in report["corpus_runtime_only_fallback_by_node_type"]
    assert report["lowering_status_by_node_type"]["GovernedMemoryWrite"]["lowering_status"] == "lowerable_to_cvm"

def test_committed_alpha3e_report_contains_lowering_methodology_fields():
    report = json.loads(Path("reports/corpus_fallback_alpha3e.json").read_text())

    assert report["schema_version"] == "2"
    assert report["routing_model"] == "static_ast_plus_lowering_status_v22"
    assert report["runtime_only_fallbacks"] == 103
    assert report["corpus_lowerable_to_cvm_by_node_type"]["GovernedMemoryWrite"] == 4
