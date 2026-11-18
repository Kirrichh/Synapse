"""Mutation reporting must not persist or print private subprocess diagnostics."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


@pytest.fixture
def reporting_run(tmp_path, monkeypatch):
    source = Path(__file__).parent / "mutation/run.py"
    spec = importlib.util.spec_from_file_location("ui_mutation_reporting", source)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    # All sensitive-looking input is invented, never copied from a user.
    canary = "SYNTHETIC_PRIVATE_DIAGNOSTIC /home/invented-person/private-input"
    seed = {"id": "SYNTHETIC-REFUSAL", "kind": "python", "behavior": "refusal",
            "target": "subject.py", "before": "accept = False", "after": "accept = True",
            "cases": ["acceptance/synthetic_refusal.py"]}
    seed_path = tmp_path / "seeds.json"
    seed_path.write_text(json.dumps([seed]))
    workspace = tmp_path / "scratch"
    workspace.mkdir()
    args = SimpleNamespace(seeds=[seed_path], only=None, output=tmp_path / "evidence",
                           workspace=workspace, python="python", cargo="cargo", timeout=1)

    def copy_subject(destination):
        (destination / "subject.py").write_text(seed["before"])

    monkeypatch.setattr(runner, "copy_subject", copy_subject)
    return runner, args, canary


@pytest.mark.parametrize("outcome", ["KILLED", "SURVIVED", "INVALID", "TIMEOUT"])
def test_mutation_report_excludes_raw_diagnostics(reporting_run, monkeypatch, capsys, outcome):
    runner, args, canary = reporting_run
    calls = 0

    def execute_process(command, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(command, 0, canary)
        if outcome == "TIMEOUT":
            raise subprocess.TimeoutExpired(command + [canary], 1, output=canary)
        code = 0 if outcome == "SURVIVED" else 1
        marker = "1 failed" if outcome == "KILLED" else "infrastructure failure"
        return subprocess.CompletedProcess(command, code, marker + "\n" + canary)

    monkeypatch.setattr(runner.subprocess, "run", execute_process)
    assert runner.main(args) == (0 if outcome == "KILLED" else 1)
    record = json.loads((args.output / "results.json").read_text())["mutants"][0]
    assert record["result"] == outcome
    assert record["selected_acceptance"] == ["acceptance/synthetic_refusal.py"]
    for artifact in args.output.rglob("*"):
        if artifact.is_file():
            assert canary not in artifact.read_text()
    captured = capsys.readouterr()
    assert canary not in captured.out + captured.err


def test_failed_baseline_does_not_export_diagnostics(reporting_run, monkeypatch, capsys):
    runner, args, canary = reporting_run

    def failed_process(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "1 failed\n" + canary)

    monkeypatch.setattr(runner.subprocess, "run", failed_process)
    with pytest.raises(RuntimeError) as failure:
        runner.main(args)
    assert "Baseline failed" in str(failure.value)
    assert canary not in str(failure.value)
    for artifact in args.output.rglob("*"):
        if artifact.is_file():
            assert canary not in artifact.read_text()
    captured = capsys.readouterr()
    assert canary not in captured.out + captured.err
