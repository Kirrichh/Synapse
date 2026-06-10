"""Patch 2b gate: REF_CAS_AND_EXISTING_LINKED_WORKTREE_SAFETY.

Guarantee boundary (stated precisely): the policy protects against target
refs checked out in worktrees that EXIST at discovery time, and discovery
itself is fail-closed. A TOCTOU window between discovery and update-ref
remains for a worktree created concurrently by another process; closing it
requires repository-level synchronization and is OUT OF SCOPE for Patch 2b.

Race-oriented tests for controlled-change ref application hardening:

    1.  evidence ref creation must use zero-OID CAS;
    2.  an existing evidence ref must never be silently overwritten;
    3.  a target ref checked out in the MAIN worktree is rejected before CAS;
    4.  a target ref checked out in ANY LINKED worktree is rejected before CAS;
    5.  detached / branchless worktree records are skipped safely;
    6.  every `git worktree list --porcelain` record is inspected, not just HEAD;
    7.  a missing target ref is created only through ZERO_OID CAS;
    8.  an existing target ref is updated only on exact base SHA match;
    9.  a concurrent ref move between read and update-ref yields
        APPLICATION_STALE_BASE and never clobbers the concurrent value;
    10. on policy refusal application_attempted is False, the target ref and
        remote refs are untouched;
    11. after a stale-base refusal the verified commit and evidence ref
        remain available for audit;
    12. no scenario executes push / merge / rebase / fetch or any remote
        ref mutation.

Scope deliberately excludes: runtime execution path, main.py, run/repl CLI,
provider framework, report schema redesign. The checked-out-target refusal
keeps the existing INTERNAL_ERROR + UNSAFE_TARGET_REF_CURRENTLY_CHECKED_OUT
classification (re-classification belongs to Patch 2c).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import synapse.change.application as application_module
import synapse.change.runner as runner_module
from synapse.change import ControlledChangeRequest
from synapse.change.application import (
    WorktreeDiscoveryError,
    _parse_worktree_porcelain_z,
    apply_verified_commit,
    checked_out_branch_refs,
)
from synapse.change.runner import (
    EvidenceRefError,
    _create_evidence_ref,
    execute_controlled_change,
)
from synapse.change.workspace import ZERO_OID

TARGET_REF = "refs/heads/controlled/verified"
UNSAFE_DIAG = "UNSAFE_TARGET_REF_CURRENTLY_CHECKED_OUT"
FORBIDDEN_GIT_VERBS = {"push", "merge", "rebase", "fetch", "pull", "cherry-pick"}


# --------------------------------------------------------------------------
# scaffold helpers (conventions follow tests/test_controlled_change_hardening)
# --------------------------------------------------------------------------

def run(cmd, cwd: Path, **kwargs):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, **kwargs)


def git(repo: Path, *args: str) -> str:
    completed = run(["git", *args], repo, check=True)
    return completed.stdout.strip()


def task_dict() -> dict:
    return {
        "schema": "synapse.controlled-change.task/v1",
        "task_id": "ref-safety-change",
        "task_class": "TEST",
        "base_revision": "HEAD",
        "target_ref": TARGET_REF,
        "allowed_scope": ["app.txt"],
        "patch_path": "patches/change.patch",
        "required_scaffold_paths": ["tasks/task.json", "patches/change.patch"],
        "reproduction": {
            "command": [sys.executable, "-c", "raise SystemExit(0)"],
            "committed_inputs": [],
            "before": {"expected_exit_codes": [0], "timeout_seconds": 10},
            "after": {"expected_exit_codes": [0], "timeout_seconds": 10},
        },
        "baseline_commands": [],
        "acceptance_commands": [[sys.executable, "-c", "raise SystemExit(0)"]],
        "full_suite_commands": [[sys.executable, "-c", "raise SystemExit(0)"]],
        "commit_message": "Apply controlled change",
    }


def init_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-b", "work")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "seed")
    (repo / "app.txt").write_bytes(b"old\n")
    (repo / "tasks").mkdir()
    (repo / "patches").mkdir()
    (repo / "patches" / "change.patch").write_text(
        "diff --git a/app.txt b/app.txt\n"
        "index 3367afd..3e75765 100644\n"
        "--- a/app.txt\n"
        "+++ b/app.txt\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n",
        encoding="utf-8",
    )
    (repo / "tasks" / "task.json").write_text(
        json.dumps(task_dict(), sort_keys=True), encoding="utf-8"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    return repo, git(repo, "rev-parse", "HEAD")


def make_commit(repo: Path, name: str) -> str:
    """Create a distinct commit object (on the current branch) and return its sha."""
    (repo / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
    git(repo, "add", f"{name}.txt")
    git(repo, "commit", "-m", name)
    return git(repo, "rev-parse", "HEAD")


def ref_sha(repo: Path, ref: str) -> str | None:
    completed = run(["git", "rev-parse", "--verify", ref], repo)
    return completed.stdout.strip() if completed.returncode == 0 else None


def add_fake_remote(repo: Path, tmp_path: Path) -> tuple[Path, str]:
    """Configure a bare 'origin' and return (bare_path, its current sha of work)."""
    bare = tmp_path / "origin.git"
    run(["git", "init", "--bare", str(bare)], tmp_path, check=True)
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-q", "origin", "work")  # the only push, in scaffold only
    return bare, git(bare, "rev-parse", "refs/heads/work")


class GitRecorder:
    """Wraps a module-level git() function, recording every argv."""

    def __init__(self, real):
        self.real = real
        self.calls: list[list[str]] = []

    def __call__(self, args, cwd, **kwargs):
        self.calls.append(list(args))
        return self.real(args, cwd, **kwargs)

    def used_verbs(self) -> set[str]:
        return {argv[0] for argv in self.calls if argv}


@pytest.fixture()
def recorded_application_git(monkeypatch):
    recorder = GitRecorder(application_module.git)
    monkeypatch.setattr(application_module, "git", recorder)
    return recorder


# --------------------------------------------------------------------------
# 1-2. evidence ref: zero-OID CAS, no silent overwrite
# --------------------------------------------------------------------------

def test_evidence_ref_created_via_zero_oid_cas(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path)
    verified = make_commit(repo, "verified")

    recorder = GitRecorder(runner_module.git)
    monkeypatch.setattr(runner_module, "git", recorder)

    ref = _create_evidence_ref(repo, "run-cas-proof", verified)

    update_calls = [argv for argv in recorder.calls if argv and argv[0] == "update-ref"]
    assert update_calls, "evidence creation must go through git update-ref"
    assert update_calls[-1] == ["update-ref", ref, verified, ZERO_OID], (
        "evidence ref must be created with explicit expected-old ZERO_OID "
        f"(zero-OID CAS); got argv: {update_calls[-1]}"
    )
    assert ref_sha(repo, ref) == verified


def test_existing_evidence_ref_is_not_overwritten(tmp_path):
    repo, base = init_repo(tmp_path)
    original = make_commit(repo, "original-evidence")
    intruder = make_commit(repo, "intruder")

    # Pre-existing evidence ref for the same (sanitized) run id.
    ref = "refs/synapse/change/evidence/collision-run"
    git(repo, "update-ref", ref, original)

    with pytest.raises(EvidenceRefError) as info:
        _create_evidence_ref(repo, "collision-run", intruder)

    assert "EVIDENCE_REF_CAS_FAILED" in str(info.value)
    assert ref_sha(repo, ref) == original, (
        "existing evidence ref was silently overwritten; "
        "creation must be a zero-OID CAS refusal"
    )


def test_evidence_collision_refuses_run_and_leaves_target_untouched(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path)
    blocker = make_commit(repo, "blocker")
    # restore HEAD to base so the run sees the contracted base revision
    git(repo, "reset", "--hard", base)

    fixed_run_id = "fixed-run-id-for-collision"
    monkeypatch.setattr(runner_module, "new_run_id", lambda: fixed_run_id)
    git(repo, "update-ref", f"refs/synapse/change/evidence/{fixed_run_id}", blocker)

    monkeypatch.chdir(repo)
    result = execute_controlled_change(
        ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST")
    )

    assert result.outcome == "INTERNAL_ERROR"
    assert ref_sha(repo, f"refs/synapse/change/evidence/{fixed_run_id}") == blocker, (
        "pre-existing evidence ref must stay unchanged after the refusal"
    )
    assert ref_sha(repo, TARGET_REF) is None, (
        "application must not be attempted after an evidence CAS refusal"
    )
    if result.application is not None:
        assert result.application.application_attempted is False


# --------------------------------------------------------------------------
# 3-6. checked-out target detection across ALL worktrees
# --------------------------------------------------------------------------

def test_target_checked_out_in_main_worktree_is_rejected(tmp_path, recorded_application_git):
    repo, base = init_repo(tmp_path)
    verified = make_commit(repo, "verified")
    git(repo, "reset", "--hard", base)
    git(repo, "switch", "-c", "controlled/verified")  # branch born at base, not verified

    result = apply_verified_commit(repo, TARGET_REF, base, verified)

    assert result.status == "INTERNAL_ERROR"
    assert UNSAFE_DIAG in result.diagnostics
    assert result.application_attempted is False
    assert ref_sha(repo, TARGET_REF) == base, "checked-out target must not be moved"
    assert not (recorded_application_git.used_verbs() & FORBIDDEN_GIT_VERBS)


def test_target_checked_out_in_linked_worktree_is_rejected(tmp_path, recorded_application_git):
    repo, base = init_repo(tmp_path)
    verified = make_commit(repo, "verified")
    linked = tmp_path / "linked-wt"
    # Target branch exists ONLY in a linked worktree; main worktree stays on 'work'.
    git(repo, "worktree", "add", "-b", "controlled/verified", str(linked), base)
    assert git(repo, "symbolic-ref", "-q", "HEAD") != TARGET_REF  # main HEAD is elsewhere

    result = apply_verified_commit(repo, TARGET_REF, base, verified)

    assert result.status == "INTERNAL_ERROR", (
        "a target ref checked out in a LINKED worktree must be rejected; "
        f"got status={result.status} diagnostics={result.diagnostics}"
    )
    assert UNSAFE_DIAG in result.diagnostics
    assert result.application_attempted is False
    assert ref_sha(repo, TARGET_REF) == base, "linked worktree branch must not be moved"
    assert not (recorded_application_git.used_verbs() & FORBIDDEN_GIT_VERBS)


def test_detached_worktree_records_are_skipped_safely(tmp_path):
    repo, base = init_repo(tmp_path)
    verified = make_commit(repo, "verified")
    git(repo, "reset", "--hard", base)
    detached = tmp_path / "detached-wt"
    git(repo, "worktree", "add", "--detach", str(detached), base)

    # Target ref is not checked out anywhere: application must proceed.
    result = apply_verified_commit(repo, TARGET_REF, base, verified)

    assert result.status == "APPLIED", (
        "a detached worktree record (no 'branch' line in porcelain output) "
        f"must be skipped, not break the policy; got {result.status}: {result.diagnostics}"
    )
    assert ref_sha(repo, TARGET_REF) == verified


def test_every_porcelain_record_is_checked_not_only_head(tmp_path, recorded_application_git):
    repo, base = init_repo(tmp_path)
    verified = make_commit(repo, "verified")
    git(repo, "reset", "--hard", base)

    # several worktrees; the TARGET branch lives in the LAST one
    git(repo, "worktree", "add", "-b", "side/one", str(tmp_path / "wt1"), base)
    git(repo, "worktree", "add", "--detach", str(tmp_path / "wt2"), base)
    git(repo, "worktree", "add", "-b", "controlled/verified", str(tmp_path / "wt3"), base)

    result = apply_verified_commit(repo, TARGET_REF, base, verified)

    assert result.status == "INTERNAL_ERROR"
    assert UNSAFE_DIAG in result.diagnostics
    assert result.application_attempted is False
    assert ref_sha(repo, TARGET_REF) == base


# --------------------------------------------------------------------------
# 7-9. target-ref CAS semantics (confirmation of existing behaviour)
# --------------------------------------------------------------------------

def test_missing_target_ref_is_created_only_via_zero_oid_cas(tmp_path, recorded_application_git):
    repo, base = init_repo(tmp_path)
    verified = make_commit(repo, "verified")
    git(repo, "reset", "--hard", base)
    assert ref_sha(repo, TARGET_REF) is None

    result = apply_verified_commit(repo, TARGET_REF, base, verified)

    assert result.status == "APPLIED"
    assert result.expected_old_sha == ZERO_OID
    assert ref_sha(repo, TARGET_REF) == verified
    cas_calls = [
        argv for argv in recorded_application_git.calls
        if argv[:2] == ["update-ref", TARGET_REF]
    ]
    assert cas_calls and cas_calls[-1] == ["update-ref", TARGET_REF, verified, ZERO_OID]


def test_existing_target_ref_requires_exact_base_sha(tmp_path):
    repo, base = init_repo(tmp_path)
    verified = make_commit(repo, "verified")
    other = make_commit(repo, "other")
    git(repo, "reset", "--hard", base)
    git(repo, "update-ref", TARGET_REF, other)

    result = apply_verified_commit(repo, TARGET_REF, base, verified)

    assert result.status == "APPLICATION_STALE_BASE"
    assert result.application_attempted is False
    assert result.actual_old_sha == other
    assert ref_sha(repo, TARGET_REF) == other, "stale target must not be overwritten"


def test_concurrent_ref_move_between_read_and_cas_is_stale_not_clobbered(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path)
    verified = make_commit(repo, "verified")
    attacker = make_commit(repo, "attacker")
    git(repo, "reset", "--hard", base)
    git(repo, "update-ref", TARGET_REF, base)

    real_git = application_module.git

    def racing_git(args, cwd, **kwargs):
        if args[:2] == ["update-ref", TARGET_REF] and len(args) == 4:
            # the race: another actor advances the ref AFTER the read,
            # IMMEDIATELY BEFORE our compare-and-swap
            real_git(["update-ref", TARGET_REF, attacker], cwd, check=True)
        return real_git(args, cwd, **kwargs)

    monkeypatch.setattr(application_module, "git", racing_git)
    result = apply_verified_commit(repo, TARGET_REF, base, verified)

    assert result.status == "APPLICATION_STALE_BASE"
    assert ref_sha(repo, TARGET_REF) == attacker, (
        "CAS must lose to the concurrent writer without clobbering its value"
    )


# --------------------------------------------------------------------------
# 10-12. refusal invariants, audit survival, no remote operations
# --------------------------------------------------------------------------

def test_policy_refusal_changes_nothing_local_or_remote(tmp_path, recorded_application_git):
    repo, base = init_repo(tmp_path)
    bare, remote_before = add_fake_remote(repo, tmp_path)
    verified = make_commit(repo, "verified")
    git(repo, "reset", "--hard", base)
    git(repo, "switch", "-c", "controlled/verified")  # main worktree on target, at base

    result = apply_verified_commit(repo, TARGET_REF, base, verified)

    assert result.status == "INTERNAL_ERROR"
    assert result.application_attempted is False
    assert result.remote_updated is False
    assert ref_sha(repo, TARGET_REF) == base
    assert git(bare, "rev-parse", "refs/heads/work") == remote_before, (
        "remote refs must never change during a policy refusal"
    )
    assert not (recorded_application_git.used_verbs() & FORBIDDEN_GIT_VERBS)


def test_stale_base_preserves_verified_commit_and_evidence_for_audit(tmp_path):
    repo, base = init_repo(tmp_path)
    verified = make_commit(repo, "verified")
    other = make_commit(repo, "other")
    git(repo, "reset", "--hard", base)

    evidence_ref = _create_evidence_ref(repo, "audit-run", verified)
    git(repo, "update-ref", TARGET_REF, other)  # target went stale meanwhile

    result = apply_verified_commit(
        repo, TARGET_REF, base, verified, evidence_ref=evidence_ref
    )

    assert result.status == "APPLICATION_STALE_BASE"
    assert ref_sha(repo, TARGET_REF) == other
    assert ref_sha(repo, evidence_ref) == verified, "evidence ref must survive the refusal"
    cat = run(["git", "cat-file", "-e", verified], repo)
    assert cat.returncode == 0, "verified commit must remain reachable for audit"


def test_repeated_application_against_original_base_is_stale_and_remote_free(tmp_path, recorded_application_git):
    """Strict CAS semantics: re-applying an ALREADY APPLIED result against the
    ORIGINAL base is stale. This is sequential re-application, NOT a
    concurrency scenario (concurrent races are covered separately by
    test_concurrent_ref_move_between_read_and_cas_is_stale_not_clobbered).
    Doubles as the aggregated no-remote-verbs guard."""
    repo, base = init_repo(tmp_path)
    verified = make_commit(repo, "verified")
    git(repo, "reset", "--hard", base)

    applied = apply_verified_commit(repo, TARGET_REF, base, verified)
    assert applied.status == "APPLIED"
    # target now points at `verified`; the contract still expects `base`
    stale = apply_verified_commit(repo, TARGET_REF, base, verified)
    assert stale.status == "APPLICATION_STALE_BASE"

    assert not (recorded_application_git.used_verbs() & FORBIDDEN_GIT_VERBS)
    assert all(argv[0] != "push" for argv in recorded_application_git.calls)


# --------------------------------------------------------------------------
# fail-closed worktree discovery and pathname-safe parsing (review fixes)
# --------------------------------------------------------------------------

def test_worktree_discovery_failure_is_fail_closed(tmp_path, monkeypatch, recorded_application_git):
    """git worktree list failure must FORBID application, never allow it."""
    repo, base = init_repo(tmp_path)
    verified = make_commit(repo, "verified")
    git(repo, "reset", "--hard", base)
    git(repo, "update-ref", TARGET_REF, base)

    def broken_git_binary(args, cwd, **kwargs):
        assert args[:3] == ["worktree", "list", "--porcelain"]
        return subprocess.CompletedProcess(
            args=["git", *args], returncode=128,
            stdout=b"", stderr=b"fatal: unable to access worktree metadata",
        )

    monkeypatch.setattr(application_module, "git_binary", broken_git_binary)
    result = apply_verified_commit(repo, TARGET_REF, base, verified)

    assert result.status == "INTERNAL_ERROR"
    assert "WORKTREE_DISCOVERY_FAILED" in result.diagnostics
    assert result.application_attempted is False
    assert result.actual_old_sha is None
    assert ref_sha(repo, TARGET_REF) == base, "target ref must survive discovery failure"
    target_updates = [
        argv for argv in recorded_application_git.calls
        if argv[:2] == ["update-ref", TARGET_REF]
    ]
    assert target_updates == [], (
        "target update-ref must NOT be invoked when worktree state is unknown"
    )


def test_porcelain_z_parser_is_pathname_safe():
    """Synthetic NUL-delimited output: newline, spaces, Unicode in pathnames."""
    evil_path = "/tmp/wt with spaces/строка\nс переводом"
    data = (
        b"worktree /repo/main\x00"
        b"HEAD 1111111111111111111111111111111111111111\x00"
        b"branch refs/heads/work\x00"
        b"\x00"
        + ("worktree " + evil_path).encode("utf-8") + b"\x00"
        b"HEAD 2222222222222222222222222222222222222222\x00"
        b"branch refs/heads/controlled/verified\x00"
        b"\x00"
        b"worktree /tmp/detached-one\x00"
        b"HEAD 3333333333333333333333333333333333333333\x00"
        b"detached\x00"
        b"\x00"
        b"worktree /tmp/locked-bare\x00"
        b"bare\x00"
        b"locked reason with spaces\x00"
        b"\x00"
    )
    refs = _parse_worktree_porcelain_z(data)
    assert refs == {
        "refs/heads/work": "/repo/main",
        "refs/heads/controlled/verified": evil_path,
    }, "pathname framing must survive newline/space/Unicode; detached/bare skipped"


def test_linked_worktree_with_space_and_unicode_pathname_is_detected(tmp_path):
    repo, base = init_repo(tmp_path)
    verified = make_commit(repo, "verified")
    git(repo, "reset", "--hard", base)
    weird = tmp_path / "wt с пробелами и юникодом"
    git(repo, "worktree", "add", "-b", "controlled/verified", str(weird), base)

    refs = checked_out_branch_refs(repo)
    assert refs.get(TARGET_REF) == str(weird)

    result = apply_verified_commit(repo, TARGET_REF, base, verified)
    assert result.status == "INTERNAL_ERROR"
    assert UNSAFE_DIAG in result.diagnostics
    assert result.actual_old_sha is None
    assert ref_sha(repo, TARGET_REF) == base


def test_checked_out_refusal_actual_old_sha_is_none(tmp_path):
    """actual_old_sha contract: CAS never started => no observed old value."""
    repo, base = init_repo(tmp_path)
    verified = make_commit(repo, "verified")
    git(repo, "reset", "--hard", base)
    git(repo, "switch", "-c", "controlled/verified")

    result = apply_verified_commit(repo, TARGET_REF, base, verified)

    assert result.status == "INTERNAL_ERROR"
    assert UNSAFE_DIAG in result.diagnostics
    assert result.actual_old_sha is None, (
        "refusal must not smuggle a ref NAME into a field documented as a SHA"
    )


# --------------------------------------------------------------------------
# parser framing robustness (review round 2)
# --------------------------------------------------------------------------

DOUBLE_NUL_FRAMING = (
    b"worktree /repo/main\x00HEAD 1111111111111111111111111111111111111111\x00"
    b"branch refs/heads/work\x00\x00"
    b"worktree /repo/wt1\x00HEAD 2222222222222222222222222222222222222222\x00"
    b"branch refs/heads/side\x00\x00"
    b"worktree /repo/wt2\x00HEAD 3333333333333333333333333333333333333333\x00"
    b"detached\x00\x00"
)

# hypothetical legal framing: record boundary defined ONLY by the next
# `worktree` field, no empty tokens, no trailing NUL after the last field
SINGLE_NUL_FRAMING = (
    b"worktree /repo/main\x00HEAD 1111111111111111111111111111111111111111\x00"
    b"branch refs/heads/work\x00"
    b"worktree /repo/wt1\x00HEAD 2222222222222222222222222222222222222222\x00"
    b"branch refs/heads/side\x00"
    b"worktree /repo/wt2\x00HEAD 3333333333333333333333333333333333333333\x00"
    b"detached"
)

EXPECTED_REFS = {
    "refs/heads/work": "/repo/main",
    "refs/heads/side": "/repo/wt1",
}


def test_parser_accepts_double_nul_record_framing():
    assert _parse_worktree_porcelain_z(DOUBLE_NUL_FRAMING) == EXPECTED_REFS


def test_parser_accepts_worktree_field_as_record_boundary():
    """No empty tokens at all: a new `worktree` field must flush the previous
    record, and the final record must be flushed after data ends."""
    assert _parse_worktree_porcelain_z(SINGLE_NUL_FRAMING) == EXPECTED_REFS, (
        "parser must not depend on double-NUL separators being present"
    )


def test_parser_against_real_git_raw_bytes(tmp_path):
    """Integration: feed the parser REAL bytes from git, captured directly
    via subprocess — independently of the production discovery code path —
    so the test cannot inherit a wrong framing assumption."""
    repo, base = init_repo(tmp_path)
    git(repo, "worktree", "add", "-b", "side/one", str(tmp_path / "wt1"), base)
    git(repo, "worktree", "add", "--detach", str(tmp_path / "wt2"), base)
    git(repo, "worktree", "add", "-b", "controlled/verified", str(tmp_path / "wt3"), base)

    raw = subprocess.run(
        ["git", "worktree", "list", "--porcelain", "-z"],
        cwd=repo, capture_output=True, check=True,
    ).stdout
    assert isinstance(raw, bytes) and b"\x00" in raw

    refs = _parse_worktree_porcelain_z(raw)
    assert refs.get("refs/heads/work") == str(repo)
    assert refs.get("refs/heads/side/one") == str(tmp_path / "wt1")
    assert refs.get(TARGET_REF) == str(tmp_path / "wt3")
    assert len(refs) == 3, f"detached worktree must not appear: {refs}"


def test_duplicate_branch_ref_fails_closed_in_parser():
    corrupted = (
        b"worktree /repo/a\x00branch refs/heads/controlled/verified\x00\x00"
        b"worktree /repo/b\x00branch refs/heads/controlled/verified\x00\x00"
    )
    with pytest.raises(WorktreeDiscoveryError) as info:
        _parse_worktree_porcelain_z(corrupted)
    assert "WORKTREE_DISCOVERY_AMBIGUOUS_BRANCH" in str(info.value)


def test_ambiguous_branch_state_refuses_application(tmp_path, monkeypatch, recorded_application_git):
    repo, base = init_repo(tmp_path)
    verified = make_commit(repo, "verified")
    git(repo, "reset", "--hard", base)
    git(repo, "update-ref", TARGET_REF, base)

    corrupted = (
        b"worktree /repo/a\x00branch " + TARGET_REF.encode() + b"\x00\x00"
        b"worktree /repo/b\x00branch " + TARGET_REF.encode() + b"\x00\x00"
    )

    def corrupted_git_binary(args, cwd, **kwargs):
        return subprocess.CompletedProcess(
            args=["git", *args], returncode=0, stdout=corrupted, stderr=b"",
        )

    monkeypatch.setattr(application_module, "git_binary", corrupted_git_binary)
    result = apply_verified_commit(repo, TARGET_REF, base, verified)

    assert result.status == "INTERNAL_ERROR"
    assert "WORKTREE_DISCOVERY_FAILED" in result.diagnostics
    assert any("WORKTREE_DISCOVERY_AMBIGUOUS_BRANCH" in d for d in result.diagnostics)
    assert result.application_attempted is False
    assert ref_sha(repo, TARGET_REF) == base
    assert not any(
        argv[:2] == ["update-ref", TARGET_REF]
        for argv in recorded_application_git.calls
    )
