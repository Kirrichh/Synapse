"""P0.5.9 AgentSnapshot standalone hardening / edge-case coverage.

These tests extend the SA1 surface from P0.5.8 with edge cases discovered by
adversarial probing: external mutation of mapping fields shifting
``snapshot_hash()``, duplicate or conflicting ``memory_refs``, duplicate
``capability_grants``, whitespace-only ``memory_key``, empty-string vs ``None``
alias, hash determinism under dict-order permutation, and explicit rejection of
NaN / Infinity / non-string keys in canonical fields.

The patch remains standalone. No legacy integration, no FunctionDescriptor
runtime registry, no central schema registry. All checks operate on the
isolated value-core module ``synapse.agent_snapshot``.
"""

import pytest

from synapse.agent_snapshot import (
    AgentCapabilityGrantError,
    AgentDefinitionRef,
    AgentIdSeed,
    AgentMemoryRefError,
    AgentRuntimeEnvelopeViolation,
    AgentSnapshot,
    AgentSnapshotSchemaError,
    AgentSnapshotSerializationError,
    AgentSnapshotValidationError,
    CapabilityGrant,
    MemoryRef,
    UnknownSchemaVersionError,
    validate_agent_snapshot_payload,
)


HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64
HASH_D = "sha256:" + "d" * 64
HASH_E = "sha256:" + "e" * 64
HASH_F = "sha256:" + "f" * 64
HASH_0 = "sha256:" + "0" * 64
HASH_1 = "sha256:" + "1" * 64


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


def _agent_id() -> str:
    return AgentIdSeed(
        parent_anchor=HASH_A,
        definition_hash=HASH_D,
        spawn_nonce="spawn-0001",
        alias=None,
        namespace="agents.example",
    ).derive_agent_id()


# ---------------------------------------------------------------------------
# Mutation safety: snapshot_hash() must be stable under external mutation
# ---------------------------------------------------------------------------


def test_external_mutation_of_config_dict_does_not_shift_snapshot_hash():
    """SA1 regression: ``cfg["k"] = X`` after construction must not change hash."""

    cfg = {"key": "original"}
    snap = AgentSnapshot(agent_id=_agent_id(), definition_ref=_definition_ref(), config=cfg)
    hash_before = snap.snapshot_hash()

    cfg["key"] = "mutated"
    cfg["new_key"] = "added"

    hash_after = snap.snapshot_hash()
    assert hash_before == hash_after, "external mutation of config must not shift snapshot identity"


def test_external_mutation_of_nested_mapping_does_not_shift_snapshot_hash():
    """Defensive deep-freeze must cover nested mappings, not only top-level."""

    nested = {"inner": {"x": 1}}
    snap = AgentSnapshot(
        agent_id=_agent_id(), definition_ref=_definition_ref(), canonical_fields=nested
    )
    hash_before = snap.snapshot_hash()

    nested["inner"]["x"] = 999
    nested["inner"]["new"] = "leaked"

    hash_after = snap.snapshot_hash()
    assert hash_before == hash_after


def test_external_mutation_of_nested_list_does_not_shift_snapshot_hash():
    """Lists nested inside config must also be frozen (stored as tuples)."""

    cfg = {"items": [1, 2, 3]}
    snap = AgentSnapshot(agent_id=_agent_id(), definition_ref=_definition_ref(), config=cfg)
    hash_before = snap.snapshot_hash()

    cfg["items"].append(999)

    hash_after = snap.snapshot_hash()
    assert hash_before == hash_after


def test_config_attribute_is_read_only_after_construction():
    snap = AgentSnapshot(
        agent_id=_agent_id(), definition_ref=_definition_ref(), config={"k": "v"}
    )

    with pytest.raises(TypeError):
        snap.config["k"] = "tampered"  # type: ignore[index]


# ---------------------------------------------------------------------------
# Duplicate / conflicting memory_refs
# ---------------------------------------------------------------------------


def test_exact_duplicate_memory_ref_is_rejected():
    m = MemoryRef(memory_space_id=HASH_E, memory_key="/k", access_mode="read")
    with pytest.raises(AgentMemoryRefError):
        AgentSnapshot(
            agent_id=_agent_id(), definition_ref=_definition_ref(), memory_refs=(m, m)
        )


