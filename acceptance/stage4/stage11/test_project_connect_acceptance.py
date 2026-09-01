"""Acceptance for the connected-project phase reached from the canonical entrypoint.

Connecting a repository is the phase that gives the Gold world a production
origin: before it, the only way to obtain one was a fixture. What is accepted
here is the production behaviour an operator depends on -- that a project is
created once, reopened only against the heads it recorded, and honest about
whether Gold applies to it yet.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AuthorityIdentity,
    ContractViolation,
)
from synapse.experiments.gold.knowledge_environment import (
    ConnectProjectRequest,
    GoldProjectDeclaration,
    GoldProjectEntitlements,
    GoldProjectIdentities,
    ProjectStatusRequest,
    connect_gold_project,
    execute_connect_project,
    execute_project_status,
    open_gold_project,
)


def _identities() -> GoldProjectIdentities:
    return GoldProjectIdentities(
        platform_attester_actor=ActorIdentity("acceptance-attester"),
        builder_actor=ActorIdentity("acceptance-builder"),
        taint_classifier_authority=AuthorityIdentity("acceptance-taint-classifier"),
        taint_reviewer_authority=AuthorityIdentity("acceptance-taint-reviewer"),
        supersession_reviewer_authority=AuthorityIdentity("acceptance-supersession"),
        revocation_reviewer_authority=AuthorityIdentity("acceptance-revocation"),
        lifecycle_writer_actor=ActorIdentity("acceptance-lifecycle-writer"),
    )


def _repository(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    for args in (
        ("init", "-q", "."),
        ("config", "user.email", "acceptance@example.invalid"),
        ("config", "user.name", "Acceptance"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=repo, check=True, capture_output=True)
    return repo


def _declaration(repo: Path, state: Path, *, granted: bool) -> GoldProjectDeclaration:
    return GoldProjectDeclaration(
        repo_root=repo,
        state_root=state,
        policy_version="synapse.acceptance-policy/v1",
        environment_profile_id="acceptance-environment",
        identities=_identities(),
        entitlements=(
            GoldProjectEntitlements(
                scopes=("repo:acceptance",), capabilities=("read",), oracles=("swebench",)
            )
            if granted
            else None
        ),
    )


@pytest.fixture()
def connected(tmp_path: Path) -> tuple[Path, Path]:
    repo = _repository(tmp_path)
    state = tmp_path / "state"
    connect_gold_project(_declaration(repo, state, granted=True))
    return repo, state


def test_a_connected_project_reopens_against_its_recorded_heads(connected) -> None:
    _repo, state = connected
    stores = open_gold_project(state)
    assert stores.library.search_index() == ()
    assert stores.fence.coordinator_id()


def test_every_store_shares_one_mutation_coordinator(connected) -> None:
    """A per-store counter cannot report that one history moved during a read."""

    _repo, state = connected
    stores = open_gold_project(state)
    assert stores.library.mutation_fence is stores.fence
    assert stores.compatibility_history.mutation_fence is stores.fence
    assert stores.admission_journal.mutation_fence is stores.fence


def test_a_second_connect_on_one_state_root_is_refused(connected, tmp_path: Path) -> None:
    repo, state = connected
    result = execute_connect_project(
        ConnectProjectRequest(
            repo_root=repo,
            state_root=state,
            declaration_path=_write_declaration(tmp_path, granted=True),
        )
    )
    assert result.exit_code == 1
    assert result.record_path is None


def test_a_tampered_recorded_head_is_refused_on_reopen(connected) -> None:
    """The recorded head is a trust anchor, so a rewritten history cannot pass."""

    _repo, state = connected
    record = state / "project.json"
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["heads"]["lifecycle"]["ordered_log_root_sha256"] = "f" * 64
    record.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ContractViolation):
        open_gold_project(state)


def test_a_head_replayed_from_another_history_is_refused(connected) -> None:
    _repo, state = connected
    record = state / "project.json"
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["heads"]["taint"] = payload["heads"]["lifecycle"]
    record.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ContractViolation):
        open_gold_project(state)


def test_an_incomplete_record_names_the_field_it_is_missing(connected) -> None:
    _repo, state = connected
    record = state / "project.json"
    payload = json.loads(record.read_text(encoding="utf-8"))
    del payload["connected_revision"]
    record.write_text(json.dumps(payload), encoding="utf-8")
    result = execute_project_status(ProjectStatusRequest(state_root=state))
    assert result.exit_code == 1
    assert "connected_revision" in result.diagnostics[0]


def test_an_accumulated_project_is_what_gold_needs_and_an_empty_one_says_so(connected) -> None:
    """Two refusals that look alike from outside are kept apart."""

    _repo, state = connected
    result = execute_project_status(ProjectStatusRequest(state_root=state))
    assert result.exit_code == 0
    assert result.status.gold_available is False
    assert result.status.entitlements_declared is True
    assert result.status.library_candidates == 0
    assert "no accumulated knowledge" in result.status.gold_unavailable_reason


def test_a_project_connected_without_a_grant_reports_that_instead(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    state = tmp_path / "state"
    connect_gold_project(_declaration(repo, state, granted=False))
    result = execute_project_status(ProjectStatusRequest(state_root=state))
    assert result.status.entitlements_declared is False
    assert "entitlements" in result.status.gold_unavailable_reason


def test_status_reports_repository_drift_since_the_project_was_connected(connected) -> None:
    repo, state = connected
    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "second"], cwd=repo, check=True, capture_output=True)
    status = execute_project_status(ProjectStatusRequest(state_root=state)).status
    assert status.current_revision != status.connected_revision


def test_status_on_a_state_root_holding_no_project_is_refused(tmp_path: Path) -> None:
    result = execute_project_status(ProjectStatusRequest(state_root=tmp_path / "nothing"))
    assert result.exit_code == 1
    assert result.status is None


def _write_declaration(root: Path, *, granted: bool) -> Path:
    payload = {
        "policy_version": "synapse.acceptance-policy/v1",
        "environment_profile_id": "acceptance-environment",
        "identities": _identities().to_dict(),
        "entitlements": (
            {"scopes": ["repo:acceptance"], "capabilities": ["read"], "oracles": ["swebench"]}
            if granted
            else None
        ),
    }
    path = root / "declaration.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
