"""Synapse v2.2 Cognitive VM bytecode boundary.

v2.2 добавляет полные управляющие структуры: условные переходы, вызовы функций,
арифметику и сравнения. Это превращает CVM из концептуального слоя в рабочий
execution path для базовых примитивов языка.

Опкоды CVM v2.2:
  LOAD_CONST  a=idx         : stack.push(constants[a])
  LOAD_NAME   a=name        : stack.push(locals[a])
  STORE       a=name        : locals[a] = stack.pop()
  SAVE_NAME   a=name        : save current binding state for scoped loop variable
  RESTORE_NAME a=name       : restore saved binding or delete if absent before scope
  POP                       : stack.pop()
  DUP                       : stack.push(stack[-1])
  JUMP        a=target_ip   : ip = a
  JUMP_IF_FALSE a=target_ip : if not stack.pop(): ip = a
  JUMP_IF_TRUE  a=target_ip : if stack.pop(): ip = a
  CALL        a=argc        : вызов функции-объекта со стека (argc аргументов)
  CALL_HOST   a=name b=argc : вызов host builtin
  RETURN                    : возврат из frame
  MAKE_FUNCTION a=name b=param_count c=code_offset : создаёт FunctionObject
  BUILD_LIST  a=count       : собирает count элементов со стека в список
  BUILD_DICT  a=count       : собирает count пар со стека в dict
  INDEX                     : stack.push(stack.pop()[stack.pop()])
  MEMBER      a=name        : stack.push(stack.pop().name)
  ADD / SUB / MUL / DIV / MOD
  EQ / NEQ / LT / GT / LTE / GTE
  AND / OR / NOT
  UNARY_NEG
  LOAD_NONE                 : stack.push(None)
  LOAD_TRUE                 : stack.push(True)
  LOAD_FALSE                : stack.push(False)
  --- HOST ABI (передаются в host_call) ---
  IMPRINT  RECALL  METRICS  AFFECT_EVENT  AFFECT_STATE
  FRACTURE_SELF  DREAM  LLM_EVAL  HOST_EVAL
  HALT
  --- LLM/PROMPT CVM BRIDGE (alpha3e Track A) ---
  PROMPT_BUILD  a=template_hash b=variable_names : build PromptEnvelope onto stack
  LLM_REQUEST   a=schema_hash b=engine_params c=cache_policy : pause VM for LLM call
  LLM_RESUME    : resume VM after LLM result injected by Bridge (no operands needed)
"""
from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional



class CompileError(Exception):
    """Compilation error for invalid or reserved source-level constructs."""

from .ast import (
    Node, Program, LetStmt, AssignStmt, IfStmt, WhileStmt, ForStmt,
    ReturnStmt, ExprStmt, TryCatchStmt, FnDef, ContextBlock, AgentDef, SubAgentDef, PolicyDef, PolicyRule, SendStmt, ReceiveBlock, ReceivePattern,
    Literal, Variable, BinaryExpr, UnaryExpr, CallExpr, MemberAccess,
    ListExpr, DictExpr,
    ImprintStmt, RecallStmt, AffectiveEventStmt, AffectiveStateDef, FractureStmt,
    PromptExpr, LLMCall, GovernedMemoryWrite,
)


# ---------------------------------------------------------------------------
# Canonical JSON helpers
# ---------------------------------------------------------------------------

