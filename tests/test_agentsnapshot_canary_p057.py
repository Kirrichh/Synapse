"""P0.5.7 read-only AgentSnapshot canary tests.

These tests intentionally do not implement AgentSnapshot. They guard the
approved field-audit boundary by proving that the legacy AgentRuntime.to_dict()
shape is not a canonical AgentSnapshot v1 payload.
"""

from synapse.builtins import AgentRuntime


AGENTSNAPSHOT_V1_ALLOWLIST = {
    "agent_id",
    "definition_ref",
    "config",
    "canonical_fields",
    "memory_refs",
    "model_ref",
    "capability_grants",
    "profile",
    "schema_version",
}


def test_agent_runtime_to_dict_is_legacy_not_agentsnapshot_shape():
    agent = AgentRuntime("canary", "mock", trust_level="medium", trust_scope=["read"])

    payload = agent.to_dict()

    assert set(payload) == {"name", "model", "trust_level", "trust_scope", "memory"}
    assert "memory" in payload
    assert "memory_refs" not in payload
    assert "agent_id" not in payload
    assert "definition_ref" not in payload
    assert "capability_grants" not in payload
    assert "profile" not in payload
    assert "schema_version" not in payload
    assert set(payload) != AGENTSNAPSHOT_V1_ALLOWLIST
    assert not AGENTSNAPSHOT_V1_ALLOWLIST.issubset(set(payload))


def test_agent_runtime_live_handles_are_not_legacy_serialized_as_snapshot_fields():
    agent = AgentRuntime("canary", "mock")
    agent.register_tool("unsafe_live_tool", lambda: "live")
    agent.env = object()
    agent.think("hello")

    payload = agent.to_dict()

    assert "tools" not in payload
    assert "llm" not in payload
    assert "env" not in payload
    assert "capability_grants" not in payload
    assert "model_ref" not in payload
    assert "memory" in payload  # legacy raw memory remains explicitly non-canonical
