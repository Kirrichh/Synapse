from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import inspect
import json
import os
import subprocess
import sys

import pytest

import synapse.experiments.swebench.gold_attempt_writer as writer_module
from synapse.change import ControlledChangeRequest
from synapse.change.outcomes import APPLIED
from synapse.change.runner import execute_controlled_change
from synapse.experiments.swebench.gold_attempt_writer import (
    GOLD_ATTEMPT_JSONL_WRITE_FAILED,
    GOLD_ATTEMPT_STATUS_INVALID,
    GOLD_ATTEMPT_WRITE_FAILED,
    GOLD_ATTEMPT_WRITTEN,
    GOLD_EVIDENCE_REJECTED,
    GOLD_ORACLE_BINDING_REQUIRED,
    GoldAttemptWriteResult,
    GoldAttemptWriter,
)
from synapse.experiments.swebench.gold_evidence import (
    GOLD_EVIDENCE_MISSING,
    GOLD_EVIDENCE_REPORT_UNREADABLE,
    GoldEvidence,
    GoldEvidenceValidationResult,
)


OLD_SOURCE = 'def bridge_value():\n    return "external-agent-artifact"\n'
NEW_SOURCE = 'def bridge_value():\n    return "canonical-controlled-change"\n'
TASK_PATH = "controlled_changes/tasks/gold_attempt_writer_smoke.json"
PATCH_PATH = "controlled_changes/patches/gold_attempt_writer_smoke.patch"
TARGET_REF = "refs/heads/controlled/gold-attempt-writer"


@dataclass(frozen=True)
class RealGoldEvidenceFixture:
    repo_root: Path
    evidence: GoldEvidence


def run(cmd: list[str], cwd: Path, **kwargs):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, **kwargs)


def git(repo: Path, *args: str) -> str:
    completed = run(["git", *args], repo, check=True)
    return completed.stdout.strip()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def sample_evidence() -> GoldEvidence:
    return GoldEvidence(
        evidence_ref="refs/synapse/change/evidence/sample",
        verified_commit="1" * 40,
        report_path="report.json",
        report_sha256="2" * 64,
        base_sha="3" * 40,
        task_contract_sha256="4" * 64,
        patch_sha256="5" * 64,
    )


def bridge_task() -> dict[str, object]:
    return {
        "schema": "synapse.controlled-change.task/v1",
        "task_id": "gold-attempt-writer-smoke",
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
        "commit_message": "Apply gold attempt writer smoke patch",
    }


def build_controlled_change_fixture(repo: Path) -> tuple[str, str]:
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")

    write(repo / "README.md", "gold attempt writer fixture\n")
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
    git(repo, "commit", "-m", "gold attempt writer fixture base")
    return git(repo, "rev-parse", "HEAD"), TASK_PATH


@pytest.fixture(scope="module")
def real_gold_evidence(tmp_path_factory: pytest.TempPathFactory) -> RealGoldEvidenceFixture:
    repo = tmp_path_factory.mktemp("gold-attempt-writer-repo") / "repo"
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
    evidence = GoldEvidence(
        evidence_ref=result.evidence_ref,
        verified_commit=result.verified_commit,
        report_path=str(report_path.relative_to(repo.resolve())),
        report_sha256=hashlib.sha256(report_bytes).hexdigest(),
        base_sha=result.base_revision,
        task_contract_sha256=report["trusted_inputs"]["task_contract_sha256"],
        patch_sha256=report["trusted_inputs"]["patch_sha256"],
    )
    return RealGoldEvidenceFixture(repo_root=repo.resolve(), evidence=evidence)


def valid_result() -> GoldEvidenceValidationResult:
    return GoldEvidenceValidationResult(ok=True, failure_code=None, detail=None)


def invalid_result() -> GoldEvidenceValidationResult:
    return GoldEvidenceValidationResult(
        ok=False,
        failure_code=GOLD_EVIDENCE_REPORT_UNREADABLE,
        detail="report_path unreadable",
    )


