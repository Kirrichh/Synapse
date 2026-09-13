"""Changing actual operations differs from changing proposal provenance."""
from dataclasses import replace

from acceptance.stage4.stage10._builders import hash_ref, plan_world
from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.contracts import ActorIdentity
from synapse.experiments.gold.stage10.intent import propose_intent
from synapse.experiments.gold.stage10.planning import (
    OperationKind, OperationRecord, propose_operation_plan, plan_semantic_sha256,
)


def test_plan_semantics_bind_edges_inputs_and_operations_not_proposal_names():
    original, plan, _, authority, _, _ = plan_world()
    fields = authority.task_contract.intent_fields()
    fields['required_capabilities'] = ('repository.edit', 'repository.read')
    intent = propose_intent(**fields, task_contract_ref=original.task_contract_ref,
        proposer=original.proposer, source_actors=original.source_actors,
        knowledge_snapshot_ref=original.knowledge_snapshot_ref)
    inspect = OperationRecord('a-read', OperationKind.INSPECT_READ, plan.operations[0].subject_paths,
        (), (), (), 'repository.read', None)
    edit = replace(plan.operations[0], operation_id='b-edit', depends_on=('a-read',))

    def candidate(operations, current_intent=intent, proposer=plan.proposer):
        return propose_operation_plan(intent=current_intent, proposer=proposer,
            source_actors=plan.source_actors, allowed_scope=plan.allowed_scope,
            capability_profile=tuple(sorted({item.capability for item in operations})), operations=operations)

    def digest(value, current_intent=intent):
        return plan_semantic_sha256(value, intent=current_intent, policy_version='policy/v1')

    first = candidate((inspect, edit))
    expected = digest(first)
    assert digest(candidate((inspect, replace(edit, depends_on=())))) != expected
    assert digest(candidate((replace(inspect, input_refs=(hash_ref(RefKind.ARTIFACT, 'source'),)), edit))) != expected
    assert digest(candidate((replace(edit, depends_on=()),))) != expected
    renamed = (replace(inspect, operation_id='inspect-again'),
               replace(edit, operation_id='edit-again', depends_on=('inspect-again',)))
    assert digest(candidate(renamed)) == expected
    assert digest(candidate((inspect, edit), proposer=ActorIdentity('another-proposer'))) == expected
    second_intent = propose_intent(**fields, task_contract_ref=original.task_contract_ref,
        proposer=original.proposer, source_actors=original.source_actors,
        knowledge_snapshot_ref=hash_ref(RefKind.KNOWLEDGE_SNAPSHOT, 'new-snapshot'))
    assert digest(candidate((inspect, edit), current_intent=second_intent), second_intent) == expected
