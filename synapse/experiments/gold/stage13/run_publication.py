"""Attach an atomic publication to the canonical run's durable C1 suffix."""

from ..canonicalization import HashBoundRef
from ..runner.records import RecordKind
from ..runner.run_recovery import PendingRunRecord
from ..stage12.reusable import register_verified_reusable_output
from .publication_store import PublicationResult
from .publication import PublicationViolation


def publish_attempt(*, publisher, session, verification, manifest, context, c1):
    if session.store.get(kind=RecordKind.ATTEMPT_RESULT, key=str(context.attempt_index)) is not None:
        raise PublicationViolation("publication cannot modify a completed attempt")
    request = publisher.authority.prepare(verification=verification, manifest=manifest, context=context, c1=c1)
    if request is None:
        return
    result = publisher.publish(request)
    if result is None:
        return
    payload = result.payload()
    session.put(PendingRunRecord(kind=RecordKind.PUBLICATION_RESULT, key=str(context.attempt_index),
        payload={"transaction_id": result.transaction_id, "result_ref": result.reference.to_dict()}))
    register_verified_reusable_output(session=session, authority=publisher.authority.stores,
        manifest=manifest, context=context, task_contract_ref=HashBoundRef.from_dict(verification.payload()["task_contract_ref"]),
        c1=c1, registration=payload["registration"])



def publication_refs(*, publisher, store, context):
    record = store.get(kind=RecordKind.PUBLICATION_RESULT, key=str(context.attempt_index))
    if record is None:
        return ()
    if publisher is None:
        raise PublicationViolation("publication result lacks its configured physical owner")
    if set(record.payload) != {"transaction_id", "result_ref"}:
        raise PublicationViolation("run publication transport has an unknown shape")
    result = PublicationResult(publisher.root, record.payload["transaction_id"])
    payload = result.payload()
    candidate = store.get(kind=RecordKind.REUSABLE_CANDIDATE, key=str(context.attempt_index))
    if (result.reference.to_dict() != record.payload["result_ref"] or candidate is None
            or candidate.payload != payload["registration"]
            or payload["request_identity"]["context_sha256"] != context.context_sha256):
        raise PublicationViolation("run publication differs from its committed write set")
    return (result.reference,)
