"""Stage 12: factual verification of one durably executed attempt.

This owner coordinates existing authority boundaries. It neither executes C1/C2
nor chooses a final status. The only constructor reads platform-owned records;
JSON inspection alone cannot mint a verified record.
"""

from __future__ import annotations

from pathlib import Path

from ..bindings import binding_from_dict, binding_to_ref
from ..canonicalization import HashBoundRef, RefKind
from ..contracts import RepositoryRevision
from ..persistence import PersistenceViolation
from ..stage10.intent import AcceptanceKind, EffectDisposition, EffectKind
from ..stage10.planning import OperationKind, VerificationKind
from ..stage10.record_store import FileStage10RecordStore
from ..runner.attempt_authority import require_c1_receipt_authority, require_completed_delivery_authority
from ..runner.attempt_delivery_failure import restore_attempt_delivery_failure
from ..runner.attempt_plan import GoldAttemptPlanProfile, validate_recorded_attempt_plan
from ..runner.c1_boundary import (
    C1AttemptBoundary, read_c1_verification_evidence, restore_c1_authority_receipt,
)
from ..runner.completed_delivery_codec import restore_completed_worker_delivery, completed_worker_delivery_ref
from ..runner.delivery import AttemptDeliveryRefusal
from ..runner.models import GoldAttemptContext, GoldRunManifest
from ..runner.records import RecordKind, RunRecordStore
from ..runner.run_progress import AttemptProgressPhase, load_attempt_progress, require_progress_payload
from ..runner.vocabulary import GoldRunViolation
from .reusable import verify_reusable_candidate
from .verification_contract import VerificationRecord, VERIFICATION_SCHEMA_V5, VERIFIER_VERSION, _seal_verified_facts
from ..stage13.run_publication import read_publication_outcome
from ..stage13.reuse import verify_mechanism_use
from ..stage13.promotion import read_reuse_promotions
from ..stage10.context_codec import decode_canonical



def _resolved_bindings(profile, intent, accepted):
    required = set(intent.target_bindings)
    required.update(ref for operation in accepted.candidate.operations
                    for ref in operation.input_refs if ref.kind is RefKind.BINDING)
    actual = {}
    revision = RepositoryRevision.git_commit(profile.task_contract.repository_revision_sha256)
    for binding in profile.target_records:
        resolved = binding_from_dict(binding.to_dict(), repo_root=profile.repository_root, consumer_revision=revision)
        ref = binding_to_ref(resolved)
        if not profile.task_contract.allowed_scope.covers(resolved.path):
            raise ValueError("resolved binding exceeds task scope")
        actual[ref] = resolved
    if set(actual) != required:
        raise ValueError("required bindings differ from resolved records")
    return [ref.to_dict() for ref in sorted(actual, key=lambda item: item.ref_id)]


def _verification_obligations(profile, accepted, c1):
    task = profile.task_contract
    condition = HashBoundRef.from_dict(c1["command_policy_ref"])
    changes = c1["changed_paths"]
    effect_codes = {EffectKind.PATH_CREATED: "A", EffectKind.PATH_MODIFIED: "M", EffectKind.PATH_DELETED: "D"}
    effects_complete = True
    for effect in task.effects:
        code = effect_codes.get(effect.kind)
        observed = code is not None and changes.get(effect.subject_path) == code
        if code is None or effect.verification_ref != condition:
            effects_complete = False
        elif effect.disposition is EffectDisposition.EXPECTED and not observed:
            effects_complete = False
        elif effect.disposition is EffectDisposition.FORBIDDEN and observed:
            effects_complete = False
    if any(not task.allowed_scope.covers(path) for path in changes):
        raise ValueError("verified change exceeds governing task scope")
    obligations = []
    for operation in accepted.candidate.operations:
        obligation = operation.verification
        discharged = (
            operation.kind is OperationKind.EDIT_CONTROLLED_CHANGE
            and obligation is not None and obligation.kind is VerificationKind.CONTRACT_CONDITION
            and obligation.condition_ref == condition and c1["commands_complete"] is True
            and effects_complete
            and all(item.kind is AcceptanceKind.CONTRACT_CONDITION and item.condition_ref == condition
                    for item in task.acceptance)
        )
        obligations.append({
            "operation_id": operation.operation_id,
            "condition_ref": None if obligation is None else obligation.condition_ref.to_dict(),
            "evidence_ref": c1["report_ref"] if discharged else None,
            "discharged": discharged,
        })
    return obligations