def test_success_like_status_requires_evidence(tmp_path: Path) -> None:
    writer = GoldAttemptWriter(tmp_path, repo_root=tmp_path)

    result = writer.write_attempt(
        attempt_id="a1",
        run_id="r1",
        status="GOLD_APPLIED_WITH_EVIDENCE",
        gold_evidence=None,
    )

    records = read_jsonl(tmp_path / "gold_attempts.jsonl")
    assert result.ok is False
    assert result.status == GOLD_EVIDENCE_REJECTED
    assert result.failure_code == GOLD_EVIDENCE_MISSING
    assert "gold_evidence" in result.detail
    assert len(records) == 1
    assert records[0]["status"] == GOLD_EVIDENCE_REJECTED
    assert records[0]["requested_status"] == "GOLD_APPLIED_WITH_EVIDENCE"
    assert records[0]["gold_evidence"] is None
    assert records[0]["failure_code"] == GOLD_EVIDENCE_MISSING
    assert records[0]["detail"]
    assert not any(record["status"] == "GOLD_APPLIED_WITH_EVIDENCE" for record in records)


def test_success_like_status_with_invalid_evidence_is_rejected(tmp_path: Path) -> None:
    writer = GoldAttemptWriter(tmp_path, repo_root=tmp_path)

    result = writer.write_attempt(
        attempt_id="a2",
        run_id="r2",
        status="GOLD_APPLIED_WITH_EVIDENCE",
        gold_evidence=sample_evidence(),
    )

    records = read_jsonl(tmp_path / "gold_attempts.jsonl")
    assert result.ok is False
    assert result.status == GOLD_EVIDENCE_REJECTED
    assert result.failure_code == GOLD_EVIDENCE_REPORT_UNREADABLE
    assert "report_path unreadable or invalid JSON" in result.detail
    assert len(records) == 1
    assert records[0]["status"] == GOLD_EVIDENCE_REJECTED
    assert records[0]["requested_status"] == "GOLD_APPLIED_WITH_EVIDENCE"
    assert records[0]["gold_evidence"] is None
    assert records[0]["failure_code"] == GOLD_EVIDENCE_REPORT_UNREADABLE
    assert records[0]["detail"]
    assert not any(record["status"] == "GOLD_APPLIED_WITH_EVIDENCE" for record in records)


def test_success_like_status_with_valid_evidence_writes_one_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(writer_module, "validate_gold_evidence", lambda *args, **kwargs: valid_result())
    evidence = sample_evidence()
    writer = GoldAttemptWriter(tmp_path, repo_root=tmp_path)

    result = writer.write_attempt(
        attempt_id="a3",
        run_id="r3",
        status="GOLD_APPLIED_WITH_EVIDENCE",
        gold_evidence=evidence,
        payload={"source": "unit"},
    )

    records = read_jsonl(tmp_path / "gold_attempts.jsonl")
    assert result.ok is True
    assert result.status == GOLD_ATTEMPT_WRITTEN
    assert len(records) == 1
    assert records[0]["schema"] == "synapse.experiments.swebench.gold_attempt/v1"
    assert records[0]["arm"] == "GOLD"
    assert records[0]["status"] == "GOLD_APPLIED_WITH_EVIDENCE"
    assert records[0]["requested_status"] == "GOLD_APPLIED_WITH_EVIDENCE"
    assert records[0]["gold_evidence"] == {
        "evidence_ref": evidence.evidence_ref,
        "verified_commit": evidence.verified_commit,
        "report_path": evidence.report_path,
        "report_sha256": evidence.report_sha256,
        "base_sha": evidence.base_sha,
        "task_contract_sha256": evidence.task_contract_sha256,
        "patch_sha256": evidence.patch_sha256,
    }
    assert records[0]["payload"] == {"source": "unit"}
    assert records[0]["writer"]["validation"] == "GOLD_EVIDENCE_VALIDATED"


def test_unknown_gold_status_is_default_deny_success_like(tmp_path: Path) -> None:
    writer = GoldAttemptWriter(tmp_path, repo_root=tmp_path)

    result = writer.write_attempt(
        attempt_id="a4",
        run_id="r4",
        status="GOLD_NEW_FUTURE_SUCCESS",
        gold_evidence=None,
    )

    records = read_jsonl(tmp_path / "gold_attempts.jsonl")
    assert result.ok is False
    assert result.status == GOLD_EVIDENCE_REJECTED
    assert result.failure_code == GOLD_EVIDENCE_MISSING
    assert len(records) == 1
    assert records[0]["status"] == GOLD_EVIDENCE_REJECTED
    assert records[0]["requested_status"] == "GOLD_NEW_FUTURE_SUCCESS"
    assert records[0]["gold_evidence"] is None
    assert not any(record["status"] == "GOLD_NEW_FUTURE_SUCCESS" for record in records)


