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
from typing import Any, Dict, Iterable, Optional, TYPE_CHECKING

from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.interpreter import Interpreter, RuntimeMode
from synapse.builtins import LLMBackend
if TYPE_CHECKING:
    from synapse.llm import LLMResult
from synapse.runtime.host_abi import HOST_ABI_VERSION
from synapse.runtime.vm_routing import RECORDED_SIDE_EFFECT_HOST_SYMBOLS
from synapse.version import LANGUAGE_VERSION, RUNTIME_VERSION, SPEC_VERSION

SCHEMA_VERSION = 2
DEFAULT_VIRTUAL_CLOCK_START = "2026-01-01T00:00:00Z"
DEFAULT_CLOCK_STEP = 1

# Schema 1 recorded an interpreter snapshot that carried neither the program
# output buffer nor recorded VM states, so these state-sanity fields were
# constant for every artifact and validated nothing. Schema 2 records both.
# Schema 1 artifacts stay replayable: the fields their snapshot could not
# observe are excluded from the comparison instead of being compared against a
# value the artifact never captured.
STATE_SANITY_OBSERVABILITY_SCHEMA = 2
_SCHEMA_2_SANITY_FIELDS = (
    "output_hash",
    "final_ip",
    "stack_depth",
    "call_frame_depth",
    "guard_stack_depth",
    "context_stack_depth",
    "policy_stack_depth",
    "guard_violation_active",
)


def _artifact_schema_version(manifest: Dict[str, Any]) -> int:
    raw = manifest.get("schema_version", 1)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ReplayArtifactError(f"manifest schema_version must be an integer, got {raw!r}")
    if raw < 1 or raw > SCHEMA_VERSION:
        raise ReplayArtifactError(f"unsupported artifact schema_version: {raw}")
    return raw


def comparable_state_sanity(sanity: Dict[str, Any], *, schema_version: int) -> Dict[str, Any]:
    """Project state sanity onto the fields the artifact's schema could observe."""

    if schema_version >= STATE_SANITY_OBSERVABILITY_SCHEMA:
        return dict(sanity)
    return {k: v for k, v in sanity.items() if k not in _SCHEMA_2_SANITY_FIELDS}


class DeterministicReplayError(RuntimeError):
    """Raised when a golden replay artifact cannot be replayed exactly."""


class ArtifactIntegrityError(DeterministicReplayError):
    """The artifact does not hold together: its source or its history chain differs from its manifest.

    Still a determinism failure for every caller that handles one; the CLI
    reports it with the artifact integrity exit code, as ``debug compare``
    reports the same broken chain, instead of the code of a replay that drifted.
    """


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
    """Build a deterministic mock table from recorded llm_call events.

    Every key retains its recorded occurrences in execution order: repeated
    requests with different answers stay distinguishable, so mock replay can
    serve them one by one instead of collapsing them onto the last answer.
    Each occurrence also records its position - the index of its llm_call
    event in the execution history - so the merge with retained thought
    requests can interleave both sources in true execution order.
    """
    cache: Dict[str, Dict[str, Any]] = {}
    for position, event in enumerate(history):
        if event.get("type") != "llm_call":
            continue
        prompt_hash = str(event.get("prompt_hash", ""))
        model = str(event.get("model") or "mock")
        key = f"{prompt_hash}:{model}"
        entry = cache.setdefault(key, {"prompt_hash": prompt_hash, "model": model,
                                       "results": [], "positions": []})
        entry["results"].append(event.get("result"))
        entry["positions"].append(position)
    return cache


