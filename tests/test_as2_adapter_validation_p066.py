"""P0.6.6 AS2 validation hardening / fixture-driven boundary enforcement.

This test file extends the P0.6.5 skeleton coverage with exhaustive edge-case
tests for ``validate_as2_inputs`` and its focused validators. It does not
construct an ``AgentSnapshot``, does not call any projection function, does
not import legacy runtime, and does not perform hash computation.

Scope discipline (Vote A consolidated by the team):
  - validation-only;
  - typed leaf exceptions for every negative path;
  - positive fixture passes silently;
  - no ``to_agent_snapshot`` reference;
  - no ``project_validated_as2_inputs`` reference (name reserved in RFC only);
  - no ``AgentSnapshot`` construction;
  - no hash computation;
  - no feature flag;
  - no legacy/ambient imports.

The 35+ cases below were derived from an adversarial probe of P0.6.5 skeleton
that identified seven concrete edge-case gaps; each gap is now exercised by an
explicit test and locked in for regression coverage.
"""

import copy
import json
from pathlib import Path

import pytest

from synapse import agent_snapshot_adapter as as2


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "as2"

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64


# ---------------------------------------------------------------------------
# Reusable builders. Each returns a fresh dict so tests can mutate freely.
# ---------------------------------------------------------------------------


def _identity_context():
    return {
        "schema_version": as2.ADAPTER_IDENTITY_CONTEXT_SCHEMA,
        "profile": as2.AS2_FIXTURE_PROFILE,
        "identity_seed": {
            "parent_anchor": "genesis:fixture",
            "definition_hash": HASH_A,
            "spawn_nonce": "nonce:0001",
            "namespace": "alpha3g.fixture",
            "alias": "fixture-agent",
        },
    }


def _model_registry():
    return {
        "schema_version": as2.STATIC_MODEL_REGISTRY_SCHEMA,
        "profile": as2.AS2_FIXTURE_PROFILE,
        "registry_snapshot_hash": HASH_A,
        "entries": [
            {
                "legacy_model": "mock-agent-model",
                "model_ref": {
                    "provider_namespace": "mock",
                    "model_id": "mock-id",
                    "model_version": "1.0.0",
                    "capability_profile_hash": HASH_B,
                },
            }
        ],
    }


def _memory_ref_source():
    return {
        "schema_version": as2.MEMORY_REF_SOURCE_SCHEMA,
        "profile": as2.AS2_FIXTURE_PROFILE,
        "expected_memory_space_id": HASH_A,
        "memory_space_policy_version": as2.MEMORY_SPACE_POLICY_V1,
        "refs": [],
    }


def _capability_grant_source():
    return {
        "schema_version": as2.CAPABILITY_GRANT_SOURCE_SCHEMA,
        "profile": as2.AS2_FIXTURE_PROFILE,
        "grants": [],
    }


def _model_selection_source():
    return {"model": "mock-agent-model"}


def _call(**overrides):
    inputs = {
        "adapter_identity_context": _identity_context(),
        "static_model_registry": _model_registry(),
        "memory_ref_source": _memory_ref_source(),
        "capability_grant_source": _capability_grant_source(),
        "model_selection_source": _model_selection_source(),
    }
    inputs.update(overrides)
    return as2.validate_as2_inputs(**inputs)


# ---------------------------------------------------------------------------
# Fixture-driven matrix: every JSON fixture must map to a real leaf exception
# class (negative) or pass silently (positive). Loads all 11 fixtures.
# ---------------------------------------------------------------------------


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _all_fixtures():
    return sorted(FIXTURE_DIR.glob("*.json"))


def _negative_fixtures():
    return [p for p in _all_fixtures() if _load(p)["polarity"] == "negative"]


def _positive_fixtures():
    return [p for p in _all_fixtures() if _load(p)["polarity"] == "positive"]