def test_non_success_status_without_evidence_can_be_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(writer_module, "validate_gold_evidence", lambda *args, **kwargs: calls.append(args))
    writer = GoldAttemptWriter(tmp_path, repo_root=tmp_path)

    result = writer.write_attempt(
        attempt_id="a5",
        run_id="r5",
        status="GOLD_ORACLE_UNRESOLVED",
        gold_evidence=None,
    )

    records = read_jsonl(tmp_path / "gold_attempts.jsonl")
    assert result.ok is True
    assert result.status == GOLD_ATTEMPT_WRITTEN
    assert calls == []
    assert len(records) == 1
    assert records[0]["arm"] == "GOLD"
    assert records[0]["status"] == "GOLD_ORACLE_UNRESOLVED"
    assert records[0]["requested_status"] == "GOLD_ORACLE_UNRESOLVED"
    assert records[0]["gold_evidence"] is None
    assert records[0]["writer"]["validation"] == "GOLD_EVIDENCE_NOT_REQUIRED"


def test_non_success_status_with_invalid_evidence_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(writer_module, "validate_gold_evidence", lambda *args, **kwargs: invalid_result())
    writer = GoldAttemptWriter(tmp_path, repo_root=tmp_path)

    result = writer.write_attempt(
        attempt_id="a6",
        run_id="r6",
        status="GOLD_ORACLE_UNRESOLVED",
        gold_evidence=sample_evidence(),
    )

    records = read_jsonl(tmp_path / "gold_attempts.jsonl")
    assert result.ok is False
    assert result.status == GOLD_EVIDENCE_REJECTED
    assert result.failure_code == GOLD_EVIDENCE_REPORT_UNREADABLE
    assert len(records) == 1
    assert records[0]["status"] == GOLD_EVIDENCE_REJECTED
    assert records[0]["requested_status"] == "GOLD_ORACLE_UNRESOLVED"
    assert records[0]["gold_evidence"] is None
    assert records[0]["failure_code"] == GOLD_EVIDENCE_REPORT_UNREADABLE
    assert records[0]["detail"] == "report_path unreadable"


def test_writer_has_no_validation_bypass_flag() -> None:
    forbidden = ("disable", "skip", "bypass", "unsafe")
    for target in (GoldAttemptWriter.__init__, GoldAttemptWriter.write_attempt):
        names = inspect.signature(target).parameters
        assert not [
            name for name in names if any(marker in name for marker in forbidden)
        ]


def test_writer_validator_call_modes_use_explicit_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_validate(evidence: GoldEvidence, **kwargs):
        calls.append({"evidence": evidence, **kwargs})
        if len(calls) == 2:
            return invalid_result()
        return valid_result()

    monkeypatch.setattr(writer_module, "validate_gold_evidence", fake_validate)
    report_root = tmp_path / "reports"
    writer = GoldAttemptWriter(tmp_path / "run", repo_root=tmp_path, report_root=report_root)
    evidence = sample_evidence()

    success = writer.write_attempt(
        attempt_id="a8-1",
        run_id="r8",
        status="GOLD_APPLIED_WITH_EVIDENCE",
        gold_evidence=evidence,
    )
    non_success = writer.write_attempt(
        attempt_id="a8-2",
        run_id="r8",
        status="GOLD_ORACLE_UNRESOLVED",
        gold_evidence=None,
    )
    non_success_invalid = writer.write_attempt(
        attempt_id="a8-3",
        run_id="r8",
        status="GOLD_ORACLE_UNRESOLVED",
        gold_evidence=evidence,
    )
    missing = writer.write_attempt(
        attempt_id="a8-4",
        run_id="r8",
        status="GOLD_APPLIED_WITH_EVIDENCE",
        gold_evidence=None,
    )
    full = writer.write_attempt(
        attempt_id="a8-5",
        run_id="r8",
        status="GOLD_FULL_VERIFIED",
        gold_evidence=evidence,
    )

    records = read_jsonl(tmp_path / "run" / "gold_attempts.jsonl")
    assert success.ok is True
    assert non_success.ok is True
    assert non_success_invalid.ok is False
    assert missing.ok is False
    assert full.ok is False
    assert full.failure_code == GOLD_ORACLE_BINDING_REQUIRED
    assert len(calls) == 2
    assert calls[0]["evidence"] == evidence
    assert calls[0]["repo_root"] == tmp_path
    assert calls[0]["report_root"] == report_root
    assert calls[1]["evidence"] == evidence
    assert calls[1]["repo_root"] == tmp_path
    assert calls[1]["report_root"] == report_root
    assert not any(record["status"] == "GOLD_FULL_VERIFIED" for record in records)


