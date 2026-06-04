"""P0.5.10 read-only AgentRuntime.to_dict() drift analysis (AS2-prep).

These tests pin the actual shape of legacy ``AgentRuntime.to_dict()`` across
the configurations enumerated in ``docs/AGENTRUNTIME-TODICT-DRIFT-REPORT.md``.
They do not modify runtime behavior, do not call interpreter or actor runtime
code, and do not produce serialized artifacts. Each test inspects a fresh
``AgentRuntime`` instance constructed inside the test.

Purpose:

- Enforce the legacy shape recorded in the drift report. A silent change to
  ``AgentRuntime.to_dict()`` (e.g. a new field, a renamed field, a dropped
  field) will fail one of these tests before the AS2 adapter is even
  proposed.
- Make the asymmetry between legacy fields and the canonical AgentSnapshot
  v1 allowlist explicit and machine-checkable.
- Document, via tests, that live runtime handles (``tools``, ``llm``, ``env``)
  and identity attributes (``soulprint``, ``identity_version``) never enter
  the legacy serialization path.

Scope discipline:

- No changes to ``synapse/builtins.py``, ``synapse/interpreter.py``,
  ``synapse/actor_runtime.py``, ``synapse/agent_snapshot.py``, memory
  backends, CVM, or golden fixtures.
- No flagged adapter, no profile selector, no canonical emission.
- Imports are limited to ``synapse.builtins.AgentRuntime`` and the
  AgentSnapshot allowlist constant from ``synapse.agent_snapshot`` (for
  asymmetry assertions only).
"""

import pytest

from synapse.agent_snapshot import AGENT_SNAPSHOT_FIELDS
from synapse.builtins import AgentRuntime


# Reference: the exact set of top-level keys that legacy ``to_dict()`` is
# expected to emit, per the drift report §2. If this changes silently, the
# canary fails and the drift report must be updated as part of the same patch.
LEGACY_TO_DICT_KEYS = frozenset({"name", "model", "trust_level", "trust_scope", "memory"})

LEGACY_MEMORY_SUBKEYS = frozenset({"short_term", "long_term", "capacity"})

# Fields that the canonical AgentSnapshot v1 allowlist requires but legacy
# ``to_dict()`` does not provide. See drift report §4 ("Identity surface").
CANONICAL_FIELDS_ABSENT_FROM_LEGACY = frozenset(
    {"agent_id", "definition_ref", "config", "canonical_fields", "memory_refs",
     "model_ref", "capability_grants", "profile", "schema_version"}
)


# ---------------------------------------------------------------------------
# Shape invariance across configurations (drift report §2)
# ---------------------------------------------------------------------------


def test_minimal_agent_shape_is_exactly_legacy_five_fields():
    payload = AgentRuntime("Greeter", "mock").to_dict()
    assert set(payload) == LEGACY_TO_DICT_KEYS


def test_full_constructor_shape_is_exactly_legacy_five_fields():
    payload = AgentRuntime(
        "Worker", "mock", trust_level="high", trust_scope=["finance", "legal"]
    ).to_dict()
    assert set(payload) == LEGACY_TO_DICT_KEYS


def test_shape_unchanged_when_memory_has_content():
    agent = AgentRuntime("Memo", "mock")
    agent.memory.short_term.append("user said hello")
    agent.memory.long_term["fact1"] = "earth is round"
    payload = agent.to_dict()
    assert set(payload) == LEGACY_TO_DICT_KEYS


def test_shape_unchanged_when_tools_are_registered():
    agent = AgentRuntime("Tooled", "mock")
    agent.register_tool("calculator", lambda x: x * 2)
    agent.register_tool("fetcher", lambda u: f"GET {u}")
    payload = agent.to_dict()
    assert set(payload) == LEGACY_TO_DICT_KEYS
    assert "tools" not in payload


def test_shape_unchanged_after_llm_backend_use():
    agent = AgentRuntime("Thinker", "mock")
    agent.think("prompt one")
    agent.think("prompt two")
    payload = agent.to_dict()
    assert set(payload) == LEGACY_TO_DICT_KEYS
    assert "llm" not in payload


def test_shape_unchanged_when_env_is_attached():
    agent = AgentRuntime("Env", "mock")
    agent.env = object()
    payload = agent.to_dict()
    assert set(payload) == LEGACY_TO_DICT_KEYS
    assert "env" not in payload


def test_shape_unchanged_when_soulprint_and_identity_version_attached():
    agent = AgentRuntime("Identity", "mock")
    agent.soulprint = {"values": {"curiosity": 0.94}, "memory": "long_term"}
    agent.identity_version = 3
    payload = agent.to_dict()
    assert set(payload) == LEGACY_TO_DICT_KEYS
    assert "soulprint" not in payload
    assert "identity_version" not in payload


@pytest.mark.parametrize(
    "memory_config",
    [None, "ephemeral", "long_term", "user_controlled", "default"],
)
def test_shape_unchanged_across_memory_config_values(memory_config):
    payload = AgentRuntime("Cfg", "mock", memory_config=memory_config).to_dict()
    assert set(payload) == LEGACY_TO_DICT_KEYS


# ---------------------------------------------------------------------------
# Field type invariants (drift report §2 table)
# ---------------------------------------------------------------------------


def test_name_is_str():
    assert isinstance(AgentRuntime("X", "mock").to_dict()["name"], str)


