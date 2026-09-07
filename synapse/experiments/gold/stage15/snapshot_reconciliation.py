"""Read-only reconciliation of an attempt's committed knowledge snapshot.

Historical roots remain valid after later publications. Current source heads
must retain the exact trusted prefix; they cannot substitute an older root.
No authority handle is reconstructed and no library index is repaired here.
"""

from enum import Enum
import hashlib
from pathlib import Path
import subprocess

from ..canonicalization import HashBoundRef
from ..contracts import AttemptId, HistoryDomain, create_history_anchor, record_id_reference_from_dict, history_anchor_from_dict
from ..knowledge import snapshot_manifest_ref, atomic_boundary_ref
from ..knowledge_store import AuthoritativeKnowledgeStore
from ..library import inspect_current_snapshot, inspect_retained_snapshot
from ..persistence import read_regular_bytes, scan_journal
from ..run_inputs import FrozenGoldInputs, MAX_INPUT_BYTES
from ..runner.records import RecordKind, RunRecordStore
from ..admission_journal import FileSnapshotFence
from ..stage14.sources import _reopen_location, read_input_graph
from .telemetry import SourceReconciliationReport, reference

SNAPSHOT_REPORT_SCHEMA = "synapse.stage4.gold.snapshot-reconciliation/v1"


class SnapshotStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    MIX_AND_MATCH = "MIX_AND_MATCH"
    ROLLBACK = "ROLLBACK"
    CORRUPTED = "CORRUPTED"


_PRECEDENCE = (SnapshotStatus.CORRUPTED, SnapshotStatus.ROLLBACK,
               SnapshotStatus.MIX_AND_MATCH, SnapshotStatus.INCOMPLETE)


class _SnapshotDifference(ValueError):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


def _history_prefix(source):
    path, _ = _reopen_location(source["location"])
    ref = HashBoundRef.from_dict(source["prefix_ref"])
    raw = read_regular_bytes(path, maximum_bytes=128 * 1024 * 1024)
    if len(raw) < ref.byte_length:
        raise _SnapshotDifference("ROLLBACK", "history_shorter_than_retained_prefix")
    if hashlib.sha256(raw[:ref.byte_length]).hexdigest() != ref.sha256:
        raise _SnapshotDifference("CORRUPTED", "history_prefix_hash_changed")
    scanned = scan_journal(path, create_if_missing=False)
    if scanned.torn_tail:
        raise _SnapshotDifference("CORRUPTED", "history_has_torn_suffix")
    selected = tuple(f for f in scanned.frames if f.end_offset <= ref.byte_length)
    anchor = source["anchor"]
    if len(selected) != anchor["entry_count"] or selected and selected[-1].end_offset != ref.byte_length:
        raise _SnapshotDifference("CORRUPTED", "history_prefix_does_not_end_at_anchor")
    configuration = record_id_reference_from_dict(anchor["configuration_id"])
    domain = HistoryDomain(anchor["history_domain"])
    history_anchor_from_dict(anchor, expected_history_domain=domain, expected_configuration_id=configuration)
    reconstructed = create_history_anchor(history_domain=domain, configuration_id=configuration,
        entry_sha256s=tuple(hashlib.sha256(f.payload).hexdigest() for f in selected), domain_heads=tuple(anchor["domain_heads"]))
    if reconstructed.to_dict() != anchor:
        raise _SnapshotDifference("CORRUPTED", "history_root_differs_from_physical_prefix")
    return tuple(f.payload for f in selected)


