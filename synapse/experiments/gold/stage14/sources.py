"""LIN-04: bind and reopen the actual upstream stores of one attempt.

The catalog retains locations, references and original coordinator identities,
never execution authority or copied replay payloads. Its digest is part of the
attempt context. All reconstruction reopens physical records using their owners.
"""

from pathlib import Path
from dataclasses import replace
import hashlib

from ..admission import gate_decision_ref, gate_decision_from_dict
from ..admission_journal import FileSnapshotFence, FileAdmissionJournal
from ..admission_store import FileAdmissionCausalStore
from ..canonicalization import HashBoundRef, RefKind
from ..compatibility_store import FileCompatibilityStore
from ..contracts import AttemptId, GateKind, LineageEdgeKind
from ..knowledge import snapshot_manifest_ref, atomic_boundary_ref
from ..knowledge_store import AuthoritativeKnowledgeStore
from ..library import IndexEntry, LibraryObjectRef, RetentionRootSet, RetentionRootKind, LIBRARY_RETENTION_ROOTS_V1, LibraryObjectNamespace, LibraryJournalRecord, LibraryJournalPhase
from ..persistence import require_directory, read_regular_bytes, scan_journal, read_committed_snapshot_transaction
from ..replay import replay_result_ref
from ..replay_store import FileReplayStore
from ..retrieval import retrieval_causal_record_ref, index_entry_subject_ref
from ..stage10.context_codec import decode_canonical
from ..stage10.record_store import plan_preparation_references
from .graph import (
    GraphBuilder, LineageGraph, LineageNodeClass, LineageViolation, LineageFailureCode, LINEAGE_SCHEMA_V1,
    canonical, record_reference,
)

SOURCE_SCHEMA = "synapse.stage4.gold.lineage-sources/v3"
_SOURCE_SCHEMA_V5 = "synapse.stage4.gold.lineage-sources/v5"
_SOURCE_SCHEMA_V4 = "synapse.stage4.gold.lineage-sources/v4"
_SOURCE_SCHEMA_V2 = "synapse.stage4.gold.lineage-sources/v2"


def _location(path, fence):
    if type(fence) is not FileSnapshotFence:
        raise LineageViolation(LineageFailureCode.TYPE_MISMATCH, "lineage requires a durable coordinator")
    return {"path": str(path.resolve()), "coordinator": str(fence.directory.resolve()),
            "coordinator_id": fence.coordinator_id()}


def _reopen_location(value):
    if type(value) is not dict or set(value) != {"path", "coordinator", "coordinator_id"}:
        raise LineageViolation(LineageFailureCode.TYPE_MISMATCH, "source location has unknown fields")
    path, directory = Path(value["path"]), Path(value["coordinator"])
    if not path.is_absolute() or not directory.is_absolute():
        raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "source location must be absolute")
    require_directory(directory)
    require_directory(path.parent)
    fence = FileSnapshotFence(directory, read_only=True)
    if fence.coordinator_id() != value["coordinator_id"]:
        raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "source coordinator identity changed")
    return path, fence


def _retained_history_source(store, fence):
    """Bind owner-validated history to its exact retained physical prefix."""
    anchor = store.current_anchor()
    path = store.source_path
    scanned = scan_journal(path, create_if_missing=False)
    raw = read_regular_bytes(path, maximum_bytes=128 * 1024 * 1024)
    if scanned.torn_tail or scanned.valid_prefix_length != len(raw) or len(scanned.frames) != anchor.entry_count:
        raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "history moved during source capture")
    digest = hashlib.sha256(raw).hexdigest()
    return {"location": _location(path, fence), "anchor": anchor.to_dict(),
            "prefix_ref": HashBoundRef(RefKind.SOURCE_EVIDENCE, digest,
                "synapse.stage4.gold.retained-journal/v1", digest, len(raw), "application/octet-stream").to_dict()}


