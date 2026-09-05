"""Stage 12: factual verification of one durably executed attempt.

This owner coordinates existing authority boundaries. It neither executes C1/C2
nor chooses a final status. The only constructor reads platform-owned records;
JSON inspection alone cannot mint a verified record.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from ..bindings import binding_from_dict, binding_to_ref
from ..canonicalization import HashBoundRef, RefKind
from ..contracts import RepositoryRevision
from ..persistence import PersistenceViolation
from ..stage10.context_codec import decode_canonical, encode_canonical
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
from ..runner.vocabulary import GoldRunFailureCode, GoldRunViolation


VERIFICATION_SCHEMA_V1 = "synapse.stage4.gold.verification/v1"
VERIFIER_VERSION = "stage12-c1-plan-bindings/v1"
_SEAL = object()


@dataclass(frozen=True, init=False)
class VerificationRecord:
    _bytes: bytes
    _digest: str
    _seal: object

    def __new__(cls, *args: object, **kwargs: object):
        raise TypeError("VerificationRecord is produced by verify_attempt")

    def payload(self) -> dict[str, object]:
        require_verification_record(self)
        return decode_canonical(self._bytes)

    @property
    def reference(self) -> HashBoundRef:
        require_verification_record(self)
        digest = hashlib.sha256(self._bytes).hexdigest()
        return HashBoundRef(RefKind.ARTIFACT, digest, VERIFICATION_SCHEMA_V1, digest,
                            len(self._bytes), "application/json")

    def to_dict(self) -> dict[str, object]:
        return {"verification_ref": self.reference.to_dict(), "payload": self.payload()}


def require_verification_record(value: object) -> VerificationRecord:
    if type(value) is not VerificationRecord or getattr(value, "_seal", None) is not _SEAL:
        raise GoldRunViolation(GoldRunFailureCode.TYPE_MISMATCH, "verification must be evaluator-sealed")
    if (type(value._bytes) is not bytes or hashlib.sha256(value._bytes).hexdigest() != value._digest
            or decode_canonical(value._bytes).get("schema_version") != VERIFICATION_SCHEMA_V1):
        raise GoldRunViolation(GoldRunFailureCode.IDENTITY_MISMATCH, "verification record is malformed")
    return value


def inspect_verification_record(value: object) -> dict[str, object]:
    """Check transport identity only. This deliberately returns no sealed type."""
    if type(value) is not dict or set(value) != {"verification_ref", "payload"}:
        raise ValueError("verification transport has an unknown shape")
    payload = value["payload"]
    required = {"schema_version", "verifier_version", "manifest_sha256", "run_id", "attempt_id",
                "context_sha256", "phase_refs", "progress_sha256", "task_contract_ref",
                "worker_result_ref", "c1_receipt_ref", "c1", "plan", "resolved_bindings", "obligations",
                "failure_codes", "interrupted", "refused"}
    if type(payload) is not dict or set(payload) != required:
        raise ValueError("verification payload has an unknown shape")
    raw = encode_canonical(payload)
    ref = HashBoundRef.from_dict(value["verification_ref"])
    if (ref.kind is not RefKind.ARTIFACT or ref.schema_id != VERIFICATION_SCHEMA_V1
            or payload["schema_version"] != VERIFICATION_SCHEMA_V1
            or payload["verifier_version"] != VERIFIER_VERSION
            or ref.ref_id != ref.sha256 or ref.sha256 != hashlib.sha256(raw).hexdigest()
            or ref.byte_length != len(raw) or ref.media_type != "application/json"):
        raise ValueError("verification identity differs from its bytes")
    for field in ("interrupted", "refused"):
        if type(payload[field]) is not bool:
            raise ValueError("verification flags must be exact booleans")
    for field in ("resolved_bindings", "obligations", "failure_codes"):
        if type(payload[field]) is not list:
            raise ValueError("verification collections must be exact lists")
    for field in ("manifest_sha256", "context_sha256"):
        if type(payload[field]) is not str or re.fullmatch(r"[0-9a-f]{64}", payload[field]) is None:
            raise ValueError("verification digest is malformed")
    for field in ("worker_result_ref", "c1_receipt_ref"):
        if payload[field] is not None and HashBoundRef.from_dict(payload[field]).kind is not RefKind.ARTIFACT:
            raise ValueError("verification source has the wrong reference kind")
    if HashBoundRef.from_dict(payload["task_contract_ref"]).kind is not RefKind.CONTRACT_CONDITION:
        raise ValueError("verification task reference has the wrong kind")
    bindings = [HashBoundRef.from_dict(item) for item in payload["resolved_bindings"]]
    if any(item.kind is not RefKind.BINDING for item in bindings) or len(set(bindings)) != len(bindings):
        raise ValueError("resolved binding references are malformed")
    if (any(item not in {"PLAN_OR_BINDING_INVALID", "C1_PROOF_INVALID", "C1_WRITER_REJECTED"}
            for item in payload["failure_codes"]) or len(set(payload["failure_codes"])) != len(payload["failure_codes"])):
        raise ValueError("verification failure codes are unknown or duplicated")
    c1 = payload["c1"]
    if c1 is not None:
        fields = {"c1_result_ref", "oracle_result_ref", "command_policy_ref", "c1_status", "oracle_resolved",
                  "infra_error", "no_candidate", "refused", "evidence_ref", "report_ref", "report_schema",
                  "task_ref", "commands_complete", "changed_paths"}
        if type(c1) is not dict or set(c1) != fields:
            raise ValueError("C1 verification projection has an unknown shape")
        if any(type(c1[field]) is not bool for field in ("infra_error", "no_candidate", "refused", "commands_complete")):
            raise ValueError("C1 verification flags must be exact booleans")
        if c1["oracle_resolved"] is not None and type(c1["oracle_resolved"]) is not bool:
            raise ValueError("oracle resolution must be boolean or absent")
        for field in ("c1_result_ref", "oracle_result_ref", "evidence_ref", "report_ref", "task_ref"):
            if c1[field] is not None and HashBoundRef.from_dict(c1[field]).kind is not RefKind.ARTIFACT:
                raise ValueError("C1 proof reference has the wrong kind")
        HashBoundRef.from_dict(c1["command_policy_ref"])
        if type(c1["changed_paths"]) is not dict or any(type(path) is not str or code not in {"A", "M", "D", "T"}
                                                        for path, code in c1["changed_paths"].items()):
            raise ValueError("verified change projection is malformed")
    plan = payload["plan"]
    if plan is not None:
        if type(plan) is not dict or set(plan) != {"bundle_sha256", "decision_ref", "authority_route", "policy_sha256"}:
            raise ValueError("plan verification projection has an unknown shape")
        HashBoundRef.from_dict(plan["decision_ref"])
        if plan["authority_route"] not in {"POLICY_ACCEPTED", "GOVERNING_HUMAN_ACCEPTED"}:
            raise ValueError("historical plan was not accepted")
    operations = set()
    for item in payload["obligations"]:
        if type(item) is not dict or set(item) != {"operation_id", "condition_ref", "evidence_ref", "discharged"}:
            raise ValueError("verification obligation has an unknown shape")
        if type(item["operation_id"]) is not str or not item["operation_id"] or item["operation_id"] in operations:
            raise ValueError("verification operation identity is malformed or duplicated")
        operations.add(item["operation_id"])
        if type(item["discharged"]) is not bool:
            raise ValueError("obligation discharge must be a boolean")
        if item["condition_ref"] is not None:
            HashBoundRef.from_dict(item["condition_ref"])
        if item["discharged"] and (c1 is None or item["evidence_ref"] != c1["report_ref"] or item["evidence_ref"] is None):
            raise ValueError("discharged obligation lacks its report")
        if not item["discharged"] and item["evidence_ref"] is not None:
            raise ValueError("undischarged obligation claims evidence")
    return decode_canonical(raw)


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
        "schema_version": VERIFICATION_SCHEMA_V1, "verifier_version": VERIFIER_VERSION,
        "manifest_sha256": manifest.manifest_sha256, "run_id": manifest.run_id.value,
        "attempt_id": context.attempt_id.value, "context_sha256": context.context_sha256,
        "phase_refs": context.phase_refs.to_dict(),
        "progress_sha256": None if latest is None else latest.progress_sha256,
        "task_contract_ref": profile.task_contract.reference.to_dict(),
        "worker_result_ref": None, "c1_receipt_ref": None, "c1": None, "plan": None,
        "resolved_bindings": [], "obligations": [], "failure_codes": [],
        "interrupted": False, "refused": False,
    }
    if latest is None or latest.phase in (AttemptProgressPhase.DELIVERY_STARTED, AttemptProgressPhase.C1_STARTED):
        payload["interrupted"] = True
    elif latest.phase in (AttemptProgressPhase.DELIVERY_REFUSED, AttemptProgressPhase.DELIVERY_UNAVAILABLE):
        raw, ref = require_progress_payload(latest)
        failure = restore_attempt_delivery_failure(raw, expected_ref=ref)
        payload["refused"] = type(failure) is AttemptDeliveryRefusal
        payload["interrupted"] = not payload["refused"]
    elif latest.phase is not AttemptProgressPhase.C1_COMPLETED:
        raise ValueError("attempt has no terminal verification boundary")
    else:
        worker = progress.get(AttemptProgressPhase.WORKER_COMPLETED)
        if worker is None:
            raise ValueError("C1 verification lacks durable worker completion")
        raw, ref = require_progress_payload(worker)
        completed = restore_completed_worker_delivery(raw, expected_ref=ref)
        require_completed_delivery_authority(context=context, completed=completed)
        raw, ref = require_progress_payload(latest)
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
        try:
            c1 = read_c1_verification_evidence(boundary, receipt=receipt, base_revision=manifest.config.base_revision, run_root=run_root).payload()
            payload["c1"] = c1
            if payload["plan"] is not None:
                payload["obligations"] = _verification_obligations(profile, accepted, c1)
            if not receipt.write_ok:
                payload["failure_codes"].append("C1_WRITER_REJECTED")
        except (ValueError, TypeError, KeyError, OSError, PersistenceViolation, GoldRunViolation):
            payload["failure_codes"].append("C1_PROOF_INVALID")
    result = object.__new__(VerificationRecord)
    object.__setattr__(result, "_bytes", encode_canonical(payload))
    object.__setattr__(result, "_digest", hashlib.sha256(result._bytes).hexdigest())
    object.__setattr__(result, "_seal", _SEAL)
    return require_verification_record(result)
