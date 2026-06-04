"""VM bridge extracted from Interpreter.

This module owns the boundary between the tree-walking interpreter and the
Cognitive VM.  It is a 1:1 lift-and-shift of the existing CVM helpers: the
Interpreter remains the orchestrator and public API owner, while VMBridge uses a
host getter to avoid circular imports and preserve mutable runtime state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Set, Iterable
import copy
import threading

from synapse.ast import Program, AtIpTrigger, BeforeOpTrigger
from synapse.bytecode import CognitiveCompiler, BytecodeProgram
from synapse.cvm import (
    CognitiveVM,
    VMState,
    decode_vm_value,
    OutOfEnergy,
    VMSnapshot,
    VMSnapshotFormatError,
    VMConflictingSourceError,
    VMResumeSyncError,
    VMTamperDetectedError,
    VMCodeMigrationRequiresMapError,
    VMHostError,
    VMStatus,
)
from synapse.hardening import hash_event_chain
from synapse.runtime.vm_routing import (
    classify_ast_node, classify_host_opcode, fallback_reason_for, VM_STRUCTURAL_RUNTIME,
    DETERMINISTIC_PURE_HOST_SYMBOLS, DETERMINISTIC_SIDE_EFFECT_HOST_SYMBOLS,
    NONDETERMINISTIC_HOST_SYMBOLS,
)
from synapse.runtime.migration import validate_vm_state_program_hashes, iter_function_objects
from synapse.runtime.host_abi import HOST_ABI_VERSION


@dataclass
class HostCallContext:
    agent_id: str
    capabilities: Set[str]
    trace_id: str = ""
    # IP captured as integer at context creation time. int is immutable — no
    # shared-reference risk. This is the IP at the moment of dispatch, which
    # is what audit logs need (not the IP after async resume). Single source
    # of truth: populated from vm.state.ip in _get_host_call_context().
    instruction_pointer: Optional[int] = None


@dataclass
class PromiseRecord:
    """Bridge-side durable promise record for Alpha.3-D2.

    The public durable identity remains ``call_id``. ``promise_id`` is kept as
    an alias for compatibility with the pre-existing actor promise store.
    """
    call_id: str
    symbol: Optional[str] = None
    status: str = "PENDING"
    agent_id: Optional[str] = None
    required_capabilities: list = None
    host_abi_version: str = HOST_ABI_VERSION
    result: Any = None
    error: Any = None
    actor_id: Optional[str] = None
    created_event_id: Optional[str] = None
    resolved_event_id: Optional[str] = None

    @property
    def promise_id(self) -> str:
        return self.call_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "promise_id": self.call_id,
            "call_id": self.call_id,
            "symbol": self.symbol,
            "status": self.status,
            "agent_id": self.agent_id,
            "required_capabilities": list(self.required_capabilities or []),
            "host_abi_version": self.host_abi_version,
            "result": copy.deepcopy(self.result),
            "error": copy.deepcopy(self.error),
            "actor_id": self.actor_id,
            "created_event_id": self.created_event_id,
            "resolved_event_id": self.resolved_event_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PromiseRecord":
        return cls(
            call_id=str(data.get("call_id") or data.get("promise_id")),
            symbol=data.get("symbol"),
            status=str(data.get("status", "PENDING")),
            agent_id=data.get("agent_id"),
            required_capabilities=list(data.get("required_capabilities") or []),
            host_abi_version=str(data.get("host_abi_version", HOST_ABI_VERSION)),
            result=data.get("result"),
            error=data.get("error"),
            actor_id=data.get("actor_id"),
            created_event_id=data.get("created_event_id"),
            resolved_event_id=data.get("resolved_event_id"),
        )


class BridgePromise:
    """Small handle returned by VMBridge.create_promise()."""

    def __init__(self, bridge: "VMBridge", call_id: str):
        self.bridge = bridge
        self.call_id = call_id

    @property
    def record(self) -> PromiseRecord:
        return self.bridge.get_promise_record(self.call_id)

    def resolve(self, value: Any, *, resolver_agent_id: Optional[str] = None) -> PromiseRecord:
        return self.bridge.resolve_promise(self.call_id, value, resolver_agent_id=resolver_agent_id)

    def reject(self, error: Any, *, resolver_agent_id: Optional[str] = None) -> PromiseRecord:
        return self.bridge.reject_promise(self.call_id, error, resolver_agent_id=resolver_agent_id)

    def cancel(self) -> None:
        raise VMResumeSyncError(
            "Promise cancellation is reserved for a future lifecycle state",
            code="PROMISE_CANCEL_RESERVED",
            call_id=self.call_id,
        )


HOST_CAPABILITIES: Dict[str, Set[str]] = {
    "default_agent": {
        "memory.read",
        "memory.write",
        "affective.read",
        "affective.write",
        "governance.evaluate",
        "llm.request",
    },
    "restricted_worker": {"memory.read"},
}

SYMBOL_TO_CAPABILITY: Dict[str, str] = {
    "SYS_MEMORY_READ": "memory.read",
    "SYS_MEMORY_WRITE": "memory.write",
    "SYS_AFFECTIVE_READ": "affective.read",
    "SYS_AFFECTIVE_EVENT": "affective.write",
    "SYS_POLICY_CHECK": "governance.evaluate",
    "llm.request":       "llm.request",
}

VM_LOCAL_DETERMINISTIC: Set[str] = set(DETERMINISTIC_PURE_HOST_SYMBOLS)
VM_LOCAL_SIDE_EFFECT: Set[str] = set(DETERMINISTIC_SIDE_EFFECT_HOST_SYMBOLS)
BRIDGE_DISPATCHED: Set[str] = set(SYMBOL_TO_CAPABILITY.keys())

# ---------------------------------------------------------------------------
# Guard violation side-effect enforcement registries (Alpha3e Prep Patch)
# ---------------------------------------------------------------------------
# When guard_violation_active is True, Bridge blocks host calls in
# SIDE_EFFECTING_HOST_SYMBOLS and allows PURE_HOST_SYMBOLS.
# Unknown symbols are BLOCKED by default (fail-closed security posture).
# If a new host ABI symbol is added, the developer must explicitly classify it.

SIDE_EFFECTING_HOST_SYMBOLS: Set[str] = {
    # LLM / external AI calls
    "llm.request",
    # Memory mutations
    "SYS_MEMORY_WRITE", "SYS_MEMORY_DELETE", "SYS_IMPRINT",
    # Actor / messaging
    "SYS_MSG_SEND", "SYS_ACTOR_SPAWN", "SYS_ACTOR_KILL",
    # Affective / cognitive state mutations
    "SYS_AFFECTIVE_WRITE", "SYS_AFFECTIVE_EVENT",
    # Identity / soulprint mutations
    "SYS_EVOLVE", "SYS_SOULPRINT_WRITE",
    # Governed memory
    "SYS_GOVERNED_WRITE",
}

PURE_HOST_SYMBOLS: Set[str] = {
    # Read-only operations — always allowed even under active violation
    "SYS_MEMORY_READ", "SYS_AFFECTIVE_READ", "SYS_POLICY_CHECK",
    # Structural boundaries — needed for cleanup/unwind
    "SYS_CONTEXT_ENTER", "SYS_CONTEXT_EXIT",
    "SYS_ACTOR_ENTER", "SYS_ACTOR_EXIT",
    "SYS_POLICY_ENTER", "SYS_POLICY_EXIT",
    # Prompt assembly — pure computation, no external I/O
    "prompt.build",
    # Metrics / diagnostics — read-only introspection
    "SYS_METRICS_READ",
}

# Symbols NOT in either set are treated as SIDE_EFFECTING under active violation
# (fail-closed). This prevents new host ABI symbols from silently bypassing
# enforcement.


def _assert_all_symbols_classified() -> None:
    """Import-time fail-fast: every registered BRIDGE_DISPATCHED symbol must
    be classified in at least one side-effect registry.

    This prevents the silent debugging nightmare where a developer adds a new
    host ABI symbol, tests without guard violation, and discovers the failure
    only in production under an active guard.

    Called once at module import time. Raises RuntimeError on misconfiguration.
    """
    # BRIDGE_DISPATCHED is defined after this function; we use a deferred check
    # called explicitly after BRIDGE_DISPATCHED is populated.
    pass  # deferred — called below after BRIDGE_DISPATCHED is defined


def _run_symbol_classification_check() -> None:
    """Fail-fast: every BRIDGE_DISPATCHED symbol must be classified."""
    unclassified = []
    for sym in BRIDGE_DISPATCHED:
        normalized = VMBridge._normalize_host_symbol.__func__(None, sym) if False else sym.split("@")[0].split("::")[0].strip()
        if normalized not in SIDE_EFFECTING_HOST_SYMBOLS and normalized not in PURE_HOST_SYMBOLS:
            unclassified.append(sym)
    if unclassified:
        raise RuntimeError(
            f"Host symbols not classified in side-effect registry "
            f"(add to SIDE_EFFECTING_HOST_SYMBOLS or PURE_HOST_SYMBOLS): "
            f"{sorted(unclassified)}"
        )



def build_vm_snapshot_dict(vm: CognitiveVM, label: Optional[str] = None) -> Dict[str, Any]:
    """Build a complete JSON-safe VM snapshot without host dependencies.

    The legacy top-level VMSnapshot fields are preserved for backwards
    compatibility, while ``vm_state`` is the canonical durable VM boundary
    introduced in Alpha.3-A.
    """
    state_dict = vm.state.to_dict()
    legacy = VMSnapshot(
        version=getattr(vm.program, "version", "2.2"),
        ip=vm.state.ip,
        stack=state_dict.get("stack", []),
        locals=state_dict.get("locals", {}),
        gas_remaining=vm.state.gas_remaining,
        cognitive_budget_remaining=vm.state.cognitive_budget_remaining,
        transition_hash=vm.state.transition_hash,
        last_processed_event_id="evt--0000001",
        palace_cursor=None,
        intention_sp=0,
        mood_snapshot={"valence": 0.0, "arousal": 0.0, "dominance": 0.0},
        current_context=vm.state.current_context,
        history_hash="",
    ).to_dict()
    legacy.update({
        "label": label,
        "snapshot_version": "2.2-alpha3",
        "vm_state": state_dict,
        "program": vm.program.to_dict(),
        "program_hash": vm.program.program_hash,
        "event_metadata": {
            "checkpoint_type": "mid" if label else "halt",
            "call_stack_depth": len(vm.state.call_stack),
        },
    })
    return legacy


def restore_vm_from_snapshot(
    snapshot: Dict[str, Any],
    program: Optional[BytecodeProgram] = None,
    *,
    gas: Optional[int] = None,
    cognitive_budget: Optional[int] = None,
    host=None,
) -> CognitiveVM:
    """Restore a VM from an Alpha.3-A unified snapshot.

    Fails closed when a snapshot taken inside an active call stack is resumed
    against bytecode with a different program_hash.  Legacy snapshots without
    ``vm_state`` remain accepted.
    """
    if "vm_state" in snapshot:
        state = VMState.from_dict(snapshot.get("vm_state") or {})
    else:
        state = VMState(
            ip=int(snapshot.get("ip", 0)),
            stack=list(snapshot.get("stack", [])),
            locals=dict(snapshot.get("locals", {})),
            gas_remaining=int(snapshot.get("gas_remaining", 1000)),
            cognitive_budget_remaining=snapshot.get("cognitive_budget_remaining"),
            transition_hash=snapshot.get("transition_hash", "sha256:genesis"),
            current_context=snapshot.get("current_context"),
            context_stack=list(snapshot.get("context_stack", [])),
        )

    if gas is not None:
        state.gas_remaining = gas
    if cognitive_budget is not None:
        state.cognitive_budget_remaining = cognitive_budget

    if program is None:
        program_dict = snapshot.get("program")
        if program_dict:
            program = BytecodeProgram.from_dict(program_dict)
        else:
            program = BytecodeProgram(instructions=[], constants=[], version="2.2")

    stored_hash = snapshot.get("program_hash")
    current_hash = getattr(program, "program_hash", None)
    if stored_hash and current_hash and stored_hash != current_hash:
        if state.call_stack:
            raise VMCodeMigrationRequiresMapError(
                "Program hash mismatch with non-empty call_stack: "
                f"stored={stored_hash[:12]}..., current={current_hash[:12]}... "
                "Mid-call resume requires identical bytecode or explicit migration_map."
            )
        validate_vm_state_program_hashes(state, current_hash)

    return CognitiveVM(program, state=state, host=host)


def _event_id_from_event(event: Any, fallback: str) -> str:
    """Best-effort event id extraction for dict/object event records."""
    if isinstance(event, dict):
        return str(event.get("event_id") or event.get("id") or fallback)
    return str(getattr(event, "event_id", getattr(event, "id", fallback)))


def _history_prefix_hash(history: list, seed: str) -> str:
    chain = hash_event_chain(history, seed=seed)
    return chain[-1]["hash"] if chain else ""


def validate_or_hydrate_history_boundary(snapshot: Dict[str, Any], runtime_host: Any) -> None:
    """Validate or hydrate host execution_history for a VM snapshot.

    Embedded history is an explicit cold-start/cross-process mode. Without
    embedded history, the current runtime host must already hold the exact
    history prefix required by the snapshot boundary.
    """
    boundary = snapshot.get("history_boundary") or {}
    if not boundary:
        return

    if boundary.get("history_embedded"):
        raw_history = copy.deepcopy(snapshot.get("embedded_execution_history", []))
        history = getattr(runtime_host, "execution_history", None)
        if hasattr(history, "restore_from_raw"):
            history.restore_from_raw(raw_history)
        else:
            runtime_host.execution_history = raw_history
    else:
        required_length = int(boundary.get("history_length") or 0)
        current_length = len(getattr(runtime_host, "execution_history", []))
        if current_length < required_length:
            raise VMResumeSyncError(
                "Host execution history is truncated. "
                f"Snapshot requires length >= {required_length}, "
                f"current host history length: {current_length}"
            )

    # Hash/tamper validation is intentionally left to the canonical
    # VMSnapshot history_hash check in restore_vm_from_checkpoint(). This
    # helper only guarantees that a required history prefix exists or can be
    # hydrated from an embedded snapshot. Keeping tamper detection in the
    # existing path preserves the public VMTamperDetectedError contract.




# Sentinel for cache miss — avoids confusion with None result
_SENTINEL = object()

class VMBridge:
    """Cognitive VM adapter/facade for Interpreter."""

    def __init__(self, host_getter, live_mode=None, replay_mode=None):
        self.get_host = host_getter
        self.live_mode = live_mode
        self.replay_mode = replay_mode
        self._nested_host_call_depth = 0
        self._bridge_lock = threading.RLock()
        self._promise_vms: Dict[str, CognitiveVM] = {}

    def route_execution(self, node: Any) -> str:
        """Return the explicit v2.1.4-C routing route for an AST node."""
        return classify_ast_node(node).route

    def log_fallback(self, node: Any, reason: str = "not_yet_compiled", compiler_phase: str = "route_execution") -> None:
        """Record structured HOST_EVAL fallback visibility without changing execution."""
        h = self.get_host()
        decision = classify_ast_node(node)
        node_type = decision.node
        structured_reason = fallback_reason_for(node_type)
        if reason and reason not in {"not_yet_compiled", decision.reason}:
            structured_reason = {"code": str(reason), "detail": structured_reason.get("detail", str(reason))}
        event = {
            "type": "vm_fallback",
            "node": node_type,  # legacy key retained
            "ast_node_type": node_type,
            "node_location": {
                "line": getattr(node, "line", getattr(node, "lineno", None)),
                "col": getattr(node, "col", getattr(node, "column", None)),
            },
            "compiler_phase": compiler_phase,
            "fallback_reason": structured_reason,
            "route": "HOST_EVAL",
            "reason": structured_reason.get("code"),
            "program_hash": getattr(h, "current_program_hash", None),
            "ip_at_fallback": None,
            "trace_id": h.current_trace_id(),
        }
        if hasattr(h, "telemetry_events"):
            h.telemetry_events.append(event)
        else:
            h.execution_history.append(event)

    def evaluate_compile_vm(self, node, env) -> Dict[str, Any]:
        """Compile Synapse source into the conservative v2.1 bytecode format."""
        h = self.get_host()
        source = h.evaluate(node.source, env) if node.source else h.source_code
        if isinstance(source, dict) and source.get("type") == "bytecode_program":
            program = BytecodeProgram.from_dict(source)
        else:
            if source and isinstance(source, str):
                from synapse.lexer import Lexer
                from synapse.parser import Parser
                ast = Parser(Lexer(source).scan_tokens()).parse()
            else:
                ast = Program(statements=[])
            program = CognitiveCompiler().compile(ast)
        data = program.to_dict()
        data["version"] = "2.1"
        env.define(node.binding, data)
        event = {"type": "vm_bytecode_compiled", "instructions": len(data.get("instructions", [])), "trace_id": h.current_trace_id()}
        h.execution_history.append(event)
        return data

    def event_id_for_index(self, idx: int) -> str:
        return f"evt-{idx:08d}"

    def parse_event_id(self, event_id: str) -> int:
        if str(event_id) == "evt--0000001":
            return -1
        try:
            return int(str(event_id).split("-")[-1])
        except Exception:
            raise VMResumeSyncError(f"Invalid VM event id: {event_id}")

    def history_hash_until(self, event_id: Optional[str]) -> str:
        h = self.get_host()
        if event_id is None:
            prefix = []
        else:
            idx = self.parse_event_id(event_id)
            if idx < 0:
                prefix = []
            else:
                prefix = h.execution_history[:idx + 1]
        chain = hash_event_chain(prefix, seed=h.history_chain_seed)
        return chain[-1]["hash"] if chain else ""

    def current_mood_snapshot(self) -> Dict[str, float]:
        h = self.get_host()
        if h.affective_states:
            state = next(iter(h.affective_states.values()))
            current = getattr(state, "current", {}) or {}
            return {"valence": float(current.get("valence", 0.0)), "arousal": float(current.get("arousal", 0.0)), "dominance": float(current.get("dominance", 0.0))}
        return {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}

    def _ensure_history_streams(self, h: Any) -> None:
        if not hasattr(h, "execution_history"):
            h.execution_history = []
        if not hasattr(h, "side_effect_history"):
            h.side_effect_history = []

    def _copy_vm_state_for_snapshot(self, vm: CognitiveVM) -> Dict[str, Any]:
        """Fast copy-on-read phase for two-phase snapshotting."""
        return copy.deepcopy(vm.state.to_dict())

    def _validate_pending_against_host(self, pending: Dict[str, Any], vm: Optional[CognitiveVM] = None) -> None:
        h = self.get_host()
        if not pending:
            return
        if pending.get("host_abi_version") != HOST_ABI_VERSION:
            raise VMResumeSyncError(
                f"HOST_ABI_VERSION mismatch: pending={pending.get('host_abi_version')}, current={HOST_ABI_VERSION}",
                code="HOST_ABI_MISMATCH",
                call_id=pending.get("call_id"),
            )
        host_agent_id = getattr(h, "current_agent_id", "default_agent")
        pending_agent_id = pending.get("agent_id")
        if pending_agent_id is not None and pending_agent_id != host_agent_id:
            if not getattr(h, "trust_snapshot_agent_id", False):
                raise VMResumeSyncError(
                    f"Security Trust Gate Violation: pending agent_id {pending_agent_id!r} "
                    f"does not match host agent_id {host_agent_id!r}",
                    code="AGENT_ID_MISMATCH",
                    call_id=pending.get("call_id"),
                )
        required = set(pending.get("required_capabilities") or [])
        if required:
            if vm is not None:
                ctx = self._get_host_call_context(vm)
                actual = set(ctx.capabilities)
            else:
                actual = set(HOST_CAPABILITIES.get(pending_agent_id or host_agent_id, set()))
            if actual != required:
                raise VMResumeSyncError(
                    f"Capability mismatch: pending requires {sorted(required)}, host has {sorted(actual)}",
                    code="CAPABILITY_EXACT_MATCH_FAILED",
                    call_id=pending.get("call_id"),
                )
        current_program_hash = None
        if vm is not None:
            current_program_hash = getattr(vm.program, "program_hash", None)
        if current_program_hash:
            for fn in iter_function_objects(decode_vm_value(pending.get("args", []))):
                if fn.program_hash != current_program_hash:
                    raise VMResumeSyncError(
                        f"FunctionObject {fn.name!r} in pending args targets stale program_hash",
                        code="PENDING_ARG_PROGRAM_HASH_MISMATCH",
                        call_id=pending.get("call_id"),
                    )

    def _find_host_call_resolution(self, call_id: str) -> Dict[str, Any]:
        h = self.get_host()
        matches = [
            e for e in getattr(h, "execution_history", [])
            if isinstance(e, dict) and e.get("type") == "host_call_resolved" and e.get("call_id") == call_id
        ]
        if len(matches) == 0:
            raise VMResumeSyncError(
                f"No host_call_resolved event found for call_id {call_id}",
                code="HOST_CALL_REPLAY_MISSING",
                call_id=call_id,
            )
        if len(matches) > 1:
            raise VMResumeSyncError(
                f"Duplicate host_call_resolved events for call_id {call_id}",
                code="HOST_CALL_REPLAY_DUPLICATE",
                call_id=call_id,
            )
        return matches[0]

    def _ensure_promise_store(self, h: Any) -> None:
        if not hasattr(h, "promises") or getattr(h, "promises") is None:
            h.promises = {}

    def _actor_runtime(self) -> Any:
        h = self.get_host()
        runtime = getattr(h, "runtime", None)
        actor = getattr(runtime, "actor", None) if runtime is not None else None
        return actor or getattr(h, "actor_runtime", None)

    def _append_promise_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        h = self.get_host()
        self._ensure_history_streams(h)
        event = copy.deepcopy(event)
        event.setdefault("event_id", self._next_host_event_id(h))
        h.execution_history.append(event)
        return event

    def get_promise_record(self, call_id: str) -> PromiseRecord:
        h = self.get_host()
        self._ensure_promise_store(h)
        raw = h.promises.get(call_id)
        if raw is None:
            raise VMResumeSyncError(
                f"Promise {call_id} not found",
                code="PROMISE_NOT_FOUND",
                call_id=call_id,
            )
        return PromiseRecord.from_dict(raw)

    def create_promise(
        self,
        call_id: str,
        *,
        vm: Optional[CognitiveVM] = None,
        symbol: Optional[str] = None,
        actor_id: Optional[str] = None,
    ) -> BridgePromise:
        """Create a bridge-side PromiseRecord linked to a D1 pending call.

        D2 intentionally preserves the D1 single-pending-call invariant: when a
        VM is provided, its pending_host_call must match ``call_id``.
        """
        h = self.get_host()
        self._ensure_promise_store(h)
        with self._bridge_lock:
            if vm is not None:
                pending = vm.state.pending_host_call or {}
                if pending.get("call_id") != call_id:
                    raise VMResumeSyncError(
                        f"Cannot create promise for {call_id}: VM pending call is {pending.get('call_id')}",
                        code="PROMISE_CALL_ID_MISMATCH",
                        call_id=call_id,
                    )
            existing = h.promises.get(call_id)
            if existing and str(existing.get("status", "PENDING")).upper() == "PENDING":
                self._promise_vms.setdefault(call_id, vm)
                return BridgePromise(self, call_id)

            pending = (vm.state.pending_host_call or {}) if vm is not None else {}
            record = PromiseRecord(
                call_id=call_id,
                symbol=symbol or pending.get("symbol"),
                status="PENDING",
                agent_id=pending.get("agent_id") or getattr(h, "current_agent_id", "default_agent"),
                required_capabilities=list(pending.get("required_capabilities") or []),
                host_abi_version=pending.get("host_abi_version", HOST_ABI_VERSION),
                actor_id=actor_id or getattr(h, "current_actor_id", None),
            )
            event = self._append_promise_event({
                "type": "promise_created",
                "call_id": call_id,
                "promise_id": call_id,
                "symbol": record.symbol,
                "agent_id": record.agent_id,
                "actor_id": record.actor_id,
                "host_abi_version": record.host_abi_version,
            })
            record.created_event_id = event.get("event_id")
            h.promises[call_id] = record.to_dict()
            if vm is not None:
                self._promise_vms[call_id] = vm
            actor = self._actor_runtime()
            if actor is not None and record.actor_id and hasattr(actor, "suspend_on_promise"):
                actor.suspend_on_promise(record.actor_id, call_id)
            return BridgePromise(self, call_id)

    def _json_safe_promise_error(self, error: Any, call_id: str) -> Dict[str, Any]:
        if isinstance(error, dict):
            return {
                "type": "PromiseRejection",
                "code": str(error.get("code", "PROMISE_REJECTED")),
                "message": str(error.get("message", error.get("error", "Promise rejected"))),
                "call_id": call_id,
            }
        return {
            "type": "PromiseRejection",
            "code": "PROMISE_REJECTED",
            "message": str(error),
            "call_id": call_id,
        }

    def _validate_promise_resolution(
        self,
        record: PromiseRecord,
        payload: Any,
        *,
        resolver_agent_id: Optional[str] = None,
        vm: Optional[CognitiveVM] = None,
    ) -> None:
        if record.status != "PENDING":
            raise VMResumeSyncError(
                f"Promise {record.call_id} is already {record.status}",
                code="PROMISE_ALREADY_RESOLVED",
                call_id=record.call_id,
            )
        if record.host_abi_version != HOST_ABI_VERSION:
            raise VMResumeSyncError(
                f"HOST_ABI_VERSION mismatch: promise={record.host_abi_version}, current={HOST_ABI_VERSION}",
                code="HOST_ABI_MISMATCH",
                call_id=record.call_id,
            )
        h = self.get_host()
        host_agent_id = resolver_agent_id or getattr(h, "current_agent_id", "default_agent")
        if record.agent_id is not None and host_agent_id != record.agent_id:
            if not getattr(h, "trust_snapshot_agent_id", False):
                raise VMResumeSyncError(
                    f"Promise resolver agent {host_agent_id!r} does not match promise agent {record.agent_id!r}",
                    code="PROMISE_AGENT_ID_MISMATCH",
                    call_id=record.call_id,
                )
        required = set(record.required_capabilities or [])
        if required:
            actual = set(HOST_CAPABILITIES.get(host_agent_id, set()))
            if actual != required:
                raise VMResumeSyncError(
                    f"Promise capability mismatch: requires {sorted(required)}, resolver has {sorted(actual)}",
                    code="PROMISE_CAPABILITY_EXACT_MATCH_FAILED",
                    call_id=record.call_id,
                )
        current_program_hash = getattr(getattr(vm, "program", None), "program_hash", None)
        if current_program_hash:
            for fn in iter_function_objects(payload):
                if fn.program_hash != current_program_hash:
                    raise VMResumeSyncError(
                        f"FunctionObject {fn.name!r} in promise payload targets stale program_hash",
                        code="PROMISE_PAYLOAD_PROGRAM_HASH_MISMATCH",
                        call_id=record.call_id,
                    )

    def _finish_promise(
        self,
        call_id: str,
        *,
        status: str,
        result: Any = None,
        error: Any = None,
        resolver_agent_id: Optional[str] = None,
    ) -> PromiseRecord:
        h = self.get_host()
        self._ensure_promise_store(h)
        with self._bridge_lock:
            record = self.get_promise_record(call_id)
            vm = self._promise_vms.get(call_id)
            payload = result if status == "RESOLVED" else error
            self._validate_promise_resolution(record, payload, resolver_agent_id=resolver_agent_id, vm=vm)
            if status == "REJECTED":
                error = self._json_safe_promise_error(error, call_id)
                payload = error
            event_type = "promise_resolved" if status == "RESOLVED" else "promise_rejected"
            event = self._append_promise_event({
                "type": event_type,
                "call_id": call_id,
                "promise_id": call_id,
                "symbol": record.symbol,
                "agent_id": record.agent_id,
                "result": copy.deepcopy(result) if status == "RESOLVED" else None,
                "error": copy.deepcopy(error) if status == "REJECTED" else None,
            })
            # D1 replay lookup remains host_call_resolved; D2 records the
            # promise event and the D1-compatible resolution under the same call_id.
            self._append_promise_event({
                "type": "host_call_resolved",
                "call_id": call_id,
                "symbol": record.symbol,
                "result": copy.deepcopy(payload),
                "agent_id": record.agent_id,
                "promise_event_id": event.get("event_id"),
            })
            record.status = status
            record.result = copy.deepcopy(result) if status == "RESOLVED" else None
            record.error = copy.deepcopy(error) if status == "REJECTED" else None
            record.resolved_event_id = event.get("event_id")
            h.promises[call_id] = record.to_dict()
            actor = self._actor_runtime()
            if actor is not None and record.actor_id:
                if status == "RESOLVED" and hasattr(actor, "wake_on_resolve"):
                    actor.wake_on_resolve(record.actor_id, call_id, result)
                if status == "REJECTED" and hasattr(actor, "wake_on_reject"):
                    actor.wake_on_reject(record.actor_id, call_id, error)
            if vm is not None and vm.state.pending_host_call:
                vm.resume_host_call(call_id, payload)
            return record

    def resolve_promise(self, call_id: str, value: Any, *, resolver_agent_id: Optional[str] = None) -> PromiseRecord:
        return self._finish_promise(call_id, status="RESOLVED", result=value, resolver_agent_id=resolver_agent_id)

    def reject_promise(self, call_id: str, error: Any, *, resolver_agent_id: Optional[str] = None) -> PromiseRecord:
        return self._finish_promise(call_id, status="REJECTED", error=error, resolver_agent_id=resolver_agent_id)

    def promise_result_from_history(self, call_id: str) -> Any:
        h = self.get_host()
        matches = [
            e for e in getattr(h, "execution_history", [])
            if isinstance(e, dict) and e.get("type") in {"promise_resolved", "promise_rejected"} and e.get("call_id") == call_id
        ]
        if len(matches) == 0:
            raise VMResumeSyncError(
                f"No promise resolution event found for call_id {call_id}",
                code="PROMISE_REPLAY_MISSING",
                call_id=call_id,
            )
        if len(matches) > 1:
            raise VMResumeSyncError(
                f"Duplicate promise resolution events for call_id {call_id}",
                code="PROMISE_REPLAY_DUPLICATE",
                call_id=call_id,
            )
        event = matches[0]
        if event.get("type") == "promise_rejected":
            return event.get("error")
        return event.get("result")

    def resume_host_call(self, vm: CognitiveVM, call_id: str, value: Any = None, *, replay: bool = False) -> None:
        """Bridge-side resume with D1 security gates and optional replay lookup."""
        with self._bridge_lock:
            pending = copy.deepcopy(vm.state.pending_host_call)
            if not pending:
                raise VMResumeSyncError(
                    "No pending host call found in CVM state",
                    code="DOUBLE_RESUME_FORBIDDEN",
                    call_id=call_id,
                )
            if pending.get("call_id") != call_id:
                raise VMResumeSyncError(
                    f"Resume call_id mismatch: expected {pending.get('call_id')}, got {call_id}",
                    code="CALL_ID_MISMATCH",
                    call_id=call_id,
                )
            self._validate_pending_against_host(pending, vm)
            if replay and pending.get("determinism_class") == "nondeterministic":
                value = self._find_host_call_resolution(call_id).get("result")
            vm.resume_host_call(call_id, value)

    def make_vm_snapshot(self, vm: CognitiveVM, label: str, embed_history: bool = False) -> Dict[str, Any]:
        h = self.get_host()
        self._ensure_history_streams(h)
        # Phase 1: copy-on-read under lock only. Slow serialization/enrichment stays outside.
        with self._bridge_lock:
            state_copy = self._copy_vm_state_for_snapshot(vm)
        last_idx = len(h.execution_history) - 1
        last_event_id = self.event_id_for_index(last_idx) if last_idx >= 0 else "evt--0000001"

        snapshot = build_vm_snapshot_dict(vm, label)
        snapshot["vm_state"] = state_copy
        snapshot.update({
            "version": "2.1",  # preserve legacy checkpoint contract
            "last_processed_event_id": last_event_id,
            "palace_cursor": f"tx-{max(0, last_idx)}",
            "intention_sp": len(h.intention_cascades),
            "mood_snapshot": self.current_mood_snapshot(),
            "current_context": h.current_context,
            "history_hash": self.history_hash_until(last_event_id),
            "host_abi_version": HOST_ABI_VERSION,
        })
        snapshot["history_boundary"] = {
            "last_processed_event_id": last_event_id,
            "history_hash": snapshot["history_hash"],
            "history_length": len(h.execution_history),
            "history_embedded": bool(embed_history),
            "history_ref": None,
        }
        if embed_history:
            snapshot["embedded_execution_history"] = copy.deepcopy(h.execution_history)
        # Keep vm_state synchronized with host-enriched context for unified restore.
        snapshot["vm_state"]["current_context"] = h.current_context
        snapshot["vm_state"]["context_stack"] = list(getattr(vm.state, "context_stack", []))
        snapshot["host_metadata"] = {
            "last_event_id": last_event_id,
            "current_context": h.current_context,
            "trace_id": h.current_trace_id(),
            "execution_history_length": len(h.execution_history),
        }

        h.vm_checkpoints[label] = snapshot
        h.vm_snapshots.append({"label": label, "snapshot": snapshot})
        event = {
            "type": "vm_checkpoint_saved",
            "label": label,
            "ip": snapshot["ip"],
            "transition_hash": snapshot["transition_hash"],
            "last_processed_event_id": snapshot["last_processed_event_id"],
            "gas_remaining": snapshot["gas_remaining"],
            "cognitive_budget_remaining": snapshot["cognitive_budget_remaining"],
            "current_context": snapshot["current_context"],
            "trace_id": h.current_trace_id(),
        }
        h.execution_history.append(event)
        return snapshot

    def restore_vm_from_checkpoint(self, label: str, gas: Optional[int] = None, cognitive_budget: Optional[int] = None) -> CognitiveVM:
        h = self.get_host()
        if label not in h.vm_checkpoints:
            raise VMSnapshotFormatError(f"Unknown VM checkpoint: {label}")
        with self._bridge_lock:
            raw = copy.deepcopy(h.vm_checkpoints[label])
        validate_or_hydrate_history_boundary(raw, h)
        if raw.get("host_abi_version") and raw.get("host_abi_version") != HOST_ABI_VERSION:
            raise VMResumeSyncError(
                f"HOST_ABI_VERSION mismatch: snapshot={raw.get('host_abi_version')}, current={HOST_ABI_VERSION}",
                code="HOST_ABI_MISMATCH",
            )
        snap = VMSnapshot.from_dict(raw)
        idx = self.parse_event_id(snap.last_processed_event_id)
        if len(h.execution_history) - 1 < idx:
            event = {"type": "vm_resume_sync_error", "from_checkpoint": label, "checkpoint_last_event": snap.last_processed_event_id, "current_last_event": self.event_id_for_index(len(h.execution_history)-1) if h.execution_history else None}
            h.execution_history.append(event)
            raise VMResumeSyncError(f"Event log is behind checkpoint {label}")
        actual_hash = self.history_hash_until(snap.last_processed_event_id)
        if actual_hash != snap.history_hash:
            h.execution_history.append({"type": "vm_tamper_detected", "from_checkpoint": label, "expected_hash": snap.history_hash, "actual_hash": actual_hash})
            raise VMTamperDetectedError(f"History hash mismatch for checkpoint {label}")

        program_dict = raw.get("program") or {"type": "bytecode_program", "version": "2.1", "constants": [], "instructions": [{"op": "HALT"}]}
        program = BytecodeProgram.from_dict(program_dict)
        vm = restore_vm_from_snapshot(raw, program, gas=gas, cognitive_budget=cognitive_budget, host=None)
        vm.host = self.get_cvm_callback_adapter(vm)
        self._validate_pending_against_host(vm.state.pending_host_call or {}, vm)
        self.sync_context_tracker_from_vm_state(vm)
        self.sync_actor_runtime_from_vm_state(vm)
        self.sync_policy_runtime_from_vm_state(vm)
        h.current_context = getattr(getattr(h, "context_tracker", None), "current", vm.state.current_context)
        h.execution_history.append({"type": "vm_resumed", "from_checkpoint": label, "hash_valid": True, "sync_valid": True, "resumed_at_ip": vm.state.ip, "trace_id": h.current_trace_id()})
        return vm

    def vm_host_call(self, opcode: str, a: Any, b: Any) -> Dict[str, Any]:
        h = self.get_host()
        host_map = {
            "IMPRINT": "host.memory.imprint",
            "RECALL": "host.memory.recall",
            "METRICS": "host.metrics_snapshot",
            "AFFECT_EVENT": "host.affective.apply_event",
            "AFFECT_STATE": "host.affective.get_state",
            "FRACTURE_SELF": "host.fracture",
            "DREAM": "host.dream",
            "LLM_EVAL": "host.llm",
            "HOST_EVAL": "host.eval",
        }
        decision = classify_host_opcode(opcode)
        result = {"opcode": opcode, "host_call": host_map.get(opcode, "host.unknown"), "status": "fallback", "a": a, "b": b, "from_cache": False}
        if opcode == "METRICS":
            result.update({"status": "ok", "value": h.metrics_snapshot(), "from_cache": False})
        if result.get("status") == "fallback":
            structured_reason = fallback_reason_for(opcode)
            if decision.route != "HOST_EVAL":
                structured_reason = {"code": "HOST_ABI_FALLBACK", "detail": "Legacy HOST_ABI fallback path"}
            h.execution_history.append({
                "type": "vm_fallback",
                "node": opcode,
                "ast_node_type": opcode,
                "node_location": {"line": None, "col": None},
                "compiler_phase": "host_abi",
                "fallback_reason": structured_reason,
                "route": decision.route,
                "reason": structured_reason.get("code"),
                "program_hash": None,
                "ip_at_fallback": None,
                "trace_id": h.current_trace_id(),
            })
        event = {"type": "vm_host_call", "opcode": opcode, "host_call": result["host_call"], "from_cache": bool(result.get("from_cache")), "gas_cost": 0, "gas_refund": result.get("gas_refund", 0), "trace_id": h.current_trace_id()}
        h.execution_history.append(event)
        return result


    def _get_host_call_context(self, vm: Any) -> HostCallContext:
        """Build the capability context for a CVM CALL_HOST dispatch.

        VM snapshot agent_id is fail-closed when it conflicts with the current
        host agent unless the host explicitly opts into trusting snapshot agent
        identity with ``trust_snapshot_agent_id = True``.
        """
        h = self.get_host()
        state = getattr(vm, "state", None)
        host_agent_id = getattr(h, "current_agent_id", "default_agent")
        snapshot_agent_id = getattr(state, "agent_id", None) if state is not None else None

        if snapshot_agent_id is not None and snapshot_agent_id != host_agent_id:
            if not getattr(h, "trust_snapshot_agent_id", False):
                raise VMResumeSyncError(
                    f"Security Trust Gate Violation: snapshot agent_id "
                    f"{snapshot_agent_id!r} does not match host agent_id {host_agent_id!r}"
                )
            agent_id = snapshot_agent_id
        else:
            agent_id = snapshot_agent_id or host_agent_id

        if state is not None and getattr(state, "agent_id", None) is None:
            state.agent_id = agent_id

        trace_fn = getattr(h, "current_trace_id", None)
        trace_id = trace_fn() if callable(trace_fn) else f"trace-{agent_id}"
        # Capture vm.state.ip as an int (immutable value) at call time.
        # This is the IP at the moment of dispatch — correct for audit logs.
        # int capture avoids shared-reference issues unlike storing vm.state itself.
        vm_ip = getattr(getattr(vm, "state", None), "ip", None)
        return HostCallContext(
            agent_id=agent_id,
            capabilities=HOST_CAPABILITIES.get(agent_id, set()),
            trace_id=trace_id,
            instruction_pointer=int(vm_ip) if vm_ip is not None else None,
        )


    # ------------------------------------------------------------------
    # Guard violation enforcement (Alpha3e Prep Patch)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_host_symbol(symbol: str) -> str:
        """Normalize a host symbol for registry lookup.

        Strips namespace/version suffixes to handle variants like:
          "llm.request@v2" → "llm.request"
          "SYS_MEMORY_WRITE::bulk" → "SYS_MEMORY_WRITE"

        This prevents registry bypass via versioned or namespaced symbol names.
        The normalized form is used only for fail-closed classification;
        the original symbol is preserved in audit events and capability checks.
        """
        # Strip @version suffix
        normalized = symbol.split("@")[0]
        # Strip ::qualifier suffix
        normalized = normalized.split("::")[0]
        return normalized.strip()

    def _is_side_effecting_symbol(self, symbol: str) -> bool:
        """Return True if symbol must be blocked under active guard violation.

        Fail-closed: symbols not in PURE_HOST_SYMBOLS (after normalization)
        are treated as side-effecting. This prevents new host ABI symbols
        from silently bypassing enforcement when a developer forgets to
        classify them, and prevents namespace/version bypass attacks.
        """
        normalized = self._normalize_host_symbol(symbol)
        if normalized in PURE_HOST_SYMBOLS:
            return False
        return True  # SIDE_EFFECTING or unknown (after normalization) → blocked

    def _check_guard_violation_enforcement(self, vm: Any, symbol: str) -> None:
        """Block side-effecting host calls under active guard violation.

        Called at the top of dispatch_host_call for every host symbol.
        Raises VMHostError("SIDE_EFFECT_BLOCKED_BY_GUARD") if:
          - vm.state.guard_violation_active is True
          - symbol is not in PURE_HOST_SYMBOLS (fail-closed)

        This method does NOT reset guard_violation_active — only
        GUARD_VIOLATION_ACK (compiler-inserted) or ACTOR_EXIT resets it.
        """
        state = getattr(vm, "state", None)
        if state is None:
            return
        if not getattr(state, "guard_violation_active", False):
            return
        if not self._is_side_effecting_symbol(symbol):
            return
        h = self.get_host()
        # Write SIDE_EFFECT_BLOCKED event to history for audit trail
        if hasattr(h, "execution_history"):
            h.execution_history.append({
                "type": "SIDE_EFFECT_BLOCKED_BY_GUARD",
                "request_symbol": symbol,
                "agent_id": getattr(state, "agent_id", None),
                "instruction_pointer": getattr(state, "ip", None),
            })
        raise VMHostError(
            code="SIDE_EFFECT_BLOCKED_BY_GUARD",
            message=(
                f"Side-effecting host call '{symbol}' blocked: "
                "unhandled GUARD_VIOLATION is active. "
                "Handle the violation via catch(GUARD_VIOLATION) before "
                "making further side-effecting calls."
            ),
            symbol=symbol,
        )

    # ------------------------------------------------------------------
    # LLM Bridge — Alpha3e Track A
    # ------------------------------------------------------------------

    def _compute_llm_content_key(self, envelope: dict, schema_hash: str,
                                  engine_params: dict) -> str:
        """Deterministic content-addressable key for LLM request caching.

        key = SHA-256(template_hash || variables_hash || schema_hash
                      || engine_params_hash || model_version)

        model_version is mandatory — absence raises LLM_MISSING_MODEL_VERSION.
        """
        import hashlib, json as _j
        model_version = engine_params.get("model_version") or engine_params.get("model", "")
        if not model_version:
            raise VMHostError(
                code="LLM_MISSING_MODEL_VERSION",
                message="engine_params must include model_version for deterministic caching",
                symbol="llm.request",
            )
        ep_hash = hashlib.sha256(
            _j.dumps(engine_params, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()
        raw = "||".join([
            envelope.get("template_hash", ""),
            envelope.get("variables_hash", ""),
            schema_hash or "",
            ep_hash,
            model_version,
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _llm_cache_lookup_replay(self, h: Any, content_key: str) -> Any:
        """In replay mode: look for LLM_RESPONSE_CACHED in execution_history."""
        history = getattr(h, "execution_history", []) or []
        for event in history:
            if (event.get("type") == "LLM_RESPONSE_CACHED"
                    and event.get("content_key") == content_key):
                return event.get("result")
        return _SENTINEL

    def _llm_cache_lookup_live(self, h: Any, content_key: str) -> Any:
        """In live mode: look in the Bridge-level LLM response cache."""
        cache = getattr(h, "_llm_response_cache", None)
        if cache is None:
            return _SENTINEL
        return cache.get(content_key, _SENTINEL)

    def _llm_cache_store(self, h: Any, content_key: str, result: Any,
                          model_version: str, schema_hash: str,
                          call_id: str) -> None:
        """Write LLM_RESPONSE_CACHED event and update live cache."""
        import hashlib as _hl
        # Live cache
        if not hasattr(h, "_llm_response_cache"):
            h._llm_response_cache = {}
        h._llm_response_cache[content_key] = result

        # Deterministic history event (embedded replay source-of-truth)
        prev_hash = ""
        history = getattr(h, "execution_history", []) or []
        if history:
            prev = history[-1]
            prev_hash = prev.get("history_hash", "")
        event_payload = {
            "type": "LLM_RESPONSE_CACHED",
            "call_id": call_id,
            "content_key": content_key,
            "result": result,
            "model_version": model_version,
            "schema_hash": schema_hash or "",
        }
        chain_src = _hl.sha256(
            (prev_hash + content_key + call_id).encode()
        ).hexdigest()
        event_payload["history_hash"] = chain_src
        if hasattr(h, "execution_history"):
            h.execution_history.append(event_payload)

    def _llm_validate_schema(self, result: Any, schema_hash: str,
                              h: Any, call_id: str) -> None:
        """Bridge-side schema validation — CVM never sees raw LLM response."""
        if not schema_hash or schema_hash == "":
            return
        # Look up schema by hash from host schema registry
        schema_registry = getattr(h, "llm_schema_registry", {})
        schema = schema_registry.get(schema_hash)
        if schema is None:
            return  # unknown schema — allow (strict mode can change this via policy)
        # Minimal structural check: schema is a list of required keys
        if isinstance(schema, list) and isinstance(result, dict):
            missing = [k for k in schema if k not in result]
            if missing:
                event = {
                    "type": "LLM_RESPONSE_SCHEMA_ERROR",
                    "call_id": call_id,
                    "schema_hash": schema_hash,
                    "missing_keys": missing,
                }
                if hasattr(h, "execution_history"):
                    h.execution_history.append(event)
                raise VMHostError(
                    code="SCHEMA_MISMATCH",
                    message=f"LLM response missing required keys: {missing}",
                    symbol="llm.request",
                )

    def _execute_llm_request(self, h: Any, args: list) -> Any:
        """Full LLM bridge dispatch with cache, replay, schema validation
        and deterministic failure taxonomy.

        args[0]: PromptEnvelope dict (from PROMPT_BUILD)
        args[1]: schema_hash str
        args[2]: engine_params dict
        args[3]: cache_policy str
        """
        import json as _j

        envelope    = args[0] if args else {}
        schema_hash = str(args[1]) if len(args) > 1 and args[1] else ""
        engine_params = args[2] if len(args) > 2 and isinstance(args[2], dict) else {}
        cache_policy  = str(args[3]) if len(args) > 3 else "model_change"

        call_id = getattr(h, "current_trace_id", lambda: "llm-unknown")()

        # --- 1. Compute content key (raises on missing model_version) ---
        try:
            content_key = self._compute_llm_content_key(
                envelope, schema_hash, engine_params)
        except VMHostError:
            raise

        model_version = (engine_params.get("model_version")
                         or engine_params.get("model", "default"))

        # --- 2. Replay mode: must find cached response ---
        is_replay = getattr(self, "replay_mode", False)
        if is_replay:
            cached = self._llm_cache_lookup_replay(h, content_key)
            if cached is _SENTINEL:
                event = {
                    "type": "LLM_RESPONSE_MISSING",
                    "call_id": call_id,
                    "content_key": content_key,
                }
                if hasattr(h, "execution_history"):
                    h.execution_history.append(event)
                raise VMHostError(
                    code="REPLAY_CACHE_MISS",
                    message=(f"Replay requires LLM_RESPONSE_CACHED event for "
                             f"content_key={content_key[:16]}..."),
                    symbol="llm.request",
                )
            return cached

        # --- 3. Live mode: check live cache (unless policy=never) ---
        if cache_policy != "never":
            cached = self._llm_cache_lookup_live(h, content_key)
            if cached is not _SENTINEL:
                if hasattr(h, "execution_history"):
                    h.execution_history.append({
                        "type": "LLM_RESPONSE_CACHED_HIT",
                        "call_id": call_id,
                        "content_key": content_key,
                        "cache_policy": cache_policy,
                    })
                return cached

        # --- 4. Dispatch to LLM provider ---
        llm_backend = getattr(h, "llm_backend", None)
        if llm_backend is None:
            # Mock mode: return deterministic stub
            result = {
                "text": f"[mock LLM response for {envelope.get('template_hash','')[:8]}]",
                "model_version": model_version,
            }
        else:
            try:
                prompt_text = self._render_prompt(envelope, h)
                raw_result = llm_backend.complete(
                    prompt=prompt_text,
                    model=engine_params.get("model", "default"),
                    temperature=engine_params.get("temperature", 0.0),
                    max_tokens=engine_params.get("max_tokens", 512),
                )
                result = raw_result if isinstance(raw_result, dict) else {"text": str(raw_result)}
                result.setdefault("model_version", model_version)
            except TimeoutError as exc:
                event = {"type": "LLM_HOST_FAILURE", "call_id": call_id,
                         "retryable": False, "error": str(exc)}
                if hasattr(h, "execution_history"):
                    h.execution_history.append(event)
                raise VMHostError(code="LLM_TIMEOUT",
                                  message=str(exc), symbol="llm.request")
            except Exception as exc:
                event = {"type": "LLM_HOST_FAILURE", "call_id": call_id,
                         "retryable": True, "error": str(exc)}
                if hasattr(h, "execution_history"):
                    h.execution_history.append(event)
                raise VMHostError(code="LLM_PROVIDER_ERROR",
                                  message=str(exc), symbol="llm.request")

        # --- 5. Bridge-side schema validation (CVM never sees raw response) ---
        self._llm_validate_schema(result, schema_hash, h, call_id)

        # --- 6. Store in cache and history ---
        self._llm_cache_store(h, content_key, result, model_version,
                               schema_hash, call_id)

        return result

    def _render_prompt(self, envelope: dict, h: Any) -> str:
        """Render prompt envelope into text for LLM provider call."""
        variables = envelope.get("variables", {})
        template_hash = envelope.get("template_hash", "")
        # Look up template text from host registry
        template_registry = getattr(h, "llm_template_registry", {})
        template_text = template_registry.get(template_hash)
        if template_text:
            try:
                return template_text.format(**variables)
            except (KeyError, ValueError):
                pass
        # Fallback: render variables as plain text
        if "__text__" in variables:
            return str(variables["__text__"])
        parts = [f"{k}: {v}" for k, v in variables.items()]
        return "\n".join(parts) if parts else "[empty prompt]"

    def _check_capability(self, symbol: str, ctx: HostCallContext) -> None:
        """Fail closed when a bridge-dispatched host symbol is not authorized.

        On denial, writes two deterministic events to execution_history:
          1. LLM_REQUEST_DENIED  (or CAPABILITY_DENIED for non-LLM symbols)
             with full context for security audit.
          2. The VMHostError propagates to the VM stack.

        event payload (alpha3e-track-b):
          capability_missing:  the specific required capability
          required_capability: same (explicit field for audit tooling)
          agent_capabilities:  sorted snapshot of agent's current capability set
          agent_id:            agent making the request
          request_symbol:      the host symbol that triggered the check
          history_hash:        tamper-evident chain link
        """
        import hashlib as _hl, json as _j
        if symbol not in SYMBOL_TO_CAPABILITY:
            raise VMHostError(
                code="UNKNOWN_SYMBOL",
                message=f"Unknown symbol: {symbol}",
                symbol=symbol,
            )
        required = SYMBOL_TO_CAPABILITY[symbol]
        if required not in ctx.capabilities:
            h = self.get_host()
            agent_caps_sorted = sorted(list(ctx.capabilities))

            # Compute tamper-evident hash for this denial event
            prev_hash = ""
            history = getattr(h, "execution_history", []) or []
            if history:
                prev_hash = history[-1].get("history_hash", "")
            event_src = _j.dumps(
                {"prev": prev_hash, "symbol": symbol, "agent_id": ctx.agent_id,
                 "required": required},
                sort_keys=True, separators=(",", ":"),
            )
            event_hash = _hl.sha256(event_src.encode()).hexdigest()

            # Event 1: semantic denial record
            denial_event = {
                "type": "LLM_REQUEST_DENIED" if symbol == "llm.request" else "CAPABILITY_DENIED",
                "capability_missing": required,
                "required_capability": required,
                "agent_capabilities": agent_caps_sorted,
                "agent_id": ctx.agent_id,
                "request_symbol": symbol,
                # instruction_pointer: current VM IP at the moment of denial.
                # Critical for debugging compiled guard/policy bytecode — without
                # IP, audit logs cannot pinpoint which instruction triggered denial.
                "instruction_pointer": ctx.instruction_pointer,
                "history_hash": event_hash,
            }
            if hasattr(h, "execution_history"):
                h.execution_history.append(denial_event)

            # Event 2: capability missing record (second layer for security audit)
            missing_event = {
                "type": "LLM_CAPABILITY_MISSING" if symbol == "llm.request" else "CAPABILITY_MISSING",
                "capability_missing": required,
                "agent_id": ctx.agent_id,
                "history_hash": _hl.sha256(
                    (event_hash + required).encode()
                ).hexdigest(),
            }
            if hasattr(h, "execution_history"):
                h.execution_history.append(missing_event)

            raise VMHostError(
                code="CAPABILITY_DENIED",
                message=(
                    f"Agent '{ctx.agent_id}' lacks required capability '{required}'. "
                    f"Agent has: {agent_caps_sorted}"
                ),
                symbol=symbol,
            )

    def dispatch_host_call(self, vm: Any, symbol: str, args: list) -> Any:
        """Dispatch Alpha.3 CALL_HOST calls without intercepting legacy HOST_ABI opcodes."""
        if self._nested_host_call_depth > 0:
            raise VMHostError(
                code="NESTED_HOST_CALL",
                message="Nested CALL_HOST not allowed",
                symbol=symbol,
            )
        # Guard violation enforcement: block side-effecting calls while
        # guard_violation_active is True. Must happen before capability check
        # so that the security state is respected regardless of capabilities.
        self._check_guard_violation_enforcement(vm, symbol)
        try:
            self._nested_host_call_depth += 1
            if symbol in VM_LOCAL_DETERMINISTIC:
                return vm._call_host_builtin(symbol, args)
            if symbol in VM_LOCAL_SIDE_EFFECT:
                result = vm._call_host_builtin(symbol, args)
                h = self.get_host()
                self._ensure_history_streams(h)
                event = {
                    "type": "deterministic_side_effect",
                    "symbol": symbol,
                    "args": copy.deepcopy(args),
                    "result": result,
                    "event_id": self._next_host_event_id(h),
                }
                h.side_effect_history.append(event)
                return result
            if symbol in VM_STRUCTURAL_RUNTIME:
                return self._execute_structural_runtime(vm, symbol, args)
            if symbol in BRIDGE_DISPATCHED:
                ctx = self._get_host_call_context(vm)
                self._check_capability(symbol, ctx)
                return self._execute_bridge_dispatched(symbol, args)
            raise VMHostError(
                code="UNKNOWN_SYMBOL",
                message=f"Unknown symbol: {symbol}",
                symbol=symbol,
            )
        finally:
            self._nested_host_call_depth -= 1

    def _next_host_event_id(self, h: Any) -> str:
        next_id = getattr(h, "next_event_id", None)
        if callable(next_id):
            return next_id()
        return f"evt-{len(getattr(h, 'execution_history', []) or []):08d}"

    def _emit_context_event(self, h: Any, event: Dict[str, Any]) -> None:
        if hasattr(h, "execution_history"):
            h.execution_history.append(event)
        emit = getattr(h, "emit_runtime_event", None)
        if callable(emit):
            emit(event, getattr(h, "current_env", None))

    def _execute_structural_runtime(self, vm: Any, symbol: str, args: list) -> Any:
        """Execute non-capability-gated structural runtime host calls."""
        h = self.get_host()
        label = str(args[0]) if args else ""
        reason = str(args[1]) if len(args) > 1 else None

        try:
            if symbol in {"SYS_CONTEXT_ENTER", "SYS_CONTEXT_EXIT"}:
                tracker = getattr(h, "context_tracker", None)
                if tracker is None:
                    raise VMHostError(
                        code="CONTEXT_TRACKER_MISSING",
                        message="ContextTracker is required for context structural runtime calls",
                        symbol=symbol,
                    )
                if symbol == "SYS_CONTEXT_ENTER":
                    event = tracker.enter_event(label)
                    event["event_id"] = self._next_host_event_id(h)
                    if hasattr(h, "current_context"):
                        h.current_context = tracker.current
                    self._emit_context_event(h, event)
                    return None

                event = tracker.exit_event(label)
                event["event_id"] = self._next_host_event_id(h)
                if reason:
                    event["unwind_reason"] = reason
                if hasattr(h, "current_context"):
                    h.current_context = tracker.current
                self._emit_context_event(h, event)
                return None

            if symbol in {"SYS_ACTOR_ENTER", "SYS_ACTOR_EXIT"}:
                actor_runtime = getattr(h, "actor_runtime", None)
                if actor_runtime is None:
                    raise VMHostError(
                        code="ACTOR_RUNTIME_MISSING",
                        message="ActorRuntime is required for actor structural runtime calls",
                        symbol=symbol,
                    )
                metadata = args[1] if len(args) > 1 and isinstance(args[1], dict) else {}
                if symbol == "SYS_ACTOR_ENTER":
                    event = actor_runtime.enter_event(label, metadata)
                    event["event_id"] = self._next_host_event_id(h)
                    if hasattr(h, "current_actor"):
                        h.current_actor = actor_runtime.current
                    self._emit_context_event(h, event)
                    return None

                event = actor_runtime.exit_event(label)
                event["event_id"] = self._next_host_event_id(h)
                if reason:
                    event["unwind_reason"] = reason
                if hasattr(h, "current_actor"):
                    h.current_actor = actor_runtime.current
                self._emit_context_event(h, event)
                return None

            if symbol in {"SYS_POLICY_ENTER", "SYS_POLICY_EXIT", "SYS_POLICY_RULE_ENTER", "SYS_POLICY_RULE_EXIT"}:
                governance = getattr(h, "governance_engine", None)
                if governance is None and hasattr(h, "runtime"):
                    governance = getattr(h.runtime, "governance", None)
                metadata = args[1] if len(args) > 1 and isinstance(args[1], dict) else {}

                # Canonical policy runtime parity path.  Older hosts do not have
                # a dedicated PolicyRuntime, so VMBridge owns a minimal structural
                # stack adapter while keeping governance semantics out of CVM.
                if not hasattr(h, "policy_stack"):
                    h.policy_stack = []
                if not hasattr(h, "current_policy"):
                    h.current_policy = None
                if not hasattr(h, "current_policy_rule"):
                    h.current_policy_rule = None

                if symbol == "SYS_POLICY_ENTER":
                    if governance is not None and hasattr(governance, "enter_event"):
                        event = governance.enter_event(label, metadata)
                    else:
                        h.policy_stack.append(label)
                        event = {
                            "type": "policy_entered",
                            "policy": label,
                            "label": label,
                            "metadata": metadata,
                            "depth": len(h.policy_stack),
                        }
                    event["event_id"] = self._next_host_event_id(h)
                    h.current_policy = label
                    self._emit_context_event(h, event)
                    return None

                if symbol == "SYS_POLICY_EXIT":
                    if governance is not None and hasattr(governance, "exit_event"):
                        event = governance.exit_event(label)
                    else:
                        if getattr(h, "policy_stack", None) and h.policy_stack[-1] == label:
                            h.policy_stack.pop()
                        elif label in getattr(h, "policy_stack", []):
                            h.policy_stack.remove(label)
                        event = {
                            "type": "policy_exited",
                            "policy": label,
                            "label": label,
                            "depth": len(getattr(h, "policy_stack", [])),
                        }
                    event["event_id"] = self._next_host_event_id(h)
                    if reason:
                        event["unwind_reason"] = reason
                    h.current_policy = h.policy_stack[-1] if getattr(h, "policy_stack", []) else None
                    self._emit_context_event(h, event)
                    return None

                if symbol == "SYS_POLICY_RULE_ENTER":
                    if governance is not None and hasattr(governance, "rule_enter_event"):
                        event = governance.rule_enter_event(label, metadata)
                    else:
                        h.policy_stack.append(label)
                        event = {
                            "type": "policy_rule_entered",
                            "rule": label,
                            "label": label,
                            "metadata": metadata,
                            "depth": len(h.policy_stack),
                        }
                    event["event_id"] = self._next_host_event_id(h)
                    h.current_policy_rule = label
                    self._emit_context_event(h, event)
                    return None

                if governance is not None and hasattr(governance, "rule_exit_event"):
                    event = governance.rule_exit_event(label)
                else:
                    if getattr(h, "policy_stack", None) and h.policy_stack[-1] == label:
                        h.policy_stack.pop()
                    elif label in getattr(h, "policy_stack", []):
                        h.policy_stack.remove(label)
                    event = {
                        "type": "policy_rule_exited",
                        "rule": label,
                        "label": label,
                        "depth": len(getattr(h, "policy_stack", [])),
                    }
                event["event_id"] = self._next_host_event_id(h)
                if reason:
                    event["unwind_reason"] = reason
                h.current_policy_rule = next((x for x in reversed(getattr(h, "policy_stack", [])) if ":rule:" in str(x)), None)
                self._emit_context_event(h, event)
                return None



            if symbol == "llm.request":
                return self._execute_llm_request(h, args)

            if symbol in {"SYS_MSG_SEND", "SYS_MSG_CONSUME", "SYS_MSG_RECEIVE"}:
                if symbol == "SYS_MSG_SEND":
                    message = copy.deepcopy(args[0]) if args and isinstance(args[0], dict) else {}
                    sender = str(message.get("sender_id") or message.get("sender") or "")
                    receiver = str(message.get("target_id") or message.get("receiver") or "")
                    msg_type = str(message.get("msg_type") or message.get("method") or "")
                    payload = message.get("payload")
                    actor_runtime = getattr(h, "actor_runtime", None)
                    if actor_runtime is not None and hasattr(actor_runtime, "send_message"):
                        # actor_runtime is the canonical mailbox authority and
                        # already records message_sent history. Avoid double logging.
                        return actor_runtime.send_message(sender, receiver, msg_type, [payload])

                    if not hasattr(h, "mailboxes"):
                        h.mailboxes = {}
                    if not hasattr(h, "actor_log"):
                        h.actor_log = []
                    h.mailboxes.setdefault(receiver, []).append(message)
                    delivered = message
                    event = {
                        "type": "message_sent",
                        "event_id": self._next_host_event_id(h),
                        "message": copy.deepcopy(message),
                    }
                    self._emit_context_event(h, event)
                    return delivered

                if symbol == "SYS_MSG_CONSUME":
                    event = copy.deepcopy(args[0]) if args and isinstance(args[0], dict) else {"type": "message_consumed"}
                    event.setdefault("type", "message_consumed")
                    event.setdefault("event_id", self._next_host_event_id(h))
                    self._emit_context_event(h, event)
                    return event

                # SYS_MSG_RECEIVE is a bridge-side hook for future host inbox sync.
                # Commit 2 keeps CVM receive decisions over VMState.mailbox_inbound.
                receiver = str(args[0]) if args else ""
                mailbox = getattr(h, "mailboxes", {}).setdefault(receiver, []) if hasattr(h, "mailboxes") else []
                return mailbox[0] if mailbox else None
        except VMHostError:
            raise
        except Exception as exc:
            code = "POLICY_RUNTIME_ERROR" if symbol.startswith("SYS_POLICY_") else ("ACTOR_RUNTIME_ERROR" if symbol.startswith("SYS_ACTOR_") else "CONTEXT_RUNTIME_ERROR")
            raise VMHostError(
                code=code,
                message=f"Structural runtime error in {symbol}: {exc}",
                symbol=symbol,
            ) from exc

        raise VMHostError(
            code="UNKNOWN_SYMBOL",
            message=f"Unknown structural runtime symbol: {symbol}",
            symbol=symbol,
        )

    def _execute_bridge_dispatched(self, symbol: str, args: list) -> Any:
        """Execute authorized bridge-dispatched symbols against runtime engines."""
        h = self.get_host()
        try:
            if symbol == "SYS_MEMORY_READ":
                room = args[0] if args else "default"
                query = args[1] if len(args) > 1 else None
                if hasattr(h, "memory_palace"):
                    try:
                        return h.memory_palace.recall(room, query)
                    except TypeError:
                        return h.memory_palace.recall(room)
                return []
            if symbol == "SYS_MEMORY_WRITE":
                room = args[0] if args else "default"
                content = args[1] if len(args) > 1 else ""
                metadata = args[2] if len(args) > 2 else {}
                if hasattr(h, "memory_palace"):
                    try:
                        return h.memory_palace.imprint(room, content, metadata)
                    except TypeError:
                        return h.memory_palace.imprint(room, content)
                return "mock"
            if symbol == "SYS_AFFECTIVE_READ":
                if hasattr(h, "affective_state"):
                    return h.affective_state.to_dict()
                if hasattr(h, "affective_states") and h.affective_states:
                    state = next(iter(h.affective_states.values()))
                    return state.to_dict() if hasattr(state, "to_dict") else dict(getattr(state, "current", {}) or {})
                return {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}
            if symbol == "SYS_AFFECTIVE_EVENT":
                name = args[0] if args else "unknown"
                delta = args[1] if len(args) > 1 else {}
                if hasattr(h, "affective_state") and hasattr(h.affective_state, "apply_event"):
                    return h.affective_state.apply_event(name, delta)
                if hasattr(h, "runtime") and hasattr(h.runtime, "affective"):
                    return h.runtime.affective.apply_delta(delta, source=name)
                return None
            if symbol == "SYS_POLICY_CHECK":
                action = args[0] if args else "unknown"
                context = args[1] if len(args) > 1 else {}
                if hasattr(h, "governance_engine"):
                    return h.governance_engine.evaluate(action, context)
                if hasattr(h, "runtime") and hasattr(h.runtime, "governance"):
                    return h.runtime.governance.evaluate(action, context)
                return {"allowed": True}


            if symbol in {"SYS_MSG_SEND", "SYS_MSG_CONSUME", "SYS_MSG_RECEIVE"}:
                if symbol == "SYS_MSG_SEND":
                    message = copy.deepcopy(args[0]) if args and isinstance(args[0], dict) else {}
                    sender = str(message.get("sender_id") or message.get("sender") or "")
                    receiver = str(message.get("target_id") or message.get("receiver") or "")
                    msg_type = str(message.get("msg_type") or message.get("method") or "")
                    payload = message.get("payload")
                    actor_runtime = getattr(h, "actor_runtime", None)
                    if actor_runtime is not None and hasattr(actor_runtime, "send_message"):
                        # actor_runtime is the canonical mailbox authority and
                        # already records message_sent history. Avoid double logging.
                        return actor_runtime.send_message(sender, receiver, msg_type, [payload])

                    if not hasattr(h, "mailboxes"):
                        h.mailboxes = {}
                    if not hasattr(h, "actor_log"):
                        h.actor_log = []
                    h.mailboxes.setdefault(receiver, []).append(message)
                    delivered = message
                    event = {
                        "type": "message_sent",
                        "event_id": self._next_host_event_id(h),
                        "message": copy.deepcopy(message),
                    }
                    self._emit_context_event(h, event)
                    return delivered

                if symbol == "SYS_MSG_CONSUME":
                    event = copy.deepcopy(args[0]) if args and isinstance(args[0], dict) else {"type": "message_consumed"}
                    event.setdefault("type", "message_consumed")
                    event.setdefault("event_id", self._next_host_event_id(h))
                    self._emit_context_event(h, event)
                    return event

                # SYS_MSG_RECEIVE is a bridge-side hook for future host inbox sync.
                # Commit 2 keeps CVM receive decisions over VMState.mailbox_inbound.
                receiver = str(args[0]) if args else ""
                mailbox = getattr(h, "mailboxes", {}).setdefault(receiver, []) if hasattr(h, "mailboxes") else []
                return mailbox[0] if mailbox else None
        except VMHostError:
            raise
        except (TypeError, ValueError, AttributeError, IndexError) as exc:
            raise VMHostError(
                code="HOST_ERROR",
                message=f"Host execution error in {symbol}: {exc}",
                symbol=symbol,
            ) from exc
        raise VMHostError(
            code="UNKNOWN_SYMBOL",
            message=f"Unknown dispatch: {symbol}",
            symbol=symbol,
        )

    def get_cvm_callback_adapter(self, vm: Any):
        """Return a CVM host callback that only routes CALL_HOST through Alpha.3 dispatch.

        Legacy v2.1 HOST_ABI opcodes (IMPRINT, RECALL, METRICS, etc.) continue to
        flow through vm_host_call unchanged.
        """
        def _callback(opcode: str, a: Any, b: Any) -> Any:
            if opcode == "CALL_HOST" and isinstance(a, dict):
                return self.dispatch_host_call(vm, a.get("symbol"), a.get("args", []))
            return self.vm_host_call(opcode, a, b)
        return _callback

    def sync_context_tracker_from_vm_state(self, vm: CognitiveVM) -> None:
        """Hydrate host ContextTracker from restored VM context_stack without emitting events."""
        try:
            h = self.get_host()
            tracker = getattr(h, "context_tracker", None)
            if tracker is not None and hasattr(tracker, "stack"):
                tracker.stack = list(getattr(vm.state, "context_stack", []) or [])
                if hasattr(h, "current_context"):
                    h.current_context = tracker.current
            elif hasattr(h, "current_context"):
                stack = list(getattr(vm.state, "context_stack", []) or [])
                h.current_context = stack[-1] if stack else None
        except Exception:
            pass



    def sync_actor_runtime_from_vm_state(self, vm: CognitiveVM) -> None:
        """Hydrate host ActorRuntime from restored VM actor_stack without emitting events."""
        try:
            h = self.get_host()
            actor_runtime = getattr(h, "actor_runtime", None)
            stack = list(getattr(vm.state, "actor_stack", []) or [])
            if actor_runtime is not None and hasattr(actor_runtime, "sync_from_cvm_stack"):
                actor_runtime.sync_from_cvm_stack(stack)
            else:
                h.actor_stack = stack
                if hasattr(h, "current_actor"):
                    h.current_actor = stack[-1] if stack else None
        except Exception:
            pass

    def sync_policy_runtime_from_vm_state(self, vm: CognitiveVM) -> None:
        """Hydrate host policy stack from restored VM policy_stack without emitting events."""
        try:
            h = self.get_host()
            stack = list(getattr(vm.state, "policy_stack", []) or [])
            h.policy_stack = stack
            h.current_policy = next((x for x in reversed(stack) if ":rule:" not in str(x)), None)
            h.current_policy_rule = next((x for x in reversed(stack) if ":rule:" in str(x)), None)
        except Exception:
            pass

    def unwind_actor(self, vm: CognitiveVM, actor_name: str, reason: str = "runtime_unwind") -> None:
        """Best-effort host-level actor unwind, idempotent and no-throw."""
        try:
            h = self.get_host()
            actor_runtime = getattr(h, "actor_runtime", None)
            if actor_runtime is not None and hasattr(actor_runtime, "exit_event"):
                event = actor_runtime.exit_event(actor_name)
            else:
                event = {"type": "actor_exited", "actor": actor_name, "label": actor_name}
            if isinstance(event, dict):
                if hasattr(h, "next_event_id"):
                    event["event_id"] = h.next_event_id()
                event["unwind_reason"] = reason
                if actor_runtime is not None and hasattr(actor_runtime, "current") and hasattr(h, "current_actor"):
                    h.current_actor = actor_runtime.current
                if hasattr(h, "execution_history"):
                    h.execution_history.append(event)
                emit = getattr(h, "emit_runtime_event", None)
                if callable(emit):
                    emit(event, getattr(h, "current_env", None))
            if hasattr(vm.state, "actor_stack") and vm.state.actor_stack:
                if vm.state.actor_stack[-1] == actor_name:
                    vm.state.actor_stack.pop()
                elif actor_name in vm.state.actor_stack:
                    vm.state.actor_stack.remove(actor_name)
        except Exception:
            pass

    def unwind_dangling_actors(self, vm: CognitiveVM, dangling_stack: list, reason: str = "runtime_unwind") -> None:
        """Unwind a stack of dangling actor scopes in LIFO order, no-throw."""
        while dangling_stack:
            actor_name = dangling_stack.pop()
            self.unwind_actor(vm, actor_name, reason=reason)

    def unwind_policy(self, vm: CognitiveVM, name: str, reason: str = "runtime_unwind") -> None:
        """Best-effort host-level policy unwind used by policy_stack RAII."""
        try:
            h = self.get_host()
            symbol = "SYS_POLICY_RULE_EXIT" if ":rule:" in str(name) else "SYS_POLICY_EXIT"
            self._execute_structural_runtime(vm, symbol, [name, reason])
            if hasattr(vm.state, "policy_stack") and vm.state.policy_stack:
                if vm.state.policy_stack[-1] == name:
                    vm.state.policy_stack.pop()
                elif name in vm.state.policy_stack:
                    vm.state.policy_stack.remove(name)
        except Exception:
            pass

    def unwind_dangling_policies(self, vm: CognitiveVM, dangling_stack: list, reason: str = "runtime_unwind") -> None:
        """Unwind a stack of dangling policy scopes in LIFO order, no-throw."""
        while dangling_stack:
            name = dangling_stack.pop()
            self.unwind_policy(vm, name, reason=reason)

    def unwind_context(self, vm: CognitiveVM, label: str, reason: str = "runtime_unwind") -> None:
        """Best-effort host-level context unwind used by future ContextBlock support.

        This method is intentionally idempotent and no-throw: it must not mask
        an OutOfEnergy, VMHostError, assertion failure, or any original VM error.
        """
        try:
            h = self.get_host()
            tracker = getattr(h, "context_tracker", None)
            event = None
            if tracker is not None and hasattr(tracker, "exit_event"):
                event = tracker.exit_event(label)
            else:
                event = {"type": "context_exited", "label": label}
            if isinstance(event, dict):
                if hasattr(h, "next_event_id"):
                    event["event_id"] = h.next_event_id()
                event["unwind_reason"] = reason
                if hasattr(tracker, "current"):
                    h.current_context = tracker.current
                if hasattr(h, "execution_history"):
                    h.execution_history.append(event)
                emit = getattr(h, "emit_runtime_event", None)
                if callable(emit):
                    emit(event, getattr(h, "current_env", None))
            if hasattr(vm.state, "context_stack") and vm.state.context_stack:
                if vm.state.context_stack[-1] == label:
                    vm.state.context_stack.pop()
                elif label in vm.state.context_stack:
                    vm.state.context_stack.remove(label)
        except Exception:
            pass

    def unwind_dangling_contexts(self, vm: CognitiveVM, dangling_stack: list, reason: str = "runtime_unwind") -> None:
        """Unwind a stack of dangling contexts in LIFO order, no-throw."""
        while dangling_stack:
            label = dangling_stack.pop()
            self.unwind_context(vm, label, reason=reason)

    def run_cvm_with_context_safety(self, vm: CognitiveVM, *args, **kwargs) -> Dict[str, Any]:
        """Run CVM with Python-level context-stack safety net.

        Commit 1 only introduces the infrastructure. Future ContextBlock support
        will route SYS_CONTEXT_ENTER/EXIT through this bridge; this wrapper keeps
        context cleanup centralized without changing the core vm.run contract.
        """
        try:
            return vm.run(*args, **kwargs)
        except Exception as exc:
            if getattr(vm.state, "actor_stack", None):
                self.unwind_dangling_actors(
                    vm,
                    vm.state.actor_stack,
                    reason=f"exception:{type(exc).__name__}",
                )
            if getattr(vm.state, "policy_stack", None):
                self.unwind_dangling_policies(
                    vm,
                    vm.state.policy_stack,
                    reason=f"exception:{type(exc).__name__}",
                )
            if getattr(vm.state, "context_stack", None):
                self.unwind_dangling_contexts(
                    vm,
                    vm.state.context_stack,
                    reason=f"exception:{type(exc).__name__}",
                )
            raise
        finally:
            if getattr(vm.state, "actor_stack", None):
                self.unwind_dangling_actors(
                    vm,
                    vm.state.actor_stack,
                    reason="vm_halted",
                )
            if getattr(vm.state, "policy_stack", None):
                self.unwind_dangling_policies(
                    vm,
                    vm.state.policy_stack,
                    reason="vm_halted",
                )
            if getattr(vm.state, "context_stack", None):
                self.unwind_dangling_contexts(
                    vm,
                    vm.state.context_stack,
                    reason="vm_halted",
                )

    def evaluate_run_vm(self, node, env) -> Dict[str, Any]:
        h = self.get_host()
        if node.source is not None and node.resume_from is not None:
            raise VMConflictingSourceError("run vm cannot use both source and resume_from")
        gas = int(h.evaluate(node.gas, env)) if node.gas else h.energy_budget_default
        cognitive_budget = int(node.cognitive_budget) if node.cognitive_budget is not None else None

        if node.resume_from is not None:
            label = str(h.evaluate(node.resume_from, env))
            vm = self.restore_vm_from_checkpoint(label, gas=gas, cognitive_budget=cognitive_budget)
        else:
            program_value = h.evaluate(node.source, env) if node.source else env.get("bytecode")
            program = BytecodeProgram.from_dict(program_value) if isinstance(program_value, dict) else program_value
            vm = CognitiveVM(program, host=None)
            vm.host = self.get_cvm_callback_adapter(vm)
            vm.state.gas_remaining = gas
            vm.state.cognitive_budget_remaining = cognitive_budget
            vm.state.current_context = h.current_context

        saved_snapshot = None
        def should_checkpoint(candidate_vm: CognitiveVM) -> bool:
            trig = node.checkpoint_trigger
            if trig is None:
                return False
            if isinstance(trig, AtIpTrigger):
                return candidate_vm.state.ip == trig.ip
            if isinstance(trig, BeforeOpTrigger):
                if candidate_vm.state.ip >= len(candidate_vm.program.instructions):
                    return False
                return candidate_vm.program.instructions[candidate_vm.state.ip].op == trig.op
            return False

        def save_checkpoint(candidate_vm: CognitiveVM):
            nonlocal saved_snapshot
            label = node.checkpoint_label or "checkpoint"
            saved_snapshot = self.make_vm_snapshot(candidate_vm, label)
            h.vm_checkpoints[label]["program"] = candidate_vm.program.to_dict()
            trigger_kind = "at_ip" if isinstance(node.checkpoint_trigger, AtIpTrigger) else "before_op"
            trigger_value = getattr(node.checkpoint_trigger, "ip", getattr(node.checkpoint_trigger, "op", None))
            h.execution_history[-1]["trigger"] = {"kind": trigger_kind, "value": trigger_value}

        try:
            result = self.run_cvm_with_context_safety(vm, checkpoint_trigger=should_checkpoint, checkpoint_callback=save_checkpoint)
        except OutOfEnergy as exc:
            result = {"halted": False, "error": "OUT_OF_ENERGY", "message": str(exc), "snapshot": vm.snapshot()}
        snapshot = vm.snapshot()
        h.vm_snapshots.append(snapshot)
        env.define(node.binding, result)
        event = {"type": "vm_executed", "result": result, "transition_hash": snapshot["state"].get("transition_hash"), "trace_id": h.current_trace_id()}
        if saved_snapshot:
            event["checkpoint_label"] = node.checkpoint_label
        h.execution_history.append(event)
        return result
