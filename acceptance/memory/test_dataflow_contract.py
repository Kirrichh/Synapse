"""Contract of event-driven graphs over plain programs (refinement §16).

The rules the heavy scenarios rely on, on the language itself: the shape a
``parallel`` graph must have (known inputs, no cycle of data, pure nodes
pure, a raised event for every subscription, an effect that acts on the
committed value only, a bounded limit), where the durable profile admits it,
what an observation contract is, and how a graph of pure nodes runs in one
interpreter — a dependent node after its inputs, mutually consistent input
versions in a diamond, a signal raised once per version of its inputs, and a
feedback loop stopped by its bound rather than running forever.
"""
from __future__ import annotations

import pytest

from synapse.durable_profile import CognitiveProfileViolation, validate_cognitive_program
from synapse.interpreter import Interpreter, RuntimeError as ProgramError
from synapse.lexer import Lexer
from synapse.memory_consolidation.tools.contracts import ToolConfigurationViolation, parse_tool_configuration
from synapse.parser import Parser
from synapse.runtime.dataflow import MAX_ATTEMPTS, GraphViolation, analyse


def _program(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


def _graph(body: str, header: str = "parallel g"):
    program = _program(f"{header} {{\n{body}\n}}\n")
    return program.statements[0]


def _run(source: str):
    interpreter = Interpreter()
    interpreter.interpret(_program(source))
    return interpreter


@pytest.mark.parametrize("body, header, reason", [
    ('node a = {"x": b.x}\nnode b = {"x": a.x}\ncommit a', "parallel g", "cycle of data"),
    ('node a = {"x": 1}\ncommit z', "parallel g", "unknown node"),
    ('node a = {"x": 1}\nnode a = {"x": 2}\ncommit a', "parallel g", "unique names"),
    ('node a = {"x": a.x}\ncommit a', "parallel g", "reads itself"),
    ('node a = {"x": print(1)}\ncommit a', "parallel g", "is pure"),
    ('node a = tool("quote", {"plan": now()})\ncommit a', "parallel g", "is pure"),
    ('node a = tool("quote")\ncommit a', "parallel g", r"tool\(name, arguments\)"),
    ('node a on "bump" = {"x": 1}\ncommit a', "parallel g", "no signal raises"),
    ('node a = {"x": 1}\nsignal "bump" when len(1) == 1\ncommit a => tool("order", {"x": b.x})\n'
     'node b = {"x": 2}', "parallel g", "committed value only"),
    ('node a = {"x": 1}\ncommit a', "parallel g limit 0", "between 1 and 64"),
    ('node a = {"x": 1}\ncommit a', "parallel g limit 65", "between 1 and 64"),
])
def test_a_graph_outside_its_shape_is_refused_before_it_runs(body, header, reason):
    with pytest.raises(GraphViolation, match=reason):
        analyse(_graph(body, header))


def test_the_analysis_names_inputs_calls_and_what_the_commit_depends_on():
    graph = analyse(_graph('''
node price on "repriced" = tool("price_feed", {"plan": plan})
node watch = tool("price_watch", {"plan": plan})
node audit = tool("audit_trail", {"plan": plan})
node terms = {"price": price.payload.price}
signal "repriced" when watch.payload.changed == true
commit terms => tool("place_order", {"price": terms.price})'''))
    assert graph.limit == 8 and graph.order.index("price") < graph.order.index("terms")
    assert graph.deps == {"price": [], "watch": [], "audit": [], "terms": ["price"]}
    assert graph.calls == {"price": True, "watch": True, "audit": True, "terms": False}
    # The commit waits for its input and for the source of the event that refreshes it — never for the audit.
    assert graph.upstream == {"terms", "price", "watch"}


def test_the_durable_profile_admits_a_graph_only_where_it_can_be_persisted_and_owns_its_names():
    graph = '''parallel offer {
  node quote = tool("billing_quote", {"plan": "basic"})
  commit quote
}
'''
    owned = validate_cognitive_program(_program(graph))
    assert {"offer", "quote"} <= owned
    with pytest.raises(CognitiveProfileViolation, match="outside dream and integrate"):
        validate_cognitive_program(_program(f"dream {{\n{graph}\n}}\n"))
    with pytest.raises(CognitiveProfileViolation, match="is pure"):
        validate_cognitive_program(_program('parallel g {\n  node a = {"x": print(1)}\n  commit a\n}\n'))


def _tools(contract: dict) -> dict:
    return {"schema_version": "synapse.memory.tool-configuration/v2", "servers": [{"id": "desk", "argv": ["x"]}],
            "tools": [{"name": "quote", "server": "desk", "descriptor_sha256": "0" * 64,
                       "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
                       "source": "billing:ops", "contract": contract}],
            "provenance": {"billing:ops": {"ancestors": []}}}


def test_an_observation_is_idempotent_leaves_no_effect_and_compensates_nothing():
    admitted = parse_tool_configuration(_tools({"observation": True, "idempotent": True}))
    assert admitted.tools["quote"].observation is True
    assert parse_tool_configuration(_tools({})).tools["quote"].canonical().get("observation") is None
    for contract in ({"observation": True},
                     {"observation": True, "idempotent": True, "effect_on_err": {"BUSY": "partial"}},
                     {"observation": True, "idempotent": True, "compensates": "order"},
                     {"observation": "yes", "idempotent": True}):
        with pytest.raises(ToolConfigurationViolation):
            parse_tool_configuration(_tools(contract))


def test_a_dependent_node_reads_mutually_consistent_versions_of_its_inputs():
    interpreter = _run('''
parallel shape {
  node base = {"n": 2}
  node left = {"v": base.n + 1}
  node right = {"v": base.n * 10}
  node joined = {"sum": left.v + right.v}
  signal "seen" when left.v == 3
  commit joined
}
''')
    result = interpreter.global_env.get("shape")
    assert result["value"] == {"sum": 23} and result["stale"] == 0 and result["cancelled"] == 0
    order = [step["node"] for step in result["steps"]]
    assert order.index("joined") > max(order.index("left"), order.index("right"))
    joined, = [step for step in result["steps"] if step["node"] == "joined"]
    assert joined["reads"] == {"left": 1, "right": 1}
    assert result["signals"] == [{"event": "seen", "count": 1, "by": {"left": 1}}]
    kinds = [event["type"] for event in interpreter.execution_history]
    assert kinds[0] == "dataflow_started" and kinds[-1] == "dataflow_commit"
    assert kinds.count("dataflow_step") == 4 and kinds.count("dataflow_signal") == 1


def test_a_feedback_loop_is_stopped_by_its_bound():
    with pytest.raises(ProgramError, match=f"more than {MAX_ATTEMPTS} times"):
        _run('''
parallel loop {
  node tick on "again" = {"n": 1}
  node echo = {"n": tick.n}
  signal "again" when echo.n == 1
  commit echo
}
''')
