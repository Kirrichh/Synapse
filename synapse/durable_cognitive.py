"""Durable execution of the cognitive profile (``synapse.durable.cognitive/v1``).

A cognitive run persists a RUNNING crash point after every recorded external
effect. Recovery re-executes the program and verifies every recorded event;
recorded effects are consumed, never repeated; the run then continues LIVE.
The same verified re-execution, stopped at the end of a recorded history,
is the court's replay check of a session (stage 1b). A reproduction runs the
program again from its replay data with a session that answers only from the
recorded results: it is how retention proves a raw trace reconstructible
before removing it (И9). The lock of a crashed cognitive run names its owner
process, so it is provably stale.

The durable artifact format, validation and the public results stay with
``synapse.application``; this module owns only the cognitive profile's flow.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from . import application as _app
from .durable_profile import CognitiveProfileViolation, prepare_cognitive_interpreter, validate_cognitive_program
from .interpreter import Interpreter
from .memory_points import DURABLE_COGNITIVE_PROFILE
from .runtime.replay_engine import ReplayIntegrityError


_LOCK_OWNER = "owner.json"


def is_cognitive_program(ast: _app.synapse_ast.Node) -> bool:
    try:
        _app._validate_durable_ast(ast)
    except _app._DurableUnsupportedError:
        try:
            validate_cognitive_program(ast)
        except CognitiveProfileViolation:
            return False
        return True
    return False


def write_lock_owner(lock_path: Path) -> None:
    """Name the owning process, so the lock a crashed run leaves is provably stale."""
    import psutil
    import socket

    process = psutil.Process()
    record = {"pid": process.pid, "create_time": process.create_time(), "host": socket.gethostname()}
    (lock_path / _LOCK_OWNER).write_bytes(_app._strict_canonical_bytes(record))


def clear_stale_cognitive_lock(lock_path: Path) -> bool:
    """Remove a cognitive run's lock whose named owner process no longer exists.

    A lock without a readable owner, from another host or held by a live
    process is never presumed stale. Only a cognitive run names its owner, so
    a P2a lock is refused before any process table is consulted.
    """
    import socket

    try:
        record = json.loads((lock_path / _LOCK_OWNER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(record, dict) or record.get("host") != socket.gethostname():
        return False
    import psutil
    try:
        alive = psutil.Process(int(record["pid"])).create_time() == record.get("create_time")
    except psutil.NoSuchProcess:
        alive = False
    except (psutil.Error, KeyError, TypeError, ValueError):
        return False
    if alive:
        return False
    _app._remove_lock_directory(lock_path)
    return True


def cognitive_run_descriptor(*, run_id: str, artifact_path: Path, source_hash: str, source_code: str,
                             initial_bindings: dict[str, Any], history: list[Any]) -> dict[str, Any]:
    """What a memory session learns about its run: identity, program and the history recorded so far."""
    return {"run_id": run_id, "artifact_path": str(artifact_path), "source_hash": source_hash,
            "source_code": source_code, "initial_bindings": copy.deepcopy(initial_bindings),
            "history": copy.deepcopy(history)}


def open_cognitive_interpreter(
    *,
    run_id: str,
    source_code: str,
    initial_bindings: dict[str, Any],
    factory: Any,
    run: dict[str, Any],
    replay_state: dict[str, Any] | None = None,
) -> Interpreter:
    interpreter = Interpreter()
    interpreter.source_code = source_code
    if replay_state is not None:
        interpreter.load_snapshot(copy.deepcopy(replay_state))
    prepare_cognitive_interpreter(interpreter, run_id=run_id)
    _app._apply_initial_bindings(interpreter, copy.deepcopy(initial_bindings))
    if factory is not None:
        interpreter.bind_memory_session(factory.open_session(run))
    return interpreter


def cognitive_checkpoint(interpreter: Interpreter, *, artifact_path: Path, base: dict[str, Any],
                          state: dict[str, int]):
    def commit() -> None:
        state["revision"] += 1
        artifact = _app._build_artifact(
            status="RUNNING", run_id=base["run_id"], correlation_id=base["correlation_id"],
            source_path=base["source_path"], source_code=base["source_code"],
            initial_bindings=base["initial_bindings"], interpreter=interpreter, suspension=None,
            terminal=None, revision=state["revision"], idempotency=base["idempotency"],
            suspension_sequence=base["sequence"], profile=DURABLE_COGNITIVE_PROFILE, memory=base["memory"])
        _app._atomic_commit_json(artifact_path, artifact)
    return commit


def settle_cognitive(
    step,
    *,
    interpreter: Interpreter,
    artifact_path: Path,
    base: dict[str, Any],
    state: dict[str, int],
    recorded_output: dict[str, Any] | None,
) -> _app.DurableRunResult:
    """Drive a cognitive run to its next persisted outcome and commit it.

    ``recorded_output`` is the output prefix a recovered run must reproduce
    before anything new is published.
    """
    suspension = None
    try:
        suspension = step()
    except StopIteration:
        status, terminal = "COMPLETED", {"status": "COMPLETED", "exit_code": 0}
    except ReplayIntegrityError:
        return _app._artifact_failure(base["run_id"], base["correlation_id"])
    except Exception:
        status, terminal = "ERROR", _app._terminal_descriptor("ERROR", 1)
    else:
        if type(suspension).__name__ != "Suspension" or getattr(suspension, "reason", "") != "awaiting_llm":
            status, terminal, suspension = "ERROR", _app._terminal_descriptor(
                "ERROR", 25, _app._UNSUPPORTED_DURABLE_OPERATION_OR_REASON, _app._PUBLIC_ERROR_MESSAGES["unsupported"]), None
        else:
            status, terminal = "PENDING", None
    if interpreter.runtime_mode.name != "LIVE":
        # The run ended before consuming its whole record: it diverged.
        return _app._artifact_failure(base["run_id"], base["correlation_id"])
    lines = [str(line) for line in interpreter.output_buffer]
    prefix = 0
    if recorded_output is not None:
        prefix = recorded_output["line_count"]
        if len(lines) < prefix or _app._sha256_prefixed_value(lines[:prefix]) != recorded_output["digest"]:
            return _app._artifact_failure(base["run_id"], base["correlation_id"])
    if status != "PENDING":
        try:
            interpreter.finish_memory_session()
        except ReplayIntegrityError:
            return _app._artifact_failure(base["run_id"], base["correlation_id"])
        except Exception:
            status, terminal = "ERROR", _app._terminal_descriptor("ERROR", 1)
    state["revision"] += 1
    artifact = _app._build_artifact(
        status=status, run_id=base["run_id"], correlation_id=base["correlation_id"],
        source_path=base["source_path"], source_code=base["source_code"],
        initial_bindings=base["initial_bindings"], interpreter=interpreter, suspension=suspension,
        terminal=terminal, revision=state["revision"], idempotency=base["idempotency"],
        suspension_sequence=base["sequence"], profile=DURABLE_COGNITIVE_PROFILE, memory=base["memory"])
    _app._atomic_commit_json(artifact_path, artifact)
    delta = lines[prefix:]
    if status == "COMPLETED":
        return _app.DurableRunResult(status="COMPLETED", exit_code=0,
                                public_payload=_app._public_completed_payload(artifact, artifact_path, delta))
    if status == "PENDING":
        return _app.DurableRunResult(status="PENDING", exit_code=20,
                                public_payload=_app._public_pending_payload(artifact, artifact_path, delta))
    return _app.DurableRunResult(status="ERROR", exit_code=int(terminal["exit_code"]),
                            public_payload=_app._public_committed_error_payload(artifact, artifact_path, delta))


def recover_cognitive_run(artifact: dict[str, Any], artifact_path: Path,
                           request: "_app.DurableResumeRequest") -> _app.DurableRunResult:
    """Continue a RUNNING crash point: emergency consolidation first, then replay."""
    factory = None
    if artifact["memory"] is not None:
        if request.memory_resolver is None:
            return _app._state_file_invalid_input()
        factory = request.memory_resolver(copy.deepcopy(artifact["memory"]))
    run = run_of_artifact(artifact, artifact_path)
    if factory is not None:
        factory.recover(run, history=copy.deepcopy(artifact["replay_state"]["execution_history"]))
    try:
        ast = _app.compile_to_ast(artifact["replay_state"]["source_code"])
        validate_cognitive_program(ast)
    except Exception:
        return _app._artifact_failure(artifact["run_id"], artifact["correlation_id"])
    interpreter = open_cognitive_interpreter(
        run_id=artifact["run_id"], source_code=artifact["replay_state"]["source_code"],
        initial_bindings=artifact["initial_bindings"]["value"], factory=factory, run=run,
        replay_state=artifact["replay_state"])
    base = {"run_id": artifact["run_id"], "correlation_id": artifact["correlation_id"],
            "source_path": Path(str(artifact["source"]["path"])),
            "source_code": artifact["replay_state"]["source_code"],
            "initial_bindings": copy.deepcopy(artifact["initial_bindings"]["value"]),
            "idempotency": copy.deepcopy(artifact["idempotency"]),
            "sequence": len(artifact["idempotency"]["resolved_suspensions"]) + 1, "memory": artifact["memory"]}
    state = {"revision": int(artifact["revision"])}
    interpreter.durable_checkpoint = cognitive_checkpoint(interpreter, artifact_path=artifact_path,
                                                           base=base, state=state)
    flow = interpreter.interpret_async(ast)
    return settle_cognitive(lambda: next(flow), interpreter=interpreter, artifact_path=artifact_path,
                             base=base, state=state, recorded_output=artifact["output_state"])


def run_of_artifact(artifact: dict[str, Any], artifact_path: Path) -> dict[str, Any]:
    return cognitive_run_descriptor(run_id=artifact["run_id"], artifact_path=artifact_path,
                                    source_hash=artifact["source"]["hash"],
                                    source_code=artifact["replay_state"]["source_code"],
                                    initial_bindings=artifact["initial_bindings"]["value"],
                                    history=artifact["replay_state"]["execution_history"])


def read_cognitive_session(artifact_path: Path) -> dict[str, Any]:
    """The recorded session of a cognitive artifact, or its integrity failure (never partial trust)."""
    try:
        artifact = _app._json_loads_strict_artifact(Path(artifact_path).read_text(encoding="utf-8"))
        _app._validate_artifact(artifact, Path(artifact_path))
    except (OSError, ValueError, TypeError, KeyError, _app._ArtifactIntegrityError) as exc:
        return {"source_code": "", "initial_bindings": {}, "history": [], "integrity_error": str(exc)[:200]}
    if artifact.get("execution_profile") != DURABLE_COGNITIVE_PROFILE:
        return {"source_code": "", "initial_bindings": {}, "history": [],
                "integrity_error": "not a cognitive durable artifact"}
    return {"source_code": artifact["replay_state"]["source_code"],
            "initial_bindings": copy.deepcopy(artifact["initial_bindings"]["value"]),
            "history": copy.deepcopy(artifact["replay_state"]["execution_history"]), "integrity_error": None}


def replay_cognitive_history(*, run_id: str, source_code: str, initial_bindings: dict[str, Any],
                             history: list[Any], session, event_budget: int) -> dict[str, Any]:
    """Re-execute a session against its recorded history, verifying every event (stage 1b).

    The replay session supplies the same pinned learned habits and refuses any
    live effect with ``ReplayHorizon``; recorded results are consumed, never
    requested again. ``replay_verified`` means every recorded event was
    reproduced in order and the whole history was consumed.
    """
    from .memory_points import ReplayHorizon

    if not history:
        return {"status": "replay_unavailable", "consumed": 0, "reason": "no recorded history"}
    if len(history) > event_budget:
        return {"status": "replay_budget_exceeded", "consumed": 0, "reason": f"{len(history)} events"}
    try:
        ast = _app.compile_to_ast(source_code)
        validate_cognitive_program(ast)
    except Exception as exc:  # noqa: BLE001 - an unreadable program cannot be re-executed
        return {"status": "replay_unavailable", "consumed": 0, "reason": type(exc).__name__}
    interpreter = open_cognitive_interpreter(run_id=run_id, source_code=source_code, initial_bindings=initial_bindings,
                                             factory=None, run=None,
                                             replay_state={"source_code": source_code,
                                                           "execution_history": copy.deepcopy(history)})
    try:
        interpreter.bind_memory_session(session)
        next(interpreter.interpret_async(ast))
    except (StopIteration, ReplayHorizon):
        pass
    except ReplayIntegrityError as exc:
        return {"status": "replay_diverged", "consumed": interpreter.replay_cursor, "reason": str(exc)[:200]}
    except Exception as exc:  # noqa: BLE001 - a re-execution that fails before the end diverged
        if interpreter.runtime_mode.name != "LIVE":
            return {"status": "replay_diverged", "consumed": interpreter.replay_cursor, "reason": type(exc).__name__}
    consumed = interpreter.runtime.replay.recorded_length()
    verified = interpreter.runtime_mode.name == "LIVE" and consumed >= len(history)
    return {"status": "replay_verified" if verified else "replay_diverged", "consumed": min(consumed, len(history)),
            "reason": None if verified else "history not fully reproduced"}



def reproduce_cognitive_session(*, run_id: str, source_code: str, initial_bindings: dict[str, Any], session,
                                event_budget: int) -> dict[str, Any]:
    """Execute a session again from its program and recorded results only (no recorded history).

    ``session`` answers every external action and similarity from the gateway's
    record and raises ``ReplayHorizon`` for anything unrecorded; the program
    never reaches a live effect. The produced history is returned for the
    caller to compare with the raw trace it is meant to reconstruct.
    """
    from .memory_points import ReplayHorizon

    try:
        ast = _app.compile_to_ast(source_code)
        validate_cognitive_program(ast)
    except Exception as exc:  # noqa: BLE001 - an unreadable program reproduces nothing
        return {"status": "replay_unavailable", "reason": type(exc).__name__, "history": []}
    interpreter = open_cognitive_interpreter(run_id=run_id, source_code=source_code, initial_bindings=initial_bindings,
                                             factory=None, run=None)
    try:
        interpreter.bind_memory_session(session)
        next(interpreter.interpret_async(ast))
    except (StopIteration, ReplayHorizon):
        pass
    except Exception as exc:  # noqa: BLE001 - a reproduction that fails is not a reproduction
        return {"status": "replay_diverged", "reason": type(exc).__name__, "history": []}
    history = copy.deepcopy(interpreter.execution_history)
    if len(history) > event_budget:
        return {"status": "replay_budget_exceeded", "reason": f"{len(history)} events", "history": []}
    return {"status": "reproduced", "reason": None, "history": history}
