from __future__ import annotations

import copy
import hashlib
import errno
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from synapse import ast as synapse_ast
from synapse import cli as synapse_cli
from synapse import compile_to_ast
import synapse.application as app
from synapse.hardening import hash_event_chain


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _run(args: list[str], *, stdin: str | None = None, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "synapse", *args],
        cwd=REPO_ROOT,
        input=stdin,
        text=True,
        capture_output=True,
        env=env,
        timeout=timeout,
    )


def _durable(program: Path, state_dir: Path, run_id: str, *extra: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return _run(["run", str(program), "--durable", "--state-dir", str(state_dir), "--run-id", run_id, *extra], stdin=stdin)


def _resume(
    state_file: Path,
    suspension_id: str,
    signal_file: Path | str,
    *,
    stdin: str | None = None,
    timeout: int = 15,
) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            "resume",
            "--state-file",
            str(state_file),
            "--suspension-id",
            suspension_id,
            "--signal-file",
            str(signal_file),
        ],
        stdin=stdin,
        timeout=timeout,
    )


def _run_raw(argv: list[str], *, stdin: str | None = None, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        argv,
        cwd=REPO_ROOT,
        input=stdin,
        text=True,
        capture_output=True,
        env=env,
        timeout=timeout,
    )


def _signal(path: Path, value: object) -> Path:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    return path


def _artifact_path(payload: dict[str, object]) -> Path:
    return Path(str(payload["artifact_path"]))