def capture_sources(*, environment, replay_store, replay_result, causal_record, gate, source_experience_origin=None):
    """Called at the real input boundary after governed replay has completed."""
    if type(replay_store) is not FileReplayStore:
        raise LineageViolation(LineageFailureCode.TYPE_MISMATCH, "lineage requires the actual replay store")
    run_id, attempt_id = replay_result.envelope.run_id.value, replay_result.envelope.attempt_id.value
    catalog = {
        "schema_version": SOURCE_SCHEMA, "run_id": run_id, "attempt_id": attempt_id,
        "execution_stores": None,
        "snapshot_ref": environment.knowledge_snapshot_ref.to_dict(),
        "boundary_ref": environment.admitted_handle.boundary_ref.to_dict(),
        "consumer_ref": environment.consumer_context_ref.to_dict(),
        "retrieval_ref": retrieval_causal_record_ref(causal_record).to_dict(),
        "retrieval_gate_ref": gate_decision_ref(gate).to_dict(),
        "replay_ref": replay_result_ref(replay_result).to_dict(),
        "library": _location(environment.library.root / "journal" / "library.v1", environment.library.mutation_fence),
        "knowledge": _location(environment.knowledge_store.path, environment.knowledge_store.mutation_fence),
        "replay": _location(replay_store.journal_path, replay_store.mutation_fence),
        "compatibility": _location(environment.compatibility_history.path, environment.compatibility_history.mutation_fence),
        "admission": _location(environment.admission_journal.path, environment.admission_journal.mutation_fence),
        "causal": _location(environment.admission_causal_history.path, environment.admission_causal_history.mutation_fence),
        "artifacts": [],
        "snapshot_sources": {
            "repository_root": str(environment.repo_root.resolve()),
            "lifecycle": _retained_history_source(environment.lifecycle_store, environment.library.mutation_fence),
            "provenance": _retained_history_source(environment.attestation_store, environment.library.mutation_fence),
            "taint": _retained_history_source(environment.taint_store, environment.library.mutation_fence),
        },
    }
    # Library files are already content-addressed. Retain their exact physical
    # references in the same input catalog; the graph only carries these refs.
    for entry in environment.library.search_index():
        if index_entry_subject_ref(entry) not in environment.subjects:
            continue
        for namespace, ref in (("blobs", entry.blob_ref), ("manifests", entry.manifest_ref)):
            path = environment.library.root / "objects" / namespace / ref.digest_sha256[:2] / ref.digest_sha256[2:]
            raw = read_regular_bytes(path, maximum_bytes=16 * 1024 * 1024)
            value = decode_canonical(raw)
            actual = replace(record_reference(value, value["schema_version"]), ref_id=ref.digest_sha256)
            catalog["artifacts"].append({"path": str(path.resolve()), "ref": actual.to_dict(),
                                         "class": "BEHAVIOR_BLOB" if namespace == "blobs" else "BEHAVIOR_MANIFEST",
                                         "object_ref": ref.to_dict(), "entry": entry.to_dict()})
    origins = []
    for unit, descriptor, entry in environment.supported:
        if index_entry_subject_ref(entry) not in environment.subjects:
            continue
        evidence = environment.evidence_resolver(descriptor)
        if evidence.source_publication is None:
            continue
        root, transaction_id, result_ref = evidence.source_publication
        origins.append({"publication_root": root, "transaction_id": transaction_id,
            "result_ref": result_ref.to_dict(), "subject_ref": index_entry_subject_ref(entry).to_dict(),
            "evidence_refs": [ref.to_dict() for ref, _ in evidence.source_evidence]})
    if origins:
        catalog["schema_version"] = _SOURCE_SCHEMA_V4
        catalog["source_publications"] = origins
    if source_experience_origin is not None:
        catalog["schema_version"] = _SOURCE_SCHEMA_V5
        catalog["source_publications"] = origins
        catalog["source_experience"] = source_experience_origin
    read_input_graph(catalog)
    return catalog


def _read_journal_ref(path, reference):
    scanned = scan_journal(path, create_if_missing=False)
    if scanned.torn_tail:
        raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "source journal is torn")
    for frame in scanned.frames:
        raw = frame.payload
        if hashlib.sha256(raw).hexdigest() == reference.sha256 and len(raw) == reference.byte_length:
            return decode_canonical(raw)
    raise LineageViolation(LineageFailureCode.MISSING_RECORD, "referenced source journal record is absent")