def test_conflicting_access_mode_for_same_memory_address_is_rejected():
    m_read = MemoryRef(memory_space_id=HASH_E, memory_key="/k", access_mode="read")
    m_write = MemoryRef(memory_space_id=HASH_E, memory_key="/k", access_mode="write")

    with pytest.raises(AgentMemoryRefError):
        AgentSnapshot(
            agent_id=_agent_id(),
            definition_ref=_definition_ref(),
            memory_refs=(m_read, m_write),
        )


def test_distinct_memory_addresses_are_allowed_even_with_overlapping_keys():
    """Different memory_space_id with the same key is a different address."""

    m1 = MemoryRef(memory_space_id=HASH_E, memory_key="/k", access_mode="read")
    m2 = MemoryRef(memory_space_id=HASH_0, memory_key="/k", access_mode="write")

    snap = AgentSnapshot(
        agent_id=_agent_id(), definition_ref=_definition_ref(), memory_refs=(m1, m2)
    )
    assert len(snap.memory_refs) == 2


def test_duplicate_memory_refs_rejected_in_round_trip_from_dict():
    """The validator path (used by ``from_dict``) must enforce the same rule."""

    m = MemoryRef(memory_space_id=HASH_E, memory_key="/k", access_mode="read").to_dict()
    snap_payload = AgentSnapshot(
        agent_id=_agent_id(), definition_ref=_definition_ref()
    ).to_dict()
    snap_payload["memory_refs"] = [m, m]

    with pytest.raises(AgentMemoryRefError):
        validate_agent_snapshot_payload(snap_payload)


# ---------------------------------------------------------------------------
# Duplicate capability_grants
# ---------------------------------------------------------------------------


def test_duplicate_capability_grant_for_same_tool_namespace_is_rejected():
    g1 = CapabilityGrant(tool_namespace="fs_read", scope_hash=HASH_F, policy_ref="p:1")
    g2 = CapabilityGrant(tool_namespace="fs_read", scope_hash=HASH_A, policy_ref="p:2")

    with pytest.raises(AgentCapabilityGrantError):
        AgentSnapshot(
            agent_id=_agent_id(),
            definition_ref=_definition_ref(),
            capability_grants=(g1, g2),
        )


def test_distinct_tool_namespaces_are_allowed():
    g1 = CapabilityGrant(tool_namespace="fs_read", scope_hash=HASH_F, policy_ref="p:1")
    g2 = CapabilityGrant(tool_namespace="net_post", scope_hash=HASH_A, policy_ref="p:2")

    snap = AgentSnapshot(
        agent_id=_agent_id(),
        definition_ref=_definition_ref(),
        capability_grants=(g1, g2),
    )
    assert len(snap.capability_grants) == 2


def test_duplicate_capability_grants_rejected_in_round_trip_from_dict():
    g = CapabilityGrant(tool_namespace="fs_read", scope_hash=HASH_F, policy_ref="p:1").to_dict()
    snap_payload = AgentSnapshot(
        agent_id=_agent_id(), definition_ref=_definition_ref()
    ).to_dict()
    snap_payload["capability_grants"] = [g, g]

    with pytest.raises(AgentCapabilityGrantError):
        validate_agent_snapshot_payload(snap_payload)


# ---------------------------------------------------------------------------
# MemoryRef key strictness
# ---------------------------------------------------------------------------


def test_memory_key_whitespace_only_is_rejected():
    with pytest.raises(AgentMemoryRefError):
        MemoryRef(memory_space_id=HASH_E, memory_key="   ", access_mode="read")


def test_memory_key_tab_only_is_rejected():
    with pytest.raises(AgentMemoryRefError):
        MemoryRef(memory_space_id=HASH_E, memory_key="\t\n", access_mode="read")


def test_memory_key_with_leading_whitespace_is_preserved_but_accepted():
    """We only reject whitespace-*only* keys. Leading/trailing whitespace on a
    real key is kept verbatim because the key may be opaque to standalone core.
    """

    ref = MemoryRef(memory_space_id=HASH_E, memory_key=" /k", access_mode="read")
    assert ref.memory_key == " /k"


# ---------------------------------------------------------------------------
# AgentIdSeed alias normalization
# ---------------------------------------------------------------------------


