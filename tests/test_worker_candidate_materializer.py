from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from synapse.worker.candidate_materializer import (
    MaterializationStatus,
    materialize_worker_candidate,
)
from synapse.worker.contract import (
    ExternalCodingWorkerResult,
    ExternalWorkerStatus,
    ExternalWorkerTokenStatus,
    ExternalWorkerUsage,
    WorkerReport,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "mini_capture"


def run(cmd: list[str], cwd: Path, **kwargs):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, **kwargs)


def git(repo: Path, *args: str) -> str:
    completed = run(["git", *args], repo, check=True)
    return completed.stdout.strip()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")


def seed_commit(repo: Path) -> None:
    write(repo / "README.md", "candidate materializer test fixture\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "seed")


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", ".")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def build_capture_001_repo(repo: Path) -> str:
    init_repo(repo)
    seed_commit(repo)
    write(repo / "src" / "calc.py", "def add(a, b):\n    return a - b  # bug\n")
    write(
        repo / "tests_local" / "test_calc.py",
        "from src.calc import add\n"
        "\n"
        "\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
    )
    return commit_all(repo, "capture 001 base")


def build_capture_002_repo(repo: Path) -> str:
    init_repo(repo)
    seed_commit(repo)
    write(repo / "src" / "calc.py", "def add(a, b):\n    return a + b\n")
    write(
        repo / "tests_local" / "test_mul.py",
        "from src.mul import multiply\n"
        "\n"
        "\n"
        "def test_mul():\n"
        "    assert multiply(2, 3) == 6\n",
    )
    return commit_all(repo, "capture 002 base")


def fixture_json(capture_id: str, name: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / capture_id / name).read_text(encoding="utf-8"))


def fixture_patch(capture_id: str) -> str:
    return (FIXTURE_ROOT / capture_id / "candidate.patch").read_text(encoding="utf-8")


def worker_result_from_envelope(
    envelope: dict[str, object],
    *,
    diff_text: str | None = None,
) -> ExternalCodingWorkerResult:
    usage_data = envelope["usage"]
    assert isinstance(usage_data, dict)
    worker_report_data = envelope.get("worker_report") or {}
    assert isinstance(worker_report_data, dict)
    diagnostics = envelope.get("diagnostics") or {}
    assert isinstance(diagnostics, dict)
    return ExternalCodingWorkerResult(
        worker_status=ExternalWorkerStatus(envelope["worker_status"]),
        diff_text=diff_text,
        touched_files=tuple(envelope.get("touched_files", ())),
        usage=ExternalWorkerUsage(
            token_status=ExternalWorkerTokenStatus(usage_data["token_status"]),
            input_tokens=usage_data.get("input_tokens"),
            output_tokens=usage_data.get("output_tokens"),
            thinking_tokens=usage_data.get("thinking_tokens"),
            total_tokens=usage_data.get("total_tokens"),
            thinking_included=usage_data["thinking_included"],
            diagnostics=usage_data.get("diagnostics", {}),
        ),
        diagnostics=diagnostics,
        worker_report=WorkerReport(
            summary=worker_report_data.get("summary"),
            failure_reason=worker_report_data.get("failure_reason"),
        ),
    )


def simple_worker_result(
    *,
    status: ExternalWorkerStatus = ExternalWorkerStatus.PROPOSED_PATCH,
    diff_text: str | None = None,
    touched_files: tuple[str, ...] = (),
    diagnostics: dict[str, object] | None = None,
) -> ExternalCodingWorkerResult:
    return ExternalCodingWorkerResult(
        worker_status=status,
        diff_text=diff_text,
        touched_files=touched_files,
        usage=ExternalWorkerUsage(
            token_status=ExternalWorkerTokenStatus.UNAVAILABLE,
            input_tokens=None,
            output_tokens=None,
            thinking_tokens=None,
            total_tokens=None,
            thinking_included=False,
            diagnostics={},
        ),
        diagnostics=diagnostics or {},
    )


def assert_patch_applies_and_tests_pass(base_repo: Path, patch_text: str) -> None:
    fresh = base_repo
    build_capture_001_repo(fresh)
    applied = subprocess.run(
        ["git", "apply", "--recount", "-"],
        cwd=fresh,
        input=patch_text,
        text=True,
        capture_output=True,
    )
    assert applied.returncode == 0, applied.stderr
    tested = run(
        [sys.executable, "-B", "-m", "pytest", "-p", "no:cacheprovider", "tests_local/test_calc.py"],
        fresh,
    )
    assert tested.returncode == 0, tested.stdout + tested.stderr