def read_consumption_gate(catalog, *, decision_id, subject_refs, policy_version):
    """Resolve the decision actually named by a consumer, without renewing it.

    A record identity in a replay request is not its physical evidence. Read
    the committed journal frame and let the admission owner verify its identity.
    Identical decisions can occur more than once in the journal.
    """
    path, _ = _reopen_location(catalog["admission"])
    scanned = scan_journal(path, create_if_missing=False)
    if scanned.torn_tail:
        raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "admission journal is torn")
    for frame in scanned.frames:
        value = decode_canonical(frame.payload)
        if value.get("envelope", {}).get("record_id") != decision_id:
            continue
        reference = HashBoundRef(RefKind.GATE_DECISION, decision_id["digest_sha256"],
            value["payload"]["schema_version"], hashlib.sha256(frame.payload).hexdigest(),
            len(frame.payload), "application/json")
        gate = gate_decision_from_dict(value, expected_ref=reference)
        if (gate.gate_kind is not GateKind.CONSUMPTION or not gate.admitted
                or gate.envelope.run_id.value != catalog["run_id"]
                or gate.envelope.attempt_id.value != catalog["attempt_id"]
                or gate.boundary_ref.to_dict() != catalog["boundary_ref"]
                or gate.consumer_context_ref.to_dict() != catalog["consumer_ref"]
                or set(gate.subject_refs) != {HashBoundRef.from_dict(item) for item in subject_refs}
                or gate.policy_version != policy_version):
            raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "consumption decision differs from its use")
        return gate
    raise LineageViolation(LineageFailureCode.MISSING_RECORD, "consumed admission decision is absent")


