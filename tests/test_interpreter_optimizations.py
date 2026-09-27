"""Behavioral boundaries of the optimized host interpreter."""
import hashlib
from types import MappingProxyType

import pytest

from synapse import Environment, FuelExhaustedError, Interpreter, compile_to_ast, run
from synapse.ast import Literal, Node
from synapse.interpreter import RuntimeError as SynapseRuntimeError
from synapse.runtime.vm_routing import classify_ast_node, classify_ast_node_v22, fallback_reason_for


def test_environment_identity_is_lazy_stable_and_survives_serialization(monkeypatch):
    allocated = []
    def allocate():
        allocated.append(len(allocated))
        return f"environment-{allocated[-1]}"
    monkeypatch.setattr("synapse.interpreter.uuid.uuid4", allocate)
    parent = Environment(env_id="retained-parent")
    first, second = Environment(parent), Environment(parent)
    assert allocated == []
    first.define("value", 42)
    first_snapshot = first.to_dict()
    assert first.env_id == first_snapshot["env_id"]
    assert first.env_id != second.env_id
    restored = Environment.from_dict(first_snapshot)
    assert restored.to_dict() == first_snapshot
    assert restored.get("value") == 42
    assert len(allocated) == 2


def test_source_hash_tracks_replacement_none_and_empty_source():
    interpreter = Interpreter()
    for source in ("print(1)", "print(2)", None, "", "print(1)"):
        interpreter.source_code = source
        digest = None if source is None else hashlib.sha256(source.encode()).hexdigest()
        for _ in range(3):
            assert interpreter._source_sha256() == digest
            assert interpreter._current_runtime_program_hash() == (None if digest is None else "sha256:" + digest)


def test_dispatch_preserves_subclass_behavior_and_rejects_same_name_impostor():
    class SpecializedLiteral(Literal):
        pass
    interpreter = Interpreter()
    assert interpreter.evaluate(SpecializedLiteral(value=42), interpreter.global_env) == 42
    unrelated = type("Literal", (Node,), {})()
    with pytest.raises(SynapseRuntimeError, match="Unknown node type"):
        interpreter.evaluate(unrelated, interpreter.global_env)


def test_closures_keep_distinct_captured_environments():
    source = '''
let first = null
let second = null
for i in range(2) {
    if i == 0 { fn zero() { return i } first = zero } else { fn one() { return i } second = one }
}
print(first())
print(second())
'''
    assert run(source) == "0\n1"


@pytest.mark.parametrize("source", [
    "while true {}", "for i in range(100) {}",
    "fn main() { while true {} }",
    "fn spin() { while true {} } spin()",
])
def test_fuel_exhaustion_propagates_through_calls_and_auto_main(source):
    interpreter = Interpreter(loop_fuel_limit=3)
    with pytest.raises(FuelExhaustedError, match="3 loop iterations"):
        run(source, interpreter)
    with pytest.raises(FuelExhaustedError):
        run('print("after exhaustion")', interpreter)
    assert interpreter.get_output() == ""


def test_nested_loops_share_one_budget_and_exact_limit_completes():
    source = "let n = 0 for i in range(2) { for j in range(2) { n = n + 1 } } print(n)"
    assert run(source, Interpreter(loop_fuel_limit=6)) == "4"
    with pytest.raises(FuelExhaustedError):
        run(source, Interpreter(loop_fuel_limit=5))
    assert run("while false {} print(0)", Interpreter(loop_fuel_limit=0)) == "0"


def test_swallowed_inner_error_cannot_become_success_or_allow_later_effects():
    interpreter = Interpreter(loop_fuel_limit=0)
    def swallow():
        try:
            interpreter.evaluate(compile_to_ast("while true {}").statements[0], interpreter.global_env)
        except Exception:
            return "not a successful task"
    interpreter.global_env.define("swallow", swallow)
    with pytest.raises(FuelExhaustedError):
        run('swallow() print("must not run")', interpreter)
    assert interpreter.get_output() == ""


def test_coroutine_entrypoint_preserves_fuel_failure():
    interpreter = Interpreter(loop_fuel_limit=2)
    flow = interpreter.interpret_async(compile_to_ast("while true {}"))
    with pytest.raises(FuelExhaustedError):
        next(flow)


@pytest.mark.parametrize("value", ["", "-1", "no", "1.5", "unlimited"])
def test_invalid_environment_fuel_limit_is_not_unlimited(monkeypatch, value):
    monkeypatch.setenv("SYNAPSE_FUEL_LIMIT", value)
    with pytest.raises(ValueError, match="SYNAPSE_FUEL_LIMIT"):
        Interpreter()


def test_trace_identity_observes_mutation_replacement_append_and_rollback():
    interpreter = Interpreter()
    first = {"type": "one"}
    second = {"type": "two"}
    interpreter.execution_history = [first, second]
    fallback = interpreter.current_trace_id()
    assert fallback == hashlib.sha256((interpreter.run_id + "2").encode()).hexdigest()[:16]
    first["trace_id"] = "earlier"
    assert interpreter.current_trace_id() == "earlier"
    second["trace_id"] = "later"
    assert interpreter.current_trace_id() == "later"
    del second["trace_id"]
    assert interpreter.current_trace_id() == "earlier"
    interpreter.execution_history.append(MappingProxyType({"trace_id": "mapping"}))
    assert interpreter.current_trace_id() == "mapping"
    interpreter.execution_history[:] = [{"type": "replacement"}, {"type": "replacement"}]
    assert interpreter.current_trace_id() == fallback
    interpreter.execution_history = []
    assert interpreter.current_trace_id() != fallback


def test_lazy_backend_captures_configuration_and_accepts_explicit_backend(monkeypatch):
    monkeypatch.setenv("SYNAPSE_LLM_MODE", "mock")
    monkeypatch.setenv("SYNAPSE_LLM_PROVIDER", "mock")
    monkeypatch.setenv("SYNAPSE_LLM_MODEL", "at-construction")
    interpreter = Interpreter()
    monkeypatch.setenv("SYNAPSE_LLM_MODEL", "later")
    assert interpreter.llm_backend.gateway_config.model == "at-construction"
    backend = object()
    interpreter.llm_backend = backend
    assert interpreter.llm_backend is backend


def test_empty_explicit_llm_environment_does_not_read_live_process(monkeypatch):
    from synapse.llm.gateway import config_from_env
    monkeypatch.setenv("SYNAPSE_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-never-used")
    config = config_from_env(environ={})
    assert config.provider == "mock" and config.api_key is None


def test_routing_versions_stay_distinct_and_fallback_templates_are_immutable():
    assert classify_ast_node("LetStmt").route == "HOST_EVAL"
    assert classify_ast_node_v22("LetStmt").route == "CVM"
    for node in ("UnknownNode", "AffectiveEventStmt"):
        reason = fallback_reason_for(node)
        with pytest.raises(TypeError):
            reason["code"] = "corrupted"
        retained = dict(reason)
        retained["code"] = "private event edit"
        assert fallback_reason_for(node)["code"] != retained["code"]
