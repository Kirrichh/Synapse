"""Acceptance: a replay follows its record to the end, then the run is live (review F6).

An ordinary replay (``load_snapshot``) re-executes the program and serves each
recorded operation from the record: a recorded send is not delivered again, a
recorded verdict is not evaluated again. A record the program produces again —
a segment entered, a VM run, an affective state, a palace imprint — takes the
place the run recorded for it, never a second one. Once the whole record is
consumed the run continues LIVE, so an operation after it acts exactly once —
whatever the record ended with: an effect, an audit event, or nothing at all —
and an operation past the end of the record inside one statement is new too.
A reaction the run recorded (an affective threshold, a habit) is not run again;
the next operation passes over its record. Before its end the record is never
left.
"""
import copy
import json

import pytest

from synapse import Interpreter, compile_to_ast
from synapse.interpreter import PolicyViolationException, ReplayIntegrityError, RuntimeMode
from synapse.memory_points import DURABLE_COGNITIVE_PROFILE

WORKER = 'agent Worker { model "mock" }\n'


def live(source, setup=lambda interp: None):
    interp = Interpreter()
    interp.source_code = source
    setup(interp)
    interp.interpret(compile_to_ast(source))
    return interp


def replayed(original, source, verified=False, saved=lambda snapshot: json.loads(json.dumps(snapshot)),
             setup=lambda interp: None):
    """A fresh interpreter that replayed ``source`` from the snapshot of ``original``, saved as JSON."""
    interp = Interpreter()
    if verified:
        interp.durable_profile = DURABLE_COGNITIVE_PROFILE
    interp.load_snapshot(saved(original.snapshot()))
    setup(interp)
    interp.interpret(compile_to_ast(source))
    return interp


def sent(interp):
    return [event["message"]["payload"] for event in interp.execution_history if event["type"] == "message_sent"]


@pytest.mark.parametrize("ending, source", [
    ("an effect", "let saved = uuid()\n"),
    ("an audit event", WORKER + 'let saved = uuid()\nsend Worker.process("earlier")\n'),
    ("a transaction", 'let saved = uuid()\nlet x = 1\nintegrate x {\n    x = 2\n} on fail rollback\n'),
    ("nothing", "let saved = 1\n"),
])
def test_once_the_record_is_consumed_a_new_send_is_delivered_exactly_once(ending, source):
    original = live(source)
    replay = replayed(original, source)
    assert replay.runtime_mode == RuntimeMode.LIVE, ending
    assert replay.global_env.get("saved") == original.global_env.get("saved")
    recorded = len(replay.execution_history)
    replay.interpret(compile_to_ast(WORKER + 'send Worker.process("new work")\n'))
    assert [message["payload"] for message in replay.mailboxes["Worker"]][-1] == "new work"
    assert sent(replay).count("new work") == 1
    assert len(replay.execution_history) == recorded + 1  # the new send's record, once


def test_a_recorded_send_is_not_delivered_again_and_the_next_one_is():
    source = WORKER + 'send Worker.process("first")\n'
    original = live(source)
    replay = replayed(original, source)
    assert [message["payload"] for message in replay.mailboxes["Worker"]] == ["first"]
    assert sent(replay) == ["first"]
    replay.interpret(compile_to_ast('send Worker.process("second")\n'))
    assert [message["payload"] for message in replay.mailboxes["Worker"]] == ["first", "second"]
    assert sent(replay) == ["first", "second"]


def coroutine(interp, source):
    flow = interp.interpret_async(compile_to_ast(source))
    with pytest.raises(StopIteration):
        next(flow)
    return interp


@pytest.mark.parametrize("body", ["", "intent and forget"])
def test_a_replay_driven_as_a_coroutine_ends_with_its_record_too(body):
    source = (PROGRAMS[body][0] if body else "") + "let saved = uuid()\n"
    original = coroutine(Interpreter(), source)
    replay = Interpreter()
    replay.load_snapshot(json.loads(json.dumps(original.snapshot())))
    coroutine(replay, source)
    assert replay.runtime_mode == RuntimeMode.LIVE
    assert replay.global_env.get("saved") == original.global_env.get("saved")
    assert replay.execution_history == original.execution_history


