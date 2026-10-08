"""Acceptance: a snapshot is the state of its moment, and restoring it gives that state back (review F4, F5).

A snapshot shares nothing with the running program: the interpreter going on
never rewrites one already taken. Saved as JSON and restored, it keeps the
program's shared objects shared — two names for one dict, an agent bound under
a second name — and the restored interpreter never writes into the snapshot it
came from. A 1.0.0 snapshot, which did not record sharing, still restores, each
position as its own object; an unknown version or an inconsistent record of
sharing is refused.
"""
import copy
import json

import pytest

from synapse import Interpreter, compile_to_ast
from synapse.interpreter import RuntimeError as SynapseError

AGENT = 'agent Worker { model "mock" }\nlet self = Worker\nmemory.write("initial") { reason "setup" }\n'


def run(source, interp=None):
    interp = interp or Interpreter()
    interp.interpret(compile_to_ast(source))
    return interp


def saved(interp):
    return json.loads(json.dumps(interp.snapshot()))


def memories(interp):
    return [copy.deepcopy(interp.global_env.get(name).memory.short_term) for name in ("Worker", "self")]


def test_a_restored_snapshot_keeps_shared_data_one_object():
    source = 'let x = {"n": 1}\nlet y = x\nlet holder = {"inner": x, "items": [x]}\n'
    original = run(source)
    restored = Interpreter.restore_snapshot(saved(original))
    for interp in (original, restored):
        run("y.n = 9\n", interp)
    values = [[interp.global_env.get("x"), interp.global_env.get("holder")] for interp in (original, restored)]
    assert values[0] == values[1] == [{"n": 9}, {"inner": {"n": 9}, "items": [{"n": 9}]}]
    env = restored.global_env
    assert env.get("y") is env.get("x") is env.get("holder")["inner"] is env.get("holder")["items"][0]


def test_an_agent_bound_under_a_second_name_stays_one_agent():
    original = run(AGENT)
    restored = Interpreter.restore_snapshot(saved(original))
    for interp in (original, restored):
        run('memory.write("later") { reason "next" }\n', interp)
    assert restored.global_env.get("self") is restored.global_env.get("Worker")
    assert memories(restored) == memories(original)
    assert [entry["value"] for entry in memories(restored)[0]] == ["initial", "later"]


def test_the_restored_interpreter_never_writes_into_the_snapshot_it_came_from():
    snapshot = saved(run(AGENT + 'send Worker.process("first")\nlet seen = uuid()\n'))
    before = copy.deepcopy(snapshot)
    restored = Interpreter.restore_snapshot(snapshot)
    run('memory.write("later") { reason "next" }\nsend Worker.process("second")\nlet more = uuid()\n', restored)
    assert snapshot == before


def test_a_snapshot_does_not_change_as_the_interpreter_goes_on():
    interp = run(AGENT + 'let payload = {"n": 1}\nsend Worker.process(payload)\nlet original = uuid()\n')
    taken = interp.snapshot()
    before = copy.deepcopy(taken)
    run('payload.n = 9\nmemory.write("later") { reason "next" }\nsend Worker.process("second")\n'
        'let continued = uuid()\n', interp)
    assert taken == before
    assert taken["history_hash"] == Interpreter.restore_snapshot(taken).compute_history_hash()


def test_a_1_0_0_snapshot_restores_each_position_as_its_own_object():
    snapshot = saved(run('let x = {"n": 1}\nlet y = x\n'))
    snapshot["version"] = "1.0.0"
    del snapshot["global_env"]["aliases"]
    restored = run("y.n = 9\n", Interpreter.restore_snapshot(snapshot))
    assert (restored.global_env.get("x"), restored.global_env.get("y")) == ({"n": 1}, {"n": 9})


@pytest.mark.parametrize("tamper, refusal", [
    (lambda snapshot: snapshot.update(version="2.0.0"), "Unsupported snapshot version"),
    (lambda snapshot: snapshot["global_env"]["variables"]["y"].update(n=7),
     "positions recorded as one object hold different values"),
    (lambda snapshot: snapshot["global_env"]["aliases"][0].append(["variables", "missing"]),
     "an alias names no position of the snapshot"),
])
def test_an_unknown_version_or_an_inconsistent_record_of_sharing_is_refused(tamper, refusal):
    snapshot = saved(run('let x = {"n": 1}\nlet y = x\n'))
    tamper(snapshot)
    with pytest.raises(SynapseError, match=refusal):
        Interpreter.restore_snapshot(snapshot)


def test_control_a_snapshot_without_shared_objects_restores_its_values():
    original = run('let x = {"n": 1}\nlet y = {"n": 1}\nlet z = [1, 2]\n')
    snapshot = saved(original)
    assert snapshot["global_env"]["aliases"] == []
    restored = run("y.n = 9\n", Interpreter.restore_snapshot(snapshot))
    assert [restored.global_env.get(name) for name in ("x", "y", "z")] == [{"n": 1}, {"n": 9}, [1, 2]]
