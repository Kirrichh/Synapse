"""Acceptance of the E1 replay-record schema and durable-attempt boundary."""

from __future__ import annotations

import copy
import dataclasses
import json

import pytest

from synapse.experiments.gold import replay as R
from synapse.experiments.gold import activities as ACT
from synapse.experiments.gold.replay_store import (
    ReplayStoreFailureCode,
    ReplayStoreViolation,
)
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
            R.run_governed_replay(
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
    "failure_point",
    (
        "_require_durable_policy_decisions",
        "_execution_identity_from_durable_lineage",
        "_issue_execution_receipt",
    ),
)
def test_post_request_non_persistence_refusal_has_a_durable_outcome(
    monkeypatch: pytest.MonkeyPatch, failure_point: str,
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
    assert len(store.recorded_result_refs()) == results_before + 1
    recorded = store.require_result(store.recorded_result_refs()[-1])
    assert recorded.status is R.ReplayStatus.INFRA_ERROR
    assert recorded.failure_reason is R.ReplayFailureReason.MACHINE_FAULT
    assert recorded.request_ref == store.recorded_request_refs()[-1]


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
    before = (machine.instruction_pointer(), machine.is_halted())
    assert permit._spent is True
    assert permit._guard.live is False

    with pytest.raises(R.ReplayViolation) as excinfo:
        real_transitions(captured["request"], **kwargs)
    assert excinfo.value.failure_code is R.ReplayFailureCode.TRUSTED_OBJECT_FORGED
    assert (machine.instruction_pointer(), machine.is_halted()) == before
    assert permit._entered is False