def verify_attempt(
    *, manifest: GoldRunManifest, context: GoldAttemptContext,
    run_store: RunRecordStore, boundary: C1AttemptBoundary,
    record_store: FileStage10RecordStore, profile: GoldAttemptPlanProfile, run_root: Path,
    reusable_authority=None, publication_store=None,
) -> VerificationRecord:
    """Evaluate exact durable progress; never accept caller-declared completion."""
    if (type(manifest) is not GoldRunManifest or type(context) is not GoldAttemptContext
            or type(run_store) is not RunRecordStore or type(boundary) is not C1AttemptBoundary
            or type(record_store) is not FileStage10RecordStore or type(profile) is not GoldAttemptPlanProfile):
        raise TypeError("verification requires exact platform owners")
    manifest.validate_identity()
    context.validate_identity()
    if context.run_id != manifest.run_id or context.gold_run_id != manifest.gold_run_id:
        raise ValueError("verification context belongs to another run")
    stored_context = run_store.get(kind=RecordKind.ATTEMPT_CONTEXT, key=str(context.attempt_index))
    stored_manifest = run_store.get(kind=RecordKind.MANIFEST, key="manifest")
    if (stored_context is None or stored_context.payload != context.stored_dict()
            or stored_manifest is None or stored_manifest.payload != manifest.stored_dict()):
        raise ValueError("verification requires the actual durable run and context")
    progress = load_attempt_progress(run_store, manifest=manifest, context=context)
    latest = progress.latest
    payload = {
        "schema_version": VERIFICATION_SCHEMA_V5, "verifier_version": VERIFIER_VERSION,
        "manifest_sha256": manifest.manifest_sha256, "run_id": manifest.run_id.value,
        "attempt_id": context.attempt_id.value, "context_sha256": context.context_sha256,
        "phase_refs": context.phase_refs.to_dict(),
        "progress_sha256": None if latest is None else latest.progress_sha256,
        "task_contract_ref": profile.task_contract.reference.to_dict(),
        "worker_result_ref": None, "c1_receipt_ref": None, "c1": None, "plan": None,
        "resolved_bindings": [], "obligations": [], "failure_codes": [],
        "interrupted": False, "refused": False,
        "reusable_candidates": [], "publication": None, "mechanism_use": None, "reuse_promotions": [],
    }
    mechanism_record = None
    if latest is None or latest.phase in (AttemptProgressPhase.DELIVERY_STARTED, AttemptProgressPhase.C1_STARTED):
        payload["interrupted"] = True
    elif latest.phase in (AttemptProgressPhase.DELIVERY_REFUSED, AttemptProgressPhase.DELIVERY_UNAVAILABLE):
        raw, ref = require_progress_payload(latest)
        failure = restore_attempt_delivery_failure(raw, expected_ref=ref)
        payload["refused"] = type(failure) is AttemptDeliveryRefusal
        payload["interrupted"] = not payload["refused"]
    elif latest.phase not in (AttemptProgressPhase.C1_COMPLETED, AttemptProgressPhase.REUSE_GUARD_COMPLETED):
        raise ValueError("attempt has no terminal verification boundary")
    else:
        worker = progress.get(AttemptProgressPhase.WORKER_COMPLETED)
        if worker is None:
            raise ValueError("C1 verification lacks durable worker completion")
        raw, ref = require_progress_payload(worker)
        completed = restore_completed_worker_delivery(raw, expected_ref=ref)
        require_completed_delivery_authority(context=context, completed=completed)
        raw, ref = require_progress_payload(latest)
        guarded = latest.phase is AttemptProgressPhase.REUSE_GUARD_COMPLETED
        if guarded:
            mechanism_record = decode_canonical(raw)
        else:
            receipt = restore_c1_authority_receipt(raw, expected_ref=ref)
            payload["c1_receipt_ref"] = ref.to_dict()
            require_c1_receipt_authority(manifest=manifest, context=context, worker_delivery=completed, receipt=receipt)
        payload["worker_result_ref"] = completed_worker_delivery_ref(completed).to_dict()
        try:
            intent, accepted, persistence = record_store.read_dispatched_plan(
                intent_ref=context.phase_refs.intent_ref, accepted_plan_ref=context.phase_refs.plan_ref,
                bundle_sha256=completed.plan_bundle_sha256,
            )
            validate_recorded_attempt_plan(profile=profile, intent=intent, accepted=accepted)
            if intent.knowledge_snapshot_ref != context.phase_refs.knowledge_snapshot_ref:
                raise ValueError("plan refers to another snapshot")
            payload["plan"] = {
                "bundle_sha256": persistence.bundle_sha256,
                "decision_ref": persistence.decision_store_ref.to_dict(),
                "authority_route": accepted.decision.reason.value,
                "policy_sha256": accepted.decision.policy_sha256,
            }
            payload["resolved_bindings"] = _resolved_bindings(profile, intent, accepted)
        except (ValueError, TypeError, KeyError, OSError, PersistenceViolation, GoldRunViolation):
            payload["failure_codes"].append("PLAN_OR_BINDING_INVALID")
        if guarded:
            try:
                payload["mechanism_use"] = verify_mechanism_use(mechanism_record, publisher=publication_store,
                    stage10_store=record_store, run_store=run_store, run_root=run_root, manifest=manifest, context=context,
                    completed=completed, profile=profile, boundary=boundary)
            except (ValueError, TypeError, KeyError, OSError, RuntimeError):
                payload["failure_codes"].append("MECHANISM_USE_INVALID")
        else:
            try:
                c1_evidence = read_c1_verification_evidence(boundary, receipt=receipt, base_revision=manifest.config.base_revision, run_root=run_root)
                c1 = c1_evidence.payload()
                payload["c1"] = c1
                if payload["plan"] is not None:
                    payload["obligations"] = _verification_obligations(profile, accepted, c1)
                if not receipt.write_ok:
                    payload["failure_codes"].append("C1_WRITER_REJECTED")
            except (ValueError, TypeError, KeyError, OSError, PersistenceViolation, GoldRunViolation):
                payload["failure_codes"].append("C1_PROOF_INVALID")
    candidate = run_store.get(kind=RecordKind.REUSABLE_CANDIDATE, key=str(context.attempt_index))
    if candidate is not None:
        try:
            if payload["c1"] is None or reusable_authority is None or reusable_authority.repository_root != profile.repository_root:
                raise ValueError("reusable output lacks its verification authority")
            payload["reusable_candidates"] = [verify_reusable_candidate(
                candidate.payload, authority=reusable_authority, manifest=manifest, context=context,
                task_contract_ref=profile.task_contract.reference, c1=c1_evidence,
            )]
        except (ValueError, TypeError, KeyError, OSError, RuntimeError):
            payload["failure_codes"].append("REUSABLE_PROOF_INVALID")
    try:
        payload["publication"] = read_publication_outcome(publisher=publication_store, store=run_store,
            manifest=manifest, context=context, facts=payload)
    except (ValueError, TypeError, KeyError, OSError, RuntimeError):
        payload["failure_codes"].append("PUBLICATION_PROOF_INVALID")
        payload["reusable_candidates"] = []
    try:
        payload["reuse_promotions"] = read_reuse_promotions(publisher=publication_store, store=run_store,
            manifest=manifest, context=context, facts=payload, mechanism_record=mechanism_record)
    except (ValueError, TypeError, KeyError, OSError, RuntimeError):
        payload["failure_codes"].append("REUSE_PROMOTION_INVALID")
        payload["reuse_promotions"] = []
    return _seal_verified_facts(payload)