def test_model_is_str_not_descriptor():
    """Legacy emits a bare model string; AS2 must wrap this in a model_ref."""

    payload = AgentRuntime("X", "mock").to_dict()
    assert isinstance(payload["model"], str)
    assert payload["model"] == "mock"


def test_trust_level_defaults_to_medium():
    assert AgentRuntime("X", "mock").to_dict()["trust_level"] == "medium"


def test_trust_scope_defaults_to_empty_list():
    payload = AgentRuntime("X", "mock").to_dict()
    assert payload["trust_scope"] == []
    assert isinstance(payload["trust_scope"], list)


def test_memory_substructure_has_exactly_three_subkeys():
    payload = AgentRuntime("X", "mock").to_dict()
    assert set(payload["memory"]) == LEGACY_MEMORY_SUBKEYS


def test_memory_capacity_default_is_one_hundred():
    """memory_config currently does not influence capacity — recorded as
    drift surface in report §2."""

    assert AgentRuntime("X", "mock").to_dict()["memory"]["capacity"] == 100


# ---------------------------------------------------------------------------
# Asymmetry vs canonical AgentSnapshot v1 (drift report §4)
# ---------------------------------------------------------------------------


def test_legacy_keys_are_disjoint_from_canonical_only_fields():
    """No legacy top-level key collides with a canonical-only field."""

    canonical_only = AGENT_SNAPSHOT_FIELDS - {"type"}  # `type` would collide if added
    assert LEGACY_TO_DICT_KEYS.isdisjoint(canonical_only - {"config"})


def test_canonical_required_fields_absent_from_legacy():
    payload = AgentRuntime("X", "mock").to_dict()
    for canonical_field in CANONICAL_FIELDS_ABSENT_FROM_LEGACY:
        assert canonical_field not in payload, (
            f"legacy to_dict() leaked canonical field {canonical_field!r}; "
            f"update drift report or revert silent migration"
        )


def test_legacy_payload_has_no_schema_version_or_profile():
    """Drift guard: legacy must not silently grow canonical envelope fields."""

    payload = AgentRuntime("X", "mock").to_dict()
    assert "schema_version" not in payload
    assert "profile" not in payload
    assert "type" not in payload


# ---------------------------------------------------------------------------
# Live handle isolation (drift report §2, §4)
# ---------------------------------------------------------------------------


def test_legacy_payload_never_contains_live_handle_fields():
    agent = AgentRuntime("Full", "mock")
    agent.register_tool("calc", lambda x: x)
    agent.env = object()
    agent.soulprint = {"values": {"x": 0.5}}
    agent.identity_version = 2
    agent.think("warm up the backend")

    payload = agent.to_dict()
    forbidden = {"tools", "llm", "env", "mailbox", "scheduler", "process_id",
                 "thread_id", "soulprint", "identity_version"}
    leaked = forbidden.intersection(payload)
    assert leaked == set(), f"live handles leaked into legacy to_dict(): {leaked}"


# ---------------------------------------------------------------------------
# Round-trip stability (drift report §8)
# ---------------------------------------------------------------------------


def test_to_dict_from_dict_round_trip_is_byte_equal():
    agent = AgentRuntime("RT", "mock", trust_level="medium", trust_scope=["a", "b"])
    agent.memory.long_term["k"] = "v"
    agent.memory.short_term.append("note")
    payload_a = agent.to_dict()

    restored = AgentRuntime.from_dict(payload_a)
    payload_b = restored.to_dict()

    assert payload_a == payload_b


def test_to_dict_is_pure_and_idempotent():
    """Repeated calls must return equal dicts; mutating one must not affect
    the next call."""

    agent = AgentRuntime("Pure", "mock")
    agent.memory.long_term["k"] = "v"

    p1 = agent.to_dict()
    p2 = agent.to_dict()
    assert p1 == p2

    p1["name"] = "TAMPERED"
    p3 = agent.to_dict()
    assert p3["name"] == "Pure"


# ---------------------------------------------------------------------------
# Drift report acceptance — classification status anchor
# ---------------------------------------------------------------------------


# Mapping each legacy field to its drift-report classification. If this map
# changes, the drift report §3 table must change in the same patch.
LEGACY_FIELD_STATUS = {
    "name": "requires_transform",
    "model": "requires_transform",
    "trust_level": "migrates_as_is",
    "trust_scope": "migrates_as_is",
    "memory.short_term": "requires_transform",
    "memory.long_term": "requires_transform",
    "memory.capacity": "requires_transform",
}

VALID_STATUS_VALUES = frozenset(
    {"migrates_as_is", "requires_transform", "legacy_only", "excluded_from_canonical"}
)


def test_every_observed_legacy_field_has_a_classification():
    """Anchor the drift-report classification map to the actual probed shape."""

    payload = AgentRuntime("Anchor", "mock").to_dict()
    flat_keys = set(payload) - {"memory"}
    flat_keys |= {f"memory.{sub}" for sub in payload["memory"]}

    classified_keys = set(LEGACY_FIELD_STATUS)
    assert flat_keys == classified_keys, (
        f"drift report classification map is out of sync with actual shape: "
        f"observed={flat_keys}, classified={classified_keys}"
    )


def test_classification_values_are_from_approved_status_set():
    for field_name, status in LEGACY_FIELD_STATUS.items():
        assert status in VALID_STATUS_VALUES, (
            f"unknown classification {status!r} for {field_name!r}"
        )
