"""Acceptance: a value that crosses an ownership boundary is the receiver's own (review F4, F1).

The record of what happened never shares a list or a dict with the program
going on: a message delivered, its send and receive records and the policy
verdict on it; the event an observer is handed; the record of a VM run and the
result bound to the program; what a memory keeps and what reading it returns.
A later change on one side rewrites nothing on the other.
"""
import copy

import pytest

from synapse import Interpreter, Memory, compile_to_ast
from acceptance.runtime.test_vm_host_calls_contract import DECLARED_PUBLIC, FREE_TIER, gemini

WORKER = 'agent Worker { model "mock" }\n'
READING_POLICY = 'policy Gov { target "Worker.process" guard(args) { let seen = args[0].n } }\n'


def run(source, interp=None, setup=None):
    interp = interp or Interpreter()
    if setup is not None:
        setup(interp)
    interp.interpret(compile_to_ast(source))
    return interp


def records(interp, kind):
    return [event for event in interp.execution_history if event["type"] == kind]


@pytest.mark.parametrize("policy", [False, True])
def test_the_senders_later_edit_rewrites_neither_the_message_nor_its_records(policy):
    interp = run(WORKER + (READING_POLICY if policy else "") + 'let payload = {"n": 1}\nsend Worker.process(payload)\n')
    before = copy.deepcopy(interp.execution_history)
    run("payload.n = 9\n", interp)
    assert interp.execution_history == before
    assert interp.mailboxes["Worker"][0]["args"][0] == {"n": 1}
    assert interp.global_env.get("payload") == {"n": 9}


def test_the_receivers_edit_rewrites_neither_the_send_nor_the_receive_record():
    source = (WORKER + 'let payload = {"n": 1}\nsend Worker.process(payload)\nlet self = Worker\n'
              'receive { sender => msg { msg.payload.n = 9 } }\n')
    live = run(source)
    replay = Interpreter()
    replay.load_snapshot(copy.deepcopy(live.snapshot()))
    run(source, replay)
    for interp in (live, replay):
        assert [event["message"]["payload"] for event in records(interp, "message_sent") + records(interp, "message_received")] \
            == [{"n": 1}, {"n": 1}]
    assert live.global_env.get("payload") == {"n": 1}


def test_an_observer_is_handed_its_own_copy_of_the_event():
    interp = run(WORKER + 'observe Worker.process {\n    on message_sent => evt {\n        evt.message.payload.n = 9\n    }\n}\n'
                 'send Worker.process({"n": 1})\n')
    assert records(interp, "message_sent")[0]["message"]["payload"] == {"n": 1}
    assert interp.mailboxes["Worker"][0]["payload"] == {"n": 1}


@pytest.mark.parametrize("model", [False, True])
def test_the_program_value_does_not_rewrite_the_vm_execution_record(model):
    prompts = []

    def setup(interp):
        interp.global_env.define("src", 'let answer = llm "question"\n' if model else "let answer = 4\n")
        if model:
            interp.llm_backend = gemini({**FREE_TIER, **DECLARED_PUBLIC}, prompts)

    interp = run("compile vm { source src bind code }\nrun vm { source code }\n", setup=setup)
    record = records(interp, "vm_executed")[-1]
    before = copy.deepcopy(record)
    run('vm_result.locals.answer.text = "edited"\n' if model else "vm_result.locals.answer = 99\n", interp)
    assert record == before
    if model:
        assert prompts == ["question"]
        assert [event["result"]["text"] for event in records(interp, "LLM_RESPONSE_CACHED")] == ["four"]


def test_a_memory_keeps_what_was_written_and_hands_out_copies():
    interp = run(WORKER + 'let self = Worker\nlet value = {"n": 1}\nmemory.write(value) { reason "setup" }\n'
                 'value.n = 9\nlet rows = memory.read()\nrows[0].value.n = 7\nlet found = memory.recall("n")\n'
                 'found[0].value.n = 5\nlet again = memory.read()\n')
    assert interp.global_env.get("again")[0]["value"] == {"n": 1}
    assert interp.global_env.get("Worker").memory.short_term[0]["value"] == {"n": 1}
    assert interp.memory_audit[0]["value"] == {"n": 1}


def test_a_memory_restored_from_data_shares_nothing_with_that_data():
    data = {"short_term": [{"value": {"n": 1}}], "long_term": {"k": {"n": 2}}, "capacity": 5}
    before = copy.deepcopy(data)
    memory = Memory.from_dict(data)
    memory.write({"n": 3})
    memory.write({"n": 4}, key="k2")
    published = memory.to_dict()
    published["short_term"][0]["value"]["n"] = 9
    assert data == before
    assert memory.read()[0]["value"] == {"n": 1}


def test_a_mobility_envelope_does_not_change_as_the_interpreter_goes_on():
    interp = run(WORKER + 'let payload = {"n": 1}\nsend Worker.process(payload)\n')
    envelope = interp.dump_state()
    before = copy.deepcopy(envelope)
    run('payload.n = 9\nsend Worker.process("second")\n', interp)
    assert envelope == before


def test_control_records_nobody_changes_stay_as_they_were():
    interp = run(WORKER + READING_POLICY + 'let payload = {"n": 1}\nsend Worker.process(payload)\n')
    before = copy.deepcopy(interp.execution_history)
    run("let unrelated = 1\n", interp)
    assert interp.execution_history == before
    assert [event["type"] for event in interp.execution_history] == ["policy_evaluated", "message_sent"]
