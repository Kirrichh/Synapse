"""Physical execution proof shared by publication and terminal reconstruction.

This reader follows already retained inputs, plans and phase records. It does
not publish, authorize execution or turn recorded verification into a grant.
"""
import hashlib

from ..canonicalization import HashBoundRef
from ..contracts import LineageEdgeKind as Edge
from ..persistence import require_directory
from ..runner.records import RunRecordStore, RecordKind
from ..runner.state_machine import load_run_state
from ..runner.run_progress import load_attempt_progress, AttemptProgressPhase, require_progress_payload
from ..runner.completed_delivery_codec import restore_completed_worker_delivery
from ..runner.attempt_knowledge_store import basis_record_key
from ..stage10.record_store import FileStage10RecordStore, Stage10RecordKind
from ..stage12.verification_contract import inspect_verification_record
from .sources import read_input_graph, _reopen_location
from .graph import (GraphBuilder, LineageNodeClass as Node, LineageViolation, LineageFailureCode as Failure,
                    LINEAGE_SCHEMA_V1, canonical)


def execution_graph(catalog, verification):
    facts = inspect_verification_record(verification)
    if hashlib.sha256(canonical(catalog)).hexdigest() != facts["phase_refs"]["lineage_sources_sha256"]:
        raise LineageViolation(Failure.PHYSICAL_MISMATCH, "execution source catalog changed")
    locations = catalog["execution_stores"]
    if type(locations) is not dict or set(locations) != {"run", "stage10"}:
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
            or catalog["replay_ref"] != context.phase_refs.replay_ref.to_dict()):
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
    if worker is not None:
        raw, ref = require_progress_payload(worker)
        completed = restore_completed_worker_delivery(raw, expected_ref=ref)
        if facts["worker_result_ref"] is not None and ref.to_dict() != facts["worker_result_ref"]:
            raise LineageViolation(Failure.PHYSICAL_MISMATCH, "verification names another worker delivery")
        b.add("worker_result", Node.WORKER_RESULT, ref)
        stage10_store.get(kind=Stage10RecordKind.WORKER_DELIVERY_ENVELOPE, ref=completed.delivery_envelope_ref)
        b.add("worker_context", Node.WORKER_CONTEXT, completed.delivery_envelope_ref)
        stage10_store.get(kind=Stage10RecordKind.DELIVERY_RECEIPT, ref=completed.delivery_receipt_ref)
        b.add("receipt", Node.DELIVERY_RECEIPT, completed.delivery_receipt_ref)
        b.link("receipt", Edge.DERIVED_FROM, "verification")
        stage10_store.get(kind=Stage10RecordKind.WORKER_CONTEXT_AUDIT, ref=completed.worker_context_audit_ref)
        b.add("worker_audit", Node.WORKER_CONTEXT, completed.worker_context_audit_ref)
        b.link("worker_audit", Edge.DERIVED_FROM, "verification")
        if "PLAN_OR_BINDING_INVALID" not in facts["failure_codes"]:
            intent, accepted, persistence = stage10_store.read_dispatched_plan(
                intent_ref=context.phase_refs.intent_ref, accepted_plan_ref=context.phase_refs.plan_ref,
                bundle_sha256=completed.plan_bundle_sha256)
            for role, kind, ref in (("intent", Node.INTENT, persistence.intent_store_ref),
                    ("plan_proposal", Node.PLAN_PROPOSAL, persistence.plan_store_ref),
                    ("plan_decision", Node.PLAN_DECISION, persistence.decision_store_ref),
                    ("plan", Node.PLAN, persistence.accepted_plan_store_ref)):
                b.add(role, kind, ref)
        b.link("input.replay_result", Edge.DERIVED_FROM, "verification")
        b.link("worker_result", Edge.DERIVED_FROM, "verification")
    for index, binding in enumerate(facts["resolved_bindings"]):
        role = f"binding.{index}"
        b.record(role, Node.BINDING, binding, "synapse.stage4.gold.lineage-resolved-binding/v1")
        b.link(role, Edge.DERIVED_FROM, "verification")
    add_verified_sources(b, facts)
    b.link_roles()
    return b.finish()


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

