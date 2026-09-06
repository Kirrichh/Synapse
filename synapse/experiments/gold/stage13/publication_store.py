"""One atomic write set across the existing Gold stores, with undo recovery.

An immutable prepared transaction retains the pre-write journal prefixes and
metadata images. All participants share one coordinator and one mutation
ticket. The terminal marker follows every required write and read-back.
Recovery rolls back an uncommitted suffix or verifies a committed transaction;
it never evaluates new authority or repeats an already committed publication.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
import hashlib
import re
from pathlib import Path

from .. import admission as A, library_admission as LA
from ..admission_journal import FileSnapshotFence
from ..canonicalization import RefKind
from ..behavior import behavior_unit_from_dict
from ..contracts import LifecycleReasonCode
from ..lifecycle import LifecycleState
from ..persistence import (
    MAX_METADATA_BYTES_V1, append_journal_payload,
    commit_snapshot_transaction, committed_transaction_exists, ensure_directory,
    initialize_journal, new_operation_id, publish_immutable, read_regular_bytes,
    read_committed_snapshot_transaction, require_directory, require_regular_file,
    restore_uncommitted_file, scan_journal, stage_snapshot_transaction,
    store_transaction, truncate_journal_to_valid_prefix, write_staged_bytes, verify_transaction_members,
)
from ..provenance import behavior_attestation_to_ref
from ..stage10.context_codec import decode_canonical, encode_canonical
from ..stage12.reusable import REUSABLE_CANDIDATE_SCHEMA_V2
from .publication import PublicationAuthority, PublicationRequest, PublicationViolation, reference, inspect_publication_decision


PUBLICATION_RESULT_V1 = "synapse.stage4.gold.publication-result/v1"
_PREPARED_V1 = "synapse.stage4.gold.publication-undo/v1"
_JOURNAL_LIMIT = 256 * 1024 * 1024
_JOURNALS = frozenset({"library/journal/library.v1", "lifecycle/lifecycle-v1.journal",
    "attestations/behavior-attestations-v1.journal", "admission/decisions.journal", "taint/taint-history-v1.journal"})
_STATES = (
    (LifecycleState.OBSERVED, LifecycleReasonCode.PLATFORM_OBSERVATION),
    (LifecycleState.EXTRACTED, LifecycleReasonCode.EXTRACTION_COMPLETED),
    (LifecycleState.DISTILLED, LifecycleReasonCode.DISTILLATION_COMPLETED),
    (LifecycleState.VALIDATED, LifecycleReasonCode.VALIDATION_PASSED),
    (LifecycleState.ATTESTED, LifecycleReasonCode.ATTESTATION_BOUND),
    (LifecycleState.ADMITTED, LifecycleReasonCode.PUBLICATION_ADMITTED),
    (LifecycleState.INDEXED, LifecycleReasonCode.INDEX_COMMITTED),
)


def _write(path, raw, ticket):
    ensure_directory(path.parent)
    staged = write_staged_bytes(path.parent, final_name=path.name, operation_id=new_operation_id(),
                                value=raw, maximum_bytes=MAX_METADATA_BYTES_V1, ticket=ticket)
    publish_immutable(staged, path, ticket=ticket)


def _safe_member(root, relative):
    if (type(relative) is not str or Path(relative).is_absolute() or ".." in Path(relative).parts
            or not Path(relative).parts or Path(relative).parts[0] not in {"library", "lifecycle", "attestations", "admission", "taint"}):
        raise PublicationViolation("recovery member is outside its participant stores")
    allowed = _JOURNALS | {"library/metadata/index.v1", "library/metadata/integrity.v1"}
    if relative not in allowed and re.fullmatch(
            r"(?:library/metadata|lifecycle|attestations)/commit-requirements/[0-9a-f]{64}\.json", relative) is None:
        raise PublicationViolation("recovery names a file outside the exact publication participants")
    path = root / relative
    for parent in reversed(path.parents):
        if parent == root or root in parent.parents:
            require_directory(parent)
    return path


def _verify_participants(request, decision, members, *, project_root):
    blob = behavior_unit_from_dict(request["unit"]).content_key.digest_sha256
    manifest = request["manifest"]["manifest_id"]["digest_sha256"]
    attestation = decision["attestation_ref"]["sha256"]
    required = _JOURNALS | {
        f"library/objects/blobs/{blob[:2]}/{blob[2:]}",
        f"library/objects/manifests/{manifest[:2]}/{manifest[2:]}",
        f"library/metadata/commit-requirements/{manifest}.json",
        f"lifecycle/commit-requirements/{attestation}.json",
        f"attestations/commit-requirements/{attestation}.json",
    }
    if {item["path"] for item in members} != required:
        raise PublicationViolation("publication omitted or added physical participants")
    verify_transaction_members(project_root, members)


@dataclass(frozen=True)
class PublicationResult:
    root: Path
    transaction_id: str

    def payload(self):
        marker, members = read_committed_snapshot_transaction(self.root / "committed", transaction_id=self.transaction_id)
        if set(members) != {"decision.json", "result.json"}:
            raise PublicationViolation("publication has an incomplete committed member set")
        result = decode_canonical(members["result.json"])
        decision = decode_canonical(members["decision.json"])
        if (result["schema_version"] != PUBLICATION_RESULT_V1 or result["transaction_id"] != self.transaction_id
                or result["decision_ref"] != reference(decision, decision["schema_version"]).to_dict()
                or marker["boundary_id"] != result["decision_ref"]["sha256"]
                or marker["marker_sha256"] != hashlib.sha256(members["result.json"]).hexdigest()
                or decision["decision_kind"] != "AUTHORIZE_PUBLICATION"):
            raise PublicationViolation("publication result differs from its terminal authority")
        prepared_marker, prepared = read_committed_snapshot_transaction(self.root / "prepared", transaction_id=self.transaction_id)
        request = decode_canonical(prepared["request.json"])
        evidence_refs = request["evidence_refs"]
        if (set(prepared) != {"request.json", *(ref["sha256"] for ref in evidence_refs)}
                or result["request_ref"] != reference(decode_canonical(prepared["request.json"])).to_dict()
                or prepared_marker["marker_sha256"] != result["undo_sha256"]
                or decision["request_ref"] != result["request_ref"]):
            raise PublicationViolation("publication lost its verified request or write-ahead record")
        for ref in evidence_refs:
            raw = prepared[ref["sha256"]]
            if len(raw) != ref["byte_length"] or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
                raise PublicationViolation("publication retained evidence differs from its verified reference")
        if result["committed_subjects"] != sorted([decision["subject_ref"]["sha256"], decision["attestation_ref"]["sha256"]]):
            raise PublicationViolation("publication changed its authorized subject set")
        registration = result["registration"]
        for name in ("ingestion", "publication"):
            if registration[name]["record"] != decision[name]:
                raise PublicationViolation("publication registration changed its gate authority")
        inspect_publication_decision(decision, request=decode_canonical(prepared["request.json"]), registration=registration)
        _verify_participants(request, decision, result["participants"], project_root=self.root.parent)
        return result

    @property
    def reference(self):
        return reference(self.payload(), PUBLICATION_RESULT_V1)


class PublicationStore:
    def __init__(self, *, root: Path, authority: PublicationAuthority):
        if type(authority) is not PublicationAuthority:
            raise TypeError("publication requires its configured independent authority")
        authority.validate()
        self.root = root
        self.authority = authority
        for path in (root, root / "prepared", root / "committed", root / "quarantine"):
            ensure_directory(path)
        self.journal = root / "publication.journal"
        initialize_journal(self.journal)

    def _phase(self, transaction_id, phase, ticket):
        append_journal_payload(self.journal, encode_canonical({"transaction_id": transaction_id, "phase": phase}), ticket=ticket)

    def _paths(self, request):
        stores = self.authority.stores
        attestation = behavior_attestation_to_ref(request.attestation)
        return {
            "library": stores.library.root / "metadata" / "commit-requirements" / (request.manifest.manifest_id.digest_sha256 + ".json"),
            "lifecycle": stores.lifecycle_store._root / "commit-requirements" / (attestation.sha256 + ".json"),
            "provenance": stores.attestation_store._root / "commit-requirements" / (attestation.sha256 + ".json"),
        }

    def _undo(self, request, interval_epoch):
        stores = self.authority.stores
        project = self.root.parent
        journals = (stores.library._journal_path, stores.lifecycle_store._journal_path,
                    stores.attestation_store._journal_path, stores.admission_journal.path,
                    self.authority.taint_store._journal_path)
        metadata = (stores.library.root / "metadata" / "index.v1", stores.library.root / "metadata" / "integrity.v1",
                    *self._paths(request).values())
        records = []
        for path in journals:
            initialize_journal(path)
            raw = read_regular_bytes(path, maximum_bytes=_JOURNAL_LIMIT)
            records.append({"path": str(path.relative_to(project)), "kind": "journal", "length": len(raw),
                            "sha256": hashlib.sha256(raw).hexdigest()})
        for path in metadata:
            ensure_directory(path.parent)
            raw = read_regular_bytes(path, maximum_bytes=MAX_METADATA_BYTES_V1) if path.exists() else None
            records.append({"path": str(path.relative_to(project)), "kind": "metadata",
                            "bytes": None if raw is None else base64.b64encode(raw).decode("ascii")})
        return {"schema_version": _PREPARED_V1, "coordinator_id": stores.fence.coordinator_id(),
                "interval_epoch": interval_epoch, "members": records}

    def _committed_members(self, request, undo):
        project = self.root.parent
        members = []
        paths = [(project / item["path"], True) for item in undo["members"] if item["kind"] == "journal"]
        for kind, digest in (("blobs", request.unit.content_key.digest_sha256),
                             ("manifests", request.manifest.manifest_id.digest_sha256)):
            paths.append((self.authority.stores.library.root / "objects" / kind / digest[:2] / digest[2:], False))
        paths.extend((path, False) for path in self._paths(request).values() if path.exists())
        for path, prefix in paths:
            raw = read_regular_bytes(path, maximum_bytes=_JOURNAL_LIMIT)
            members.append({"path": str(path.relative_to(project)), "byte_length": len(raw),
                            "sha256": hashlib.sha256(raw).hexdigest(), "prefix": prefix})
        verify_transaction_members(project, members)
        return members

    def _append_states(self, request, states, *, predecessor, ticket, evidence_ref):
        stores = self.authority.stores
        subject = behavior_attestation_to_ref(request.attestation)
        for state, reason in states:
            predecessor = stores.lifecycle_store.append(authority_handle=stores.authority_handle,
                subject_ref=subject, context=request.context, to_state=state, reason_code=reason,
                evidence_refs=(evidence_ref,), expected_predecessor_record_id=None if predecessor is None else predecessor.record_id.value,
                expected_subject_sequence=1 if predecessor is None else predecessor.subject_sequence + 1,
                mutation_ticket=ticket)
        return predecessor

    def publish(self, request):
        """Prepare proof, obtain independent authority, write exactly it, commit last."""
        if type(request) is not PublicationRequest:
            raise TypeError("publication accepts only independently derived candidates")
        value = request.payload()
        tx = request.transaction_id
        stores = self.authority.stores
        self.authority.validate()
        with stores.fence.exclusive() as guard:
            if committed_transaction_exists(self.root / "committed", transaction_id=tx):
                result = PublicationResult(self.root, tx)
                prior = result.payload()
                if prior["request_identity"] != value["identity"]:
                    raise PublicationViolation("idempotency identity differs from committed request")
                return result
            if (self.root / "quarantine" / (tx + ".json")).exists():
                return None
            if (self.root / "prepared" / tx).exists():
                raise PublicationViolation("unfinished publication requires project recovery before retry")
            if stores.fence.current_epoch() % 2:
                raise PublicationViolation("project has an abandoned authority interval")
            undo = self._undo(request, stores.fence.current_epoch() + 1)
            recovery = {"schema_version": _PREPARED_V1, "transaction_id": tx,
                        "request_ref": reference(value).to_dict(), "undo": undo}
            with store_transaction(stores.fence, guard=guard, recovery_payload=encode_canonical(recovery)) as ticket:
                raw_request, raw_undo = encode_canonical(value), encode_canonical(undo)
                members = stage_snapshot_transaction(self.root / "prepared", transaction_id=tx,
                    members={"request.json": raw_request, **{ref.sha256: raw for ref, raw in request.evidence}}, ticket=ticket)
                commit_snapshot_transaction(self.root / "prepared", transaction_id=tx, members=members,
                    boundary_id=reference(value).sha256, marker_sha256=hashlib.sha256(raw_undo).hexdigest(), ticket=ticket)
                self._phase(tx, "PREPARED", ticket)
                stores.attestation_store.append(authority_handle=stores.authority_handle, attestation=request.attestation, mutation_ticket=ticket)
                self._phase(tx, "ATTESTATION", ticket)
                existing_taint_ids = {item[2] for item in self.authority.taint_store._entries()}
                if request.taint.profile_id.value not in existing_taint_ids:
                    self.authority.taint_store.append_profile(authority_handle=stores.authority_handle, profile=request.taint, mutation_ticket=ticket)
                head = self._append_states(request, _STATES[:5], predecessor=None, ticket=ticket,
                                           evidence_ref=behavior_attestation_to_ref(request.attestation))
                self._phase(tx, "ATTESTED", ticket)
                decision = self.authority.evaluate(request, mutation_ticket=ticket)
                decision_value = decision.payload()
                if (decision_value["transaction_id"] != tx or decision_value["request_ref"] != reference(value).to_dict()
                        or decision_value["required_transition"] != ["ATTESTED", "ADMITTED", "INDEXED"]):
                    raise PublicationViolation("executor received a different transaction contract")
                self._phase(tx, "AUTHORIZED", ticket)
                subject = LA.write_subject_ref(content_key=request.unit.content_key, manifest_id=request.manifest.manifest_id)
                attestation_ref = behavior_attestation_to_ref(request.attestation)
                for name, path in self._paths(request).items():
                    if path.exists():
                        # Existing content remains governed by its original commit.
                        continue
                    requirement = {"schema_version": "synapse.store-commit-requirement/v1",
                        "root": str(self.root / "committed"), "transaction_id": tx,
                        "decision_sha256": decision.reference.sha256,
                        "subject_sha256": subject.sha256 if name == "library" else attestation_ref.sha256,
                        "coordinator_id": stores.fence.coordinator_id(), "interval_epoch": ticket.interval_epoch}
                    _write(path, encode_canonical(requirement), ticket)
                write = LA.admit_library_write(decision.prepared_write.authority, unit=request.unit,
                    blob=request.blob, manifest=request.manifest, requested=decision.prepared_write.requested,
                    prepared_decisions=decision.prepared_write, coordinator_guard=guard, mutation_ticket=ticket)
                self._phase(tx, "LIBRARY", ticket)
                head = self._append_states(request, _STATES[5:], predecessor=head, ticket=ticket,
                                           evidence_ref=replace(decision.reference, kind=RefKind.SOURCE_EVIDENCE))
                self._phase(tx, "INDEXED", ticket)
                loaded = stores.library.get_verified_behavior(request.unit.content_key, request.manifest.manifest_id, mutation_ticket=ticket)
                if loaded.unit.to_dict() != request.unit.to_dict() or loaded.manifest != request.manifest:
                    raise PublicationViolation("published bytes differ from the verified candidate")
                stores.lifecycle_store.require_consumable(subject_ref=attestation_ref, context=request.context, mutation_ticket=ticket)
                for gate, receipt in zip((write.ingestion, write.publication), write.receipts):
                    A.require_committed_decision(receipt, decision=gate, journal=stores.admission_journal)
                registration = {"schema_version": REUSABLE_CANDIDATE_SCHEMA_V2,
                    "publication_transaction": {"transaction_id": tx, "decision_ref": decision.reference.to_dict()},
                    "manifest_sha256": value["identity"]["manifest_sha256"], "context_sha256": value["identity"]["context_sha256"],
                    "unit": request.unit.to_dict(), "manifest_id": request.manifest.manifest_id.to_dict(),
                    "attestation": request.attestation.to_dict(), "domain": value["domain"],
                    "lifecycle_context": request.context.to_dict(), "journal_anchor": write.receipts[-1].journal_anchor,
                    "journal_sequence": stores.admission_journal.record_position(A.gate_decision_ref(write.publication).sha256) + 1,
                    **{name: {"ref": A.gate_decision_ref(gate).to_dict(), "record": decode_canonical(gate.canonical_bytes())}
                       for name, gate in (("ingestion", write.ingestion), ("publication", write.publication))}}
                result = {"schema_version": PUBLICATION_RESULT_V1, "transaction_id": tx,
                    "request_identity": value["identity"], "request_ref": reference(value).to_dict(),
                    "decision_ref": decision.reference.to_dict(), "registration": registration,
                    "committed_subjects": sorted([subject.sha256, attestation_ref.sha256]),
                    "lifecycle_record_id": head.record_id.value,
                    "taint_profile_id": request.taint.profile_id.value,
                    "interval_epoch": ticket.interval_epoch, "undo_sha256": hashlib.sha256(raw_undo).hexdigest(),
                    "participants": self._committed_members(request, undo)}
                inspect_publication_decision(decision_value, request=value, registration=registration)
                _verify_participants(value, decision_value, result["participants"], project_root=self.root.parent)
                raw_result = encode_canonical(result)
                members = stage_snapshot_transaction(self.root / "committed", transaction_id=tx,
                    members={"decision.json": encode_canonical(decision_value), "result.json": raw_result}, ticket=ticket)
                self._phase(tx, "VERIFIED", ticket)
                commit_snapshot_transaction(self.root / "committed", transaction_id=tx, members=members,
                    boundary_id=decision.reference.sha256, marker_sha256=hashlib.sha256(raw_result).hexdigest(), ticket=ticket)
                self._phase(tx, "COMMITTED", ticket)
            LA.complete_library_write(write, fence=stores.fence)
        result = PublicationResult(self.root, tx)
        result.payload()
        return result


def _restore_publication(project_root, undo, *, ticket):
    restored = []
    paths = set()
    for member in undo["members"]:
        path = _safe_member(project_root, member["path"])
        if path in paths:
            raise PublicationViolation("recovery duplicates a participant")
        paths.add(path)
        if member["kind"] == "journal":
            raw = read_regular_bytes(path, maximum_bytes=_JOURNAL_LIMIT)
            if len(raw) < member["length"] or hashlib.sha256(raw[:member["length"]]).hexdigest() != member["sha256"]:
                raise PublicationViolation("rollback would alter a pre-existing committed prefix")
        elif member["kind"] != "metadata":
            raise PublicationViolation("rollback member has an unknown kind")
        restored.append((member, path))
    for member, path in restored:
        if member["kind"] == "journal":
            truncate_journal_to_valid_prefix(path, member["length"])
        else:
            restore_uncommitted_file(path, previous_bytes=None if member["bytes"] is None
                else base64.b64decode(member["bytes"], validate=True), ticket=ticket)
    for member, path in restored:
        raw = read_regular_bytes(path, maximum_bytes=_JOURNAL_LIMIT) if path.exists() else None
        if member["kind"] == "journal":
            matches = raw is not None and len(raw) == member["length"] and hashlib.sha256(raw).hexdigest() == member["sha256"]
        else:
            matches = raw == (None if member["bytes"] is None else base64.b64decode(member["bytes"], validate=True))
        if not matches:
            raise PublicationViolation("rollback read-back differs from the durable write-ahead image")


def recover_project_publications(project_root: Path, *, fence: FileSnapshotFence):
    """Verify commits, or undo the exact interval before participant owners open.

    The opening coordinator frame retains undo before any participant can write,
    including before the prepared request exists. Repeated recovery is safe even
    when recovery itself was interrupted after restoring a subset of members.
    """
    root = project_root / "publications"
    if not root.exists():
        return ()
    reports = []
    with fence.exclusive() as guard:
        epoch = fence.current_epoch()
        if epoch % 2:
            raw = fence.recovery_payload(epoch, guard=guard)
            if raw is None:
                raise PublicationViolation("unfinished project interval has no publication recovery contract")
            recovery = decode_canonical(raw)
            if (set(recovery) != {"schema_version", "transaction_id", "request_ref", "undo"}
                    or recovery["schema_version"] != _PREPARED_V1):
                raise PublicationViolation("unfinished interval belongs to another recovery owner")
            tx, undo = recovery["transaction_id"], recovery["undo"]
            if (re.fullmatch(r"pub-[0-9a-f]{64}", tx) is None or undo["schema_version"] != _PREPARED_V1
                    or undo["coordinator_id"] != fence.coordinator_id() or undo["interval_epoch"] != epoch):
                raise PublicationViolation("publication undo names another coordinator or interval")
            committed = committed_transaction_exists(root / "committed", transaction_id=tx)
            if committed:
                result = PublicationResult(root, tx).payload()
                if (result["request_ref"] != recovery["request_ref"]
                        or result["undo_sha256"] != hashlib.sha256(encode_canonical(undo)).hexdigest()):
                    raise PublicationViolation("committed publication lost its opening write-ahead contract")
            with fence.resume_abandoned_interval(guard=guard, expected_epoch=epoch) as ticket:
                if not committed:
                    _restore_publication(project_root, undo, ticket=ticket)
                    quarantine = root / "quarantine" / (tx + ".json")
                    value = encode_canonical({"transaction_id": tx, "state": "ROLLED_BACK",
                        "undo_sha256": hashlib.sha256(encode_canonical(undo)).hexdigest()})
                    if quarantine.exists():
                        if read_regular_bytes(quarantine, maximum_bytes=MAX_METADATA_BYTES_V1) != value:
                            raise PublicationViolation("quarantine identity changed during recovery")
                    else:
                        _write(quarantine, value, ticket)
                    journal = root / "publication.journal"
                    scan = scan_journal(journal)
                    if scan.torn_tail:
                        truncate_journal_to_valid_prefix(journal, scan.valid_prefix_length)
                    payload = encode_canonical({"transaction_id": tx, "phase": "ROLLED_BACK"})
                    if not scan.frames or scan.frames[-1].payload != payload:
                        append_journal_payload(journal, payload, ticket=ticket)
                reports.append((tx, "COMMITTED" if committed else "QUARANTINED"))
        for directory in sorted((root / "committed").iterdir()):
            require_directory(directory)
            if committed_transaction_exists(root / "committed", transaction_id=directory.name):
                result = PublicationResult(root, directory.name).payload()
                raw = fence.recovery_payload(result["interval_epoch"], guard=guard)
                if raw is None or hashlib.sha256(encode_canonical(decode_canonical(raw)["undo"])).hexdigest() != result["undo_sha256"]:
                    raise PublicationViolation("committed publication has no original write-ahead contract")
                if (directory.name, "COMMITTED") not in reports:
                    reports.append((directory.name, "COMMITTED"))
    return tuple(reports)
