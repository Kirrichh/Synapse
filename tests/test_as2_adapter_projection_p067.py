"""P0.6.7 AS2 fixture-driven minimal standalone projection.

This test file authorizes exactly one new behavior: projecting already validated
explicit AS2 inputs into the existing standalone AgentSnapshot core. It does not
call ``to_agent_snapshot()``, does not import legacy runtime, does not perform
storage/CAS writes, does not compute real AdapterDerivationRecord hashes, and
does not modify AgentSnapshot schema.
"""

import json
from pathlib import Path

import pytest

from synapse import agent_snapshot_adapter as as2
from synapse.agent_snapshot import AgentSnapshot


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "as2"
POSITIVE_FIXTURE = FIXTURE_DIR / "positive_minimal_valid_projection_inputs.json"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _positive_inputs():
    data = _load(POSITIVE_FIXTURE)
    return data, data["inputs"]


def _project_positive():
    data, inputs = _positive_inputs()
    return as2.project_validated_as2_inputs(
        adapter_identity_context=inputs["adapter_identity_context"],
        adapter_definition_source=inputs["adapter_definition_source"],
        static_model_registry=inputs["static_model_registry"],
        memory_ref_source=inputs["memory_ref_source"],
        capability_grant_source=inputs["capability_grant_source"],
        model_selection_source=inputs["model_selection_source"],
        expected_derivation_record=data["expected_derivation_record"],
    )


def test_project_validated_as2_inputs_returns_agent_snapshot_and_derivation_record():
    snapshot, derivation = _project_positive()
    assert isinstance(snapshot, AgentSnapshot)
    assert isinstance(derivation, as2.AdapterDerivationRecordSkeleton)


def test_projection_matches_positive_fixture_selected_fields():
    data, _ = _positive_inputs()
    expected = data["expected_snapshot_skeleton"]
    snapshot, derivation = _project_positive()

    assert snapshot.agent_id == expected["agent_id"]
    assert snapshot.schema_version == expected["schema_version"]
    assert snapshot.profile == expected["profile"]
    assert snapshot.definition_ref.namespace == expected["definition_namespace"]
    assert snapshot.definition_ref.class_name == expected["definition_class_name"]
    assert snapshot.model_ref["provider_namespace"] == expected["model_provider_namespace"]
    assert snapshot.model_ref["model_id"] == expected["model_id"]
    assert len(snapshot.memory_refs) == expected["memory_refs_count"]
    assert len(snapshot.capability_grants) == expected["capability_grants_count"]
    assert [grant.tool_namespace for grant in snapshot.capability_grants] == expected["capability_tool_namespaces"]

    assert set(derivation.input_hashes) == set(data["expected_derivation_record"]["input_hashes"])
    assert derivation.memory_space_policy == data["expected_derivation_record"]["memory_space_policy"]


def test_projection_is_deterministic_for_repeated_positive_fixture_inputs():
    snapshot_a, derivation_a = _project_positive()
    snapshot_b, derivation_b = _project_positive()

    assert snapshot_a.snapshot_hash() == snapshot_b.snapshot_hash()
    assert snapshot_a.to_dict() == snapshot_b.to_dict()
    assert derivation_a == derivation_b


def test_r8_canonical_projection_preserves_tool_namespace_and_generates_scope_hash():
    snapshot, _ = _project_positive()
    [grant] = snapshot.capability_grants
    assert grant.tool_namespace == "fixture.echo"
    assert grant.scope_hash.startswith("sha256:")
    assert len(grant.scope_hash) == len("sha256:") + 64
    assert grant.policy_ref == "policy:fixture.echo.v1"


@pytest.mark.parametrize(
    "fixture_path",
    sorted(p for p in FIXTURE_DIR.glob("negative_*.json")),
    ids=lambda p: p.stem,
)
def test_negative_fixtures_remain_validation_failures_before_projection(fixture_path):
    data = _load(fixture_path)
    inputs = data["inputs"]
    expected_cls = as2.ERROR_NAME_TO_CLASS[data["expected_error"]]
    # Negative fixtures must still fail at the validation boundary; projection
    # is never entered for invalid inputs. Some negative cases (envelope,
    # inline memory, subagent, ambient authority) are intentionally represented
    # outside the AS2 projection signature.
    with pytest.raises(expected_cls):
        as2.validate_as2_inputs(
            adapter_identity_context=inputs.get("adapter_identity_context"),
            adapter_definition_source=inputs.get("adapter_definition_source"),
            static_model_registry=inputs.get("static_model_registry"),
            memory_ref_source=inputs.get("memory_ref_source"),
            capability_grant_source=inputs.get("capability_grant_source"),
            model_selection_source=inputs.get("model_selection_source"),
            candidate_output_envelope=data.get("candidate_output_envelope"),
            inline_memory_payload=inputs.get("inline_memory_payload"),
            subagent_runtime_graph=inputs.get("subagent_runtime_graph"),
            harness_metadata=data.get("harness_metadata"),
        )


def test_projection_module_keeps_legacy_bridge_locked():
    source = Path(as2.__file__).read_text(encoding="utf-8")
    assert not hasattr(as2, "to_agent_snapshot")
    forbidden_imports = (
        "from synapse.builtins",
        "import synapse.builtins",
        "from synapse.interpreter",
        "import synapse.interpreter",
        "from synapse.actor_runtime",
        "import synapse.actor_runtime",
        "from synapse.environment",
        "import synapse.environment",
    )
    for token in forbidden_imports:
        assert token not in source
    assert "FeatureFlag" not in source