def read_input_graph(catalog):
    required = {"schema_version", "run_id", "attempt_id", "snapshot_ref", "boundary_ref", "consumer_ref",
                "retrieval_ref", "retrieval_gate_ref", "replay_ref", "knowledge", "replay", "compatibility",
                "admission", "causal", "artifacts", "execution_stores", "library"}
    if type(catalog) is dict and catalog.get("schema_version") in {SOURCE_SCHEMA, _SOURCE_SCHEMA_V4, _SOURCE_SCHEMA_V5}:
        required.add("snapshot_sources")
    if type(catalog) is dict and catalog.get("schema_version") in {_SOURCE_SCHEMA_V4, _SOURCE_SCHEMA_V5}:
        required.add("source_publications")
    if type(catalog) is dict and catalog.get("schema_version") == _SOURCE_SCHEMA_V5:
        required.add("source_experience")
    if type(catalog) is not dict or set(catalog) != required or catalog["schema_version"] not in {SOURCE_SCHEMA, _SOURCE_SCHEMA_V2, _SOURCE_SCHEMA_V4, _SOURCE_SCHEMA_V5}:
        raise LineageViolation(LineageFailureCode.TYPE_MISMATCH, "unknown lineage source catalog")
    path, fence = _reopen_location(catalog["knowledge"])
    snapshot = AuthoritativeKnowledgeStore(path.parent, mutation_fence=fence, read_only=True).open_for_attempt(AttemptId(catalog["attempt_id"]))
    if (snapshot_manifest_ref(snapshot.manifest).to_dict() != catalog["snapshot_ref"]
            or atomic_boundary_ref(snapshot.boundary).to_dict() != catalog["boundary_ref"]):
        raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "source snapshot differs from its occurrence")
    path, fence = _reopen_location(catalog["replay"])
    replay_store = FileReplayStore(path.parent, mutation_fence=fence, read_only=True)
    replay = replay_store.require_result(HashBoundRef.from_dict(catalog["replay_ref"]))
    request = replay_store.request_record(replay.request_ref)["payload"]
    if (request["snapshot_manifest_ref"] != catalog["snapshot_ref"]
            or request["boundary_ref"] != catalog["boundary_ref"]
            or request["consumer_context_ref"] != catalog["consumer_ref"]):
        raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "replay request changed its input boundary")
    if (replay.envelope.run_id.value != catalog["run_id"] or replay.envelope.attempt_id.value != catalog["attempt_id"]
            or replay.knowledge_snapshot_id != catalog["snapshot_ref"]["ref_id"]):
        raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "replay source belongs to another occurrence")
    path, fence = _reopen_location(catalog["compatibility"])
    compatibility = FileCompatibilityStore(path.parent, mutation_fence=fence, read_only=True)
    consumer_ref = HashBoundRef.from_dict(catalog["consumer_ref"])
    compatibility.resolve_ref(consumer_ref)
    gate_ref = HashBoundRef.from_dict(catalog["retrieval_gate_ref"])
    path, admission_fence = _reopen_location(catalog["admission"])
    admission_history = FileAdmissionJournal(path, mutation_fence=admission_fence, read_only=True)
    gate = gate_decision_from_dict(_read_journal_ref(path, gate_ref), expected_ref=gate_ref)
    path, causal_fence = _reopen_location(catalog["causal"])
    causal_store = FileAdmissionCausalStore(path.parent, mutation_fence=causal_fence, admission_history=admission_history, read_only=True)
    causal = decode_canonical(causal_store.resolve_ref(HashBoundRef.from_dict(catalog["retrieval_ref"])))
    facts = causal["payload"]
    if facts["boundary_ref"] != catalog["boundary_ref"] or facts["retrieval_gate_decision_ref"] != catalog["retrieval_gate_ref"]:
        raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "retrieval changed its governing evidence")
    builder = GraphBuilder("inputs/v1", catalog["run_id"], catalog["attempt_id"])
    for role, kind, ref in (
        ("boundary", LineageNodeClass.SNAPSHOT_BOUNDARY, catalog["boundary_ref"]),
        ("snapshot", LineageNodeClass.KNOWLEDGE_SNAPSHOT, catalog["snapshot_ref"]),
        ("consumer", LineageNodeClass.CONSUMER_CONTEXT, catalog["consumer_ref"]),
        ("retrieval_gate", LineageNodeClass.ADMISSION_DECISION, catalog["retrieval_gate_ref"]),
        ("retrieval", LineageNodeClass.RETRIEVAL_DECISION, catalog["retrieval_ref"]),
        ("replay_result", LineageNodeClass.REPLAY_RESULT, catalog["replay_ref"]),
    ):
        builder.add(role, kind, HashBoundRef.from_dict(ref))
    builder.add("replay_request", LineageNodeClass.REPLAY_REQUEST, replay.request_ref)
    consumption = read_consumption_gate(catalog, decision_id=request["consumption_decision_id"],
        subject_refs=request["knowledge_subject_refs"], policy_version=request["policy_version"])
    builder.add("replay_consumption_gate", LineageNodeClass.ADMISSION_DECISION, gate_decision_ref(consumption))
    manifest_ref = HashBoundRef.from_dict(request["execution_manifest_ref"])
    manifest = replay_store.require_manifest(manifest_ref)
    replay_store.require_capture(manifest.source_capture_ref)
    builder.add("replay_manifest", LineageNodeClass.REPLAY_MANIFEST, manifest_ref)
    builder.add("reference_capture", LineageNodeClass.REFERENCE_CAPTURE, manifest.source_capture_ref)
    builder.link("reference_capture", LineageEdgeKind.DERIVED_FROM, "replay_manifest")
    builder.link("replay_manifest", LineageEdgeKind.DERIVED_FROM, "replay_request")
    for index, reference in enumerate(manifest.initial_snapshot_refs):
        replay_store.open_snapshot(reference)
        role = f"initial_vm.{index}"
        builder.add(role, LineageNodeClass.VM_SNAPSHOT, reference)
        builder.link(role, LineageEdgeKind.DERIVED_FROM, "replay_manifest")
    for index, reference in enumerate(manifest.expected_structural_history_refs):
        replay_store.open_structural_history(reference)
        role = f"structural_history.{index}"
        builder.add(role, LineageNodeClass.STRUCTURAL_HISTORY, reference)
        builder.link(role, LineageEdgeKind.DERIVED_FROM, "replay_manifest")
    for index, binding in enumerate(request["bindings"]):
        role = f"compiler_binding.{index}"
        builder.record(role, LineageNodeClass.BINDING, binding, "synapse.stage4.gold.lineage-compiler-binding/v1")
        builder.link(role, LineageEdgeKind.DERIVED_FROM, "replay_request")
    # Ranking/frozen-set records are resolved through the same retained owner.
    for name in ("retrieval_decision_ref", "frozen_candidate_set_ref"):
        reference = HashBoundRef.from_dict(facts[name])
        causal_store.resolve_ref(reference)
        builder.add(name, LineageNodeClass.FROZEN_CANDIDATES if name == "frozen_candidate_set_ref" else LineageNodeClass.RETRIEVAL_DECISION, reference)
        builder.link(name, LineageEdgeKind.DERIVED_FROM, "replay_request")
    library_path, _ = _reopen_location(catalog["library"])
    journal = scan_journal(library_path, create_if_missing=False)
    if journal.torn_tail:
        raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "library history is torn")
    committed = {}
    for frame in journal.frames:
        record = LibraryJournalRecord.from_dict(decode_canonical(frame.payload))
        if record.phase is LibraryJournalPhase.COMMITTED:
            committed[(record.blob_ref, record.manifest_ref)] = record
    actual_subjects = set()
    source_members = set()
    for index, item in enumerate(catalog["artifacts"]):
        if type(item) is not dict or set(item) != {"path", "ref", "class", "object_ref", "entry"}:
            raise LineageViolation(LineageFailureCode.TYPE_MISMATCH, "library source has unknown fields")
        entry = IndexEntry.from_dict(item["entry"])
        native = LibraryObjectRef.from_dict(item["object_ref"])
        expected_native = entry.blob_ref if item["class"] == "BEHAVIOR_BLOB" else entry.manifest_ref
        member = (entry.manifest_id, native.namespace)
        if native != expected_native or member in source_members:
            raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "library source identity is inconsistent")
        source_members.add(member)
        actual_subjects.add(index_entry_subject_ref(entry))
        reference = HashBoundRef.from_dict(item["ref"])
        committed_pair = committed.get((entry.blob_ref, entry.manifest_ref))
        if committed_pair is None:
            raise LineageViolation(LineageFailureCode.MISSING_RECORD, "library source lacks its committed pair")
        expected_hash = committed_pair.blob_sha256 if native.namespace is LibraryObjectNamespace.BLOB else committed_pair.manifest_sha256
        if reference.sha256 != expected_hash or reference.ref_id != native.digest_sha256:
            raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "library reference differs from its committed content")
        raw = read_regular_bytes(Path(item["path"]), maximum_bytes=16 * 1024 * 1024)
        if len(raw) != reference.byte_length or hashlib.sha256(raw).hexdigest() != reference.sha256:
            raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "retained library source changed")
        role = f"artifact.{index}"
        builder.add(role, LineageNodeClass(item["class"]), reference)
        builder.link(role, LineageEdgeKind.DERIVED_FROM, "snapshot")
    if actual_subjects != set(snapshot.manifest.behavior_refs) or len(source_members) != 2 * len(actual_subjects):
        raise LineageViolation(LineageFailureCode.MISSING_RECORD, "snapshot library sources are incomplete")
    for index, source in enumerate(read_source_publications(catalog)):
        if HashBoundRef.from_dict(source["origin"]["subject_ref"]) not in actual_subjects:
            raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "source origin belongs to another library subject")
        prefix = f"source_origin.{index}"
        builder.add(prefix, LineageNodeClass.PUBLICATION_RESULT, HashBoundRef.from_dict(source["origin"]["result_ref"]))
        builder.link(prefix, LineageEdgeKind.DERIVED_FROM, "snapshot")
        for ordinal, raw_ref in enumerate(source["origin"]["evidence_refs"]):
            role = f"{prefix}.evidence.{ordinal}"
            builder.add(role, LineageNodeClass.SOURCE_EVIDENCE, HashBoundRef.from_dict(raw_ref))
            builder.link(role, LineageEdgeKind.DERIVED_FROM, prefix)
    for index, reference in enumerate(snapshot.compatibility_evidence_manifest.compatibility_refs):
        compatibility.resolve_ref(reference)
        role = f"compatibility.{index}"
        builder.add(role, LineageNodeClass.COMPATIBILITY_EVIDENCE, reference)
        builder.link(role, LineageEdgeKind.DERIVED_FROM, "snapshot")
    for index, observation in enumerate(replay.observations):
        replay_store.open_snapshot(observation.terminal_snapshot_ref)
        role = f"vm.{index}"
        builder.add(role, LineageNodeClass.VM_SNAPSHOT, observation.terminal_snapshot_ref)
        builder.link(role, LineageEdgeKind.DERIVED_FROM, "replay_result")
    if "source_experience" in catalog:
        from ..source_snapshot import read_frozen_source_experience
        experience = read_frozen_source_experience(catalog["source_experience"], run_id=catalog["run_id"])
        builder.add("source_experience", LineageNodeClass.SOURCE_EXPERIENCE,
                    HashBoundRef.from_dict(catalog["source_experience"]["snapshot_ref"]))
        builder.add("frozen_inputs", LineageNodeClass.FROZEN_INPUTS,
                    HashBoundRef.from_dict(catalog["source_experience"]["ref"]))
        builder.link("source_experience", LineageEdgeKind.DERIVED_FROM, "frozen_inputs")
        for index, selected in enumerate(experience["recall"]["selected"]):
            for ordinal, retained in enumerate(selected["experience"]["retained"]):
                role = f"experience.{index}.evidence.{ordinal}"
                builder.add(role, LineageNodeClass.SOURCE_EVIDENCE, HashBoundRef.from_dict(retained["ref"]))
                builder.link(role, LineageEdgeKind.DERIVED_FROM, "source_experience")
    builder.link_roles()
    return builder.finish()


