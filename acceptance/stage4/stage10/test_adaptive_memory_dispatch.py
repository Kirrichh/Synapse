"""Real Stage 10 dispatch, delivery and influence for adaptive memory."""
from dataclasses import replace

import pytest

from acceptance.stage4.stage10._builders import hash_ref, plan_world
from acceptance.stage4.stage10.test_local_edit_contract import encoded
from synapse.canonical_values import canonical_json_bytes
from synapse.experiments.gold import point_of_use as P
from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.persistence import store_transaction
from synapse.experiments.gold.stage10.context import build_worker_context
from synapse.experiments.gold.stage10.context_codec import SOURCE_DELIVERY_PROFILE_V2, encode_canonical
from synapse.experiments.gold.stage10.delivery_verification import decode_delivery_receipt
from synapse.experiments.gold.stage10.influence import ConsumptionStage, observe_local_context_influence
from synapse.experiments.gold.stage10.plan_revalidation import CurrentPlanState, authorize_first_side_effect
from synapse.experiments.gold.stage10.worker_context_adapter import Stage10WorkerContextAdapter
from synapse.experiments.gold.source_delivery import _adaptive_memory_items
from synapse.experiments.gold.stage10.worker_transport import (
    SYNAPSE_ADAPTIVE_MEMORY_TRANSPORT,
    WorkerCandidateStatus,
    WorkerDeliveryStatus,
)
from synapse.worker.input_contract import LocalInformationInput, WorkerTaskInput
from synapse.worker.local_edits import (
    LOCAL_EDIT_PROFILE_V5,
    LOCAL_EDIT_PROPOSAL_V1,
    propose_local_edits,
)


OLD_SOURCE = "# header\nvalue = old\n"
CURRENT_SOURCE = "# later revision\n# header\nvalue = old\n"
OLD_REVISION = "b" * 40


class TransportCalled(RuntimeError):
    pass


class TransportSpy:
    def __init__(self):
        self.calls = 0

    def run(self, *args, **kwargs):
        self.calls += 1
        raise TransportCalled("ordinary agent transport was invoked")


def _source_item(task, source):
    return {"role": "REFERENCE", "media_type": "application/json",
        "content_base64url": encoded(canonical_json_bytes({
            "repository_revision": task["repository_revision"], "status": "REJECTED",
            "applicability": "UNASSESSED", "sources": [{
                "path": task["effects"][0]["subject_path"],
                "content_base64url": encoded(source.encode()),
            }]}))}


def _information(items):
    return LocalInformationInput(canonical_json_bytes({
        "schema_version": "synapse.worker.local-information-input/v1", "items": items}))


def _memory_information(current_task, *, source=CURRENT_SOURCE):
    path = current_task["effects"][0]["subject_path"]
    old_task = replace_task_revision(current_task, OLD_REVISION)
    old_information = _information([_source_item(old_task, OLD_SOURCE)])
    old_patch = propose_local_edits(
        task=WorkerTaskInput(canonical_json_bytes(old_task)), information=old_information,
        proposal={"schema_version": LOCAL_EDIT_PROPOSAL_V1, "alternatives": [{"edits": [{
            "path": path, "old": "value = old", "new": "value = new"}]}]},
    )["diff_text"]
    memory_items = [
        {"role": "REFERENCE", "media_type": "application/json",
         "content_base64url": encoded(canonical_json_bytes({
             "repository_revision": current_task["repository_revision"],
             "procedural_memory": {
                 "profile": "exact-verified-patch-memory/v1",
                 "generalization": "NOT_ESTABLISHED",
                 "patches": [],
             }}))},
        *_adaptive_memory_items({"court": {"adaptive_patches": [{
            "repository_revision": OLD_REVISION,
            "patch_sha256": __import__("hashlib").sha256(old_patch.encode()).hexdigest(),
            "diff_text": old_patch,
        }]}}),
    ]
    return _information([_source_item(current_task, source), *memory_items])


def replace_task_revision(task, revision):
    return {**task, "repository_revision": revision}


