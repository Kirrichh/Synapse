"""P2 durable mailbox wait contract tests."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import synapse.application as app
from synapse import ast as synapse_ast
from synapse import compile_to_ast
from synapse.runtime.mailbox_wait import (
    MailboxWaitValidationError,
    normalized_mailbox_signal_hash,
    validate_mailbox_resume,
)


_MESSAGE_SOURCE = '''agent Inbox { model "mock" }
let self = Inbox
receive { sender => msg { print(sender) } }
'''

_TIMEOUT_SOURCE = '''agent Inbox { model "mock" }
let self = Inbox
receive timeout 10 { sender => msg { print(sender) } } else { print("timed out") }
'''

_TWO_RECEIVES_SOURCE = '''agent Inbox { model "mock" }
let self = Inbox
receive { sender => msg { print(sender) } }
receive { sender => msg { print(sender) } }
'''


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _signal(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    return path


def _artifact(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _start(tmp_path: Path, source: str = _MESSAGE_SOURCE, run_id: str = "mailbox-run") -> tuple[Path, dict[str, object], app.DurableRunResult]:
    program = _write(tmp_path / f"{run_id}.syn", source)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = app.execute_durable_run(app.DurableRunRequest(program, state_dir, run_id=run_id))
    artifact_path = state_dir / f"{run_id}.json"
    assert artifact_path.exists(), result
    return artifact_path, _artifact(artifact_path), result


def _message(*, message_id: str = "msg-1", payload: object = "approved", actor: str = "Inbox") -> dict[str, object]:
    return {
        "kind": "mailbox_message",
        "message_id": message_id,
        "actor": actor,
        "message": {
            "sender": "Tester",
            "receiver": actor,
            "method": "approve",
            "args": [payload],
        },
    }


def _resume(artifact_path: Path, artifact: dict[str, object], signal: Path) -> app.DurableRunResult:
    active = artifact["active_suspension"]
    return app.execute_durable_resume(
        app.DurableResumeRequest(artifact_path, str(active["suspension_id"]), signal_file=signal)
    )


def test_durable_receive_block_is_allowed_and_emits_mailbox_wait_payload(tmp_path: Path):
    artifact_path, artifact, result = _start(tmp_path)

    assert result.status == "PENDING"
    assert result.exit_code == 20
    active = artifact["active_suspension"]
    assert active["reason"] == "awaiting_message"
    assert active["promise_id"] is None
    assert active["payload"] == {
        "mailbox_wait_schema": "synapse.mailbox.wait.v1",
        "actor": "Inbox",
        "timeout": None,
        "receive_shape": {"patterns": 1, "has_else": False},
    }
    assert artifact_path.exists()


def test_timeout_receive_wait_is_pending_and_external_timeout_executes_else(tmp_path: Path):
    artifact_path, artifact, result = _start(tmp_path, _TIMEOUT_SOURCE)

    assert result.status == "PENDING"
    active = artifact["active_suspension"]
    assert active["reason"] == "awaiting_message_or_timeout"
    assert active["promise_id"] is None
    assert active["payload"]["timeout"] == 10

    resumed = _resume(artifact_path, artifact, _signal(tmp_path / "timeout.json", {"kind": "mailbox_timeout", "actor": "Inbox", "timeout": True}))
    completed = _artifact(artifact_path)

    assert resumed.status == "COMPLETED"
    assert resumed.public_payload["output_delta"] == ["timed out"]
    assert completed["replay_state"]["execution_history"][-1] == {
        "type": "receive_timeout", "actor": "Inbox", "timeout": 10
    }


def test_valid_message_is_canonicalized_before_inline_receive_flow(tmp_path: Path):
    artifact_path, artifact, _ = _start(tmp_path)
    resumed = _resume(artifact_path, artifact, _signal(tmp_path / "message.json", _message(payload={"ok": True})))
    completed = _artifact(artifact_path)
    received = completed["replay_state"]["execution_history"][-1]

    assert resumed.status == "COMPLETED"
    assert resumed.public_payload["output_delta"] == ["Tester"]
    assert received == {
        "type": "message_received",
        "actor": "Inbox",
        "message": {
            "sender": "Tester",
            "receiver": "Inbox",
            "method": "approve",
            "args": [{"ok": True}],
            "payload": {"ok": True},
        },
    }
    assert "kind" not in received["message"]
    assert "message_id" not in received["message"]


def test_external_payload_and_receiver_mismatch_are_rejected_without_mutation(tmp_path: Path):
    artifact_path, artifact, _ = _start(tmp_path)
    before = artifact_path.read_bytes()
    with_payload = _message()
    with_payload["message"]["payload"] = "forbidden"  # type: ignore[index]

    rejected = _resume(artifact_path, artifact, _signal(tmp_path / "bad-payload.json", with_payload))
    assert rejected.exit_code == 2
    assert artifact_path.read_bytes() == before

    wrong_receiver = _message()
    wrong_receiver["message"]["receiver"] = "Other"  # type: ignore[index]
    rejected = _resume(artifact_path, artifact, _signal(tmp_path / "wrong-receiver.json", wrong_receiver))
    assert rejected.exit_code == 2
    assert artifact_path.read_bytes() == before


def test_timeout_is_rejected_for_awaiting_message_without_artifact_mutation(tmp_path: Path):
    artifact_path, artifact, _ = _start(tmp_path)
    before = artifact_path.read_bytes()

    rejected = _resume(artifact_path, artifact, _signal(tmp_path / "timeout.json", {"kind": "mailbox_timeout", "actor": "Inbox", "timeout": True}))

    assert rejected.exit_code == 2
    assert artifact_path.read_bytes() == before


def test_actor_mismatch_and_extra_mailbox_fields_are_rejected_without_mutation(tmp_path: Path):
    artifact_path, artifact, _ = _start(tmp_path)
    before = artifact_path.read_bytes()

    wrong_actor = _message(actor="Other")
    assert _resume(artifact_path, artifact, _signal(tmp_path / "wrong-actor.json", wrong_actor)).exit_code == 2
    assert artifact_path.read_bytes() == before

    extra_field = _message()
    extra_field["trace"] = "not part of the mailbox envelope"
    assert _resume(artifact_path, artifact, _signal(tmp_path / "extra.json", extra_field)).exit_code == 2
    assert artifact_path.read_bytes() == before


def test_normalized_mailbox_hash_drives_duplicate_idempotency(tmp_path: Path):
    artifact_path, artifact, _ = _start(tmp_path)
    signal = _message(payload={"value": 1})
    expected_hash = normalized_mailbox_signal_hash(signal)
    resumed = _resume(artifact_path, artifact, _signal(tmp_path / "first.json", signal))
    completed = _artifact(artifact_path)
    entry = completed["idempotency"]["resolved_suspensions"][artifact["active_suspension"]["suspension_id"]]

    assert resumed.status == "COMPLETED"
    assert entry["signal_hash"] == expected_hash
    duplicate = app.execute_durable_resume(
        app.DurableResumeRequest(
            artifact_path,
            str(artifact["active_suspension"]["suspension_id"]),
            signal_file=_signal(tmp_path / "duplicate.json", copy.deepcopy(signal)),
        )
    )
    assert duplicate.public_payload == resumed.public_payload

    conflict = app.execute_durable_resume(
        app.DurableResumeRequest(
            artifact_path,
            str(artifact["active_suspension"]["suspension_id"]),
            signal_file=_signal(tmp_path / "conflict.json", _message(payload={"value": 2})),
        )
    )
    assert conflict.exit_code == 24

    timeout_conflict = app.execute_durable_resume(
        app.DurableResumeRequest(
            artifact_path,
            str(artifact["active_suspension"]["suspension_id"]),
            signal_file=_signal(tmp_path / "timeout-conflict.json", {"kind": "mailbox_timeout", "actor": "Inbox", "timeout": True}),
        )
    )
    assert timeout_conflict.exit_code == 24


def test_two_sequential_mailbox_resumes_replay_first_event_once(tmp_path: Path):
    artifact_path, first_artifact, _ = _start(tmp_path, _TWO_RECEIVES_SOURCE, "two-receives")
    first = _resume(artifact_path, first_artifact, _signal(tmp_path / "first.json", _message(message_id="a", payload="A")))
    second_artifact = _artifact(artifact_path)

    assert first.status == "PENDING"
    assert first.public_payload["output_delta"] == ["Tester"]
    assert second_artifact["active_suspension"]["reason"] == "awaiting_message"
    assert [event["type"] for event in second_artifact["replay_state"]["execution_history"]].count("message_received") == 1

    second = _resume(artifact_path, second_artifact, _signal(tmp_path / "second.json", _message(message_id="b", payload="B")))
    final = _artifact(artifact_path)
    received = [event for event in final["replay_state"]["execution_history"] if event.get("type") == "message_received"]

    assert second.status == "COMPLETED"
    assert second.public_payload["output_delta"] == ["Tester"]
    assert [event["message"]["payload"] for event in received] == ["A", "B"]


def test_replay_message_actor_receiver_and_order_mismatches_fail_closed(tmp_path: Path):
    artifact_path, artifact, _ = _start(tmp_path)
    tampered = copy.deepcopy(artifact)
    tampered["replay_state"]["execution_history"] = [{
        "type": "message_received",
        "actor": "Other",
        "message": {"sender": "Tester", "receiver": "Other", "method": "approve", "args": ["x"], "payload": "x"},
    }]
    # Recompute integrity through the application helper to isolate boundary replay validation.
    tampered["history_integrity"] = app._history_integrity(tampered["replay_state"])
    tampered["active_suspension"]["payload_hash"] = app._sha256_prefixed_value(tampered["active_suspension"]["payload"])
    tampered["active_suspension"]["boundary_fingerprint"] = app._sha256_prefixed_value(
        app._boundary_from_persisted_artifact(tampered, tampered["active_suspension"])
    )
    tampered["active_suspension"]["suspension_id"] = app._suspension_id(
        tampered["run_id"],
        tampered["active_suspension"]["sequence"],
        tampered["active_suspension"]["boundary_fingerprint"],
    )
    tampered["artifact_hash"] = app._artifact_with_hash({key: value for key, value in tampered.items() if key != "artifact_hash"})["artifact_hash"]
    artifact_path.write_text(json.dumps(tampered, sort_keys=True, separators=(",", ":")), encoding="utf-8")

    rejected = _resume(artifact_path, tampered, _signal(tmp_path / "message.json", _message()))

    assert rejected.exit_code == 22


def test_durable_receive_validation_rejects_multi_pattern_and_nondeterministic_timeout():
    multi = compile_to_ast('receive { a => m { print(m.payload) } b => n { print(n.payload) } }')
    with pytest.raises(app._DurableUnsupportedError, match="exactly one"):
        app._validate_durable_ast(multi)

    for timeout in ("random()", 'llm("x")', 'suspend await_human_approval("x")'):
        ast = compile_to_ast(f'receive timeout {timeout} {{ a => m {{ print(m.payload) }} }} else {{ print("t") }}')
        with pytest.raises(app._DurableUnsupportedError, match="deterministic scalar"):
            app._validate_durable_ast(ast)


def test_receive_validation_is_recursive_and_timeout_profile_rejects_async_nodes():
    safe_pattern = synapse_ast.ReceivePattern(
        sender_var="sender",
        target_var="message",
        body=[synapse_ast.ExprStmt(expr=synapse_ast.Literal(value="safe"))],
    )
    for timeout in (
        synapse_ast.LLMCall(prompt=synapse_ast.Literal(value="x")),
        synapse_ast.AwaitExpr(expr=synapse_ast.Variable(name="promise")),
        synapse_ast.SuspendExpr(request=synapse_ast.Literal(value="request")),
        synapse_ast.CallExpr(callee=synapse_ast.Variable(name="random"), args=[]),
    ):
        root = synapse_ast.Program(statements=[synapse_ast.ReceiveBlock(patterns=[safe_pattern], timeout=timeout)])
        with pytest.raises(app._DurableUnsupportedError, match="deterministic scalar"):
            app._validate_durable_ast(root)

    unsupported_body = synapse_ast.ReceivePattern(
        sender_var="sender",
        target_var="message",
        body=[synapse_ast.ExprStmt(expr=synapse_ast.MemberAccess(obj=synapse_ast.Variable(name="message"), member="payload"))],
    )
    with pytest.raises(app._DurableUnsupportedError, match="MemberAccess"):
        app._validate_durable_ast(synapse_ast.Program(statements=[synapse_ast.ReceiveBlock(patterns=[unsupported_body])]))

    with pytest.raises(app._DurableUnsupportedError, match="else_body requires timeout"):
        app._validate_durable_ast(
            synapse_ast.Program(statements=[synapse_ast.ReceiveBlock(patterns=[safe_pattern], else_body=[synapse_ast.ExprStmt(expr=synapse_ast.Literal(value="x"))])])
        )


def test_mailbox_contract_rejects_non_json_values_and_requires_derived_payload():
    payload = {
        "mailbox_wait_schema": "synapse.mailbox.wait.v1",
        "actor": "Inbox",
        "timeout": None,
        "receive_shape": {"patterns": 1, "has_else": False},
    }
    malformed = _message(payload=float("nan"))
    with pytest.raises(MailboxWaitValidationError):
        validate_mailbox_resume(malformed, "awaiting_message", payload)

    infinite = _message(payload=float("inf"))
    with pytest.raises(MailboxWaitValidationError):
        validate_mailbox_resume(infinite, "awaiting_message", payload)

    non_string = _message()
    non_string["message"]["args"] = [{1: "bad"}]  # type: ignore[index]
    with pytest.raises(MailboxWaitValidationError):
        validate_mailbox_resume(non_string, "awaiting_message", payload)

    host_value = _message(payload=object())
    with pytest.raises(MailboxWaitValidationError):
        validate_mailbox_resume(host_value, "awaiting_message", payload)

    multi_args = validate_mailbox_resume(_message(payload="first"), "awaiting_message", payload)
    assert multi_args.internal_value["payload"] == "first"


def test_oversized_resume_signal_is_rejected_by_existing_p2_signal_limit(tmp_path: Path):
    signal_path = tmp_path / "oversized.json"
    signal_path.write_bytes(b"x" * (app._MAX_SIGNAL_BYTES + 1))
    with pytest.raises(app._DurablePreExecutionError):
        app._read_signal_value(
            app.DurableResumeRequest(Path("unused.json"), "suspension", signal_file=signal_path),
            None,
        )


def test_ghost_mailbox_is_blocked_before_pop_in_inline_durable_receive_path():
    source = compile_to_ast(_MESSAGE_SOURCE)
    interpreter = app.Interpreter()
    interpreter._durable_mailbox_wait_enabled = True
    interpreter.mailboxes["Inbox"] = [{"sender": "ghost", "receiver": "Inbox", "method": "x", "args": ["ghost"], "payload": "ghost"}]
    flow = interpreter.interpret_async(source)

    with pytest.raises(Exception, match="DURABLE_GHOST_MAILBOX_CONSUMPTION"):
        next(flow)