def read_source_publications(catalog):
    """Reopen source-origin bytes; admission remains the publication owner's job.

    Execution reconstruction additionally opens PublicationResult to validate
    the atomic participant set. This reader binds the original physical bytes
    into the input snapshot, including runs interrupted before a context exists.
    """
    from ..behavior import behavior_unit_from_dict, create_behavior_blob, create_behavior_manifest, compile_behavior_unit
    from ..canonicalization import library_subject_ref
    from ..source_verification import inspect_source_verification
    if catalog["schema_version"] not in {_SOURCE_SCHEMA_V4, _SOURCE_SCHEMA_V5}:
        return ()
    origins, opened, subjects = catalog["source_publications"], [], set()
    if type(origins) is not list or not origins and catalog["schema_version"] == _SOURCE_SCHEMA_V4:
        raise LineageViolation(LineageFailureCode.MISSING_RECORD, "source catalog has no origins")
    for origin in origins:
        if type(origin) is not dict or set(origin) != {"publication_root", "transaction_id", "result_ref", "subject_ref", "evidence_refs"}:
            raise LineageViolation(LineageFailureCode.TYPE_MISMATCH, "unknown source origin")
        root = Path(origin["publication_root"])
        if not root.is_absolute():
            raise LineageViolation(LineageFailureCode.TYPE_MISMATCH, "source publication is not an absolute location")
        _, committed = read_committed_snapshot_transaction(root / "committed", transaction_id=origin["transaction_id"])
        result = decode_canonical(committed["result.json"])
        if record_reference(result, result["schema_version"]).to_dict() != origin["result_ref"]:
            raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "source publication result changed")
        _, prepared = read_committed_snapshot_transaction(root / "prepared", transaction_id=origin["transaction_id"])
        request = decode_canonical(prepared["request.json"])
        if request["evidence_refs"] != origin["evidence_refs"]:
            raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "source publication evidence set changed")
        retained = {HashBoundRef.from_dict(ref): prepared[ref["sha256"]] for ref in origin["evidence_refs"]}
        facts = inspect_source_verification(request["verification"]["payload"], evidence=retained)
        unit = behavior_unit_from_dict(request["unit"])
        blob = create_behavior_blob(unit)
        manifest = create_behavior_manifest(unit, blob, compiler_binding=compile_behavior_unit(unit))
        subject = library_subject_ref(content_key=unit.content_key.value, manifest_id=manifest.manifest_id.value,
            blob_digest_sha256=unit.content_key.digest_sha256, manifest_digest_sha256=manifest.manifest_id.digest_sha256)
        if subject.to_dict() != origin["subject_ref"] or subject in subjects:
            raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "source origin changed its admitted subject")
        subjects.add(subject)
        opened.append({"origin": origin, "facts": facts, "request": request})
    return tuple(opened)


