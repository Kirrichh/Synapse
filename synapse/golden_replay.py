"""Alpha3e Golden Replay utilities.

This module implements a deliberately small record/mock-replay contract for the
alpha3e release candidate. It is not a debugger and it does not introduce new
VM/runtime semantics; it serializes existing Interpreter state and validates a
stable semantic subset during replay.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.interpreter import Interpreter, RuntimeMode
from synapse.builtins import LLMBackend
from synapse.llm import LLMProviderStatus, LLMResult, LLMTokenStatus, LLMUsage
from synapse.runtime.host_abi import HOST_ABI_VERSION
from synapse.version import LANGUAGE_VERSION, RUNTIME_VERSION, SPEC_VERSION

SCHEMA_VERSION = 1
DEFAULT_VIRTUAL_CLOCK_START = "2026-01-01T00:00:00Z"
DEFAULT_CLOCK_STEP = 1


class DeterministicReplayError(RuntimeError):
    """Raised when a golden replay artifact cannot be replayed exactly."""


class ReplayArtifactError(RuntimeError):
    """Raised when a golden artifact is missing, malformed, or structurally invalid.

    Distinct from DeterministicReplayError: this is an *input artifact* problem
    (missing manifest/history, unreadable JSON, wrong schema) rather than a
    replay determinism mismatch (chain broken, hash mismatch). The debugger
    P5 bridge uses this to fail fast on bad artifact directories before any
    trace cursor is exposed.
    """


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compile_source(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


def program_hash_for_source(source: str) -> str:
    """Current alpha3e golden program identity.

    This mirrors Interpreter._current_runtime_program_hash(): source hashing is
    the cross-layer stable contract for the tree-walker/CVM mixed runtime.
    """
    return f"sha256:{_sha256_text(source)}"


def _history_hash(history: list[dict[str, Any]]) -> str:
    # Use Interpreter's canonical replay engine when possible by loading the
    # history into a fresh host. This keeps golden artifacts aligned with the
    # project runtime rather than inventing another hash chain.
    interp = Interpreter()
    interp.execution_history = list(history)
    return interp.compute_history_hash()


def _history_chain_valid(history: list[dict[str, Any]], expected_hash: str) -> bool:
    return _history_hash(history) == expected_hash


def extract_llm_cache(history: Iterable[dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Build a deterministic mock table from recorded llm_call events."""
    cache: Dict[str, Dict[str, Any]] = {}
    for event in history:
        if event.get("type") != "llm_call":
            continue
        prompt_hash = str(event.get("prompt_hash", ""))
        model = str(event.get("model") or "mock")
        key = f"{prompt_hash}:{model}"
        cache[key] = {
            "prompt_hash": prompt_hash,
            "model": model,
            "result": event.get("result"),
        }
    return cache


class CacheOnlyLLMBackend(LLMBackend):
    """LLM backend used by replay --mock.

    It has no provider fallback. Missing cache entries fail deterministically.
    """

    def __init__(self, cache: Dict[str, Dict[str, Any]]):
        super().__init__(default_model="mock", provider="mock", mode="mock")
        self.cache = dict(cache)
        self.provider_call_count = 0

    def _cached_entry(self, prompt: str, model: Optional[str]) -> tuple[str, Any]:
        prompt_hash = hashlib.sha256(str(prompt).encode("utf-8")).hexdigest()
        model_name = str(model or self.default_model)
        key = f"{prompt_hash}:{model_name}"
        if key not in self.cache:
            raise DeterministicReplayError(
                f"LLM_REQUEST unresolved in mock replay: missing cache entry {key}"
            )
        return model_name, self.cache[key].get("result")

    def complete_result(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 100,
        privacy_context: Any = None,
    ) -> LLMResult:
        model_name, cached_result = self._cached_entry(prompt, model)
        result = LLMResult(
            status=LLMProviderStatus.COMPLETED,
            provider="replay",
            model=model_name,
            response_text=str(cached_result),
            usage=LLMUsage(
                token_status=LLMTokenStatus.UNAVAILABLE,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                thinking_included=False,
                diagnostics={},
            ),
        )
        self.last_result = result
        return result

    def complete(self, prompt: str, model: Optional[str] = None,
                 temperature: float = 0.7, max_tokens: int = 100) -> str:
        _model_name, cached_result = self._cached_entry(prompt, model)
        return cached_result


