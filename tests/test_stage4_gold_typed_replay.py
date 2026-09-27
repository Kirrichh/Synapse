"""Governed typed CVM execution; no recorder worker or prescribed verdict."""

import pytest

from synapse.experiments.gold import behavior as B
from synapse.experiments.gold import replay as R
from synapse.experiments.gold import replay_composition as RC
from synapse.experiments.gold import replay_vm_codec as Codec
from synapse.experiments.gold.replay_vm_adapter import read_replayed_return_value
from tests.gold_typed_replay_support import numeric_procedure, explicit_subject
from tests.stage4_gold_replay_support import prepare_for


@pytest.mark.parametrize("left,right", [(11, 4), (-7, 5), (0, 0)])
def test_typed_inputs_execute_a_branch_and_survive_durable_replay(left, right):
    unit = numeric_procedure(left=left, right=right, required=left == 0)
    prepared = prepare_for(unit, inputs={"left": left, "right": right} if left == 0 else None)
    result = prepared.run()
    assert result.status is R.ReplayStatus.REPLAY_IDENTICAL
    observation, = result.observations
    assert read_replayed_return_value(observation,
        prepared.bundle.replay_store.open_snapshot(observation.terminal_snapshot_ref)) == abs(left - right)
    request = prepared.bundle.replay_store.request_record(result.request_ref)["payload"]
    binding, = request["bindings"]
    assert binding["schema_version"] == "synapse.stage4.gold.replay-program-binding/v2"
    assert binding["invocation"]["bound_values"] == {"left": left, "right": right}
    assert not result.recorded_activity_refs and not result.consumed_activity_identities
    manifest = prepared.bundle.replay_store.require_manifest(result_request_manifest(request))
    _, state, halted, sequence, _ = Codec.decode_adapter_snapshot(
        prepared.bundle.replay_store.open_snapshot(manifest.initial_snapshot_refs[0]))
    assert state.locals == {"left": left, "right": right}
    assert not halted and sequence == 0


def result_request_manifest(request):
    from synapse.experiments.gold.canonicalization import HashBoundRef
    return HashBoundRef.from_dict(request["execution_manifest_ref"])


def test_a_matching_transcript_does_not_certify_a_wrong_output_type():
    prepared = prepare_for(numeric_procedure(output_type=B.ValueType.BOOLEAN))
    with pytest.raises(R.ReplayViolation):
        prepared.run()
    captures = prepared.bundle.replay_store.recorded_capture_refs()
    assert captures
    capture = prepared.bundle.replay_store.require_capture(captures[-1])
    assert capture.contract_failure_reason is R.ReplayFailureReason.OUTPUT_CONTRACT_VIOLATION
    assert not capture.contract_matched
    assert not prepared.bundle.replay_store.recorded_manifest_refs()


def test_a_fresh_manifest_cannot_be_relabelled_with_other_inputs():
    prepared = prepare_for(numeric_procedure())
    manifest_ref = prepared.manifest_ref(prepared.bundle.replay_store)
    previous_results = prepared.bundle.replay_store.recorded_result_refs()
    prepared.subjects = (explicit_subject(prepared, left=90, right=4),)
    with pytest.raises(R.ReplayViolation) as error:
        RC.run_governed_replay(admission=prepared.admission, subjects=prepared.subjects,
            compiler=prepared.compiler, manifest_ref=manifest_ref,
            **prepared._governed(), **prepared._run_arguments())
    assert error.value.failure_code is R.ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH
    assert prepared.bundle.replay_store.recorded_result_refs() == previous_results


def test_continuation_uses_recorded_inputs_and_refuses_rebinding():
    prepared = prepare_for(numeric_procedure(), gas_budget=2)
    first = prepared.run()
    assert first.failure_reason is R.ReplayFailureReason.GAS_EXHAUSTED
    prepared = prepare_for(prepared.units[0], gas_budget=100)
    continued = prepared.resume(resumed_from=first)
    assert continued.status is R.ReplayStatus.REPLAY_IDENTICAL
    observation, = continued.observations
    assert read_replayed_return_value(observation,
        prepared.bundle.replay_store.open_snapshot(observation.terminal_snapshot_ref)) == 7
    prepared = prepare_for(prepared.units[0], gas_budget=100)
    prepared.subjects = (explicit_subject(prepared, left=12, right=4),)
    with pytest.raises(R.ReplayViolation) as error:
        prepared.resume(resumed_from=continued)
    assert error.value.failure_code is R.ReplayFailureCode.RESUME_LINEAGE_MISMATCH