def read_run_publications(catalog):
    """Read the exact run-publication origins retained in a frozen source slice."""
    if "source_experience" not in catalog:
        return ()
    from ..source_snapshot import read_frozen_source_experience
    snapshot = read_frozen_source_experience(catalog["source_experience"], run_id=catalog["run_id"])
    root = Path(snapshot["project_state_root"]) / "publications"
    opened = []
    for origin in snapshot.get("run_publications", ()):
        _, committed = read_committed_snapshot_transaction(root / "committed", transaction_id=origin["transaction_id"])
        result = decode_canonical(committed["result.json"])
        if record_reference(result, result["schema_version"]).to_dict() != origin["result_ref"]:
            raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "run publication origin changed")
        _, prepared = read_committed_snapshot_transaction(root / "prepared", transaction_id=origin["transaction_id"])
        graph = LineageGraph.from_dict(decode_canonical(committed["lineage.json"]))
        if result["lineage_ref"] != record_reference(graph.to_dict(), LINEAGE_SCHEMA_V1).to_dict():
            raise LineageViolation(LineageFailureCode.PHYSICAL_MISMATCH, "run publication lost its original lineage")
        request = decode_canonical(prepared["request.json"])
        retained = {HashBoundRef.from_dict(ref): prepared[ref["sha256"]] for ref in request["evidence_refs"]}
        opened.append({"origin": origin, "request": request, "graph": graph, "retained": retained})
    return tuple(opened)


