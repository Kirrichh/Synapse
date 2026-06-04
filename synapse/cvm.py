"""Synapse v2.2 Cognitive VM.

v2.2 добавляет полный execution path для управляющих структур и функций:
- Управляющие переходы: JUMP, JUMP_IF_FALSE, JUMP_IF_TRUE
- Вызов функций: CALL, CALL_HOST, CALL_METHOD, RETURN, MAKE_FUNCTION
- Арифметика: ADD, SUB, MUL, DIV, MOD
- Сравнения: EQ, NEQ, LT, GT, LTE, GTE
- Логика: AND, OR, NOT, UNARY_NEG
- Структуры: BUILD_LIST, BUILD_DICT, INDEX, MEMBER, DUP
- Литералы: LOAD_NONE, LOAD_TRUE, LOAD_FALSE
- Все HOST_ABI из v2.1 сохранены без изменений.

Фреймы функций реализованы как CallFrame-стек внутри VM,
без рекурсии Python — для replay-safety и gas metering.
"""
from __future__ import annotations

import hashlib
import json
import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Iterable

from .bytecode import BytecodeProgram
from .runtime.host_abi import HOST_ABI_VERSION

# ---------------------------------------------------------------------------
# HOST ABI — фиксированная таблица v2.1, расширяемая в v2.3+
# ---------------------------------------------------------------------------
HOST_ABI: Dict[str, str] = {
    "SEND":           "host.send",
    "RECEIVE":        "host.receive",
    "IMPRINT":        "host.memory.imprint",
    "RECALL":         "host.memory.recall",
    "METRICS":        "host.metrics_snapshot",
    "AFFECT_EVENT":   "host.affective.apply_event",
    "AFFECT_STATE":   "host.affective.get_state",
    "FRACTURE_SELF":  "host.fracture",
    "DREAM":          "host.dream",
    "LLM_EVAL":       "host.llm",
    "HABIT_SUGGEST":  "host.habit.suggest",
    "THRESHOLD_CHECK":"host.threshold.check",
}

# v2.2 расширенная таблица стоимости газа
GAS_COSTS: Dict[str, int] = {
    # Базовые — дёшевы
    "LOAD_CONST": 1, "LOAD_NAME": 1, "STORE": 1, "POP": 1, "DUP": 1,
    "SAVE_NAME": 1, "RESTORE_NAME": 1,
    "LOAD_NONE": 1, "LOAD_TRUE": 1, "LOAD_FALSE": 1,
    # Управляющие
    "JUMP": 1, "JUMP_IF_FALSE": 1, "JUMP_IF_TRUE": 1,
    "RETURN": 2, "CALL": 5, "CALL_HOST": 5, "CALL_METHOD": 5,
    "CONTEXT_ENTER": 3, "CONTEXT_EXIT": 3,
    "ACTOR_ENTER": 3, "ACTOR_EXIT": 3,
    "POLICY_ENTER": 3, "POLICY_EXIT": 3,
    "POLICY_RULE_ENTER": 3, "POLICY_RULE_EXIT": 3,
    "MAKE_FUNCTION": 3,
    # Арифметика
    "ADD": 1, "SUB": 1, "MUL": 2, "DIV": 2, "MOD": 2,
    # Сравнения
    "EQ": 1, "NEQ": 1, "LT": 1, "GT": 1, "LTE": 1, "GTE": 1,
    # Логика
    "AND": 1, "OR": 1, "NOT": 1, "UNARY_NEG": 1,
    # Структуры
    "BUILD_LIST": 2, "BUILD_DICT": 3, "INDEX": 2, "MEMBER": 2,
    # HOST ABI — дороже
    "IMPRINT": 5, "RECALL": 5, "AFFECT_EVENT": 3, "AFFECT_STATE": 3,
    "FRACTURE_SELF": 20, "DREAM": 20, "LLM_EVAL": 25,
    "METRICS": 2, "HOST_EVAL": 10, "SEND": 8, "RECEIVE": 8,
    "MSG_SEND": 5, "MSG_RECEIVE": 5, "RECEIVE_ENTER": 1, "RECEIVE_EXIT": 1,
    "HABIT_SUGGEST": 4, "THRESHOLD_CHECK": 3,
    "HALT": 0,
    # LLM/Prompt CVM Bridge (alpha3e Track A)
    "PROMPT_BUILD": 3, "LLM_REQUEST": 25, "LLM_RESUME": 2,
    # Guard Blocks (alpha3e Track B)
    # GUARD_ENTER/EXIT: audit boundary cost — slightly more than POLICY_ENTER/EXIT
    # to discourage excessive guard nesting in tight loops.
    # GUARD_CHECK_RESULT: reads bool from stack, zero-cost computation.
    # GUARD_VIOLATION_ACK: internal-only recovery opcode — cheap to encourage
    # explicit recovery paths.
    "GUARD_ENTER": 5, "GUARD_EXIT": 5,
    "GUARD_CHECK_RESULT": 1,
    "GUARD_VIOLATION_ACK": 2,  # internal-only: compiler-inserted in catch(GUARD_VIOLATION)
}

GAS_BACK_EDGE = 2  # per loop iteration (target_ip <= executed_ip)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class VMHostError(Exception):
    """Unified JSON-safe host-call error for CVM CALL_HOST failures."""

    def __init__(self, code: str, message: str, symbol: Optional[str] = None, call_id: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.symbol = symbol
        self.call_id = call_id

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "type": "VMHostError",
            "code": self.code,
            "message": str(self),
            "symbol": self.symbol,
        }
        if self.call_id is not None:
            payload["call_id"] = self.call_id
        return payload


class VMStatus:
    RUNNING = "STATUS_RUNNING"
    PAUSED_HOST_CALL = "STATUS_PAUSED_HOST_CALL"
    PAUSED_MESSAGING = "STATUS_PAUSED_MESSAGING"
    HALTED = "STATUS_HALTED"
    CANCELLED_HOST_CALL = "STATUS_CANCELLED_HOST_CALL"
    HOST_CALL_TIMEOUT = "STATUS_HOST_CALL_TIMEOUT"


class PendingHostCall(dict):
    """Dict payload with backward-compatible equality for B1 tests.

    Alpha.3-D1 expands the envelope, but older hardening tests compared the
    four-field B1 payload exactly. Equality accepts that legacy subset without
    weakening serialization: the actual object still contains the full envelope.
    """
    _legacy_keys = {"symbol", "args", "argc", "call_id"}

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, dict) and set(other.keys()) == self._legacy_keys:
            return {k: self.get(k) for k in self._legacy_keys} == other
        return dict.__eq__(self, other)


def compute_call_id(
    program_hash: str,
    ip: int,
    transition_hash: str,
    event_id: str,
    frame_depth: int,
) -> str:
    """Deterministic content-addressed id for durable host-call replay."""
    seed = f"{program_hash}|{ip}|{transition_hash}|{event_id}|{frame_depth}"
    return "hc-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def generate_host_call_id() -> str:
    """Backward-compatible deterministic fallback for legacy callers.

    D1 code paths use compute_call_id() with the full replay seed. This helper
    remains only for external tests/imports that referenced the B1 symbol.
    """
    return compute_call_id("legacy", 0, "sha256:legacy", "evt--0000001", 0)


def compute_message_consumed_id(
    receiver_id: str,
    msg_type: str,
    sender_id: str,
    transition_hash: str,
    event_id: str,
    payload_hash: str = "",
) -> str:
    """Content-addressed id for deterministic message-consumption replay."""
    seed = f"{receiver_id}|{msg_type}|{sender_id}|{transition_hash}|{event_id}|{payload_hash}"
    return "mc-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


class OutOfEnergy(RuntimeError): pass
class UnknownOpcodeError(RuntimeError): pass
class VMSnapshotFormatError(RuntimeError): pass
class VMConflictingSourceError(RuntimeError): pass
class VMMultipleCheckpointError(RuntimeError): pass
class VMResumeSyncError(RuntimeError):
    def __init__(self, message: str, code: Optional[str] = None, call_id: Optional[str] = None):
        super().__init__(message)
        self.code = code or "VM_RESUME_SYNC_ERROR"
        self.call_id = call_id
class VMTamperDetectedError(RuntimeError): pass
class VMCodeMigrationRequiresMapError(RuntimeError): pass
class VMStackUnderflow(RuntimeError): pass
class VMCallStackOverflow(RuntimeError): pass
class VMAssertionFailed(RuntimeError): pass
class VMError(RuntimeError): pass


# ---------------------------------------------------------------------------
# JSON-safe VM value encoding
# ---------------------------------------------------------------------------

