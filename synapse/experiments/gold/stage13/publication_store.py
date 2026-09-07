"""One atomic write set across the existing Gold stores, with undo recovery.

An immutable prepared transaction retains the pre-write journal prefixes and
metadata images. All participants share one coordinator and one mutation
ticket. The terminal marker follows every required write and read-back.
Recovery rolls back an uncommitted suffix or verifies a committed transaction;
it never evaluates new authority or repeats an already committed publication.
"""

from __future__ import annotations

from ..stage14.publication import publication_graph
from ..runner.records import RecordKind
from ..stage14.graph import LineageGraph, LINEAGE_SCHEMA_V1, record_reference

import base64
from dataclasses import dataclass, replace
import hashlib
import re
from pathlib import Path

from synapse.resource_usage import observed_operation

from .. import admission as A, library_admission as LA
from ..admission_journal import FileSnapshotFence
from ..canonicalization import HashBoundRef, RefKind
from ..behavior import behavior_unit_from_dict
from ..contracts import LifecycleReasonCode, record_id_reference_from_dict
from ..lifecycle import LifecycleState
from ..library import IndexEntry, LibraryJournalRecord
from ..persistence import (
    MAX_METADATA_BYTES_V1, append_journal_payload,
    commit_snapshot_transaction, committed_transaction_exists, ensure_directory,
    initialize_journal, new_operation_id, publish_immutable, read_regular_bytes,
    read_committed_snapshot_transaction, require_directory, require_regular_file, require_store_commit,
    restore_uncommitted_file, scan_journal, stage_snapshot_transaction,
    store_transaction, truncate_journal_to_valid_prefix, write_staged_bytes, verify_transaction_members,
)
from ..provenance import behavior_attestation_to_ref
from ..stage10.context_codec import decode_canonical, encode_canonical
from ..stage12.reusable import REUSABLE_CANDIDATE_SCHEMA_V2
from .publication import (PublicationAuthority, PublicationRequest, PublicationViolation, reference,
    inspect_publication_decision, SOURCE_REQUEST_V1)


PUBLICATION_RESULT_V3 = "synapse.stage4.gold.publication-result/v3"
_PREPARED_V2 = "synapse.stage4.gold.publication-undo/v2"
_JOURNAL_LIMIT = 256 * 1024 * 1024
QUARANTINE_SCHEMA_V1 = "synapse.stage4.gold.publication-quarantine/v1"
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


def _quarantine_payload(recovery):
    return {"schema_version": QUARANTINE_SCHEMA_V1, "transaction_id": recovery["transaction_id"],
        "state": "ROLLED_BACK", "request_ref": recovery["request_ref"],
        "request_identity": recovery["request_identity"], "interval_epoch": recovery["undo"]["interval_epoch"],
        "undo_sha256": hashlib.sha256(encode_canonical(recovery["undo"])).hexdigest()}


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