def _dispatch_fixture(data):
    inputs = data["inputs"]
    return as2.validate_as2_inputs(
        adapter_identity_context=inputs.get("adapter_identity_context"),
        static_model_registry=inputs.get("static_model_registry"),
        memory_ref_source=inputs.get("memory_ref_source"),
        capability_grant_source=inputs.get("capability_grant_source"),
        model_selection_source=inputs.get("model_selection_source"),
        candidate_output_envelope=data.get("candidate_output_envelope"),
        inline_memory_payload=inputs.get("inline_memory_payload"),
        subagent_runtime_graph=inputs.get("subagent_runtime_graph"),
        harness_metadata=data.get("harness_metadata"),
    )


@pytest.mark.parametrize("fixture_path", _negative_fixtures(), ids=lambda p: p.stem)
def test_negative_fixture_raises_exact_leaf_exception(fixture_path):
    data = _load(fixture_path)
    expected_cls = as2.ERROR_NAME_TO_CLASS[data["expected_error"]]
    with pytest.raises(expected_cls) as exc_info:
        _dispatch_fixture(data)
    # P0.6.6 hardening: the raised class must be exactly the declared leaf
    # class, not a generic base. This forbids the regression where a future
    # validator change accidentally up-casts the exception.
    assert type(exc_info.value) is expected_cls, (
        f"fixture {fixture_path.name} expected exactly {expected_cls.__name__}, "
        f"got {type(exc_info.value).__name__}"
    )


@pytest.mark.parametrize("fixture_path", _positive_fixtures(), ids=lambda p: p.stem)
def test_positive_fixture_validates_silently(fixture_path):
    data = _load(fixture_path)
    assert _dispatch_fixture(data) is None


# ---------------------------------------------------------------------------
# Identity context — alias edge cases
# ---------------------------------------------------------------------------


def test_alias_none_is_accepted():
    ctx = _identity_context()
    ctx["identity_seed"]["alias"] = None
    assert _call(adapter_identity_context=ctx) is None


def test_alias_meaningful_string_is_accepted():
    ctx = _identity_context()
    ctx["identity_seed"]["alias"] = "planner"
    assert _call(adapter_identity_context=ctx) is None


def test_alias_empty_string_is_rejected():
    ctx = _identity_context()
    ctx["identity_seed"]["alias"] = ""
    with pytest.raises(as2.AdapterIdentityContextIncompleteError):
        _call(adapter_identity_context=ctx)


def test_alias_whitespace_only_is_rejected():
    """P0.6.6 hardening: whitespace-only alias is an identity drift surface."""

    ctx = _identity_context()
    ctx["identity_seed"]["alias"] = "   "
    with pytest.raises(as2.AdapterIdentityContextIncompleteError):
        _call(adapter_identity_context=ctx)


def test_alias_tab_newline_is_rejected():
    ctx = _identity_context()
    ctx["identity_seed"]["alias"] = "\t\n"
    with pytest.raises(as2.AdapterIdentityContextIncompleteError):
        _call(adapter_identity_context=ctx)


def test_alias_non_string_is_rejected():
    ctx = _identity_context()
    ctx["identity_seed"]["alias"] = 42
    with pytest.raises(as2.AdapterIdentityContextIncompleteError):
        _call(adapter_identity_context=ctx)


# ---------------------------------------------------------------------------
# Identity context — seed field strictness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["parent_anchor", "spawn_nonce", "namespace"])
def test_seed_required_field_int_is_rejected(field):
    ctx = _identity_context()
    ctx["identity_seed"][field] = 12345
    with pytest.raises(as2.AdapterIdentityContextIncompleteError):
        _call(adapter_identity_context=ctx)


@pytest.mark.parametrize("field", ["parent_anchor", "spawn_nonce", "namespace"])
def test_seed_required_field_empty_string_is_rejected(field):
    ctx = _identity_context()
    ctx["identity_seed"][field] = ""
    with pytest.raises(as2.AdapterIdentityContextIncompleteError):
        _call(adapter_identity_context=ctx)


def test_definition_hash_uppercase_is_rejected():
    ctx = _identity_context()
    ctx["identity_seed"]["definition_hash"] = "sha256:" + "A" * 64
    with pytest.raises(as2.AdapterIdentityContextIncompleteError):
        _call(adapter_identity_context=ctx)