def test_capture_001_modify_only_fixture_materializes_exact_worker_diff_text(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = build_capture_001_repo(repo)
    patch_text = fixture_patch("capture_001")
    envelope = fixture_json("capture_001", "envelope.json")
    worker_result = worker_result_from_envelope(envelope, diff_text=patch_text)

    candidate = materialize_worker_candidate(
        worktree_path=repo,
        worker_result=worker_result,
        allowed_scope=["src/"],
        base_commit=base,
    )

    assert candidate.status == MaterializationStatus.MATERIALIZED
    assert candidate.patch_text == patch_text
    assert candidate.touched_files == ("src/calc.py",)
    assert candidate.scope_violations == ()
    assert "worker_diff_text" in candidate.source_forms


def test_capture_001_rederived_worktree_patch_is_semantically_equivalent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = build_capture_001_repo(repo)
    patch_text = fixture_patch("capture_001")
    applied = subprocess.run(["git", "apply", "--recount", "-"], cwd=repo, input=patch_text, text=True, capture_output=True)
    assert applied.returncode == 0, applied.stderr
    worker_result = simple_worker_result(diff_text=None)

    candidate = materialize_worker_candidate(
        worktree_path=repo,
        worker_result=worker_result,
        allowed_scope=["src/"],
        base_commit=base,
    )

    assert candidate.status == MaterializationStatus.MATERIALIZED
    assert candidate.patch_text is not None
    assert "unstaged_diff" in candidate.source_forms
    assert candidate.scope_violations == ()
    assert_patch_applies_and_tests_pass(tmp_path / "fresh-base", candidate.patch_text)


def test_capture_002_fixture_rejects_out_of_scope_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = build_capture_002_repo(repo)
    write(repo / "src" / "mul.py", "def multiply(a, b):\n    return a * b\n")
    write(repo / "test_import.py", "from src.mul import multiply\nprint(multiply(2, 3))\n")
    envelope = fixture_json("capture_002", "envelope.json")
    worker_result = worker_result_from_envelope(envelope, diff_text=fixture_patch("capture_002"))

    candidate = materialize_worker_candidate(
        worktree_path=repo,
        worker_result=worker_result,
        allowed_scope=["src/"],
        base_commit=base,
    )

    assert candidate.status == MaterializationStatus.REJECTED_SCOPE_VIOLATION
    assert candidate.patch_text is None
    assert "test_import.py" in candidate.scope_violations
    assert set(candidate.touched_files) == {"src/mul.py", "test_import.py"}
    assert "untracked_files" in candidate.source_forms
    assert candidate.diagnostics["untracked_files"] == ["src/mul.py", "test_import.py"]


def test_scope_boundary_rejects_src2_evil_with_src_prefix_scope(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    seed_commit(repo)
    write(repo / "src" / "calc.py", "def add(a, b):\n    return a + b\n")
    base = commit_all(repo, "base")
    write(repo / "src2" / "evil.py", "print('no')\n")
    worker_result = simple_worker_result(
        touched_files=("src2/evil.py",),
        diagnostics={"untracked_files": ("src2/evil.py",)},
    )

    candidate = materialize_worker_candidate(
        worktree_path=repo,
        worker_result=worker_result,
        allowed_scope=["src"],
        base_commit=base,
    )

    assert candidate.status == MaterializationStatus.REJECTED_SCOPE_VIOLATION
    assert "src2/evil.py" in candidate.scope_violations
    assert candidate.patch_text is None


def test_empty_allowed_scope_rejects_at_api_boundary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    seed_commit(repo)
    worker_result = simple_worker_result(status=ExternalWorkerStatus.NO_PATCH)

    with pytest.raises(ValueError, match="allowed_scope must not be empty"):
        materialize_worker_candidate(
            worktree_path=repo,
            worker_result=worker_result,
            allowed_scope=[],
        )


def test_in_scope_untracked_file_materializes_as_new_file_patch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    seed_commit(repo)
    write(repo / "src" / "calc.py", "def add(a, b):\n    return a + b\n")
    base = commit_all(repo, "base")
    write(repo / "src" / "mul.py", "def multiply(a, b):\n    return a * b\n")
    worker_result = simple_worker_result(
        touched_files=("src/mul.py",),
        diagnostics={"untracked_files": ("src/mul.py",)},
    )

    candidate = materialize_worker_candidate(
        worktree_path=repo,
        worker_result=worker_result,
        allowed_scope=["src/"],
        base_commit=base,
    )

    assert candidate.status == MaterializationStatus.MATERIALIZED
    assert candidate.patch_text is not None
    assert "diff --git a/src/mul.py b/src/mul.py" in candidate.patch_text
    assert "new file mode" in candidate.patch_text
    assert "--- /dev/null" in candidate.patch_text
    assert "+++ b/src/mul.py" in candidate.patch_text
    assert candidate.scope_violations == ()
    assert "untracked_files" in candidate.source_forms


def test_staged_change_materializes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    seed_commit(repo)
    write(repo / "src" / "calc.py", "def add(a, b):\n    return a - b\n")
    base = commit_all(repo, "base")
    write(repo / "src" / "calc.py", "def add(a, b):\n    return a + b\n")
    git(repo, "add", "src/calc.py")
    assert git(repo, "diff") == ""

    candidate = materialize_worker_candidate(
        worktree_path=repo,
        worker_result=simple_worker_result(diff_text=None),
        allowed_scope=["src/"],
        base_commit=base,
    )

    assert candidate.status == MaterializationStatus.MATERIALIZED
    assert candidate.patch_text is not None
    assert "diff --git a/src/calc.py b/src/calc.py" in candidate.patch_text
    assert "staged_diff" in candidate.source_forms


def test_scratch_commit_beyond_base_materializes_patch_not_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    seed_commit(repo)
    write(repo / "src" / "calc.py", "def add(a, b):\n    return a - b\n")
    base = commit_all(repo, "base")
    write(repo / "src" / "calc.py", "def add(a, b):\n    return a + b\n")
    git(repo, "add", "src/calc.py")
    git(repo, "commit", "-m", "agent candidate")
    head = git(repo, "rev-parse", "HEAD")

    candidate = materialize_worker_candidate(
        worktree_path=repo,
        worker_result=simple_worker_result(diff_text=None),
        allowed_scope=["src/"],
        base_commit=base,
    )

    assert candidate.status == MaterializationStatus.MATERIALIZED
    assert candidate.patch_text is not None
    assert "diff --git a/src/calc.py b/src/calc.py" in candidate.patch_text
    assert candidate.diagnostics["candidate_commit_shas"] == [head]
    assert "scratch_commits" in candidate.source_forms


def test_combined_unstaged_diff_and_untracked_file_materializes_both(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    seed_commit(repo)
    write(repo / "src" / "calc.py", "def add(a, b):\n    return a - b\n")
    base = commit_all(repo, "base")
    write(repo / "src" / "calc.py", "def add(a, b):\n    return a + b\n")
    worker_diff = git(repo, "diff", "--binary")
    write(repo / "src" / "mul.py", "def multiply(a, b):\n    return a * b\n")
    worker_result = simple_worker_result(
        diff_text=worker_diff,
        touched_files=("src/calc.py",),
        diagnostics={"untracked_files": ("src/mul.py",)},
    )

    candidate = materialize_worker_candidate(
        worktree_path=repo,
        worker_result=worker_result,
        allowed_scope=["src/"],
        base_commit=base,
    )

    assert candidate.status == MaterializationStatus.MATERIALIZED
    assert candidate.patch_text is not None
    assert "diff --git a/src/calc.py b/src/calc.py" in candidate.patch_text
    assert "diff --git a/src/mul.py b/src/mul.py" in candidate.patch_text
    assert "untracked_files" in candidate.source_forms
    assert "worker_diff_text" in candidate.source_forms or "unstaged_diff" in candidate.source_forms
    assert candidate.scope_violations == ()


def test_no_candidate_for_clean_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    seed_commit(repo)
    base = git(repo, "rev-parse", "HEAD")

    candidate = materialize_worker_candidate(
        worktree_path=repo,
        worker_result=simple_worker_result(status=ExternalWorkerStatus.NO_PATCH),
        allowed_scope=["src/"],
        base_commit=base,
    )

    assert candidate.status == MaterializationStatus.NO_CANDIDATE
    assert candidate.patch_text is None
    assert candidate.touched_files == ()
