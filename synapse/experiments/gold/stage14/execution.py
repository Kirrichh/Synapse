"""Physical execution proof shared by publication and terminal reconstruction.

This reader follows already retained inputs, plans and phase records. It does
not publish, authorize execution or turn recorded verification into a grant.
"""
import hashlib

from ..canonicalization import HashBoundRef
from ..admission import gate_decision_ref
from ..admission_journal import FileAdmissionJournal
from ..contracts import LineageEdgeKind as Edge
from ..persistence import require_directory
from ..replay_store import FileReplayStore
from ..runner.records import RunRecordStore, RecordKind
from ..runner.state_machine import load_run_state
from ..runner.run_progress import load_attempt_progress, AttemptProgressPhase, require_progress_payload
from ..runner.completed_delivery_codec import restore_completed_worker_delivery
from ..runner.attempt_knowledge_store import basis_record_key
from ..stage10.record_store import FileStage10RecordStore, Stage10RecordKind
from ..stage10.context import replay_observation_delivery
from ..stage10.context_codec import decode_canonical, decode_worker_delivery_envelope
from ..stage10.intent_transport import decode_intent_candidate
from ..stage12.verification_contract import inspect_verification_record
from .sources import read_input_graph, read_consumption_gate, _reopen_location
from .graph import (GraphBuilder, LineageGraph, LineageNode, LineageNodeClass as Node, LineageViolation, LineageFailureCode as Failure,
                    LINEAGE_SCHEMA_V1, canonical, record_reference)