def _merge_llm_cache(llm_cache: Dict[str, Dict[str, Any]],
                     thought_history: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Merge retained thought requests into the recorded llm_call table.

    Both sources keep per-key occurrence lists. When the recording carried
    positions - every thought observation the history length it ran at, every
    llm occurrence its llm_call event's index in the same history - the merge
    orders each key's observations by true execution order: a thought step
    that ran while the history held N events precedes the llm_call event
    appended at index N, because that call's backend request happened before
    its append. Artifacts recorded without positions (and backends never
    wired to an interpreter) keep the previous contract: llm_call occurrences
    first, thought occurrences after, each in source order. Legacy entries
    carrying a plain ``result`` are wrapped as a single occurrence.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    for key, value in llm_cache.items():
        results = list(value.get("results") or [value.get("result")])
        positions = list(value.get("positions") or [])
        merged[key] = {"prompt_hash": value.get("prompt_hash"), "model": value.get("model"),
                       "results": results,
                       "positions": [positions[i] if i < len(positions) else None
                                     for i in range(len(results))],
                       "sources": ["llm_call"] * len(results)}
    for key, value in thought_history.items():
        entry = merged.setdefault(key, {"prompt_hash": value.get("prompt_hash"),
                                        "model": value.get("model"), "results": [],
                                        "positions": [], "sources": []})
        results = list(value.get("results") or [value.get("result")])
        positions = list(value.get("positions") or [])
        entry["results"].extend(results)
        entry["positions"].extend(positions[i] if i < len(positions) else None
                                   for i in range(len(results)))
        entry["sources"].extend(["thought"] * len(results))
    for entry in merged.values():
        positions, sources = entry.pop("positions"), entry.pop("sources")
        if positions and all(position is not None for position in positions):
            order = sorted(range(len(entry["results"])),
                           key=lambda i: (positions[i], 0 if sources[i] == "thought" else 1))
            entry["positions"] = [positions[i] for i in order]
        else:
            # No interleaving information: keep the previous contract.
            order = sorted(range(len(entry["results"])),
                           key=lambda i: (0 if sources[i] == "llm_call" else 1, i))
        entry["results"] = [entry["results"][i] for i in order]
    return merged


class CacheOnlyLLMBackend(LLMBackend):
    """LLM backend used by replay --mock.

    It has no provider fallback. Missing cache entries fail deterministically.
    Each key recorded with an occurrence list is served one occurrence per
    request in order, and a request occurring more often than recorded fails
    instead of silently reusing the last answer. A legacy entry carrying a
    plain ``result`` - the format every earlier recorder wrote - is reusable:
    the previous replayer served that one answer to every matching request,
    and an unchanged old artifact must keep replaying exactly as it did.
    """

    def __init__(self, cache: Dict[str, Dict[str, Any]]):
        super().__init__(default_model="mock", provider="mock", mode="mock")
        self.cache: Dict[str, list] = {}
        self.reusable: set = set()
        for key, value in dict(cache).items():
            results = value.get("results")
            if results is None:  # legacy single-result artifact entry
                self.reusable.add(key)
                results = [value.get("result")]
            self.cache[key] = list(results)
        self.provider_call_count = 0

    def _cached_entry(self, prompt: str, model: Optional[str]) -> tuple[str, Any]:
        prompt_hash = hashlib.sha256(str(prompt).encode("utf-8")).hexdigest()
        model_name = str(model or self.default_model)
        key = f"{prompt_hash}:{model_name}"
        occurrences = self.cache.get(key)
        if occurrences is None:
            raise DeterministicReplayError(
                f"LLM_REQUEST unresolved in mock replay: missing cache entry {key}"
            )
        if key in self.reusable:
            return model_name, occurrences[0] if occurrences else None
        if not occurrences:
            raise DeterministicReplayError(
                f"LLM_REQUEST occurred more often than recorded: cache entry {key} is exhausted"
            )
        return model_name, occurrences.pop(0)

    def complete_result(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 100,
        privacy_context: Any = None,
    ) -> LLMResult:
        from synapse.llm import LLMProviderStatus, LLMResult, LLMTokenStatus, LLMUsage

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


class RecordedSideEffects:
    """The values replay --mock serves the recorded nondeterministic builtins (``time``, ``random``, ``uuid``).

    A recording appends every value one of these builtins drew as a
    ``side_effect`` event. Mock replay serves each builtin its recorded values
    in order and never draws a live one: the record is the authority, as the
    determinism contract requires of a recorded value (section 8.2, rule 1) and
    the golden replay contract requires of the clock. A builtin called more
    often than recorded fails like an exhausted LLM cache entry.
    """

    def __init__(self, history: Iterable[dict[str, Any]]):
        self.results: Dict[str, list] = {name: [] for name in sorted(RECORDED_SIDE_EFFECT_HOST_SYMBOLS)}
        for event in history:
            if event.get("type") == "side_effect" and event.get("name") in self.results:
                self.results[event["name"]].append(event.get("result"))

    def registry(self, builtins: Dict[str, Any]) -> Dict[str, Any]:
        """``builtins`` with every recorded builtin served from the record instead of drawn live."""
        served = dict(builtins)
        for name in self.results:
            served[name] = self._served(name)
        return served

    def _served(self, name: str):
        def recorded(*_args: Any) -> Any:
            if not self.results[name]:
                raise DeterministicReplayError(
                    f"SIDE_EFFECT unresolved in mock replay: {name} occurred more often than recorded"
                )
            return self.results[name].pop(0)
        return recorded


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


def _read_artifact_bytes(root: Path, relative: str) -> bytes:
    """Byte-level artifact read that fails closed with a typed error."""
    try:
        return (root / relative).read_bytes()
    except OSError as exc:
        raise ReplayArtifactError(f"artifact file unreadable: {relative}: {exc}") from exc


def _decode_artifact_text(root: Path, relative: str) -> str:
    raw = _read_artifact_bytes(root, relative)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReplayArtifactError(f"artifact file is not valid UTF-8: {relative}: {exc}") from exc


def _read_artifact_json(root: Path, relative: str) -> Any:
    text = _decode_artifact_text(root, relative)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReplayArtifactError(f"artifact file is not valid JSON: {relative}: {exc}") from exc


def _artifact_file_entry(manifest: Dict[str, Any], name: str) -> str:
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ReplayArtifactError("manifest is missing the files map")
    value = files.get(name)
    if not isinstance(value, str) or not value:
        raise ReplayArtifactError(f"manifest files map is missing entry: {name}")
    return value


def _run_interpreter_from_source(source: str, *, llm_backend: Optional[LLMBackend] = None) -> Interpreter:
    interp = Interpreter()
    if llm_backend is not None:
        interp.llm_backend = llm_backend
    interp.source_code = source
    interp.interpret(compile_source(source))
    return interp


def record_source(source: str, output_dir: Path | str, *, source_path: str = "<memory>",
                  layer: str = "strict", virtual_clock_start: str = DEFAULT_VIRTUAL_CLOCK_START,
                  clock_step: int = DEFAULT_CLOCK_STEP,
                  llm_backend: Optional[LLMBackend] = None) -> Dict[str, Any]:
    """Record a golden artifact directory for a source program.

    ``llm_backend`` optionally pins the recording backend (acceptance tests
    script it to make repeated identical prompts answer differently); the
    default keeps the runtime's own backend.
    """
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

    interp = _run_interpreter_from_source(source, llm_backend=llm_backend)
    final_snapshot = interp.snapshot()
    history = list(interp.execution_history)
    final_history_hash = interp.compute_history_hash()
    llm_cache = extract_llm_cache(history)
    # Thought-chain LLM requests are retained by the backend: multi-step
    # reasoning emits no per-step llm_call history events, so the artifact
    # cache is merged with the retained thought requests to keep the
    # record -> replay --mock roundtrip complete. Both sides keep per-key
    # occurrence lists, so repeated identical prompts replay in order.
    thought_history = getattr(interp.llm_backend, "thought_history", None)
    if thought_history:
        llm_cache = _merge_llm_cache(llm_cache, thought_history)

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
            # Identities the run derived from its run id (fallback trace ids of
            # VM and memory events) are reproduced only under the same run id.
            "run_id": interp.run_id,
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
    manifest = _read_artifact_json(root, "manifest.json")
    # Fail closed on an unreadable schema before loading or executing anything
    # the artifact describes. Byte-level reads are guarded the same way: a
    # corrupted artifact must surface as a typed integrity failure, never as
    # an unhandled decode/parse traceback.
    schema_version = _artifact_schema_version(manifest)
    source = _decode_artifact_text(root, _artifact_file_entry(manifest, "source"))
    expected_history = _read_artifact_json(root, _artifact_file_entry(manifest, "history"))
    expected_snapshot = _read_artifact_json(root, _artifact_file_entry(manifest, "vm_snapshot"))
    llm_cache = _read_artifact_json(root, _artifact_file_entry(manifest, "llm_cache"))

    expected_hash = manifest["metadata"]["program_hash"]
    actual_hash = program_hash_for_source(source)
    if actual_hash != expected_hash:
        raise ArtifactIntegrityError(f"program_hash mismatch: expected {expected_hash}, got {actual_hash}")
    if manifest["metadata"].get("host_abi_version") != HOST_ABI_VERSION:
        raise DeterministicReplayError(
            f"host_abi_version mismatch: artifact={manifest['metadata'].get('host_abi_version')} runtime={HOST_ABI_VERSION}"
        )
    final_hash = manifest["final"]["final_history_hash"]
    if not _history_chain_valid(expected_history, final_hash):
        raise ArtifactIntegrityError("execution_history chain broken in artifact")

    # Replay from the recorded initial state. The replay environment uses a
    # cache-only LLM backend, so provider calls are impossible and missing cache
    # entries become deterministic failures. The recorded time/random/uuid
    # values are served the same way: a live draw is impossible.
    initial_snapshot = _read_artifact_json(root, _artifact_file_entry(manifest, "initial_vm_snapshot"))
    replay_interp = Interpreter.restore_snapshot(initial_snapshot)
    recorded_run_id = manifest["metadata"].get("run_id")
    if recorded_run_id is not None:
        if not isinstance(recorded_run_id, str) or not recorded_run_id:
            raise ReplayArtifactError("manifest metadata run_id must be a non-empty string")
        # Artifacts recorded before the run id was kept replay under a fresh one, as they always did.
        replay_interp.run_id = recorded_run_id
    replay_interp.source_code = source
    replay_interp.llm_backend = CacheOnlyLLMBackend(llm_cache)
    replay_interp.runtime.replay.builtins = RecordedSideEffects(expected_history).registry(
        replay_interp.runtime.replay.builtins
    )
    replay_interp.interpret(compile_source(source))
    replay_snapshot = replay_interp.snapshot()

    actual_history = replay_interp.execution_history
    drift = _diff_events(expected_history, actual_history)
    actual_final_hash = replay_interp.compute_history_hash()
    if actual_final_hash != final_hash:
        drift += 1
    expected_sanity = manifest["final"].get("state_sanity") or state_sanity_from_snapshot(expected_snapshot)
    actual_sanity = state_sanity_from_snapshot(replay_snapshot)
    if comparable_state_sanity(actual_sanity, schema_version=schema_version) != comparable_state_sanity(
        expected_sanity, schema_version=schema_version
    ):
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
    # Same thought-request merge as the strict record path: the integrate
    # artifact must replay multi-step reasoning from its cache as well.
    thought_history = getattr(interp.llm_backend, "thought_history", None)
    if thought_history:
        llm_cache = _merge_llm_cache(llm_cache, thought_history)

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
    manifest = _read_artifact_json(root, "manifest.json")
    # Fail closed on an unreadable schema before loading or executing anything
    # the artifact describes. Byte-level reads share the typed integrity
    # guards of the strict replay path.
    schema_version = _artifact_schema_version(manifest)
    source = _decode_artifact_text(root, _artifact_file_entry(manifest, "source"))
    expected_history = _read_artifact_json(root, _artifact_file_entry(manifest, "history"))
    expected_snapshot = _read_artifact_json(root, _artifact_file_entry(manifest, "vm_snapshot"))
    llm_cache = _read_artifact_json(root, _artifact_file_entry(manifest, "llm_cache"))

    expected_hash = manifest["metadata"]["program_hash"]
    actual_hash = program_hash_for_source(source)
    if actual_hash != expected_hash:
        raise ArtifactIntegrityError(
            f"program_hash mismatch: expected {expected_hash}, got {actual_hash}"
        )
    if manifest["metadata"].get("host_abi_version") != HOST_ABI_VERSION:
        raise DeterministicReplayError(
            f"host_abi_version mismatch: artifact={manifest['metadata'].get('host_abi_version')} runtime={HOST_ABI_VERSION}"
        )
    final_hash = manifest["final"]["final_history_hash"]
    if final_hash and not _history_chain_valid(expected_history, final_hash):
        raise ArtifactIntegrityError("execution_history chain broken in artifact")

    initial_snapshot = _read_artifact_json(root, _artifact_file_entry(manifest, "initial_vm_snapshot"))
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
    if comparable_state_sanity(actual_sanity, schema_version=schema_version) != comparable_state_sanity(
        expected_sanity, schema_version=schema_version
    ):
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
