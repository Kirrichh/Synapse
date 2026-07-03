from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import copy
import hashlib
import json
import os
import subprocess
import sys
from typing import Any, Callable

import pytest

from synapse.change import ControlledChangeRequest
from synapse.change.outcomes import APPLIED
from synapse.change.runner import execute_controlled_change
from synapse.experiments.swebench.gold_evidence import (
    GOLD_EVIDENCE_APPLICATION_NOT_APPLIED,
    GOLD_EVIDENCE_LIFECYCLE_NOT_APPLIED,
    GOLD_EVIDENCE_MISSING,
    GOLD_EVIDENCE_NOT_CREATED,
    GOLD_EVIDENCE_REF_MISMATCH,
    GOLD_EVIDENCE_REF_UNRESOLVABLE,
    GOLD_EVIDENCE_REPORT_BINDING_MISMATCH,
    GOLD_EVIDENCE_REPORT_HASH_MISMATCH,
    GOLD_EVIDENCE_STRUCTURAL_INVALID,
    GoldEvidence,
    validate_gold_evidence,
)


OLD_SOURCE = 'def bridge_value():\n    return "external-agent-artifact"\n'
NEW_SOURCE = 'def bridge_value():\n    return "canonical-controlled-change"\n'
TASK_PATH = "controlled_changes/tasks/gold_evidence_smoke.json"
PATCH_PATH = "controlled_changes/patches/gold_evidence_smoke.patch"
TARGET_REF = "refs/heads/controlled/gold-evidence"


@dataclass(frozen=True)
class GoldEvidenceFixture:
    repo_root: Path
    evidence: GoldEvidence
    report_json: dict[str, Any]
    report_path: Path
    base_commit: str
    observed_shape: dict[str, list[str]]


def run(cmd: list[str], cwd: Path, **kwargs):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, **kwargs)


def git(repo: Path, *args: str) -> str:
    completed = run(["git", *args], repo, check=True)
    return completed.stdout.strip()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def bridge_task() -> dict[str, object]:
    return {
        "schema": "synapse.controlled-change.task/v1",
        "task_id": "gold-evidence-smoke",
        "task_class": "TEST",
        "base_revision": "HEAD",
        "target_ref": TARGET_REF,
        "allowed_scope": ["src/example.py"],
        "patch_path": PATCH_PATH,
        "required_scaffold_paths": [
            TASK_PATH,
            PATCH_PATH,
            "src/example.py",
            "tests/test_example.py",
        ],
        "reproduction": {
            "command": [
                sys.executable,
                "-c",
                "from pathlib import Path; print(Path('src/example.py').read_text(encoding='utf-8'))",
            ],
            "committed_inputs": ["src/example.py", "tests/test_example.py"],
            "before": {
                "expected_exit_codes": [0],
                "combined_output_contains": ["external-agent-artifact"],
                "timeout_seconds": 10,
            },
            "after": {
                "expected_exit_codes": [0],
                "combined_output_contains": ["canonical-controlled-change"],
                "timeout_seconds": 10,
            },
        },
        "baseline_commands": [],
        "acceptance_commands": [
            [
                sys.executable,
                "-c",
                "from pathlib import Path; "
                "assert 'canonical-controlled-change' in Path('src/example.py').read_text(encoding='utf-8')",
            ]
        ],
        "full_suite_commands": [[sys.executable, "tests/test_example.py"]],
        "commit_message": "Apply gold evidence smoke patch",
    }