def execution_graph(catalog, verification):
    facts = inspect_verification_record(verification)
    if hashlib.sha256(canonical(catalog)).hexdigest() != facts["phase_refs"]["lineage_sources_sha256"]:
        raise LineageViolation(Failure.PHYSICAL_MISMATCH, "execution source catalog changed")
    locations = catalog["execution_stores"]
    if type(locations) is not dict or set(locations) != {"run", "stage10", "plan_preparation_refs"}:
        raise LineageViolation(Failure.MISSING_RECORD, "execution lacks its concrete record owners")
    path, fence = _reopen_location(locations["run"])
    for kind in RecordKind.ALL:
        require_directory(path / kind)
    store = RunRecordStore(path.parent, mutation_fence=fence)
    state = load_run_state(store)
    manifest = state.manifest
    attempts = [item for item in state.attempts if item.context.attempt_id.value == facts["attempt_id"]]
    if len(attempts) != 1:
        raise LineageViolation(Failure.MISSING_RECORD, "execution context is absent")
    context = attempts[0].context
    if (manifest.manifest_sha256 != facts["manifest_sha256"] or context.context_sha256 != facts["context_sha256"]
            or context.phase_refs.to_dict() != facts["phase_refs"]):
        raise LineageViolation(Failure.PHYSICAL_MISMATCH, "execution context differs from verification")
    path, fence = _reopen_location(locations["stage10"])
    for kind in Stage10RecordKind:
        require_directory(path / kind.value)
    stage10_store = FileStage10RecordStore(path, mutation_fence=fence)
    if (catalog["run_id"] != manifest.run_id.value or catalog["attempt_id"] != context.attempt_id.value
            or catalog["snapshot_ref"] != context.phase_refs.knowledge_snapshot_ref.to_dict()
            or catalog["retrieval_ref"] != context.phase_refs.retrieval_ref.to_dict()
            or catalog["replay_ref"] != context.phase_refs.replay_ref.to_dict()
            or locations["plan_preparation_refs"][0] != context.phase_refs.intent_ref.to_dict()
            or locations["plan_preparation_refs"][3] != context.phase_refs.plan_ref.to_dict()):
        raise LineageViolation(Failure.PHYSICAL_MISMATCH, "upstream sources belong to another execution")
    inputs = read_input_graph(catalog)
    basis = store.get(kind=RecordKind.ATTEMPT_KNOWLEDGE_BASIS, key=basis_record_key(context.attempt_index))
    if basis is None or basis.sha256 != context.phase_refs.knowledge_basis_sha256:
        raise LineageViolation(Failure.MISSING_RECORD, "attempt lacks its exact knowledge basis")
    b = GraphBuilder("execution-incomplete/v1" if facts["failure_codes"] else "execution/v1",
                     manifest.run_id.value, context.attempt_id.value)
    if facts["failure_codes"]:
        b.record("gaps", Node.EVIDENCE_GAP, {"failure_codes": facts["failure_codes"],
            "phase_refs": facts["phase_refs"], "verification_ref": verification["verification_ref"]},
            "synapse.stage4.gold.lineage-evidence-gaps/v1")
    b.record("run", Node.RUN, manifest.stored_dict(), manifest.payload()["schema_version"])
    b.record("context", Node.ATTEMPT, context.stored_dict(), context.payload()["schema_version"])
    b.record("basis", Node.KNOWLEDGE_BASIS, basis.payload, basis.payload["schema_version"])
    b.merge("input", inputs)
    b.record("inputs", Node.LINEAGE, inputs.to_dict(), LINEAGE_SCHEMA_V1)
    for role in ("replay_result", "snapshot"):
        b.link("input." + role, Edge.DERIVED_FROM, "inputs")
    b.add("verification", Node.VERIFICATION, HashBoundRef.from_dict(verification["verification_ref"]))
    b.add("task", Node.TASK_CONTRACT, HashBoundRef.from_dict(facts["task_contract_ref"]))
    b.link("task", Edge.DERIVED_FROM, "verification")
    progress = load_attempt_progress(store, manifest=manifest, context=context)
    latest_sha = None if progress.latest is None else progress.latest.progress_sha256
    if latest_sha != facts["progress_sha256"]:
        raise LineageViolation(Failure.PHYSICAL_MISMATCH, "verification names another execution phase")
    for item in progress.records:
        role = "phase." + item.phase.value
        b.record(role, Node.PHASE_RECORD, item.stored_dict(), item.payload()["schema_version"])
        b.link("context", Edge.OBSERVED_AS, role)
        b.link(role, Edge.DERIVED_FROM, "verification")
    worker = progress.get(AttemptProgressPhase.WORKER_COMPLETED)
    completed = None
    if worker is not None:
        raw, ref = require_progress_payload(worker)
        completed = restore_completed_worker_delivery(raw, expected_ref=ref)
        if facts["worker_result_ref"] is not None and ref.to_dict() != facts["worker_result_ref"]:
            raise LineageViolation(Failure.PHYSICAL_MISMATCH, "verification names another worker delivery")
        b.add("worker_result", Node.WORKER_RESULT, ref)
        stage10_store.get(kind=Stage10RecordKind.DELIVERY_RECEIPT, ref=completed.delivery_receipt_ref)
        b.add("receipt", Node.DELIVERY_RECEIPT, completed.delivery_receipt_ref)
        b.link("receipt", Edge.DERIVED_FROM, "verification")
        b.link("input.replay_result", Edge.DERIVED_FROM, "verification")
        b.link("worker_result", Edge.DERIVED_FROM, "verification")
    if "PLAN_OR_BINDING_INVALID" not in facts["failure_codes"]:
        intent, accepted, persistence = stage10_store.read_plan_bundle(
            intent_ref=context.phase_refs.intent_ref, accepted_plan_ref=context.phase_refs.plan_ref,
            bundle_sha256=None if completed is None else completed.plan_bundle_sha256)
        if (intent.knowledge_snapshot_ref != context.phase_refs.knowledge_snapshot_ref
                or intent.task_contract_ref.to_dict() != facts["task_contract_ref"]):
            raise LineageViolation(Failure.PHYSICAL_MISMATCH, "prepared plan names another input boundary")
        for role, kind, ref in (("intent", Node.INTENT, persistence.intent_store_ref),
                ("plan_proposal", Node.PLAN_PROPOSAL, persistence.plan_store_ref),
                ("plan_decision", Node.PLAN_DECISION, persistence.decision_store_ref),
                ("plan", Node.PLAN, persistence.accepted_plan_store_ref)):
            b.add(role, kind, ref)
        _add_feedback(b, intent, attempt_index=context.attempt_index, state=state, store=store)
    if context.phase_refs.worker_context_id is not None:
        audit, delivery = stage10_store.read_worker_context(
            context_id=context.phase_refs.worker_context_id,
            audit_sha256=context.phase_refs.worker_context_audit_sha256)
        if completed is not None and (audit.ref != completed.worker_context_audit_ref
                or delivery.ref != completed.delivery_envelope_ref):
            raise LineageViolation(Failure.PHYSICAL_MISMATCH, "worker completion names another persisted context")
        _require_replay_delivery(b, catalog, audit=audit, delivery=delivery)
        b.add("worker_context", Node.WORKER_CONTEXT, delivery.ref)
        b.add("worker_audit", Node.WORKER_CONTEXT, audit.ref)
        b.link("worker_context", Edge.DERIVED_FROM, "verification")
        b.link("worker_audit", Edge.DERIVED_FROM, "verification")
    for index, binding in enumerate(facts["resolved_bindings"]):
        role = f"binding.{index}"
        b.record(role, Node.BINDING, binding, "synapse.stage4.gold.lineage-resolved-binding/v1")
        b.link(role, Edge.DERIVED_FROM, "verification")
    add_verified_sources(b, facts)
    b.link_roles()
    return b.finish()


