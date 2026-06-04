from __future__ import annotations

import io
import json

import pytest

from synapse import cli
from synapse.cvm import VMState
from synapse.debugger_core import (
    DeterministicReplayPolicy,
    EventInjectionValidator,
    ForkDisposedError,
    ForkLifecycleError,
    ForkRegistry,
    ForkResourceLimitError,
    ForkedVMState,
    GovernanceViolationError,
    REPLAY_DETERMINISTIC,
    REPLAY_EXPLORATORY_LIVE,
)
from synapse.golden_replay import DeterministicReplayError


def run_debug(argv, registry=None):
    out = io.StringIO()
    err = io.StringIO()
    code = cli.run_debug_cli(argv, registry=registry or ForkRegistry(), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_cli_fork_maps_to_core():
    registry = ForkRegistry()
    code, out, err = run_debug([
        "fork",
        "--from",
        "sha256:parent",
        "--mode",
        REPLAY_EXPLORATORY_LIVE,
    ], registry)

    assert code == 0, err
    payload = json.loads(out)
    fork_id = payload["created"]["fork_id"]
    record = registry.get(fork_id)
    assert record.parent_history_hash == "sha256:parent"
    assert record.mode == REPLAY_EXPLORATORY_LIVE




def test_fork_default_mode_is_deterministic_replay():
    registry = ForkRegistry()

    code, out, err = run_debug([
        "fork",
        "--from",
        "sha256:parent",
    ], registry)

    assert code == 0, err
    payload = json.loads(out)
    fork_id = payload["created"]["fork_id"]
    record = registry.get(fork_id)
    assert record.mode == REPLAY_DETERMINISTIC


def test_malformed_cli_args_exit_1_not_argparse_default_2():
    code, out, err = run_debug([
        "fork",
        "--unknown-param",
    ])

    assert code == 1
    assert out == ""
    assert "invalid argument" in err


def test_cli_help_is_success_path_exit_0():
    code, out, err = run_debug([
        "fork",
        "--help",
    ])

    assert code == 0
    assert err == ""


def test_cli_inject_forwards_raw_payload(monkeypatch):
    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_EXPLORATORY_LIVE)
    captured = {}

    class FakeValidator:
        def __init__(self, registry_arg):
            captured["registry"] = registry_arg

        def validate_injection(self, *, fork_id, event_type, payload, trace_context=None):
            captured["fork_id"] = fork_id
            captured["event_type"] = event_type
            captured["payload"] = payload
            return type("Result", (), {
                "allowed": True,
                "event_type": event_type,
                "fork_id": fork_id,
                "sanitized_payload": payload,
                "reason": "fake-core-accepted",
            })()

    monkeypatch.setattr(cli, "EventInjectionValidator", FakeValidator)
    raw = {"guard_hash": "g-1"}  # deliberately missing scope; CLI must not validate it.
    code, out, err = run_debug([
        "inject-event",
        "--fork-id",
        fork.fork_id,
        "--type",
        "GUARD_ENTER",
        "--payload",
        json.dumps(raw),
    ], registry)

    assert code == 0, err
    assert captured["registry"] is registry
    assert captured["fork_id"] == fork.fork_id
    assert captured["event_type"] == "GUARD_ENTER"
    assert captured["payload"] == raw
    assert json.loads(out)["validated"]["reason"] == "fake-core-accepted"


@pytest.mark.parametrize(
    "exc,expected_code,stderr_text",
    [
        (GovernanceViolationError("nope"), 2, "governance violation"),
        (DeterministicReplayError("drift"), 3, "deterministic replay constraint violated"),
        (ForkDisposedError("fork is disposed: f"), 4, "disposed"),
        (ForkLifecycleError("bad transition"), 5, "invalid fork lifecycle transition"),
        (ForkResourceLimitError("limit"), 6, "fork resource limit exceeded"),
    ],
)
def test_cli_error_code_mapping(monkeypatch, exc, expected_code, stderr_text):
    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_EXPLORATORY_LIVE)

    class RaisingValidator:
        def __init__(self, registry_arg):
            pass

        def validate_injection(self, **kwargs):
            raise exc

    monkeypatch.setattr(cli, "EventInjectionValidator", RaisingValidator)
    code, out, err = run_debug([
        "inject-event",
        "--fork-id",
        fork.fork_id,
        "--type",
        "ACTOR_MESSAGE",
        "--payload",
        '{"scope":"fork-local"}',
    ], registry)

    assert code == expected_code
    assert out == ""
    assert stderr_text in err


def test_cli_malformed_json_rejected(monkeypatch):
    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_EXPLORATORY_LIVE)

    class ShouldNotBeCalled:
        def __init__(self, registry_arg):
            raise AssertionError("core validator should not be constructed for malformed JSON")

    monkeypatch.setattr(cli, "EventInjectionValidator", ShouldNotBeCalled)
    code, out, err = run_debug([
        "inject-event",
        "--fork-id",
        fork.fork_id,
        "--type",
        "GUARD_ENTER",
        "--payload",
        "{not-json",
    ], registry)

    assert code == 1
    assert out == ""
    assert "malformed json" in err


def test_forensic_preservation_after_dispose():
    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_EXPLORATORY_LIVE)
    base = VMState()
    base.locals["profile"] = {"score": 7}
    forked = ForkedVMState.from_vm_state(base, fork)
    forked.locals["profile"]["score"] = 99
    forked.memory["tmp"] = {"v": 1}
    history_suffix = [{"event": "INJECTED", "history_hash": "sha256:suffix"}]
    fork.metadata["history_suffix"] = history_suffix
    registry.attach_resource(fork.fork_id, forked)

    code, out, err = run_debug(["dispose", fork.fork_id], registry)

    assert code == 0, err
    assert json.loads(out)["disposed"]["status"] == "disposed"
    assert forked.state_delta()["locals_overlay"] == {}
    assert forked.state_delta()["memory_overlay"] == {}
    assert base.locals["profile"] == {"score": 7}
    assert history_suffix == [{"event": "INJECTED", "history_hash": "sha256:suffix"}]


def test_disposed_fork_cli_rejection():
    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_EXPLORATORY_LIVE)
    registry.dispose(fork.fork_id)

    code, out, err = run_debug([
        "inject-event",
        "--fork-id",
        fork.fork_id,
        "--type",
        "GUARD_ENTER",
        "--payload",
        '{"guard_hash":"g","scope":"fork-local"}',
    ], registry)

    assert code == 4
    assert out == ""
    assert "disposed" in err


def test_client_mode_in_payload_ignored_and_rejected_by_core():
    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_EXPLORATORY_LIVE)

    code, out, err = run_debug([
        "inject-event",
        "--fork-id",
        fork.fork_id,
        "--type",
        "ACTOR_MESSAGE",
        "--payload",
        '{"scope":"fork-local","mode":"exploratory-live"}',
    ], registry)

    assert code == 2
    assert out == ""
    assert "forbidden payload key: mode" in err


def test_cli_status_is_read_only():
    registry = ForkRegistry()
    a = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_EXPLORATORY_LIVE)
    status_code, status_out, status_err = run_debug(["status", a.fork_id], registry)

    assert status_code == 0, status_err
    assert json.loads(status_out)["status"]["fork_id"] == a.fork_id
    assert registry.get(a.fork_id).status == "active"
