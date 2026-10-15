"""Acceptance: the runtime owns its state, and what it records stays what happened.

A program's binding, a history event and the live state never share one
object; declared values are checked; a checkpoint keeps its moment; the energy
pool is read live and changed only by the runtime; a palace snapshot carries
its records; a nondeterministic builtin is recorded under any name; a policy
guard reads and changes nothing, however it ends (review F1), and what it reads
from an agent's memory is its own copy.
"""
import copy
import math

import pytest

from synapse import Interpreter, compile_to_ast
from synapse.affective import AffectiveState, clamp
from synapse.interpreter import PolicyCompilationError
from synapse.interpreter import RuntimeError as SynapseError


def run(source, snapshot=None):
    interp = Interpreter()
    if snapshot is not None:
        interp.load_snapshot(snapshot)
    interp.source_code = source
    interp.interpret(compile_to_ast(source))
    return interp


def events(interp, kind):
    return [event for event in interp.execution_history if event["type"] == kind]


def test_affective_bindings_events_and_live_state_are_separate():
    interp = run('affective state "Mood" { baseline { valence 0.2 arousal 0.4 dominance 0.6 } bind mood }\n'
                 'mood.current.valence = 9\n'
                 'affective event "sad" { valence -0.9 bind sad_tag }\n'
                 'sad_tag.after.valence = 5\n'
                 'fn lift() { affective event "lift" { valence 0.2 bind lift_tag } }\n'
                 'let returned = lift()\n'
                 'returned.after.valence = 5\n')
    state = interp.affective_states["Mood"]
    assert events(interp, "affective_state_initialized")[0]["state"]["current"]["valence"] == 0.2
    assert state.current["valence"] == pytest.approx(-0.5)
    assert [event["tag"]["after"]["valence"] for event in events(interp, "affective_event_tagged")] == \
        pytest.approx([-0.7, -0.5])
    assert [tag["after"]["valence"] for tag in interp.affective_events] == pytest.approx([-0.7, -0.5])
    assert [tag["after"]["valence"] for tag in state.events] == pytest.approx([-0.7, -0.5])


@pytest.mark.parametrize("values, message", [
    ({"valence": 5.0, "arousal": 0.4, "dominance": 0.6}, "baseline valence must lie in [-1.0, 1.0], got 5.0"),
    ({"valence": 0.0, "arousal": math.nan, "dominance": 0.6}, "baseline arousal must be a finite number, got nan"),
])
def test_a_declared_mood_outside_its_range_or_not_finite_is_refused(values, message):
    with pytest.raises(ValueError, match=message.replace("[", r"\[").replace("]", r"\]")):
        AffectiveState(name="Mood", baseline=values)
    with pytest.raises(ValueError, match="must be a finite number"):
        clamp(math.nan, 0.0, 1.0)


def test_a_restored_mood_does_not_alter_the_data_it_was_restored_from():
    data = {"name": "Imported", "current": {"valence": 0.5, "arousal": 0.4, "dominance": 0.6},
            "events": [{"event": "earlier"}], "dimensions": {"valence": [-1.0, 1.0]}}
    state = AffectiveState.from_dict(data)
    returned = state.apply_event("lift", {"valence": 0.3})
    returned["after"]["valence"] = 5.0
    state.dimensions["valence"].append(9.0)
    assert state.events[-1]["after"]["valence"] == pytest.approx(0.8)
    assert data["current"] == {"valence": 0.5, "arousal": 0.4, "dominance": 0.6}
    assert data["events"] == [{"event": "earlier"}]
    assert data["dimensions"] == {"valence": [-1.0, 1.0]}
    assert state.current["valence"] == pytest.approx(0.8)
    with pytest.raises(ValueError, match="current valence"):
        AffectiveState.from_dict({"name": "Imported", "current": {"valence": 9.0, "arousal": 0.4, "dominance": 0.6}})


def test_a_checkpoint_keeps_the_state_of_its_moment():
    interp = run('agent Worker { model "mock" }\nlet self = Worker\n')
    returned = interp.create_state_checkpoint("before")
    interp.interpret(compile_to_ast('memory.write("written after checkpoint") { reason "probe" }\n'
                                    'send Worker.process("delivered after checkpoint")\n'))
    stored = interp.checkpoints[0]
    assert stored["mailboxes"] == {"global": []}
    assert stored["global_env"]["agents"]["Worker"]["memory"]["short_term"] == []
    returned["mailboxes"]["global"].append("forged")
    assert interp.checkpoints[0]["mailboxes"] == {"global": []}


