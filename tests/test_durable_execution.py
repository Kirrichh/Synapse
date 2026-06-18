from __future__ import annotations

import hashlib
import errno
import json
import math
import os
from pathlib import Path
import subprocess
import sys

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
