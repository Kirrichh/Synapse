"""Explicit CVM/HOST_EVAL routing boundary for Synapse v2.1.4-C.

This module is intentionally declarative.  It does not execute AST nodes; it
answers whether a node/opcode belongs to the current CVM surface or to the
legacy tree-walking host interpreter.  v2.2 can expand these tables without
changing the public VMBridge contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Set

CVM_AST_NODE_TYPES: Set[str] = {
    "CompileVmStmt",
    "RunVmStmt",
}

FIXED_HOST_ABI_OPCODES: Set[str] = {
    "IMPRINT",
    "RECALL",
    "METRICS",
    "AFFECT_EVENT",
    "AFFECT_STATE",
    "FRACTURE_SELF",
    "DREAM",
    "LLM_EVAL",
    "HOST_EVAL",
}

# Alpha.3-D1 deterministic replay classification.
DETERMINISTIC_PURE_HOST_SYMBOLS: Set[str] = {"len", "str", "int", "float", "bool", "abs", "range"}
DETERMINISTIC_SIDE_EFFECT_HOST_SYMBOLS: Set[str] = {"print"}
NONDETERMINISTIC_HOST_SYMBOLS: Set[str] = {
    "SYS_MEMORY_READ",
    "SYS_MEMORY_WRITE",
    "SYS_AFFECTIVE_READ",
    "SYS_AFFECTIVE_EVENT",
    "SYS_LLM_EVAL",
    "SYS_POLICY_CHECK",
}

@dataclass(frozen=True)
class VMRoutingDecision:
    node: str
    route: str
    reason: str


def classify_ast_node(node_or_name: Any) -> VMRoutingDecision:
    name = node_or_name if isinstance(node_or_name, str) else type(node_or_name).__name__
    if name in CVM_AST_NODE_TYPES:
        return VMRoutingDecision(node=name, route="CVM", reason="compiled_vm_surface")
    return VMRoutingDecision(node=name, route="HOST_EVAL", reason="not_yet_compiled")


def classify_host_opcode(opcode: str) -> VMRoutingDecision:
    if opcode in FIXED_HOST_ABI_OPCODES:
        return VMRoutingDecision(node=opcode, route="CVM_HOST_ABI", reason="fixed_v2_1_host_abi")
    return VMRoutingDecision(node=opcode, route="HOST_EVAL", reason="unknown_or_dynamic_opcode")


def coverage_ratio(events: Iterable[Dict[str, Any]]) -> float:
    """Runtime VM coverage ratio for executed statement-level routing decisions.

    ``vm_executed`` is the legacy event emitted by explicit ``run vm`` blocks.
    ``vm_routing_cvm`` is the Alpha.3-C statement-level route-audit event used
    when the interpreter chooses the CVM-capable path for an executed AST
    statement.  ``vm_fallback`` is the corresponding HOST_EVAL decision.

    This is intentionally runtime/taken-path coverage, not static AST coverage.
    For deterministic linear programs the two converge; for branches/loops this
    metric reflects only executed statements.
    """
    events = list(events or [])
    vm_exec = sum(1 for e in events if e.get("type") in {"vm_executed", "vm_routing_cvm"})
    fallback = sum(1 for e in events if e.get("type") == "vm_fallback")
    total = vm_exec + fallback
    if total <= 0:
        return 0.0
    return round(vm_exec / total, 6)


FALLBACK_REASONS: Dict[str, Dict[str, str]] = {
    "AffectiveEventStmt": {
        "code": "COMPILER_UNSUPPORTED_AFFECTIVE",
        "detail": "Affective runtime mutations remain HOST_EVAL in alpha3c1",
    },
    "DreamStmt": {
        "code": "COMPILER_UNSUPPORTED_DREAM",
        "detail": "Dream execution is deferred because async continuation cursor is not finalized",
    },
    "ResonateStmt": {
        "code": "COMPILER_UNSUPPORTED_RESONANCE",
        "detail": "Resonance requires runtime affective context and is not compiled in alpha3c1",
    },
}


def fallback_reason_for(node_type: str) -> Dict[str, str]:
    """Return structured reason for a HOST_EVAL fallback without changing execution."""
    return FALLBACK_REASONS.get(node_type, {
        "code": "COMPILER_NO_HANDLER",
        "detail": "No CVM compilation path registered for this AST node",
    })


def fallback_audit_from_events(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate fallback telemetry from immutable event history.

    This stays thread-safe for actor workloads because counters are derived from
    recorded events instead of mutating shared dicts during VM dispatch.
    """
    by_node: Dict[str, int] = {}
    by_reason: Dict[str, int] = {}
    total = 0
    for event in events or []:
        if event.get("type") != "vm_fallback":
            continue
        total += 1
        node_type = event.get("ast_node_type") or event.get("node") or "Unknown"
        by_node[node_type] = by_node.get(node_type, 0) + 1
        reason = event.get("fallback_reason") or {}
        if isinstance(reason, dict):
            code = reason.get("code") or event.get("reason") or "UNKNOWN"
        else:
            code = str(reason or event.get("reason") or "UNKNOWN")
        by_reason[code] = by_reason.get(code, 0) + 1
    return {
        "vm_fallbacks_total": total,
        "vm_fallback_by_node_type": by_node,
        "vm_fallback_by_reason": by_reason,
    }


