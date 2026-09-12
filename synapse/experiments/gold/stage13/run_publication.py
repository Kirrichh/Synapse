"""Attach publication decisions and physical results to canonical C1 completion."""

from ..canonicalization import HashBoundRef
from ..contracts import record_id_reference_from_dict
from ..persistence import committed_transaction_exists, read_committed_snapshot_transaction
from ..stage10.context_codec import decode_canonical
from ..runner.records import RecordKind
from ..runner.run_recovery import PendingRunRecord
from ..stage12.reusable import register_verified_reusable_output
from .publication_store import PublicationResult, PublicationStore, QUARANTINE_SCHEMA_V1, PUBLICATION_RESULT_V3
from .publication import REQUEST_SCHEMA_V3, REQUEST_SCHEMA_V4, PublicationAuthorityDecision, PublicationViolation, reference


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


def read_project_run_knowledge(*, state_root, task, origins=None):
    """Project verified run outputs into a new task's immutable candidate basis.

    The archive keeps every attempt. This projection admits nothing: it reads
    actual committed publication proof, and the normal consumer gates still
    evaluate every exported behavior. Explicit origins reopen historical input
    without adding knowledge published later.
    """
    root = state_root / "publications"
    if origins is None:
        transactions = [] if not (root / "committed").exists() else [
            path.name for path in sorted((root / "committed").iterdir())
            if committed_transaction_exists(root / "committed", transaction_id=path.name)]
    else:
        if type(origins) is not list or any(type(item) is not dict or set(item) != {"transaction_id", "result_ref"}
                                           for item in origins):
            raise PublicationViolation("run knowledge origins have an unknown contract")
        transactions = [item["transaction_id"] for item in origins]
        if transactions != sorted(set(transactions)):
            raise PublicationViolation("run knowledge origins are repeated or unordered")
    candidates, files, captured, publications = [], {}, [], {}
    for index, transaction_id in enumerate(transactions):
        _, prepared = read_committed_snapshot_transaction(root / "prepared", transaction_id=transaction_id)
        request = decode_canonical(prepared["request.json"])
        if request["schema_version"] not in {REQUEST_SCHEMA_V3, REQUEST_SCHEMA_V4}:
            if origins is not None:
                raise PublicationViolation("run knowledge origin names a source publication")
            continue
        published = PublicationResult(root, transaction_id)
        result = published.payload()
        domain = request["domain"]
        matches = (domain["base_revision"] == task.repository_revision_sha256
                   and domain["task_contract_ref"] == task.reference.to_dict())
        if not matches:
            if origins is not None:
                raise PublicationViolation("retained run knowledge belongs to another task or revision")
            continue
        # Bind the exact bytes just verified; reopening .reference would verify
        # the whole producer lineage again and could name another read.
        origin = {"transaction_id": transaction_id, "result_ref": reference(result, PUBLICATION_RESULT_V3).to_dict()}
        if origins is not None and origin != origins[index]:
            raise PublicationViolation("run knowledge publication changed its original identity")
        registration = result["registration"]
        candidate = {"unit": registration["unit"], "manifest_id": registration["manifest_id"],
            "attestation": registration["attestation"], "bindings": [],
            "lifecycle_context": registration["lifecycle_context"],
            "taint": {"profiles": [request["taint"]], "derivations": [], "decisions": [],
                      "root_id": record_id_reference_from_dict(request["taint"]["profile_id"]).value}}
        # Current run extraction publishes exact patch observations without code
        # bindings. Future extraction profiles must supply their actual bindings.
        if request["manifest"]["binding_refs"]:
            raise PublicationViolation("run knowledge export has no resolver for this binding profile")
        candidates.append(candidate)
        captured.append(origin)
        publications[candidate["unit"]["content_key"]["value"]] = (published, candidate)
        for raw_ref in request["evidence_refs"]:
            ref = HashBoundRef.from_dict(raw_ref)
            files[ref] = {"ref": raw_ref, "path": str(root / "prepared" / transaction_id / ref.sha256)}
    return {"origins": captured, "candidates": candidates, "files": list(files.values()), "publications": publications}