def _verify_write_set(request, decision, result, undo, index, *, project_root):
    """Reconcile actual journal suffixes and the whole index with exact authority."""
    starts = {item["path"]: item["length"] for item in undo["members"] if item["kind"] == "journal"}
    ends = {item["path"]: item["byte_length"] for item in result["participants"] if item["prefix"]}
    if set(starts) != _JOURNALS or set(ends) != _JOURNALS:
        raise PublicationViolation("publication journal boundaries are incomplete")
    rows, prior = {}, {}
    for path in sorted(_JOURNALS):
        scan = scan_journal(project_root / path, create_if_missing=False)
        if starts[path] > ends[path] or scan.valid_prefix_length < ends[path]:
            raise PublicationViolation("publication journal interval is incomplete")
        rows[path] = [decode_canonical(frame.payload) for frame in scan.frames
                      if starts[path] <= frame.start_offset and frame.end_offset <= ends[path]]
        prior[path] = [decode_canonical(frame.payload) for frame in scan.frames if frame.end_offset <= starts[path]]
    attestations = rows["attestations/behavior-attestations-v1.journal"]
    if attestations != [request["attestation"]] and not (
            attestations == [] and request["attestation"] in prior["attestations/behavior-attestations-v1.journal"]):
        raise PublicationViolation("publication wrote a different provenance set")
    taint = rows["taint/taint-history-v1.journal"]
    if taint:
        if len(taint) != 1 or taint[0]["kind"] != "SOURCE_PROFILE" or taint[0]["payload"] != request["taint"]:
            raise PublicationViolation("publication changed taint outside its authorized profile")
    elif not any(item["kind"] == "SOURCE_PROFILE" and item["payload"] == request["taint"]
                 for item in prior["taint/taint-history-v1.journal"]):
        raise PublicationViolation("publication lost its effective taint basis")
    lifecycle = rows["lifecycle/lifecycle-v1.journal"]
    predecessor = None
    if len(lifecycle) != len(_STATES):
        raise PublicationViolation("publication changed additional lifecycle records")
    lifecycle_refs = []
    for entry, (state, reason) in zip(lifecycle, _STATES):
        payload = entry["payload"]
        if (entry["kind"] != "RECORD" or payload["subject_ref"] != decision["attestation_ref"]
                or payload["context"] != decision["lifecycle_context"] or payload["to_state"] != state.value
                or payload["reason_code"] != reason.value or payload["predecessor_record_id"] != predecessor):
            raise PublicationViolation("publication lifecycle differs from the authorized transition")
        predecessor = record_id_reference_from_dict(payload["record_id"]).value
        lifecycle_refs.append(reference(payload, payload["schema_version"]).to_dict())
    if predecessor != result["lifecycle_record_id"]:
        raise PublicationViolation("publication reports a different lifecycle head")
    gates = rows["admission/decisions.journal"]
    if gates != [decision["ingestion"], decision["publication"]]:
        raise PublicationViolation("publication changed additional admission decisions")
    operations = [LibraryJournalRecord.from_dict(item) for item in rows["library/journal/library.v1"]]
    if operations and len({item.operation_id for item in operations}) != 1:
        raise PublicationViolation("publication added multiple physical library operations")
    if operations and [item.phase.value for item in operations] != ["BEGIN", "BLOB_STAGED", "MANIFEST_STAGED", "BLOB_PUBLISHED",
            "MANIFEST_PUBLISHED", "METADATA_PUBLISHED", "COMMITTED", "CLEANED"]:
        raise PublicationViolation("publication library operation did not complete exactly once")
    expected_manifest = request["manifest"]["manifest_id"]["digest_sha256"]
    expected_blob = behavior_unit_from_dict(request["unit"]).content_key.digest_sha256
    if not operations and not any(item["phase"] == "COMMITTED"
            and item["blob_ref"]["digest_sha256"] == expected_blob
            and item["manifest_ref"]["digest_sha256"] == expected_manifest
            for item in prior["library/journal/library.v1"]):
        raise PublicationViolation("deduplicated publication has no pre-existing committed content")
    if any(item.blob_ref.digest_sha256 != expected_blob or item.manifest_ref.digest_sha256 != expected_manifest
           or item.publisher_component_id != decision["publisher_identity"]["component_id"] for item in operations):
        raise PublicationViolation("publication wrote an unauthorized library subject")
    before = next(item["bytes"] for item in undo["members"] if item["path"] == "library/metadata/index.v1")
    previous = [] if before is None else decode_canonical(base64.b64decode(before, validate=True))["entries"]
    expected_entries = {item["manifest_id"]: item for item in previous}
    if not operations and not any(item["manifest_ref"]["digest_sha256"] == expected_manifest for item in previous):
        raise PublicationViolation("deduplicated publication has no pre-existing index entry")
    actual_entries = [IndexEntry.from_dict(item) for item in index["entries"]]
    selected = [item for item in actual_entries if item.manifest_ref.digest_sha256 == expected_manifest]
    if len(selected) != 1:
        raise PublicationViolation("publication index lacks its exact authorized entry")
    entry = selected[0]
    visibility = decision["index_visibility"]
    if (entry.content_key != visibility["content_key"] or entry.manifest_id != visibility["manifest_id"]
            or entry.behavior_kind != visibility["behavior_kind"] or entry.blob_ref.digest_sha256 != expected_blob
            or entry.lifecycle_pointer is not None or visibility["searchable"] is not True):
        raise PublicationViolation("publication index widened its exact metadata or visibility")
    expected_entries[entry.manifest_id] = entry.to_dict()
    if len(actual_entries) != len(expected_entries) or {item.manifest_id: item.to_dict() for item in actual_entries} != expected_entries:
        raise PublicationViolation("publication changed index entries outside its authorized write set")
    object_refs = {}
    for kind, digest in (("blob", expected_blob), ("manifest", expected_manifest)):
        raw = read_regular_bytes(project_root / "library" / "objects" / (kind + "s") / digest[:2] / digest[2:],
                                 maximum_bytes=_JOURNAL_LIMIT)
        value = decode_canonical(raw)
        object_refs[kind] = reference(value, value["schema_version"]).to_dict()
    return {**object_refs, "attestation": decision["attestation_ref"], "lifecycle": lifecycle_refs,
            "admission": [result["registration"][name]["ref"] for name in ("ingestion", "publication")],
            "index": reference(entry.to_dict(), entry.schema_version).to_dict()}