def test_definition_hash_wrong_length_is_rejected():
    ctx = _identity_context()
    ctx["identity_seed"]["definition_hash"] = "sha256:" + "a" * 63
    with pytest.raises(as2.AdapterIdentityContextIncompleteError):
        _call(adapter_identity_context=ctx)


def test_definition_hash_missing_prefix_is_rejected():
    ctx = _identity_context()
    ctx["identity_seed"]["definition_hash"] = "a" * 64
    with pytest.raises(as2.AdapterIdentityContextIncompleteError):
        _call(adapter_identity_context=ctx)


# ---------------------------------------------------------------------------
# Identity context — audit_context edge cases (R5 boundary)
# ---------------------------------------------------------------------------


def test_audit_context_absent_is_accepted():
    ctx = _identity_context()
    ctx.pop("audit_context", None)
    assert _call(adapter_identity_context=ctx) is None


def test_audit_context_null_is_accepted():
    ctx = _identity_context()
    ctx["audit_context"] = None
    assert _call(adapter_identity_context=ctx) is None


def test_audit_context_with_valid_soulprint_and_version():
    ctx = _identity_context()
    ctx["audit_context"] = {"soulprint": HASH_C, "identity_version": 7}
    assert _call(adapter_identity_context=ctx) is None


def test_audit_context_identity_version_zero_is_accepted():
    ctx = _identity_context()
    ctx["audit_context"] = {"identity_version": 0}
    assert _call(adapter_identity_context=ctx) is None


def test_audit_context_negative_identity_version_is_rejected():
    """P0.6.6 hardening: negative identity_version is nonsensical at audit boundary."""

    ctx = _identity_context()
    ctx["audit_context"] = {"identity_version": -1}
    with pytest.raises(as2.AdapterIdentityContextIncompleteError):
        _call(adapter_identity_context=ctx)


def test_audit_context_bool_identity_version_is_rejected():
    """P0.6.6 hardening: ``bool`` is a subclass of int in Python; reject explicitly."""

    ctx = _identity_context()
    ctx["audit_context"] = {"identity_version": True}
    with pytest.raises(as2.AdapterIdentityContextIncompleteError):
        _call(adapter_identity_context=ctx)


def test_audit_context_float_identity_version_is_rejected():
    ctx = _identity_context()
    ctx["audit_context"] = {"identity_version": 1.5}
    with pytest.raises(as2.AdapterIdentityContextIncompleteError):
        _call(adapter_identity_context=ctx)


def test_audit_context_non_sha256_soulprint_is_rejected():
    ctx = _identity_context()
    ctx["audit_context"] = {"soulprint": "not-a-hash"}
    with pytest.raises(as2.AdapterIdentityContextIncompleteError):
        _call(adapter_identity_context=ctx)


def test_audit_context_list_shape_is_rejected():
    ctx = _identity_context()
    ctx["audit_context"] = ["not", "a", "mapping"]
    with pytest.raises(as2.AdapterIdentityContextIncompleteError):
        _call(adapter_identity_context=ctx)


# ---------------------------------------------------------------------------
# Identity context — schema/profile strictness
# ---------------------------------------------------------------------------


def test_identity_context_unknown_schema_version_is_rejected():
    ctx = _identity_context()
    ctx["schema_version"] = "alpha3g.adapter_identity_context.v2"
    with pytest.raises(as2.AdapterIdentityContextIncompleteError):
        _call(adapter_identity_context=ctx)


def test_identity_context_unknown_profile_is_rejected():
    ctx = _identity_context()
    ctx["profile"] = "stable-canonical.v2"
    with pytest.raises(as2.AdapterIdentityContextIncompleteError):
        _call(adapter_identity_context=ctx)


# ---------------------------------------------------------------------------
# Static model registry — boundary cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["anthropic", "openai", "local", "custom"])
def test_real_provider_namespaces_rejected_in_p066_skeleton(provider):
    """Real providers are intentionally locked out of the P0.6.6 skeleton."""

    reg = _model_registry()
    reg["entries"][0]["model_ref"]["provider_namespace"] = provider
    with pytest.raises(as2.ModelRefUnknownError):
        _call(static_model_registry=reg)


