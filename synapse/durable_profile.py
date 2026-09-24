"""The durable cognitive execution profile (``synapse.durable.cognitive/v1``).

P2a durable runs support a narrow statement subset whose replay consumes a few
recorded effects. The cognitive profile extends ``run --durable`` to functions,
loops, habits, the memory palace, ``dream``, ``integrate``, plan segments,
external actions and their slow path. Its replay model is different and
stronger: a reconstructed run re-executes the program and every event it
produces must equal the recorded event at the same position, so a divergence
is an integrity failure, never a silently different run. Recorded effects
(external actions, LLM answers, clock and randomness) are consumed, never
repeated.

This module owns the profile's fail-closed classification of the program and
the preparation of an interpreter for it. Artifact persistence stays with the
durable application; memory semantics stay with the memory subsystem, reached
only through ``synapse.memory_points``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import ast as synapse_ast
from .memory_points import ACTION_FAILED, DURABLE_COGNITIVE_PROFILE

COGNITIVE_ARTIFACT_SCHEMA = "1.1.0"

#: Builtins a cognitive program may call directly. ``tool`` and ``task_plan``
#: exist only while a memory session is bound.
PURE_BUILTINS = frozenset({
    "print", "len", "range", "time", "random", "uuid", "type", "str", "int", "float", "list", "dict",
    "abs", "sum", "max", "min", "sorted", "reversed", "enumerate", "zip", "any", "all",
})
MEMORY_BUILTINS = frozenset({"tool", "task_plan"})
_INTEGRATE_FORBIDDEN_BUILTINS = frozenset({"print", "time", "random", "uuid"}) | MEMORY_BUILTINS
_DEFAULT_PALACE_BACKENDS = frozenset({"sqlite", "memory", ""})


class CognitiveProfileViolation(ValueError):
    """The program uses a construct outside the durable cognitive profile."""


@dataclass(frozen=True)
class _Scope:
    top_level: bool = True
    suspension_allowed: bool = True
    in_function: bool = False
    in_dream: bool = False
    in_integrate: bool = False
    functions: frozenset[str] = frozenset()

    def nested(self, **changes: Any) -> "_Scope":
        values = {"top_level": False, "suspension_allowed": self.suspension_allowed,
                  "in_function": self.in_function, "in_dream": self.in_dream,
                  "in_integrate": self.in_integrate, "functions": self.functions}
        values.update(changes)
        return _Scope(**values)


def _fail(detail: str) -> CognitiveProfileViolation:
    return CognitiveProfileViolation(f"durable cognitive profile: {detail}")


def validate_cognitive_program(root: synapse_ast.Program) -> set[str]:
    """Classify a whole program; returns the identifiers the source owns.

    Fail-closed: an unlisted node, a suspension where the tree-walker cannot
    persist it, an effect inside ``dream`` or ``integrate``, a palace bound to a
    file backend or a call to an unknown callee rejects the program.
    """
    if not isinstance(root, synapse_ast.Program):
        raise _fail("the durable entrypoint is a program")
    functions = frozenset(stmt.name for stmt in _walk(root) if isinstance(stmt, synapse_ast.FnDef) and stmt.name)
    parameters = frozenset(param for stmt in _walk(root) if isinstance(stmt, synapse_ast.FnDef)
                           for param in stmt.params)
    scope = _Scope(functions=functions | parameters)
    for statement in root.statements:
        _statement(statement, scope)
    owned: set[str] = set()
    for node in _walk(root):
        if isinstance(node, synapse_ast.LetStmt):
            owned.add(node.name)
        elif isinstance(node, synapse_ast.AssignStmt):
            owned.add(node.target)
        elif isinstance(node, synapse_ast.FnDef):
            owned.add(node.name)
            owned.update(node.params)
        elif isinstance(node, synapse_ast.ForStmt):
            owned.add(node.var)
        elif isinstance(node, synapse_ast.TryCatchStmt) and node.catch_binding:
            owned.add(node.catch_binding)
        elif isinstance(node, (synapse_ast.HabitStmt, synapse_ast.ConsolidateStmt)):
            owned.add(node.binding)
        elif isinstance(node, synapse_ast.MemoryPalaceDef):
            owned.update({node.name, node.binding})
    return owned


def _walk(node: Any):
    if isinstance(node, synapse_ast.Node):
        yield node
        for key, value in vars(node).items():
            if key in {"line", "column", "closure"}:
                continue
            yield from _walk(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        for item in node.values():
            yield from _walk(item)


def _block(statements: list[Any], scope: _Scope) -> None:
    for statement in statements:
        _statement(statement, scope)


def _statement(node: Any, scope: _Scope) -> None:
    if isinstance(node, synapse_ast.ExprStmt):
        if isinstance(node.expr, _EXPRESSIONS):
            _expression(node.expr, scope)
        else:
            _statement(node.expr, scope)
        return
    if isinstance(node, synapse_ast.IntegrateBlock):
        _integrate(node, scope)
        return
    if isinstance(node, synapse_ast.LetStmt):
        _expression(node.value, scope)
        return
    if isinstance(node, synapse_ast.AssignStmt):
        if node.target == "soulprint":
            raise _fail("identity state is not assignable")
        _expression(node.value, scope)
        return
    if isinstance(node, synapse_ast.MemberAssignStmt):
        _expression(node.target, scope)
        _expression(node.value, scope)
        return
    if isinstance(node, synapse_ast.IfStmt):
        _expression(node.condition, scope)
        branch = scope.nested(top_level=False)
        _block(node.then_body, branch)
        _block(node.else_body, branch)
        return
    if isinstance(node, synapse_ast.AssertStmt):
        _expression(node.condition, scope.nested(suspension_allowed=False))
        if node.message is not None:
            _expression(node.message, scope.nested(suspension_allowed=False))
        return
    if isinstance(node, (synapse_ast.WhileStmt, synapse_ast.ForStmt)):
        loop = scope.nested(suspension_allowed=False)
        if isinstance(node, synapse_ast.WhileStmt):
            _expression(node.condition, loop)
        else:
            _expression(node.iterable, loop)
        _block(node.body, loop)
        return
    if isinstance(node, synapse_ast.FnDef):
        if scope.in_dream or scope.in_integrate:
            raise _fail("functions are defined outside dream and integrate")
        _block(node.body, scope.nested(suspension_allowed=False, in_function=True))
        return
    if isinstance(node, synapse_ast.ReturnStmt):
        if not (scope.in_function or scope.in_dream):
            raise _fail("return belongs to a function or a dream body")
        if node.value is not None:
            _expression(node.value, scope)
        return
    if isinstance(node, synapse_ast.ContextBlock):
        if scope.in_dream or scope.in_integrate:
            raise _fail("segments are entered outside dream and integrate")
        if not isinstance(node.label, str) or not node.label:
            raise _fail("a context block names its segment with a literal label")
        _block(node.body, scope.nested(suspension_allowed=False))
        return
    if isinstance(node, synapse_ast.TryCatchStmt):
        if node.catch_error != ACTION_FAILED:
            raise _fail("only catch(ACTION_FAILED) is part of the profile")
        if scope.in_dream or scope.in_integrate:
            raise _fail("an action slow path cannot run inside dream or integrate")
        _block(node.try_body, scope.nested(suspension_allowed=False))
        _block(node.catch_body, scope.nested(suspension_allowed=False))
        return
    if isinstance(node, synapse_ast.HabitStmt):
        if not scope.top_level:
            raise _fail("habits are declared at top level")
        for condition in list(node.activate_when) + list(node.suppress_when):
            if not isinstance(condition, synapse_ast.InlineHabitCond):
                raise _fail("habit conditions are inline typed or context conditions")
            for _, _, value in condition.field_conditions:
                if not isinstance(value, synapse_ast.Literal):
                    raise _fail("typed habit conditions compare with literals")
        for field_node in (node.frequency_val, node.stability_val, node.energy_cost):
            if field_node is not None and not isinstance(field_node, synapse_ast.Literal):
                raise _fail("habit thresholds are literals")
        if node.promote_to is not None:
            raise _fail("procedural promotion is owned by the memory court")
        _block(node.body, scope.nested(suspension_allowed=False))
        return
    if isinstance(node, synapse_ast.EnergyPoolDecl):
        if not scope.top_level:
            raise _fail("an energy pool is declared at top level")
        return
    if isinstance(node, synapse_ast.MemoryPalaceDef):
        if not scope.top_level:
            raise _fail("a memory palace is declared at top level")
        if str(node.backend or "").lower() not in _DEFAULT_PALACE_BACKENDS:
            raise _fail("a durable palace is rebuilt by replay; file or remote backends are refused")
        if node.decay_policy:
            for value in node.decay_policy.values():
                if not isinstance(value, (str, int, float)):
                    raise _fail("decay policy values are literals")
        return
    if isinstance(node, synapse_ast.ConsolidateStmt):
        if scope.in_dream or scope.in_integrate or scope.in_function:
            raise _fail("consolidate runs as a top-level session statement")
        if node.affective_routing:
            raise _fail("affective routing is outside the memory court")
        _expression(node.palace, scope)
        return
    if isinstance(node, synapse_ast.DreamBlock):
        _dream(node, scope)
        return
    raise _fail(f"{type(node).__name__} is not a supported statement")


_EXPRESSIONS = (synapse_ast.Literal, synapse_ast.Variable, synapse_ast.AffectivePadLiteral, synapse_ast.DecayExpr,
                synapse_ast.BinaryExpr, synapse_ast.UnaryExpr, synapse_ast.ListExpr, synapse_ast.DictExpr,
                synapse_ast.MemberAccess, synapse_ast.PromptExpr, synapse_ast.CallExpr, synapse_ast.LLMCall)


def _integrate(node: synapse_ast.IntegrateBlock, scope: _Scope) -> None:
    if scope.in_dream or scope.in_integrate or scope.in_function:
        raise _fail("integrate is a top-level transaction")
    if node.dream_result is not None:
        _expression(node.dream_result, scope)
    if node.reason is not None:
        _expression(node.reason, scope)
    _block(node.body, scope.nested(suspension_allowed=False, in_integrate=True))


def _dream(node: synapse_ast.DreamBlock, scope: _Scope) -> None:
    if scope.in_dream or scope.in_integrate:
        raise _fail("dream does not nest")
    inner = scope.nested(suspension_allowed=False, in_dream=True)
    if node.scenario is not None:
        _expression(node.scenario, scope)
    for value in (node.config or {}).values():
        _expression(value, scope)
    _block(node.body, inner)
    if node.integration_clause:
        _block(node.integration_clause, scope.nested(suspension_allowed=False))


def _expression(node: Any, scope: _Scope) -> None:
    if node is None:
        return
    if isinstance(node, (synapse_ast.Literal, synapse_ast.Variable, synapse_ast.AffectivePadLiteral,
                         synapse_ast.DecayExpr)):
        return
    if isinstance(node, synapse_ast.BinaryExpr):
        _expression(node.left, scope)
        _expression(node.right, scope)
        return
    if isinstance(node, synapse_ast.UnaryExpr):
        _expression(node.operand, scope)
        return
    if isinstance(node, synapse_ast.ListExpr):
        for item in node.elements:
            _expression(item, scope)
        return
    if isinstance(node, synapse_ast.DictExpr):
        for key, value in node.pairs:
            if not isinstance(key, str):
                raise _fail("object keys are strings")
            _expression(value, scope)
        return
    if isinstance(node, synapse_ast.MemberAccess):
        _expression(node.obj, scope)
        return
    if isinstance(node, synapse_ast.PromptExpr):
        for value in node.args.values():
            _expression(value, scope)
        return
    if isinstance(node, synapse_ast.CallExpr):
        _call(node, scope)
        return
    if isinstance(node, synapse_ast.LLMCall):
        if not scope.suspension_allowed:
            raise _fail("an LLM call suspends only at a top-level statement")
        _expression(node.prompt, scope)
        return
    raise _fail(f"{type(node).__name__} is not a supported expression")


def _call(node: synapse_ast.CallExpr, scope: _Scope) -> None:
    callee = node.callee
    if not isinstance(callee, synapse_ast.Variable):
        raise _fail("calls name a function directly")
    name = callee.name
    if name in MEMORY_BUILTINS:
        if scope.in_dream or scope.in_integrate:
            raise _fail(f"{name} is an external effect and cannot run inside dream or integrate")
    elif name not in PURE_BUILTINS and name not in scope.functions:
        raise _fail(f"call to an unknown function: {name}")
    if scope.in_integrate and name in _INTEGRATE_FORBIDDEN_BUILTINS:
        raise _fail(f"{name} is forbidden inside integrate")
    for argument in node.args:
        _expression(argument, scope)


def prepare_cognitive_interpreter(interpreter: Any, *, run_id: str) -> None:
    """Configure an interpreter for the cognitive profile before binding memory."""
    interpreter.durable_profile = DURABLE_COGNITIVE_PROFILE
    # The Alpha3g integrate path is the one with a replay applier (I4).
    interpreter.integrate_i2_skeleton_enabled = True
    # Trace identities fall back to the run id; it must be the durable one.
    interpreter.run_id = run_id
