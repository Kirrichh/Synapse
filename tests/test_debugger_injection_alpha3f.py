from __future__ import annotations

import pytest

from synapse.cvm import VMState
from synapse.debugger_core import (
    EventInjectionValidator,
    ForkDisposedError,
    ForkLifecycleError,
    ForkRegistry,
    ForkedVMState,
    GovernanceViolationError,
    INJECTION_POLICY_MATRIX,
    REPLAY_DETERMINISTIC,
    REPLAY_EXPLORATORY_LIVE,
)
from synapse.golden_replay import DeterministicReplayError


class TraceContext:
    def __init__(self, events):
        self.events = list(events)
        self.index = 0

    def next_expected_event(self):
        if self.index >= len(self.events):
            return None
        return self.events[self.index]

    def consume_expected_event(self):
        self.index += 1


def test_policy_matrix_is_declarative_and_forbidden_events_are_explicit():
    assert INJECTION_POLICY_MATRIX["GUARD_ENTER"][REPLAY_DETERMINISTIC] == "recorded-only"
    assert INJECTION_POLICY_MATRIX["GUARD_ENTER"][REPLAY_EXPLORATORY_LIVE] == "new-or-recorded"
    assert INJECTION_POLICY_MATRIX["GUARD_VERDICT_OVERRIDE"] is None
    assert INJECTION_POLICY_MATRIX["GUARD_VIOLATION_ACK"] is None
    assert INJECTION_POLICY_MATRIX["CAPABILITY_GRANT"] is None
    assert INJECTION_POLICY_MATRIX["PROGRAM_HASH_REWRITE"] is None
    assert INJECTION_POLICY_MATRIX["HISTORY_HASH_REWRITE"] is None


def test_deterministic_replay_allows_recorded_guard_enter_and_consumes_trace():
    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_DETERMINISTIC)
    validator = EventInjectionValidator(registry)
    trace = TraceContext([{"type": "GUARD_ENTER", "guard_hash": "guard-a"}])

    result = validator.validate_injection(
        fork_id=fork.fork_id,
        event_type="GUARD_ENTER",
        payload={"guard_hash": "guard-a"},
        trace_context=trace,
    )

    assert result.allowed is True
    assert result.reason == "recorded guard replay"
    assert result.sanitized_payload == {"guard_hash": "guard-a"}
    assert trace.index == 1


def test_deterministic_replay_rejects_new_guard_path():
    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_DETERMINISTIC)
    validator = EventInjectionValidator(registry)

    with pytest.raises(DeterministicReplayError, match="New guard path forbidden"):
        validator.validate_injection(
            fork_id=fork.fork_id,
            event_type="GUARD_ENTER",
            payload={"guard_hash": "new-guard"},
            trace_context=TraceContext([]),
        )


def test_deterministic_replay_rejects_guard_hash_mismatch():
    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_DETERMINISTIC)
    validator = EventInjectionValidator(registry)

    with pytest.raises(DeterministicReplayError, match="GUARD_ENTER mismatch"):
        validator.validate_injection(
            fork_id=fork.fork_id,
            event_type="GUARD_ENTER",
            payload={"guard_hash": "new-guard"},
            trace_context=TraceContext([{"type": "GUARD_ENTER", "guard_hash": "old-guard"}]),
        )


def test_exploratory_live_allows_new_guard_enter_fork_local_only():
    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_EXPLORATORY_LIVE)
    validator = EventInjectionValidator(registry)

    result = validator.validate_injection(
        fork_id=fork.fork_id,
        event_type="GUARD_ENTER",
        payload={"guard_hash": "new-guard", "scope": "fork-local", "policy_hash": "policy-a"},
    )

    assert result.allowed is True
    assert result.reason == "fork-local guard evaluation path"
    assert result.sanitized_payload == {
        "guard_hash": "new-guard",
        "scope": "fork-local",
        "policy_hash": "policy-a",
    }


def test_exploratory_live_rejects_guard_enter_without_fork_local_scope():
    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_EXPLORATORY_LIVE)
    validator = EventInjectionValidator(registry)

    with pytest.raises(GovernanceViolationError, match="scope='fork-local'"):
        validator.validate_injection(
            fork_id=fork.fork_id,
            event_type="GUARD_ENTER",
            payload={"guard_hash": "new-guard"},
        )


@pytest.mark.parametrize(
    "event_type",
    [
        "GUARD_VERDICT_OVERRIDE",
        "GUARD_VIOLATION_ACK",
        "CAPABILITY_GRANT",
        "PROGRAM_HASH_REWRITE",
        "HISTORY_HASH_REWRITE",
    ],
)
def test_forbidden_events_raise_governance_violation_in_any_mode(event_type):
    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_EXPLORATORY_LIVE)
    validator = EventInjectionValidator(registry)

    with pytest.raises(GovernanceViolationError, match="forbidden injection event type"):
        validator.validate_injection(
            fork_id=fork.fork_id,
            event_type=event_type,
            payload={"scope": "fork-local"},
        )


@pytest.mark.parametrize("forbidden_key", ["mode", "fork_mode", "capability_grant", "program_hash", "history_hash"])
def test_client_supplied_mode_or_security_keys_are_rejected(forbidden_key):
    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_EXPLORATORY_LIVE)
    validator = EventInjectionValidator(registry)

    with pytest.raises(GovernanceViolationError, match="forbidden payload key"):
        validator.validate_injection(
            fork_id=fork.fork_id,
            event_type="ACTOR_MESSAGE",
            payload={"scope": "fork-local", forbidden_key: "attempted-bypass"},
        )


