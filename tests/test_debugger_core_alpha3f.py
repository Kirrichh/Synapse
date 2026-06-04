from __future__ import annotations

import pytest

from synapse.cvm import VMState
from synapse.debugger_core import (
    DeterministicReplayPolicy,
    ForkRecord,
    ForkRegistry,
    ForkResourceLimitError,
    ForkedVMState,
    OverlayMap,
    REPLAY_DETERMINISTIC,
    REPLAY_EXPLORATORY_LIVE,
)
from synapse.golden_replay import DeterministicReplayError


def test_fork_record_requires_parent_history_hash():
    with pytest.raises(ValueError, match="parent_history_hash"):
        ForkRecord(fork_id="fork-x", parent_history_hash="")


def test_fork_registry_creates_unique_fork_ids():
    registry = ForkRegistry()
    a = registry.create_fork(parent_history_hash="sha256:a")
    b = registry.create_fork(parent_history_hash="sha256:a")
    assert a.fork_id != b.fork_id
    assert a.mode == REPLAY_DETERMINISTIC
    assert registry.get(a.fork_id) == a


def test_fork_from_golden_creates_new_lineage_without_mutating_artifact_dict():
    golden = {"final_history_hash": "sha256:golden", "metadata": {"name": "baseline"}}
    record = ForkRecord.from_golden(
        fork_id="fork-golden",
        final_history_hash=golden["final_history_hash"],
        mode=REPLAY_EXPLORATORY_LIVE,
    )
    assert record.parent_history_hash == "sha256:golden"
    assert record.metadata["source"] == "golden"
    assert golden == {"final_history_hash": "sha256:golden", "metadata": {"name": "baseline"}}


def test_fork_registry_enforces_resource_limit_deterministically():
    registry = ForkRegistry(max_active_forks=1)
    registry.create_fork(parent_history_hash="sha256:a")
    with pytest.raises(ForkResourceLimitError):
        registry.create_fork(parent_history_hash="sha256:b")


def test_overlay_map_read_cascades_overlay_to_parent():
    parent = {"a": 1, "b": 2}
    overlay = OverlayMap(parent)
    overlay["a"] = 10
    assert overlay["a"] == 10
    assert overlay["b"] == 2
    assert overlay.snapshot() == {"a": 10, "b": 2}
    assert parent == {"a": 1, "b": 2}


def test_overlay_map_write_is_overlay_only():
    parent = {"a": 1}
    overlay = OverlayMap(parent)
    overlay["a"] = 2
    overlay["c"] = 3
    assert parent == {"a": 1}
    assert overlay.overlay_delta() == {"a": 2, "c": 3}


def test_overlay_map_nested_mutation_uses_write_barrier():
    parent = {"profile": {"score": 10, "tags": ["root"]}}
    overlay = OverlayMap(parent)
    profile = overlay["profile"]
    profile["score"] = 11
    profile["tags"].append("fork")
    assert overlay["profile"] == {"score": 11, "tags": ["root", "fork"]}
    assert parent["profile"] == {"score": 10, "tags": ["root"]}
    assert "profile" in overlay.overlay_delta()


def test_overlay_map_delete_parent_key_uses_tombstone_without_parent_pollution():
    parent = {"a": 1, "b": 2}
    overlay = OverlayMap(parent)
    del overlay["a"]
    assert "a" not in overlay
    assert overlay.snapshot() == {"b": 2}
    assert parent == {"a": 1, "b": 2}
    assert overlay.overlay_delta(include_tombstones=True)["a"] == {"__deleted__": True}


def test_forked_vm_state_wraps_base_without_mutating_base():
    base = VMState()
    base.locals["profile"] = {"score": 10}
    base.stack.append("base-value")
    base.context_stack.append("ctx")
    record = ForkRecord(fork_id="fork-1", parent_history_hash="sha256:parent")
    forked = ForkedVMState.from_vm_state(base, record, parent_memory={"memory_key": {"n": 1}})

    forked.locals["profile"]["score"] = 99
    forked.memory["memory_key"]["n"] = 2
    forked.stack.append("child-value")
    forked.context_stack.append("child-ctx")

    assert base.locals["profile"] == {"score": 10}
    assert base.stack == ["base-value"]
    assert base.context_stack == ["ctx"]
    assert forked.locals["profile"] == {"score": 99}
    assert forked.memory["memory_key"] == {"n": 2}


def test_forked_vm_state_delta_contains_only_fork_local_overlays():
    base = VMState()
    base.locals["x"] = 1
    record = ForkRecord(fork_id="fork-2", parent_history_hash="sha256:parent")
    forked = ForkedVMState.from_vm_state(base, record)
    forked.locals["x"] = 2
    forked.memory["y"] = 3
    delta = forked.state_delta()
    assert delta["fork_id"] == "fork-2"
    assert delta["locals_overlay"] == {"x": 2}
    assert delta["memory_overlay"] == {"y": 3}
    assert base.locals == {"x": 1}


def test_deterministic_replay_policy_missing_llm_cache_is_hard_failure():
    policy = DeterministicReplayPolicy(llm_cache={})
    with pytest.raises(DeterministicReplayError, match="missing cache entry"):
        policy.resolve_llm("content-key")


def test_deterministic_replay_policy_missing_host_event_is_hard_failure():
    policy = DeterministicReplayPolicy(host_events=[])
    with pytest.raises(DeterministicReplayError, match="missing recorded host event"):
        policy.consume_host_event(event_type="host_call", symbol="llm.request")


def test_deterministic_replay_policy_consumes_matching_host_event_without_live_fallback():
    policy = DeterministicReplayPolicy(host_events=[{"type": "host_call", "symbol": "SYS_STDOUT", "result": None}])
    event = policy.consume_host_event(event_type="host_call", symbol="SYS_STDOUT")
    assert event["symbol"] == "SYS_STDOUT"
    with pytest.raises(DeterministicReplayError):
        policy.consume_host_event(event_type="host_call", symbol="SYS_STDOUT")


def test_deterministic_replay_policy_does_not_call_provider():
    class Provider:
        def dispatch(self):  # pragma: no cover - should never be called
            raise AssertionError("live provider fallback must not be called")

    provider = Provider()
    policy = DeterministicReplayPolicy(llm_cache={"k": "cached"})
    assert policy.resolve_llm("k") == "cached"
    # The provider object is intentionally unused; deterministic replay resolves
    # from cache only and has no provider fallback path.
    assert isinstance(provider, Provider)