def _canonical_json_value(value: Any) -> Any:
    """Return a deterministic JSON-safe representation for hashing.

    Kept local to bytecode.py to avoid importing cvm.py and creating a
    circular dependency. Runtime VM values with to_dict() are normalized by
    their own representation; unknown objects fall back to repr() so hash
    computation remains total and deterministic for debugging artifacts.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _canonical_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(v) for v in value]
    if hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
        return _canonical_json_value(value.to_dict())
    return repr(value)


# ---------------------------------------------------------------------------
# Instruction & Program
# ---------------------------------------------------------------------------

@dataclass
class Instruction:
    op: str
    a: Any = None
    b: Any = None
    c: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {"op": self.op, "a": self.a, "b": self.b, "c": self.c}



@dataclass
class GuardCleanupRange:
    """Compiler-generated guard cleanup range — analogous to JVM/CPython exception table.

    When a VMHostError propagates through [start_ip, end_ip), the VM uses this
    table to emit GUARD_FORCIBLY_CLOSED events and pop active GuardFrames before
    searching for a catch handler. This is the primary cleanup path (compiler-driven).
    A runtime safety fallback also exists for malformed/legacy bytecode.

    start_ip: inclusive
    end_ip:   exclusive
    guard_id: matches GuardFrame.guard_id pushed by GUARD_ENTER at start_ip
    """
    start_ip: int
    end_ip: int
    guard_id: str

    def to_dict(self) -> Dict[str, Any]:
        return {"start_ip": self.start_ip, "end_ip": self.end_ip, "guard_id": self.guard_id}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GuardCleanupRange":
        return cls(start_ip=d["start_ip"], end_ip=d["end_ip"], guard_id=d["guard_id"])


@dataclass
class BytecodeProgram:
    instructions: List[Instruction] = field(default_factory=list)
    constants: List[Any] = field(default_factory=list)
    version: str = "2.2"
    host_abi_version: str = "2.2"
    # Compiler-generated guard cleanup table.
    # Maps IP ranges to guard_ids for RAII-style cleanup on VMHostError propagation.
    # Analogous to CPython 3.11+ co_exceptiontable and JVM exception_table.
    # Empty list = no guard scopes (default). Populated by CognitiveCompiler in Track B.
    guard_cleanup_table: List["GuardCleanupRange"] = field(default_factory=list)

    @property
    def program_hash(self) -> str:
        """Deterministic, source-independent hash of CVM program structure."""
        canonical = {
            "bytecode_version": self.version,
            "constants": _canonical_json_value(self.constants),
            "instructions": [
                {
                    "op": i.op,
                    "a": _canonical_json_value(i.a),
                    "b": _canonical_json_value(i.b),
                    "c": _canonical_json_value(i.c),
                }
                for i in self.instructions
            ],
            "host_abi_version": self.host_abi_version,
            "compiler_target": "cvm-v2.2",
            # guard_cleanup_table is part of canonical hash — different tables
            # produce different hashes, which is required for replay correctness:
            # two programs with identical instructions but different guard ranges
            # must not produce the same program_hash (snapshot replay depends on it).
            "guard_cleanup_table": [r.to_dict() for r in self.guard_cleanup_table],
        }
        payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "bytecode_program",
            "version": self.version,
            "constants": list(self.constants),
            "instructions": [i.to_dict() for i in self.instructions],
            "host_abi_version": self.host_abi_version,
            "program_hash": self.program_hash,
            "guard_cleanup_table": [r.to_dict() for r in self.guard_cleanup_table],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BytecodeProgram":
        return cls(
            instructions=[Instruction(**i) for i in data.get("instructions", [])],
            constants=data.get("constants", []),
            version=data.get("version", "2.2"),
            host_abi_version=data.get("host_abi_version", "2.2"),
            guard_cleanup_table=[
                GuardCleanupRange.from_dict(r)
                for r in data.get("guard_cleanup_table", [])
            ],
        )


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------

class CognitiveCompiler:
    """Транслирует Synapse AST в байткод CVM v2.2.

    Стратегия: компилируем всё, что можем. Неподдерживаемые узлы
    получают HOST_EVAL — это обратно-совместимо с v2.1.
    """

    def __init__(self) -> None:
        self.instructions: List[Instruction] = []
        self.constants: List[Any] = []
        # Стек фреймов функций: список (name, params, start_ip_placeholder)
        self._fn_patch_queue: List[Dict[str, Any]] = []
        # Track B.1: strict lexical checked effects.  Guarded side effects may
        # compile only inside a local try/catch(GUARD_VIOLATION) recovery scope.
        self._guard_recovery_depth: int = 0
        self._guard_handler_stack: List[Dict[str, Any]] = []
        self._guard_cleanup_table: List[GuardCleanupRange] = []

    # --- helpers ---

    def _const(self, value: Any) -> int:
        """Добавить константу в пул, вернуть индекс."""
        self.constants.append(value)
        return len(self.constants) - 1

    def _emit(self, op: str, a: Any = None, b: Any = None, c: Any = None) -> int:
        """Добавить инструкцию, вернуть её ip."""
        ip = len(self.instructions)
        self.instructions.append(Instruction(op, a, b, c))
        return ip

    def _current_ip(self) -> int:
        return len(self.instructions)

    def _patch(self, ip: int, **kwargs: Any) -> None:
        """Пропатчить уже добавленную инструкцию (для backpatching прыжков)."""
        ins = self.instructions[ip]
        for k, v in kwargs.items():
            setattr(ins, k, v)

    # --- public entry ---

    def compile(self, node: Node) -> BytecodeProgram:
        self._visit(node)
        self._emit("HALT")
        return BytecodeProgram(
            instructions=self.instructions,
            constants=self.constants,
            version="2.2",
            guard_cleanup_table=list(self._guard_cleanup_table),
        )

    # --- visitor dispatch ---

    def _visit(self, node: Node) -> None:  # noqa: C901
        if node is None:
            self._emit("LOAD_NONE")
            return

        t = type(node).__name__

        # — Program / body lists —
        if t == "Program":
            for stmt in node.statements:
                self._visit(stmt)

        # — Statements —
        elif t == "LetStmt":
            self._visit(node.value)
            self._emit("STORE", node.name)

        elif t == "AssignStmt":
            self._visit(node.value)
            self._emit("STORE", node.target)

        elif t == "TryCatchStmt":
            self._compile_try_catch_stmt(node)

        elif t == "ExprStmt":
            self._visit(node.expr)
            # AssignStmt/LetStmt are statement-valued in the VM: STORE already
            # consumes the value, so emitting POP after them causes
            # VMStackUnderflow inside control-flow blocks. Value-producing
            # expressions still need POP to discard their result.
            expr_type = type(node.expr).__name__
            if expr_type not in {"AssignStmt", "LetStmt", "GovernedMemoryWrite"}:
                self._emit("POP")

        elif t == "ReturnStmt":
            if node.value is not None:
                self._visit(node.value)
            else:
                self._emit("LOAD_NONE")
            self._emit("RETURN")

        elif t == "IfStmt":
            self._compile_if(node)

        elif t == "WhileStmt":
            self._compile_while(node)

        elif t == "ForStmt":
            self._compile_for(node)

        elif t == "ContextBlock":
            self._compile_context_block(node)

        elif t == "AgentDef":
            self._compile_agent_def(node)

        elif t == "SubAgentDef":
            self._compile_subagent_def(node)

        elif t == "PolicyDef":
            self._compile_policy_def(node)

        elif t == "PolicyRule":
            self._compile_policy_rule(node, policy_name="<policy>", index=0)

        elif t == "SendStmt":
            self._compile_send_stmt(node)

        elif t == "ReceiveBlock":
            self._compile_receive_block(node)

        elif t == "AssertStmt":
            # assert cond, "msg"  →  if not cond: raise
            self._visit(node.condition)
            patch = self._emit("JUMP_IF_TRUE", None)   # если true — пропустить panic
            # emit assert failure host call
            msg_idx = self._const(node.message.value if node.message else "assertion failed")
            self._emit("LOAD_CONST", msg_idx)
            self._emit("CALL_HOST", "assert_fail", 1)
            self._patch(patch, a=self._current_ip())

        elif t == "FnDef":
            self._compile_fn(node)

        # — Expressions —
        elif t == "Literal":
            if node.value is None:
                self._emit("LOAD_NONE")
            elif node.value is True:
                self._emit("LOAD_TRUE")
            elif node.value is False:
                self._emit("LOAD_FALSE")
            else:
                self._emit("LOAD_CONST", self._const(node.value))

        elif t == "Variable":
            self._emit("LOAD_NAME", node.name)

        elif t == "BinaryExpr":
            self._compile_binary(node)

        elif t == "UnaryExpr":
            self._visit(node.operand)
            if node.op == "not":
                self._emit("NOT")
            elif node.op == "-":
                self._emit("UNARY_NEG")
            else:
                self._emit("CALL_HOST", f"unary_{node.op}", 1)

        elif t == "CallExpr":
            self._compile_call(node)

        elif t == "MemberAccess":
            self._visit(node.obj)
            self._emit("MEMBER", node.member)

        elif t == "ListExpr":
            for el in node.elements:
                self._visit(el)
            self._emit("BUILD_LIST", len(node.elements))

        elif t == "DictExpr":
            for k, v in node.pairs:
                self._visit(k)
                self._visit(v)
            self._emit("BUILD_DICT", len(node.pairs))

        # — HOST ABI primitives —
        elif t == "ImprintStmt":
            self._emit("IMPRINT", node.room, getattr(node, "binding", None))

        elif t == "RecallStmt":
            self._emit("RECALL", node.room, getattr(node, "binding", None))

        elif t == "AffectiveEventStmt":
            self._emit("AFFECT_EVENT", node.name, node.binding)

        elif t == "AffectiveStateDef":
            self._emit("AFFECT_STATE", node.name, node.binding)

        elif t == "FractureStmt":
            self._emit("FRACTURE_SELF", len(node.subagents), node.consensus_strategy)

        elif t == "PromptExpr":
            self._compile_prompt_expr(node)

        elif t == "LLMCall":
            self._compile_llm_call(node)

        elif t == "GovernedMemoryWrite":
            self._compile_governed_memory_write(node)

        # — Fallback: всё остальное — HOST_EVAL —
        else:
            self._emit("HOST_EVAL", t)

    # -----------------------------------------------------------------------
    # Track B.1 — source-level guard lowering / checked effects
    # -----------------------------------------------------------------------

    def _compile_try_catch_stmt(self, node: "TryCatchStmt") -> None:
        """Compile local catch(GUARD_VIOLATION) recovery.

        This is intentionally not a general exception mechanism.  Track B.1 uses
        the handler as the sole compiler-approved recovery context for guarded
        side effects.  The compiler inserts GUARD_VIOLATION_ACK as the first
        handler instruction; user code cannot emit that opcode directly.
        """
        if getattr(node, "catch_error", "") != "GUARD_VIOLATION":
            raise CompileError("Only catch(GUARD_VIOLATION) is supported in alpha3e Track B.1")
        handler_ctx: Dict[str, Any] = {"patches": []}
        self._guard_handler_stack.append(handler_ctx)
        self._guard_recovery_depth += 1
        try:
            for stmt in getattr(node, "try_body", []) or []:
                self._visit(stmt)
        finally:
            self._guard_recovery_depth -= 1
            self._guard_handler_stack.pop()
        jump_over_handler = self._emit("JUMP", None)
        handler_ip = self._current_ip()
        for patch_ip in handler_ctx["patches"]:
            self._patch(patch_ip, a=handler_ip)
        self._emit("GUARD_VIOLATION_ACK")
        if getattr(node, "catch_binding", None):
            self._emit("LOAD_CONST", self._const({"code": "GUARD_VIOLATION"}))
            self._emit("STORE", node.catch_binding)
        for stmt in getattr(node, "catch_body", []) or []:
            self._visit(stmt)
        self._patch(jump_over_handler, a=self._current_ip())

    def _guard_hashes_for_governed_write(self, node: "GovernedMemoryWrite") -> tuple[str, str, str]:
        payload = {
            "node": "GovernedMemoryWrite",
            "line": getattr(node, "line", 0),
            "column": getattr(node, "column", 0),
            "fields": sorted([str(k) for k in (getattr(node, "fields", {}) or {}).keys()]),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        return f"gwm-{getattr(node, 'line', 0)}-{getattr(node, 'column', 0)}-{len(self._guard_cleanup_table)}", f"policy:{digest}", f"guard:{digest}"

    def _compile_governed_memory_write(self, node: "GovernedMemoryWrite") -> None:
        """Lower memory.write(...) { ... } into CVM guard opcodes.

        Track B.1 checked-effect rule: source-level governed side effects must
        have a lexical try/catch(GUARD_VIOLATION) ancestor in the same function.
        We deliberately do not infer that a caller catches the error; helpers
        must contain their own local recovery context.
        """
        if self._guard_recovery_depth <= 0 or not self._guard_handler_stack:
            raise CompileError(
                "guarded side-effect statement may raise GUARD_VIOLATION; "
                "wrap it in try/catch(GUARD_VIOLATION)"
            )
        handler_ctx = self._guard_handler_stack[-1]
        guard_id, policy_hash, guard_hash = self._guard_hashes_for_governed_write(node)
        start_ip = self._emit("GUARD_ENTER", guard_id, policy_hash, guard_hash)

        # Alpha3e B.1 uses a conservative default guard expression.  If the
        # governed field block contains a `guard` field, compile it; otherwise
        # the guard passes.  Governance policy semantics remain host-owned.
        fields = getattr(node, "fields", {}) or {}
        guard_expr = fields.get("guard") or fields.get("require")
        if guard_expr is not None:
            self._visit(guard_expr)
        else:
            self._emit("LOAD_TRUE")

        # Duplicate the strict bool: one copy drives the branch, the other is
        # consumed by GUARD_CHECK_RESULT to produce GUARD_PASS/GUARD_FAIL.
        self._emit("DUP")
        fail_jump = self._emit("JUMP_IF_FALSE", None)
        self._emit("GUARD_CHECK_RESULT")

        # Protected side effect.  The bridge owns the actual memory effect; the
        # VM only sequences it under a guard boundary and discards the return.
        self._visit(getattr(node, "value", None))
        self._emit("CALL_HOST", "SYS_MEMORY_WRITE", 1)
        self._emit("POP")
        self._emit("GUARD_EXIT")
        jump_end = self._emit("JUMP", None)

        fail_ip = self._current_ip()
        self._patch(fail_jump, a=fail_ip)
        self._emit("GUARD_CHECK_RESULT")
        self._emit("GUARD_EXIT")
        # Recovery is compiler-controlled: jump into the local catch handler,
        # whose first instruction is GUARD_VIOLATION_ACK.
        handler_jump = self._emit("JUMP", None)
        handler_ctx["patches"].append(handler_jump)

        end_ip = self._current_ip()
        self._patch(jump_end, a=end_ip)
        self._guard_cleanup_table.append(GuardCleanupRange(start_ip=start_ip, end_ip=end_ip, guard_id=guard_id))

    # --- compound statement compilers ---

    def _compile_send_stmt(self, node: "SendStmt") -> None:
        """Compile send receiver.method(args...) as D5 fire-and-forget MSG_SEND.

        Variant A syntax is preserved: SendStmt.method becomes msg_type.  The
        payload is the single argument when present, a list for multiple args,
        or None for zero args.  No payload destructuring or LLM/prompt behavior
        is introduced here.
        """
        self._visit(node.receiver)
        args = list(getattr(node, "args", []) or [])
        if len(args) == 0:
            self._emit("LOAD_NONE")
        elif len(args) == 1:
            self._visit(args[0])
        else:
            for arg in args:
                self._visit(arg)
            self._emit("BUILD_LIST", len(args))
        self._emit("MSG_SEND", getattr(node, "method", ""))

    def _compile_receive_block(self, node: "ReceiveBlock") -> None:
        """Compile receive { sender => payload { ... } } without grammar changes.

        D5 keeps ReceivePattern as binding-only.  MSG_RECEIVE either binds
        sender_var/target_var to the first available message or pauses the VM
        with STATUS_PAUSED_MESSAGING when the durable inbox snapshot is empty.
        """
        self._emit("RECEIVE_ENTER")
        patterns = list(getattr(node, "patterns", []) or [])
        if not patterns:
            self._emit("RECEIVE_EXIT")
            return
        pattern = patterns[0]
        self._emit("MSG_RECEIVE", pattern.sender_var, pattern.target_var)
        for stmt in getattr(pattern, "body", []) or []:
            self._visit(stmt)
        self._emit("RECEIVE_EXIT")

    def _compile_context_block(self, node: "ContextBlock") -> None:
        """Compile context "label" { ... } as statement-scoped CVM code.

        ContextBlock is not an expression.  The body is compiled as a normal
        statement block and no block result is preserved on the operand stack.
        Context enter/exit side effects are delegated to the host context
        tracker by the CVM CONTEXT_ENTER/CONTEXT_EXIT opcodes.
        """
        self._emit("CONTEXT_ENTER", node.label)
        for stmt in node.body:
            self._visit(stmt)
        self._emit("CONTEXT_EXIT", node.label)


    def _compile_agent_def(self, node: "AgentDef") -> None:
        """Compile AgentDef as a structural wrapper.

        CVM only records actor-scope structure.  Actor registry, mailboxes,
        spawning and runtime topology stay behind VMBridge/actor_runtime.
        The body for current AgentDef syntax is the method list; metadata is
        canonical and JSON-safe so it can be used by host actor_runtime parity.
        """
        metadata = {
            "kind": "agent",
            "model": getattr(node, "model", None),
            "memory": getattr(node, "memory", None),
            "trust_level": repr(getattr(node, "trust_level", None)),
            "trust_scope": [repr(x) for x in (getattr(node, "trust_scope", []) or [])],
        }
        self._emit("ACTOR_ENTER", node.name, metadata)
        for method in getattr(node, "methods", []) or []:
            self._visit(method)
        self._emit("ACTOR_EXIT", node.name)

    def _compile_subagent_def(self, node: "SubAgentDef") -> None:
        """Compile SubAgentDef as a structural wrapper with normal CVM body."""
        metadata = {
            "kind": "subagent",
            "focus": getattr(node, "focus", None),
            "soulprint_override": getattr(node, "soulprint_override", {}) or {},
        }
        self._emit("ACTOR_ENTER", node.name, metadata)
        for stmt in getattr(node, "body", []) or []:
            self._visit(stmt)
        self._emit("ACTOR_EXIT", node.name)

    def _policy_metadata_literal(self, value: Any) -> Any:
        """Return a compile-time-constant metadata payload for policy wrappers.

        Policy metadata must be stable at bytecode-hash time.  Runtime metadata
        expressions are intentionally not evaluated or embedded; AST nodes are
        represented by deterministic descriptors only.
        """
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(k): self._policy_metadata_literal(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
        if isinstance(value, (list, tuple)):
            return [self._policy_metadata_literal(v) for v in value]
        if isinstance(value, Literal):
            return self._policy_metadata_literal(value.value)
        return {"ast_type": type(value).__name__, "repr": repr(value)}

    def _compile_policy_def(self, node: "PolicyDef") -> None:
        """Compile PolicyDef as a structural wrapper.

        CVM records policy/rule scope structure only. Governance evaluation,
        enforcement and conflict resolution remain in the runtime governance
        layer behind VMBridge.
        """
        fields = getattr(node, "fields", {}) or {}
        metadata = {
            "kind": "policy",
            "target": self._policy_metadata_literal(getattr(node, "target", None)),
            "trigger": self._policy_metadata_literal(getattr(node, "trigger", None)),
            "cooldown": self._policy_metadata_literal(getattr(node, "cooldown", None)),
            "max_delta": self._policy_metadata_literal(getattr(node, "max_delta", None)),
            "guard_expr": self._policy_metadata_literal(getattr(node, "guard_expr", None)),
            "require_approval": bool(getattr(node, "require_approval", False)),
            "fields": self._policy_metadata_literal(fields),
        }
        self._emit("POLICY_ENTER", node.name, metadata)
        for index, rule in enumerate(getattr(node, "rules", []) or []):
            self._compile_policy_rule(rule, policy_name=node.name, index=index)
        for stmt in getattr(node, "guard_body", []) or []:
            self._visit(stmt)
        self._emit("POLICY_EXIT", node.name)

    def _compile_policy_rule(self, node: "PolicyRule", policy_name: str, index: int) -> None:
        """Compile PolicyRule as nested structural wrapper plus pure expression body."""
        rule_name = f"{policy_name}:rule:{index}:{getattr(node, 'kind', 'rule')}"
        metadata = {
            "kind": "policy_rule",
            "policy": policy_name,
            "rule_kind": getattr(node, "kind", ""),
            "index": index,
        }
        self._emit("POLICY_RULE_ENTER", rule_name, metadata)
        if getattr(node, "value", None) is not None:
            self._visit(node.value)
            self._emit("POP")
        self._emit("POLICY_RULE_EXIT", rule_name)



    # -----------------------------------------------------------------------
    # LLM / Prompt CVM Bridge  (alpha3e Track A)
    # -----------------------------------------------------------------------

    def _compile_prompt_expr(self, node: "PromptExpr") -> None:
        """Compile PromptExpr → PROMPT_BUILD.

        Evaluates all bound variable expressions onto the stack, then emits
        PROMPT_BUILD with the template_hash and the ordered variable name list.
        CVM will pop the values and assemble a PromptEnvelope dict.
        """
        import hashlib
        template_hash = hashlib.sha256(node.template.encode("utf-8")).hexdigest()
        var_names = list(node.args.keys())
        for var_node in node.args.values():
            self._visit(var_node)
        self._emit("PROMPT_BUILD", template_hash, var_names)

    def _compile_llm_call(self, node: "LLMCall") -> None:
        """Compile LLMCall → PROMPT_BUILD + LLM_REQUEST.

        The result is left on the stack when Bridge calls resume_host_call()
        which triggers LLM_RESUME to push the resolved value.
        """
        import hashlib
        # Compile the prompt argument
        if isinstance(node.prompt, PromptExpr):
            self._compile_prompt_expr(node.prompt)
        else:
            # bare string expression or variable — evaluate it
            self._visit(node.prompt)
            # wrap in a minimal prompt envelope with no named variables
            inline_hash = "sha256:inline-" + hashlib.sha256(b"inline").hexdigest()[:16]
            self._emit("PROMPT_BUILD", inline_hash, [])

        # schema_hash is empty until LLMCall AST grows a schema annotation
        schema_hash = ""
        engine_params = {
            "model":         node.model or "default",
            "model_version": node.model or "default",
            "temperature":   node.temperature,
            "max_tokens":    node.max_tokens,
        }
        cache_policy = "model_change"
        self._emit("LLM_REQUEST", schema_hash, engine_params, cache_policy)

    def _compile_if(self, node: "IfStmt") -> None:
        """
        if cond { then } else { else }
        →
          <cond>
          JUMP_IF_FALSE else_ip
          <then>
          JUMP end_ip
        else_ip:
          <else>
        end_ip:
        """
        self._visit(node.condition)
        jump_to_else = self._emit("JUMP_IF_FALSE", None)

        for stmt in node.then_body:
            self._visit(stmt)

        if node.else_body:
            jump_over_else = self._emit("JUMP", None)
            self._patch(jump_to_else, a=self._current_ip())
            for stmt in node.else_body:
                self._visit(stmt)
            self._patch(jump_over_else, a=self._current_ip())
        else:
            self._patch(jump_to_else, a=self._current_ip())

    def _compile_while(self, node: "WhileStmt") -> None:
        """
        while cond { body }
        →
        loop_ip:
          <cond>
          JUMP_IF_FALSE end_ip
          <body>
          JUMP loop_ip
        end_ip:
        """
        loop_ip = self._current_ip()
        self._visit(node.condition)
        jump_out = self._emit("JUMP_IF_FALSE", None)
        for stmt in node.body:
            self._visit(stmt)
        self._emit("JUMP", loop_ip)
        self._patch(jump_out, a=self._current_ip())

    def _compile_for(self, node: "ForStmt") -> None:
        """
        for x in iterable { body }
        →
          <iterable>
          STORE __iter_N
          STORE __idx_N = 0
        loop:
          LOAD __idx_N < len(__iter_N) → JUMP_IF_FALSE end
          LOAD __iter_N[__idx_N] → STORE x
          __idx_N += 1
          <body>
          JUMP loop
        end:
        """
        uid = str(id(node))
        iter_name = f"__iter_{uid}"
        idx_name = f"__idx_{uid}"

        # Preserve any outer binding of the loop variable. Tree-walker ForStmt
        # defines node.var in a child Environment, so the binding must not leak
        # into the surrounding CVM locals after loop exit.
        self._emit("SAVE_NAME", node.var)

        self._visit(node.iterable)
        self._emit("STORE", iter_name)
        self._emit("LOAD_CONST", self._const(0))
        self._emit("STORE", idx_name)

        loop_ip = self._current_ip()

        # condition: idx < len(iter)
        self._emit("LOAD_NAME", idx_name)
        self._emit("LOAD_NAME", iter_name)
        self._emit("CALL_HOST", "len", 1)
        self._emit("LT")
        jump_out = self._emit("JUMP_IF_FALSE", None)

        # x = iter[idx]
        self._emit("LOAD_NAME", iter_name)
        self._emit("LOAD_NAME", idx_name)
        self._emit("INDEX")
        self._emit("STORE", node.var)

        # idx += 1
        self._emit("LOAD_NAME", idx_name)
        self._emit("LOAD_CONST", self._const(1))
        self._emit("ADD")
        self._emit("STORE", idx_name)

        for stmt in node.body:
            self._visit(stmt)

        self._emit("JUMP", loop_ip)
        restore_ip = self._current_ip()
        self._patch(jump_out, a=restore_ip)
        self._emit("RESTORE_NAME", node.var)

    def _compile_fn(self, node: "FnDef") -> None:
        """
        fn foo(a, b) { body }
        →
          MAKE_FUNCTION name=foo params=[a,b] body_ip=<offset>

        Тело функции компилируется inline — CVM при вызове сохраняет
        ip и прыгает к body_ip; RETURN восстанавливает ip.
        """
        jump_over = self._emit("JUMP", None)   # перепрыгиваем тело при определении
        body_ip = self._current_ip()

        for stmt in node.body:
            self._visit(stmt)
        # неявный return None
        self._emit("LOAD_NONE")
        self._emit("RETURN")

        end_ip = self._current_ip()
        self._patch(jump_over, a=end_ip)

        # Инструкция определения функции — ставим ПОСЛЕ тела
        # (уже перепрыгнуто), указываем на body_ip
        params_idx = self._const(node.params)
        self._emit("MAKE_FUNCTION", node.name, params_idx, body_ip)
        self._emit("STORE", node.name)

    def _compile_binary(self, node: "BinaryExpr") -> None:
        # Short-circuit для and/or
        if node.op == "and":
            self._visit(node.left)
            self._emit("DUP")
            jump_out = self._emit("JUMP_IF_FALSE", None)
            self._emit("POP")
            self._visit(node.right)
            self._patch(jump_out, a=self._current_ip())
            return
        if node.op == "or":
            self._visit(node.left)
            self._emit("DUP")
            jump_out = self._emit("JUMP_IF_TRUE", None)
            self._emit("POP")
            self._visit(node.right)
            self._patch(jump_out, a=self._current_ip())
            return

        self._visit(node.left)
        self._visit(node.right)
        # Парсер использует строковые имена для сравнений (eq, lt, ...) и символы для арифметики (+, -, ...)
        op_map = {
            "+": "ADD", "-": "SUB", "*": "MUL", "/": "DIV", "%": "MOD",
            # Символьные варианты
            "==": "EQ", "!=": "NEQ", "<": "LT", ">": "GT", "<=": "LTE", ">=": "GTE",
            # Именованные варианты (то, что реально генерирует парсер)
            "eq": "EQ", "neq": "NEQ", "lt": "LT", "gt": "GT", "lte": "LTE", "gte": "GTE",
            "and": "AND", "or": "OR",
        }
        opcode = op_map.get(node.op)
        if opcode:
            self._emit(opcode)
        else:
            self._emit("CALL_HOST", f"binop_{node.op}", 2)

    def _compile_call(self, node: "CallExpr") -> None:
        """Вызов функции или builtin."""
        if isinstance(node.callee, Variable):
            # GUARD_VIOLATION_ACK is an internal-only opcode. Source code must
            # not be able to clear guard_violation_active manually. Recovery ACK
            # is inserted only by compiler-lowered catch(GUARD_VIOLATION) paths.
            if node.callee.name == "acknowledge_violation":
                raise CompileError(
                    "acknowledge_violation() is internal-only; "
                    "GUARD_VIOLATION_ACK may only be compiler-inserted"
                )
            # Сначала компилируем аргументы
            for arg in node.args:
                self._visit(arg)
            # Проверяем: это builtin host call или пользовательская fn?
            builtins = {"print", "len", "str", "int", "float", "bool",
                        "range", "llm", "time", "random", "uuid",
                        "create_state_checkpoint"}
            if node.callee.name in builtins:
                self._emit("CALL_HOST", node.callee.name, len(node.args))
            else:
                # Загружаем функцию-объект и вызываем
                self._emit("LOAD_NAME", node.callee.name)
                self._emit("CALL", len(node.args))
        elif isinstance(node.callee, MemberAccess):
            # obj.method(args)
            self._visit(node.callee.obj)
            for arg in node.args:
                self._visit(arg)
            self._emit("CALL_METHOD", node.callee.member, len(node.args))
        else:
            # Сложный каллибль — fallback
            self._visit(node.callee)
            for arg in node.args:
                self._visit(arg)
            self._emit("CALL", len(node.args))