def _dispatch(stage10_delivery_world, *, source=CURRENT_SOURCE):
    world = stage10_delivery_world
    previous = world.context
    current_task = WorkerTaskInput(previous.delivery_envelope.prompt_text.encode()).to_dict()
    information = _memory_information(current_task, source=source)
    snapshot = hash_ref(RefKind.SOURCE_EVIDENCE, "adaptive-memory-source",
                        schema="synapse.stage4.gold.source-experience-snapshot/v1")
    experience = {"snapshot_ref": snapshot.to_dict(), "projection_profile": SOURCE_DELIVERY_PROFILE_V2,
                  "information": information.to_dict()}
    context = build_worker_context(
        intent=previous.intent, accepted_plan=previous.accepted_plan, attempt_id=previous.attempt_id,
        admitted_knowledge=previous.admitted_knowledge, knowledge_selection=previous.knowledge_selection,
        knowledge_items=previous.knowledge_items, replay_observations=previous.replay_observations,
        excluded_refs=previous.excluded_refs, source_experience_bytes=encode_canonical(experience))
    with store_transaction(world.store_fence) as ticket:
        persistence = world.store.persist_worker_context(context, ticket=ticket)
    current = CurrentPlanState(
        repository_revision_sha256=context.intent.repository_revision_sha256,
        knowledge_snapshot_ref=context.intent.knowledge_snapshot_ref,
        policy_sha256=world.authorization.policy_sha256,
        admitted_knowledge=context.admitted_knowledge,
        compatibility_revalidation=world.case.compatibility_probe.records[-1])
    authority = plan_world(snapshot_ref=world.case.boundary.manifest_ref,
                           behavior_refs=world.case.subjects)[3]
    authorization = authorize_first_side_effect(
        accepted_plan=context.accepted_plan, intent=context.intent, authority=authority,
        attempt_id=context.attempt_id, current_state_reader=lambda: current,
        admission_freshness_validator=lambda value: P.require_current_point_of_use_evidence(
            value, binding=world.case.binding),
        context_id=context.context_id, context_audit_sha256=context.audit_sha256,
        delivery_envelope_sha256=context.delivery_envelope.envelope_sha256,
        plan_bundle_sha256=world.plan_persistence.bundle_sha256)
    spy = TransportSpy()
    adapter = Stage10WorkerContextAdapter(spy, local_edit_profile=LOCAL_EDIT_PROFILE_V5,
                                          memory_profile=LOCAL_EDIT_PROFILE_V5)
    return adapter, spy, dict(worktree_path=world.worker_worktree, context=context, persistence=persistence,
                              plan_persistence=world.plan_persistence, authorization=authorization)


def test_real_dispatch_uses_adapt_before_agent_and_replays_delivery_and_influence(stage10_delivery_world):
    adapter, spy, arguments = _dispatch(stage10_delivery_world)
    dispatched = adapter.dispatch(**arguments)
    result = dispatched.worker_result
    assert spy.calls == 0
    assert result.status is WorkerCandidateStatus.PROPOSED_PATCH
    assert result.diagnostics["local_edit_result"]["planning_route"] == "ADAPT_MEMORY"
    assert result.diagnostics["local_edit_result"]["candidates"][0]["prior_outcome"] is None
    assert result.usage.input_tokens == result.usage.output_tokens == result.usage.total_tokens == 0
    evidence = result.delivery_evidence
    assert evidence.status is WorkerDeliveryStatus.SYNAPSE_ADAPTIVE_MEMORY
    assert evidence.transport_name == SYNAPSE_ADAPTIVE_MEMORY_TRANSPORT
    receipt = dispatched.delivery_receipt
    assert receipt.delivery_status is evidence.status
    assert receipt.transport_name == evidence.transport_name
    assert decode_delivery_receipt(receipt.canonical_bytes()).canonical_bytes() == receipt.canonical_bytes()
    influence = observe_local_context_influence(receipt=receipt, invocation=dispatched.invocation,
                                                worker_result=result)
    assert influence is not None
    assert influence.payload(receipt)["worker_transport_name"] == SYNAPSE_ADAPTIVE_MEMORY_TRANSPORT
    assert any(item["selection_changed"] for item in influence.payload(receipt)["comparisons"])


def test_real_dispatch_falls_back_to_agent_when_adapt_target_is_stale(stage10_delivery_world):
    adapter, spy, arguments = _dispatch(stage10_delivery_world, source="# changed\nvalue = different\n")
    with pytest.raises(TransportCalled):
        adapter.dispatch(**arguments)
    assert spy.calls == 1
