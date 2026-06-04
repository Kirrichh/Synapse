"""Alpha3f Time-Travel Debugger core primitives.

Patch 1 intentionally stays outside the CognitiveVM execution core. It provides
identity, fork registry, copy-on-write overlay utilities, a lightweight
ForkedVMState adapter, and deterministic replay policy helpers. It does not add
opcodes, parser syntax, CLI commands, or mutate VMState globally.
"""
from __future__ import annotations

import copy
import hashlib
from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Protocol, Sequence, runtime_checkable

from .golden_replay import (
    DeterministicReplayError,
    ReplayArtifactError,
    _history_chain_valid,
    _read_json,
)

REPLAY_DETERMINISTIC = "deterministic-replay"
REPLAY_EXPLORATORY_LIVE = "exploratory-live"
FORK_STATUS_ACTIVE = "active"
FORK_STATUS_DISPOSED = "disposed"
FORK_STATUS_COMPLETED = "completed"
FORK_STATUS_FAILED = "failed"

_ALLOWED_MODES = {REPLAY_DETERMINISTIC, REPLAY_EXPLORATORY_LIVE}
_TERMINAL_STATUSES = {FORK_STATUS_DISPOSED, FORK_STATUS_COMPLETED, FORK_STATUS_FAILED}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ForkRecord:
    """Immutable identity record for a debugger fork lineage.

    Fork identity is deliberately independent from VMState. A fork must always
    point at a parent history hash; creating a fork without lineage is invalid.
    """

    fork_id: str
    parent_history_hash: str
    mode: str = REPLAY_DETERMINISTIC
    parent_fork_id: Optional[str] = None
    status: str = FORK_STATUS_ACTIVE
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.fork_id or not isinstance(self.fork_id, str):
            raise ValueError("fork_id is required")
        if not self.parent_history_hash or not isinstance(self.parent_history_hash, str):
            raise ValueError("parent_history_hash is required")
        if self.mode not in _ALLOWED_MODES:
            raise ValueError(f"unsupported fork mode: {self.mode}")

    @classmethod
    def from_golden(
        cls,
        *,
        fork_id: str,
        final_history_hash: str,
        mode: str = REPLAY_DETERMINISTIC,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "ForkRecord":
        """Create a fork identity from an immutable golden artifact baseline."""
        return cls(
            fork_id=fork_id,
            parent_history_hash=final_history_hash,
            mode=mode,
            parent_fork_id=None,
            metadata={"source": "golden", **(metadata or {})},
        )

    def with_status(self, status: str) -> "ForkRecord":
        if status not in {FORK_STATUS_ACTIVE, *_TERMINAL_STATUSES}:
            raise ValueError(f"unsupported fork status: {status}")
        return ForkRecord(
            fork_id=self.fork_id,
            parent_history_hash=self.parent_history_hash,
            mode=self.mode,
            parent_fork_id=self.parent_fork_id,
            status=status,
            metadata=dict(self.metadata),
        )


class ForkRegistry:
    """Debugger-owned fork lifecycle registry.

    This is an instrumentation-layer registry, not a VM opcode surface. Fork ids
    are deterministic within the registry and do not depend on wall-clock time.
    """

    def __init__(self, *, max_active_forks: Optional[int] = None) -> None:
        self.max_active_forks = max_active_forks
        self._counter = 0
        self._records: Dict[str, ForkRecord] = {}
        self._resources: Dict[str, list[Any]] = {}

    def _next_fork_id(self, parent_history_hash: str, mode: str) -> str:
        self._counter += 1
        digest = _sha256_text(f"{self._counter}|{parent_history_hash}|{mode}")[:16]
        return f"fork-{digest}"

    def _active_count(self) -> int:
        return sum(1 for record in self._records.values() if record.status == FORK_STATUS_ACTIVE)

    def create_fork(
        self,
        *,
        parent_history_hash: str,
        mode: str = REPLAY_DETERMINISTIC,
        parent_fork_id: Optional[str] = None,
        fork_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ForkRecord:
        if self.max_active_forks is not None and self._active_count() >= self.max_active_forks:
            raise ForkResourceLimitError(
                f"max_active_forks exceeded: {self.max_active_forks}"
            )
        if parent_fork_id is not None and parent_fork_id not in self._records:
            raise KeyError(f"unknown parent_fork_id: {parent_fork_id}")
        chosen_id = fork_id or self._next_fork_id(parent_history_hash, mode)
        if chosen_id in self._records:
            raise ValueError(f"duplicate fork_id: {chosen_id}")
        record = ForkRecord(
            fork_id=chosen_id,
            parent_history_hash=parent_history_hash,
            mode=mode,
            parent_fork_id=parent_fork_id,
            metadata=dict(metadata or {}),
        )
        self._records[record.fork_id] = record
        return record

    def create_fork_from_artifact(
        self,
        adapter: "GoldenArtifactTraceAdapter",
        *,
        mode: str = REPLAY_DETERMINISTIC,
        fork_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ForkRecord:
        """Open a debug fork anchored to a verified golden artifact baseline.

        The fork's parent_history_hash is bound to the artifact's
        final_history_hash, giving an unbroken forensic trail from the recorded
        golden baseline to the debug lineage. The artifact itself is immutable
        and unmodified; the fork only references its final hash.
        """
        if not adapter.final_history_hash:
            raise ReplayArtifactError(
                "cannot fork from artifact without final_history_hash"
            )
        fork_metadata = {
            "source": "golden_artifact",
            "artifact_dir": adapter.artifact_dir,
            "program_hash": adapter.program_hash,
            **(metadata or {}),
        }
        return self.create_fork(
            parent_history_hash=adapter.final_history_hash,
            mode=mode,
            fork_id=fork_id,
            metadata=fork_metadata,
        )

    def get(self, fork_id: str) -> ForkRecord:
        return self._records[fork_id]

    def require_active(self, fork_id: str) -> ForkRecord:
        record = self.get(fork_id)
        if record.status == FORK_STATUS_DISPOSED:
            raise ForkDisposedError(f"fork is disposed: {fork_id}")
        if record.status in {FORK_STATUS_COMPLETED, FORK_STATUS_FAILED}:
            raise ForkLifecycleError(f"fork is not active: {fork_id} status={record.status}")
        return record

    def attach_resource(self, fork_id: str, resource: Any) -> None:
        """Attach debugger-owned fork-local resources for deterministic disposal.

        Resources may expose ``clear_overlays()``, ``clear_overlay()``, or ``clear()``.
        The registry does not own VMState itself; it only releases fork-local
        adapter/overlay resources explicitly attached by the debugger layer.
        """
        self.require_active(fork_id)
        self._resources.setdefault(fork_id, []).append(resource)

    def _clear_resources(self, fork_id: str) -> None:
        for resource in self._resources.pop(fork_id, []):
            if hasattr(resource, "clear_overlays"):
                resource.clear_overlays()
            elif hasattr(resource, "clear_overlay"):
                resource.clear_overlay()
            elif hasattr(resource, "clear"):
                resource.clear()

    def transition(self, fork_id: str, status: str) -> ForkRecord:
        if status not in _TERMINAL_STATUSES:
            raise ForkLifecycleError(f"unsupported terminal status: {status}")
        record = self.get(fork_id)
        if record.status == FORK_STATUS_DISPOSED:
            raise ForkDisposedError(f"fork is disposed: {fork_id}")
        if record.status != FORK_STATUS_ACTIVE:
            raise ForkLifecycleError(
                f"invalid fork transition: {record.status} -> {status}"
            )
        if status == FORK_STATUS_DISPOSED:
            self._clear_resources(fork_id)
        record = record.with_status(status)
        self._records[fork_id] = record
        return record

    def dispose(self, fork_id: str) -> ForkRecord:
        return self.transition(fork_id, FORK_STATUS_DISPOSED)

    def complete(self, fork_id: str) -> ForkRecord:
        return self.transition(fork_id, FORK_STATUS_COMPLETED)

    def fail(self, fork_id: str) -> ForkRecord:
        return self.transition(fork_id, FORK_STATUS_FAILED)

    def active(self) -> list[ForkRecord]:
        return [record for record in self._records.values() if record.status == FORK_STATUS_ACTIVE]

    def all(self) -> list[ForkRecord]:
        return list(self._records.values())


class ForkResourceLimitError(RuntimeError):
    """Raised when debugger fork resource limits are exceeded deterministically."""


class ForkDisposedError(RuntimeError):
    """Raised when an operation targets a disposed debugger fork."""


class ForkLifecycleError(RuntimeError):
    """Raised when a fork lifecycle transition is invalid."""


class GovernanceViolationError(RuntimeError):
    """Raised when debugger event injection would bypass governance boundaries."""


@runtime_checkable
class TraceContextProtocol(Protocol):
    """Minimal structural trace cursor contract for deterministic replay.

    The trace context does not own or mutate execution history. Implementations
    expose only a read cursor over already-recorded events. Production code must
    rely on structural behavior, not concrete trace runtime classes.
    """

    def next_expected_event(self) -> Mapping[str, Any] | None:
        """Return the next expected event, or ``None`` at end-of-trace."""

    def consume_expected_event(self) -> None:
        """Advance the trace cursor after a successful deterministic match."""


class ReplayRuntimeStub:
    """In-memory reference implementation of TraceContextProtocol.

    The stub stores a defensive immutable tuple of event mappings. The only
    mutable state is the integer cursor. It performs no I/O, owns no fork
    lifecycle, and never mutates parent execution history.
    """

    def __init__(self, history: Sequence[Mapping[str, Any]]) -> None:
        immutable_history: list[Mapping[str, Any]] = []
        for index, event in enumerate(history):
            if not isinstance(event, Mapping):
                raise DeterministicReplayError(
                    f"malformed trace event at index {index}: expected mapping"
                )
            # Copy the top-level mapping so parent history cannot be mutated by
            # cursor consumption or by edits to the caller-owned event object.
            immutable_history.append(dict(event))
        self._execution_history: tuple[Mapping[str, Any], ...] = tuple(immutable_history)
        self._cursor = 0

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def execution_history(self) -> tuple[Mapping[str, Any], ...]:
        return self._execution_history

    def next_expected_event(self) -> Mapping[str, Any] | None:
        if self._cursor >= len(self._execution_history):
            return None
        return self._execution_history[self._cursor]

    def consume_expected_event(self) -> None:
        if self._cursor >= len(self._execution_history):
            raise DeterministicReplayError(
                "cannot consume expected event: end of deterministic trace"
            )
        self._cursor += 1


# --- Alpha3f P5: Golden Artifact → TraceContext bridge ---------------------

# Files an adapter needs from a golden artifact directory. vm_snapshot and
# llm_cache are part of the artifact but not required by the trace cursor
# itself (they belong to the replay runner layer, P7).
_REQUIRED_ARTIFACT_FILES = ("manifest.json",)


class GoldenArtifactTraceAdapter:
    """Read-only TraceContextProtocol over a recorded golden artifact directory.

    This is the production bridge that ReplayRuntimeStub only stubbed. It loads
    a real golden artifact (as produced by ``golden_replay.record_source``) and
    exposes its recorded ``execution_history`` through the cursor contract.

    Design contract (Alpha3f P5):
      - Strictly read-only. The on-disk artifact is never modified.
      - In-memory history is a defensive immutable tuple; only the integer
        cursor is mutable. Parent history cannot be polluted by consumption.
      - Fails fast at construction time:
          * missing/unreadable manifest or history → ReplayArtifactError
          * malformed history (not a list of mappings) → ReplayArtifactError
          * broken history_hash chain vs manifest.final_history_hash
            → DeterministicReplayError
      - Does NOT call providers, does NOT execute the VM, does NOT persist
        sessions. It is a pure cursor over already-recorded events.

    The adapter intentionally does not verify program_hash against live source
    (that is the replay runner's job in deterministic-replay execution). It only
    guarantees the artifact's own history is internally consistent, so that a
    debug fork built on top of it starts from a verified baseline.
    """

    def __init__(self, artifact_dir: "Path | str", *, verify_chain: bool = True) -> None:
        from pathlib import Path

        root = Path(artifact_dir)
        if not root.exists() or not root.is_dir():
            raise ReplayArtifactError(f"artifact directory not found: {root}")

        manifest_path = root / "manifest.json"
        if not manifest_path.exists():
            raise ReplayArtifactError(f"missing manifest.json in artifact: {root}")

        try:
            manifest = _read_json(manifest_path)
        except Exception as exc:  # malformed JSON
            raise ReplayArtifactError(f"unreadable manifest.json: {exc}") from exc

        if not isinstance(manifest, Mapping):
            raise ReplayArtifactError("manifest.json must be a JSON object")

        files = manifest.get("files")
        if not isinstance(files, Mapping) or "history" not in files:
            raise ReplayArtifactError("manifest.files.history is missing")

        history_path = root / str(files["history"])
        if not history_path.exists():
            raise ReplayArtifactError(f"missing history file: {history_path}")

        try:
            history = _read_json(history_path)
        except Exception as exc:
            raise ReplayArtifactError(f"unreadable history.json: {exc}") from exc

        if not isinstance(history, list):
            raise ReplayArtifactError("history.json must be a JSON array of events")

        immutable_history: list[Mapping[str, Any]] = []
        for index, event in enumerate(history):
            if not isinstance(event, Mapping):
                raise ReplayArtifactError(
                    f"malformed history event at index {index}: expected object"
                )
            immutable_history.append(dict(event))

        # Capture identity from manifest for forensic continuity.
        metadata = manifest.get("metadata") or {}
        final = manifest.get("final") or {}
        self._program_hash = str(metadata.get("program_hash", ""))
        self._host_abi_version = str(metadata.get("host_abi_version", ""))
        self._final_history_hash = str(final.get("final_history_hash", ""))

        # Verify chain integrity against the manifest's recorded final hash.
        # A debug fork must start from a verified baseline; a broken chain is a
        # determinism failure, not merely a malformed input.
        if verify_chain and self._final_history_hash:
            if not _history_chain_valid(list(immutable_history), self._final_history_hash):
                raise DeterministicReplayError(
                    f"artifact history chain broken: recomputed hash does not match "
                    f"manifest.final_history_hash ({self._final_history_hash})"
                )

        self._artifact_dir = str(root)
        self._manifest = dict(manifest)
        self._execution_history: tuple[Mapping[str, Any], ...] = tuple(immutable_history)
        self._cursor = 0

    # --- identity (read-only) ---

    @property
    def artifact_dir(self) -> str:
        return self._artifact_dir

    @property
    def program_hash(self) -> str:
        return self._program_hash

    @property
    def host_abi_version(self) -> str:
        return self._host_abi_version

    @property
    def final_history_hash(self) -> str:
        return self._final_history_hash

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def execution_history(self) -> tuple[Mapping[str, Any], ...]:
        return self._execution_history

    # --- TraceContextProtocol ---

    def next_expected_event(self) -> Mapping[str, Any] | None:
        if self._cursor >= len(self._execution_history):
            return None
        # Defensive copy: a caller mutating the returned event must not corrupt
        # the adapter's internal immutable view or any other reader.
        return dict(self._execution_history[self._cursor])

    def consume_expected_event(self) -> None:
        if self._cursor >= len(self._execution_history):
            raise DeterministicReplayError(
                "cannot consume expected event: end of golden artifact trace"
            )
        self._cursor += 1


# --- Alpha3f P6: Trace divergence detection -------------------------------

# Divergence reasons. history_hash is the primary forensic key; payload
# differences that do not change history_hash are NOT divergences.
DIVERGENCE_EQUAL = "equal"
DIVERGENCE_HASH_MISMATCH = "hash_mismatch"
DIVERGENCE_TYPE_MISMATCH = "type_mismatch"
DIVERGENCE_LENGTH_MISMATCH = "length_mismatch"


@dataclass(frozen=True)
class TraceDivergenceResult:
    """Read-only structured result of comparing two trace histories.

    Primary divergence key is ``history_hash`` (the forensic chain). Two events
    with equal history_hash are considered equivalent for trace divergence even
    if their payloads carry additional fields. event type is a secondary
    diagnostic only.
    """

    equal: bool
    reason: str
    first_divergence_index: Optional[int] = None
    left_event: Optional[Mapping[str, Any]] = None
    right_event: Optional[Mapping[str, Any]] = None
    left_history_hash: Optional[str] = None
    right_history_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "equal": self.equal,
            "reason": self.reason,
            "first_divergence_index": self.first_divergence_index,
            "left_event": dict(self.left_event) if self.left_event is not None else None,
            "right_event": dict(self.right_event) if self.right_event is not None else None,
            "left_history_hash": self.left_history_hash,
            "right_history_hash": self.right_history_hash,
        }


def history_from_context(source: Any) -> tuple[Mapping[str, Any], ...]:
    """Extract an immutable history snapshot from a trace source WITHOUT
    consuming any cursor.

    Accepts:
      - GoldenArtifactTraceAdapter / ReplayRuntimeStub (uses .execution_history)
      - a plain Sequence[Mapping] (list/tuple of event dicts)

    Never calls next_expected_event()/consume_expected_event(), so the source's
    cursor is left untouched. compare is strictly read-only.
    """
    if hasattr(source, "execution_history"):
        events = source.execution_history
    elif isinstance(source, (list, tuple)):
        events = source
    else:
        raise TypeError(
            "history_from_context requires a trace adapter/stub or a "
            f"Sequence[Mapping], got {type(source).__name__}"
        )
    snapshot: list[Mapping[str, Any]] = []
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            raise ReplayArtifactError(
                f"malformed trace event at index {index}: expected mapping"
            )
        snapshot.append(dict(event))
    return tuple(snapshot)


def find_trace_divergence(left: Any, right: Any) -> TraceDivergenceResult:
    """Find the first point where two traces diverge, by derived chain hash.

    Read-only: works over immutable history snapshots, never advances any
    TraceContext cursor. Accepts trace adapters/stubs or raw event sequences.

    The forensic key is the per-event tamper-evident chain hash, derived from
    the raw events via the same hash_event_chain used by the replay engine.
    Raw golden events do not carry a per-event history_hash field; the chain is
    computed over the full event stream so each position's hash also reflects
    all preceding events (true forensic divergence, not just local payload).

    Comparison rules, in order at each index:
      1. Both events present:
         - if derived chain hash differs → hash_mismatch divergence
         - if chain hash equal but event 'type' differs → type_mismatch
           (secondary diagnostic; should not happen under a sound chain)
         - else: events equivalent, advance
      2. One sequence shorter → length_mismatch at the first missing index
      3. Both exhausted with no divergence → equal

    A pre-derived per-event "history_hash" field, if present on every event, is
    honored as an override (used by synthetic unit-test traces). When any event
    in a sequence lacks it, the chain is derived instead.
    """
    from .hardening import hash_event_chain

    left_history = history_from_context(left)
    right_history = history_from_context(right)

    left_hashes = _trace_hashes(left_history, hash_event_chain)
    right_hashes = _trace_hashes(right_history, hash_event_chain)

    n = min(len(left_history), len(right_history))
    for index in range(n):
        ev_a = left_history[index]
        ev_b = right_history[index]
        hash_a = left_hashes[index]
        hash_b = right_hashes[index]

        if hash_a != hash_b:
            return TraceDivergenceResult(
                equal=False,
                reason=DIVERGENCE_HASH_MISMATCH,
                first_divergence_index=index,
                left_event=ev_a,
                right_event=ev_b,
                left_history_hash=hash_a,
                right_history_hash=hash_b,
            )

        if ev_a.get("type") != ev_b.get("type"):
            return TraceDivergenceResult(
                equal=False,
                reason=DIVERGENCE_TYPE_MISMATCH,
                first_divergence_index=index,
                left_event=ev_a,
                right_event=ev_b,
                left_history_hash=hash_a,
                right_history_hash=hash_b,
            )

    if len(left_history) != len(right_history):
        index = n
        ev_a = left_history[index] if index < len(left_history) else None
        ev_b = right_history[index] if index < len(right_history) else None
        return TraceDivergenceResult(
            equal=False,
            reason=DIVERGENCE_LENGTH_MISMATCH,
            first_divergence_index=index,
            left_event=ev_a,
            right_event=ev_b,
            left_history_hash=left_hashes[index] if ev_a is not None else None,
            right_history_hash=right_hashes[index] if ev_b is not None else None,
        )

    return TraceDivergenceResult(equal=True, reason=DIVERGENCE_EQUAL)


def _trace_hashes(history: tuple[Mapping[str, Any], ...], hash_event_chain) -> list[str]:
    """Per-event forensic hashes for a history.

    If every event already carries a "history_hash" field (synthetic unit-test
    traces), those are used directly. Otherwise the tamper-evident chain is
    derived from the raw events, so each hash reflects all preceding events.
    """
    if history and all("history_hash" in ev for ev in history):
        return [str(ev["history_hash"]) for ev in history]
    chain = hash_event_chain(list(history))
    return [str(entry["hash"]) for entry in chain]


_DELETED = object()


def _is_mutable_value(value: Any) -> bool:
    return isinstance(value, (dict, list, set))


class OverlayMap(MutableMapping[str, Any]):
    """Copy-on-write overlay map with a top-level write barrier.

    Reads cascade overlay -> parent. Writes go only to the overlay. When a
    mutable value is read from the parent, it is materialized into the overlay
    via deepcopy before being returned, preventing nested mutations in a child
    fork from polluting the parent view.
    """

    def __init__(
        self,
        parent: Optional[Mapping[str, Any]] = None,
        overlay: Optional[Dict[str, Any]] = None,
        *,
        clone: Callable[[Any], Any] = copy.deepcopy,
    ) -> None:
        self._parent: Mapping[str, Any] = parent or {}
        self._overlay: Dict[str, Any] = overlay if overlay is not None else {}
        self._clone = clone

    @property
    def parent(self) -> Mapping[str, Any]:
        return self._parent

    @property
    def overlay(self) -> Dict[str, Any]:
        return self._overlay

    def __getitem__(self, key: str) -> Any:
        if key in self._overlay:
            value = self._overlay[key]
            if value is _DELETED:
                raise KeyError(key)
            return value
        if key not in self._parent:
            raise KeyError(key)
        value = self._parent[key]
        if _is_mutable_value(value):
            value = self._clone(value)
            self._overlay[key] = value
        return value

    def __setitem__(self, key: str, value: Any) -> None:
        self._overlay[key] = value

    def __delitem__(self, key: str) -> None:
        if key in self._overlay:
            del self._overlay[key]
            if key not in self._parent:
                return
        if key in self._parent:
            self._overlay[key] = _DELETED
            return
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        seen: set[str] = set()
        for key, value in self._overlay.items():
            seen.add(key)
            if value is not _DELETED:
                yield key
        for key in self._parent.keys():
            if key not in seen:
                yield key

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        if key in self._overlay:
            return self._overlay[key] is not _DELETED
        return key in self._parent

    def snapshot(self) -> Dict[str, Any]:
        """Materialize the current merged view as a plain dict."""
        return {key: self[key] for key in self}

    def overlay_delta(self, *, include_tombstones: bool = False) -> Dict[str, Any]:
        """Return fork-local changes only."""
        delta: Dict[str, Any] = {}
        for key, value in self._overlay.items():
            if value is _DELETED:
                if include_tombstones:
                    delta[key] = {"__deleted__": True}
                continue
            delta[key] = value
        return delta

    def clear_overlay(self) -> None:
        """Release fork-local overlay entries without touching the parent view."""
        self._overlay.clear()


@dataclass
class ForkedVMState:
    """Adapter that provides fork-local mutable views over a base VMState.

    The base VMState is not modified. This adapter is the boundary for debugger
    state isolation and intentionally avoids changing the CognitiveVM core.
    """

    base_state: Any
    fork_record: ForkRecord
    locals: OverlayMap
    memory: OverlayMap
    stack: list[Any]
    guard_stack: tuple[Any, ...]
    context_stack: list[Any]
    policy_stack: list[Any]
    guard_violation_active: bool

    @classmethod
    def from_vm_state(
        cls,
        base_state: Any,
        fork_record: ForkRecord,
        *,
        parent_memory: Optional[Mapping[str, Any]] = None,
    ) -> "ForkedVMState":
        return cls(
            base_state=base_state,
            fork_record=fork_record,
            locals=OverlayMap(getattr(base_state, "locals", {}) or {}),
            memory=OverlayMap(parent_memory or getattr(base_state, "memory", {}) or {}),
            stack=list(getattr(base_state, "stack", []) or []),
            guard_stack=tuple(getattr(base_state, "guard_stack", []) or []),
            context_stack=list(getattr(base_state, "context_stack", []) or []),
            policy_stack=list(getattr(base_state, "policy_stack", []) or []),
            guard_violation_active=bool(getattr(base_state, "guard_violation_active", False)),
        )

    def append_guard_enter(self, guard_hash: str, *, policy_hash: Optional[str] = None) -> Dict[str, Any]:
        """Append a fork-local guard frame without mutating the parent stack.

        Existing parent frames remain a read-only tuple captured at fork time;
        exploratory guard evaluation paths append only to the fork-local view.
        """
        if not guard_hash:
            raise GovernanceViolationError("guard_hash is required for GUARD_ENTER")
        frame = {
            "guard_hash": guard_hash,
            "policy_hash": policy_hash,
            "scope": "fork-local",
        }
        self.guard_stack = (*self.guard_stack, frame)
        return frame

    def clear_overlays(self) -> None:
        """Release fork-local overlays/resources without touching base VMState."""
        self.locals.clear_overlay()
        self.memory.clear_overlay()
        self.stack.clear()
        self.context_stack.clear()
        self.policy_stack.clear()
        self.guard_stack = tuple(getattr(self.base_state, "guard_stack", []) or [])

    def state_delta(self) -> Dict[str, Any]:
        return {
            "fork_id": self.fork_record.fork_id,
            "locals_overlay": self.locals.overlay_delta(include_tombstones=True),
            "memory_overlay": self.memory.overlay_delta(include_tombstones=True),
            "stack": list(self.stack),
            "guard_stack_depth": len(self.guard_stack),
            "context_stack_depth": len(self.context_stack),
            "policy_stack_depth": len(self.policy_stack),
            "guard_violation_active": self.guard_violation_active,
        }


class DeterministicReplayPolicy:
    """Small helper enforcing no-live-fallback replay semantics."""

    def __init__(self, *, llm_cache: Optional[Mapping[str, Any]] = None,
                 host_events: Optional[Iterable[Mapping[str, Any]]] = None) -> None:
        self.llm_cache = dict(llm_cache or {})
        self.host_events = list(host_events or [])
        self._event_index = 0

    def resolve_llm(self, content_key: str) -> Any:
        if content_key not in self.llm_cache:
            raise DeterministicReplayError(
                f"LLM_REQUEST unresolved in deterministic replay: missing cache entry {content_key}"
            )
        return self.llm_cache[content_key]

    def consume_host_event(self, *, event_type: str, symbol: Optional[str] = None) -> Mapping[str, Any]:
        if self._event_index >= len(self.host_events):
            raise DeterministicReplayError(
                f"missing recorded host event: type={event_type} symbol={symbol}"
            )
        event = self.host_events[self._event_index]
        if event.get("type") != event_type:
            raise DeterministicReplayError(
                f"host event type mismatch: expected={event_type} got={event.get('type')}"
            )
        if symbol is not None and event.get("symbol") != symbol:
            raise DeterministicReplayError(
                f"host event symbol mismatch: expected={symbol} got={event.get('symbol')}"
            )
        self._event_index += 1
        return event

@dataclass(frozen=True)
class ValidationResult:
    """Result returned by EventInjectionValidator for allowed injections."""

    allowed: bool
    event_type: str
    fork_id: str
    sanitized_payload: Dict[str, Any]
    reason: Optional[str] = None


INJECTION_POLICY_MATRIX: Dict[str, Optional[Dict[str, Any]]] = {
    "GUARD_ENTER": {
        REPLAY_DETERMINISTIC: "recorded-only",
        REPLAY_EXPLORATORY_LIVE: "new-or-recorded",
        "requires_scope": "fork-local",
        "requires_guard_hash": True,
    },
    "ACTOR_MESSAGE": {
        REPLAY_DETERMINISTIC: False,
        REPLAY_EXPLORATORY_LIVE: True,
        "requires_scope": "fork-local",
    },
    "AFFECTIVE_EVENT": {
        REPLAY_DETERMINISTIC: False,
        REPLAY_EXPLORATORY_LIVE: True,
        "requires_scope": "fork-local",
    },
    "GUARD_VERDICT_OVERRIDE": None,
    "GUARD_VIOLATION_ACK": None,
    "CAPABILITY_GRANT": None,
    "PROGRAM_HASH_REWRITE": None,
    "HISTORY_HASH_REWRITE": None,
}

_FORBIDDEN_PAYLOAD_KEYS = {
    "mode",
    "fork_mode",
    "capability_grant",
    "program_hash",
    "history_hash",
    "GUARD_VERDICT_OVERRIDE",
    "GUARD_VIOLATION_ACK",
}


def _trace_next_event(trace_context: Optional[TraceContextProtocol]) -> Optional[Mapping[str, Any]]:
    if trace_context is None:
        return None
    if hasattr(trace_context, "next_expected_event"):
        event = trace_context.next_expected_event()
        if event is None:
            return None
        if not isinstance(event, Mapping):
            raise DeterministicReplayError(
                "malformed trace event: next_expected_event() must return mapping or None"
            )
        return event
    if isinstance(trace_context, Mapping):
        event = trace_context.get("next_event")
        if event is None:
            return None
        if isinstance(event, Mapping):
            return event
        raise DeterministicReplayError(
            "malformed trace event: next_event must be mapping or None"
        )
    return None


def _trace_consume_event(trace_context: Optional[TraceContextProtocol]) -> None:
    if trace_context is None:
        return
    if hasattr(trace_context, "consume_expected_event"):
        trace_context.consume_expected_event()


class EventInjectionValidator:
    """Validate synthetic debugger event injection against RFC-approved policy.

    The fork mode is always resolved from ForkRegistry. Client-supplied mode
    fields are forbidden and treated as governance violations.
    """

    def __init__(self, registry: ForkRegistry, *,
                 policy_matrix: Optional[Mapping[str, Optional[Mapping[str, Any]]]] = None) -> None:
        self.registry = registry
        self.policy_matrix = dict(policy_matrix or INJECTION_POLICY_MATRIX)

    def validate_injection(
        self,
        *,
        fork_id: str,
        event_type: str,
        payload: Optional[Mapping[str, Any]] = None,
        trace_context: Optional[TraceContextProtocol] = None,
    ) -> ValidationResult:
        record = self.registry.require_active(fork_id)
        sanitized = dict(payload or {})
        self._reject_client_mode_or_forbidden_payload_keys(sanitized)

        if event_type not in self.policy_matrix:
            raise GovernanceViolationError(f"unknown injection event type: {event_type}")
        policy = self.policy_matrix[event_type]
        if policy is None:
            raise GovernanceViolationError(f"forbidden injection event type: {event_type}")

        if event_type == "GUARD_ENTER":
            return self._validate_guard_enter(record, sanitized, trace_context)

        return self._validate_simple_fork_local_event(record, event_type, sanitized, policy)

    def _reject_client_mode_or_forbidden_payload_keys(self, payload: Mapping[str, Any]) -> None:
        for key in _FORBIDDEN_PAYLOAD_KEYS:
            if key in payload:
                raise GovernanceViolationError(f"forbidden payload key: {key}")

    def _require_fork_local_scope(self, payload: Mapping[str, Any], *, event_type: str) -> None:
        if payload.get("scope") != "fork-local":
            raise GovernanceViolationError(
                f"{event_type} injection requires scope='fork-local'"
            )

    def _validate_guard_enter(
        self,
        record: ForkRecord,
        payload: Dict[str, Any],
        trace_context: Optional[TraceContextProtocol],
    ) -> ValidationResult:
        guard_hash = payload.get("guard_hash")
        if not guard_hash:
            raise GovernanceViolationError("GUARD_ENTER injection requires guard_hash")

        if record.mode == REPLAY_DETERMINISTIC:
            expected = _trace_next_event(trace_context)
            if expected is None:
                raise DeterministicReplayError(
                    "New guard path forbidden in deterministic replay: missing recorded GUARD_ENTER"
                )
            if expected.get("type") != "GUARD_ENTER" or expected.get("guard_hash") != guard_hash:
                raise DeterministicReplayError(
                    "New guard path forbidden in deterministic replay: GUARD_ENTER mismatch"
                )
            _trace_consume_event(trace_context)
            return ValidationResult(
                allowed=True,
                event_type="GUARD_ENTER",
                fork_id=record.fork_id,
                sanitized_payload={"guard_hash": guard_hash},
                reason="recorded guard replay",
            )

        if record.mode == REPLAY_EXPLORATORY_LIVE:
            self._require_fork_local_scope(payload, event_type="GUARD_ENTER")
            return ValidationResult(
                allowed=True,
                event_type="GUARD_ENTER",
                fork_id=record.fork_id,
                sanitized_payload={
                    "guard_hash": guard_hash,
                    "scope": "fork-local",
                    **({"policy_hash": payload["policy_hash"]} if "policy_hash" in payload else {}),
                },
                reason="fork-local guard evaluation path",
            )

        raise GovernanceViolationError(f"unsupported fork mode: {record.mode}")

    def _validate_simple_fork_local_event(
        self,
        record: ForkRecord,
        event_type: str,
        payload: Dict[str, Any],
        policy: Mapping[str, Any],
    ) -> ValidationResult:
        if not policy.get(record.mode, False):
            raise DeterministicReplayError(
                f"{event_type} injection is not allowed in {record.mode}"
            )
        self._require_fork_local_scope(payload, event_type=event_type)
        return ValidationResult(
            allowed=True,
            event_type=event_type,
            fork_id=record.fork_id,
            sanitized_payload=dict(payload),
            reason="fork-local injection",
        )