def test_unknown_provider_namespace_is_rejected():
    reg = _model_registry()
    reg["entries"][0]["model_ref"]["provider_namespace"] = "totally-made-up"
    with pytest.raises(as2.ModelRefUnknownError):
        _call(static_model_registry=reg)


def test_registry_snapshot_hash_must_be_sha256():
    reg = _model_registry()
    reg["registry_snapshot_hash"] = "not-a-hash"
    with pytest.raises(as2.ModelRefUnknownError):
        _call(static_model_registry=reg)


def test_model_selection_source_none_is_rejected():
    with pytest.raises(as2.ModelRefUnknownError):
        _call(model_selection_source=None)


def test_model_selection_source_empty_model_is_rejected():
    with pytest.raises(as2.ModelRefUnknownError):
        _call(model_selection_source={"model": ""})


def test_model_selection_source_missing_model_key_is_rejected():
    with pytest.raises(as2.ModelRefUnknownError):
        _call(model_selection_source={})


def test_duplicate_legacy_model_in_registry_is_rejected():
    """P0.6.6 hardening: duplicate legacy_model creates lookup ambiguity."""

    reg = _model_registry()
    reg["entries"].append(
        {
            "legacy_model": "mock-agent-model",
            "model_ref": {
                "provider_namespace": "mock",
                "model_id": "different",
                "model_version": "2.0",
                "capability_profile_hash": HASH_C,
            },
        }
    )
    with pytest.raises(as2.ModelRefUnknownError):
        _call(static_model_registry=reg)


def test_model_ref_missing_capability_profile_hash_is_rejected():
    reg = _model_registry()
    del reg["entries"][0]["model_ref"]["capability_profile_hash"]
    with pytest.raises(as2.ModelRefUnknownError):
        _call(static_model_registry=reg)


# ---------------------------------------------------------------------------
# MemoryRefSource — duplicate / conflicting / boundary cases
# ---------------------------------------------------------------------------


def test_memory_ref_source_with_empty_list_is_accepted():
    """Empty list MUST be distinct from missing source per RFC §5.3."""

    mrs = _memory_ref_source()
    mrs["refs"] = []
    assert _call(memory_ref_source=mrs) is None


def test_memory_ref_source_refs_must_be_list_not_tuple():
    mrs = _memory_ref_source()
    mrs["refs"] = ()
    with pytest.raises(as2.MemoryRefSourceMissingError):
        _call(memory_ref_source=mrs)


def test_memory_ref_source_refs_null_is_rejected():
    mrs = _memory_ref_source()
    mrs["refs"] = None
    with pytest.raises(as2.MemoryRefSourceMissingError):
        _call(memory_ref_source=mrs)


def test_memory_ref_missing_space_id_is_rejected():
    mrs = _memory_ref_source()
    mrs["refs"] = [{"memory_key": "/k", "access_mode": "read"}]
    with pytest.raises(as2.AdapterMemorySpaceMismatchError):
        _call(memory_ref_source=mrs)


def test_duplicate_memory_refs_exact_match_is_rejected():
    """P0.6.6 hardening: align with P0.5.9 standalone-core invariants."""

    ref = {"memory_space_id": HASH_A, "memory_key": "/k", "access_mode": "read"}
    mrs = _memory_ref_source()
    mrs["refs"] = [ref, copy.deepcopy(ref)]
    with pytest.raises(as2.AdapterMemorySpaceMismatchError):
        _call(memory_ref_source=mrs)


def test_conflicting_access_mode_same_memory_address_is_rejected():
    """P0.6.6 hardening: read and write on same (space, key) must fail closed."""

    mrs = _memory_ref_source()
    mrs["refs"] = [
        {"memory_space_id": HASH_A, "memory_key": "/k", "access_mode": "read"},
        {"memory_space_id": HASH_A, "memory_key": "/k", "access_mode": "write"},
    ]
    with pytest.raises(as2.AdapterMemorySpaceMismatchError):
        _call(memory_ref_source=mrs)


