"""Closed procedural data is executed by CVM, never by an alternate interpreter."""

from copy import deepcopy

import pytest

from synapse.bytecode import Instruction
from synapse.experiments.gold.behavior import compile_behavior_unit
from synapse.experiments.gold.contracts import AttemptId, RepositoryRevision, RunId
from synapse.experiments.gold.replay import ReplayViolation, replay_machine_execution_context
from synapse.experiments.gold.replay_vm_adapter import CognitiveVMReplayAdapter, certify_data_return_transitions
from synapse.experiments.gold.source_procedure import source_procedure, source_procedure_program, procedure_return_value
from synapse.experiments.gold.source_verification import SOURCE_KNOWLEDGE_V1, SOURCE_FILE_V1, canonical, source_ref
from tests.test_stage4_gold_behavior import make_valid_unit, _unit_from_core


def _procedure():
    return source_procedure({'schema_version': SOURCE_KNOWLEDGE_V1, 'kind': 'VERIFICATION_RECIPE',
        'revision': 'a' * 40, 'sources': [{'path': 'src/расчёт.py',
            'ref': source_ref(b'def add(a, b): return a + b\n', SOURCE_FILE_V1).to_dict()}],
        'bindings': [], 'recipe': {'command': ['python', '-c', 'print("данные; $(not-a-shell)")'],
            'expectation': {'expected_exit_codes': [0], 'expected_nonzero_exit': False,
                'combined_output_contains': ['данные'], 'combined_output_not_contains': [], 'timeout_seconds': 10}}})


def _program(procedure):
    core = make_valid_unit().core.to_dict()
    core['canonical_program'] = source_procedure_program(procedure).to_dict()
    return compile_behavior_unit(_unit_from_core(core)).program


def _context():
    return replay_machine_execution_context(run_id=RunId('procedure-certificate'), attempt_id=AttemptId('1'),
        repository_revision=RepositoryRevision.git_commit('a' * 40), environment_profile_id='pure', policy_version='policy/v1')


def test_procedural_return_preserves_parameters_and_has_no_host_effects():
    procedure = _procedure()
    program = _program(procedure)
    transitions = certify_data_return_transitions(program, gas_budget=10000, execution_context=_context())
    machine = CognitiveVMReplayAdapter(program, gas_budget=10000, execution_context=_context())
    observed = []
    while not machine.is_halted():
        machine.step()
        observed.append(machine.transition_hash())
    assert tuple(observed) == transitions
    assert machine.machine_snapshot()['state']['stack'] == [procedure_return_value(procedure)]
    actual = machine.machine_snapshot()['state']['stack'][0]
    assert procedure['knowledge_ref'] == source_ref(canonical(procedure['knowledge']), SOURCE_KNOWLEDGE_V1).to_dict()
    def decoded_text(words):
        length, *chunks = words
        return b''.join(word.to_bytes(min(6, length - index * 6), 'big')
                        for index, word in enumerate(chunks)).decode('utf-8')
    read, command = actual[3]
    assert actual[0] == 1 and read[0] == 1 and command[0] == 2
    assert decoded_text(read[1]) == 'src/расчёт.py'
    assert [decoded_text(item) for item in command[1]] == procedure['operations'][1]['command']
    assert command[2] == [0] and command[3] is False and command[6] == 10
    assert [decoded_text(item) for item in command[4]] == ['данные'] and command[5] == []
    assert machine.machine_snapshot()['state']['pending_host_call'] is None
    assert len(transitions) > 8  # The result carries operation parameters, not only a digest.
    changed = deepcopy(procedure)
    changed['operations'][1]['command'][2] += '; different'
    assert procedure_return_value(changed) != procedure_return_value(procedure)
    assert _program(changed).program_hash != program.program_hash
    assert certify_data_return_transitions(program, gas_budget=9999, execution_context=_context()) != transitions


@pytest.mark.parametrize('instruction', [
    Instruction('CALL_HOST', 'print', 0), Instruction('JUMP', 0),
    Instruction('BUILD_LIST', 100000), Instruction('LOAD_CONST', -1),
    Instruction('RETURN'), Instruction('LOAD_FALSE', 0),
])
def test_certificate_rejects_hidden_execution_and_malformed_stack(instruction):
    program = deepcopy(_program(_procedure()))
    program.instructions[0] = instruction
    with pytest.raises(ReplayViolation):
        certify_data_return_transitions(program, gas_budget=10000, execution_context=_context())


def test_certificate_does_not_publish_a_partial_gas_exhausted_return():
    with pytest.raises(ReplayViolation):
        certify_data_return_transitions(_program(_procedure()), gas_budget=1, execution_context=_context())