@dataclass(frozen=True)
class PublicationResult:
    root: Path
    transaction_id: str

    def payload(self):
        marker, members = read_committed_snapshot_transaction(self.root / "committed", transaction_id=self.transaction_id)
        if set(members) != {"decision.json", "result.json", "index.json", "lineage.json"}:
            raise PublicationViolation("publication has an incomplete committed member set")
        result = decode_canonical(members["result.json"])
        decision = decode_canonical(members["decision.json"])
        if (result["schema_version"] != PUBLICATION_RESULT_V3 or result["transaction_id"] != self.transaction_id
                or result["decision_ref"] != reference(decision, decision["schema_version"]).to_dict()
                or marker["boundary_id"] != result["decision_ref"]["sha256"]
                or marker["marker_sha256"] != hashlib.sha256(members["result.json"]).hexdigest()
                or decision["decision_kind"] != "AUTHORIZE_PUBLICATION"):
            raise PublicationViolation("publication result differs from its terminal authority")
        prepared_marker, prepared = read_committed_snapshot_transaction(self.root / "prepared", transaction_id=self.transaction_id)
        request = decode_canonical(prepared["request.json"])
        evidence_refs = request["evidence_refs"]
        if (set(prepared) != {"request.json", "undo.json", "lineage-sources.json", *(ref["sha256"] for ref in evidence_refs)}
                or result["request_ref"] != reference(decode_canonical(prepared["request.json"])).to_dict()
                or prepared_marker["marker_sha256"] != result["undo_sha256"]
                or decision["request_ref"] != result["request_ref"]
                or hashlib.sha256(prepared["undo.json"]).hexdigest() != result["undo_sha256"]
                or decision["sequence"] != result["interval_epoch"]):
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
        retained = {HashBoundRef.from_dict(ref): prepared[ref["sha256"]] for ref in evidence_refs}
        inspect_publication_decision(decision, request=request, registration=registration, retained_evidence=retained)
        _verify_participants(request, decision, result["participants"], project_root=self.root.parent)
        created = _verify_write_set(request, decision, result, decode_canonical(prepared["undo.json"]),
                                   decode_canonical(members["index.json"]), project_root=self.root.parent)
        if result["created_refs"] != created:
            raise PublicationViolation("publication created references differ from its physical records")
        graph = LineageGraph.from_dict(decode_canonical(members["lineage.json"]))
        expected = publication_graph(request=request, decision=decision, created_refs=created,
                                     source_catalog=decode_canonical(prepared["lineage-sources.json"]))
        if (graph.to_dict() != expected.to_dict()
                or result.get("lineage_ref") != record_reference(graph.to_dict(), LINEAGE_SCHEMA_V1).to_dict()):
            raise PublicationViolation("publication lineage differs from physical verification")
        return result

    @property
    def reference(self):
        return reference(self.payload(), PUBLICATION_RESULT_V3)


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

    def read_quarantine(self, transaction_id: str) -> dict[str, object]:
        """Read rollback evidence bound to its original durable opening frame."""
        if type(transaction_id) is not str or re.fullmatch(r"pub-[0-9a-f]{64}", transaction_id) is None:
            raise PublicationViolation("quarantine transaction identity is malformed")
        fence = self.authority.stores.fence
        with fence.exclusive() as guard:
            raw = read_regular_bytes(self.root / "quarantine" / (transaction_id + ".json"), maximum_bytes=MAX_METADATA_BYTES_V1)
            value = decode_canonical(raw)
            opening = fence.recovery_payload(value["interval_epoch"], guard=guard)
            if opening is None:
                raise PublicationViolation("quarantine has no original recovery authority")
            recovery = decode_canonical(opening)
            if (value != _quarantine_payload(recovery) or value["transaction_id"] != transaction_id
                    or fence.current_epoch() <= value["interval_epoch"]
                    or committed_transaction_exists(self.root / "committed", transaction_id=transaction_id)):
                raise PublicationViolation("quarantine differs from its completed rollback")
            return value

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
        return {"schema_version": _PREPARED_V2, "coordinator_id": stores.fence.coordinator_id(),
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

    @observed_operation("publication.commit")
    def publish(self, request: PublicationRequest) -> PublicationResult | None:
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
            facts = value["verification"]["payload"]
            source_origin = value["schema_version"] == SOURCE_REQUEST_V1
            if source_origin:
                source_catalog = {"schema_version": "synapse.stage4.gold.source-lineage-catalog/v1",
                    "verification_ref": value["verification"]["verification_ref"], "evidence_refs": value["evidence_refs"]}
            else:
                if stores.source_run_store is None:
                    raise PublicationViolation("attempt publication requires its original run store")
                sources = stores.source_run_store.get(kind=RecordKind.LINEAGE_SOURCES, key=facts["attempt_id"])
                if sources is None or sources.sha256 != facts["phase_refs"]["lineage_sources_sha256"]:
                    raise PublicationViolation("publication lost its bound execution source catalog")
                source_catalog = sources.payload
            undo = self._undo(request, stores.fence.current_epoch() + 1)
            recovery = {"schema_version": _PREPARED_V2, "transaction_id": tx,
                        "request_ref": reference(value).to_dict(), "request_identity": value["identity"], "undo": undo}
            with store_transaction(stores.fence, guard=guard, recovery_payload=encode_canonical(recovery)) as ticket:
                raw_request, raw_undo = encode_canonical(value), encode_canonical(undo)
                members = stage_snapshot_transaction(self.root / "prepared", transaction_id=tx,
                    members={"request.json": raw_request, "undo.json": raw_undo,
                             "lineage-sources.json": encode_canonical(source_catalog),
                             **{ref.sha256: raw for ref, raw in request.evidence}}, ticket=ticket)
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
                for path in self._paths(request).values():
                    require_regular_file(path)
                    require_store_commit(path, fence=stores.fence, ticket=ticket)
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
                registration = {"schema_version": "synapse.stage4.gold.source-candidate/v1" if source_origin else REUSABLE_CANDIDATE_SCHEMA_V2,
                    "publication_transaction": {"transaction_id": tx, "decision_ref": decision.reference.to_dict()},
                    **({"source_verification_ref": value["verification"]["verification_ref"]} if source_origin else {
                        "manifest_sha256": value["identity"]["manifest_sha256"], "context_sha256": value["identity"]["context_sha256"]}),
                    "unit": request.unit.to_dict(), "manifest_id": request.manifest.manifest_id.to_dict(),
                    "attestation": request.attestation.to_dict(), "domain": value["domain"],
                    "lifecycle_context": request.context.to_dict(), "journal_anchor": write.receipts[-1].journal_anchor,
                    "journal_sequence": stores.admission_journal.record_position(A.gate_decision_ref(write.publication).sha256) + 1,
                    **{name: {"ref": A.gate_decision_ref(gate).to_dict(), "record": decode_canonical(gate.canonical_bytes())}
                       for name, gate in (("ingestion", write.ingestion), ("publication", write.publication))}}
                result = {"schema_version": PUBLICATION_RESULT_V3, "transaction_id": tx,
                    "request_identity": value["identity"], "request_ref": reference(value).to_dict(),
                    "decision_ref": decision.reference.to_dict(), "registration": registration,
                    "committed_subjects": sorted([subject.sha256, attestation_ref.sha256]),
                    "lifecycle_record_id": head.record_id.value,
                    "taint_profile_id": request.taint.profile_id.value,
                    "interval_epoch": ticket.interval_epoch, "undo_sha256": hashlib.sha256(raw_undo).hexdigest(),
                    "participants": self._committed_members(request, undo)}
                inspect_publication_decision(decision_value, request=value, registration=registration,
                                             retained_evidence=dict(request.evidence))
                _verify_participants(value, decision_value, result["participants"], project_root=self.root.parent)
                raw_index = read_regular_bytes(stores.library.root / "metadata" / "index.v1", maximum_bytes=MAX_METADATA_BYTES_V1)
                result["created_refs"] = _verify_write_set(value, decision_value, result, undo,
                    decode_canonical(raw_index), project_root=self.root.parent)
                lineage = publication_graph(request=value, decision=decision_value, created_refs=result["created_refs"],
                                            source_catalog=source_catalog)
                result["lineage_ref"] = record_reference(lineage.to_dict(), LINEAGE_SCHEMA_V1).to_dict()
                raw_result = encode_canonical(result)
                members = stage_snapshot_transaction(self.root / "committed", transaction_id=tx,
                    members={"decision.json": encode_canonical(decision_value), "result.json": raw_result,
                             "index.json": raw_index, "lineage.json": encode_canonical(lineage.to_dict())}, ticket=ticket)
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


@observed_operation("publication.recover")
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
            if (set(recovery) != {"schema_version", "transaction_id", "request_ref", "request_identity", "undo"}
                    or recovery["schema_version"] != _PREPARED_V2):
                raise PublicationViolation("unfinished interval belongs to another recovery owner")
            tx, undo = recovery["transaction_id"], recovery["undo"]
            if (re.fullmatch(r"pub-[0-9a-f]{64}", tx) is None or undo["schema_version"] != _PREPARED_V2
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
                    value = encode_canonical(_quarantine_payload(recovery))
                    if quarantine.exists():
                        if read_regular_bytes(quarantine, maximum_bytes=MAX_METADATA_BYTES_V1) != value:
                            raise PublicationViolation("quarantine identity changed during recovery")
                    else:
                        _write(quarantine, value, ticket)
                journal = root / "publication.journal"
                scan = scan_journal(journal)
                if scan.torn_tail:
                    truncate_journal_to_valid_prefix(journal, scan.valid_prefix_length)
                payload = encode_canonical({"transaction_id": tx, "phase": "COMMITTED" if committed else "ROLLED_BACK"})
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
