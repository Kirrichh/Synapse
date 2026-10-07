"""Acceptance: the runtime owns its state, and what it records stays what happened.

A program's binding, a history event and the live state never share one
object; declared values are checked; a checkpoint keeps its moment; the energy
pool is read live and changed only by the runtime; a palace snapshot carries
its records; a nondeterministic builtin is recorded under any name; a policy
guard reads and changes nothing.
"""
import math

import pytest

from synapse import Interpreter, compile_to_ast
from synapse.affective import AffectiveState, clamp
from synapse.interpreter import PolicyCompilationError


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
           'let box = {"value": 1}\nlet items = [1]\n'
           'policy Gov {\n    target "Worker.process"\n    guard (args) {\n        BODY\n    }\n}\n'
           'agent Worker { model "mock" }\nsend Worker.process({"value": 1})\n')


@pytest.mark.parametrize("body, refusal", [
    ("box.value = 9", "Policy guard of Gov changed program state"),
    ("items.append(2)", "Policy guard of Gov changed program state"),
    ("args[0].value = 7", "Policy guard of Gov changed program state"),
    ('imprint into palace.hidden { content "secret" confidence 1.0 bind mid }', "Policy guard cannot perform memory imprint"),
    ('affective event "joy" { valence 0.9 bind joy }', "Policy guard cannot perform affective operation"),
    ('somatic marker "gut" { gut_feeling 0.9 bind marker }', "Policy guard cannot perform somatic mutation"),
    ('print("inside guard")', "Policy guard cannot print"),
])
def test_a_policy_guard_that_would_change_state_refuses_the_guarded_call(body, refusal):
    with pytest.raises(PolicyCompilationError, match=refusal):
        run(GUARDED.replace("BODY", body))


def test_a_policy_guard_that_only_reads_lets_the_call_through_with_no_trace_but_its_verdict():
    interp = run(GUARDED.replace("BODY", 'let seen = args[0].value + box.value\nif seen > 5 {\n            reject "too big"\n        }'))
    assert [event["type"] for event in interp.execution_history][-2:] == ["policy_evaluated", "message_sent"]
    assert interp.memory_palaces["Palace"].rooms == ["episodic"]
    assert interp.global_env.get("box") == {"value": 1}
    assert interp.affective_states["Mood"].current["valence"] == 0.0