def lineage_retention_roots(catalog, graph, *, root_node_id):
    """Feed verified input reachability into the library's existing GC contract."""
    physical = read_input_graph(catalog)
    actual_nodes = {node.node_id: node for node in graph.ancestors(root_node_id)}
    if not all(actual_nodes.get(node.node_id) == node for node in physical.nodes):
        raise LineageViolation(LineageFailureCode.MISSING_RECORD, "retention root does not reach its input evidence")
    namespaces = {LineageNodeClass.BEHAVIOR_BLOB: LibraryObjectNamespace.BLOB,
                  LineageNodeClass.BEHAVIOR_MANIFEST: LibraryObjectNamespace.MANIFEST}
    roots = tuple(sorted({LibraryObjectRef(namespaces[node.node_class], node.reference.ref_id)
                          for node in actual_nodes.values() if node.node_class in namespaces}))
    return RetentionRootSet(LIBRARY_RETENTION_ROOTS_V1, RetentionRootKind.LINEAGE, roots)


def bind_execution_stores(catalog, *, run_store, stage10_store, intent, accepted_plan):
    """Bind the concrete completion owners before the attempt context is sealed."""
    read_input_graph(catalog)
    if catalog["execution_stores"] is not None:
        raise LineageViolation(LineageFailureCode.IDENTITY_MISMATCH, "execution stores are already bound")
    return {**catalog, "execution_stores": {
        "run": _location(run_store.record_root, run_store.mutation_fence),
        "stage10": _location(stage10_store.record_root, stage10_store.mutation_fence),
        "plan_preparation_refs": [ref.to_dict() for ref in plan_preparation_references(
            intent=intent, accepted_plan=accepted_plan)]}}