def test_actor_message_and_affective_event_are_exploratory_live_only():
    registry = ForkRegistry()
    deterministic = registry.create_fork(parent_history_hash="sha256:p1", mode=REPLAY_DETERMINISTIC)
    exploratory = registry.create_fork(parent_history_hash="sha256:p2", mode=REPLAY_EXPLORATORY_LIVE)
    validator = EventInjectionValidator(registry)

    with pytest.raises(DeterministicReplayError, match="not allowed"):
        validator.validate_injection(
            fork_id=deterministic.fork_id,
            event_type="ACTOR_MESSAGE",
            payload={"scope": "fork-local", "message": "hello"},
        )

    result = validator.validate_injection(
        fork_id=exploratory.fork_id,
        event_type="AFFECTIVE_EVENT",
        payload={"scope": "fork-local", "valence": 0.25},
    )
    assert result.allowed is True
    assert result.sanitized_payload["valence"] == 0.25


def test_unknown_event_type_is_fail_closed():
    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_EXPLORATORY_LIVE)
    validator = EventInjectionValidator(registry)

    with pytest.raises(GovernanceViolationError, match="unknown injection event type"):
        validator.validate_injection(
            fork_id=fork.fork_id,
            event_type="SURPRISE_EVENT",
            payload={"scope": "fork-local"},
        )


def test_dispose_clears_attached_fork_overlays_and_blocks_future_injection():
    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_EXPLORATORY_LIVE)
    base = VMState()
    base.locals["profile"] = {"score": 10}
    forked = ForkedVMState.from_vm_state(base, fork)
    forked.locals["profile"]["score"] = 42
    forked.memory["m"] = {"n": 1}
    forked.stack.append("child")
    registry.attach_resource(fork.fork_id, forked)

    disposed = registry.dispose(fork.fork_id)

    assert disposed.status == "disposed"
    assert forked.state_delta()["locals_overlay"] == {}
    assert forked.state_delta()["memory_overlay"] == {}
    assert forked.stack == []
    assert base.locals["profile"] == {"score": 10}
    with pytest.raises(ForkDisposedError):
        EventInjectionValidator(registry).validate_injection(
            fork_id=fork.fork_id,
            event_type="GUARD_ENTER",
            payload={"guard_hash": "g", "scope": "fork-local"},
        )


def test_completed_or_failed_forks_reject_injection_with_lifecycle_error():
    registry = ForkRegistry()
    completed = registry.create_fork(parent_history_hash="sha256:c", mode=REPLAY_EXPLORATORY_LIVE)
    failed = registry.create_fork(parent_history_hash="sha256:f", mode=REPLAY_EXPLORATORY_LIVE)
    registry.complete(completed.fork_id)
    registry.fail(failed.fork_id)
    validator = EventInjectionValidator(registry)

    with pytest.raises(ForkLifecycleError, match="not active"):
        validator.validate_injection(
            fork_id=completed.fork_id,
            event_type="GUARD_ENTER",
            payload={"guard_hash": "g", "scope": "fork-local"},
        )
    with pytest.raises(ForkLifecycleError, match="not active"):
        validator.validate_injection(
            fork_id=failed.fork_id,
            event_type="GUARD_ENTER",
            payload={"guard_hash": "g", "scope": "fork-local"},
        )


def test_invalid_status_transitions_are_rejected():
    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent")
    registry.complete(fork.fork_id)

    with pytest.raises(ForkLifecycleError, match="invalid fork transition"):
        registry.fail(fork.fork_id)
    with pytest.raises(ForkLifecycleError):
        registry.transition(fork.fork_id, "active")


def test_forked_vm_state_append_guard_enter_is_fork_local_and_parent_read_only():
    base = VMState()
    base.guard_stack.append({"guard_hash": "parent-guard", "verdict": "PASS"})
    fork = ForkRegistry().create_fork(parent_history_hash="sha256:parent", mode=REPLAY_EXPLORATORY_LIVE)
    forked = ForkedVMState.from_vm_state(base, fork)

    frame = forked.append_guard_enter("new-guard", policy_hash="policy-a")

    assert frame == {"guard_hash": "new-guard", "policy_hash": "policy-a", "scope": "fork-local"}
    assert base.guard_stack == [{"guard_hash": "parent-guard", "verdict": "PASS"}]
    assert forked.guard_stack[-1]["guard_hash"] == "new-guard"
    assert forked.guard_stack[0]["guard_hash"] == "parent-guard"


def test_golden_artifact_dict_remains_immutable_under_exploratory_injection():
    golden = {"final_history_hash": "sha256:golden", "history": [{"type": "HALT"}]}
    registry = ForkRegistry()
    fork = registry.create_fork(
        parent_history_hash=golden["final_history_hash"],
        mode=REPLAY_EXPLORATORY_LIVE,
        metadata={"source": "golden"},
    )
    validator = EventInjectionValidator(registry)

    validator.validate_injection(
        fork_id=fork.fork_id,
        event_type="GUARD_ENTER",
        payload={"guard_hash": "new-guard", "scope": "fork-local"},
    )

    assert golden == {"final_history_hash": "sha256:golden", "history": [{"type": "HALT"}]}
