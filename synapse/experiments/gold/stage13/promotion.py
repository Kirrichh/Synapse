"""Independent, immutable useful-reuse promotion after downstream verification.

The closed policy recognizes one observed benefit: declining an identical
already-rejected C1 candidate in its exact domain, with task resolution unchanged.
The record is retained by the existing consumer run store, not an index overlay.
"""

from ..runner.records import RecordKind
from ..runner.run_recovery import PendingRunRecord
from ..stage12.outcome import evaluate_attempt_outcome, inspect_outcome
from ..stage12.verification_contract import require_verification_record, inspect_verification_record
from ..persistence import read_committed_snapshot_transaction
from ..stage10.context_codec import decode_canonical
from .publication import EVALUATOR, PublicationViolation, reference
from .publication_store import PublicationResult
from .reuse import MECHANISM_USE_SCHEMA_V1, OBSERVER, REUSE_GUARD_POLICY_V1


REUSE_PROMOTION_SCHEMA_V1 = "synapse.stage4.gold.reuse-promotion/v1"


def _basis(*, publisher, facts):
    use = facts["mechanism_use"]
    if (publisher is None or use is None or facts["failure_codes"] or facts["interrupted"] or facts["refused"]
            or facts["c1"] is not None or facts["plan"] is None or not facts["resolved_bindings"]
            or use["effect"] != "EXACT_REJECTED_C1_DISPATCH_AVOIDED"
            or use["task_resolved"] is not False or use["repository_unchanged"] is not True):
        raise PublicationViolation("useful reuse requires independently verified exact negative-guard consumption")
    publisher.authority.validate()
    return use


def _source_proof(publisher, mechanism_record, use):
    if (type(mechanism_record) is not dict
            or reference(mechanism_record, MECHANISM_USE_SCHEMA_V1).to_dict() != use["record_ref"]
            or mechanism_record["publication_ref"] != use["publication_ref"]
            or mechanism_record["behavior_ref"] != use["behavior_ref"]):
        raise PublicationViolation("promotion mechanism differs from the independently verified consumption")
    result = PublicationResult(publisher.root, mechanism_record["publication_transaction_id"])
    if result.reference.to_dict() != mechanism_record["publication_ref"]:
        raise PublicationViolation("promotion names a different source publication")
    _, members = read_committed_snapshot_transaction(publisher.root / "prepared", transaction_id=result.transaction_id)
    request = decode_canonical(members["request.json"])
    facts = inspect_verification_record(request["verification"])
    outcome = inspect_outcome(request["outcome"])
    if (facts["failure_codes"] or facts["c1"] is None or facts["c1"]["oracle_resolved"] is not False
            or outcome["status"] != "UNRESOLVED" or outcome["verification"] != request["verification"]):
        raise PublicationViolation("promotion has no independently verified unresolved baseline")
    sources = sorted({item["value"] for item in request["source_actor_ids"]}
                     | {item.value for item in publisher.authority.source_actors} | {OBSERVER})
    if EVALUATOR.value in sources:
        raise PublicationViolation("promotion evaluator must be independent of producer, observer and consumer")
    return request, sources


def read_reuse_promotions(*, publisher, store, manifest, context, facts, mechanism_record):
    record = store.get(kind=RecordKind.REUSE_PROMOTION, key=str(context.attempt_index))
    if record is None:
        return []
    use = _basis(publisher=publisher, facts=facts)
    value = record.payload
    fields = {"schema_version", "policy_version", "from_state", "to_state", "behavior_ref", "publication_ref", "mechanism_use_ref",
        "consumer_verification", "consumer_outcome", "authority_identity", "source_actor_ids", "reason_codes"}
    if type(value) is not dict or set(value) != fields:
        raise PublicationViolation("promotion has an unknown immutable contract")
    request, sources = _source_proof(publisher, mechanism_record, use)
    before = {**facts, "reuse_promotions": []}
    actual = inspect_verification_record(value["consumer_verification"])
    outcome = inspect_outcome(value["consumer_outcome"])
    expected = {"schema_version": REUSE_PROMOTION_SCHEMA_V1, "policy_version": REUSE_GUARD_POLICY_V1,
        "from_state": "VERIFIED_REUSABLE_CANDIDATE", "to_state": "OBSERVED_USEFUL_REUSE",
        "behavior_ref": use["behavior_ref"], "publication_ref": use["publication_ref"], "mechanism_use_ref": use["record_ref"],
        "authority_identity": EVALUATOR.to_dict(), "source_actor_ids": sources,
        "reason_codes": ["OBSERVED_EXACT_DISPATCH_AVOIDANCE", "INDEPENDENT_NON_WORSENED_TASK_RESOLUTION"]}
    if (any(value[key] != item for key, item in expected.items()) or actual != before
            or outcome["status"] != "UNRESOLVED" or outcome["verification"] != value["consumer_verification"]
            or actual["manifest_sha256"] != manifest.manifest_sha256 or actual["context_sha256"] != context.context_sha256
            or request["identity"]["manifest_sha256"] == manifest.manifest_sha256):
        raise PublicationViolation("promotion differs from its independent source and downstream verification")
    return [{"record_ref": reference(value, REUSE_PROMOTION_SCHEMA_V1).to_dict(),
             "behavior_ref": value["behavior_ref"], "state": "OBSERVED_USEFUL_REUSE", "mechanism_use_ref": use["record_ref"]}]


def promote_verified_use(*, publisher, session, manifest, context, verification, mechanism_record):
    """Attach independent promotion before completion; never rewrite the producer."""
    facts = require_verification_record(verification).payload()
    if session.store.get(kind=RecordKind.ATTEMPT_RESULT, key=str(context.attempt_index)) is not None:
        raise PublicationViolation("a completed consumer cannot acquire retrospective promotion")
    use = _basis(publisher=publisher, facts=facts)
    if session.store.get(kind=RecordKind.REUSE_PROMOTION, key=str(context.attempt_index)) is not None:
        return read_reuse_promotions(publisher=publisher, store=session.store, manifest=manifest, context=context,
                                    facts=facts, mechanism_record=mechanism_record)
    if facts["reuse_promotions"]:
        raise PublicationViolation("promotion input already claims an uncommitted promotion")
    _, sources = _source_proof(publisher, mechanism_record, use)
    outcome = evaluate_attempt_outcome(verification)
    value = {"schema_version": REUSE_PROMOTION_SCHEMA_V1, "policy_version": REUSE_GUARD_POLICY_V1,
        "from_state": "VERIFIED_REUSABLE_CANDIDATE", "to_state": "OBSERVED_USEFUL_REUSE",
        "behavior_ref": use["behavior_ref"], "publication_ref": use["publication_ref"], "mechanism_use_ref": use["record_ref"],
        "consumer_verification": verification.to_dict(), "consumer_outcome": outcome.to_dict(),
        "authority_identity": EVALUATOR.to_dict(), "source_actor_ids": sources,
        "reason_codes": ["OBSERVED_EXACT_DISPATCH_AVOIDANCE", "INDEPENDENT_NON_WORSENED_TASK_RESOLUTION"]}
    session.put(PendingRunRecord(kind=RecordKind.REUSE_PROMOTION, key=str(context.attempt_index), payload=value))
    return read_reuse_promotions(publisher=publisher, store=session.store, manifest=manifest, context=context,
                                facts=facts, mechanism_record=mechanism_record)