def encode_vm_value(value: Any) -> Any:
    """Convert VM runtime values to a JSON-safe representation."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, FunctionObject):
        return {
            "__vm_type__": "FunctionObject",
            "name": value.name,
            "params": list(value.params),
            "body_ip": value.body_ip,
            "closure": encode_vm_value(value.closure),
            "program_hash": value.program_hash,
        }
    if isinstance(value, dict):
        return {str(k): encode_vm_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode_vm_value(item) for item in value]
    return {"__vm_type__": "opaque", "repr": repr(value)}


def decode_vm_value(data: Any) -> Any:
    """Reconstruct VM runtime values from JSON-safe representation."""
    if data is None or isinstance(data, (bool, int, float, str)):
        return data
    if isinstance(data, list):
        return [decode_vm_value(item) for item in data]
    if isinstance(data, dict):
        vm_type = data.get("__vm_type__")
        if vm_type == "FunctionObject":
            return FunctionObject(
                name=data["name"],
                params=list(data.get("params", [])),
                body_ip=int(data["body_ip"]),
                closure=decode_vm_value(data.get("closure", {})),
                program_hash=data.get("program_hash"),
            )
        if vm_type == "opaque":
            return data.get("repr")
        return {k: decode_vm_value(v) for k, v in data.items()}
    return data


# ---------------------------------------------------------------------------
# Call Frame
# ---------------------------------------------------------------------------

@dataclass
class CallFrame:
    """Один стековый фрейм вызова функции."""
    return_ip: int
    locals_snapshot: Dict[str, Any]   # caller locals при входе в функцию
    fn_name: str = "<anon>"
    program_hash: Optional[str] = None
    body_ip: Optional[int] = None
    stack_base: int = 0
    # Snapshot of caller context stack at function entry (RAII boundary).
    context_stack_snapshot: List[str] = field(default_factory=list)
    # Snapshot of caller actor stack at function entry (RAII boundary).
    actor_stack_snapshot: List[str] = field(default_factory=list)
    # Snapshot of caller policy stack at function entry (RAII boundary).
    policy_stack_snapshot: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "return_ip": self.return_ip,
            "locals_snapshot": encode_vm_value(self.locals_snapshot),
            "fn_name": self.fn_name,
            "program_hash": self.program_hash,
            "body_ip": self.body_ip,
            "stack_base": self.stack_base,
            "context_stack_snapshot": list(self.context_stack_snapshot),
            "actor_stack_snapshot": list(self.actor_stack_snapshot),
            "policy_stack_snapshot": list(self.policy_stack_snapshot),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CallFrame":
        return cls(
            return_ip=int(data["return_ip"]),
            locals_snapshot=decode_vm_value(data.get("locals_snapshot", {})),
            fn_name=data.get("fn_name", "<anon>"),
            program_hash=data.get("program_hash"),
            body_ip=data.get("body_ip"),
            stack_base=int(data.get("stack_base", 0)),
            context_stack_snapshot=list(data.get("context_stack_snapshot", [])),
            actor_stack_snapshot=list(data.get("actor_stack_snapshot", [])),
            policy_stack_snapshot=list(data.get("policy_stack_snapshot", [])),
        )


# ---------------------------------------------------------------------------
# VM State
# ---------------------------------------------------------------------------


@dataclass
class GuardFrame:
    """Typed, immutable guard frame for guard_stack LIFO.

    frozen=True means any change to verdict requires dataclasses.replace(),
    which creates a new object. This prevents shared-reference bugs where
    snapshot copies accidentally point to the same mutable dict as the live
    guard_stack. Follows the same immutable-frame pattern as CallFrame.

    Fields:
      guard_id:               unique identifier for this guard scope (UUID or hash-based)
      policy_hash:            SHA-256 of the policy that owns this guard
      guard_hash:             SHA-256 of the guard body / expression
      verdict:                PENDING until GUARD_EXIT resolves it to PASS or FAIL
      entered_at_ip:          vm.state.ip at GUARD_ENTER — for audit trail and debugger
      entered_at_history_hash: history_hash at GUARD_ENTER — tamper-evident anchor
      parent_guard_id:        guard_id of the enclosing guard (for nested guards, flat history)
    """
    guard_id: str = ""
    policy_hash: str = ""
    guard_hash: str = ""
    verdict: str = "PENDING"          # "PENDING" | "PASS" | "FAIL"
    entered_at_ip: int = 0
    entered_at_history_hash: str = ""
    parent_guard_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "guard_id": self.guard_id,
            "policy_hash": self.policy_hash,
            "guard_hash": self.guard_hash,
            "verdict": self.verdict,
            "entered_at_ip": self.entered_at_ip,
            "entered_at_history_hash": self.entered_at_history_hash,
            "parent_guard_id": self.parent_guard_id,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GuardFrame":
        return cls(
            guard_id=str(d.get("guard_id", "")),
            policy_hash=str(d.get("policy_hash", "")),
            guard_hash=str(d.get("guard_hash", "")),
            verdict=str(d.get("verdict", "PENDING")),
            entered_at_ip=int(d.get("entered_at_ip", 0)),
            entered_at_history_hash=str(d.get("entered_at_history_hash", "")),
            parent_guard_id=d.get("parent_guard_id"),
        )


@dataclass
class VMState:
    ip: int = 0
    stack: List[Any] = field(default_factory=list)
    locals: Dict[str, Any] = field(default_factory=dict)
    call_stack: List[CallFrame] = field(default_factory=list)
    gas_remaining: int = 1000
    cognitive_budget_remaining: Optional[int] = None
    transition_hash: str = "sha256:genesis"
    current_context: Optional[str] = None
    # Durable CVM context stack. Alpha.3-C2 uses this for context-block RAII.
    context_stack: List[str] = field(default_factory=list)
    # Durable structural actor stack. Alpha.3-D3 uses this for AgentDef/SubAgentDef RAII.
    actor_stack: List[str] = field(default_factory=list)
    # Durable structural policy stack. Alpha.3-D4 uses this for PolicyDef/PolicyRule RAII.
    policy_stack: List[str] = field(default_factory=list)
    # LIFO stack for loop variable shadowing: (name, existed_before_scope, old_value)
    name_save_stack: List[tuple[str, bool, Any]] = field(default_factory=list)
    # Serialized async host-call pause state. CognitiveVM.halted remains the runtime halt flag.
    pending_host_call: Optional[Dict[str, Any]] = None
    # Alpha.3-D5 durable mailbox snapshot views. actor_runtime remains the canonical
    # mailbox authority; these fields are serialized snapshots for replay/checkpoint
    # determinism only.
    mailbox_inbound: List[Dict[str, Any]] = field(default_factory=list)
    mailbox_outbound: List[Dict[str, Any]] = field(default_factory=list)
    # Alpha.3-D5 messaging pause envelope. This is intentionally separate from
    # pending_host_call so STATUS_PAUSED_MESSAGING cannot pollute D1/D2 host-call
    # invariants.
    pending_message_receive: Optional[Dict[str, Any]] = None
    # Security principal captured in snapshots. VM-local agent_id is trusted only after VMBridge trust gate.
    agent_id: Optional[str] = None
    # Last terminal VM error payload, used when resume fails into HALTED.
    error: Optional[Dict[str, Any]] = None
    # --- Alpha3e Track B: Guard Blocks ---
    # Orthogonal LIFO guard stack. Independent from policy_stack (which tracks
    # what policy is active) and context_stack. guard_stack tracks which guard
    # is being evaluated. Multiple nested guards are supported via LIFO.
    # Uses typed frozen GuardFrame dataclass — NOT plain Dict — to prevent
    # shared-reference bugs in snapshot copies (mutable Dict + copy.copy =
    # same object in live stack and snapshot). Use dataclasses.replace() to
    # produce modified frames (e.g. verdict PENDING→FAIL).
    guard_stack: List["GuardFrame"] = field(default_factory=list)
    # Bridge-readable enforcement flag. True when an unhandled GUARD_VIOLATION is
    # active. Bridge blocks SIDE_EFFECTING_HOST_SYMBOLS while this flag is True.
    # Set by Bridge on GUARD_FAIL; cleared only via GUARD_VIOLATION_ACK opcode
    # (compiler-inserted at start of catch(GUARD_VIOLATION) handlers) or ACTOR_EXIT.
    # Serialized in VMSnapshot so pause/resume and cross-node migration preserve
    # enforcement state correctly.
    guard_violation_active: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ip": self.ip,
            "stack": encode_vm_value(self.stack),
            "locals": encode_vm_value(self.locals),
            "call_stack": [frame.to_dict() for frame in self.call_stack],
            "gas_remaining": self.gas_remaining,
            "cognitive_budget_remaining": self.cognitive_budget_remaining,
            "transition_hash": self.transition_hash,
            "current_context": self.current_context,
            "context_stack": list(self.context_stack),
            "actor_stack": list(self.actor_stack),
            "policy_stack": list(self.policy_stack),
            "name_save_stack": encode_vm_value([
                {"name": name, "existed": existed, "value": value}
                for name, existed, value in self.name_save_stack
            ]),
            "pending_host_call": encode_vm_value(self.pending_host_call),
            "mailbox_inbound": encode_vm_value(self.mailbox_inbound),
            "mailbox_outbound": encode_vm_value(self.mailbox_outbound),
            "pending_message_receive": encode_vm_value(self.pending_message_receive),
            "agent_id": self.agent_id,
            "error": encode_vm_value(self.error),
            "guard_stack": [f.to_dict() if hasattr(f, "to_dict") else f for f in self.guard_stack],
            "guard_violation_active": self.guard_violation_active,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VMState":
        call_stack = []
        for frame_data in data.get("call_stack", []):
            if isinstance(frame_data, dict):
                call_stack.append(CallFrame.from_dict(frame_data))
            else:
                raise VMSnapshotFormatError(f"Invalid CallFrame format: {frame_data!r}")

        raw_saves = decode_vm_value(data.get("name_save_stack", []))
        name_save_stack = []
        for entry in raw_saves:
            if isinstance(entry, dict):
                name_save_stack.append((entry["name"], bool(entry["existed"]), entry.get("value")))
            elif isinstance(entry, (list, tuple)) and len(entry) == 3:
                name, existed, value = entry
                name_save_stack.append((name, bool(existed), value))
            else:
                raise VMSnapshotFormatError(f"Invalid name_save_stack entry: {entry!r}")

        return cls(
            ip=int(data.get("ip", 0)),
            stack=decode_vm_value(data.get("stack", [])),
            locals=decode_vm_value(data.get("locals", {})),
            call_stack=call_stack,
            gas_remaining=int(data.get("gas_remaining", 1000)),
            cognitive_budget_remaining=data.get("cognitive_budget_remaining"),
            transition_hash=data.get("transition_hash", "sha256:genesis"),
            current_context=data.get("current_context"),
            context_stack=list(data.get("context_stack", [])),
            actor_stack=list(data.get("actor_stack", [])),
            policy_stack=list(data.get("policy_stack", [])),
            name_save_stack=name_save_stack,
            pending_host_call=decode_vm_value(data.get("pending_host_call")),
            mailbox_inbound=decode_vm_value(data.get("mailbox_inbound", [])),
            mailbox_outbound=decode_vm_value(data.get("mailbox_outbound", [])),
            pending_message_receive=decode_vm_value(data.get("pending_message_receive")),
            agent_id=data.get("agent_id"),
            error=decode_vm_value(data.get("error")),
            guard_stack=[GuardFrame.from_dict(f) if isinstance(f, dict) else f for f in data.get("guard_stack", [])],
            guard_violation_active=bool(data.get("guard_violation_active", False)),
        )


# ---------------------------------------------------------------------------
# VM Snapshot (canonical, compatible with v2.1)
# ---------------------------------------------------------------------------

@dataclass
class VMSnapshot:
    version: str
    ip: int
    stack: List[Any]
    locals: Dict[str, Any]
    gas_remaining: int
    cognitive_budget_remaining: Optional[int]
    transition_hash: str
    last_processed_event_id: str
    palace_cursor: Optional[str]
    intention_sp: int
    mood_snapshot: Dict[str, float]
    current_context: Optional[str]
    history_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "ip": self.ip,
            "stack": self.stack,
            "locals": self.locals,
            "gas_remaining": self.gas_remaining,
            "cognitive_budget_remaining": self.cognitive_budget_remaining,
            "transition_hash": self.transition_hash,
            "last_processed_event_id": self.last_processed_event_id,
            "palace_cursor": self.palace_cursor,
            "intention_sp": self.intention_sp,
            "mood_snapshot": self.mood_snapshot,
            "current_context": self.current_context,
            "history_hash": self.history_hash,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VMSnapshot":
        # Совместимость: принимаем и "2.1" и "2.2"
        required = {"ip", "stack", "locals", "gas_remaining",
                    "transition_hash", "last_processed_event_id",
                    "mood_snapshot", "history_hash"}
        missing = required - set(data.keys())
        if missing:
            raise VMSnapshotFormatError(f"Missing VM snapshot fields: {sorted(missing)}")
        version = data.get("version", "2.2")
        if version not in ("2.1", "2.2"):
            raise VMSnapshotFormatError(f"Unsupported VM snapshot version: {version}")
        return cls(
            version=version,
            ip=int(data["ip"]),
            stack=list(data.get("stack", [])),
            locals=dict(data.get("locals", {})),
            gas_remaining=int(data.get("gas_remaining", 0)),
            cognitive_budget_remaining=data.get("cognitive_budget_remaining"),
            transition_hash=str(data.get("transition_hash")),
            last_processed_event_id=str(data.get("last_processed_event_id")),
            palace_cursor=data.get("palace_cursor"),
            intention_sp=int(data.get("intention_sp", 0)),
            mood_snapshot=dict(data.get("mood_snapshot") or {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}),
            current_context=data.get("current_context"),
            history_hash=str(data.get("history_hash")),
        )


# ---------------------------------------------------------------------------
# FunctionObject — скомпилированная функция в CVM
# ---------------------------------------------------------------------------

@dataclass
class FunctionObject:
    name: str
    params: List[str]
    body_ip: int
    closure: Dict[str, Any] = field(default_factory=dict)
    program_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return encode_vm_value(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FunctionObject":
        decoded = decode_vm_value(data)
        if not isinstance(decoded, FunctionObject):
            raise VMSnapshotFormatError(
                f"Expected FunctionObject, got {type(decoded).__name__}"
            )
        return decoded

    def __repr__(self) -> str:
        return f"<fn {self.name} ({', '.join(self.params)}) @ip={self.body_ip}>"


# ---------------------------------------------------------------------------
# CognitiveVM
# ---------------------------------------------------------------------------

class CognitiveVM:
    MAX_CALL_DEPTH = 64

    def __init__(
        self,
        program: BytecodeProgram,
        state: VMState = None,
        host: Optional[Callable[[str, Any, Any], Dict[str, Any]]] = None,
    ) -> None:
        self.program = program
        self.state = state or VMState()
        self.host = host
        self.halted = False
        self._output: List[str] = []    # внутренний буфер для print

    def status(self) -> str:
        if self.state.pending_host_call:
            return VMStatus.PAUSED_HOST_CALL
        if self.state.pending_message_receive:
            return VMStatus.PAUSED_MESSAGING
        if self.halted:
            return VMStatus.HALTED
        return VMStatus.RUNNING

    def _last_committed_event_id(self) -> str:
        try:
            if self.host:
                result = self.host("HOST_STATUS", {"field": "last_event_id"}, None)
                if isinstance(result, dict):
                    return str(result.get("last_event_id") or result.get("event_id") or "evt--0000001")
                if result:
                    return str(result)
        except Exception:
            pass
        return "evt--0000001"

    def _make_pending_host_call_envelope(
        self,
        *,
        symbol: str,
        args: List[Any],
        argc: int,
        executed_ip: int,
        event_id: Optional[str] = None,
        required_capabilities: Optional[Iterable[str]] = None,
        determinism_class: str = "nondeterministic",
        explicit_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        created_at_event_id = event_id or self._last_committed_event_id()
        frame_depth = len(self.state.call_stack)
        computed_call_id = compute_call_id(
            self.program.program_hash,
            executed_ip,
            self.state.transition_hash,
            created_at_event_id,
            frame_depth,
        )
        call_id = explicit_call_id or computed_call_id
        return PendingHostCall({
            "pending_schema_version": "1",
            "status": VMStatus.PAUSED_HOST_CALL,
            "call_id": call_id,
            "symbol": symbol,
            "args": encode_vm_value(list(args)),
            "argc": int(argc),
            "ip_after_call": self.state.ip,
            "program_hash": self.program.program_hash,
            "transition_hash_at_call": self.state.transition_hash,
            "frame_depth_at_call": frame_depth,
            "agent_id": self.state.agent_id,
            "required_capabilities": sorted(set(required_capabilities or [])),
            "host_abi_version": HOST_ABI_VERSION,
            "created_at_event_id": created_at_event_id,
            "determinism_class": determinism_class,
            "deterministic_call_id": computed_call_id,
        })

    # --- snapshot ---

    def snapshot(self) -> Dict[str, Any]:
        return {
            "program": self.program.to_dict(),
            "state": self.state.to_dict(),
            "halted": self.halted,
        }

    @classmethod
    def restore(
        cls,
        snapshot: Dict[str, Any],
        host: Optional[Callable[[str, Any, Any], Dict[str, Any]]] = None,
    ) -> "CognitiveVM":
        vm = cls(
            BytecodeProgram.from_dict(snapshot["program"]),
            VMState.from_dict(snapshot["state"]),
            host=host,
        )
        vm.halted = snapshot.get("halted", False)
        return vm

    # --- transition hash ---

    def _hash_transition(self, ins) -> None:
        # Включаем top-of-stack значение для детерминированной привязки к данным
        stack_top = repr(self.state.stack[-1]) if self.state.stack else None
        payload = json.dumps(
            {
                "prev": self.state.transition_hash,
                "ip": self.state.ip,
                "op": ins.to_dict(),
                "locals_keys": sorted(self.state.locals.keys()),
                "stack_len": len(self.state.stack),
                "stack_top": stack_top,
                "gas": self.state.gas_remaining,
                "context_stack": tuple(self.state.context_stack),
                "actor_stack": tuple(self.state.actor_stack),
                "policy_stack": tuple(self.state.policy_stack),
                "mailbox_inbound": encode_vm_value(self.state.mailbox_inbound),
                "mailbox_outbound": encode_vm_value(self.state.mailbox_outbound),
                "pending_message_receive": encode_vm_value(self.state.pending_message_receive),
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        self.state.transition_hash = (
            "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
        )

    def _hash_resume_transition(self, call_id: str) -> None:
        """Update transition_hash after a successful host-call resume.

        ``transition_hash_at_call`` remains the identity material stored in the
        pending envelope.  After resume, the operand stack has changed, so the
        durable transition hash must move forward before the next checkpoint.
        """
        stack_top = repr(self.state.stack[-1]) if self.state.stack else None
        payload = json.dumps(
            {
                "prev": self.state.transition_hash,
                "resume_call_id": call_id,
                "ip": self.state.ip,
                "locals_keys": sorted(self.state.locals.keys()),
                "stack_len": len(self.state.stack),
                "stack_top": stack_top,
                "gas": self.state.gas_remaining,
                "context_stack": tuple(self.state.context_stack),
                "actor_stack": tuple(self.state.actor_stack),
                "policy_stack": tuple(self.state.policy_stack),
                "pending_host_call": None,
                "mailbox_inbound": encode_vm_value(self.state.mailbox_inbound),
                "mailbox_outbound": encode_vm_value(self.state.mailbox_outbound),
                "pending_message_receive": encode_vm_value(self.state.pending_message_receive),
            },
            sort_keys=True,
            default=str,
        )
        self.state.transition_hash = (
            "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
        )

    # --- stack helpers ---

    def _push(self, v: Any) -> None:
        self.state.stack.append(v)

    def _pop(self) -> Any:
        if not self.state.stack:
            raise VMStackUnderflow("Stack underflow")
        return self.state.stack.pop()

    def _peek(self) -> Any:
        if not self.state.stack:
            raise VMStackUnderflow("Stack underflow on peek")
        return self.state.stack[-1]

    def _charge_back_edge(self, executed_ip: int, target_ip: int) -> None:
        """Charge additional gas for backward jumps (loop back-edges)."""
        if target_ip <= executed_ip:
            if self.state.gas_remaining < GAS_BACK_EDGE:
                raise OutOfEnergy(
                    f"OUT_OF_ENERGY back-edge at ip={executed_ip} target={target_ip}"
                )
            self.state.gas_remaining -= GAS_BACK_EDGE

    # --- host helper ---

    def _call_host(self, opcode: str, a: Any, b: Any) -> Any:
        if self.host:
            result = self.host(opcode, a, b)
        else:
            result = {"op": opcode, "host_call": HOST_ABI.get(opcode, "host.unknown"),
                      "status": "fallback", "a": a, "b": b, "from_cache": False}
        # gas refund для cached результатов
        if isinstance(result, dict) and result.get("from_cache"):
            cost = GAS_COSTS.get(opcode, 1)
            refund = min(cost // 2, cost)
            self.state.gas_remaining = min(
                self.state.gas_remaining + refund,
                self.state.gas_remaining + cost,
            )
            result["gas_refund"] = refund
        return result

    def _call_host_builtin(self, symbol: str, args: List[Any]) -> Any:
        """Execute VM-local CALL_HOST builtins with normalized VMHostError failures."""
        try:
            if symbol == "len":
                if len(args) != 1:
                    raise VMHostError("HOST_ERROR", f"len() takes exactly 1 argument, got {len(args)}", symbol)
                return len(args[0])
            if symbol == "str":
                if len(args) != 1:
                    raise VMHostError("HOST_ERROR", f"str() takes exactly 1 argument, got {len(args)}", symbol)
                return str(args[0])
            if symbol == "int":
                if len(args) != 1:
                    raise VMHostError("HOST_ERROR", f"int() takes exactly 1 argument, got {len(args)}", symbol)
                return int(args[0])
            if symbol == "float":
                if len(args) != 1:
                    raise VMHostError("HOST_ERROR", f"float() takes exactly 1 argument, got {len(args)}", symbol)
                return float(args[0])
            if symbol == "bool":
                if len(args) != 1:
                    raise VMHostError("HOST_ERROR", f"bool() takes exactly 1 argument, got {len(args)}", symbol)
                return bool(args[0])
            if symbol == "abs":
                if len(args) != 1:
                    raise VMHostError("HOST_ERROR", f"abs() takes exactly 1 argument, got {len(args)}", symbol)
                return abs(args[0])
            if symbol == "range":
                return list(range(*[int(a) for a in args]))
            if symbol == "print":
                line = " ".join(str(a) for a in args)
                self._output.append(line)
                return None
            if symbol == "assert_fail":
                raise VMAssertionFailed(str(args[0]) if args else "assertion failed")
            raise VMHostError("UNKNOWN_SYMBOL", f"Unknown builtin: {symbol}", symbol)
        except VMHostError:
            raise
        except VMAssertionFailed:
            raise
        except (TypeError, ValueError, AttributeError) as exc:
            raise VMHostError("HOST_ERROR", f"{symbol}() error: {exc}", symbol) from exc

    # --- actor messaging helpers ---

    def _current_actor_id(self) -> str:
        if self.state.actor_stack:
            return str(self.state.actor_stack[-1])
        return str(self.state.agent_id or "default_agent")

    def _payload_hash(self, payload: Any) -> str:
        encoded = json.dumps(encode_vm_value(payload), sort_keys=True, separators=(",", ":"), default=str)
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _make_message_consumed_event(self, message: Dict[str, Any]) -> Dict[str, Any]:
        receiver_id = self._current_actor_id()
        msg_type = str(message.get("msg_type") or message.get("method") or message.get("type") or "")
        sender_id = str(message.get("sender_id") or message.get("sender") or "")
        payload = message.get("payload", message)
        payload_hash = str(message.get("payload_hash") or self._payload_hash(payload))
        event_id = self._last_committed_event_id()
        consumed_id = compute_message_consumed_id(
            receiver_id,
            msg_type,
            sender_id,
            self.state.transition_hash,
            event_id,
            payload_hash,
        )
        return {
            "type": "message_consumed",
            "message_consumed_id": consumed_id,
            "receiver_id": receiver_id,
            "msg_type": msg_type,
            "sender_id": sender_id,
            "payload_hash": payload_hash,
            "message": encode_vm_value(message),
        }

    def _execute_msg_send(self, msg_type: str) -> None:
        payload = self._pop()
        target_id = self._pop()
        sender_id = self._current_actor_id()
        message = {
            "msg_type": str(msg_type),
            "method": str(msg_type),
            "sender_id": sender_id,
            "sender": sender_id,
            "target_id": str(target_id),
            "receiver": str(target_id),
            "payload": payload,
            "payload_hash": self._payload_hash(payload),
        }
        self.state.mailbox_outbound.append(copy.deepcopy(message))
        result = self._call_host(
            "CALL_HOST",
            {"symbol": "SYS_MSG_SEND", "args": [message]},
            None,
        )
        self._raise_if_host_error_payload(result, "SYS_MSG_SEND")
        self._push(result)

    def _execute_receive_enter(self) -> None:
        # RAII marker reserved for D5 receive blocks. No host side effect.
        return None

    def _execute_receive_exit(self) -> None:
        # RAII marker reserved for D5 receive blocks. No host side effect.
        return None

    def _bind_received_message(self, sender_var: str, target_var: str, message: Dict[str, Any]) -> None:
        sender = message.get("sender_id", message.get("sender"))
        self.state.locals[str(sender_var)] = sender
        self.state.locals[str(target_var)] = message

    def _execute_msg_receive(self, sender_var: str, target_var: str) -> bool:
        if self.state.mailbox_inbound:
            message = self.state.mailbox_inbound.pop(0)
            self._bind_received_message(sender_var, target_var, message)
            event = self._make_message_consumed_event(message)
            result = self._call_host(
                "CALL_HOST",
                {"symbol": "SYS_MSG_CONSUME", "args": [event]},
                None,
            )
            self._raise_if_host_error_payload(result, "SYS_MSG_CONSUME")
            self._push(message)
            return False

        receiver_id = self._current_actor_id()
        event_id = self._last_committed_event_id()
        seed = f"{receiver_id}|{sender_var}|{target_var}|{self.state.transition_hash}|{event_id}"
        receive_id = "mr-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        self.state.pending_message_receive = {
            "pending_schema_version": "1",
            "status": VMStatus.PAUSED_MESSAGING,
            "message_receive_id": receive_id,
            "receiver_id": receiver_id,
            "sender_var": str(sender_var),
            "target_var": str(target_var),
            "created_at_event_id": event_id,
            "transition_hash_at_receive": self.state.transition_hash,
        }
        self.halted = True
        return True

    def resume_message_receive(self, message: Dict[str, Any]) -> None:
        """Resume STATUS_PAUSED_MESSAGING with a delivered message."""
        pending = self.state.pending_message_receive
        if not pending:
            raise VMResumeSyncError(
                "No pending message receive found in CVM state",
                code="NO_PENDING_MESSAGE_RECEIVE",
            )
        self._bind_received_message(
            str(pending.get("sender_var", "sender")),
            str(pending.get("target_var", "payload")),
            message,
        )
        self.state.mailbox_inbound.append(copy.deepcopy(message))
        self.state.pending_message_receive = None
        self.halted = False
        self._hash_resume_transition(str(pending.get("message_receive_id", "message_receive")))

    # --- single step ---

    def step(self) -> Any:  # noqa: C901
        if self.halted or self.state.ip >= len(self.program.instructions):
            self.halted = True
            return None
        if self.state.pending_host_call:
            raise VMError("VM already has pending host call")

        executed_ip = self.state.ip
        ins = self.program.instructions[executed_ip]
        op = ins.op

        # Gas check
        cost = GAS_COSTS.get(op, 1)
        if self.state.gas_remaining < cost:
            raise OutOfEnergy(f"OUT_OF_ENERGY at ip={executed_ip} op={op}")

        self.state.gas_remaining -= cost
        self.state.ip = executed_ip + 1

        result: Any = None

        # ---- Dispatch -------------------------------------------------------

        if op == "HALT":
            self.halted = True

        elif op == "LOAD_CONST":
            self._push(self.program.constants[ins.a])

        elif op == "LOAD_NAME":
            self._push(self.state.locals.get(ins.a))

        elif op == "LOAD_NONE":
            self._push(None)

        elif op == "LOAD_TRUE":
            self._push(True)

        elif op == "LOAD_FALSE":
            self._push(False)

        elif op == "STORE":
            self.state.locals[ins.a] = self._pop()

        elif op == "SAVE_NAME":
            name = ins.a
            existed = name in self.state.locals
            old_value = self.state.locals.get(name)
            self.state.name_save_stack.append((name, existed, old_value))

        elif op == "RESTORE_NAME":
            name = ins.a
            if not self.state.name_save_stack:
                raise VMStackUnderflow(f"RESTORE_NAME {name!r} without matching SAVE_NAME")
            saved_name, existed, old_value = self.state.name_save_stack.pop()
            if saved_name != name:
                raise RuntimeError(
                    f"RESTORE_NAME mismatch: expected {saved_name!r}, got {name!r}"
                )
            if existed:
                self.state.locals[name] = old_value
            else:
                self.state.locals.pop(name, None)

        elif op == "POP":
            self._pop()

        elif op == "DUP":
            self._push(self._peek())

        # --- Control flow ---

        elif op == "JUMP":
            target = ins.a
            self._charge_back_edge(executed_ip, target)
            self.state.ip = target

        elif op == "JUMP_IF_FALSE":
            val = self._pop()
            if not val:
                target = ins.a
                self._charge_back_edge(executed_ip, target)
                self.state.ip = target

        elif op == "JUMP_IF_TRUE":
            val = self._pop()
            if val:
                target = ins.a
                self._charge_back_edge(executed_ip, target)
                self.state.ip = target

        # --- Context blocks ---

        elif op == "CONTEXT_ENTER":
            self._execute_context_enter(str(ins.a))

        elif op == "CONTEXT_EXIT":
            self._execute_context_exit(str(ins.a))

        # --- Actor structural wrappers ---

        elif op == "ACTOR_ENTER":
            self._execute_actor_enter(str(ins.a), ins.b if isinstance(ins.b, dict) else {})

        elif op == "ACTOR_EXIT":
            self._execute_actor_exit(str(ins.a))

        # --- Policy structural wrappers ---

        elif op == "POLICY_ENTER":
            self._execute_policy_enter(str(ins.a), ins.b if isinstance(ins.b, dict) else {})

        elif op == "POLICY_EXIT":
            self._execute_policy_exit(str(ins.a))

        elif op == "POLICY_RULE_ENTER":
            self._execute_policy_rule_enter(str(ins.a), ins.b if isinstance(ins.b, dict) else {})

        elif op == "POLICY_RULE_EXIT":
            self._execute_policy_rule_exit(str(ins.a))

        # --- Actor messaging ---

        elif op == "RECEIVE_ENTER":
            self._execute_receive_enter()

        elif op == "RECEIVE_EXIT":
            self._execute_receive_exit()

        elif op == "MSG_SEND":
            self._execute_msg_send(str(ins.a))

        elif op == "MSG_RECEIVE":
            paused = self._execute_msg_receive(str(ins.a), str(ins.b))
            if paused:
                self._hash_transition(ins)
                return True

        # --- LLM / Prompt Bridge (alpha3e Track A) ---

        elif op == "PROMPT_BUILD":
            # ins.a = template_hash (str)
            # ins.b = variable_names (list[str])
            template_hash = str(ins.a)
            var_names = ins.b if isinstance(ins.b, list) else []
            # Pop variable values in reverse order (last pushed = last var)
            variables = {}
            for name in reversed(var_names):
                variables[name] = self._pop()
            # Build deterministic variables_hash
            import hashlib, json as _json
            canonical = _json.dumps(variables, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            variables_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            envelope = {
                "type":           "prompt_envelope",
                "template_hash":  template_hash,
                "variables":      variables,
                "variables_hash": variables_hash,
            }
            self._push(envelope)

        elif op == "LLM_REQUEST":
            # ins.a = schema_hash, ins.b = engine_params dict, ins.c = cache_policy
            schema_hash   = str(ins.a) if ins.a else ""
            engine_params = ins.b if isinstance(ins.b, dict) else {}
            cache_policy  = str(ins.c) if ins.c else "model_change"
            envelope = self._pop()
            if not isinstance(envelope, dict) or envelope.get("type") != "prompt_envelope":
                # Bare string on stack — wrap it
                import hashlib as _hl
                raw = str(envelope)
                envelope = {
                    "type":           "prompt_envelope",
                    "template_hash":  _hl.sha256(raw.encode()).hexdigest(),
                    "variables":      {"__text__": raw},
                    "variables_hash": _hl.sha256(raw.encode()).hexdigest(),
                }
            pending = self._make_pending_host_call_envelope(
                symbol="llm.request",
                args=[envelope, schema_hash, engine_params, cache_policy],
            )
            self.state.pending_host_call = pending
            self._hash_transition(ins)
            return True   # pause VM

        elif op == "LLM_RESUME":
            # Bridge calls resume_host_call() which pushes result and calls step()
            # This opcode is a no-op marker — result already on stack from Bridge
            pass

        # --- Guard Blocks (Alpha3e Track B) ---

        elif op == "GUARD_ENTER":
            # ins.a = guard_id (str), ins.b = policy_hash (str), ins.c = guard_hash (str)
            # Creates a new GuardFrame and pushes it onto guard_stack (LIFO).
            # Writes GUARD_ENTER event to execution_history via Bridge.
            import dataclasses as _dc
            guard_id    = str(ins.a) if ins.a is not None else f"guard-{executed_ip}"
            policy_hash = str(ins.b) if ins.b is not None else ""
            guard_hash  = str(ins.c) if ins.c is not None else ""

            # Determine parent_guard_id for nested guard flat-history tracking
            parent_guard_id = (
                self.state.guard_stack[-1].guard_id
                if self.state.guard_stack else None
            )

            # Capture history_hash at entry for tamper-evident anchor
            history_hash_at_entry = self.state.transition_hash

            frame = GuardFrame(
                guard_id=guard_id,
                policy_hash=policy_hash,
                guard_hash=guard_hash,
                verdict="PENDING",
                entered_at_ip=executed_ip,
                entered_at_history_hash=history_hash_at_entry,
                parent_guard_id=parent_guard_id,
            )
            self.state.guard_stack.append(frame)
            self._hash_transition(ins)

            # Write GUARD_ENTER audit event (via _call_host_builtin-style direct write)
            # This does NOT go through Bridge dispatch to avoid capability check on enter.
            try:
                h = self._host if hasattr(self, "_host") else None
                if h is None and hasattr(self, "_bridge"):
                    h = self._bridge.get_host() if hasattr(self._bridge, "get_host") else None
                if h is not None and hasattr(h, "execution_history"):
                    h.execution_history.append({
                        "type": "GUARD_ENTER",
                        "guard_id": guard_id,
                        "policy_hash": policy_hash,
                        "guard_hash": guard_hash,
                        "parent_guard_id": parent_guard_id,
                        "entered_at_ip": executed_ip,
                        "entered_at_history_hash": history_hash_at_entry,
                    })
            except Exception:
                pass  # history write is best-effort; guard semantic is in guard_stack

        elif op == "GUARD_CHECK_RESULT":
            # Reads a strict boolean from the top of the stack.
            # Security invariant: guard checks MUST NOT use Python truthiness.
            # Only True and False are accepted; every other value is a type error.
            result = self._pop()
            if result is True:
                self._push("GUARD_PASS")
            elif result is False:
                self._push("GUARD_FAIL")
            else:
                raise VMHostError(
                    code="GUARD_RESULT_TYPE_ERROR",
                    message=f"Guard condition must evaluate to bool, got {type(result).__name__}",
                    symbol="GUARD_CHECK_RESULT",
                )

        elif op == "GUARD_EXIT":
            # ins.a = explicit_verdict (optional: "PASS"|"FAIL"|None)
            # If ins.a is None: reads verdict signal from stack (set by GUARD_CHECK_RESULT).
            # Pops the top GuardFrame from guard_stack.
            # Writes GUARD_EXIT event with verdict to history.
            # On FAIL: sets guard_violation_active = True.
            import dataclasses as _dc

            # Determine verdict
            explicit_verdict = ins.a
            if explicit_verdict is not None:
                verdict = str(explicit_verdict).upper()
                if verdict not in ("PASS", "FAIL"):
                    verdict = "FAIL"
            else:
                # Consume verdict signal from stack
                signal = self._pop() if self.state.stack else "FAIL"
                verdict = "PASS" if signal == "GUARD_PASS" else "FAIL"

            # Pop active guard frame (LIFO)
            if not self.state.guard_stack:
                raise VMError("GUARD_EXIT without matching GUARD_ENTER (guard_stack underflow)")
            frame = self.state.guard_stack.pop()

            # Update frame with verdict (frozen → replace creates new object)
            closed_frame = _dc.replace(frame, verdict=verdict)

            # Update guard_violation_active
            if verdict == "FAIL":
                self.state.guard_violation_active = True

            self._hash_transition(ins)

            # Write GUARD_EXIT event with verdict
            try:
                h = self._host if hasattr(self, "_host") else None
                if h is None and hasattr(self, "_bridge"):
                    h = self._bridge.get_host() if hasattr(self._bridge, "get_host") else None
                if h is not None and hasattr(h, "execution_history"):
                    import hashlib as _hl, json as _j
                    prev_hash = h.execution_history[-1].get("history_hash", "") if h.execution_history else ""
                    event_hash = _hl.sha256(
                        (prev_hash + closed_frame.guard_id + verdict).encode()
                    ).hexdigest()
                    h.execution_history.append({
                        "type": "GUARD_EXIT",
                        "guard_id": closed_frame.guard_id,
                        "policy_hash": closed_frame.policy_hash,
                        "guard_hash": closed_frame.guard_hash,
                        "verdict": verdict,
                        "entered_at_ip": closed_frame.entered_at_ip,
                        "exited_at_ip": executed_ip,
                        "parent_guard_id": closed_frame.parent_guard_id,
                        "history_hash": event_hash,
                    })
            except Exception:
                pass

        elif op == "GUARD_VIOLATION_ACK":
            # Internal-only opcode — NOT accessible from .syn syntax.
            # Compiler inserts this as the first instruction in catch(GUARD_VIOLATION)
            # handlers. Clears guard_violation_active, allowing side-effecting host
            # calls to proceed again.
            # If no active violation: this is a no-op (idempotent, safe).
            was_active = self.state.guard_violation_active
            self.state.guard_violation_active = False
            self._hash_transition(ins)

            if was_active:
                try:
                    h = self._host if hasattr(self, "_host") else None
                    if h is None and hasattr(self, "_bridge"):
                        h = self._bridge.get_host() if hasattr(self._bridge, "get_host") else None
                    if h is not None and hasattr(h, "execution_history"):
                        h.execution_history.append({
                            "type": "GUARD_VIOLATION_ACKNOWLEDGED",
                            "instruction_pointer": executed_ip,
                        })
                except Exception:
                    pass


        # --- Functions ---


        elif op == "MAKE_FUNCTION":
            # ins.a = name, ins.b = params_const_idx, ins.c = body_ip
            params = self.program.constants[ins.b] if ins.b is not None else []
            fn = FunctionObject(
                name=ins.a,
                params=params,
                body_ip=ins.c,
                closure=dict(self.state.locals),
                program_hash=self.program.program_hash,
            )
            self._push(fn)

        elif op == "CALL":
            # Stack при CALL: [..., arg1, arg2, fn]  (fn на вершине)
            # Компилятор пушит аргументы слева направо, затем функцию.
            argc = ins.a
            fn = self._pop()          # fn на вершине
            args = []
            for _ in range(argc):
                args.insert(0, self._pop())   # снимаем args в обратном порядке

            if callable(fn) and not isinstance(fn, FunctionObject):
                # Python callable (builtin injected into locals)
                result = fn(*args)
                self._push(result)
            elif isinstance(fn, FunctionObject):
                if len(self.state.call_stack) >= self.MAX_CALL_DEPTH:
                    raise VMCallStackOverflow(f"Max call depth {self.MAX_CALL_DEPTH} exceeded")
                frame = CallFrame(
                    return_ip=self.state.ip,
                    locals_snapshot=dict(self.state.locals),
                    fn_name=fn.name,
                    program_hash=fn.program_hash,
                    body_ip=fn.body_ip,
                    stack_base=len(self.state.stack),
                    context_stack_snapshot=list(self.state.context_stack),
                    actor_stack_snapshot=list(self.state.actor_stack),
                    policy_stack_snapshot=list(self.state.policy_stack),
                )
                self.state.call_stack.append(frame)
                # Новый scope: closure (snapshot при определении) +
                # текущие locals (включают саму fn после STORE для рекурсии) +
                # параметры (перекрывают всё)
                new_locals = dict(fn.closure)
                new_locals.update(self.state.locals)   # обеспечивает self-reference для рекурсии
                for param, val in zip(fn.params, args):
                    new_locals[param] = val
                self.state.locals = new_locals
                self.state.ip = fn.body_ip
            else:
                # fallback — host
                result = self._call_host("HOST_EVAL", fn, args)
                self._push(result)

        elif op == "CALL_METHOD":
            # ins.a = method_name, ins.b = argc
            # Stack: [arg_n, ..., arg_1, obj]
            argc = ins.b
            args = []
            for _ in range(argc):
                args.insert(0, self._pop())
            obj = self._pop()
            method = getattr(obj, ins.a, None)
            if callable(method):
                result = method(*args)
            elif isinstance(obj, dict) and ins.a in obj:
                result = obj[ins.a]
            else:
                result = self._call_host("HOST_EVAL", f"{obj}.{ins.a}", args)
            self._push(result)

        elif op == "CALL_HOST":
            symbol = ins.a
            argc = ins.b if ins.b is not None else 0
            args = []
            for _ in range(argc):
                args.insert(0, self._pop())

            try:
                if symbol in {"print", "len", "str", "int", "float", "bool", "range", "abs", "assert_fail"}:
                    result = self._call_host_builtin(symbol, args)
                else:
                    result = self._call_host(
                        "CALL_HOST",
                        {"symbol": symbol, "args": args},
                        None,
                    )

                if isinstance(result, dict) and result.get("status") == "STATUS_PAUSED_HOST_CALL":
                    self.halted = True
                    self.state.pending_host_call = self._make_pending_host_call_envelope(
                        symbol=symbol,
                        args=args,
                        argc=argc,
                        executed_ip=executed_ip,
                        event_id=result.get("created_at_event_id") or result.get("event_id"),
                        required_capabilities=result.get("required_capabilities", []),
                        determinism_class=result.get("determinism_class", "nondeterministic"),
                        explicit_call_id=result.get("call_id"),
                    )
                    return True

                self._push(result)

            except VMHostError as exc:
                self._push(exc.to_dict())
            except (TypeError, ValueError, AttributeError) as exc:
                self._push(VMHostError(
                    code="HOST_ERROR",
                    message=f"Host execution error in {symbol}: {exc}",
                    symbol=symbol,
                ).to_dict())

        elif op == "RETURN":
            ret_val = self._pop()
            if self.state.call_stack:
                frame = self.state.call_stack.pop()
                if frame.stack_base <= len(self.state.stack):
                    self.state.stack = self.state.stack[:frame.stack_base]
                self._restore_context_stack_to_snapshot(
                    frame.context_stack_snapshot,
                    reason="function_return",
                )
                self._restore_actor_stack_to_snapshot(
                    frame.actor_stack_snapshot,
                    reason="function_return",
                )
                self._restore_policy_stack_to_snapshot(
                    frame.policy_stack_snapshot,
                    reason="function_return",
                )
                self.state.ip = frame.return_ip
                self.state.locals = frame.locals_snapshot
            else:
                self.halted = True
            self._push(ret_val)

        # --- Arithmetic ---

        elif op == "ADD":
            b, a = self._pop(), self._pop()
            self._push(a + b)

        elif op == "SUB":
            b, a = self._pop(), self._pop()
            self._push(a - b)

        elif op == "MUL":
            b, a = self._pop(), self._pop()
            self._push(a * b)

        elif op == "DIV":
            b, a = self._pop(), self._pop()
            self._push(a / b)

        elif op == "MOD":
            b, a = self._pop(), self._pop()
            self._push(a % b)

        elif op == "UNARY_NEG":
            self._push(-self._pop())

        # --- Comparison ---

        elif op == "EQ":
            b, a = self._pop(), self._pop()
            self._push(a == b)

        elif op == "NEQ":
            b, a = self._pop(), self._pop()
            self._push(a != b)

        elif op == "LT":
            b, a = self._pop(), self._pop()
            self._push(a < b)

        elif op == "GT":
            b, a = self._pop(), self._pop()
            self._push(a > b)

        elif op == "LTE":
            b, a = self._pop(), self._pop()
            self._push(a <= b)

        elif op == "GTE":
            b, a = self._pop(), self._pop()
            self._push(a >= b)

        # --- Logic ---

        elif op == "NOT":
            self._push(not self._pop())

        elif op == "AND":
            b, a = self._pop(), self._pop()
            self._push(bool(a) and bool(b))

        elif op == "OR":
            b, a = self._pop(), self._pop()
            self._push(bool(a) or bool(b))

        # --- Data structures ---

        elif op == "BUILD_LIST":
            count = ins.a
            items = []
            for _ in range(count):
                items.insert(0, self._pop())
            self._push(items)

        elif op == "BUILD_DICT":
            # Компилятор эмитирует для каждой пары: push key, push val
            # Стек (вершина справа): ..., key_n, val_n, ..., key_1, val_1
            # Снимаем пары от top к bottom
            count = ins.a
            d = {}
            pairs = []
            for _ in range(count):
                val = self._pop()
                key = self._pop()
                pairs.append((key, val))
            for key, val in reversed(pairs):
                d[key] = val
            self._push(d)

        elif op == "INDEX":
            idx = self._pop()
            obj = self._pop()
            try:
                self._push(obj[idx])
            except (KeyError, IndexError, TypeError):
                self._push(None)

        elif op == "MEMBER":
            obj = self._pop()
            attr = ins.a
            if isinstance(obj, dict):
                self._push(obj.get(attr))
            else:
                self._push(getattr(obj, attr, None))

        # --- HOST ABI (fixed v2.1 set) ---

        elif op in HOST_ABI or op == "HOST_EVAL":
            result = self._call_host(op, ins.a, ins.b)
            self._push(result)

        else:
            raise UnknownOpcodeError(f"Unknown opcode: {op!r} at ip={executed_ip}")

        self._hash_transition(ins)
        return result

    def _raise_if_host_error_payload(self, payload: Any, symbol: str) -> None:
        """Convert JSON-safe VMHostError payloads back into VMHostError for structural ops."""
        if isinstance(payload, dict) and payload.get("type") == "VMHostError":
            raise VMHostError(
                code=str(payload.get("code") or "HOST_ERROR"),
                message=str(payload.get("message") or f"Host error in {symbol}"),
                symbol=str(payload.get("symbol") or symbol),
            )

    def _execute_context_enter(self, label: str) -> None:
        """Enter a ContextBlock via host context_tracker, then mirror VM state."""
        result = self._call_host(
            "CALL_HOST",
            {"symbol": "SYS_CONTEXT_ENTER", "args": [label]},
            None,
        )
        self._raise_if_host_error_payload(result, "SYS_CONTEXT_ENTER")
        self.state.context_stack.append(label)

    def _execute_context_exit(self, label: str) -> None:
        """Exit a ContextBlock with LIFO VM-state validation and host parity."""
        if not self.state.context_stack:
            raise VMHostError(
                code="CONTEXT_STACK_UNDERFLOW",
                message=f"Context exit {label!r} with empty context_stack",
                symbol="SYS_CONTEXT_EXIT",
            )
        expected = self.state.context_stack[-1]
        if expected != label:
            raise VMHostError(
                code="CONTEXT_STACK_MISMATCH",
                message=f"Context exit mismatch: expected {expected!r}, got {label!r}",
                symbol="SYS_CONTEXT_EXIT",
            )
        result = self._call_host(
            "CALL_HOST",
            {"symbol": "SYS_CONTEXT_EXIT", "args": [label]},
            None,
        )
        self._raise_if_host_error_payload(result, "SYS_CONTEXT_EXIT")
        self.state.context_stack.pop()

    def _unwind_context(self, label: str, reason: str) -> None:
        """Best-effort context cleanup hook used by CallFrame RAII.

        Commit 1 intentionally does not introduce SYS_CONTEXT_ENTER/EXIT
        dispatch.  The host callback may already know how to consume the
        future structural symbol; if it does not, cleanup stays no-throw and
        the VM still restores its durable context_stack to the frame snapshot.
        """
        try:
            self._call_host(
                "CALL_HOST",
                {"symbol": "SYS_CONTEXT_EXIT", "args": [label, reason]},
                None,
            )
        except Exception:
            # Cleanup must never mask the original RETURN or unwind path.
            pass

    def _restore_context_stack_to_snapshot(self, snapshot: List[str], reason: str) -> None:
        """Close contexts opened after snapshot and restore snapshot exactly."""
        target_len = len(snapshot)
        dangling = list(self.state.context_stack[target_len:])
        for label in reversed(dangling):
            self._unwind_context(label, reason=reason)
        self.state.context_stack = list(snapshot)



    def _execute_actor_enter(self, name: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Enter an AgentDef/SubAgentDef structural wrapper via host actor_runtime."""
        result = self._call_host(
            "CALL_HOST",
            {"symbol": "SYS_ACTOR_ENTER", "args": [name, metadata or {}]},
            None,
        )
        self._raise_if_host_error_payload(result, "SYS_ACTOR_ENTER")
        self.state.actor_stack.append(name)

    def _execute_actor_exit(self, name: str) -> None:
        """Exit an AgentDef/SubAgentDef structural wrapper with LIFO validation."""
        if not self.state.actor_stack:
            raise VMHostError(
                code="ACTOR_STACK_UNDERFLOW",
                message=f"Actor exit {name!r} with empty actor_stack",
                symbol="SYS_ACTOR_EXIT",
            )
        expected = self.state.actor_stack[-1]
        if expected != name:
            raise VMHostError(
                code="ACTOR_STACK_MISMATCH",
                message=f"Actor exit mismatch: expected {expected!r}, got {name!r}",
                symbol="SYS_ACTOR_EXIT",
            )
        result = self._call_host(
            "CALL_HOST",
            {"symbol": "SYS_ACTOR_EXIT", "args": [name]},
            None,
        )
        self._raise_if_host_error_payload(result, "SYS_ACTOR_EXIT")
        self.state.actor_stack.pop()

    def _unwind_actor(self, name: str, reason: str) -> None:
        """Best-effort actor cleanup hook used by CallFrame RAII."""
        try:
            self._call_host(
                "CALL_HOST",
                {"symbol": "SYS_ACTOR_EXIT", "args": [name, reason]},
                None,
            )
        except Exception:
            # Cleanup must never mask the original RETURN or unwind path.
            pass

    def _restore_actor_stack_to_snapshot(self, snapshot: List[str], reason: str) -> None:
        """Close actor scopes opened after snapshot and restore snapshot exactly."""
        target_len = len(snapshot)
        dangling = list(self.state.actor_stack[target_len:])
        for name in reversed(dangling):
            self._unwind_actor(name, reason=reason)
        self.state.actor_stack = list(snapshot)

    def _execute_policy_enter(self, name: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Enter a PolicyDef structural wrapper via host governance runtime."""
        result = self._call_host(
            "CALL_HOST",
            {"symbol": "SYS_POLICY_ENTER", "args": [name, metadata or {}]},
            None,
        )
        self._raise_if_host_error_payload(result, "SYS_POLICY_ENTER")
        self.state.policy_stack.append(name)

    def _execute_policy_exit(self, name: str) -> None:
        """Exit a PolicyDef structural wrapper with LIFO validation."""
        if not self.state.policy_stack:
            raise VMHostError(
                code="POLICY_STACK_UNDERFLOW",
                message=f"Policy exit {name!r} with empty policy_stack",
                symbol="SYS_POLICY_EXIT",
            )
        expected = self.state.policy_stack[-1]
        if expected != name:
            raise VMHostError(
                code="POLICY_STACK_MISMATCH",
                message=f"Policy exit mismatch: expected {expected!r}, got {name!r}",
                symbol="SYS_POLICY_EXIT",
            )
        result = self._call_host(
            "CALL_HOST",
            {"symbol": "SYS_POLICY_EXIT", "args": [name]},
            None,
        )
        self._raise_if_host_error_payload(result, "SYS_POLICY_EXIT")
        self.state.policy_stack.pop()

    def _execute_policy_rule_enter(self, name: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Enter a PolicyRule structural wrapper via host governance runtime."""
        result = self._call_host(
            "CALL_HOST",
            {"symbol": "SYS_POLICY_RULE_ENTER", "args": [name, metadata or {}]},
            None,
        )
        self._raise_if_host_error_payload(result, "SYS_POLICY_RULE_ENTER")
        self.state.policy_stack.append(name)

    def _execute_policy_rule_exit(self, name: str) -> None:
        """Exit a PolicyRule structural wrapper with LIFO validation."""
        if not self.state.policy_stack:
            raise VMHostError(
                code="POLICY_STACK_UNDERFLOW",
                message=f"Policy rule exit {name!r} with empty policy_stack",
                symbol="SYS_POLICY_RULE_EXIT",
            )
        expected = self.state.policy_stack[-1]
        if expected != name:
            raise VMHostError(
                code="POLICY_STACK_MISMATCH",
                message=f"Policy rule exit mismatch: expected {expected!r}, got {name!r}",
                symbol="SYS_POLICY_RULE_EXIT",
            )
        result = self._call_host(
            "CALL_HOST",
            {"symbol": "SYS_POLICY_RULE_EXIT", "args": [name]},
            None,
        )
        self._raise_if_host_error_payload(result, "SYS_POLICY_RULE_EXIT")
        self.state.policy_stack.pop()

    def _unwind_policy(self, name: str, reason: str) -> None:
        """Best-effort policy cleanup hook used by CallFrame RAII."""
        try:
            symbol = "SYS_POLICY_RULE_EXIT" if ":rule:" in str(name) else "SYS_POLICY_EXIT"
            self._call_host(
                "CALL_HOST",
                {"symbol": symbol, "args": [name, reason]},
                None,
            )
        except Exception:
            pass

    def _restore_policy_stack_to_snapshot(self, snapshot: List[str], reason: str) -> None:
        """Close policy scopes opened after snapshot and restore snapshot exactly."""
        target_len = len(snapshot)
        dangling = list(self.state.policy_stack[target_len:])
        for name in reversed(dangling):
            self._unwind_policy(name, reason=reason)
        self.state.policy_stack = list(snapshot)


    def resume_host_call(self, call_id: str, response_value: Any) -> None:
        """Resume a single pending host call by deterministic call_id.

        The CALL_HOST instruction has already pre-incremented IP. Resume is
        therefore atomic stack push + pending clear + unhalt; it never adjusts IP.
        """
        pending = self.state.pending_host_call
        if not pending:
            raise VMResumeSyncError(
                "No pending host call found in CVM state",
                code="DOUBLE_RESUME_FORBIDDEN",
                call_id=call_id,
            )
        expected = pending.get("call_id")
        if call_id != expected:
            self.halted = True
            self.state.error = {
                "type": "VMResumeSyncError",
                "code": "CALL_ID_MISMATCH",
                "message": f"Resume call_id mismatch: expected {expected}, got {call_id}",
                "call_id": call_id,
            }
            raise VMResumeSyncError(self.state.error["message"], code="CALL_ID_MISMATCH", call_id=call_id)
        try:
            self._push(response_value)
            self.state.pending_host_call = None
            self.state.error = None
            self.halted = False
            self._hash_resume_transition(call_id)
        except Exception as exc:
            self.halted = True
            self.state.error = {
                "type": "VMResumeSyncError",
                "code": "RESUME_FAILED",
                "message": str(exc),
                "call_id": call_id,
            }
            raise

    def resume(self, response_value: Any) -> None:
        """Backward-compatible B1 resume wrapper using the pending call_id."""
        pending = self.state.pending_host_call
        if not pending:
            raise VMResumeSyncError("No pending host call found in CVM state")
        self.resume_host_call(str(pending.get("call_id")), response_value)

    # --- run loop ---


    def _handle_vmhosterror_propagation(self, exc: VMHostError, at_ip: Optional[int] = None) -> None:
        """Cleanup guard frames before a VMHostError propagates out of execution.

        Compiler-generated guard_cleanup_table is the primary cleanup model;
        _forcibly_close_guard_frames also includes a runtime safety fallback for
        malformed/legacy bytecode. This hook is invoked from the real run path
        instead of only from tests.
        """
        cleanup_ip = self.state.ip - 1 if at_ip is None else at_ip
        if cleanup_ip < 0:
            cleanup_ip = 0
        self._forcibly_close_guard_frames(cleanup_ip)

    def _forcibly_close_guard_frames(self, at_ip: int) -> None:
        """RAII cleanup: forcibly close any guard frames covering at_ip.

        Called when a VMHostError propagates up and we need to clean up the
        guard_stack before searching for a catch handler. This is the runtime
        safety fallback — the primary path is compiler-generated guard_cleanup_table.

        For each GuardCleanupRange in guard_cleanup_table that covers at_ip,
        find the corresponding GuardFrame in guard_stack and emit
        GUARD_FORCIBLY_CLOSED. guard_violation_active remains True after forced
        close — it is only cleared by GUARD_VIOLATION_ACK.

        Does NOT touch guard frames that are outside the current error's IP range
        (those belong to outer scopes that may still recover cleanly).
        """
        import dataclasses as _dc, hashlib as _hl

        if not self.state.guard_stack:
            return

        # Build set of guard_ids that have a cleanup range covering at_ip
        covered_guard_ids: set = set()
        for cleanup_range in self.program.guard_cleanup_table:
            if cleanup_range.start_ip <= at_ip < cleanup_range.end_ip:
                covered_guard_ids.add(cleanup_range.guard_id)

        # Runtime safety fallback: if no cleanup table entries but guard_stack is
        # non-empty, close all active frames (malformed/legacy bytecode guard).
        if not covered_guard_ids and self.state.guard_stack:
            covered_guard_ids = {f.guard_id for f in self.state.guard_stack}

        if not covered_guard_ids:
            return

        # Pop frames that are in covered set (innermost first, LIFO)
        remaining = []
        forcibly_closed = []
        for frame in reversed(self.state.guard_stack):
            if frame.guard_id in covered_guard_ids:
                forcibly_closed.append(frame)
            else:
                remaining.append(frame)

        # Restore stack without the forcibly-closed frames
        self.state.guard_stack = list(reversed(remaining))

        # Set violation active — forced close always counts as FAIL
        if forcibly_closed:
            self.state.guard_violation_active = True

        # Write GUARD_FORCIBLY_CLOSED events to history
        try:
            h = self._host if hasattr(self, "_host") else None
            if h is None and hasattr(self, "_bridge"):
                h = self._bridge.get_host() if hasattr(self._bridge, "get_host") else None
            if h is not None and hasattr(h, "execution_history"):
                for frame in forcibly_closed:
                    prev_hash = h.execution_history[-1].get("history_hash", "") if h.execution_history else ""
                    event_hash = _hl.sha256(
                        (prev_hash + frame.guard_id + "FORCIBLY_CLOSED").encode()
                    ).hexdigest()
                    h.execution_history.append({
                        "type": "GUARD_FORCIBLY_CLOSED",
                        "guard_id": frame.guard_id,
                        "policy_hash": frame.policy_hash,
                        "guard_hash": frame.guard_hash,
                        "entered_at_ip": frame.entered_at_ip,
                        "forcibly_closed_at_ip": at_ip,
                        "parent_guard_id": frame.parent_guard_id,
                        # guard_violation_active remains True — only ACK clears it
                        "guard_violation_active_after": True,
                        "history_hash": event_hash,
                    })
        except Exception:
            pass  # history write is best-effort

    def run(
        self,
        max_steps: int = 10_000,
        checkpoint_trigger: Optional[Callable[["CognitiveVM"], bool]] = None,
        checkpoint_callback: Optional[Callable[["CognitiveVM"], None]] = None,
    ) -> Dict[str, Any]:
        steps = 0
        last = None

        while not self.halted and steps < max_steps:
            if checkpoint_trigger and checkpoint_trigger(self):
                if checkpoint_callback:
                    checkpoint_callback(self)
                checkpoint_trigger = None   # однократный триггер

            try:
                last = self.step()
            except VMHostError as exc:
                self._handle_vmhosterror_propagation(exc, at_ip=max(self.state.ip - 1, 0))
                raise
            steps += 1

        return {
            "halted": self.halted,
            "steps": steps,
            "locals": dict(self.state.locals),
            "stack": list(self.state.stack),
            "gas_remaining": self.state.gas_remaining,
            "cognitive_budget_remaining": self.state.cognitive_budget_remaining,
            "transition_hash": self.state.transition_hash,
            "last": last,
            "output": list(self._output),
        }

    def get_output(self) -> str:
        return "\n".join(self._output)
