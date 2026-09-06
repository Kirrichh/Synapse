"""Attach publication decisions and physical results to canonical C1 completion."""

from ..canonicalization import HashBoundRef
from ..runner.records import RecordKind
from ..runner.run_recovery import PendingRunRecord
from ..stage12.reusable import register_verified_reusable_output
from .publication_store import PublicationResult, PublicationStore, QUARANTINE_SCHEMA_V1
from .publication import PublicationAuthorityDecision, PublicationViolation, reference


def publish_attempt(*, publisher, session, verification, manifest, context, c1):
    if session.store.get(kind=RecordKind.ATTEMPT_RESULT, key=str(context.attempt_index)) is not None:
        raise PublicationViolation("publication cannot modify a completed attempt")
    existing = session.store.get(kind=RecordKind.PUBLICATION_RESULT, key=str(context.attempt_index))
    if existing is not None:
        if existing.payload["state"] != "COMMITTED":
            read_publication_outcome(publisher=publisher, store=session.store, manifest=manifest,
                                     context=context, facts=verification.payload())
            return
        result = PublicationResult(publisher.root, existing.payload["transaction_id"])
        if result.reference.to_dict() != existing.payload["result_ref"]:
            raise PublicationViolation("run publication differs from its committed transaction")
    else:
        proposed = publisher.authority.prepare(verification=verification, manifest=manifest, context=context, c1=c1)
        if type(proposed) is PublicationAuthorityDecision:
            decision = proposed.payload()
            publisher.authority.inspect_refusal(decision, manifest=manifest, context=context)
            state = {"REJECT_PUBLICATION": "REJECTED", "QUARANTINE": "QUARANTINED",
                     "REQUIRE_HUMAN_REVIEW": "REVIEW_REQUIRED"}[decision["decision_kind"]]
            session.put(PendingRunRecord(kind=RecordKind.PUBLICATION_RESULT, key=str(context.attempt_index),
                payload={"state": state, "transaction_id": None, "result_ref": proposed.reference.to_dict(), "decision": decision}))
            return
        result = publisher.publish(proposed)
        if result is None:
            quarantine = publisher.read_quarantine(proposed.transaction_id)
            session.put(PendingRunRecord(kind=RecordKind.PUBLICATION_RESULT, key=str(context.attempt_index),
                payload={"state": "QUARANTINED", "transaction_id": proposed.transaction_id,
                         "result_ref": reference(quarantine, QUARANTINE_SCHEMA_V1).to_dict(), "decision": None}))
            return
        session.put(PendingRunRecord(kind=RecordKind.PUBLICATION_RESULT, key=str(context.attempt_index),
            payload={"state": "COMMITTED", "transaction_id": result.transaction_id,
                     "result_ref": result.reference.to_dict(), "decision": None}))
    payload = result.payload()
    register_verified_reusable_output(session=session, authority=publisher.authority.stores,
        manifest=manifest, context=context, task_contract_ref=HashBoundRef.from_dict(verification.payload()["task_contract_ref"]),
        c1=c1, registration=payload["registration"])


def read_publication_outcome(*, publisher, store, manifest, context, facts=None):
    """Reopen the actual result; a run label alone is never publication proof."""
    record = store.get(kind=RecordKind.PUBLICATION_RESULT, key=str(context.attempt_index))
    if record is None:
        return None
    if type(publisher) is not PublicationStore:
        raise PublicationViolation("publication result lacks its configured physical owner")
    publisher.authority.validate()
    value = record.payload
    if set(value) != {"state", "transaction_id", "result_ref", "decision"}:
        raise PublicationViolation("run publication transport has an unknown shape")
    candidate = store.get(kind=RecordKind.REUSABLE_CANDIDATE, key=str(context.attempt_index))
    state = value["state"]
    decision_ref = None
    retained_facts = None
    if state == "COMMITTED":
        result = PublicationResult(publisher.root, value["transaction_id"])
        payload = result.payload()
        if value["decision"] is not None or candidate is None or candidate.payload != payload["registration"]:
            raise PublicationViolation("committed publication lacks its verified run registration")
        expected_ref = result.reference
        identity = payload["request_identity"]
        decision_ref = payload["decision_ref"]
        reasons = ["ATOMIC_PUBLICATION_COMMITTED"]
    elif value["transaction_id"] is not None:
        if state != "QUARANTINED" or candidate is not None or value["decision"] is not None:
            raise PublicationViolation("rollback result cannot claim an admitted output")
        payload = publisher.read_quarantine(value["transaction_id"])
        expected_ref = reference(payload, QUARANTINE_SCHEMA_V1)
        identity = payload["request_identity"]
        reasons = ["INTERRUPTED_TRANSACTION_ROLLED_BACK"]
    else:
        if candidate is not None:
            raise PublicationViolation("refusal cannot carry an admitted output")
        decision = value["decision"]
        retained_facts = publisher.authority.inspect_refusal(decision, manifest=manifest, context=context)
        expected_state = {"REJECT_PUBLICATION": "REJECTED", "QUARANTINE": "QUARANTINED",
                          "REQUIRE_HUMAN_REVIEW": "REVIEW_REQUIRED"}[decision["decision_kind"]]
        if state != expected_state:
            raise PublicationViolation("run refusal relabels its independent decision")
        expected_ref = reference(decision, decision["schema_version"])
        decision_ref = expected_ref.to_dict()
        identity = retained_facts
        reasons = decision["reason_codes"]
    if (value["result_ref"] != expected_ref.to_dict() or identity["manifest_sha256"] != manifest.manifest_sha256
            or identity["context_sha256"] != context.context_sha256):
        raise PublicationViolation("publication result belongs to different physical proof or execution")
    if facts is not None:
        before = {**facts, "publication": None, "reusable_candidates": []}
        if retained_facts is not None:
            if before != retained_facts:
                raise PublicationViolation("publication refusal no longer matches independent execution verification")
        elif identity["verification_ref"] != reference(before, before["schema_version"]).to_dict():
            raise PublicationViolation("publication no longer matches its independent pre-publication verification")
    return {"state": state, "result_ref": expected_ref.to_dict(), "decision_ref": decision_ref, "reason_codes": reasons}


def publication_refs(*, publisher, store, manifest, context):
    result = read_publication_outcome(publisher=publisher, store=store, manifest=manifest, context=context)
    return () if result is None else (HashBoundRef.from_dict(result["result_ref"]),)