# ---------------------------------------------------------------------------
# v2.2 expanded routing surface
# ---------------------------------------------------------------------------

CVM_AST_NODE_TYPES_V22: Set[str] = CVM_AST_NODE_TYPES | {
    "LetStmt",
    "AssignStmt",
    "ExprStmt",
    "IfStmt",
    "WhileStmt",
    "ForStmt",
    "ReturnStmt",
    "AssertStmt",
    "FnDef",
    "Literal",
    "Variable",
    "BinaryExpr",
    "UnaryExpr",
    "CallExpr",
    "MemberAccess",
    "ListExpr",
    "DictExpr",
    # HOST ABI via compiler
    "ImprintStmt",
    "RecallStmt",
    "AffectiveEventStmt",
    "AffectiveStateDef",
    "FractureStmt",
    "ContextBlock",
    "AgentDef",
    "SubAgentDef",
    "PolicyDef",
    "PolicyRule",
    "SendStmt",
    "ReceiveBlock",
    "ReceivePattern",
    # Alpha3e Track A: LLM / Prompt CVM Bridge
    "PromptExpr",
    "LLMCall",
}

CVM_CORE_OPCODES_V22: Set[str] = {
    "JUMP", "JUMP_IF_FALSE", "JUMP_IF_TRUE",
    "CALL", "CALL_HOST", "CALL_METHOD", "RETURN", "MAKE_FUNCTION",
    "DUP", "SAVE_NAME", "RESTORE_NAME", "LOAD_NONE", "LOAD_TRUE", "LOAD_FALSE",
    "ADD", "SUB", "MUL", "DIV", "MOD",
    "EQ", "NEQ", "LT", "GT", "LTE", "GTE",
    "AND", "OR", "NOT", "UNARY_NEG",
    "BUILD_LIST", "BUILD_DICT", "INDEX", "MEMBER",
    "CONTEXT_ENTER", "CONTEXT_EXIT",
    "ACTOR_ENTER", "ACTOR_EXIT",
    "POLICY_ENTER", "POLICY_EXIT", "POLICY_RULE_ENTER", "POLICY_RULE_EXIT",
    "MSG_SEND", "MSG_RECEIVE", "RECEIVE_ENTER", "RECEIVE_EXIT",
    # Alpha3e Track A
    "PROMPT_BUILD", "LLM_REQUEST", "LLM_RESUME",
    # Alpha3e Track B: Guard Blocks
    "GUARD_ENTER", "GUARD_EXIT", "GUARD_CHECK_RESULT",
    "GUARD_VIOLATION_ACK",  # internal-only: compiler-inserted in catch(GUARD_VIOLATION)
}
# Expanded static CVM core opcode set for v2.2-alpha.
# This is not the future dynamic opcode plugin registry.

VM_STRUCTURAL_RUNTIME = {
    "SYS_CONTEXT_ENTER",
    "SYS_CONTEXT_EXIT",
    "SYS_ACTOR_ENTER",
    "SYS_ACTOR_EXIT",
    "SYS_POLICY_ENTER",
    "SYS_POLICY_EXIT",
    "SYS_POLICY_RULE_ENTER",
    "SYS_POLICY_RULE_EXIT",
    "SYS_MSG_SEND",
    "SYS_MSG_CONSUME",
    "SYS_MSG_RECEIVE",
}


def classify_ast_node_v22(node_or_name: Any) -> VMRoutingDecision:
    """v2.2 routing: расширенная CVM поверхность."""
    name = node_or_name if isinstance(node_or_name, str) else type(node_or_name).__name__
    if name in CVM_AST_NODE_TYPES_V22:
        return VMRoutingDecision(node=name, route="CVM", reason="compiled_vm_surface_v22")
    return VMRoutingDecision(node=name, route="HOST_EVAL", reason="not_yet_compiled")


def classify_host_opcode_v22(opcode: str) -> VMRoutingDecision:
    """v2.2: включает расширенный статический набор CVM core opcodes."""
    if opcode in FIXED_HOST_ABI_OPCODES:
        return VMRoutingDecision(node=opcode, route="CVM_HOST_ABI", reason="fixed_v2_1_host_abi")
    if opcode in CVM_CORE_OPCODES_V22:
        return VMRoutingDecision(node=opcode, route="CVM", reason="core_opcode_v22")
    return VMRoutingDecision(node=opcode, route="HOST_EVAL", reason="unknown_or_dynamic_opcode")