def test_before_its_end_the_record_is_never_left():
    source = "let first = uuid()\nlet second = uuid()\n"
    original = live(source)
    replay = Interpreter()
    replay.load_snapshot(json.loads(json.dumps(original.snapshot())))
    replay.interpret(compile_to_ast("let first = uuid()\n"))
    assert replay.runtime_mode == RuntimeMode.REPLAY  # one recorded draw is still ahead
    replay.interpret(compile_to_ast("let second = uuid()\n"))
    assert replay.runtime_mode == RuntimeMode.LIVE
    assert [replay.global_env.get(name) for name in ("first", "second")] == \
        [original.global_env.get(name) for name in ("first", "second")]
    assert len(replay.execution_history) == 2


POLICY = ('policy Gov {\n    target "Worker.process"\n    guard (args) {\n'
          '        if args[0] == "bad" {\n            reject "refused"\n        }\n    }\n}\n')


def test_a_verdict_is_read_from_the_record_and_a_new_call_is_judged_again():
    source = POLICY + WORKER + 'send Worker.process("ok")\n'
    original = live(source)
    replay = replayed(original, source, saved=copy.deepcopy)  # a policy holds its guard's syntax tree, not JSON
    assert [event["type"] for event in replay.execution_history] == ["policy_evaluated", "message_sent"]
    with pytest.raises(PolicyViolationException, match="refused"):
        replay.interpret(compile_to_ast('send Worker.process("bad")\n'))
    assert replay.execution_history[-1]["type"] == "policy_violation"
    assert [message["payload"] for message in replay.mailboxes["Worker"]] == ["ok"]


def test_a_replay_saved_and_replayed_again_continues_alike():
    source = WORKER + 'let saved = uuid()\nsend Worker.process("first")\n'
    once = replayed(live(source), source)
    once.interpret(compile_to_ast('send Worker.process("second")\n'))
    continued = source + 'send Worker.process("second")\n'
    twice = replayed(once, continued)
    assert twice.runtime_mode == RuntimeMode.LIVE
    assert sent(twice) == ["first", "second"]
    assert [message["payload"] for message in twice.mailboxes["Worker"]] == ["first", "second"]
    assert twice.global_env.get("saved") == once.global_env.get("saved")


def test_a_replayed_send_the_record_holds_no_message_for_is_refused():
    original = live("let saved = uuid()\n")
    with pytest.raises(ReplayIntegrityError, match="holds no message"):
        replayed(original, WORKER + 'send Worker.process("unrecorded")\nlet saved = uuid()\n')


def test_control_a_verified_replay_also_continues_live_at_the_end_of_its_record():
    source = "let saved = uuid()\n"
    replay = replayed(live(source), source, verified=True)
    assert replay.runtime_mode == RuntimeMode.LIVE
    replay.interpret(compile_to_ast(WORKER + 'send Worker.process("new work")\n'))
    assert sent(replay) == ["new work"]


def test_a_verdict_past_the_end_of_the_record_inside_a_statement_is_judged():
    work = (POLICY + WORKER + 'fn work(first, second) {\n    send Worker.process(first)\n    if second != "" {\n'
            '        send Worker.process(second)\n    }\n}\n')
    original = live(work + 'work("ok", "")\n')
    replay = Interpreter()
    replay.load_snapshot(copy.deepcopy(original.snapshot()))
    with pytest.raises(PolicyViolationException, match="refused"):
        replay.interpret(compile_to_ast(work + 'work("ok", "bad")\n'))
    assert [message["payload"] for message in replay.mailboxes["Worker"]] == ["ok"]
    assert [event["type"] for event in replay.execution_history] == ["policy_evaluated", "message_sent",
                                                                      "policy_violation"]