def test_the_energy_pool_is_read_live_and_changed_only_by_the_runtime():
    interp = run('energy_pool { max 10 initial 10 recharge 0 per 100 events rest_threshold 9 hysteresis_margin 1 }\n'
                 'habit "BodyHabit" from pattern {\n    energy_cost 2\n    activate when { context "task" }\n'
                 '    body { print("habit body") }\n    bind habit_id\n}\n'
                 'context "task" { print("task body") }\n'
                 'let seen = energy_pool.current\nlet mode = energy_pool.mode\n')
    assert (interp.global_env.get("seen"), interp.global_env.get("mode")) == (8.0, "REST")
    with pytest.raises(Exception, match="the energy pool is changed only by the runtime"):
        interp.interpret(compile_to_ast("energy_pool.current = 100\n"))
    assert interp.energy_pool.current == 8.0


def test_a_palace_creation_event_keeps_its_rooms_and_a_snapshot_keeps_its_records():
    interp = run('memory palace "P" { rooms { semantic } backend sqlite bind palace }\n'
                 'imprint into palace.episodic { content "first" confidence 1.0 bind mid }\n')
    assert events(interp, "memory_palace_created")[0]["rooms"] == ["semantic"]
    assert interp.global_env.get("palace")["rooms"] == ["semantic"]
    restored = Interpreter.restore_snapshot(interp.snapshot())
    assert [record["content"] for record in restored.memory_palaces["P"].all_records("episodic")] == ["first"]
    assert restored.memory_palaces["P"].rooms == ["semantic", "episodic"]


@pytest.mark.parametrize("name", ["random", "time", "uuid"])
def test_a_nondeterministic_builtin_is_recorded_and_replayed_under_another_name(name):
    source = f"let alias = {name}\nlet value = alias()\n"
    live = run(source)
    assert [event["name"] for event in events(live, "side_effect")] == [name]
    assert run(source, snapshot=live.snapshot()).global_env.get("value") == live.global_env.get("value")


GUARDED = ('memory palace "Palace" { rooms { episodic } backend sqlite bind palace }\n'
           'affective state "Mood" { baseline { valence 0.0 arousal 0.8 dominance 0.0 } bind mood }\n'
           'let box = {"value": 1}\nlet items = [1]\nlet alias = box\n'
           'fn counter() {\n    let cell = {"n": 0}\n    fn bump() { cell.n = cell.n + 1\n return cell.n }\n    return bump\n}\n'
           'let bump = counter()\nfn double(v) { return v * 2 }\n'
           'policy Gov {\n    target "Worker.process"\n    guard (args) {\n        BODY\n    }\n}\n'
           'agent Worker { model "mock" }\nsend Worker.process({"value": 1})\n')


@pytest.mark.parametrize("body, refusal", [
    ("box.value = 9", "Policy guard of Gov changed program state"),
    ("let hidden = bump()", "Policy guard of Gov changed program state"),
    ("items.append(2)", "Policy guard of Gov changed program state"),
    ("args[0].value = 7", "Policy guard of Gov changed program state"),
    ('imprint into palace.hidden { content "secret" confidence 1.0 bind mid }', "Policy guard cannot perform memory imprint"),
    ('affective event "joy" { valence 0.9 bind joy }', "Policy guard cannot perform affective operation"),
    ('somatic marker "gut" { gut_feeling 0.9 bind marker }', "Policy guard cannot perform somatic mutation"),
    ('print("inside guard")', "Policy guard cannot print"),
])
def test_a_policy_guard_that_would_change_state_refuses_the_guarded_call_and_changes_nothing(body, refusal):
    interp = Interpreter()
    with pytest.raises(PolicyCompilationError, match=refusal):
        interp.interpret(compile_to_ast(GUARDED.replace("BODY", body)))
    assert interp.global_env.get("box") == {"value": 1} and interp.global_env.get("alias") is interp.global_env.get("box")
    assert interp.global_env.get("items") == [1]
    interp.interpret(compile_to_ast("let first = bump()\n"))
    assert interp.global_env.get("first") == 1
    assert interp.mailboxes.get("Worker") in (None, [])


