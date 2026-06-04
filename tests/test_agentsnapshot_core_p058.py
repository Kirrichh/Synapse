"""P0.5.8 standalone AgentSnapshot schema/value core tests."""

import pytest

from synapse.agent_snapshot import (
    AGENT_DEFINITION_REF_SCHEMA,
    AGENT_ID_SCHEMA,
    AGENT_SNAPSHOT_FIELDS,
    AGENT_SNAPSHOT_SCHEMA,
    CAPABILITY_GRANT_SCHEMA,
    LOCAL_SCHEMA_ALLOWLIST,
    MEMORY_REF_SCHEMA,
    STABLE_CANONICAL_PROFILE,
    AgentDefinitionRef,
    AgentIdSeed,
    AgentMemoryRefError,
    AgentRuntimeEnvelopeViolation,
    AgentSnapshot,
    AgentSnapshotSchemaError,
    AgentSnapshotSerializationError,
    CapabilityGrant,
    MemoryRef,
    UnknownSchemaVersionError,
    validate_agent_snapshot_payload,
)
from synapse.canonical_service import stable_canonical_hash

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64
HASH_D = "sha256:" + "d" * 64
HASH_E = "sha256:" + "e" * 64
HASH_F = "sha256:" + "f" * 64


def _definition_ref() -> AgentDefinitionRef:
    return AgentDefinitionRef(
        namespace="agents.example",
        class_name="PlannerAgent",
        declared_version="1.0.0",
        interface_schema_hash=HASH_A,
        config_schema_hash=HASH_B,
        capability_schema_hash=HASH_C,
        manifest_hash=HASH_D,
    )


def _memory_ref() -> MemoryRef:
    return MemoryRef(
        memory_space_id=HASH_E,
        memory_key="/memory/project%2Fnotes",
        access_mode="read",
    )


def _capability_grant() -> CapabilityGrant:
    return CapabilityGrant(
        tool_namespace="fs_read",
        scope_hash=HASH_F,
        policy_ref="policy:readonly-fixture",
    )


def _agent_id() -> str:
    seed = AgentIdSeed(
        parent_anchor=HASH_A,
        definition_hash=HASH_D,
        spawn_nonce="spawn-0001",
        alias="planner",
        namespace="agents.example",
    )
    return seed.derive_agent_id()


def _snapshot() -> AgentSnapshot:
    return AgentSnapshot(
        agent_id=_agent_id(),
        definition_ref=_definition_ref(),
        config={"temperature": 0.0, "labels": ["alpha", "beta"]},
        canonical_fields={"state": "idle"},
        memory_refs=(_memory_ref(),),
        model_ref={"provider": "mock", "model": "deterministic"},
        capability_grants=(_capability_grant(),),
    )


def test_local_schema_allowlist_matches_p057_gate():
    assert LOCAL_SCHEMA_ALLOWLIST == {
        AGENT_SNAPSHOT_SCHEMA,
        AGENT_DEFINITION_REF_SCHEMA,
        AGENT_ID_SCHEMA,
        MEMORY_REF_SCHEMA,
        "alpha3g.memory_space_id.v1",
        CAPABILITY_GRANT_SCHEMA,
        "alpha3g.function_descriptor.v1",
        STABLE_CANONICAL_PROFILE,
    }


def test_agent_id_seed_is_stable_and_excludes_causal_index():
    seed = AgentIdSeed(
        parent_anchor=HASH_A,
        definition_hash=HASH_D,
        spawn_nonce="spawn-0001",
        alias=None,
        namespace="agents.example",
    )

    payload = seed.to_dict()

    assert "causal_index" not in payload
    assert payload["schema_version"] == AGENT_ID_SCHEMA
    assert seed.derive_agent_id() == stable_canonical_hash(payload)


def test_agentsnapshot_to_dict_uses_only_allowlisted_snapshot_fields():
    payload = _snapshot().to_dict()

    assert set(payload) == AGENT_SNAPSHOT_FIELDS
    assert payload["type"] == "agent_snapshot"
    assert payload["schema_version"] == AGENT_SNAPSHOT_SCHEMA
    assert payload["profile"] == STABLE_CANONICAL_PROFILE
    assert payload["definition_ref"]["schema_version"] == AGENT_DEFINITION_REF_SCHEMA
    assert payload["memory_refs"][0]["schema_version"] == MEMORY_REF_SCHEMA
    assert payload["capability_grants"][0]["schema_version"] == CAPABILITY_GRANT_SCHEMA
    assert "memory" not in payload
    assert "tools" not in payload
    assert "env" not in payload


def test_agentsnapshot_hash_is_stable_canonical_hash_of_payload():
    snapshot = _snapshot()

    assert snapshot.snapshot_hash() == stable_canonical_hash(snapshot.to_dict())
    assert snapshot.snapshot_hash().startswith("sha256:")


def test_agentsnapshot_round_trip_from_dict_preserves_hash():
    snapshot = _snapshot()
    payload = snapshot.to_dict()

    restored = AgentSnapshot.from_dict(payload)

    assert restored.to_dict() == payload
    assert restored.snapshot_hash() == snapshot.snapshot_hash()


def test_unknown_snapshot_schema_fails_closed():
    payload = _snapshot().to_dict()
    payload["schema_version"] = "alpha3g.agent_snapshot.v2"

    with pytest.raises(UnknownSchemaVersionError):
        validate_agent_snapshot_payload(payload)


def test_unknown_profile_fails_closed():
    payload = _snapshot().to_dict()
    payload["profile"] = "alpha3g.local-json.v1"

    with pytest.raises(UnknownSchemaVersionError):
        validate_agent_snapshot_payload(payload)


def test_extra_or_runtime_envelope_fields_are_rejected():
    payload = _snapshot().to_dict()
    payload["tools"] = {"live": object()}

    with pytest.raises((AgentRuntimeEnvelopeViolation, AgentSnapshotSchemaError)):
        validate_agent_snapshot_payload(payload)


def test_runtime_envelope_fields_inside_config_are_rejected():
    with pytest.raises(AgentRuntimeEnvelopeViolation):
        AgentSnapshot(
            agent_id=_agent_id(),
            definition_ref=_definition_ref(),
            config={"env": "forbidden-runtime-envelope-field"},
        )


def test_non_canonical_field_value_rejected():
    with pytest.raises(AgentSnapshotSerializationError):
        AgentSnapshot(
            agent_id=_agent_id(),
            definition_ref=_definition_ref(),
            canonical_fields={"callback": lambda: None},
        )


def test_memory_ref_access_mode_is_fail_closed():
    with pytest.raises(AgentMemoryRefError):
        MemoryRef(
            memory_space_id=HASH_E,
            memory_key="/memory/project",
            access_mode="admin",
        )


def test_definition_ref_requires_sha256_hash_fields():
    with pytest.raises(Exception):
        AgentDefinitionRef(
            namespace="agents.example",
            class_name="PlannerAgent",
            declared_version="1.0.0",
            interface_schema_hash="not-a-hash",
            config_schema_hash=HASH_B,
            capability_schema_hash=HASH_C,
            manifest_hash=HASH_D,
        )
