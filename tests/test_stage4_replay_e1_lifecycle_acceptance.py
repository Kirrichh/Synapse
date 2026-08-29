"""Acceptance of the E1 replay-record schema and durable-attempt boundary."""

from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path

import pytest

from synapse.experiments.gold import replay as R
from synapse.experiments.gold import replay_composition as RC
from synapse.experiments.gold import activities as ACT
from synapse.experiments.gold.replay_store import (
    FileReplayStore,
    ReplayStoreFailureCode,
    ReplayStoreViolation,
)
from synapse.experiments.gold.replay_attempt_lifecycle import (
    ReplayAttemptFailureDomain,
    ReplayAttemptPhase,
    ReplayAttemptState,
    ReplayIncompleteAttempt,
)
from synapse.experiments.gold.persistence import store_transaction
from tests.gold_store_fence import fence_for
from tests.test_stage4_gold_replay import MACHINE_CONTEXT, MACHINE_FACTORY, pure_prepared


def test_legacy_or_incomplete_v1_records_fail_typed_without_conversion() -> None:
    prepared = pure_prepared()
    store = prepared.bundle.replay_store
    manifest = store.require_manifest(prepared.manifest_ref(store))
    capture = store.require_capture(manifest.source_capture_ref)
    cases = (
        (
            manifest.to_dict(), R.replay_manifest_from_dict,
            "expected_structural_history_refs",
            "synapse.stage4.gold.replay-execution-manifest/v1",
        ),
        (
            capture.to_dict(), R.reference_capture_from_dict,
            "observed_structural_history_refs",
            "synapse.stage4.gold.reference-replay-capture/v1",
        ),
    )

    for record, decoder, e1_field, legacy_schema in cases:
        incomplete = copy.deepcopy(record)
        incomplete["payload"].pop(e1_field)
        with pytest.raises(R.ReplayViolation) as excinfo:
            decoder(incomplete)
        assert excinfo.value.failure_code is R.ReplayFailureCode.TYPE_MISMATCH

        legacy = copy.deepcopy(record)
        legacy["payload"]["schema_version"] = legacy_schema
        with pytest.raises(R.ReplayViolation) as excinfo:
            decoder(legacy)
        assert excinfo.value.failure_code is R.ReplayFailureCode.UNKNOWN_SCHEMA_VERSION


def test_corrupt_structural_history_refuses_before_durable_attempt() -> None:
    prepared = pure_prepared()
    store = prepared.bundle.replay_store
    manifest_ref = prepared.manifest_ref(store)
    governed = prepared._governed()
    manifest = store.require_manifest(manifest_ref)
    history_ref = manifest.expected_structural_history_refs[0]
    history_path = store._structural_history_path(history_ref.sha256)
    original = history_path.read_bytes()
    requests_before = store.recorded_request_refs()
    results_before = store.recorded_result_refs()

    history_path.write_bytes(b"x" * len(original))
    try:
        with pytest.raises(ReplayStoreViolation) as excinfo:
            RC.run_governed_replay(
                admission=prepared.admission,
                subjects=prepared.subjects,
                compiler=prepared.compiler,
                manifest_ref=manifest_ref,
                **governed,
                **prepared._run_arguments(),
            )
    finally:
        history_path.write_bytes(original)

    assert excinfo.value.failure_code is ReplayStoreFailureCode.STRUCTURAL_HISTORY_CORRUPTED
    assert store.recorded_request_refs() == requests_before
    assert store.recorded_result_refs() == results_before