def inner_segment(interp):
    interp.global_env.define("src", 'context "inner" {\n  let t = time()\n}\n')


PROGRAMS = {
    "segment": ('context "outer" {\n    let inside = uuid()\n}\n', None, None),
    "vm": ("compile vm { source src bind code }\nrun vm { source code }\n", inner_segment, None),
    "affect": ('affective state "Mood" {\n    baseline { valence 0.1 arousal 0.2 dominance 0.5 }\n    bind mood\n}\n'
               'affective event "calm" { valence 0.1 bind tag }\naffective modulation {\n    bind rules\n}\n'
               'somatic marker "decision" {\n    threshold 0.4\n    bind marker\n}\n', None, None),
    "palace": ('memory palace "Notes" {\n    rooms { episodic }\n    bind palace\n}\n'
               'imprint into palace.episodic {\n    content "the migration failed"\n    confidence 0.9\n'
               '    source "ops"\n    bind imprint_id\n}\n'
               'recall from palace.episodic {\n    query "migration"\n    limit 3\n    bind found\n}\n', None, None),
    # Recalled before its tag expires, then twice after: the expiry is reported once, at the place it was.
    "affective expiry": ('memory palace "Pains" {\n    rooms { episodic }\n    bind palace\n}\n'
                         'affective state "Mood" {\n    baseline { valence 0.0 arousal 0.0 dominance 0.0 }\n'
                         '    bind mood\n}\naffective event "fail" {\n    valence -0.9\n    arousal 0.8\n'
                         '    bind fail_tag\n}\nimprint into palace.episodic {\n    content "the migration failed"\n'
                         '    confidence 0.9\n    source "ops"\n    affective_tag fail_tag\n    affective_decay 2 events\n'
                         '    bind imprint_id\n}\n' + 3 * ('recall from palace.episodic {\n    query "migration"\n'
                                                        '    limit 3\n    bind found\n}\nlet drawn = uuid()\n'),
                         None, None),
    "intention": ('intention cascade "Deploy" {\n    mission "keep the service up"\n    task "back up"\n'
                  '    bind plan\n}\nplan weave with [self] under "Shared" {\n    intention plan\n'
                  '    checkpoint every 2 steps\n    timeout 60\n    bind execution\n}\n', None, None),
    "vm checkpoint": ('compile vm {\n    source "let x = 1"\n    bind code\n}\nrun vm {\n    source code\n    gas 100\n'
                      '    checkpoint "after_init" at_ip 1\n    bind partial\n}\nrun vm {\n    resume_from "after_init"\n'
                      '    gas 100\n    bind final\n}\n', None, None),
    "evolution deferred": ('policy Pace {\n    target "evolve.Guide"\n    cooldown: 5\n}\nagent Guide {\n    model "mock"\n'
                           '    soulprint {\n        values: [ integrity: 1.0 ]\n    }\n}\nlet self = Guide\n'
                           'evolve self when true under Pace {\n    let first = 1\n}\n'
                           'evolve self when true under Pace {\n    let second = 2\n}\n', None, copy.deepcopy),
    "collective": ('agent Guide { model "mock" }\nagent Peer { model "mock" }\npolicy Shared {\n'
                   '    target "collective.*"\n    collective_dream: true\n    swarm_fracture: true\n}\nlet self = Guide\n'
                   'collective dream with [Peer] under "Shared" {\n    scenario "resource conflict"\n'
                   '    converge_on "protocol_v2"\n    timeout 30\n    bind shared\n}\n'
                   'swarm fracture with [Peer] under "Shared" {\n    scenario "recovery"\n    roles {\n'
                   '        Peer -> Analyst\n    }\n    consensus unanimous\n    timeout 30\n    bind swarm_result\n}\n', None, copy.deepcopy),
    "resonance": ('resonate with @user {\n    aspects ["emotional_tone"]\n    window 20\n    bind profile\n}\n', None, None),
    "habit": ('habit "Tidy" from pattern {\n    frequency > 3\n    stability > 0.9\n    energy_cost 0.2\n'
              '    bind habit_id\n}\n', None, None),
    "intent and forget": ('agent Keeper { model "mock" }\nintent tidy {\n    action "forget the note"\n'
                          '    target "notes"\n    reversible true\n}\nlet self = Keeper\n'
                          'memory.write("note") { reason "setup" }\ndeclare intent tidy\n'
                          'memory.forget("note") { reason "cleanup" }\n', None, None),
}