def test_distinct_keys_same_access_mode_are_allowed():
    mrs = _memory_ref_source()
    mrs["refs"] = [
        {"memory_space_id": HASH_A, "memory_key": "/a", "access_mode": "read"},
        {"memory_space_id": HASH_A, "memory_key": "/b", "access_mode": "read"},
    ]
    assert _call(memory_ref_source=mrs) is None


def test_unsupported_memory_space_policy_version_is_rejected():
    mrs = _memory_ref_source()
    mrs["memory_space_policy_version"] = "alpha3g.memory_space_policy.v2"
    with pytest.raises(as2.AdapterMemorySpaceMismatchError):
        _call(memory_ref_source=mrs)


# ---------------------------------------------------------------------------
# CapabilityGrantSource — duplicate / shape cases (R8 boundary)
# ---------------------------------------------------------------------------


def _grant(tool_namespace="tool.x"):
    return {
        "schema_version": as2.CAPABILITY_GRANT_SCHEMA,
        "tool_namespace": tool_namespace,
        "function_descriptor_ref": {
            "namespace": "ns",
            "symbol": "sym",
            "input_schema_hash": HASH_A,
            "output_schema_hash": HASH_B,
        },
        "effect_policy_hash": HASH_C,
    }


def test_two_grants_for_distinct_tool_namespaces_are_allowed():
    cgs = _capability_grant_source()
    cgs["grants"] = [_grant("tool.a"), _grant("tool.b")]
    assert _call(capability_grant_source=cgs) is None


def test_duplicate_tool_namespace_grants_are_rejected():
    """P0.6.6 hardening: ambiguous scope/policy for one tool must fail closed."""

    cgs = _capability_grant_source()
    cgs["grants"] = [_grant("tool.x"), _grant("tool.x")]
    with pytest.raises(as2.CapabilityGrantInvalidRefError):
        _call(capability_grant_source=cgs)


def test_grant_missing_function_descriptor_ref_is_rejected():
    cgs = _capability_grant_source()
    g = _grant()
    del g["function_descriptor_ref"]
    cgs["grants"] = [g]
    with pytest.raises(as2.CapabilityGrantInvalidRefError):
        _call(capability_grant_source=cgs)


def test_function_descriptor_ref_uppercase_hash_is_rejected():
    cgs = _capability_grant_source()
    g = _grant()
    g["function_descriptor_ref"]["input_schema_hash"] = "sha256:" + "A" * 64
    cgs["grants"] = [g]
    with pytest.raises(as2.CapabilityGrantInvalidRefError):
        _call(capability_grant_source=cgs)


def test_function_descriptor_ref_missing_symbol_is_rejected():
    cgs = _capability_grant_source()
    g = _grant()
    del g["function_descriptor_ref"]["symbol"]
    cgs["grants"] = [g]
    with pytest.raises(as2.CapabilityGrantInvalidRefError):
        _call(capability_grant_source=cgs)


def test_grant_missing_effect_policy_hash_is_rejected():
    cgs = _capability_grant_source()
    g = _grant()
    del g["effect_policy_hash"]
    cgs["grants"] = [g]
    with pytest.raises(as2.CapabilityGrantInvalidRefError):
        _call(capability_grant_source=cgs)


# ---------------------------------------------------------------------------
# Envelope conflict — extended R7 coverage
# ---------------------------------------------------------------------------


def test_legacy_envelope_with_durable_actor_ref_marker_is_rejected():
    """P0.6.6 hardening: any legacy __type__ marker, not only 'agent'."""

    with pytest.raises(as2.AdapterEnvelopeConflictError):
        _call(candidate_output_envelope={"__type__": "durable_actor_ref", "data": {}})


def test_legacy_envelope_with_opaque_marker_is_rejected():
    with pytest.raises(as2.AdapterEnvelopeConflictError):
        _call(candidate_output_envelope={"__type__": "opaque"})


def test_legacy_envelope_with_durable_promise_marker_is_rejected():
    with pytest.raises(as2.AdapterEnvelopeConflictError):
        _call(candidate_output_envelope={"__type__": "durable_promise"})


def test_canonical_envelope_without_legacy_marker_passes():
    """A candidate envelope using only canonical fields must not be rejected."""

    assert (
        _call(
            candidate_output_envelope={
                "type": "agent_snapshot",
                "schema_version": "alpha3g.agent_snapshot.v1",
                "profile": as2.AS2_FIXTURE_PROFILE,
            }
        )
        is None
    )


