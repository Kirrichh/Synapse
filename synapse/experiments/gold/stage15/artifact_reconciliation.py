"""Physical artifact reconciliation over the existing Stage 14 reconstruction.

This reader has no recovery, publisher or execution capability. Exact run and
attempt DAGs are rebuilt by their existing owner, including atomic publication
participants and producer/consumer evidence. A self-consistent supplied graph
is never accepted in place of retained source bytes.
"""

from enum import Enum
from pathlib import Path

from ..admission_journal import FileSnapshotFence
from ..canonicalization import HashBoundRef
from ..runner.records import RecordKind, RunRecordStore
from ..runner.state_machine import load_run_state
from ..runner.c1_boundary import C1EvidenceContext
from ..persistence import read_regular_bytes
from ..run_inputs import FrozenGoldInputs, MAX_INPUT_BYTES
from ..stage14.graph import LineageGraph, LineageViolation, LineageFailureCode
from ..stage14.reconstruction import reconstruct_retained_attempt, reconstruct_run
from .telemetry import SourceReconciliationReport, reference

ARTIFACT_REPORT_SCHEMA = "synapse.stage4.gold.artifact-reconciliation/v1"


class ArtifactStatus(str, Enum):
    COMPLETE = "COMPLETE"
    MISSING_BLOB = "MISSING_BLOB"
    HASH_MISMATCH = "HASH_MISMATCH"
    ORPHAN_REF = "ORPHAN_REF"
    LINEAGE_MISMATCH = "LINEAGE_MISMATCH"


_PRECEDENCE = (ArtifactStatus.HASH_MISMATCH, ArtifactStatus.ORPHAN_REF,
               ArtifactStatus.LINEAGE_MISMATCH, ArtifactStatus.MISSING_BLOB)


def _failure_status(exc):
    if isinstance(exc, LineageViolation):
        if exc.failure_code is LineageFailureCode.ORPHAN_EDGE:
            return ArtifactStatus.ORPHAN_REF
        if exc.failure_code is LineageFailureCode.MISSING_RECORD:
            return ArtifactStatus.MISSING_BLOB
        if exc.failure_code in {LineageFailureCode.MISSING_MANDATORY_EDGE, LineageFailureCode.TYPE_CONSTRAINT,
                                 LineageFailureCode.CYCLE}:
            return ArtifactStatus.LINEAGE_MISMATCH
    chain = exc
    while chain is not None:
        if isinstance(chain, FileNotFoundError):
            return ArtifactStatus.MISSING_BLOB
        chain = chain.__cause__
    if getattr(getattr(exc, "failure_code", None), "value", None) in {"RECORD_MISSING", "MISSING_RECORD", "DIRECTORY_MISSING"}:
        return ArtifactStatus.MISSING_BLOB
    return ArtifactStatus.HASH_MISMATCH


def reopen_attempt_verification_sources(*, run_root, manifest, attempt):
    """Reopen C1's original writer, report, committed inputs and oracle pair.

    Both artifact reconciliation and verification observations use this exact
    reader; a saved verification transport cannot stand in for physical bytes.
    """
    facts = attempt.result.structured_outcome["payload"]["verification"]["payload"]["c1"]
    if facts is None:
        return None
    inputs = FrozenGoldInputs(read_regular_bytes(run_root / "experiment.json", maximum_bytes=MAX_INPUT_BYTES))
    if inputs.manifest.manifest_sha256 != manifest.manifest_sha256:
        raise ValueError("C1 retained source context belongs to another run")
    data = inputs.data
    context = C1EvidenceContext.from_inputs(repo_root=Path(data["repo_root"]),
        command_policy=data["declaration"]["command_policy"], environment_kind=manifest.config.environment_kind,
        oracle_config=data["declaration"]["oracle"])
    evidence = context.read(run_root=run_root, gold_run_id=manifest.gold_run_id,
        attempt_id=attempt.context.attempt_id.value, base_revision=manifest.config.base_revision)
    if evidence.payload() != facts:
        raise ValueError("C1 physical sources differ from retained verification")
    return evidence


def reconcile_artifacts(*, run_root: Path, manifest_ref: HashBoundRef,
                        publication_root: Path | None) -> SourceReconciliationReport:
    findings, compared = [], []
    source = {"run_root": str(run_root), "manifest_ref": manifest_ref.to_dict(),
              "publication_root": None if publication_root is None else str(publication_root)}
    try:
        store = RunRecordStore(run_root, mutation_fence=FileSnapshotFence(run_root / "run-coordinator", read_only=True), read_only=True)
        state = load_run_state(store)
        if reference(state.manifest.stored_dict(), state.manifest.payload()["schema_version"]) != manifest_ref:
            raise ValueError("physical run belongs to another manifest")
        for attempt in state.attempts:
            if attempt.result is None:
                findings.append({"status": "MISSING_BLOB", "code": "attempt_has_no_terminal_evidence", "subject": str(attempt.attempt_index)})
                continue
            c1 = reopen_attempt_verification_sources(run_root=run_root, manifest=state.manifest, attempt=attempt)
            if c1 is not None:
                compared.extend(ref.to_dict() for ref, raw in c1.retained_artifacts())
            expected = reconstruct_retained_attempt(manifest=state.manifest, context=attempt.context,
                result=attempt.result, store=store, publication_root=publication_root)
            actual = store.get(kind=RecordKind.ATTEMPT_LINEAGE, key=str(attempt.attempt_index))
            if actual is None:
                findings.append({"status": "MISSING_BLOB", "code": "missing_attempt_graph", "subject": str(attempt.attempt_index)})
            elif LineageGraph.from_dict(actual.payload).to_dict() != expected.to_dict():
                findings.append({"status": "LINEAGE_MISMATCH", "code": "graph_differs_from_sources", "subject": str(attempt.attempt_index)})
            compared.extend(node.reference.to_dict() for node in expected.nodes)
        if state.final_result is None:
            findings.append({"status": "MISSING_BLOB", "code": "run_has_no_terminal_evidence", "subject": "run"})
        else:
            terminal = state.preparation_failure or state.decision_for(state.attempts[-1].attempt_index)
            expected = reconstruct_run(store=store, manifest=state.manifest, attempts=state.attempts,
                                       terminal=terminal, result=state.final_result)
            actual = store.get(kind=RecordKind.RUN_LINEAGE, key="final")
            if actual is None:
                findings.append({"status": "MISSING_BLOB", "code": "missing_run_graph", "subject": "run"})
            elif LineageGraph.from_dict(actual.payload).to_dict() != expected.to_dict():
                findings.append({"status": "LINEAGE_MISMATCH", "code": "run_graph_differs_from_sources", "subject": "run"})
            compared.extend(node.reference.to_dict() for node in expected.nodes)
    except (ValueError, TypeError, OSError, RuntimeError, KeyError) as exc:
        findings.append({"status": _failure_status(exc).value, "code": "physical_reconstruction_failed",
                         "subject": type(exc).__name__, "detail": str(exc)[:256]})
    status = next((s.value for s in _PRECEDENCE if any(f["status"] == s.value for f in findings)), "COMPLETE")
    return SourceReconciliationReport.evaluated(schema=ARTIFACT_REPORT_SCHEMA, source=source, status=status,
        discrepancies=findings, compared=compared, authority="synapse.stage4.physical-artifact-evaluator/v1",
        precedence=[s.value for s in _PRECEDENCE])
