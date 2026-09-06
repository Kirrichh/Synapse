"""Immutable verification transport shared by verification and publication.

The disk-reading verifier remains the sole producer. Inspection checks the
closed evidence contract and never creates an execution or publication grant.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from ..canonicalization import HashBoundRef, RefKind
from ..stage10.context_codec import decode_canonical, encode_canonical
from ..runner.vocabulary import GoldRunFailureCode, GoldRunViolation
from .reusable import inspect_reusable_projection


VERIFICATION_SCHEMA_V5 = "synapse.stage4.gold.verification/v5"
VERIFIER_VERSION = "stage12-c1-plan-bindings/v5"
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
        return HashBoundRef(RefKind.ARTIFACT, digest, VERIFICATION_SCHEMA_V5, digest,
                            len(self._bytes), "application/json")

    def to_dict(self) -> dict[str, object]:
        value = {"verification_ref": self.reference.to_dict(), "payload": self.payload()}
        inspect_verification_record(value)
        return value


def require_verification_record(value: object) -> VerificationRecord:
    if type(value) is not VerificationRecord or getattr(value, "_seal", None) is not _SEAL:
        raise GoldRunViolation(GoldRunFailureCode.TYPE_MISMATCH, "verification must be evaluator-sealed")
    if (type(value._bytes) is not bytes or hashlib.sha256(value._bytes).hexdigest() != value._digest
            or decode_canonical(value._bytes).get("schema_version") != VERIFICATION_SCHEMA_V5):
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
                "failure_codes", "interrupted", "refused", "reusable_candidates", "publication", "mechanism_use", "reuse_promotions"}
    if type(payload) is not dict or set(payload) != required:
        raise ValueError("verification payload has an unknown shape")
    raw = encode_canonical(payload)
    ref = HashBoundRef.from_dict(value["verification_ref"])
    if (ref.kind is not RefKind.ARTIFACT or ref.schema_id != VERIFICATION_SCHEMA_V5
            or payload["schema_version"] != VERIFICATION_SCHEMA_V5
            or payload["verifier_version"] != VERIFIER_VERSION
            or ref.ref_id != ref.sha256 or ref.sha256 != hashlib.sha256(raw).hexdigest()
            or ref.byte_length != len(raw) or ref.media_type != "application/json"):
        raise ValueError("verification identity differs from its bytes")
    for field in ("interrupted", "refused"):
        if type(payload[field]) is not bool:
            raise ValueError("verification flags must be exact booleans")
    for field in ("resolved_bindings", "obligations", "failure_codes", "reusable_candidates", "reuse_promotions"):
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
    if (any(item not in {"PLAN_OR_BINDING_INVALID", "C1_PROOF_INVALID", "C1_WRITER_REJECTED", "REUSABLE_PROOF_INVALID", "PUBLICATION_PROOF_INVALID", "MECHANISM_USE_INVALID", "REUSE_PROMOTION_INVALID"}
            for item in payload["failure_codes"]) or len(set(payload["failure_codes"])) != len(payload["failure_codes"])):
        raise ValueError("verification failure codes are unknown or duplicated")
    c1 = payload["c1"]
    if c1 is not None:
        fields = {"c1_result_ref", "oracle_result_ref", "command_policy_ref", "c1_status", "oracle_resolved",
                  "infra_error", "no_candidate", "refused", "evidence_ref", "report_ref", "report_schema",
                  "task_ref", "commands_complete", "changed_paths", "verified_patch_sha256", "verified_revision"}
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
        for name, size in (("verified_patch_sha256", 64), ("verified_revision", 40)):
            if c1[name] is not None and (type(c1[name]) is not str or re.fullmatch(r"[0-9a-f]{%d}" % size, c1[name]) is None):
                raise ValueError("C1 verified input identity is malformed")
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
    inspect_reusable_projection(payload["reusable_candidates"], c1=c1, task_contract_ref=payload["task_contract_ref"])
    publication = payload["publication"]
    if publication is not None:
        if (type(publication) is not dict or set(publication) != {"state", "result_ref", "decision_ref", "reason_codes"}
                or publication["state"] not in {"COMMITTED", "REJECTED", "QUARANTINED", "REVIEW_REQUIRED"}
                or type(publication["reason_codes"]) is not list or not publication["reason_codes"]
                or any(type(item) is not str or not item for item in publication["reason_codes"])):
            raise ValueError("publication verification has an unknown shape")
        for field in ("result_ref", "decision_ref"):
            if publication[field] is not None and HashBoundRef.from_dict(publication[field]).kind is not RefKind.ARTIFACT:
                raise ValueError("publication proof reference has the wrong kind")
        if publication["result_ref"] is None:
            raise ValueError("publication result lacks physical proof")
        candidates = payload["reusable_candidates"]
        if publication["state"] == "COMMITTED":
            if len(candidates) != 1 or candidates[0]["publication_ref"] != publication["result_ref"]:
                raise ValueError("committed publication differs from its verified output")
        elif candidates:
            raise ValueError("uncommitted publication cannot create reusable output")
    use = payload["mechanism_use"]
    if use is not None:
        if (type(use) is not dict or set(use) != {"record_ref", "publication_ref", "behavior_ref", "effect", "task_resolved", "repository_unchanged"}
                or use["effect"] != "EXACT_REJECTED_C1_DISPATCH_AVOIDED" or use["task_resolved"] is not False
                or use["repository_unchanged"] is not True or c1 is not None or payload["c1_receipt_ref"] is not None
                or payload["worker_result_ref"] is None or payload["reusable_candidates"]):
            raise ValueError("observed guard use contradicts execution facts")
        for name in ("record_ref", "publication_ref", "behavior_ref"):
            if HashBoundRef.from_dict(use[name]).kind is not RefKind.ARTIFACT:
                raise ValueError("mechanism proof has the wrong reference kind")
    if len(payload["reuse_promotions"]) > 1:
        raise ValueError("an exact guard can promote only its observed subject")
    for promotion in payload["reuse_promotions"]:
        if (use is None or type(promotion) is not dict
                or set(promotion) != {"record_ref", "behavior_ref", "state", "mechanism_use_ref"}
                or promotion["state"] != "OBSERVED_USEFUL_REUSE" or promotion["behavior_ref"] != use["behavior_ref"]
                or promotion["mechanism_use_ref"] != use["record_ref"] or payload["failure_codes"]):
            raise ValueError("promotion lacks its exact independent mechanism proof")
        if HashBoundRef.from_dict(promotion["record_ref"]).schema_id != "synapse.stage4.gold.reuse-promotion/v1":
            raise ValueError("promotion reference has an unknown schema")
    return decode_canonical(raw)


def _seal_verified_facts(payload: dict[str, object]) -> VerificationRecord:
    """Private handoff from the one disk-reading verifier after all checks."""
    result = object.__new__(VerificationRecord)
    raw = encode_canonical(payload)
    object.__setattr__(result, "_bytes", raw)
    object.__setattr__(result, "_digest", hashlib.sha256(raw).hexdigest())
    object.__setattr__(result, "_seal", _SEAL)
    result.to_dict()
    return result