@pytest.mark.parametrize("name", list(PROGRAMS))
def test_the_programs_own_records_take_their_places_and_the_replay_ends_with_them(name):
    body, setup, saved = PROGRAMS[name]  # a policy holds its guard's syntax tree, not JSON: such a run is copied
    setup = setup or (lambda interp: None)
    source = body + "let saved = uuid()\n"
    original = live(source, setup)
    replay = replayed(original, source, setup=setup, saved=saved or (lambda snapshot: json.loads(json.dumps(snapshot))))
    assert replay.execution_history == original.execution_history  # nothing recorded twice
    assert replay.runtime_mode == RuntimeMode.LIVE
    assert replay.global_env.get("saved") == original.global_env.get("saved")
    replay.interpret(compile_to_ast(WORKER + 'send Worker.process("new work")\n'))
    assert sent(replay) == ["new work"]


def test_a_vm_checkpoint_record_names_the_trigger_that_saved_it():
    # The trigger is part of the record before it is recorded: a replay takes the record as it is, never edits it.
    source = PROGRAMS["vm checkpoint"][0]
    original = live(source)
    replay = replayed(original, source)
    for interp in (original, replay):
        saved, = [event for event in interp.execution_history if event["type"] == "vm_checkpoint_saved"]
        assert saved["trigger"] == {"kind": "at_ip", "value": 1}


def failing_segment(interp):
    interp.global_env.define("src", 'context "inner" {\n  assert false, "boom"\n}\n')


def test_a_vm_run_failing_inside_its_segment_is_replayed_as_the_same_failure_recorded_once():
    source = "compile vm { source src bind code }\nrun vm { source code bind result }\n"
    original = Interpreter()
    failing_segment(original)
    with pytest.raises(Exception, match="boom") as failed:
        original.interpret(compile_to_ast(source))
    replay = Interpreter()
    replay.load_snapshot(copy.deepcopy(original.snapshot()))
    failing_segment(replay)
    with pytest.raises(type(failed.value), match="boom"):
        replay.interpret(compile_to_ast(source))
    assert [event["type"] for event in original.execution_history] == ["vm_bytecode_compiled", "context_entered",
                                                                        "context_exited"]
    assert replay.execution_history == original.execution_history


REACTING = ('affective state "Mood" {\n    baseline { valence -0.5 arousal 0.8 dominance 0.3 }\n    bind mood\n}\n'
            'affective threshold "HighStress" {\n    when arousal > 0.7\n    for 1 events\n'
            '    action {\n        suspend emergency_pause("high_stress")\n    }\n}\n'
            + WORKER + 'send Worker.process("first")\nlet self = Worker\nreceive { sender => msg { let got = msg } }\n'
            'affective event "stress" { arousal 0.0 bind tag }\n')


def test_a_reaction_the_run_recorded_is_not_run_again_and_the_next_operation_acts_once():
    original = live(REACTING)
    assert [event for event in original.execution_history if event["type"] == "threshold_suspend_requested"]
    replay = replayed(original, REACTING)
    assert replay.execution_history == original.execution_history  # no reaction ran a second time
    replay.interpret(compile_to_ast('send Worker.process("new work")\n'))
    assert sent(replay) == ["first", "new work"]
    assert replay.runtime_mode == RuntimeMode.LIVE