def _read_artifact(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _store_artifact(path: Path, artifact: dict[str, object]) -> None:
    artifact["artifact_hash"] = _sha256_value(_artifact_hash_preimage(artifact))
    path.write_text(
        json.dumps(artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )


def _json_stdout(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, completed.stdout
    return json.loads(lines[0])


def _load_artifact(payload: dict[str, object]) -> dict[str, object]:
    artifact_path = Path(str(payload["artifact_path"]))
    assert artifact_path.exists()
    return json.loads(artifact_path.read_text(encoding="utf-8"))


def _artifact_hash_preimage(artifact: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in artifact.items() if key != "artifact_hash"}


def _strict_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha256_value(value: object) -> str:
    return "sha256:" + hashlib.sha256(_strict_bytes(value)).hexdigest()


def _artifact_content_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_artifact_integrity(path: Path) -> dict[str, object]:
    artifact = _read_artifact(path)
    expected = app._artifact_with_hash(_artifact_hash_preimage(artifact))
    assert artifact["artifact_hash"] == expected["artifact_hash"]
    history = artifact["replay_state"]["execution_history"]
    chain = hash_event_chain(history)
    assert artifact["history_integrity"]["chain"] == chain
    assert artifact["history_integrity"]["event_count"] == len(history)
    assert artifact["history_integrity"]["final_hash"] == (chain[-1]["hash"] if chain else "")
    return artifact


def _artifact_snapshot(path: Path) -> dict[str, object]:
    artifact = _read_artifact(path)
    return {
        "bytes": path.read_bytes(),
        "content_hash": _artifact_content_hash(path),
        "artifact_hash": artifact["artifact_hash"],
        "revision": artifact["revision"],
        "resolved_count": len(artifact["idempotency"]["resolved_suspensions"]),
    }


def _assert_artifact_unchanged(path: Path, before: dict[str, object]) -> None:
    after = _artifact_snapshot(path)
    assert after["bytes"] == before["bytes"]
    assert after["content_hash"] == before["content_hash"]
    assert after["artifact_hash"] == before["artifact_hash"]
    assert after["revision"] == before["revision"]
    assert after["resolved_count"] == before["resolved_count"]


def _assert_error_payload(
    payload: dict[str, object],
    *,
    exit_code: int,
    code: str,
    message: str,
    run_id: str | None = None,
    correlation_id: str | None = None,
) -> None:
    assert payload == {
        "result_schema_version": "1.0.0",
        "status": "ERROR",
        "exit_code": exit_code,
        "run_id": run_id,
        "correlation_id": correlation_id,
        "error": {"code": code, "message": message},
    }


def _assert_generated_run_id(payload: dict[str, object]) -> str:
    run_id = payload["run_id"]
    assert isinstance(run_id, str)
    assert run_id.startswith("run-")
    return run_id


def _run_with_lock_mkdir_fault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: OSError,
) -> app.DurableRunResult:
    program = _write(tmp_path / "program.syn", 'print("x")\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    run_id = "run-lock-fault"
    lock_path = state_dir / f"{run_id}.json.lock"
    original_mkdir = Path.mkdir

    def faulting_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self == lock_path:
            raise fault
        original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", faulting_mkdir)
    return app.execute_durable_run(
        app.DurableRunRequest(
            source_path=program,
            state_dir=state_dir,
            run_id=run_id,
            correlation_id="corr-lock-fault",
        )
    )


def test_durable_completed_cli_commits_terminal_artifact(tmp_path: Path):
    program = _write(tmp_path / "complete.syn", 'print("done")\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    completed = _durable(program, state_dir, "run-complete")

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    payload = _json_stdout(completed)
    artifact = _load_artifact(payload)
    assert payload["status"] == "COMPLETED"
    assert payload["output_delta"] == ["done"]
    assert artifact["status"] == "COMPLETED"
    assert artifact["terminal"] == {"status": "COMPLETED", "exit_code": 0}
    assert artifact["active_suspension"] is None
    assert artifact["source"]["content"] == 'print("done")\n'
    assert artifact["idempotency"] == {"resolved_suspensions": {}}
    assert "request_hash" not in artifact
    assert "program_result" not in artifact
    assert "global_env" not in artifact["replay_state"]
    expected_hash = "sha256:" + __import__("hashlib").sha256(_strict_bytes(_artifact_hash_preimage(artifact))).hexdigest()
    assert artifact["artifact_hash"] == expected_hash
    expected_chain = hash_event_chain(artifact["replay_state"]["execution_history"])
    assert artifact["history_integrity"]["chain"] == expected_chain
    assert artifact["history_integrity"]["final_hash"] == (expected_chain[-1]["hash"] if expected_chain else "")


def test_durable_pending_suspend_uses_canonical_suspension_id(tmp_path: Path):
    program = _write(
        tmp_path / "pending.syn",
        'let plan = "ship"\nlet approved = suspend await_human_approval(plan)\nprint(approved)\n',
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    completed = _durable(program, state_dir, "run-pending")

    assert completed.returncode == 20, completed.stderr
    payload = _json_stdout(completed)
    artifact = _load_artifact(payload)
    active = artifact["active_suspension"]
    assert artifact["status"] == "PENDING"
    assert artifact["terminal"] is None
    assert active["reason"] == "awaiting_external_signal"
    assert active["node_type"] == "SuspendExpr"
    assert "env" not in active
    assert "request" not in active
    expected_id = "susp-" + __import__("hashlib").sha256(
        _strict_bytes({
            "version": "synapse-p2-suspension-v1",
            "run_id": "run-pending",
            "sequence": 1,
            "boundary_fingerprint": active["boundary_fingerprint"],
        })
    ).hexdigest()
    assert active["suspension_id"] == expected_id
    assert payload["suspension_id"] == expected_id
    assert payload["resume_argv"] == [
        sys.executable,
        "-m",
        "synapse",
        "resume",
        "--state-file",
        str(Path(str(payload["artifact_path"]))),
        "--suspension-id",
        expected_id,
        "--signal-file",
        "<path|->",
    ]
    assert "run" not in payload["resume_argv"]
    assert "--durable" not in payload["resume_argv"]
    assert "ship" not in json.dumps(active, sort_keys=True)
    assert "ship" not in json.dumps(payload["resume_argv"], sort_keys=True)
    assert "request_hash" not in artifact


def test_resume_cli_missing_flags_are_structured_and_replay_cli_is_preserved(tmp_path: Path):
    missing_cases = [
        ["resume"],
        ["resume", "--state-file", str(tmp_path / "run.json")],
        ["resume", "--suspension-id", "susp-missing"],
        ["resume", "--signal-file", str(tmp_path / "signal.json")],
    ]
    for args in missing_cases:
        completed = _run(args)

        assert completed.returncode == 2
        assert completed.stderr == ""
        _assert_error_payload(
            _json_stdout(completed),
            exit_code=2,
            code="INVALID_CLI_INPUT",
            message="Invalid durable input",
        )

    replay_help = _run(["replay", "--help"])
    assert replay_help.returncode == 0
    assert "--mock" in replay_help.stdout


def test_resume_from_p2a_resume_argv_completes_suppresses_prefix_and_is_terminal_idempotent(tmp_path: Path):
    program = _write(
        tmp_path / "external.syn",
        'print("before")\nlet approved = suspend await_human_approval("go")\nprint("after")\n',
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    signal = _signal(tmp_path / "signal.json", True)
    conflict_signal = _signal(tmp_path / "conflict.json", False)

    initial = _durable(program, state_dir, "run-resume")
    initial_payload = _json_stdout(initial)
    artifact_path = _artifact_path(initial_payload)
    initial_artifact = _read_artifact(artifact_path)
    active = initial_artifact["active_suspension"]

    assert initial.returncode == 20
    assert initial.stderr == ""
    assert initial_payload["output_delta"] == ["before"]
    assert initial_artifact["output_state"]["line_count"] == 1
    assert active["sequence"] == 1

    resume_argv = list(initial_payload["resume_argv"])
    assert resume_argv[-1] == "<path|->"
    resume_argv[-1] = str(signal)
    completed = _run_raw([str(item) for item in resume_argv])
    completed_payload = _json_stdout(completed)
    completed_artifact = _read_artifact(artifact_path)
    entry = completed_artifact["idempotency"]["resolved_suspensions"][active["suspension_id"]]

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert completed_payload["status"] == "COMPLETED"
    assert completed_payload["artifact_revision"] == 2
    assert completed_payload["output_delta"] == ["after"]
    assert completed_artifact["status"] == "COMPLETED"
    assert completed_artifact["revision"] == 2
    assert completed_artifact["active_suspension"] is None
    assert completed_artifact["terminal"] == {"status": "COMPLETED", "exit_code": 0}
    assert set(entry) == {"signal_hash", "committed_revision", "committed_status", "operation_result"}
    assert entry["committed_revision"] == 2
    assert entry["committed_status"] == "COMPLETED"
    assert entry["operation_result"]["status"] == "COMPLETED"
    assert entry["operation_result"]["artifact_revision"] == 2
    assert entry["operation_result"]["run_id"] == "run-resume"
    assert entry["operation_result"]["source_hash"] == completed_artifact["source"]["hash"]
    assert "resume_argv" not in entry["operation_result"]
    for forbidden in {"raw_signal", "signal_file", "request", "prompt", "initial_bindings", "traceback", "raw_exception"}:
        assert forbidden not in entry["operation_result"]

    before_bytes = artifact_path.read_bytes()
    before_mtime = artifact_path.stat().st_mtime_ns
    duplicate = _run_raw([str(item) for item in resume_argv])
    assert duplicate.returncode == 0
    assert _json_stdout(duplicate)["output_delta"] == ["after"]
    assert artifact_path.read_bytes() == before_bytes
    assert artifact_path.stat().st_mtime_ns == before_mtime

    conflict_argv = list(resume_argv)
    conflict_argv[-1] = str(conflict_signal)
    conflict = _run_raw([str(item) for item in conflict_argv])
    assert conflict.returncode == 24
    assert conflict.stderr == ""
    _assert_error_payload(
        _json_stdout(conflict),
        exit_code=24,
        code="RESOLUTION_CONFLICT",
        message="Resolution conflict",
        run_id="run-resume",
    )
    assert artifact_path.read_bytes() == before_bytes


def test_resume_pending_to_pending_uses_sequence_aware_ids_and_old_idempotency(tmp_path: Path):
    program = _write(
        tmp_path / "two-boundaries.syn",
        "\n".join(
            [
                'let first = suspend await_human_approval("one")',
                'let second = suspend await_human_approval("two")',
                "print(second)",
                "",
            ]
        ),
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    first_signal = _signal(tmp_path / "first.json", {"approved": True})
    conflict_signal = _signal(tmp_path / "first-conflict.json", {"approved": False})
    second_signal = _signal(tmp_path / "second.json", "done")

    initial = _durable(program, state_dir, "run-two-boundaries")
    initial_payload = _json_stdout(initial)
    artifact_path = _artifact_path(initial_payload)
    first_id = str(initial_payload["suspension_id"])

    pending = _resume(artifact_path, first_id, first_signal)
    pending_payload = _json_stdout(pending)
    pending_artifact = _read_artifact(artifact_path)
    second_active = pending_artifact["active_suspension"]
    first_entry = pending_artifact["idempotency"]["resolved_suspensions"][first_id]

    assert pending.returncode == 20, pending.stderr
    assert pending.stderr == ""
    assert pending_payload["status"] == "PENDING"
    assert pending_payload["artifact_revision"] == 2
    assert pending_artifact["revision"] == 2
    assert second_active["sequence"] == 2
    assert second_active["suspension_id"] != first_id
    assert first_entry["committed_revision"] == 2
    assert first_entry["committed_status"] == "PENDING"
    assert first_entry["operation_result"]["suspension_id"] == second_active["suspension_id"]
    assert "resume_argv" not in first_entry["operation_result"]

    bytes_after_pending = artifact_path.read_bytes()
    old_duplicate = _resume(artifact_path, first_id, first_signal)
    assert old_duplicate.returncode == 20
    assert _json_stdout(old_duplicate)["suspension_id"] == second_active["suspension_id"]
    assert artifact_path.read_bytes() == bytes_after_pending

    old_conflict = _resume(artifact_path, first_id, conflict_signal)
    assert old_conflict.returncode == 24
    assert artifact_path.read_bytes() == bytes_after_pending

    completed = _resume(artifact_path, second_active["suspension_id"], second_signal)
    completed_payload = _json_stdout(completed)
    completed_artifact = _read_artifact(artifact_path)
    assert completed.returncode == 0, completed.stderr
    assert completed_payload["output_delta"] == ["done"]
    assert completed_artifact["status"] == "COMPLETED"
    assert completed_artifact["revision"] == 3


def test_resume_llm_signal_contract_accepts_strings_and_rejects_other_json_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    def create_llm_artifact(run_id: str) -> tuple[Path, str]:
        program = _write(
            tmp_path / f"{run_id}.syn",
            'let p = prompt "hello"\nlet answer = llm(p)\nprint(answer)\n',
        )
        state_dir = tmp_path / f"{run_id}-state"
        state_dir.mkdir()
        pending = _durable(program, state_dir, run_id)
        payload = _json_stdout(pending)
        assert pending.returncode == 20
        return _artifact_path(payload), str(payload["suspension_id"])

    string_path, string_id = create_llm_artifact("run-llm-string")
    accepted = _resume(string_path, string_id, _signal(tmp_path / "llm-string.json", "answer"))
    assert accepted.returncode == 0, accepted.stderr
    assert _json_stdout(accepted)["output_delta"] == ["answer"]

    empty_path, empty_id = create_llm_artifact("run-llm-empty")
    empty = _resume(empty_path, empty_id, _signal(tmp_path / "llm-empty.json", ""))
    assert empty.returncode == 0, empty.stderr
    assert _json_stdout(empty)["output_delta"] == [""]

    invalid_path, invalid_id = create_llm_artifact("run-llm-invalid")
    before = invalid_path.read_bytes()

    class NoInterpreter:
        def __init__(self) -> None:
            raise AssertionError("Interpreter must not be created for invalid LLM signal")

    monkeypatch.setattr(app, "Interpreter", NoInterpreter)
    for index, value in enumerate([None, {}, [], 1, True]):
        signal_path = _signal(tmp_path / f"llm-invalid-{index}.json", value)
        result = app.execute_durable_resume(
            app.DurableResumeRequest(
                state_file=invalid_path,
                suspension_id=invalid_id,
                signal_file=signal_path,
            )
        )
        assert result.exit_code == 2
        _assert_error_payload(
            result.public_payload,
            exit_code=2,
            code="INVALID_CLI_INPUT",
            message="Invalid durable input",
            run_id="run-llm-invalid",
        )
        assert invalid_path.read_bytes() == before


def test_resume_uses_embedded_source_and_rejects_source_divergence(tmp_path: Path):
    program = _write(
        tmp_path / "embedded.syn",
        'let approved = suspend await_human_approval("go")\nprint("embedded")\n',
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    signal = _signal(tmp_path / "signal.json", True)

    pending = _durable(program, state_dir, "run-embedded")
    payload = _json_stdout(pending)
    artifact_path = _artifact_path(payload)
    program.unlink()

    completed = _resume(artifact_path, str(payload["suspension_id"]), signal)
    assert completed.returncode == 0, completed.stderr
    assert _json_stdout(completed)["output_delta"] == ["embedded"]

    bad_program = _write(
        tmp_path / "diverged.syn",
        'let approved = suspend await_human_approval("go")\n',
    )
    bad_state = tmp_path / "bad-state"
    bad_state.mkdir()
    bad_pending = _durable(bad_program, bad_state, "run-diverged")
    bad_payload = _json_stdout(bad_pending)
    bad_path = _artifact_path(bad_payload)
    artifact = _read_artifact(bad_path)
    artifact["source"]["content"] += 'print("mutated")\n'
    _store_artifact(bad_path, artifact)
    before = bad_path.read_bytes()

    rejected = _resume(bad_path, str(bad_payload["suspension_id"]), signal)
    assert rejected.returncode == 21
    assert rejected.stderr == ""
    _assert_error_payload(
        _json_stdout(rejected),
        exit_code=21,
        code="ARTIFACT_INVALID_OR_INTEGRITY_FAILURE",
        message="Artifact invalid or integrity failure",
        run_id="run-diverged",
    )
    assert bad_path.read_bytes() == before


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_source",
        "null_run_id",
        "artifact_hash",
        "history_count",
        "boundary_reason",
        "boundary_sequence",
        "boundary_suspension_id",
        "boundary_promise_id",
        "boundary_payload_hash",
        "idempotency_entry_integrity",
    ],
)
def test_resume_artifact_integrity_and_persisted_boundary_fail_closed(
    tmp_path: Path,
    mutation: str,
):
    program = _write(
        tmp_path / f"{mutation}.syn",
        'let approved = suspend await_human_approval("go")\n',
    )
    state_dir = tmp_path / mutation
    state_dir.mkdir()
    pending = _durable(program, state_dir, f"run-{mutation.replace('_', '-')}")
    payload = _json_stdout(pending)
    artifact_path = _artifact_path(payload)
    artifact = _read_artifact(artifact_path)
    active = artifact["active_suspension"]

    if mutation == "missing_source":
        artifact.pop("source")
        _store_artifact(artifact_path, artifact)
    elif mutation == "null_run_id":
        artifact["run_id"] = None
        _store_artifact(artifact_path, artifact)
    elif mutation == "artifact_hash":
        artifact["artifact_hash"] = "sha256:" + "0" * 64
        artifact_path.write_text(json.dumps(artifact, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    elif mutation == "history_count":
        artifact["history_integrity"]["event_count"] += 1
        _store_artifact(artifact_path, artifact)
    elif mutation == "boundary_reason":
        active["reason"] = "awaiting_promise"
        _store_artifact(artifact_path, artifact)
    elif mutation == "boundary_sequence":
        active["sequence"] += 1
        _store_artifact(artifact_path, artifact)
    elif mutation == "boundary_suspension_id":
        active["suspension_id"] = "susp-" + "0" * 64
        _store_artifact(artifact_path, artifact)
    elif mutation == "boundary_promise_id":
        active["promise_id"] = "promise-mutated"
        _store_artifact(artifact_path, artifact)
    elif mutation == "boundary_payload_hash":
        active["payload_hash"] = "sha256:" + "1" * 64
        _store_artifact(artifact_path, artifact)
    elif mutation == "idempotency_entry_integrity":
        artifact["idempotency"]["resolved_suspensions"]["susp-old"] = {
            "signal_hash": _sha256_value(True),
            "committed_revision": 2,
            "committed_status": "COMPLETED",
            "operation_result": {
                "result_schema_version": "1.0.0",
                "status": "ERROR",
                "exit_code": 0,
                "run_id": artifact["run_id"],
                "correlation_id": artifact["correlation_id"],
                "artifact_path": str(artifact_path),
                "artifact_revision": 2,
                "source_hash": artifact["source"]["hash"],
                "history_hash": artifact["history_integrity"]["final_hash"],
                "output_delta": [],
            },
        }
        _store_artifact(artifact_path, artifact)

    before = artifact_path.read_bytes()
    signal = _signal(tmp_path / f"{mutation}-signal.json", True)
    result = _resume(artifact_path, str(payload["suspension_id"]), signal)
    assert result.returncode == 21
    assert result.stderr == ""
    assert _json_stdout(result)["error"]["code"] == "ARTIFACT_INVALID_OR_INTEGRITY_FAILURE"
    assert artifact_path.read_bytes() == before


def test_resume_signal_failure_matrix_is_invalid_input_without_mutation(tmp_path: Path):
    program = _write(
        tmp_path / "signal-matrix.syn",
        'let approved = suspend await_human_approval("go")\n',
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pending = _durable(program, state_dir, "run-signal-matrix")
    payload = _json_stdout(pending)
    artifact_path = _artifact_path(payload)
    suspension_id = str(payload["suspension_id"])
    directory_signal = tmp_path / "signal-dir"
    directory_signal.mkdir()
    invalid_utf8 = tmp_path / "invalid-utf8.json"
    invalid_utf8.write_bytes(b"\xff")
    empty = _write(tmp_path / "empty.json", "")
    trailing = _write(tmp_path / "trailing.json", "true false")
    nan = _write(tmp_path / "nan.json", "NaN")
    infinity = _write(tmp_path / "infinity.json", "Infinity")
    malformed = _write(tmp_path / "malformed.json", "{")
    too_large = tmp_path / "too-large.json"
    too_large.write_text('"' + ("x" * (app._MAX_SIGNAL_BYTES + 1)) + '"', encoding="utf-8")

    cases: list[tuple[Path | str, str | None]] = [
        (tmp_path / "missing.json", None),
        (directory_signal, None),
        (invalid_utf8, None),
        (empty, None),
        (trailing, None),
        (nan, None),
        (infinity, None),
        (malformed, None),
        (too_large, None),
        ("-", ""),
    ]
    for signal_file, stdin in cases:
        before = artifact_path.read_bytes()
        completed = _resume(artifact_path, suspension_id, signal_file, stdin=stdin, timeout=30)
        assert completed.returncode == 2
        assert completed.stderr == ""
        _assert_error_payload(
            _json_stdout(completed),
            exit_code=2,
            code="INVALID_CLI_INPUT",
            message="Invalid durable input",
            run_id="run-signal-matrix",
        )
        assert artifact_path.read_bytes() == before


def test_resume_signal_permission_error_is_invalid_input_before_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    program = _write(
        tmp_path / "signal-permission.syn",
        'let approved = suspend await_human_approval("go")\n',
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pending = _durable(program, state_dir, "run-signal-permission")
    payload = _json_stdout(pending)
    artifact_path = _artifact_path(payload)
    signal_path = _signal(tmp_path / "permission.json", True)
    before = artifact_path.read_bytes()
    original_read_bytes = Path.read_bytes

    def denied(self: Path) -> bytes:
        if self == signal_path:
            raise PermissionError(errno.EACCES, "P2B_SECRET_DO_NOT_LEAK")
        return original_read_bytes(self)

    class NoInterpreter:
        def __init__(self) -> None:
            raise AssertionError("Interpreter must not be created for signal input failure")

    monkeypatch.setattr(Path, "read_bytes", denied)
    monkeypatch.setattr(app, "Interpreter", NoInterpreter)
    result = app.execute_durable_resume(
        app.DurableResumeRequest(
            state_file=artifact_path,
            suspension_id=str(payload["suspension_id"]),
            signal_file=signal_path,
        )
    )
    assert result.exit_code == 2
    assert "P2B_SECRET_DO_NOT_LEAK" not in json.dumps(result.public_payload, sort_keys=True)
    assert artifact_path.read_bytes() == before


def test_resume_state_file_identity_lock_and_filename_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    program = _write(
        tmp_path / "state-file.syn",
        'let approved = suspend await_human_approval("go")\nprint("done")\n',
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pending = _durable(program, state_dir, "run-state-file")
    payload = _json_stdout(pending)
    artifact_path = _artifact_path(payload)
    suspension_id = str(payload["suspension_id"])
    signal = _signal(tmp_path / "signal.json", True)

    missing = _resume(tmp_path / "missing.json", suspension_id, signal)
    assert missing.returncode == 2
    assert missing.stderr == ""
    directory = _resume(tmp_path, suspension_id, signal)
    assert directory.returncode == 2
    assert directory.stderr == ""

    original_is_symlink = Path.is_symlink

    def symlink_for_artifact(self: Path) -> bool:
        if self == artifact_path:
            return True
        return original_is_symlink(self)

    monkeypatch.setattr(Path, "is_symlink", symlink_for_artifact)
    symlink_result = app.execute_durable_resume(
        app.DurableResumeRequest(state_file=artifact_path, suspension_id=suspension_id, signal_file=signal)
    )
    assert symlink_result.exit_code == 2
    monkeypatch.setattr(Path, "is_symlink", original_is_symlink)

    lock_path = artifact_path.with_name(f"{artifact_path.name}.lock")
    lock_path.mkdir()
    locked = _resume(artifact_path, suspension_id, signal)
    assert locked.returncode == 26
    assert locked.stderr == ""
    _assert_error_payload(
        _json_stdout(locked),
        exit_code=26,
        code="ARTIFACT_EXISTS_OR_LOCKED",
        message="Artifact already exists or is locked",
        run_id="run-state-file",
    )
    lock_path.rmdir()

    wrong_path = artifact_path.with_name("wrong.json")
    wrong_path.write_bytes(artifact_path.read_bytes())
    wrong = _resume(wrong_path, suspension_id, signal)
    assert wrong.returncode == 21
    assert _json_stdout(wrong)["error"]["code"] == "ARTIFACT_INVALID_OR_INTEGRITY_FAILURE"


def test_resume_rejects_non_regular_state_file_without_hanging(tmp_path: Path):
    if os.name != "posix" or not hasattr(os, "mkfifo"):
        pytest.skip("POSIX FIFO is not available on this platform")

    fifo_path = tmp_path / "fifo-artifact.json"
    os.mkfifo(fifo_path)
    signal = _signal(tmp_path / "signal.json", True)

    result = _resume(fifo_path, "susp-any", signal, timeout=5)

    assert result.returncode == 2
    assert result.stderr == ""
    _assert_error_payload(
        _json_stdout(result),
        exit_code=2,
        code="INVALID_CLI_INPUT",
        message="Invalid durable input",
    )


def test_resume_concurrent_os_process_race_acquires_lock_exclusively(tmp_path: Path):
    post_signal_lines = 6000
    source = "\n".join(
        [
            'let approved = suspend await_human_approval("race")',
            *(f'print("race-{index}")' for index in range(post_signal_lines)),
            "",
        ]
    )
    program = _write(tmp_path / "resume-race.syn", source)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pending = _durable(program, state_dir, "run-resume-race", stdin=None)
    assert pending.returncode == 20, pending.stderr
    payload = _json_stdout(pending)
    artifact_path = _artifact_path(payload)
    suspension_id = str(payload["suspension_id"])
    signal = _signal(tmp_path / "signal.json", True)
    lock_path = artifact_path.with_name(f"{artifact_path.name}.lock")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [
        sys.executable,
        "-m",
        "synapse",
        "resume",
        "--state-file",
        str(artifact_path),
        "--suspension-id",
        suspension_id,
        "--signal-file",
        str(signal),
    ]

    winner = subprocess.Popen(command, cwd=REPO_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    contender: subprocess.Popen[str] | None = None
    try:
        import time

        deadline = time.monotonic() + 15
        while not lock_path.exists() and winner.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert lock_path.exists(), winner.poll()

        contender = subprocess.Popen(command, cwd=REPO_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        contender_out, contender_err = contender.communicate(timeout=30)
        winner_out, winner_err = winner.communicate(timeout=60)
    except Exception:
        winner.kill()
        if contender is not None:
            contender.kill()
        winner.communicate(timeout=5)
        if contender is not None:
            contender.communicate(timeout=5)
        raise

    results = [
        (winner.returncode, winner_out, winner_err),
        (contender.returncode, contender_out, contender_err),
    ]
    exit_codes = sorted(code for code, _, _ in results)
    assert exit_codes == [0, 26], results
    assert all(stderr == "" for _, _, stderr in results)
    locked_payloads = [_json_stdout(subprocess.CompletedProcess(command, code, out, err)) for code, out, err in results if code == 26]
    assert len(locked_payloads) == 1
    _assert_error_payload(
        locked_payloads[0],
        exit_code=26,
        code="ARTIFACT_EXISTS_OR_LOCKED",
        message="Artifact already exists or is locked",
        run_id="run-resume-race",
    )
    completed_payloads = [json.loads(out) for code, out, _ in results if code == 0]
    assert len(completed_payloads) == 1
    assert completed_payloads[0]["output_delta"][0] == "race-0"
    assert completed_payloads[0]["output_delta"][-1] == f"race-{post_signal_lines - 1}"

    artifact = _read_artifact(artifact_path)
    assert artifact["artifact_hash"] == _sha256_value(_artifact_hash_preimage(artifact))
    assert artifact["status"] == "COMPLETED"
    assert artifact["active_suspension"] is None
    assert artifact["terminal"] == {"status": "COMPLETED", "exit_code": 0}
    resolved = artifact["idempotency"]["resolved_suspensions"]
    assert list(resolved) == [suspension_id]
    assert len(resolved) == 1
    history_types = [event["type"] for event in artifact["replay_state"]["execution_history"]]
    assert history_types.count("promise_created") == 1
    assert history_types.count("promise_resolved") == 1


def _p2c_three_boundary_source(*, initial_output: bool = True) -> str:
    lines = []
    if initial_output:
        lines.append('print("before-1")')
    lines.extend(
        [
            'let step1 = suspend await_human_approval("p2c-step-1")',
            'print("after-1")',
            'let step2 = suspend await_human_approval("p2c-step-2")',
            'print("after-2")',
            'let step3 = suspend await_human_approval("p2c-step-3")',
            'print("after-3")',
            "",
        ]
    )
    return "\n".join(lines)


def test_p2c_three_cycle_campaign_preserves_dense_sequences_history_and_output_delta(tmp_path: Path):
    program = _write(tmp_path / "p2c-three-cycle.syn", _p2c_three_boundary_source())
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    signals = [_signal(tmp_path / f"signal-{index}.json", {"step": index}) for index in (1, 2, 3)]

    initial = _durable(program, state_dir, "run-p2c-three-cycle")
    assert initial.returncode == 20, initial.stderr
    initial_payload = _json_stdout(initial)
    artifact_path = _artifact_path(initial_payload)
    artifact = _assert_artifact_integrity(artifact_path)
    records = [
        (
            artifact["status"],
            artifact["revision"],
            artifact["active_suspension"]["sequence"],
            artifact["active_suspension"]["suspension_id"],
            artifact["active_suspension"]["reason"],
            artifact["artifact_hash"],
            artifact["history_integrity"]["final_hash"],
            initial_payload["output_delta"],
            len(artifact["idempotency"]["resolved_suspensions"]),
        )
    ]

    active_id = str(initial_payload["suspension_id"])
    for index, signal_path in enumerate(signals, start=1):
        expected_exit = 20 if index < 3 else 0
        result = _resume(artifact_path, active_id, signal_path, timeout=30)
        assert result.returncode == expected_exit, result.stderr
        assert result.stderr == ""
        payload = _json_stdout(result)
        artifact = _assert_artifact_integrity(artifact_path)
        active = artifact["active_suspension"]
        records.append(
            (
                artifact["status"],
                artifact["revision"],
                active["sequence"] if active else None,
                active["suspension_id"] if active else None,
                active["reason"] if active else None,
                artifact["artifact_hash"],
                artifact["history_integrity"]["final_hash"],
                payload["output_delta"],
                len(artifact["idempotency"]["resolved_suspensions"]),
            )
        )
        if active:
            active_id = str(active["suspension_id"])

    sequences = [record[2] for record in records if record[2] is not None]
    revisions = [record[1] for record in records]
    suspension_ids = [record[3] for record in records if record[3] is not None]
    output_deltas = [record[7] for record in records]
    resolved_counts = [record[8] for record in records]

    assert sequences == [1, 2, 3]
    assert revisions == [1, 2, 3, 4]
    assert len(set(suspension_ids)) == 3
    assert output_deltas == [["before-1"], ["after-1"], ["after-2"], ["after-3"]]
    assert resolved_counts == [0, 1, 2, 3]
    assert records[-1][0] == "COMPLETED"
    assert artifact["artifact_schema_version"] == "1.0.0"
    assert artifact["terminal"] == {"status": "COMPLETED", "exit_code": 0}
    assert artifact["active_suspension"] is None


def test_p2c_duplicate_stale_and_resume_argv_matrix_across_cycles(tmp_path: Path):
    program = _write(tmp_path / "p2c-duplicates.syn", _p2c_three_boundary_source(initial_output=False))
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    signal_1 = _signal(tmp_path / "signal-1.json", "step1")
    signal_1_conflict = _signal(tmp_path / "signal-1-conflict.json", "step1-conflict")
    signal_2 = _signal(tmp_path / "signal-2.json", "step2")
    signal_2_conflict = _signal(tmp_path / "signal-2-conflict.json", "step2-conflict")
    signal_3 = _signal(tmp_path / "signal-3.json", "step3")
    signal_3_conflict = _signal(tmp_path / "signal-3-conflict.json", "step3-conflict")

    initial = _durable(program, state_dir, "run-p2c-duplicates")
    assert initial.returncode == 20, initial.stderr
    payload_1 = _json_stdout(initial)
    artifact_path = _artifact_path(payload_1)
    suspension_1 = str(payload_1["suspension_id"])

    pending_2 = _resume(artifact_path, suspension_1, signal_1)
    assert pending_2.returncode == 20, pending_2.stderr
    payload_2 = _json_stdout(pending_2)
    artifact_2 = _assert_artifact_integrity(artifact_path)
    suspension_2 = str(payload_2["suspension_id"])
    entry_1 = artifact_2["idempotency"]["resolved_suspensions"][suspension_1]
    assert entry_1["operation_result"]["artifact_path"] == str(artifact_path)
    assert "resume_argv" not in entry_1["operation_result"]
    assert sys.executable not in json.dumps(entry_1["operation_result"], sort_keys=True)

    before = _artifact_snapshot(artifact_path)
    duplicate_after_one = _resume(artifact_path, suspension_1, signal_1)
    assert duplicate_after_one.returncode == 20
    duplicate_after_one_payload = _json_stdout(duplicate_after_one)
    _assert_artifact_unchanged(artifact_path, before)
    assert duplicate_after_one_payload["artifact_revision"] == 2
    assert duplicate_after_one_payload["output_delta"] == ["after-1"]
    assert duplicate_after_one_payload["resume_argv"] == [
        sys.executable,
        "-m",
        "synapse",
        "resume",
        "--state-file",
        str(artifact_path),
        "--suspension-id",
        suspension_2,
        "--signal-file",
        "<path|->",
    ]

    pending_3 = _resume(artifact_path, suspension_2, signal_2)
    assert pending_3.returncode == 20, pending_3.stderr
    payload_3 = _json_stdout(pending_3)
    artifact_3 = _assert_artifact_integrity(artifact_path)
    suspension_3 = str(payload_3["suspension_id"])
    assert artifact_3["revision"] == 3
    assert artifact_3["active_suspension"]["sequence"] == 3

    for old_id, original_signal in [(suspension_1, signal_1), (suspension_2, signal_2)]:
        before = _artifact_snapshot(artifact_path)
        duplicate = _resume(artifact_path, old_id, original_signal)
        assert duplicate.returncode == 20
        _assert_artifact_unchanged(artifact_path, before)

    before = _artifact_snapshot(artifact_path)
    prior_conflict = _resume(artifact_path, suspension_1, signal_1_conflict)
    assert prior_conflict.returncode == 24
    assert _json_stdout(prior_conflict)["error"]["code"] == "RESOLUTION_CONFLICT"
    _assert_artifact_unchanged(artifact_path, before)

    before = _artifact_snapshot(artifact_path)
    unknown = _resume(artifact_path, "susp-never-issued-p2c", signal_1)
    assert unknown.returncode == 23
    assert _json_stdout(unknown)["error"]["code"] == "STALE_OR_UNKNOWN_SUSPENSION"
    _assert_artifact_unchanged(artifact_path, before)

    completed = _resume(artifact_path, suspension_3, signal_3)
    assert completed.returncode == 0, completed.stderr
    completed_payload = _json_stdout(completed)
    completed_artifact = _assert_artifact_integrity(artifact_path)
    assert completed_payload["output_delta"] == ["after-3"]
    assert completed_artifact["status"] == "COMPLETED"
    assert len(completed_artifact["idempotency"]["resolved_suspensions"]) == 3

    before = _artifact_snapshot(artifact_path)
    current_same_hash = _resume(artifact_path, suspension_3, signal_3)
    assert current_same_hash.returncode == 0
    assert _json_stdout(current_same_hash)["status"] == "COMPLETED"
    _assert_artifact_unchanged(artifact_path, before)

    for resolved_id, conflict_signal in [(suspension_2, signal_2_conflict), (suspension_3, signal_3_conflict)]:
        before = _artifact_snapshot(artifact_path)
        conflict = _resume(artifact_path, resolved_id, conflict_signal)
        assert conflict.returncode == 24
        assert _json_stdout(conflict)["error"]["code"] == "RESOLUTION_CONFLICT"
        _assert_artifact_unchanged(artifact_path, before)


def test_p2c_mixed_reason_campaign_uses_public_runtime_reasons(tmp_path: Path):
    source = "\n".join(
        [
            'let external = suspend await_human_approval("external")',
            'print("after-external")',
            'agent Analyst { model "mock" }',
            "let analyst_proc = spawn Analyst()",
            'analyst_proc => queue_task("job-42")',
            "let result = await analyst_proc.get_response()",
            'print("after-promise")',
            "",
        ]
    )
    program = _write(tmp_path / "p2c-mixed-reason.syn", source)
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    initial = _durable(program, state_dir, "run-p2c-mixed-reason")
    assert initial.returncode == 20, initial.stderr
    initial_payload = _json_stdout(initial)
    artifact_path = _artifact_path(initial_payload)
    artifact = _assert_artifact_integrity(artifact_path)
    first_reason = artifact["active_suspension"]["reason"]

    pending_promise = _resume(artifact_path, str(initial_payload["suspension_id"]), _signal(tmp_path / "external.json", True))
    assert pending_promise.returncode == 20, pending_promise.stderr
    promise_payload = _json_stdout(pending_promise)
    artifact = _assert_artifact_integrity(artifact_path)
    second_reason = artifact["active_suspension"]["reason"]

    completed = _resume(artifact_path, str(promise_payload["suspension_id"]), _signal(tmp_path / "promise.json", "ready"))
    assert completed.returncode == 0, completed.stderr
    completed_payload = _json_stdout(completed)
    artifact = _assert_artifact_integrity(artifact_path)

    assert [first_reason, second_reason] == ["awaiting_external_signal", "awaiting_promise"]
    assert completed_payload["output_delta"] == ["after-promise"]
    assert artifact["status"] == "COMPLETED"
    assert artifact["artifact_schema_version"] == "1.0.0"


def test_p2c_malformed_resolved_entry_with_recomputed_artifact_hash_is_integrity_failure(tmp_path: Path):
    program = _write(tmp_path / "p2c-malformed-idempotency.syn", _p2c_three_boundary_source(initial_output=False))
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    initial = _durable(program, state_dir, "run-p2c-malformed-idempotency")
    assert initial.returncode == 20, initial.stderr
    initial_payload = _json_stdout(initial)
    valid_path = _artifact_path(initial_payload)
    suspension_1 = str(initial_payload["suspension_id"])
    first_signal = _signal(tmp_path / "signal-1.json", "step1")
    pending_2 = _resume(valid_path, suspension_1, first_signal)
    assert pending_2.returncode == 20, pending_2.stderr
    valid_artifact = _assert_artifact_integrity(valid_path)

    corrupt_without_hash = copy.deepcopy(_artifact_hash_preimage(valid_artifact))
    corrupt_entry = corrupt_without_hash["idempotency"]["resolved_suspensions"][suspension_1]
    assert corrupt_entry["committed_status"] == "PENDING"
    corrupt_entry["operation_result"]["status"] = "COMPLETED"
    corrupt_artifact = app._artifact_with_hash(corrupt_without_hash)
    corrupt_dir = tmp_path / "corrupt"
    corrupt_dir.mkdir()
    corrupt_path = corrupt_dir / f"{corrupt_artifact['run_id']}.json"
    corrupt_path.write_bytes(app._strict_canonical_bytes(corrupt_artifact))

    before = _artifact_snapshot(corrupt_path)
    rejected = _resume(corrupt_path, suspension_1, first_signal)
    assert rejected.returncode == 21
    assert _json_stdout(rejected)["error"]["code"] == "ARTIFACT_INVALID_OR_INTEGRITY_FAILURE"
    _assert_artifact_unchanged(corrupt_path, before)


def _p2c_make_late_boundary_artifact(tmp_path: Path, run_id: str) -> tuple[Path, str, dict[str, object]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    post_signal_lines = 6000
    source = "\n".join(
        [
            'let first = suspend await_human_approval("first")',
            'print("after-first")',
            'let second = suspend await_human_approval("second")',
            *(f'print("race-{index}")' for index in range(post_signal_lines)),
            "",
        ]
    )
    program = _write(tmp_path / f"{run_id}.syn", source)
    state_dir = tmp_path / f"{run_id}-state"
    state_dir.mkdir()
    initial = _durable(program, state_dir, run_id)
    assert initial.returncode == 20, initial.stderr
    initial_payload = _json_stdout(initial)
    artifact_path = _artifact_path(initial_payload)
    first_resume = _resume(artifact_path, str(initial_payload["suspension_id"]), _signal(tmp_path / f"{run_id}-first.json", "first"))
    assert first_resume.returncode == 20, first_resume.stderr
    first_resume_payload = _json_stdout(first_resume)
    artifact = _assert_artifact_integrity(artifact_path)
    assert artifact["revision"] == 2
    assert artifact["active_suspension"]["sequence"] == 2
    return artifact_path, str(first_resume_payload["suspension_id"]), artifact


def _p2c_spawn_resume_process(artifact_path: Path, suspension_id: str, signal_path: Path) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "synapse",
            "resume",
            "--state-file",
            str(artifact_path),
            "--suspension-id",
            suspension_id,
            "--signal-file",
            str(signal_path),
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def _p2c_forced_overlap_resume_race(
    artifact_path: Path,
    suspension_id: str,
    first_signal: Path,
    second_signal: Path,
) -> list[tuple[int, str, str]]:
    lock_path = artifact_path.with_name(f"{artifact_path.name}.lock")
    first = _p2c_spawn_resume_process(artifact_path, suspension_id, first_signal)
    second: subprocess.Popen[str] | None = None
    try:
        deadline = time.monotonic() + 15
        while not lock_path.exists() and first.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert lock_path.exists(), first.poll()
        second = _p2c_spawn_resume_process(artifact_path, suspension_id, second_signal)
        second_out, second_err = second.communicate(timeout=60)
        first_out, first_err = first.communicate(timeout=90)
    except Exception:
        first.kill()
        if second is not None:
            second.kill()
        first.communicate(timeout=5)
        if second is not None:
            second.communicate(timeout=5)
        raise
    return [(first.returncode, first_out, first_err), (second.returncode, second_out, second_err)]


def test_p2c_late_boundary_process_races_same_and_different_hashes(tmp_path: Path):
    same_path, same_id, same_before_artifact = _p2c_make_late_boundary_artifact(tmp_path / "same", "run-p2c-race-same")
    same_signal = _signal(tmp_path / "same" / "same.json", {"winner": "same"})
    same_before = _artifact_snapshot(same_path)
    same_results = _p2c_forced_overlap_resume_race(same_path, same_id, same_signal, same_signal)
    assert sorted(code for code, _, _ in same_results) == [0, 26], same_results
    assert all(stderr == "" for _, _, stderr in same_results)
    same_after = _assert_artifact_integrity(same_path)
    assert same_before_artifact["active_suspension"]["sequence"] == 2
    assert same_after["revision"] == same_before["revision"] + 1
    assert same_after["status"] == "COMPLETED"
    assert same_after["terminal"] == {"status": "COMPLETED", "exit_code": 0}
    same_resolved = same_after["idempotency"]["resolved_suspensions"]
    assert len(same_resolved) == 2
    assert set(same_before_artifact["idempotency"]["resolved_suspensions"]).issubset(same_resolved)
    assert same_id in same_resolved

    before_duplicate = _artifact_snapshot(same_path)
    duplicate = _resume(same_path, same_id, same_signal)
    assert duplicate.returncode == 0
    assert _json_stdout(duplicate)["status"] == "COMPLETED"
    _assert_artifact_unchanged(same_path, before_duplicate)

    diff_path, diff_id, diff_before_artifact = _p2c_make_late_boundary_artifact(tmp_path / "different", "run-p2c-race-diff")
    winner_signal = _signal(tmp_path / "different" / "winner.json", {"winner": "A"})
    loser_signal = _signal(tmp_path / "different" / "loser.json", {"winner": "B"})
    diff_before = _artifact_snapshot(diff_path)
    diff_results = _p2c_forced_overlap_resume_race(diff_path, diff_id, winner_signal, loser_signal)
    assert sorted(code for code, _, _ in diff_results) == [0, 26], diff_results
    assert all(stderr == "" for _, _, stderr in diff_results)
    diff_after = _assert_artifact_integrity(diff_path)
    winner_hash = app._read_signal_value(app.DurableResumeRequest(state_file=diff_path, suspension_id=diff_id, signal_file=winner_signal), None)[1]
    loser_hash = app._read_signal_value(app.DurableResumeRequest(state_file=diff_path, suspension_id=diff_id, signal_file=loser_signal), None)[1]
    resolved_entry = diff_after["idempotency"]["resolved_suspensions"][diff_id]
    assert diff_before_artifact["active_suspension"]["sequence"] == 2
    assert diff_after["revision"] == diff_before["revision"] + 1
    assert resolved_entry["signal_hash"] == winner_hash
    assert loser_hash not in json.dumps(diff_after, sort_keys=True)
    assert diff_after["status"] == "COMPLETED"

    before_conflict = _artifact_snapshot(diff_path)
    conflict = _resume(diff_path, diff_id, loser_signal)
    assert conflict.returncode == 24
    assert _json_stdout(conflict)["error"]["code"] == "RESOLUTION_CONFLICT"
    _assert_artifact_unchanged(diff_path, before_conflict)


def test_p2c_p2a_and_p2b_artifact_compatibility_without_schema_migration(tmp_path: Path):
    p2a_program = _write(tmp_path / "p2a-style.syn", 'let approved = suspend await_human_approval("p2a")\nprint("p2a-done")\n')
    p2a_state = tmp_path / "p2a-state"
    p2a_state.mkdir()
    p2a_initial = _durable(p2a_program, p2a_state, "run-p2a-style")
    assert p2a_initial.returncode == 20, p2a_initial.stderr
    p2a_payload = _json_stdout(p2a_initial)
    p2a_path = _artifact_path(p2a_payload)
    p2a_artifact = _assert_artifact_integrity(p2a_path)
    assert p2a_artifact["artifact_schema_version"] == "1.0.0"
    assert p2a_artifact["revision"] == 1
    assert p2a_artifact["status"] == "PENDING"
    assert p2a_artifact["idempotency"]["resolved_suspensions"] == {}
    assert p2a_artifact["active_suspension"]["sequence"] == 1
    p2a_completed = _resume(p2a_path, str(p2a_payload["suspension_id"]), _signal(tmp_path / "p2a-signal.json", True))
    assert p2a_completed.returncode == 0, p2a_completed.stderr
    assert _assert_artifact_integrity(p2a_path)["artifact_schema_version"] == "1.0.0"

    p2b_program = _write(tmp_path / "p2b-style.syn", _p2c_three_boundary_source(initial_output=False))
    p2b_state = tmp_path / "p2b-state"
    p2b_state.mkdir()
    p2b_initial = _durable(p2b_program, p2b_state, "run-p2b-style")
    assert p2b_initial.returncode == 20, p2b_initial.stderr
    p2b_payload_1 = _json_stdout(p2b_initial)
    p2b_path = _artifact_path(p2b_payload_1)
    p2b_signal_1 = _signal(tmp_path / "p2b-signal-1.json", "one")
    p2b_signal_2 = _signal(tmp_path / "p2b-signal-2.json", "two")
    p2b_signal_3 = _signal(tmp_path / "p2b-signal-3.json", "three")
    p2b_conflict = _signal(tmp_path / "p2b-conflict.json", "conflict")

    p2b_payload_2 = _json_stdout(_resume(p2b_path, str(p2b_payload_1["suspension_id"]), p2b_signal_1))
    p2b_one_resolved = _assert_artifact_integrity(p2b_path)
    assert p2b_one_resolved["status"] == "PENDING"
    assert len(p2b_one_resolved["idempotency"]["resolved_suspensions"]) == 1
    before = _artifact_snapshot(p2b_path)
    assert _resume(p2b_path, str(p2b_payload_1["suspension_id"]), p2b_signal_1).returncode == 20
    _assert_artifact_unchanged(p2b_path, before)
    before = _artifact_snapshot(p2b_path)
    assert _resume(p2b_path, "susp-p2b-unknown", p2b_signal_1).returncode == 23
    _assert_artifact_unchanged(p2b_path, before)

    p2b_payload_3 = _json_stdout(_resume(p2b_path, str(p2b_payload_2["suspension_id"]), p2b_signal_2))
    p2b_two_resolved = _assert_artifact_integrity(p2b_path)
    assert p2b_two_resolved["status"] == "PENDING"
    assert len(p2b_two_resolved["idempotency"]["resolved_suspensions"]) == 2
    before = _artifact_snapshot(p2b_path)
    assert _resume(p2b_path, str(p2b_payload_2["suspension_id"]), p2b_conflict).returncode == 24
    _assert_artifact_unchanged(p2b_path, before)

    p2b_completed = _resume(p2b_path, str(p2b_payload_3["suspension_id"]), p2b_signal_3)
    assert p2b_completed.returncode == 0, p2b_completed.stderr
    p2b_terminal = _assert_artifact_integrity(p2b_path)
    assert p2b_terminal["status"] == "COMPLETED"
    assert p2b_terminal["artifact_schema_version"] == "1.0.0"
    assert len(p2b_terminal["idempotency"]["resolved_suspensions"]) == 3

    error_program = _write(tmp_path / "p2b-error-terminal.syn", 'let approved = suspend await_human_approval("err")\nlet x = 1 / 0\n')
    error_state = tmp_path / "error-state"
    error_state.mkdir()
    error_initial = _durable(error_program, error_state, "run-p2b-error-terminal")
    assert error_initial.returncode == 20, error_initial.stderr
    error_payload = _json_stdout(error_initial)
    error_path = _artifact_path(error_payload)
    error_signal = _signal(tmp_path / "error-signal.json", True)
    error_completed = _resume(error_path, str(error_payload["suspension_id"]), error_signal)
    assert error_completed.returncode == 1
    error_artifact = _assert_artifact_integrity(error_path)
    assert error_artifact["status"] == "ERROR"
    assert len(error_artifact["idempotency"]["resolved_suspensions"]) == 1
    before = _artifact_snapshot(error_path)
    assert _resume(error_path, str(error_payload["suspension_id"]), error_signal).returncode == 1
    _assert_artifact_unchanged(error_path, before)
    before = _artifact_snapshot(error_path)
    assert _resume(error_path, str(error_payload["suspension_id"]), _signal(tmp_path / "error-conflict.json", False)).returncode == 24
    _assert_artifact_unchanged(error_path, before)
    before = _artifact_snapshot(error_path)
    assert _resume(error_path, "susp-error-unknown", error_signal).returncode == 23
    _assert_artifact_unchanged(error_path, before)


def test_resume_public_error_symbols_and_stale_terminal_without_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    program = _write(
        tmp_path / "symbols.syn",
        'let approved = suspend await_human_approval("go")\n',
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pending = _durable(program, state_dir, "run-symbols")
    payload = _json_stdout(pending)
    artifact_path = _artifact_path(payload)
    suspension_id = str(payload["suspension_id"])
    signal = _signal(tmp_path / "signal.json", True)

    stale = _resume(artifact_path, "susp-unknown", signal)
    assert stale.returncode == 23
    assert _json_stdout(stale)["error"]["code"] == "STALE_OR_UNKNOWN_SUSPENSION"

    def mismatch(_: dict[str, object]) -> tuple[object, object, object]:
        raise app._ArtifactIntegrityError("forced mismatch")

    monkeypatch.setattr(app, "_reconstruct_boundary", mismatch)
    boundary = app.execute_durable_resume(
        app.DurableResumeRequest(state_file=artifact_path, suspension_id=suspension_id, signal_file=signal)
    )
    assert boundary.exit_code == 22
    assert boundary.public_payload["error"]["code"] == "RESUME_BOUNDARY_MISMATCH"
    monkeypatch.undo()

    completed = _resume(artifact_path, suspension_id, signal)
    assert completed.returncode == 0
    terminal_unknown = _resume(artifact_path, "susp-unknown", signal)
    assert terminal_unknown.returncode == 23
    assert _json_stdout(terminal_unknown)["error"]["code"] == "STALE_OR_UNKNOWN_SUSPENSION"


def test_resume_lock_release_failure_reports_stale_lock_and_blocks_next_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    program = _write(
        tmp_path / "stale-lock.syn",
        'let approved = suspend await_human_approval("go")\nprint("done")\n',
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pending = _durable(program, state_dir, "run-resume-stale-lock")
    payload = _json_stdout(pending)
    artifact_path = _artifact_path(payload)
    signal = _signal(tmp_path / "signal.json", True)
    lock_path = artifact_path.with_name(f"{artifact_path.name}.lock")

    def fail_remove(path: Path) -> None:
        assert path == lock_path
        raise PermissionError(errno.EACCES, "P2B_SECRET_DO_NOT_LEAK")

    monkeypatch.setattr(app, "_remove_lock_directory", fail_remove)
    result = app.execute_durable_resume(
        app.DurableResumeRequest(
            state_file=artifact_path,
            suspension_id=str(payload["suspension_id"]),
            signal_file=signal,
        )
    )
    assert result.exit_code == 0
    assert result.public_payload["status"] == "COMPLETED"
    assert result.diagnostics == ("STALE_LOCK_AFTER_COMMIT",)
    assert lock_path.exists()

    blocked = app.execute_durable_resume(
        app.DurableResumeRequest(
            state_file=artifact_path,
            suspension_id=str(payload["suspension_id"]),
            signal_file=signal,
        )
    )
    assert blocked.exit_code == 26


def test_resume_atomic_commit_fault_preserves_previous_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    program = _write(
        tmp_path / "atomic.syn",
        'let approved = suspend await_human_approval("go")\nprint("done")\n',
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pending = _durable(program, state_dir, "run-resume-atomic")
    payload = _json_stdout(pending)
    artifact_path = _artifact_path(payload)
    signal = _signal(tmp_path / "signal.json", True)
    before = artifact_path.read_bytes()

    def interrupted(path: Path, artifact: dict[str, object]) -> None:
        path.with_name("." + path.name + ".resume-fault.tmp").write_text("{", encoding="utf-8")
        raise OSError(errno.EIO, "P2B_SECRET_DO_NOT_LEAK")

    monkeypatch.setattr(app, "_atomic_commit_json", interrupted)
    result = app.execute_durable_resume(
        app.DurableResumeRequest(
            state_file=artifact_path,
            suspension_id=str(payload["suspension_id"]),
            signal_file=signal,
        )
    )
    assert result.exit_code == 1
    _assert_error_payload(
        result.public_payload,
        exit_code=1,
        code="RUNTIME_EXECUTION_ERROR",
        message="Runtime execution failed",
        run_id="run-resume-atomic",
    )
    assert "P2B_SECRET_DO_NOT_LEAK" not in json.dumps(result.public_payload, sort_keys=True)
    assert artifact_path.read_bytes() == before
    assert not artifact_path.with_name(f"{artifact_path.name}.lock").exists()


def test_resume_unexpected_generator_yield_after_injection_commits_error_without_new_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    program = _write(
        tmp_path / "unexpected-yield.syn",
        'let approved = suspend await_human_approval("go")\nprint("done")\n',
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    pending = _durable(program, state_dir, "run-unexpected-yield")
    payload = _json_stdout(pending)
    artifact_path = _artifact_path(payload)
    signal = _signal(tmp_path / "signal.json", True)
    original = app._reconstruct_boundary

    class BadFlow:
        def send(self, value: object) -> object:
            return {"unexpected": value}

    def fake_reconstruct(artifact: dict[str, object]) -> tuple[object, object, object]:
        interpreter, _flow, yielded = original(artifact)
        return interpreter, BadFlow(), yielded

    monkeypatch.setattr(app, "_reconstruct_boundary", fake_reconstruct)
    result = app.execute_durable_resume(
        app.DurableResumeRequest(
            state_file=artifact_path,
            suspension_id=str(payload["suspension_id"]),
            signal_file=signal,
        )
    )
    artifact = _read_artifact(artifact_path)
    assert result.exit_code == 1
    assert result.public_payload["error"]["code"] == "RUNTIME_EXECUTION_ERROR"
    assert artifact["status"] == "ERROR"
    assert artifact["terminal"]["exit_code"] == 1


def test_resume_replays_actor_and_side_effect_history_without_duplicate_effects(tmp_path: Path):
    actor_state = tmp_path / "actor-state"
    actor_state.mkdir()
    actor_pending = _durable(REPO_ROOT / "examples" / "durable_promise.syn", actor_state, "run-actor-resume")
    actor_payload = _json_stdout(actor_pending)
    actor_path = _artifact_path(actor_payload)
    actor_initial = _read_artifact(actor_path)
    assert [event["type"] for event in actor_initial["replay_state"]["execution_history"]] == [
        "actor_spawned",
        "message_sent",
    ]

    actor_completed = _resume(actor_path, str(actor_payload["suspension_id"]), _signal(tmp_path / "actor-signal.json", "ready"))
    actor_artifact = _read_artifact(actor_path)
    actor_types = [event["type"] for event in actor_artifact["replay_state"]["execution_history"]]
    assert actor_completed.returncode == 0, actor_completed.stderr
    assert actor_types.count("actor_spawned") == 1
    assert actor_types.count("message_sent") == 1
    assert actor_types.count("promise_resolved") == 1

    side_program = _write(
        tmp_path / "side-effects.syn",
        "\n".join(
            [
                "let started_at = time()",
                "let chance = random()",
                "let ident = uuid()",
                'let approved = suspend await_human_approval("go")',
                "print(started_at)",
                "print(chance)",
                "print(ident)",
                "",
            ]
        ),
    )
    side_state = tmp_path / "side-state"
    side_state.mkdir()
    side_pending = _durable(side_program, side_state, "run-side-resume")
    side_payload = _json_stdout(side_pending)
    side_path = _artifact_path(side_payload)
    side_initial = _read_artifact(side_path)
    initial_side_names = [
        event["name"]
        for event in side_initial["replay_state"]["execution_history"]
        if event["type"] == "side_effect"
    ]
    assert initial_side_names == ["time", "random", "uuid"]

    side_completed = _resume(side_path, str(side_payload["suspension_id"]), _signal(tmp_path / "side-signal.json", True))
    side_artifact = _read_artifact(side_path)
    final_side_names = [
        event["name"]
        for event in side_artifact["replay_state"]["execution_history"]
        if event["type"] == "side_effect"
    ]
    assert side_completed.returncode == 0, side_completed.stderr
    assert final_side_names == ["time", "random", "uuid"]
    assert len(_json_stdout(side_completed)["output_delta"]) == 3


def test_durable_examples_for_promise_cross_node_and_llm_boundaries(tmp_path: Path):
    promise_state = tmp_path / "promise-state"
    promise_state.mkdir()
    promise = _durable(REPO_ROOT / "examples" / "durable_promise.syn", promise_state, "run-promise")
    promise_payload = _json_stdout(promise)
    promise_artifact = _load_artifact(promise_payload)
    assert promise.returncode == 20
    assert promise_artifact["active_suspension"]["reason"] == "awaiting_promise"
    assert promise_artifact["active_suspension"]["promise_id"].startswith("await:Analyst#")
    history_types = [event["type"] for event in promise_artifact["replay_state"]["execution_history"]]
    assert history_types == ["actor_spawned", "message_sent"]

    input_file = _write(tmp_path / "input.json", '{"promise_token":"remote-job-42"}')
    cross_state = tmp_path / "cross-state"
    cross_state.mkdir()
    cross = _durable(
        REPO_ROOT / "examples" / "cross_node_promise.syn",
        cross_state,
        "run-cross",
        "--input-file",
        str(input_file),
    )
    cross_payload = _json_stdout(cross)
    assert cross.returncode == 20
    assert cross_payload["promise_id"] == "await:remote-job-42"

    llm_program = _write(tmp_path / "llm.syn", 'let p = prompt "hello"\nlet answer = llm(p)\nprint(answer)\n')
    llm_state = tmp_path / "llm-state"
    llm_state.mkdir()
    llm = _durable(llm_program, llm_state, "run-llm")
    llm_artifact = _load_artifact(_json_stdout(llm))
    assert llm.returncode == 20
    assert llm_artifact["active_suspension"]["reason"] == "awaiting_llm"
    assert llm_artifact["replay_state"]["execution_history"] == []
    assert llm_artifact["replay_state"]["llm_context_cache"] == {}
    assert "prompt" not in llm_artifact["active_suspension"]


def test_suspension_payload_hash_uses_full_runtime_payload_without_public_raw_request(tmp_path: Path):
    program = _write(tmp_path / "external.syn", "let approved = suspend await_human_approval(payload_value)\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    first_request = "request-value-one"
    second_request = "request-value-two"
    input_a = _write(tmp_path / "a.json", json.dumps({"payload_value": first_request}))
    input_b = _write(tmp_path / "b.json", json.dumps({"payload_value": second_request}))

    first = _durable(program, state_dir, "run-one", "--input-file", str(input_a))
    second = _durable(program, state_dir, "run-two", "--input-file", str(input_b))

    assert first.returncode == 20
    assert second.returncode == 20
    first_payload = _json_stdout(first)
    second_payload = _json_stdout(second)
    first_artifact = _load_artifact(first_payload)
    second_artifact = _load_artifact(second_payload)
    first_active = first_artifact["active_suspension"]
    second_active = second_artifact["active_suspension"]

    expected_first_payload = {
        "promise_id": first_active["promise_id"],
        "request": {"call": "await_human_approval", "args": [first_request]},
    }
    expected_second_payload = {
        "promise_id": second_active["promise_id"],
        "request": {"call": "await_human_approval", "args": [second_request]},
    }
    assert first_active["payload_hash"] == _sha256_value(expected_first_payload)
    assert second_active["payload_hash"] == _sha256_value(expected_second_payload)
    assert first_active["payload_hash"] != second_active["payload_hash"]
    assert first_active["payload_hash"] != _sha256_value({"promise_id": first_active["promise_id"]})
    assert first_request not in json.dumps(first_active, sort_keys=True)
    assert first_request not in json.dumps(first_payload, sort_keys=True)


def test_llm_payload_hash_uses_full_prompt_without_public_raw_prompt(tmp_path: Path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    first_prompt = "raw-prompt-one"
    second_prompt = "raw-prompt-two"
    first_program = _write(tmp_path / "first.syn", f'let answer = llm("{first_prompt}")\n')
    second_program = _write(tmp_path / "second.syn", f'let answer = llm("{second_prompt}")\n')

    hello_result = _durable(first_program, state_dir, "run-first")
    goodbye_result = _durable(second_program, state_dir, "run-second")

    assert hello_result.returncode == 20
    assert goodbye_result.returncode == 20
    hello_payload = _json_stdout(hello_result)
    goodbye_payload = _json_stdout(goodbye_result)
    hello_active = _load_artifact(hello_payload)["active_suspension"]
    goodbye_active = _load_artifact(goodbye_payload)["active_suspension"]
    assert hello_active["payload_hash"] == _sha256_value({"prompt": first_prompt, "model": None})
    assert goodbye_active["payload_hash"] == _sha256_value({"prompt": second_prompt, "model": None})
    assert hello_active["payload_hash"] != goodbye_active["payload_hash"]
    assert hello_active["payload_hash"] != _sha256_value({"model": None})
    assert first_prompt not in json.dumps(hello_active, sort_keys=True)
    assert first_prompt not in json.dumps(hello_payload, sort_keys=True)


def test_cli_validation_and_json_channel_separation(tmp_path: Path):
    program = _write(tmp_path / "sync.syn", 'print("sync")\n')
    sync = _run(["run", str(program)])
    assert sync.returncode == 0
    assert sync.stdout == "sync\n"

    bad_flag = _run(["run", str(program), "--state-dir", str(tmp_path)])
    assert bad_flag.returncode == 2
    missing_state = _run(["run", str(program), "--durable"])
    assert missing_state.returncode == 2
    assert missing_state.stderr == ""
    _assert_error_payload(
        _json_stdout(missing_state),
        exit_code=2,
        code="INVALID_CLI_INPUT",
        message="Invalid durable input",
    )
    missing_dir = _run(["run", str(program), "--durable", "--state-dir", str(tmp_path / "missing")])
    assert missing_dir.returncode == 2
    assert missing_dir.stderr == ""
    missing_dir_payload = _json_stdout(missing_dir)
    missing_dir_run_id = _assert_generated_run_id(missing_dir_payload)
    _assert_error_payload(
        missing_dir_payload,
        exit_code=2,
        code="INVALID_CLI_INPUT",
        message="Invalid state directory",
        run_id=missing_dir_run_id,
    )
    state_file = _write(tmp_path / "state-file", "")
    state_is_file = _run(["run", str(program), "--durable", "--state-dir", str(state_file)])
    assert state_is_file.returncode == 2
    assert state_is_file.stderr == ""
    state_file_payload = _json_stdout(state_is_file)
    state_file_run_id = _assert_generated_run_id(state_file_payload)
    _assert_error_payload(
        state_file_payload,
        exit_code=2,
        code="INVALID_CLI_INPUT",
        message="Invalid state directory",
        run_id=state_file_run_id,
    )

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    durable = _durable(program, state_dir, "run-json")
    assert durable.returncode == 0
    assert durable.stderr == ""
    assert durable.stdout.count("\n") == 1
    assert _json_stdout(durable)["output_delta"] == ["sync"]


def test_invalid_source_parse_is_structured_invalid_input_without_artifact(tmp_path: Path):
    program = _write(tmp_path / "invalid.syn", "let =\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    completed = _durable(program, state_dir, "run-invalid-source", "--correlation-id", "corr-invalid-source")

    assert completed.returncode == 2
    assert completed.stderr == ""
    _assert_error_payload(
        _json_stdout(completed),
        exit_code=2,
        code="INVALID_CLI_INPUT",
        message="Invalid durable input",
        run_id="run-invalid-source",
        correlation_id="corr-invalid-source",
    )
    assert not (state_dir / "run-invalid-source.json").exists()


@pytest.mark.parametrize("binding", ["let", "print", "medium", "__synapse_private", "owned", "assigned", "AgentName"])
def test_initial_binding_collisions_are_rejected_without_artifact(tmp_path: Path, binding: str):
    program = _write(tmp_path / "bindings.syn", "agent AgentName { model \"mock\" }\nlet owned = 1\nlet assigned = 0\nassigned = 2\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    input_file = tmp_path / f"{binding}.json"
    input_file.write_text(json.dumps({binding: 1}), encoding="utf-8")
    run_id = f"run-input-{binding.replace('_', 'x')}"
    result = app.execute_durable_run(
        app.DurableRunRequest(source_path=program, state_dir=state_dir, run_id=run_id, input_file=input_file),
        stdin=None,
    )
    assert result.exit_code in {2, 25}
    assert result.public_payload["run_id"] == run_id
    assert result.public_payload["correlation_id"] is None
    assert not (state_dir / f"{run_id}.json").exists()


def test_stdin_input_binding_is_accepted_for_cross_node_promise(tmp_path: Path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    completed = _durable(
        REPO_ROOT / "examples" / "cross_node_promise.syn",
        state_dir,
        "run-stdin",
        "--input-file",
        "-",
        stdin='{"promise_token":"remote-job-42"}',
    )
    assert completed.returncode == 20
    assert _json_stdout(completed)["promise_id"] == "await:remote-job-42"


@pytest.mark.parametrize(
    "source",
    [
        'agent A { model "mock" }\nmemory.write("x") { reason "nope" }\n',
        'let x = "abc"\nlet y = x.upper()\n',
    ],
)
def test_validator_rejects_unsupported_operations_before_artifact(tmp_path: Path, source: str):
    program = _write(tmp_path / "bad.syn", source)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    completed = _durable(program, state_dir, "run-bad", "--correlation-id", "corr-validator")
    assert completed.returncode == 25
    assert completed.stderr == ""
    _assert_error_payload(
        _json_stdout(completed),
        exit_code=25,
        code="UNSUPPORTED_DURABLE_OPERATION_OR_REASON",
        message="Unsupported durable operation",
        run_id="run-bad",
        correlation_id="corr-validator",
    )
    assert not (state_dir / "run-bad.json").exists()


def test_actor_provenance_is_invalidated_by_reassignment_and_branch_leakage(tmp_path: Path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    accepted = _write(
        tmp_path / "accepted.syn",
        'agent A { model "mock" }\nlet worker = spawn A()\nworker => process()\nlet result = await worker.process()\n',
    )
    accepted_result = _durable(accepted, state_dir, "run-actor-accepted")
    assert accepted_result.returncode == 20, accepted_result.stderr

    reassigned = _write(
        tmp_path / "reassigned.syn",
        'agent A { model "mock" }\nlet worker = spawn A()\nworker = 0\nworker => process()\n',
    )
    reassigned_result = _durable(reassigned, state_dir, "run-actor-reassigned")
    assert reassigned_result.returncode == 25
    _assert_error_payload(
        _json_stdout(reassigned_result),
        exit_code=25,
        code="UNSUPPORTED_DURABLE_OPERATION_OR_REASON",
        message="Unsupported durable operation",
        run_id="run-actor-reassigned",
    )
    assert not (state_dir / "run-actor-reassigned.json").exists()

    branch_leak = _write(
        tmp_path / "branch-leak.syn",
        'agent A { model "mock" }\nlet worker = 0\nif true {\n  worker = spawn A()\n}\nworker => process()\n',
    )
    branch_leak_result = _durable(branch_leak, state_dir, "run-actor-branch-leak")
    assert branch_leak_result.returncode == 25
    _assert_error_payload(
        _json_stdout(branch_leak_result),
        exit_code=25,
        code="UNSUPPORTED_DURABLE_OPERATION_OR_REASON",
        message="Unsupported durable operation",
        run_id="run-actor-branch-leak",
    )
    assert not (state_dir / "run-actor-branch-leak.json").exists()


def test_actor_provenance_is_preserved_when_all_branches_spawn(tmp_path: Path):
    source = (
        'agent A { model "mock" }\n'
        "let worker = 0\n"
        "if true {\n"
        "  worker = spawn A()\n"
        "} else {\n"
        "  worker = spawn A()\n"
        "}\n"
        "worker => process()\n"
        "let result = await worker.process()\n"
    )
    ast_root = compile_to_ast(source)
    assert any(isinstance(stmt, synapse_ast.IfStmt) for stmt in ast_root.statements)
    program = _write(tmp_path / "all-branches.syn", source)
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    completed = _durable(program, state_dir, "run-actor-all-branches")

    assert completed.returncode == 20, completed.stderr
    payload = _json_stdout(completed)
    artifact = _load_artifact(payload)
    assert artifact["active_suspension"]["reason"] == "awaiting_promise"


@pytest.mark.parametrize(
    ("source", "field_name", "descendant_type"),
    [
        (
            'assert suspend await_human_approval("ok"), "message"\n',
            "condition",
            synapse_ast.SuspendExpr,
        ),
        (
            'assert await promise_token, "message"\n',
            "condition",
            synapse_ast.AwaitExpr,
        ),
        (
            'assert true, llm("secret")\n',
            "message",
            synapse_ast.LLMCall,
        ),
    ],
)
def test_assert_statement_rejects_suspension_descendants_before_artifact(
    tmp_path: Path,
    source: str,
    field_name: str,
    descendant_type: type,
):
    ast_root = compile_to_ast(source)
    assert_stmt = ast_root.statements[0]
    assert isinstance(assert_stmt, synapse_ast.AssertStmt)
    assert any(isinstance(node, descendant_type) for node in app._walk_ast(getattr(assert_stmt, field_name)))
    program = _write(tmp_path / "assert-async.syn", source)
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    completed = _durable(program, state_dir, "run-assert-async")

    assert completed.returncode == 25
    assert completed.stderr == ""
    _assert_error_payload(
        _json_stdout(completed),
        exit_code=25,
        code="UNSUPPORTED_DURABLE_OPERATION_OR_REASON",
        message="Unsupported durable operation",
        run_id="run-assert-async",
    )
    assert not (state_dir / "run-assert-async.json").exists()


def test_direct_call_allowlist_matches_replay_recorded_builtin_contract(tmp_path: Path):
    assert app._DIRECT_CALL_ALLOWLIST == {
        "len",
        "range",
        "type",
        "str",
        "int",
        "float",
        "list",
        "dict",
        "abs",
        "sum",
        "max",
        "min",
        "sorted",
        "reversed",
        "enumerate",
        "zip",
        "any",
        "all",
        "time",
        "random",
        "uuid",
        "print",
    }
    program = _write(
        tmp_path / "builtins.syn",
        "\n".join([
            "let a = len([1, 2])",
            "let b = range(2)",
            "let c = type(a)",
            'let d = str(a)',
            'let e = int("3")',
            'let f = float("3.5")',
            "let g = list(b)",
            "let h = dict()",
            "let i = abs(-1)",
            "let j = sum([1, 2])",
            "let k = max(1, 2)",
            "let l = min(1, 2)",
            "let m = sorted([2, 1])",
            "let n = reversed([1, 2])",
            'let o = enumerate(["x"])',
            "let p = zip([1], [2])",
            "let q = any([false, true])",
            "let r = all([true, true])",
            "let s = time()",
            "let t = random()",
            "let u = uuid()",
            'print("ok")',
            "",
        ]),
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    completed = _durable(program, state_dir, "run-builtins")

    assert completed.returncode == 0, completed.stderr
    artifact = _load_artifact(_json_stdout(completed))
    side_effect_names = [event["name"] for event in artifact["replay_state"]["execution_history"] if event["type"] == "side_effect"]
    assert side_effect_names == ["time", "random", "uuid"]


@pytest.mark.parametrize("callee", ["map", "filter"])
def test_direct_call_allowlist_rejects_map_filter(tmp_path: Path, callee: str):
    program = _write(tmp_path / f"{callee}.syn", f"let x = {callee}(print, [1])\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    completed = _durable(program, state_dir, f"run-{callee}")

    assert completed.returncode == 25
    _assert_error_payload(
        _json_stdout(completed),
        exit_code=25,
        code="UNSUPPORTED_DURABLE_OPERATION_OR_REASON",
        message="Unsupported durable operation",
        run_id=f"run-{callee}",
    )
    assert not (state_dir / f"run-{callee}.json").exists()


def test_terminal_error_artifact_is_retained_and_reused_run_id_conflicts(tmp_path: Path):
    program = _write(tmp_path / "error.syn", "let x = 1 / 0\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    first = _durable(program, state_dir, "run-error")
    payload = _json_stdout(first)
    artifact = json.loads((state_dir / "run-error.json").read_text(encoding="utf-8"))
    assert first.returncode == 1
    _assert_error_payload(
        payload,
        exit_code=1,
        code="RUNTIME_EXECUTION_ERROR",
        message="Runtime execution failed",
        run_id="run-error",
    )
    assert artifact["status"] == "ERROR"
    assert artifact["terminal"]["error"] == {
        "code": "RUNTIME_EXECUTION_ERROR",
        "message": "Runtime execution failed",
    }

    second = _durable(program, state_dir, "run-error")
    assert second.returncode == 26
    assert second.stderr == ""
    _assert_error_payload(
        _json_stdout(second),
        exit_code=26,
        code="ARTIFACT_EXISTS_OR_LOCKED",
        message="Artifact already exists or is locked",
        run_id="run-error",
    )
    assert (state_dir / "run-error.json").exists()


def test_existing_artifact_and_lock_prevent_initial_run(tmp_path: Path):
    program = _write(tmp_path / "program.syn", 'print("x")\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "run-artifact.json").write_text('{"already":true}', encoding="utf-8")
    artifact_conflict = _durable(program, state_dir, "run-artifact", "--correlation-id", "corr-artifact")
    assert artifact_conflict.returncode == 26
    _assert_error_payload(
        _json_stdout(artifact_conflict),
        exit_code=26,
        code="ARTIFACT_EXISTS_OR_LOCKED",
        message="Artifact already exists or is locked",
        run_id="run-artifact",
        correlation_id="corr-artifact",
    )
    assert json.loads((state_dir / "run-artifact.json").read_text(encoding="utf-8")) == {"already": True}

    (state_dir / "run-lock.json.lock").mkdir()
    lock_conflict = _durable(program, state_dir, "run-lock", "--correlation-id", "corr-lock")
    assert lock_conflict.returncode == 26
    _assert_error_payload(
        _json_stdout(lock_conflict),
        exit_code=26,
        code="ARTIFACT_EXISTS_OR_LOCKED",
        message="Artifact already exists or is locked",
        run_id="run-lock",
        correlation_id="corr-lock",
    )


@pytest.mark.parametrize(
    "fault",
    [
        PermissionError(errno.EACCES, "P2A_SECRET_DO_NOT_LEAK_7f93"),
        OSError(errno.EPERM, "P2A_SECRET_DO_NOT_LEAK_7f93"),
        OSError(errno.EROFS, "P2A_SECRET_DO_NOT_LEAK_7f93"),
    ],
)
def test_lock_acquisition_permission_errors_are_invalid_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: OSError,
):
    result = _run_with_lock_mkdir_fault(tmp_path, monkeypatch, fault)

    assert result.exit_code == 2
    _assert_error_payload(
        result.public_payload,
        exit_code=2,
        code="INVALID_CLI_INPUT",
        message="Invalid durable input",
        run_id="run-lock-fault",
        correlation_id="corr-lock-fault",
    )
    rendered = json.dumps(result.public_payload, sort_keys=True)
    assert "P2A_SECRET_DO_NOT_LEAK_7f93" not in rendered
    assert not (tmp_path / "state" / "run-lock-fault.json").exists()


@pytest.mark.parametrize("fault_errno", [errno.EIO, errno.ENOSPC, errno.EMFILE])
def test_unexpected_lock_acquisition_oserror_is_runtime_error_not_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fault_errno: int,
):
    marker = "P2A_SECRET_DO_NOT_LEAK_7f93"
    result = _run_with_lock_mkdir_fault(tmp_path, monkeypatch, OSError(fault_errno, marker))

    assert result.exit_code == 1
    assert result.exit_code != 26
    _assert_error_payload(
        result.public_payload,
        exit_code=1,
        code="RUNTIME_EXECUTION_ERROR",
        message="Runtime execution failed",
        run_id="run-lock-fault",
        correlation_id="corr-lock-fault",
    )
    assert not (tmp_path / "state" / "run-lock-fault.json").exists()
    synapse_cli._render_durable_result(result)
    captured = capsys.readouterr()
    assert captured.out.count("\n") == 1
    assert captured.err == ""
    assert marker not in captured.out
    assert marker not in captured.err


def test_process_level_concurrent_initial_run_is_exclusive(tmp_path: Path):
    source = "\n".join(f'print("line-{idx}")' for idx in range(1200))
    program = _write(tmp_path / "slow.syn", source)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    command = [
        sys.executable,
        "-m",
        "synapse",
        "run",
        str(program),
        "--durable",
        "--state-dir",
        str(state_dir),
        "--run-id",
        "run-concurrent",
    ]
    first = subprocess.Popen(command, cwd=REPO_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    second = subprocess.Popen(command, cwd=REPO_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    out1, err1 = first.communicate(timeout=30)
    out2, err2 = second.communicate(timeout=30)
    results = [(first.returncode, out1, err1), (second.returncode, out2, err2)]
    assert sorted(code for code, _, _ in results) == [0, 26]
    assert (state_dir / "run-concurrent.json").exists()
    winners = [json.loads(out) for code, out, _ in results if code == 0]
    assert len(winners) == 1
    assert winners[0]["output_delta"][0] == "line-0"
    assert winners[0]["output_delta"][-1] == "line-1199"


@pytest.mark.parametrize(
    "bad_value",
    [
        float("nan"),
        float("inf"),
        b"bytes",
        lambda: None,
        {1: "non-string-key"},
    ],
)
def test_whole_artifact_strict_json_faults_do_not_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_value: object):
    program = _write(tmp_path / "complete.syn", 'print("x")\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    original = app._project_replay_state

    def poisoned(interpreter):
        projection = original(interpreter)
        projection["actor_log"] = [bad_value]
        return projection

    monkeypatch.setattr(app, "_project_replay_state", poisoned)
    result = app.execute_durable_run(app.DurableRunRequest(source_path=program, state_dir=state_dir, run_id="run-poison"))
    assert result.exit_code == 1
    _assert_error_payload(
        result.public_payload,
        exit_code=1,
        code="RUNTIME_EXECUTION_ERROR",
        message="Artifact validation failed",
        run_id="run-poison",
    )
    assert not (state_dir / "run-poison.json").exists()


def test_whole_artifact_cycle_fault_does_not_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    program = _write(tmp_path / "complete.syn", 'print("x")\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    original = app._project_replay_state

    def poisoned(interpreter):
        projection = original(interpreter)
        cycle: list[object] = []
        cycle.append(cycle)
        projection["actor_log"] = cycle
        return projection

    monkeypatch.setattr(app, "_project_replay_state", poisoned)
    result = app.execute_durable_run(app.DurableRunRequest(source_path=program, state_dir=state_dir, run_id="run-cycle"))
    assert result.exit_code == 1
    _assert_error_payload(
        result.public_payload,
        exit_code=1,
        code="RUNTIME_EXECUTION_ERROR",
        message="Artifact validation failed",
        run_id="run-cycle",
    )
    assert not (state_dir / "run-cycle.json").exists()


@pytest.mark.parametrize("bad_payload_value", [float("nan"), object()])
def test_non_strict_suspension_payload_blocks_commit_without_raw_repr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_payload_value: object,
):
    program = _write(tmp_path / "poison-payload.syn", 'print("x")\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    original_interpreter = app.Interpreter

    class PoisonInterpreter(original_interpreter):
        def interpret_async(self, ast_root):
            def flow():
                yield self.Suspension(
                    ast_root,
                    self.global_env,
                    "awaiting_external_signal",
                    {"promise_id": "poison", "request": {"bad": bad_payload_value}},
                )

            return flow()

    monkeypatch.setattr(app, "Interpreter", PoisonInterpreter)

    result = app.execute_durable_run(app.DurableRunRequest(source_path=program, state_dir=state_dir, run_id="run-payload-poison"))

    assert result.exit_code == 1
    _assert_error_payload(
        result.public_payload,
        exit_code=1,
        code="RUNTIME_EXECUTION_ERROR",
        message="Artifact validation failed",
        run_id="run-payload-poison",
    )
    rendered = json.dumps(result.public_payload, sort_keys=True)
    assert "nan" not in rendered.lower()
    assert "object at" not in rendered
    assert not (state_dir / "run-payload-poison.json").exists()


def test_atomic_interruption_before_replace_does_not_publish_canonical_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    program = _write(tmp_path / "complete.syn", 'print("x")\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    def interrupted(artifact_path: Path, artifact: dict[str, object]) -> None:
        artifact_path.with_name("." + artifact_path.name + ".fault.tmp").write_text("{", encoding="utf-8")
        raise OSError("injected before replace")

    monkeypatch.setattr(app, "_atomic_commit_json", interrupted)
    result = app.execute_durable_run(app.DurableRunRequest(source_path=program, state_dir=state_dir, run_id="run-interrupt"))
    assert result.exit_code == 1
    _assert_error_payload(
        result.public_payload,
        exit_code=1,
        code="RUNTIME_EXECUTION_ERROR",
        message="Artifact validation failed",
        run_id="run-interrupt",
    )
    assert not (state_dir / "run-interrupt.json").exists()
    assert list(state_dir.glob("*.tmp"))


def test_permission_error_during_commit_is_invalid_input_not_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    program = _write(tmp_path / "complete.syn", 'print("x")\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    def permission_denied(artifact_path: Path, artifact: dict[str, object]) -> None:
        raise PermissionError("P2A_SECRET_DO_NOT_LEAK_7f93")

    monkeypatch.setattr(app, "_atomic_commit_json", permission_denied)

    result = app.execute_durable_run(app.DurableRunRequest(source_path=program, state_dir=state_dir, run_id="run-permission"))

    assert result.exit_code == 2
    _assert_error_payload(
        result.public_payload,
        exit_code=2,
        code="INVALID_CLI_INPUT",
        message="Invalid durable input",
        run_id="run-permission",
    )
    assert "P2A_SECRET_DO_NOT_LEAK_7f93" not in json.dumps(result.public_payload, sort_keys=True)
    assert not (state_dir / "run-permission.json").exists()


def test_secret_marker_is_redacted_from_public_error_and_pending_surfaces(tmp_path: Path):
    marker = "P2A_SECRET_DO_NOT_LEAK_7f93"
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    input_file = _write(tmp_path / "input.json", json.dumps({"secret": marker}))

    error_program = _write(tmp_path / "error.syn", "assert false, secret\n")
    error = _durable(error_program, state_dir, "run-secret-error", "--input-file", str(input_file))
    assert error.returncode == 1
    error_payload = _json_stdout(error)
    error_artifact = json.loads((state_dir / "run-secret-error.json").read_text(encoding="utf-8"))
    assert marker not in error.stdout
    assert marker not in error.stderr
    assert marker not in json.dumps(error_payload, sort_keys=True)
    assert marker not in json.dumps(error_artifact["terminal"]["error"], sort_keys=True)

    request_program = _write(tmp_path / "request.syn", "let approved = suspend await_human_approval(secret)\n")
    request = _durable(request_program, state_dir, "run-secret-request", "--input-file", str(input_file))
    assert request.returncode == 20
    request_payload = _json_stdout(request)
    request_artifact = _load_artifact(request_payload)
    assert marker not in request.stdout
    assert marker not in request.stderr
    assert marker not in json.dumps(request_payload, sort_keys=True)
    assert marker not in json.dumps(request_artifact["active_suspension"], sort_keys=True)
    assert marker not in json.dumps(request_payload["resume_argv"], sort_keys=True)

    prompt_program = _write(tmp_path / "prompt.syn", "let answer = llm(secret)\n")
    prompt = _durable(prompt_program, state_dir, "run-secret-prompt", "--input-file", str(input_file))
    assert prompt.returncode == 20
    prompt_payload = _json_stdout(prompt)
    prompt_artifact = _load_artifact(prompt_payload)
    assert marker not in prompt.stdout
    assert marker not in prompt.stderr
    assert marker not in json.dumps(prompt_payload, sort_keys=True)
    assert marker not in json.dumps(prompt_artifact["active_suspension"], sort_keys=True)
    assert marker not in json.dumps(prompt_payload["resume_argv"], sort_keys=True)


def test_lock_release_failure_reports_stale_lock_and_blocks_next_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    program = _write(tmp_path / "complete.syn", 'print("x")\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    def fail_release(lock_path: Path) -> None:
        raise OSError("cannot remove lock")

    monkeypatch.setattr(app, "_remove_lock_directory", fail_release)
    first = app.execute_durable_run(app.DurableRunRequest(source_path=program, state_dir=state_dir, run_id="run-stale"))
    assert first.exit_code == 0
    assert first.public_payload["status"] == "COMPLETED"
    assert any("STALE_LOCK_AFTER_COMMIT" in diagnostic for diagnostic in first.diagnostics)
    assert (state_dir / "run-stale.json").exists()
    assert (state_dir / "run-stale.json.lock").exists()

    monkeypatch.setattr(app, "_remove_lock_directory", app.shutil.rmtree)
    second = app.execute_durable_run(app.DurableRunRequest(source_path=program, state_dir=state_dir, run_id="run-stale"))
    assert second.exit_code == 26


def test_ast_inventory_is_complete_and_explicitly_classified():
    rows = app.durable_ast_inventory()
    assert len(rows) == 93
    assert not [row for row in rows if row["classification"] == "UNCLASSIFIED"]
    supported = {row["class_name"] for row in rows if str(row["classification"]).startswith("SUPPORTED")}
    assert {
        "Program",
        "SuspendExpr",
        "AwaitExpr",
        "LLMCall",
        "SpawnExpr",
        "SendStmt",
    } <= supported