def _require_replay_delivery(builder, catalog, *, audit, delivery):
    """Establish the data edge from the exact retained replay to its delivery."""
    evidence = decode_canonical(audit.payload)["payload"]
    selection = evidence["knowledge_selection"]
    if (evidence["task_policy"]["attempt_id"] != {"value": catalog["attempt_id"]}
            or evidence["run_id"] != {"value": catalog["run_id"]}
            or evidence["task_policy"]["knowledge_snapshot_ref"] != catalog["snapshot_ref"]
            or selection["boundary_ref"] != catalog["boundary_ref"]
            or selection["consumer_context_ref"] != catalog["consumer_ref"]):
        raise LineageViolation(Failure.PHYSICAL_MISMATCH, "delivery names another input selection")
    path, fence = _reopen_location(catalog["replay"])
    replay = FileReplayStore(path.parent, mutation_fence=fence).require_result(
        HashBoundRef.from_dict(catalog["replay_ref"]))
    if evidence["replay_observation_ids"] != [item.observation_id.to_dict() for item in replay.observations]:
        raise LineageViolation(Failure.PHYSICAL_MISMATCH, "context audit names another replay")
    if delivery is not None:
        envelope = decode_worker_delivery_envelope(delivery.payload)
        body = decode_canonical(envelope.body_bytes)
        if (body["replay_observations"] != [replay_observation_delivery(item) for item in replay.observations]
                or body["admission"]["policy_version"] != evidence["consumption_policy_version"]):
            raise LineageViolation(Failure.PHYSICAL_MISMATCH, "delivered observations differ from retained replay")
    gate = read_consumption_gate(catalog, decision_id=evidence["consumption_decision_id"],
        subject_refs=selection["admitted_refs"], policy_version=evidence["consumption_policy_version"])
    path, fence = _reopen_location(catalog["admission"])
    if not FileAdmissionJournal(path, mutation_fence=fence).extends(evidence["consumption_journal_anchor"]):
        raise LineageViolation(Failure.PHYSICAL_MISMATCH, "worker admission history lost its committed prefix")
    builder.add("worker_consumption_gate", Node.ADMISSION_DECISION, gate_decision_ref(gate))


def preparation_graph(catalog, *, store, manifest, attempt_index):
    """Reconstruct a retained prefix with no Gold context or execution claim."""
    state = load_run_state(store)
    if state.manifest.manifest_sha256 != manifest.manifest_sha256:
        raise LineageViolation(Failure.PHYSICAL_MISMATCH, "preparation manifest differs from its run store")
    locations = catalog["execution_stores"]
    if type(locations) is not dict or set(locations) != {"run", "stage10", "plan_preparation_refs"}:
        raise LineageViolation(Failure.MISSING_RECORD, "preparation lacks its concrete owners")
    path, fence = _reopen_location(locations["run"])
    if (path != store.record_root.resolve() or fence.coordinator_id() != store.mutation_fence.coordinator_id()
            or catalog["run_id"] != manifest.run_id.value or catalog["attempt_id"] != str(attempt_index)):
        raise LineageViolation(Failure.PHYSICAL_MISMATCH, "preparation belongs to another run occurrence")
    started = store.get(kind=RecordKind.PREPARATION_STARTED, key=str(attempt_index))
    if started is None:
        raise LineageViolation(Failure.MISSING_RECORD, "prepared sources lack their authorized start")
    inputs = read_input_graph(catalog)
    path, fence = _reopen_location(locations["stage10"])
    for kind in Stage10RecordKind:
        require_directory(path / kind.value)
    stage10 = FileStage10RecordStore(path, mutation_fence=fence)
    records = stage10.read_preparation_prefix(
        plan_refs=tuple(HashBoundRef.from_dict(ref) for ref in locations["plan_preparation_refs"]),
        run_id=catalog["run_id"], attempt_id=catalog["attempt_id"])
    b = GraphBuilder("preparation/v1", catalog["run_id"], catalog["attempt_id"])
    b.record("run", Node.RUN, manifest.stored_dict(), manifest.payload()["schema_version"])
    b.merge("input", inputs)
    b.record("inputs", Node.LINEAGE, inputs.to_dict(), LINEAGE_SCHEMA_V1)
    for role in ("replay_result", "snapshot"):
        b.link("input." + role, Edge.DERIVED_FROM, "inputs")
    b.record("preparation_started", Node.PHASE_RECORD, started.payload, started.payload["schema_version"])
    b.record("preparation", Node.ATTEMPT_PREPARATION, {
        "sources_ref": record_reference(catalog, catalog["schema_version"]).to_dict(),
        "started_ref": record_reference(started.payload, started.payload["schema_version"]).to_dict(),
        "retained_refs": [item.ref.to_dict() for item in records],
    }, "synapse.stage4.gold.lineage-preparation/v1")
    roles = (("intent", Node.INTENT), ("plan_proposal", Node.PLAN_PROPOSAL),
             ("plan_decision", Node.PLAN_DECISION), ("plan", Node.PLAN),
             ("worker_audit", Node.WORKER_CONTEXT), ("worker_context", Node.WORKER_CONTEXT))
    for record, (role, kind) in zip(records, roles):
        b.add(role, kind, record.ref)
    if records:
        intent = decode_intent_candidate(records[0].payload)
        if intent.knowledge_snapshot_ref.to_dict() != catalog["snapshot_ref"]:
            raise LineageViolation(Failure.PHYSICAL_MISMATCH, "prepared intent names another snapshot")
        _add_feedback(b, intent, attempt_index=attempt_index, state=state, store=store)
    if len(records) >= 5:
        _require_replay_delivery(b, catalog, audit=records[4], delivery=records[5] if len(records) == 6 else None)
    b.link_roles()
    return b.finish()