def test_legacy_snapshot_identities_fail_typed_without_aliases() -> None:
    prepared = pure_prepared()
    store = prepared.bundle.replay_store
    manifest = store.require_manifest(prepared.manifest_ref(store))
    reference = manifest.initial_snapshot_refs[0]
    raw = store.open_snapshot(reference)

    legacy_reference = dataclasses.replace(
        reference, schema_id="synapse.stage4.gold.replay-vm-snapshot/v1"
    )
    with pytest.raises(ReplayStoreViolation) as excinfo:
        store.open_snapshot(legacy_reference)
    assert excinfo.value.failure_code is ReplayStoreFailureCode.TYPE_MISMATCH

    legacy_inner = json.loads(raw)
    legacy_inner["schema_version"] = (
        "synapse.stage4.gold.replay-vm-adapter-snapshot/v1"
    )
    with pytest.raises(R.ReplayViolation) as excinfo:
        MACHINE_FACTORY.restore(
            json.dumps(
                legacy_inner, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8"),
            gas_budget=10_000,
            execution_context=MACHINE_CONTEXT,
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.UNKNOWN_SCHEMA_VERSION

    legacy_adapter = json.loads(raw)
    legacy_adapter["adapter_id"] = (
        "synapse.stage4.gold.cognitive-vm-replay-adapter/v1"
    )
    with pytest.raises(R.ReplayViolation) as excinfo:
        MACHINE_FACTORY.restore(
            json.dumps(
                legacy_adapter, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8"),
            gas_budget=10_000,
            execution_context=MACHINE_CONTEXT,
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.IDENTITY_MISMATCH

    assert not hasattr(R, "REPLAY_MACHINE_ADAPTER_ID_V1")
    assert not hasattr(ACT, "ACTIVITY_RESULT_CODEC_V1")


def test_direct_adapter_restore_refuses_oversize_snapshot_before_decode() -> None:
    with pytest.raises(R.ReplayViolation) as excinfo:
        MACHINE_FACTORY.restore(
            b" " * (R.MAX_SNAPSHOT_BYTES_V1_E1 + 1),
            gas_budget=10_000,
            execution_context=MACHINE_CONTEXT,
        )

    assert excinfo.value.failure_code is R.ReplayFailureCode.RESOURCE_LIMIT_EXCEEDED


@pytest.mark.parametrize(
    ("failure_point", "expected_phase", "expected_domain"),
    (
        (
            "_require_durable_policy_decisions",
            ReplayAttemptPhase.DURABLE_POLICY_REREAD,
            ReplayAttemptFailureDomain.POLICY_AUTHORITY,
        ),
        (
            "_execution_identity_from_durable_lineage",
            ReplayAttemptPhase.RECEIPT_ISSUE,
            ReplayAttemptFailureDomain.POLICY_AUTHORITY,
        ),
        (
            "_issue_execution_receipt",
            ReplayAttemptPhase.RECEIPT_ISSUE,
            ReplayAttemptFailureDomain.POLICY_AUTHORITY,
        ),
    ),
)
def test_post_request_refusal_is_durable_incomplete_without_a_false_verdict(
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
    expected_phase: ReplayAttemptPhase,
    expected_domain: ReplayAttemptFailureDomain,
) -> None:
    prepared = pure_prepared()
    store = prepared.bundle.replay_store
    requests_before = len(store.recorded_request_refs())
    results_before = len(store.recorded_result_refs())

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise R._fail(
            R.ReplayFailureCode.TRUSTED_OBJECT_FORGED,
            "acceptance: post-request execution prerequisite refused",
        )

    monkeypatch.setattr(R, failure_point, refuse)
    with pytest.raises(R.ReplayViolation) as excinfo:
        prepared.run()

    assert excinfo.value.failure_code is R.ReplayFailureCode.TRUSTED_OBJECT_FORGED
    assert len(store.recorded_request_refs()) == requests_before + 1
    assert len(store.recorded_result_refs()) == results_before
    request_ref = store.recorded_request_refs()[-1]
    assert store.result_ref_for_request(request_ref) is None
    incomplete = store.recoverable_attempts()[-1]
    assert incomplete.request_ref == request_ref
    assert incomplete.phase is expected_phase
    assert incomplete.failure_domain is expected_domain


def test_spent_receipt_cannot_enter_after_its_coordinator_guard_is_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = pure_prepared()
    captured: dict[str, object] = {}
    real_transitions = R._execute_replay_transitions

    def abort_before_entry(request: object, **kwargs: object) -> None:
        captured["request"] = request
        captured["kwargs"] = kwargs
        raise R._fail(
            R.ReplayFailureCode.TRUSTED_OBJECT_FORGED,
            "acceptance: abort after durable spend before transition entry",
        )

    monkeypatch.setattr(R, "_execute_replay_transitions", abort_before_entry)
    with pytest.raises(R.ReplayViolation):
        prepared.run()
    monkeypatch.undo()

    kwargs = captured["kwargs"]
    assert type(kwargs) is dict
    machine = kwargs["machines"][0]
    permit = kwargs["permit"]
    before = machine.snapshot_bytes()
    assert permit._spent is True
    assert permit._guard.live is False

    with pytest.raises(R.ReplayViolation) as excinfo:
        real_transitions(captured["request"], **kwargs)
    assert excinfo.value.failure_code is R.ReplayFailureCode.TRUSTED_OBJECT_FORGED
    assert machine.snapshot_bytes() == before
    assert permit._entered is False


def test_incomplete_attempt_is_exactly_recoverable_after_restart_without_reexecution(
    tmp_path: Path,
) -> None:
    """A spent request without a result becomes explicit, not executable again."""

    request = pure_prepared().request()
    request_ref = R.replay_request_ref(request)
    execution_identity = "7" * 64
    root = tmp_path / "replay-store"
    fence = fence_for(tmp_path)
    store = FileReplayStore(root, mutation_fence=fence)
    with store_transaction(fence) as ticket:
        assert store.append_request(request, ticket=ticket) == request_ref
    assert store.unresolved_request_refs() == (request_ref,)

    with store_transaction(fence) as ticket:
        store.spend_execution(
            execution_identity, request_ref=request_ref, ticket=ticket
        )
    claims = store.recorded_execution_claims()
    assert len(claims) == 1
    assert claims[0].request_ref == request_ref
    assert claims[0].execution_identity == execution_identity
    assert store.unresolved_execution_claims() == claims

    claimed_after_restart = FileReplayStore(root, mutation_fence=fence)
    assert claimed_after_restart.unresolved_request_refs() == (request_ref,)
    assert claimed_after_restart.unresolved_execution_claims() == claims
    with store_transaction(fence) as ticket:
        with pytest.raises(ReplayStoreViolation) as excinfo:
            claimed_after_restart.spend_execution(
                execution_identity, request_ref=request_ref, ticket=ticket
            )
        assert excinfo.value.failure_code is ReplayStoreFailureCode.RECORD_DUPLICATE

    incomplete = ReplayIncompleteAttempt(
        request_ref=request_ref,
        execution_identity=execution_identity,
        # A hard restart proves the request and claim, but cannot invent which
        # in-process phase or backend exception happened after the last append.
        phase=None,
        failure_domain=None,
    )
    with store_transaction(fence) as ticket:
        incomplete_ref = store.append_incomplete_attempt(
            incomplete, ticket=ticket
        )
    sequence = store.current_sequence()

    reopened = FileReplayStore(root, mutation_fence=fence)
    assert reopened.require_incomplete_attempt(incomplete_ref) == incomplete
    assert reopened.recoverable_attempts() == (incomplete,)
    assert reopened.unresolved_request_refs() == ()
    assert reopened.unresolved_execution_claims() == ()
    assert reopened.result_ref_for_request(request_ref) is None
    assert incomplete.state is ReplayAttemptState.INCOMPLETE_RECOVERABLE

    # Recovery may safely materialise the same fact again, but it cannot spend
    # the execution identity again or move this request to a different failure.
    with store_transaction(fence) as ticket:
        assert (
            reopened.append_incomplete_attempt(incomplete, ticket=ticket)
            == incomplete_ref
        )
    assert reopened.current_sequence() == sequence
    conflicting = dataclasses.replace(
        incomplete,
        phase=ReplayAttemptPhase.RESULT_APPEND,
        failure_domain=ReplayAttemptFailureDomain.BACKEND,
    )
    with store_transaction(fence) as ticket:
        with pytest.raises(ReplayStoreViolation) as excinfo:
            reopened.append_incomplete_attempt(conflicting, ticket=ticket)
        assert excinfo.value.failure_code is ReplayStoreFailureCode.RECORD_CONFLICT


def test_append_result_is_the_only_completion_marker_for_an_incomplete_attempt(
    tmp_path: Path,
) -> None:
    request = pure_prepared().request()
    request_ref = R.replay_request_ref(request)
    root = tmp_path / "replay-store"
    fence = fence_for(tmp_path)
    store = FileReplayStore(root, mutation_fence=fence)
    with store_transaction(fence) as ticket:
        store.append_request(request, ticket=ticket)

    incomplete = ReplayIncompleteAttempt(
        request_ref=request_ref,
        execution_identity=None,
        phase=ReplayAttemptPhase.DURABLE_POLICY_REREAD,
        failure_domain=ReplayAttemptFailureDomain.POLICY_AUTHORITY,
    )
    with store_transaction(fence) as ticket:
        incomplete_ref = store.append_incomplete_attempt(
            incomplete, ticket=ticket
        )
    assert store.recoverable_attempts() == (incomplete,)

    terminal = R._seal_result(
        request=request,
        status=R.ReplayStatus.REPLAY_FAILED,
        failure_reason=R.ReplayFailureReason.GAS_EXHAUSTED,
        observations=(),
    )
    with store_transaction(fence) as ticket:
        result_ref = store.append_result(terminal, ticket=ticket)

    reopened = FileReplayStore(root, mutation_fence=fence)
    assert reopened.require_result(result_ref) == terminal
    assert reopened.result_ref_for_request(request_ref) == result_ref
    assert reopened.recoverable_attempts() == ()
    assert reopened.unresolved_request_refs() == ()
    # The lifecycle evidence remains auditable but did not itself complete the
    # request; only the result made recoverable_attempts empty.
    assert reopened.require_incomplete_attempt(incomplete_ref) == incomplete

    with store_transaction(fence) as ticket:
        with pytest.raises(ReplayStoreViolation) as excinfo:
            reopened.append_result(terminal, ticket=ticket)
        assert excinfo.value.failure_code is ReplayStoreFailureCode.RECORD_DUPLICATE