def test_alias_none_and_empty_string_produce_identical_agent_id():
    """Identity drift defense: '' and None mean the same thing semantically."""

    seed_none = AgentIdSeed(
        parent_anchor=HASH_A,
        definition_hash=HASH_D,
        spawn_nonce="n",
        alias=None,
        namespace="ns",
    )
    seed_empty = AgentIdSeed(
        parent_anchor=HASH_A,
        definition_hash=HASH_D,
        spawn_nonce="n",
        alias="",
        namespace="ns",
    )
    seed_ws = AgentIdSeed(
        parent_anchor=HASH_A,
        definition_hash=HASH_D,
        spawn_nonce="n",
        alias="   ",
        namespace="ns",
    )

    assert seed_none.derive_agent_id() == seed_empty.derive_agent_id()
    assert seed_none.derive_agent_id() == seed_ws.derive_agent_id()


def test_alias_meaningful_string_yields_distinct_agent_id():
    seed_none = AgentIdSeed(
        parent_anchor=HASH_A,
        definition_hash=HASH_D,
        spawn_nonce="n",
        alias=None,
        namespace="ns",
    )
    seed_named = AgentIdSeed(
        parent_anchor=HASH_A,
        definition_hash=HASH_D,
        spawn_nonce="n",
        alias="planner",
        namespace="ns",
    )

    assert seed_none.derive_agent_id() != seed_named.derive_agent_id()


def test_alias_non_string_is_rejected():
    with pytest.raises(AgentSnapshotValidationError):
        AgentIdSeed(
            parent_anchor=HASH_A,
            definition_hash=HASH_D,
            spawn_nonce="n",
            alias=123,  # type: ignore[arg-type]
            namespace="ns",
        )


# ---------------------------------------------------------------------------
# Canonical hash determinism guarantees
# ---------------------------------------------------------------------------


def test_snapshot_hash_is_independent_of_python_dict_insertion_order():
    """``stable-canonical.v1`` must sort keys; assert that here at SA layer."""

    s_ab = AgentSnapshot(
        agent_id=_agent_id(),
        definition_ref=_definition_ref(),
        config={"a": 1, "b": 2, "c": 3},
    )
    s_ba = AgentSnapshot(
        agent_id=_agent_id(),
        definition_ref=_definition_ref(),
        config={"c": 3, "b": 2, "a": 1},
    )

    assert s_ab.snapshot_hash() == s_ba.snapshot_hash()


def test_snapshot_hash_changes_on_meaningful_config_change():
    s_v1 = AgentSnapshot(
        agent_id=_agent_id(), definition_ref=_definition_ref(), config={"x": 1}
    )
    s_v2 = AgentSnapshot(
        agent_id=_agent_id(), definition_ref=_definition_ref(), config={"x": 2}
    )

    assert s_v1.snapshot_hash() != s_v2.snapshot_hash()


def test_to_dict_is_idempotent_and_pure():
    snap = AgentSnapshot(
        agent_id=_agent_id(), definition_ref=_definition_ref(), config={"k": "v"}
    )

    p1 = snap.to_dict()
    p2 = snap.to_dict()
    assert p1 == p2
    # mutating returned payload must not affect a later to_dict() call
    p1["config"]["k"] = "tampered"
    p3 = snap.to_dict()
    assert p3["config"]["k"] == "v"


# ---------------------------------------------------------------------------
# Non-finite floats, non-string keys, callables — fail-closed regression guards
# ---------------------------------------------------------------------------


def test_nan_in_config_is_rejected():
    with pytest.raises(AgentSnapshotSerializationError):
        AgentSnapshot(
            agent_id=_agent_id(),
            definition_ref=_definition_ref(),
            config={"x": float("nan")},
        )


def test_positive_infinity_is_rejected():
    with pytest.raises(AgentSnapshotSerializationError):
        AgentSnapshot(
            agent_id=_agent_id(),
            definition_ref=_definition_ref(),
            config={"x": float("inf")},
        )


def test_negative_infinity_is_rejected():
    with pytest.raises(AgentSnapshotSerializationError):
        AgentSnapshot(
            agent_id=_agent_id(),
            definition_ref=_definition_ref(),
            config={"x": float("-inf")},
        )


def test_non_string_dict_keys_are_rejected():
    with pytest.raises(AgentSnapshotSerializationError):
        AgentSnapshot(
            agent_id=_agent_id(),
            definition_ref=_definition_ref(),
            config={1: "value"},  # type: ignore[dict-item]
        )