BOOTSTRAP_SYMBOLS = {
    "print", "trust_at_least",
    "untrusted", "low", "medium", "high", "critical",
    "short_term", "long_term", "session", "project",
    "user_controlled", "system_controlled",
    "reversible", "irreversible",
    "deep", "shallow", "exploratory", "conservative",
    "rollback", "warn", "halt", "events", "seconds", "calls",
    "minute", "days", "never", "tagged", "untagged", "asc", "desc",
    "policy_violation", "affective_history",
}


def _stable_user_variables(variables: Dict[str, Any]) -> Dict[str, Any]:
    """Return only user-defined, JSON-stable locals for sanity hashing."""
    stable: Dict[str, Any] = {}
    for key, value in (variables or {}).items():
        if key in BOOTSTRAP_SYMBOLS:
            continue
        if isinstance(value, dict) and value.get("__type__") == "opaque":
            continue
        stable[str(key)] = value
    return stable


def state_sanity_from_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Return stable semantic state fields for golden validation.

    This intentionally avoids comparing the whole VM/interpreter __dict__. New
    instrumentation fields may be added later with defaults without invalidating
    alpha3e artifacts.
    """
    global_env = snapshot.get("global_env", {}) or {}
    variables = global_env.get("variables", {}) if isinstance(global_env, dict) else {}
    stable_variables = _stable_user_variables(variables)
    vm_snapshots = snapshot.get("vm_snapshots", []) or []
    last_vm_state = {}
    if vm_snapshots and isinstance(vm_snapshots[-1], dict):
        last_vm_state = vm_snapshots[-1].get("state", {}) or {}
    return {
        "history_length": len(snapshot.get("execution_history", []) or []),
        "history_hash": snapshot.get("history_hash"),
        "output_hash": _sha256_json(snapshot.get("output_buffer", [])),
        "locals_hash": _sha256_json(stable_variables),
        "locals_keys": sorted(str(k) for k in stable_variables.keys()),
        "final_ip": last_vm_state.get("ip"),
        "stack_depth": len(last_vm_state.get("stack", []) or []),
        "call_frame_depth": len(last_vm_state.get("call_stack", []) or []),
        "guard_stack_depth": len(last_vm_state.get("guard_stack", []) or []),
        "context_stack_depth": len(last_vm_state.get("context_stack", []) or []),
        "policy_stack_depth": len(last_vm_state.get("policy_stack", []) or []),
        "guard_violation_active": bool(last_vm_state.get("guard_violation_active", False)),
        "mailbox_count": sum(len(v) for v in (snapshot.get("mailboxes", {}) or {}).values()),
        "actor_log_length": len(snapshot.get("actor_log", []) or []),
        "memory_audit_length": len(snapshot.get("memory_audit", []) or []),
    }


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_interpreter_from_source(source: str, *, llm_backend: Optional[LLMBackend] = None) -> Interpreter:
    interp = Interpreter()
    if llm_backend is not None:
        interp.llm_backend = llm_backend
    interp.source_code = source
    interp.interpret(compile_source(source))
    return interp


def record_source(source: str, output_dir: Path | str, *, source_path: str = "<memory>",
                  layer: str = "strict", virtual_clock_start: str = DEFAULT_VIRTUAL_CLOCK_START,
                  clock_step: int = DEFAULT_CLOCK_STEP) -> Dict[str, Any]:
    """Record a golden artifact directory for a source program."""
    output = Path(output_dir)
    if output.exists():
        # Avoid stale files corrupting the artifact; keep implementation simple.
        for child in sorted(output.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
    output.mkdir(parents=True, exist_ok=True)

    initial = Interpreter()
    initial.source_code = source
    initial_snapshot = initial.snapshot()

    interp = _run_interpreter_from_source(source)
    final_snapshot = interp.snapshot()
    history = list(interp.execution_history)
    final_history_hash = interp.compute_history_hash()
    llm_cache = extract_llm_cache(history)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "layer": layer,
        "metadata": {
            "source_path": source_path,
            "program_hash": program_hash_for_source(source),
            "host_abi_version": HOST_ABI_VERSION,
            "language_version": LANGUAGE_VERSION,
            "runtime_version": RUNTIME_VERSION,
            "spec_version": SPEC_VERSION,
            "generated_by": "synapse.golden_replay",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "environment": {
            "virtual_clock_start": virtual_clock_start,
            "clock_mode": "deterministic",
            "clock_step": int(clock_step),
            "gas_limit": None,
        },
        "final": {
            "history_length": len(history),
            "final_history_hash": final_history_hash,
            "state_sanity": state_sanity_from_snapshot(final_snapshot),
        },
        "files": {
            "source": "source.syn",
            "initial_vm_snapshot": "initial_vm_snapshot.json",
            "vm_snapshot": "vm_snapshot.json",
            "history": "history.json",
            "llm_cache": "llm_cache.mock.json",
        },
    }

    _write_json(output / "manifest.json", manifest)
    (output / "source.syn").write_text(source, encoding="utf-8")
    _write_json(output / "initial_vm_snapshot.json", initial_snapshot)
    _write_json(output / "vm_snapshot.json", final_snapshot)
    _write_json(output / "history.json", history)
    _write_json(output / "llm_cache.mock.json", llm_cache)
    return manifest


@dataclass
class ReplayResult:
    artifact_dir: str
    program_hash: str
    history_length: int
    final_history_hash: str
    drift: int
    state_sanity: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artifact_dir": self.artifact_dir,
            "program_hash": self.program_hash,
            "history_length": self.history_length,
            "final_history_hash": self.final_history_hash,
            "drift": self.drift,
            "state_sanity": self.state_sanity,
        }


def _diff_events(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> int:
    drift = abs(len(expected) - len(actual))
    for lhs, rhs in zip(expected, actual):
        if lhs != rhs:
            drift += 1
    return drift


def replay_mock_artifact(artifact_dir: Path | str) -> ReplayResult:
    """Replay a golden artifact using only embedded mocks and stable validators."""
    root = Path(artifact_dir)
    manifest = _read_json(root / "manifest.json")
    source = (root / manifest["files"]["source"]).read_text(encoding="utf-8")
    expected_history = _read_json(root / manifest["files"]["history"])
    expected_snapshot = _read_json(root / manifest["files"]["vm_snapshot"])
    llm_cache = _read_json(root / manifest["files"]["llm_cache"])

    expected_hash = manifest["metadata"]["program_hash"]
    actual_hash = program_hash_for_source(source)
    if actual_hash != expected_hash:
        raise DeterministicReplayError(f"program_hash mismatch: expected {expected_hash}, got {actual_hash}")
    if manifest["metadata"].get("host_abi_version") != HOST_ABI_VERSION:
        raise DeterministicReplayError(
            f"host_abi_version mismatch: artifact={manifest['metadata'].get('host_abi_version')} runtime={HOST_ABI_VERSION}"
        )
    final_hash = manifest["final"]["final_history_hash"]
    if not _history_chain_valid(expected_history, final_hash):
        raise DeterministicReplayError("execution_history chain broken in artifact")

    # Replay from the recorded initial state. The replay environment uses a
    # cache-only LLM backend, so provider calls are impossible and missing cache
    # entries become deterministic failures.
    initial_snapshot = _read_json(root / manifest["files"]["initial_vm_snapshot"])
    replay_interp = Interpreter.restore_snapshot(initial_snapshot)
    replay_interp.source_code = source
    replay_interp.llm_backend = CacheOnlyLLMBackend(llm_cache)
    replay_interp.interpret(compile_source(source))
    replay_snapshot = replay_interp.snapshot()

    actual_history = replay_interp.execution_history
    drift = _diff_events(expected_history, actual_history)
    actual_final_hash = replay_interp.compute_history_hash()
    if actual_final_hash != final_hash:
        drift += 1
    expected_sanity = manifest["final"].get("state_sanity") or state_sanity_from_snapshot(expected_snapshot)
    actual_sanity = state_sanity_from_snapshot(replay_snapshot)
    if actual_sanity != expected_sanity:
        drift += 1
    if drift != 0:
        raise DeterministicReplayError(
            f"golden replay drift detected: drift={drift}, expected_hash={final_hash}, actual_hash={actual_final_hash}"
        )

    return ReplayResult(
        artifact_dir=str(root),
        program_hash=actual_hash,
        history_length=len(actual_history),
        final_history_hash=actual_final_hash,
        drift=drift,
        state_sanity=actual_sanity,
    )


# ---------------------------------------------------------------------------
# Alpha3g I6: integrate golden-fixture helpers
# ---------------------------------------------------------------------------

def record_integrate_artifact(
    source: str,
    output_dir: "Path | str",
    *,
    source_path: str = "<memory>",
    layer: str = "strict",
    virtual_clock_start: str = DEFAULT_VIRTUAL_CLOCK_START,
    clock_step: int = DEFAULT_CLOCK_STEP,
    expect_abort: bool = False,
) -> Dict[str, Any]:
    """Record a golden artifact for an integrate program using the Alpha3g
    i2-skeleton path.

    This is intentionally separate from :func:`record_source` so that existing
    strict golden fixtures remain on the legacy path and are not affected by
    integrate-specific interpreter flags.

    ``expect_abort`` must be set to True when the source is expected to raise
    an exception (e.g. barrier-violation abort).  In that case the interpreter
    run is allowed to raise and the history up to the abort is recorded.
    """
    output = Path(output_dir)
    if output.exists():
        for child in sorted(output.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
    output.mkdir(parents=True, exist_ok=True)

    initial = Interpreter()
    initial.source_code = source
    initial_snapshot = initial.snapshot()

    interp = Interpreter()
    interp.integrate_i2_skeleton_enabled = True
    interp.source_code = source
    exc_repr: str = ""
    try:
        interp.interpret(compile_source(source))
    except Exception as exc:
        if not expect_abort:
            raise
        exc_repr = repr(exc)

    final_snapshot = interp.snapshot()
    history = list(interp.execution_history)
    final_history_hash = interp.compute_history_hash()
    llm_cache = extract_llm_cache(history)

    manifest: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "layer": layer,
        "metadata": {
            "source_path": source_path,
            "program_hash": program_hash_for_source(source),
            "host_abi_version": HOST_ABI_VERSION,
            "language_version": LANGUAGE_VERSION,
            "runtime_version": RUNTIME_VERSION,
            "spec_version": SPEC_VERSION,
            "generated_by": "synapse.golden_replay.record_integrate_artifact",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "integrate_i2_skeleton_enabled": True,
        },
        "environment": {
            "virtual_clock_start": virtual_clock_start,
            "clock_mode": "deterministic",
            "clock_step": int(clock_step),
            "gas_limit": None,
        },
        "final": {
            "history_length": len(history),
            "final_history_hash": final_history_hash,
            "state_sanity": state_sanity_from_snapshot(final_snapshot),
            "expected_abort": expect_abort,
            "abort_repr": exc_repr,
        },
        "files": {
            "source": "source.syn",
            "initial_vm_snapshot": "initial_vm_snapshot.json",
            "vm_snapshot": "vm_snapshot.json",
            "history": "history.json",
            "llm_cache": "llm_cache.mock.json",
        },
    }

    import json as _json
    (output / "manifest.json").write_text(_json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    (output / "source.syn").write_text(source, encoding="utf-8")
    (output / "history.json").write_text(_json.dumps(history, indent=2, default=str), encoding="utf-8")
    (output / "initial_vm_snapshot.json").write_text(_json.dumps(initial_snapshot, indent=2, default=str), encoding="utf-8")
    (output / "vm_snapshot.json").write_text(_json.dumps(final_snapshot, indent=2, default=str), encoding="utf-8")
    (output / "llm_cache.mock.json").write_text(_json.dumps(llm_cache, indent=2, default=str), encoding="utf-8")

    return manifest


def replay_integrate_artifact(artifact_dir: "Path | str") -> "ReplayResult":
    """Replay a golden artifact recorded by :func:`record_integrate_artifact`.

    Uses the Alpha3g i2-skeleton replay path.  Verifies:
    - program_hash and host_abi_version match the runtime;
    - history chain integrity;
    - zero drift between recorded history and replayed history.

    The replay interpreter runs with ``integrate_i2_skeleton_enabled = True``
    and ``RuntimeMode.REPLAY``, which activates :meth:`replay_integrate_i4_event`
    so the integrate body is NOT re-executed.
    """
    import copy as _copy
    root = Path(artifact_dir)
    manifest = _read_json(root / "manifest.json")
    source = (root / manifest["files"]["source"]).read_text(encoding="utf-8")
    expected_history = _read_json(root / manifest["files"]["history"])
    expected_snapshot = _read_json(root / manifest["files"]["vm_snapshot"])
    llm_cache = _read_json(root / manifest["files"]["llm_cache"])

    expected_hash = manifest["metadata"]["program_hash"]
    actual_hash = program_hash_for_source(source)
    if actual_hash != expected_hash:
        raise DeterministicReplayError(
            f"program_hash mismatch: expected {expected_hash}, got {actual_hash}"
        )
    if manifest["metadata"].get("host_abi_version") != HOST_ABI_VERSION:
        raise DeterministicReplayError(
            f"host_abi_version mismatch: artifact={manifest['metadata'].get('host_abi_version')} runtime={HOST_ABI_VERSION}"
        )
    final_hash = manifest["final"]["final_history_hash"]
    if final_hash and not _history_chain_valid(expected_history, final_hash):
        raise DeterministicReplayError("execution_history chain broken in artifact")

    initial_snapshot = _read_json(root / manifest["files"]["initial_vm_snapshot"])
    replay_interp = Interpreter.restore_snapshot(initial_snapshot)
    replay_interp.integrate_i2_skeleton_enabled = True
    replay_interp.execution_history = _copy.deepcopy(expected_history)
    replay_interp.runtime_mode = RuntimeMode.REPLAY
    replay_interp.replay_cursor = 0
    replay_interp.source_code = source
    replay_interp.llm_backend = CacheOnlyLLMBackend(llm_cache)

    expect_abort = manifest["final"].get("expected_abort", False)
    try:
        replay_interp.interpret(compile_source(source))
    except Exception:
        if not expect_abort:
            raise

    replay_snapshot = replay_interp.snapshot()
    actual_history = list(replay_interp.execution_history[:replay_interp.replay_cursor])
    drift = _diff_events(expected_history, actual_history)
    actual_final_hash = replay_interp.compute_history_hash()

    expected_sanity = manifest["final"].get("state_sanity") or state_sanity_from_snapshot(expected_snapshot)
    actual_sanity = state_sanity_from_snapshot(replay_snapshot)
    if actual_sanity != expected_sanity:
        drift += 1
    if drift != 0:
        raise DeterministicReplayError(
            f"integrate golden replay drift detected: drift={drift}, "
            f"expected_hash={final_hash}, actual_hash={actual_final_hash}"
        )

    return ReplayResult(
        artifact_dir=str(root),
        program_hash=actual_hash,
        history_length=len(actual_history),
        final_history_hash=actual_final_hash,
        drift=drift,
        state_sanity=actual_sanity,
    )