def reconcile_snapshot(*, run_root: Path, attempt_index: int, catalog_ref: HashBoundRef) -> SourceReconciliationReport:
    findings, compared = [], [catalog_ref.to_dict()]
    source = {"run_root": str(run_root), "attempt_index": attempt_index, "catalog_ref": catalog_ref.to_dict()}
    try:
        store = RunRecordStore(run_root, mutation_fence=FileSnapshotFence(run_root / "run-coordinator", read_only=True), read_only=True)
        record = store.get(kind=RecordKind.LINEAGE_SOURCES, key=str(attempt_index))
        if record is None:
            raise _SnapshotDifference("INCOMPLETE", "missing_snapshot_source_catalog")
        catalog = record.payload
        if reference(catalog, catalog["schema_version"]) != catalog_ref:
            raise _SnapshotDifference("MIX_AND_MATCH", "catalog_belongs_to_another_snapshot")
        context = store.get(kind=RecordKind.ATTEMPT_CONTEXT, key=str(attempt_index))
        if context is not None:
            phase_refs = context.payload["payload"]["phase_refs"]
            if (phase_refs["lineage_sources_sha256"] != record.sha256
                    or phase_refs["knowledge_snapshot_ref"] != catalog["snapshot_ref"]):
                raise _SnapshotDifference("MIX_AND_MATCH", "catalog_differs_from_committed_attempt_inputs")
        sources = catalog.get("snapshot_sources")
        if sources is None:
            raise _SnapshotDifference("INCOMPLETE", "historical_catalog_has_no_snapshot_source_roots")
        path, fence = _reopen_location(catalog["knowledge"])
        opened = AuthoritativeKnowledgeStore(path.parent, mutation_fence=fence, read_only=True).open_for_attempt(AttemptId(catalog["attempt_id"]))
        manifest = opened.manifest
        if (snapshot_manifest_ref(manifest).to_dict() != catalog["snapshot_ref"]
                or atomic_boundary_ref(opened.boundary).to_dict() != catalog["boundary_ref"]):
            raise _SnapshotDifference("MIX_AND_MATCH", "snapshot_boundary_differs_from_catalog")
        roots = manifest.roots
        library_path, _ = _reopen_location(catalog["library"])
        library_root = library_path.parent.parent
        historical, entries = inspect_retained_snapshot(library_root, index_sha256=roots.index_root_sha256,
                                                         integrity_sha256=roots.library_root_sha256)
        current, _ = inspect_current_snapshot(library_root)
        if historical.generation != roots.library_generation or historical.generation != roots.index_generation:
            raise _SnapshotDifference("MIX_AND_MATCH", "snapshot_generation_differs_from_retained_root")
        if current.generation < historical.generation or current.committed_journal_sequence < historical.committed_journal_sequence:
            raise _SnapshotDifference("ROLLBACK", "current_library_older_than_retained_snapshot")
        if current.generation == historical.generation and current != historical:
            raise _SnapshotDifference("MIX_AND_MATCH", "same_library_generation_has_different_roots")
        retained = {entry.manifest_ref for entry in entries}
        for artifact in catalog["artifacts"]:
            if artifact["class"] == "BEHAVIOR_MANIFEST" and artifact["object_ref"] not in [r.to_dict() for r in retained]:
                raise _SnapshotDifference("MIX_AND_MATCH", "selected_behavior_is_outside_retained_index")
        histories = {kind: _history_prefix(sources[kind]) for kind in ("lifecycle", "provenance", "taint")}
        anchor = sources["lifecycle"]["anchor"]
        prefix = histories["lifecycle"][:roots.lifecycle_record_count]
        if len(prefix) != roots.lifecycle_record_count:
            raise _SnapshotDifference("ROLLBACK", "lifecycle_is_shorter_than_snapshot")
        retained_anchor = create_history_anchor(history_domain=HistoryDomain.LIFECYCLE,
            configuration_id=record_id_reference_from_dict(anchor["configuration_id"]),
            entry_sha256s=tuple(hashlib.sha256(raw).hexdigest() for raw in prefix), domain_heads=())
        if retained_anchor.ordered_log_root_sha256 != roots.lifecycle_root_sha256:
            raise _SnapshotDifference("MIX_AND_MATCH", "lifecycle_root_differs_from_snapshot")
        attestations = {hashlib.sha256(raw).hexdigest(): raw for raw in histories["provenance"]}
        for ref in manifest.attestation_refs:
            if ref.sha256 not in attestations or len(attestations[ref.sha256]) != ref.byte_length:
                raise _SnapshotDifference("INCOMPLETE", "selected_attestation_not_retained")
        # Existing Stage 14 owner resolves admission, compatibility, bindings,
        # retrieval and replay against this same atomic input boundary.
        graph = read_input_graph(catalog)
        compared.extend(node.reference.to_dict() for node in graph.nodes)
        inputs = FrozenGoldInputs(read_regular_bytes(run_root / "experiment.json", maximum_bytes=MAX_INPUT_BYTES))
        data = inputs.data
        if (inputs.manifest.run_id.value != catalog["run_id"]
                or manifest.context.repository_revision != inputs.manifest.config.base_revision
                or manifest.context.policy_version != inputs.manifest.versions.policy_version
                or str(Path(data["repo_root"])) != sources["repository_root"]):
            raise _SnapshotDifference("MIX_AND_MATCH", "frozen_repository_or_policy_differs_from_snapshot")
        inputs.verify_project()  # Pure hash check; does not open or initialize project owners.
        for item in data["knowledge"]["files"]:
            ref = HashBoundRef.from_dict(item["ref"])
            raw = read_regular_bytes(Path(item["path"]), maximum_bytes=MAX_INPUT_BYTES)
            if hashlib.sha256(raw).hexdigest() != ref.sha256 or len(raw) != ref.byte_length:
                raise _SnapshotDifference("CORRUPTED", "frozen_policy_or_binding_source_changed")
            compared.append(ref.to_dict())
        repository = subprocess.run(["git", "--no-optional-locks", "-C", sources["repository_root"],
            "cat-file", "-e", manifest.context.repository_revision + "^{commit}"], capture_output=True, timeout=30)
        if repository.returncode != 0:
            raise _SnapshotDifference("INCOMPLETE", "recorded_repository_revision_is_absent")
        compared.extend(HashBoundRef.from_dict(sources[k]["prefix_ref"]).to_dict() for k in histories)
    except _SnapshotDifference as exc:
        findings.append({"status": exc.status, "code": exc.code})
    except (ValueError, TypeError, OSError, RuntimeError, KeyError) as exc:
        chain = exc
        missing = False
        while chain is not None:
            missing |= isinstance(chain, FileNotFoundError)
            chain = chain.__cause__
        code = getattr(getattr(exc, "failure_code", None), "value", "")
        status = "INCOMPLETE" if missing or code in {"RECORD_MISSING", "MISSING_RECORD", "DIRECTORY_MISSING"} else "CORRUPTED"
        findings.append({"status": status, "code": "physical_snapshot_source_unavailable_or_changed",
                         "subject": type(exc).__name__, "detail": str(exc)[:256]})
    status = next((s.value for s in _PRECEDENCE if any(f["status"] == s.value for f in findings)), "COMPLETE")
    return SourceReconciliationReport.evaluated(schema=SNAPSHOT_REPORT_SCHEMA, source=source, status=status,
        discrepancies=findings, compared=compared, authority="synapse.stage4.physical-snapshot-evaluator/v1",
        precedence=[s.value for s in _PRECEDENCE])
