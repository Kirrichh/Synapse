from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest

from synapse import ast as synapse_ast
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
    _assert_error_payload(
        _json_stdout(missing_dir),
        exit_code=2,
        code="INVALID_CLI_INPUT",
        message="Invalid state directory",
    )
    state_file = _write(tmp_path / "state-file", "")
    state_is_file = _run(["run", str(program), "--durable", "--state-dir", str(state_file)])
    assert state_is_file.returncode == 2
    assert state_is_file.stderr == ""
    _assert_error_payload(
        _json_stdout(state_is_file),
        exit_code=2,
        code="INVALID_CLI_INPUT",
        message="Invalid state directory",
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

    completed = _durable(program, state_dir, "run-invalid-source")

    assert completed.returncode == 2
    assert completed.stderr == ""
    _assert_error_payload(
        _json_stdout(completed),
        exit_code=2,
        code="INVALID_CLI_INPUT",
        message="Invalid durable input",
    )
    assert not (state_dir / "run-invalid-source.json").exists()


@pytest.mark.parametrize("binding", ["let", "print", "medium", "__synapse_private", "owned", "assigned", "AgentName"])
def test_initial_binding_collisions_are_rejected_without_artifact(tmp_path: Path, binding: str):
    program = _write(tmp_path / "bindings.syn", "agent AgentName { model \"mock\" }\nlet owned = 1\nlet assigned = 0\nassigned = 2\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    input_file = tmp_path / f"{binding}.json"
    input_file.write_text(json.dumps({binding: 1}), encoding="utf-8")
    result = app.execute_durable_run(
        app.DurableRunRequest(source_path=program, state_dir=state_dir, run_id=f"run-input-{binding.replace('_', 'x')}", input_file=input_file),
        stdin=None,
    )
    assert result.exit_code in {2, 25}
    assert not (state_dir / f"run-input-{binding.replace('_', 'x')}.json").exists()


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
    completed = _durable(program, state_dir, "run-bad")
    assert completed.returncode == 25
    assert completed.stderr == ""
    _assert_error_payload(
        _json_stdout(completed),
        exit_code=25,
        code="UNSUPPORTED_DURABLE_OPERATION_OR_REASON",
        message="Unsupported durable operation",
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
    )
    assert not (state_dir / "run-actor-reassigned.json").exists()

    branch_leak = _write(
        tmp_path / "branch-leak.syn",
        'agent A { model "mock" }\nlet worker = 0\nif true {\n  worker = spawn A()\n}\nworker => process()\n',
    )
    branch_leak_result = _durable(branch_leak, state_dir, "run-actor-branch-leak")
    assert branch_leak_result.returncode == 25
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
    _assert_error_payload(
        _json_stdout(completed),
        exit_code=25,
        code="UNSUPPORTED_DURABLE_OPERATION_OR_REASON",
        message="Unsupported durable operation",
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
    )
    assert (state_dir / "run-error.json").exists()


def test_existing_artifact_and_lock_prevent_initial_run(tmp_path: Path):
    program = _write(tmp_path / "program.syn", 'print("x")\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "run-artifact.json").write_text('{"already":true}', encoding="utf-8")
    artifact_conflict = _durable(program, state_dir, "run-artifact")
    assert artifact_conflict.returncode == 26
    _assert_error_payload(
        _json_stdout(artifact_conflict),
        exit_code=26,
        code="ARTIFACT_EXISTS_OR_LOCKED",
        message="Artifact already exists or is locked",
    )
    assert json.loads((state_dir / "run-artifact.json").read_text(encoding="utf-8")) == {"already": True}

    (state_dir / "run-lock.json.lock").mkdir()
    lock_conflict = _durable(program, state_dir, "run-lock")
    assert lock_conflict.returncode == 26
    _assert_error_payload(
        _json_stdout(lock_conflict),
        exit_code=26,
        code="ARTIFACT_EXISTS_OR_LOCKED",
        message="Artifact already exists or is locked",
    )


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
