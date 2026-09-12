"""A literal CVM sample, not acceptance of procedural execution by Gold."""

from copy import deepcopy

from synapse.experiments.gold.behavior import compile_behavior_unit
from synapse.experiments.gold.contracts import AttemptId, RepositoryRevision, RunId
from synapse.experiments.gold.replay import replay_machine_execution_context
from synapse.experiments.gold.replay_vm_adapter import CognitiveVMReplayAdapter
from acceptance.stage4.stage13.source_procedure import source_procedure, source_procedure_program, procedure_return_value
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


def test_literal_sample_preserves_parameters_in_the_real_vm_without_host_effects():
    procedure = _procedure()
    program = _program(procedure)
    machine = CognitiveVMReplayAdapter(program, gas_budget=10000, execution_context=_context())
    observed = []
    while not machine.is_halted():
        machine.step()
        observed.append(machine.transition_hash())
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
    assert len(observed) > 8
    changed = deepcopy(procedure)
    changed['operations'][1]['command'][2] += '; different'
    assert procedure_return_value(changed) != procedure_return_value(procedure)
    assert _program(changed).program_hash != program.program_hash