def _add_feedback(builder, intent, *, attempt_index, state, store):
    """Follow explicit feedback references; chronology never supplies this edge."""
    for index, feedback in enumerate(intent.execution_feedback):
        matches = [item for item in state.attempts if item.result is not None
                   and item.context.attempt_index < attempt_index
                   and record_reference(item.result.payload(), item.result.payload()["schema_version"])
                   == feedback.source_result_ref]
        if len(matches) != 1:
            raise LineageViolation(Failure.MISSING_RECORD, "feedback lacks its exact predecessor result")
        previous = matches[0]
        if (previous.result.verified_patch_sha256 != feedback.evaluated_patch_sha256
                or previous.result.oracle_resolved != feedback.oracle_resolved):
            raise LineageViolation(Failure.PHYSICAL_MISMATCH, "feedback differs from its predecessor result")
        key = str(previous.context.attempt_index)
        source = store.get(kind=RecordKind.LINEAGE_SOURCES, key=key)
        record = store.get(kind=RecordKind.ATTEMPT_LINEAGE, key=key)
        if source is None or record is None:
            raise LineageViolation(Failure.MISSING_RECORD, "feedback lost its predecessor lineage")
        graph = LineageGraph.from_dict(record.payload)
        physical = execution_graph(source.payload, previous.result.structured_outcome["payload"]["verification"])
        result_node = LineageNode(Node.ATTEMPT_RESULT,
            record_reference(previous.result.stored_dict(), previous.result.payload()["schema_version"]),
            previous.context.run_id.value, previous.context.attempt_id.value)
        if (not set(physical.nodes) <= set(graph.nodes) or not set(physical.edges) <= set(graph.edges)
                or dict(graph.roles).get("result") != result_node.node_id
                or result_node not in graph.nodes):
            raise LineageViolation(Failure.PHYSICAL_MISMATCH, "feedback lineage differs from physical predecessor proof")
        prefix = f"feedback.{index}"
        builder.merge(prefix, graph)
        builder.link(prefix + ".result", Edge.DERIVED_FROM, "intent")


def add_verified_sources(builder, facts):
    """Bind the explicit source fields of a verified execution record."""
    c1 = facts["c1"]
    if c1 is None:
        return
    builder.record("c1", Node.CONTROLLED_CHANGE_RESULT, c1,
                   "synapse.stage4.gold.lineage-c1-evidence/v1")
    for role, field, kind in (
        ("evidence", "evidence_ref", Node.GOLD_EVIDENCE),
        ("report", "report_ref", Node.GOLD_EVIDENCE),
        ("oracle", "oracle_result_ref", Node.ORACLE_RESULT),
    ):
        if c1.get(field) is not None:
            builder.add(role, kind, HashBoundRef.from_dict(c1[field]))
            builder.link(role, Edge.DERIVED_FROM, "verification")
    if c1.get("verified_revision") is not None:
        builder.record("commit", Node.COMMIT,
                       {"revision": c1["verified_revision"]},
                       "synapse.stage4.gold.lineage-verified-commit/v1")