def build_controlled_change_fixture(repo: Path) -> tuple[str, str]:
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")

    write(repo / "README.md", "gold evidence validator fixture\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "seed")

    write(repo / "src" / "example.py", OLD_SOURCE)
    write(
        repo / "tests" / "test_example.py",
        "from pathlib import Path\n\n"
        "\n"
        "def test_bridge_value():\n"
        '    assert "canonical-controlled-change" in Path("src/example.py").read_text(encoding="utf-8")\n'
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    test_bridge_value()\n",
    )
    git(repo, "add", "src/example.py", "tests/test_example.py")
    git(repo, "commit", "-m", "pre-patch source")

    write(repo / "src" / "example.py", NEW_SOURCE)
    patch_text = run(["git", "diff", "--", "src/example.py"], repo, check=True).stdout
    assert patch_text
    write(repo / "src" / "example.py", OLD_SOURCE)

    write(repo / PATCH_PATH, patch_text)
    write(repo / TASK_PATH, json.dumps(bridge_task(), indent=2, sort_keys=True) + "\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "gold evidence fixture base")
    base = git(repo, "rev-parse", "HEAD")
    return base, TASK_PATH


def require_report_shape(report: dict[str, Any]) -> dict[str, list[str]]:
    expected = {
        "verified_result": ["verified_commit"],
        "trusted_inputs": ["base_commit", "task_contract_sha256", "patch_sha256"],
        "evidence": ["evidence_ref", "result_status"],
        "application": ["status", "result_status"],
        "lifecycle": ["outcome"],
    }
    for section, keys in expected.items():
        assert section in report, f"missing report section: {section}"
        assert isinstance(report[section], dict), f"report section is not object: {section}"
        for key in keys:
            assert key in report[section], f"missing report key: {section}.{key}"
    return {
        section: sorted(report[section].keys())
        for section in expected
    }


@pytest.fixture(scope="module")
def gold_evidence_fixture(tmp_path_factory: pytest.TempPathFactory) -> GoldEvidenceFixture:
    repo = tmp_path_factory.mktemp("gold-evidence-repo") / "repo"
    base, task_path = build_controlled_change_fixture(repo)

    old_cwd = Path.cwd()
    try:
        os.chdir(repo)
        result = execute_controlled_change(
            ControlledChangeRequest(
                base=base,
                task_path=task_path,
                keep_worktree=False,
                environment_kind="TEST",
            )
        )
    finally:
        os.chdir(old_cwd)

    assert result.outcome == APPLIED
    assert result.verified_commit is not None
    assert result.evidence_ref is not None
    assert result.report_path is not None
    assert result.base_revision is not None

    report_path = Path(result.report_path)
    if not report_path.is_absolute():
        report_path = repo / report_path
    report_path = report_path.resolve()
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes.decode("utf-8"))
    observed_shape = require_report_shape(report)

    assert report["lifecycle"]["outcome"] == APPLIED
    assert report["application"]["result_status"] == APPLIED
    assert report["verified_result"]["verified_commit"] == result.verified_commit
    assert report["evidence"]["evidence_ref"] == result.evidence_ref
    assert report["trusted_inputs"]["base_commit"] == result.base_revision

    report_relative = str(report_path.relative_to(repo.resolve()))
    evidence = GoldEvidence(
        evidence_ref=result.evidence_ref,
        verified_commit=result.verified_commit,
        report_path=report_relative,
        report_sha256=hashlib.sha256(report_bytes).hexdigest(),
        base_sha=result.base_revision,
        task_contract_sha256=report["trusted_inputs"]["task_contract_sha256"],
        patch_sha256=report["trusted_inputs"]["patch_sha256"],
    )
    return GoldEvidenceFixture(
        repo_root=repo.resolve(),
        evidence=evidence,
        report_json=report,
        report_path=report_path,
        base_commit=base,
        observed_shape=observed_shape,
    )


def write_report_copy(
    tmp_path: Path,
    fixture: GoldEvidenceFixture,
    mutate: Callable[[dict[str, Any]], None],
) -> tuple[GoldEvidence, Path]:
    copied = copy.deepcopy(fixture.report_json)
    mutate(copied)
    report_path = tmp_path / "report.json"
    report_bytes = json.dumps(copied, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    report_path.write_bytes(report_bytes)
    return (
        replace(
            fixture.evidence,
            report_path=report_path.name,
            report_sha256=hashlib.sha256(report_bytes).hexdigest(),
            evidence_ref=copied["evidence"]["evidence_ref"],
        ),
        tmp_path,
    )


def different_sha256(value: str) -> str:
    return "0" * 64 if value != "0" * 64 else "1" * 64


def different_commit(value: str) -> str:
    return "0" * 40 if value != "0" * 40 else "1" * 40


def test_positive_validation_accepts_real_controlled_change_report(
    gold_evidence_fixture: GoldEvidenceFixture,
) -> None:
    result = validate_gold_evidence(
        gold_evidence_fixture.evidence,
        repo_root=gold_evidence_fixture.repo_root,
    )

    assert result.ok is True
    assert result.failure_code is None
    assert result.detail is None


def test_validation_is_independent_of_process_cwd(
    tmp_path: Path,
    gold_evidence_fixture: GoldEvidenceFixture,
) -> None:
    other_cwd = tmp_path / "other"
    other_cwd.mkdir()
    old_cwd = Path.cwd()
    try:
        os.chdir(other_cwd)
        result = validate_gold_evidence(
            gold_evidence_fixture.evidence,
            repo_root=gold_evidence_fixture.repo_root,
        )
    finally:
        os.chdir(old_cwd)

    assert result.ok is True
    assert result.failure_code is None


def test_empty_required_gold_evidence_field_fails_missing(
    gold_evidence_fixture: GoldEvidenceFixture,
) -> None:
    evidence = replace(gold_evidence_fixture.evidence, report_path="")

    result = validate_gold_evidence(evidence, repo_root=gold_evidence_fixture.repo_root)

    assert result.ok is False
    assert result.failure_code == GOLD_EVIDENCE_MISSING
    assert "report_path" in result.detail


def test_report_hash_mismatch_fails_before_bindings(
    tmp_path: Path,
    gold_evidence_fixture: GoldEvidenceFixture,
) -> None:
    report_bytes = bytearray(gold_evidence_fixture.report_path.read_bytes())
    index = report_bytes.index(b"personal_slice")
    report_bytes[index] = ord("q")
    report_path = tmp_path / "report.json"
    report_path.write_bytes(report_bytes)
    evidence = replace(
        gold_evidence_fixture.evidence,
        report_path=report_path.name,
    )

    result = validate_gold_evidence(
        evidence,
        repo_root=gold_evidence_fixture.repo_root,
        report_root=tmp_path,
    )

    assert result.ok is False
    assert result.failure_code == GOLD_EVIDENCE_REPORT_HASH_MISMATCH
    assert "report_sha256" in result.detail


def test_unresolvable_evidence_ref_fails_after_report_binding(
    tmp_path: Path,
    gold_evidence_fixture: GoldEvidenceFixture,
) -> None:
    missing_ref = "refs/synapse/change/evidence/nonexistent-run"
    evidence, report_root = write_report_copy(
        tmp_path,
        gold_evidence_fixture,
        lambda report: report["evidence"].update({"evidence_ref": missing_ref}),
    )

    result = validate_gold_evidence(
        replace(evidence, evidence_ref=missing_ref),
        repo_root=gold_evidence_fixture.repo_root,
        report_root=report_root,
    )

    assert result.ok is False
    assert result.failure_code == GOLD_EVIDENCE_REF_UNRESOLVABLE
    assert "evidence_ref" in result.detail


def test_evidence_ref_resolving_to_wrong_commit_fails_ref_mismatch(
    tmp_path: Path,
    gold_evidence_fixture: GoldEvidenceFixture,
) -> None:
    wrong_ref = "refs/synapse/change/evidence/wrong-commit"
    run(
        ["git", "update-ref", wrong_ref, gold_evidence_fixture.base_commit],
        gold_evidence_fixture.repo_root,
        check=True,
    )
    evidence, report_root = write_report_copy(
        tmp_path,
        gold_evidence_fixture,
        lambda report: report["evidence"].update({"evidence_ref": wrong_ref}),
    )

    result = validate_gold_evidence(
        replace(evidence, evidence_ref=wrong_ref),
        repo_root=gold_evidence_fixture.repo_root,
        report_root=report_root,
    )

    assert result.ok is False
    assert result.failure_code == GOLD_EVIDENCE_REF_MISMATCH
    assert "verified_commit" in result.detail


def test_report_verified_commit_binding_mismatch_fails(
    tmp_path: Path,
    gold_evidence_fixture: GoldEvidenceFixture,
) -> None:
    other_commit = different_commit(gold_evidence_fixture.evidence.verified_commit)
    evidence, report_root = write_report_copy(
        tmp_path,
        gold_evidence_fixture,
        lambda report: report["verified_result"].update({"verified_commit": other_commit}),
    )

    result = validate_gold_evidence(
        evidence,
        repo_root=gold_evidence_fixture.repo_root,
        report_root=report_root,
    )

    assert result.ok is False
    assert result.failure_code == GOLD_EVIDENCE_REPORT_BINDING_MISMATCH
    assert "verified_result.verified_commit" in result.detail


def test_report_task_contract_sha256_binding_mismatch_fails(
    tmp_path: Path,
    gold_evidence_fixture: GoldEvidenceFixture,
) -> None:
    other_sha = different_sha256(gold_evidence_fixture.evidence.task_contract_sha256)
    evidence, report_root = write_report_copy(
        tmp_path,
        gold_evidence_fixture,
        lambda report: report["trusted_inputs"].update({"task_contract_sha256": other_sha}),
    )

    result = validate_gold_evidence(
        evidence,
        repo_root=gold_evidence_fixture.repo_root,
        report_root=report_root,
    )

    assert result.ok is False
    assert result.failure_code == GOLD_EVIDENCE_REPORT_BINDING_MISMATCH
    assert "trusted_inputs.task_contract_sha256" in result.detail


def test_report_patch_sha256_binding_mismatch_fails(
    tmp_path: Path,
    gold_evidence_fixture: GoldEvidenceFixture,
) -> None:
    other_sha = different_sha256(gold_evidence_fixture.evidence.patch_sha256)
    evidence, report_root = write_report_copy(
        tmp_path,
        gold_evidence_fixture,
        lambda report: report["trusted_inputs"].update({"patch_sha256": other_sha}),
    )

    result = validate_gold_evidence(
        evidence,
        repo_root=gold_evidence_fixture.repo_root,
        report_root=report_root,
    )

    assert result.ok is False
    assert result.failure_code == GOLD_EVIDENCE_REPORT_BINDING_MISMATCH
    assert "trusted_inputs.patch_sha256" in result.detail


def test_application_result_status_not_applied_fails_even_with_legacy_status(
    tmp_path: Path,
    gold_evidence_fixture: GoldEvidenceFixture,
) -> None:
    evidence, report_root = write_report_copy(
        tmp_path,
        gold_evidence_fixture,
        lambda report: report["application"].update(
            {
                "result_status": "NOT_APPLIED",
                "status": "COMPLETED_LEGACY_SEMANTICS",
            }
        ),
    )

    result = validate_gold_evidence(
        evidence,
        repo_root=gold_evidence_fixture.repo_root,
        report_root=report_root,
    )

    assert result.ok is False
    assert result.failure_code == GOLD_EVIDENCE_APPLICATION_NOT_APPLIED
    assert "application.result_status" in result.detail


def test_lifecycle_outcome_not_applied_fails(
    tmp_path: Path,
    gold_evidence_fixture: GoldEvidenceFixture,
) -> None:
    evidence, report_root = write_report_copy(
        tmp_path,
        gold_evidence_fixture,
        lambda report: report["lifecycle"].update({"outcome": "NOT_APPLIED"}),
    )

    result = validate_gold_evidence(
        evidence,
        repo_root=gold_evidence_fixture.repo_root,
        report_root=report_root,
    )

    assert result.ok is False
    assert result.failure_code == GOLD_EVIDENCE_LIFECYCLE_NOT_APPLIED
    assert "lifecycle.outcome" in result.detail


def test_evidence_result_status_not_created_fails(
    tmp_path: Path,
    gold_evidence_fixture: GoldEvidenceFixture,
) -> None:
    evidence, report_root = write_report_copy(
        tmp_path,
        gold_evidence_fixture,
        lambda report: report["evidence"].update({"result_status": "NOT_CREATED"}),
    )

    result = validate_gold_evidence(
        evidence,
        repo_root=gold_evidence_fixture.repo_root,
        report_root=report_root,
    )

    assert result.ok is False
    assert result.failure_code == GOLD_EVIDENCE_NOT_CREATED
    assert "evidence.result_status" in result.detail


def test_evidence_ref_outside_namespace_fails_structural_validation(
    gold_evidence_fixture: GoldEvidenceFixture,
) -> None:
    evidence = replace(
        gold_evidence_fixture.evidence,
        evidence_ref="refs/heads/controlled/verified",
    )

    result = validate_gold_evidence(evidence, repo_root=gold_evidence_fixture.repo_root)

    assert result.ok is False
    assert result.failure_code == GOLD_EVIDENCE_STRUCTURAL_INVALID
    assert "evidence_ref" in result.detail