def test_a_policy_guard_that_only_reads_lets_the_call_through_with_no_trace_but_its_verdict():
    interp = run(GUARDED.replace("BODY", 'let seen = double(args[0].value) + box.value\nif seen > 5 {\n            reject "too big"\n        }'))
    assert [event["type"] for event in interp.execution_history][-2:] == ["policy_evaluated", "message_sent"]
    assert interp.memory_palaces["Palace"].rooms == ["episodic"]
    assert interp.global_env.get("box") == {"value": 1}
    assert interp.affective_states["Mood"].current["valence"] == 0.0


def test_program_state_sees_and_puts_back_every_reachable_change_in_place():
    from synapse.interpreter import Environment
    from synapse.program_state import ProgramState
    outer = Environment()
    inner = Environment(outer)
    shared, tags, pair_item = {"n": 1}, {"a"}, [1]
    big = 10 ** 20
    outer.define("shared", shared)
    outer.define("alias", shared)
    outer.define("tags", tags)
    outer.define("pair", (pair_item, 2))
    inner.define("big", big)
    state = ProgramState([inner])
    inner.variables["big"] = int(str(big))  # an equal number, another object: nothing changed
    assert not state.changed()
    inner.variables["big"] = big + 1  # a rebinding alone is a change
    assert state.changed()
    state.restore()
    pair_item[0] = 5  # the same length, another element
    assert state.changed()
    state.restore()
    assert pair_item == [1] and not state.changed()
    outer.variables["shared"] = {"n": 1}
    tags.add("b")
    pair_item.append(2)
    inner.define("created", 1)
    assert state.changed()
    state.restore()
    assert outer.variables["shared"] is shared and outer.variables["alias"] is shared
    assert tags == {"a"} and pair_item == [1] and "created" not in inner.variables
    assert not state.changed()


@pytest.mark.parametrize("ending, failure, message", [
    ("let bad = 1 / 0", SynapseError, "Division by zero"),
    ('print("refused")', PolicyCompilationError, "Policy guard cannot print"),
    ('reject "no"', PolicyCompilationError, "Policy guard of Gov changed program state"),
])
def test_a_guard_ending_in_any_way_after_a_change_leaves_nothing_changed(ending, failure, message):
    interp = Interpreter()
    source = ('let box = {"value": 1}\npolicy Gov { target "Worker.process"\n guard(args) {\n box.value = 9\n '
              + ending + '\n }\n}\nagent Worker { model "mock" }\nsend Worker.process("hello")\n')
    with pytest.raises(failure, match=message):
        interp.interpret(compile_to_ast(source))
    assert interp.global_env.get("box") == {"value": 1}
    assert interp.mailboxes.get("Worker") in (None, []) and interp.execution_history == []


def test_control_a_guard_that_only_read_keeps_its_own_error():
    interp = Interpreter()
    with pytest.raises(SynapseError, match="Division by zero"):
        interp.interpret(compile_to_ast('let box = {"value": 1}\npolicy Gov { target "Worker.process"\n guard(args) {\n'
                                        ' let seen = box.value\n let bad = 1 / 0\n }\n}\nagent Worker { model "mock" }\n'
                                        'send Worker.process("hello")\n'))
    assert interp.global_env.get("box") == {"value": 1}


@pytest.mark.parametrize("edit", [True, False])
def test_a_guard_reading_an_agents_memory_changes_nothing_and_replays_alike(edit):
    source = ('agent Worker { model "mock" }\nlet self = Worker\nmemory.write({"n": 1}) { reason "setup" }\n'
              'policy Gov { target "Worker.process"\n guard(args) {\n let rows = Worker.memory.read()\n'
              + (' rows[0].value.n = 9\n' if edit else ' let seen = rows[0].value.n\n') + ' }\n}\n'
              'send Worker.process("hello")\n')
    live = run(source)
    replay = Interpreter()
    replay.load_snapshot(copy.deepcopy(live.snapshot()))
    replay.interpret(compile_to_ast(source))
    for interp in (live, replay):
        assert [entry["value"] for entry in interp.global_env.get("Worker").memory.short_term] == [{"n": 1}]
    assert [message["payload"] for message in live.mailboxes["Worker"]] == ["hello"]
