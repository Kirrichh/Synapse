from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest

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


def test_cli_validation_and_json_channel_separation(tmp_path: Path):
    program = _write(tmp_path / "sync.syn", 'print("sync")\n')
    sync = _run(["run", str(program)])
    assert sync.returncode == 0
    assert sync.stdout == "sync\n"

    bad_flag = _run(["run", str(program), "--state-dir", str(tmp_path)])
    assert bad_flag.returncode == 2
    missing_state = _run(["run", str(program), "--durable"])
    assert missing_state.returncode == 2
    missing_dir = _run(["run", str(program), "--durable", "--state-dir", str(tmp_path / "missing")])
    assert missing_dir.returncode == 2
    assert missing_dir.stdout == ""

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    durable = _durable(program, state_dir, "run-json")
    assert durable.returncode == 0
    assert durable.stderr == ""
    assert durable.stdout.count("\n") == 1
    assert _json_stdout(durable)["output_delta"] == ["sync"]


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
    assert completed.stdout == ""
    assert not (state_dir / "run-bad.json").exists()


def test_terminal_error_artifact_is_retained_and_reused_run_id_conflicts(tmp_path: Path):
    program = _write(tmp_path / "error.syn", "let x = 1 / 0\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    first = _durable(program, state_dir, "run-error")
    payload = _json_stdout(first)
    artifact = _load_artifact(payload)
    assert first.returncode == 1
    assert artifact["status"] == "ERROR"
    assert artifact["terminal"]["error"]["message"] == "Division by zero"

    second = _durable(program, state_dir, "run-error")
    assert second.returncode == 26
    assert second.stdout == ""
    assert (state_dir / "run-error.json").exists()


def test_existing_artifact_and_lock_prevent_initial_run(tmp_path: Path):
    program = _write(tmp_path / "program.syn", 'print("x")\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "run-artifact.json").write_text('{"already":true}', encoding="utf-8")
    artifact_conflict = _durable(program, state_dir, "run-artifact")
    assert artifact_conflict.returncode == 26
    assert json.loads((state_dir / "run-artifact.json").read_text(encoding="utf-8")) == {"already": True}

    (state_dir / "run-lock.json.lock").mkdir()
    lock_conflict = _durable(program, state_dir, "run-lock")
    assert lock_conflict.returncode == 26
    assert lock_conflict.stdout == ""


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
    assert not (state_dir / "run-cycle.json").exists()


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
    assert not (state_dir / "run-interrupt.json").exists()
    assert list(state_dir.glob("*.tmp"))


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
