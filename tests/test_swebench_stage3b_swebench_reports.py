"""Stage 3B SWE-bench report parser tests."""

from __future__ import annotations

from pathlib import Path
import json

from synapse.experiments.swebench.swebench_reports import (
    parse_swebench_report,
    resolve_swebench_instance_report_path,
    swebench_model_log_dir_name,
)


def test_model_log_dir_name_and_report_resolver(tmp_path):
    assert swebench_model_log_dir_name("plain-model") == "plain-model"
    assert swebench_model_log_dir_name("org/model") == "org__model"

    path = resolve_swebench_instance_report_path(
        tmp_path,
        run_id="run-1",
        model_name_or_path="org/model",
        instance_id="repo__issue-1",
    )

    assert path == tmp_path / "logs" / "run_evaluation" / "run-1" / "org__model" / "repo__issue-1" / "report.json"


def _write_report(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_parse_valid_instance_level_resolved_and_unresolved(tmp_path):
    report = tmp_path / "report.json"
    _write_report(
        report,
        {
            "repo__issue-1": {
                "patch_is_None": False,
                "patch_exists": True,
                "patch_successfully_applied": True,
                "resolved": True,
                "tests_status": {},
            }
        },
    )

    resolved = parse_swebench_report(report, "repo__issue-1")
    assert resolved.target_instance_found is True
    assert resolved.resolved is True
    assert resolved.diagnostics["infra_error"] is False

    _write_report(report, {"repo__issue-1": {"resolved": False}})
    unresolved = parse_swebench_report(report, "repo__issue-1")
    assert unresolved.target_instance_found is True
    assert unresolved.resolved is False
    assert unresolved.diagnostics["infra_error"] is False


def test_parse_missing_empty_malformed_and_aggregate_reports(tmp_path):
    missing = parse_swebench_report(tmp_path / "missing.json", "repo__issue-1")
    assert missing.diagnostics["failure_reason"] == "swebench_report_missing"

    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    assert parse_swebench_report(empty, "repo__issue-1").diagnostics["failure_reason"] == "swebench_report_empty"

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{", encoding="utf-8")
    assert parse_swebench_report(malformed, "repo__issue-1").diagnostics["failure_reason"] == "swebench_report_malformed"

    aggregate = tmp_path / "aggregate.json"
    _write_report(aggregate, {"total_instances": 1, "resolved_instances": []})
    assert parse_swebench_report(aggregate, "repo__issue-1").diagnostics["failure_reason"] == "swebench_report_not_instance_level"


def test_parse_target_missing_resolved_missing_and_resolved_not_bool(tmp_path):
    report = tmp_path / "report.json"

    _write_report(report, {"other": {"resolved": True}})
    assert parse_swebench_report(report, "repo__issue-1").diagnostics["failure_reason"] == "swebench_report_target_missing"

    _write_report(report, {"repo__issue-1": {"tests_status": {}}})
    assert parse_swebench_report(report, "repo__issue-1").diagnostics["failure_reason"] == "swebench_report_resolved_missing"

    _write_report(report, {"repo__issue-1": {"resolved": "yes"}})
    assert parse_swebench_report(report, "repo__issue-1").diagnostics["failure_reason"] == "swebench_report_resolved_not_bool"