# ---------------------------------------------------------------------------
# Hash-format strictness
# ---------------------------------------------------------------------------


def test_uppercase_sha256_hex_is_rejected():
    with pytest.raises(AgentSnapshotValidationError):
        AgentDefinitionRef(
            namespace="ns",
            class_name="C",
            declared_version="1.0.0",
            interface_schema_hash="sha256:" + "A" * 64,
            config_schema_hash=HASH_B,
            capability_schema_hash=HASH_C,
            manifest_hash=HASH_D,
        )


def test_wrong_length_sha256_hex_is_rejected():
    with pytest.raises(AgentSnapshotValidationError):
        AgentDefinitionRef(
            namespace="ns",
            class_name="C",
            declared_version="1.0.0",
            interface_schema_hash="sha256:" + "a" * 63,
            config_schema_hash=HASH_B,
            capability_schema_hash=HASH_C,
            manifest_hash=HASH_D,
        )


def test_missing_sha256_prefix_is_rejected():
    with pytest.raises(AgentSnapshotValidationError):
        AgentDefinitionRef(
            namespace="ns",
            class_name="C",
            declared_version="1.0.0",
            interface_schema_hash="a" * 64,
            config_schema_hash=HASH_B,
            capability_schema_hash=HASH_C,
            manifest_hash=HASH_D,
        )


# ---------------------------------------------------------------------------
# Runtime-envelope leakage at depth
# ---------------------------------------------------------------------------


def test_runtime_envelope_field_in_canonical_fields_is_rejected():
    with pytest.raises(AgentRuntimeEnvelopeViolation):
        AgentSnapshot(
            agent_id=_agent_id(),
            definition_ref=_definition_ref(),
            canonical_fields={"llm": "live-backend-placeholder"},
        )


def test_runtime_envelope_field_in_model_ref_is_rejected():
    with pytest.raises(AgentRuntimeEnvelopeViolation):
        AgentSnapshot(
            agent_id=_agent_id(),
            definition_ref=_definition_ref(),
            model_ref={"tools": "leaked"},
        )


# ---------------------------------------------------------------------------
# Schema/profile strictness on nested objects
# ---------------------------------------------------------------------------


def test_unknown_memory_ref_schema_is_rejected_at_construction():
    with pytest.raises(UnknownSchemaVersionError):
        MemoryRef(
            memory_space_id=HASH_E,
            memory_key="/k",
            access_mode="read",
            schema_version="alpha3g.memory_ref.v2",
        )


def test_unknown_capability_grant_schema_is_rejected_at_construction():
    with pytest.raises(UnknownSchemaVersionError):
        CapabilityGrant(
            tool_namespace="fs_read",
            scope_hash=HASH_F,
            policy_ref="p:1",
            schema_version="alpha3g.capability_grant.v2",
        )


def test_unknown_definition_ref_schema_is_rejected_at_construction():
    with pytest.raises(UnknownSchemaVersionError):
        AgentDefinitionRef(
            namespace="ns",
            class_name="C",
            declared_version="1.0.0",
            interface_schema_hash=HASH_A,
            config_schema_hash=HASH_B,
            capability_schema_hash=HASH_C,
            manifest_hash=HASH_D,
            schema_version="alpha3g.agent_definition_ref.v2",
        )


# ---------------------------------------------------------------------------
# Missing required fields in payload validator
# ---------------------------------------------------------------------------


def test_payload_missing_agent_id_is_rejected():
    snap = AgentSnapshot(agent_id=_agent_id(), definition_ref=_definition_ref())
    payload = snap.to_dict()
    del payload["agent_id"]

    with pytest.raises(AgentSnapshotSchemaError):
        validate_agent_snapshot_payload(payload)


def test_payload_with_non_mapping_input_is_rejected():
    with pytest.raises(AgentSnapshotSchemaError):
        validate_agent_snapshot_payload([1, 2, 3])  # type: ignore[arg-type]


def test_memory_refs_must_be_list_not_tuple_in_serialized_payload():
    """JSON has no tuple; the validator must reject non-list sequences."""

    snap_payload = AgentSnapshot(
        agent_id=_agent_id(), definition_ref=_definition_ref()
    ).to_dict()
    snap_payload["memory_refs"] = ()  # type: ignore[assignment]

    with pytest.raises(AgentMemoryRefError):
        validate_agent_snapshot_payload(snap_payload)
