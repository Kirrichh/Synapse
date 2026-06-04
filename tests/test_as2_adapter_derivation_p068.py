"""P0.6.8 AS2 AdapterDerivationRecord hashing / Merkle-transparent audit tests.

These tests verify the approved audit-trail behavior only. They do not change
projection semantics, do not touch legacy runtime, and do not introduce a bridge
or feature flag.
"""

import json
from pathlib import Path

from synapse import agent_snapshot_adapter as as2
from synapse.canonical_service import stable_canonical_hash


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "as2"
POSITIVE_FIXTURE = FIXTURE_DIR / "positive_minimal_valid_projection_inputs.json"
REQUIRED_INPUT_HASH_KEYS = {
    "identity_context_hash",
    "model_registry_snapshot_hash",
    "adapter_definition_source_hash",
    "memory_ref_source_hash",
    "capability_grant_source_hash",
}


def _load_positive():
    data = json.loads(POSITIVE_FIXTURE.read_text(encoding="utf-8"))
    return data, data["inputs"]


def _project_positive():
    data, inputs = _load_positive()
    return as2.project_validated_as2_inputs(
        adapter_identity_context=inputs["adapter_identity_context"],
        adapter_definition_source=inputs["adapter_definition_source"],
        static_model_registry=inputs["static_model_registry"],
        memory_ref_source=inputs["memory_ref_source"],
        capability_grant_source=inputs["capability_grant_source"],
        model_selection_source=inputs["model_selection_source"],
        expected_derivation_record=data["expected_derivation_record"],
    )


def test_derivation_record_contains_real_input_hashes_from_positive_fixture():
    data, _ = _load_positive()
    _, derivation = _project_positive()

    assert set(derivation.input_hashes) == REQUIRED_INPUT_HASH_KEYS
    assert derivation.input_hashes == data["expected_derivation_record"]["input_hashes"]



def test_derivation_record_hashes_match_stable_canonical_hash_of_each_input():
    _, inputs = _load_positive()
    _, derivation = _project_positive()

    assert derivation.input_hashes["identity_context_hash"] == stable_canonical_hash(
        inputs["adapter_identity_context"]
    )
    assert derivation.input_hashes["model_registry_snapshot_hash"] == stable_canonical_hash(
        inputs["static_model_registry"]
    )
    assert derivation.input_hashes["adapter_definition_source_hash"] == stable_canonical_hash(
        inputs["adapter_definition_source"]
    )
    assert derivation.input_hashes["memory_ref_source_hash"] == stable_canonical_hash(
        inputs["memory_ref_source"]
    )
    assert derivation.input_hashes["capability_grant_source_hash"] == stable_canonical_hash(
        inputs["capability_grant_source"]
    )



def test_derivation_record_is_deterministic_for_repeated_projection_calls():
    snapshot_a, derivation_a = _project_positive()
    snapshot_b, derivation_b = _project_positive()

    assert derivation_a == derivation_b
    assert derivation_a.input_hashes == derivation_b.input_hashes
    assert derivation_a.memory_space_policy == derivation_b.memory_space_policy
    assert snapshot_a.snapshot_hash() == snapshot_b.snapshot_hash()



def test_derivation_record_does_not_affect_snapshot_hash():
    _, inputs = _load_positive()

    snapshot_with_fixture_record, derivation_with_fixture_record = as2.project_validated_as2_inputs(
        adapter_identity_context=inputs["adapter_identity_context"],
        adapter_definition_source=inputs["adapter_definition_source"],
        static_model_registry=inputs["static_model_registry"],
        memory_ref_source=inputs["memory_ref_source"],
        capability_grant_source=inputs["capability_grant_source"],
        model_selection_source=inputs["model_selection_source"],
        expected_derivation_record={
            "schema_version": "alpha3g.adapter_derivation.v1",
            "profile": "stable-canonical.v1",
            "input_hashes": {key: "sha256:" + "0" * 64 for key in REQUIRED_INPUT_HASH_KEYS},
            "memory_space_policy": {
                "policy_version": "alpha3g.memory_space_policy.v1",
                "expected_memory_space_id": "sha256:" + "1" * 64,
            },
        },
    )
    snapshot_without_fixture_record, derivation_without_fixture_record = as2.project_validated_as2_inputs(
        adapter_identity_context=inputs["adapter_identity_context"],
        adapter_definition_source=inputs["adapter_definition_source"],
        static_model_registry=inputs["static_model_registry"],
        memory_ref_source=inputs["memory_ref_source"],
        capability_grant_source=inputs["capability_grant_source"],
        model_selection_source=inputs["model_selection_source"],
        expected_derivation_record=None,
    )

    assert snapshot_with_fixture_record.snapshot_hash() == snapshot_without_fixture_record.snapshot_hash()
    assert derivation_with_fixture_record == derivation_without_fixture_record



def test_projection_module_keeps_runtime_forbidden_surface_locked_after_p069():
    source = Path(as2.__file__).read_text(encoding="utf-8")
    assert not hasattr(as2, "to_agent_snapshot")
    # P0.6.9 intentionally authorizes AS2ViolationContext as failure-path attribution.
    assert not hasattr(as2, "FeatureFlag")
    assert "from synapse.builtins" not in source
    assert "from synapse.interpreter" not in source
    assert "from synapse.actor_runtime" not in source
    assert "from synapse.environment" not in source