def test_candidate_output_envelope_null_passes():
    assert _call(candidate_output_envelope=None) is None


# ---------------------------------------------------------------------------
# Subagent boundary — presence semantics
# ---------------------------------------------------------------------------


def test_subagent_graph_empty_dict_is_still_presence():
    """RFC §11: any subagent_runtime_graph value is out-of-scope."""

    with pytest.raises(as2.AdapterSubagentOutOfScopeError):
        _call(subagent_runtime_graph={})


def test_subagent_graph_empty_list_is_still_presence():
    with pytest.raises(as2.AdapterSubagentOutOfScopeError):
        _call(subagent_runtime_graph=[])


def test_subagent_graph_arbitrary_object_is_rejected():
    with pytest.raises(as2.AdapterSubagentOutOfScopeError):
        _call(subagent_runtime_graph={"agents": [{"name": "sub"}]})


def test_subagent_graph_none_passes():
    assert _call(subagent_runtime_graph=None) is None


# ---------------------------------------------------------------------------
# Ambient authority — explicit forbidden-call marker
# ---------------------------------------------------------------------------


def test_harness_metadata_with_forbidden_calls_triggers_ambient_authority():
    with pytest.raises(as2.AdapterAmbientAuthorityError):
        _call(
            harness_metadata={
                "requires_sandbox_mock": True,
                "forbidden_calls": ["time.time", "uuid.uuid4"],
            }
        )


def test_harness_metadata_without_forbidden_calls_passes():
    assert _call(harness_metadata={"requires_sandbox_mock": True, "forbidden_calls": []}) is None


def test_harness_metadata_none_passes():
    assert _call(harness_metadata=None) is None


# ---------------------------------------------------------------------------
# Inline memory — RFC §7.1 host-prep boundary
# ---------------------------------------------------------------------------


def test_any_inline_memory_payload_is_rejected():
    with pytest.raises(as2.AdapterInlineMemoryRejectedError):
        _call(inline_memory_payload={"short_term": ["leaked"]})


def test_inline_memory_payload_empty_string_is_rejected():
    """An empty string is not None; it is still 'present' for the boundary."""

    with pytest.raises(as2.AdapterInlineMemoryRejectedError):
        _call(inline_memory_payload="")


def test_inline_memory_payload_empty_list_is_rejected():
    with pytest.raises(as2.AdapterInlineMemoryRejectedError):
        _call(inline_memory_payload=[])


def test_inline_memory_payload_none_passes():
    assert _call(inline_memory_payload=None) is None


# ---------------------------------------------------------------------------
# Discipline anchors — validation-layer constraints that remain after P0.6.7
# ---------------------------------------------------------------------------


def test_adapter_module_still_has_no_to_agent_snapshot_function():
    """AS2 must not expose the legacy-looking conversion name."""

    assert not hasattr(as2, "to_agent_snapshot")


def test_adapter_module_has_only_the_reserved_projection_name():
    """P0.6.7 authorizes project_validated_as2_inputs, not alias names."""

    assert hasattr(as2, "project_validated_as2_inputs")
    assert not hasattr(as2, "build_snapshot_from_as2_inputs")
    assert not hasattr(as2, "build_snapshot_from_validated_inputs")


def test_adapter_module_still_quarantined():
    assert as2.AS2_ADAPTER_SKELETON_ENABLED is False


def test_validation_layer_still_has_no_legacy_runtime_imports():
    """Projection may use standalone AgentSnapshot core, never legacy runtime."""

    source = Path(as2.__file__).read_text(encoding="utf-8")
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


def test_adapter_module_does_not_use_feature_flag_machinery():
    """Vote A: feature flag deferred to P0.6.7+ when public projection appears."""

    source = Path(as2.__file__).read_text(encoding="utf-8")
    # The quarantine marker constant is allowed; an actual FeatureFlag class is not.
    assert "FeatureFlag" not in source
    assert "feature_flag" not in source.lower().replace("# ", "")