def test_write_failure_reports_typed_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = Path.open

    def failing_open(self: Path, *args, **kwargs):
        if self.name == "gold_attempts.jsonl":
            raise OSError("forced write failure")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)
    writer = GoldAttemptWriter(tmp_path, repo_root=tmp_path)

    result = writer.write_attempt(
        attempt_id="a9",
        run_id="r9",
        status="GOLD_ORACLE_UNRESOLVED",
        gold_evidence=None,
    )

    assert result.ok is False
    assert result.status == GOLD_ATTEMPT_WRITE_FAILED
    assert result.failure_code == GOLD_ATTEMPT_JSONL_WRITE_FAILED
    assert result.detail


def test_writer_uses_real_validator_with_real_controlled_change_evidence(
    tmp_path: Path,
    real_gold_evidence: RealGoldEvidenceFixture,
) -> None:
    writer = GoldAttemptWriter(tmp_path, repo_root=real_gold_evidence.repo_root)

    result = writer.write_attempt(
        attempt_id="a-real",
        run_id="r-real",
        status="GOLD_APPLIED_WITH_EVIDENCE",
        gold_evidence=real_gold_evidence.evidence,
        payload={"source": "real-controlled-change-fixture"},
    )

    records = read_jsonl(tmp_path / "gold_attempts.jsonl")
    assert result.ok is True
    assert result.status == GOLD_ATTEMPT_WRITTEN
    assert len(records) == 1
    assert records[0]["arm"] == "GOLD"
    assert records[0]["status"] == "GOLD_APPLIED_WITH_EVIDENCE"
    assert records[0]["requested_status"] == "GOLD_APPLIED_WITH_EVIDENCE"
    assert records[0]["gold_evidence"] == {
        "evidence_ref": real_gold_evidence.evidence.evidence_ref,
        "verified_commit": real_gold_evidence.evidence.verified_commit,
        "report_path": real_gold_evidence.evidence.report_path,
        "report_sha256": real_gold_evidence.evidence.report_sha256,
        "base_sha": real_gold_evidence.evidence.base_sha,
        "task_contract_sha256": real_gold_evidence.evidence.task_contract_sha256,
        "patch_sha256": real_gold_evidence.evidence.patch_sha256,
    }
    assert records[0]["writer"]["validation"] == "GOLD_EVIDENCE_VALIDATED"


def test_gold_full_verified_is_reserved_until_oracle_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(writer_module, "validate_gold_evidence", lambda *args, **kwargs: valid_result())
    writer = GoldAttemptWriter(tmp_path, repo_root=tmp_path)

    result = writer.write_attempt(
        attempt_id="a-full",
        run_id="r-full",
        status="GOLD_FULL_VERIFIED",
        gold_evidence=sample_evidence(),
    )

    records = read_jsonl(tmp_path / "gold_attempts.jsonl")
    assert result.ok is False
    assert result.status == GOLD_EVIDENCE_REJECTED
    assert result.failure_code == GOLD_ORACLE_BINDING_REQUIRED
    assert "oracle binding" in result.detail
    assert len(records) == 1
    assert records[0]["status"] == GOLD_EVIDENCE_REJECTED
    assert records[0]["requested_status"] == "GOLD_FULL_VERIFIED"
    assert records[0]["gold_evidence"] is None
    assert not any(record["status"] == "GOLD_FULL_VERIFIED" for record in records)


def test_non_gold_status_is_rejected_without_record(tmp_path: Path) -> None:
    writer = GoldAttemptWriter(tmp_path, repo_root=tmp_path)

    result = writer.write_attempt(
        attempt_id="a-bad",
        run_id="r-bad",
        status="BASELINE",
        gold_evidence=None,
    )

    assert result.ok is False
    assert result.status == GOLD_ATTEMPT_WRITE_FAILED
    assert result.failure_code == GOLD_ATTEMPT_STATUS_INVALID
    assert "BASELINE" in result.detail
    assert not (tmp_path / "gold_attempts.jsonl").exists()