def assess_retained_publication_pair(left_publication, left_evidence, right_publication, right_evidence):
    """Compare the exact assertions of independently committed run/source facts.

    Supported source facts describe the original committed bytes or a command
    observed on them. An exact negative guard describes a task outcome after a
    particular patch. Neither asserts that the other's observation succeeded or
    failed, and neither performs combined effects. Two negative guards retain
    two verified exclusions, rather than competing positive solutions.

    Unknown assertion kinds require an assessor of their own. Merely having a
    publication, matching labels or missing a declared conflict proves nothing.
    """
    from ..behavior import VerificationResultClass
    from ..compatibility import ConflictKind
    from .publication import SOURCE_REQUEST_V1, SOURCE_REQUEST_SCHEMAS
    from .rejected_patch_profile import (
        REJECTED_PATCH_GUARD_V3, REJECTED_PATCH_GUARD_V4, REJECTED_PATCH_GUARD_V5, VERIFIED_PATCH_GUARD_V1)

    claims, refs = [], set()
    for publication, evidence in ((left_publication, left_evidence), (right_publication, right_evidence)):
        if type(publication) is not PublicationResult:
            raise PublicationViolation("pair assessment requires physical committed publications")
        result = publication.payload()
        if (result["registration"]["unit"] != evidence.unit.to_dict()
                or result["registration"]["attestation"] != evidence.attestation.to_dict()):
            raise PublicationViolation("pair assessment names another retained candidate")
        _, prepared = read_committed_snapshot_transaction(publication.root / "prepared",
                                                        transaction_id=publication.transaction_id)
        request = decode_canonical(prepared["request.json"])
        domain = request["domain"]
        refs.add(HashBoundRef.from_dict(request["verification"]["verification_ref"]))
        refs.add(reference(result, PUBLICATION_RESULT_V3))
        if request["schema_version"] in SOURCE_REQUEST_SCHEMAS:
            if domain["kind"] not in {"REPOSITORY_FACT_CHECK", "VERIFICATION_RECIPE"}:
                raise PublicationViolation("source assertion has no run-knowledge pair assessor")
            claims.append(("ORIGINAL_SOURCE_OBSERVATION", domain["revision"], None))
        elif request["schema_version"] in {REQUEST_SCHEMA_V3, REQUEST_SCHEMA_V4}:
            contract = evidence.unit.core.verification_contract
            facts = request["verification"]["payload"]["c1"]
            positive = request["schema_version"] == REQUEST_SCHEMA_V4
            profiles = {VERIFIED_PATCH_GUARD_V1} if positive else {
                REJECTED_PATCH_GUARD_V3, REJECTED_PATCH_GUARD_V4, REJECTED_PATCH_GUARD_V5}
            result_class = VerificationResultClass.CONTRACT_SATISFIED if positive else VerificationResultClass.BEHAVIOR_REJECTED
            if (contract.profile_id not in profiles or contract.expected_result_class is not result_class
                    or facts["oracle_resolved"] is not positive or facts["infra_error"] or facts["refused"]
                    or facts["verified_revision"] == domain["base_revision"]):
                raise PublicationViolation("run assertion has no supported independent outcome proof")
            claims.append(("PATCHED_TASK_POSITIVE" if positive else "PATCHED_TASK_NEGATIVE", domain["base_revision"],
                           {key: value for key, value in domain.items() if key != "schema_version"}))
        else:
            raise PublicationViolation("unknown publication assertion cannot acquire a no-conflict finding")
    if claims[0][1] != claims[1][1] or all(kind == "ORIGINAL_SOURCE_OBSERVATION" for kind, _, _ in claims):
        raise PublicationViolation("pair requires its source or cross-revision assertion assessor")
    contradiction = (claims[0][2] is not None and claims[0][2] == claims[1][2] and claims[0][0] != claims[1][0])
    return (ConflictKind.CONTRADICTORY_EVIDENCE if contradiction else None,
            tuple(sorted(refs, key=lambda ref: (ref.kind.value, ref.ref_id, ref.sha256))))
