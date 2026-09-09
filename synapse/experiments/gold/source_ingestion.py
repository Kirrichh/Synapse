"""Canonical composition for a source operation and its existing publisher.

The operation journal commits STARTED before executing a recipe. An uncertain
operation cannot silently repeat external commands. Publication uses the sole
project writer; this journal owns only operation inputs and observations.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from synapse.change.workspace import GitWorkspaceError

from .admission_journal import FileSnapshotFence, JournalAdapterViolation
from .canonicalization import HashBoundRef
from .contracts import AttemptId
from .knowledge_environment import open_gold_project, _builder_runtime_identity
from .persistence import (
    committed_transaction_exists, read_regular_bytes,
    PersistenceViolation,
)
from .run_inputs import read_input_json
from .source_verification import (
    canonical, inspect_source_claim, verify_source_claim, SourceVerification, MAX_EVIDENCE_BYTES,
    SOURCE_CLAIM_V1, SOURCE_CLAIM_V2, source_ref,
)
from .stage12.reusable import ReusableVerificationAuthority
from .stage13.publication import PublicationAuthority
from .stage13.publication_store import PublicationStore, PublicationResult
from . import source_operation_journal as journal
from . import source_experience as experience


SOURCE_INGESTION_V1 = "synapse.stage4.gold.source-ingestion/v1"
SOURCE_INGESTION_V2 = "synapse.stage4.gold.source-ingestion/v2"


def execute_source_ingestion(*, state_root: Path, input_path: Path):
    """Translate owned ingestion/storage failures at the CLI composition boundary."""
    try:
        return _ingest_sources(state_root=state_root, input_path=input_path)
    except (OSError, ValueError, TypeError, GitWorkspaceError, PersistenceViolation, JournalAdapterViolation) as exc:
        return 2, {"status": "REFUSED", "reason": str(exc)}


def _ingest_sources(*, state_root: Path, input_path: Path):
    state_root, input_path = state_root.resolve(), input_path.resolve()
    declaration = read_input_json(input_path)
    if (set(declaration) != {"schema_version", "claim", "files"}
            or declaration["schema_version"] not in {SOURCE_INGESTION_V1, SOURCE_INGESTION_V2}):
        raise ValueError("source ingestion has an unknown declaration")
    claim = inspect_source_claim(declaration["claim"])
    if claim["schema_version"] != (SOURCE_CLAIM_V1 if declaration["schema_version"] == SOURCE_INGESTION_V1 else SOURCE_CLAIM_V2):
        raise ValueError("source ingestion and claim versions differ")
    project = open_gold_project(state_root)
    if project.declaration.entitlements is None:
        raise ValueError("source ingestion requires the connected project's operator entitlements")
    grant = project.declaration.entitlements
    if "read" not in grant.capabilities or any(not any(path == scope or path.startswith(scope.rstrip("/") + "/")
            for scope in grant.scopes) for path in claim["sources"]):
        raise ValueError("source claim exceeds the project's declared read scope")
    if claim["recipe"] is not None and "execute" not in grant.capabilities:
        raise ValueError("source recipe execution is outside the project's declared capabilities")
    evidence_paths = {}
    if type(declaration["files"]) is not list:
        raise ValueError("source ingestion needs explicit observed input files")
    for item in declaration["files"]:
        if type(item) is not dict or set(item) != {"ref", "path"}:
            raise ValueError("source evidence has an unknown declaration")
        ref, path = HashBoundRef.from_dict(item["ref"]), Path(item["path"])
        if not path.is_absolute():
            path = input_path.parent / path
        if ref in evidence_paths:
            raise ValueError("source evidence is repeated")
        evidence_paths[ref] = path
    required = {HashBoundRef.from_dict(item["ref"]) for field in ("policy_inputs", "environment_inputs", "tool_inputs")
                for item in claim[field]}
    if set(evidence_paths) != required or sum(ref.byte_length for ref in required) > MAX_EVIDENCE_BYTES:
        raise ValueError("source dependency evidence is incomplete, unexpected or over budget")
    claim_bytes = canonical(claim)
    operation_key = hashlib.sha256(claim["operation_id"].encode()).hexdigest()
    operation_root = journal.allocate_source_operation(state_root, operation_key)
    fence = FileSnapshotFence(operation_root / "fence")
    with fence.exclusive() as guard:
        if committed_transaction_exists(operation_root, transaction_id="started"):
            previous = journal.read_checkpoint(operation_root, "started")
            if previous["claim"] != claim:
                raise ValueError("source operation identity was reused for another claim")
            for result, request, _ in experience.source_publications(state_root):
                if request["domain"] == claim:
                    return 0, {"status": "PUBLISHED", "publication": result, "knowledge": experience.export_source_knowledge(state_root)}
            if committed_transaction_exists(operation_root, transaction_id="result"):
                result = journal.read_checkpoint(operation_root, "result")
                if result["status"] == "ALREADY_KNOWN":
                    prior = PublicationResult(state_root / "publications", result["publication"]["transaction_id"]).payload()
                    if prior != result["publication"]:
                        raise ValueError("reused source publication changed")
                    return 0, {**result, "knowledge": experience.export_source_knowledge(state_root)}
                return (0 if result["status"] == "RETAINED" else 2), result
            return 2, {"status": "INTERRUPTED", "operation_id": claim["operation_id"],
                       "reason": "SOURCE_EFFECTS_MAY_HAVE_OCCURRED"}
        if fence.current_epoch() % 2:
            return 2, {"status": "INTERRUPTED", "operation_id": claim["operation_id"],
                       "reason": "SOURCE_OPERATION_CHECKPOINT_INCOMPLETE"}
        head = journal.write_checkpoint(operation_root, "started", {"schema_version": declaration["schema_version"],
            "claim": claim, "claim_sha256": hashlib.sha256(claim_bytes).hexdigest()}, fence, guard)
        sequence = 0

        def retain_observation(observation, evidence):
            nonlocal sequence, head
            next_sequence = sequence + 1
            committed_head = journal.write_checkpoint(operation_root, f"observation-{next_sequence:08d}", {
                "schema_version": journal.SOURCE_OBSERVATION_CHECKPOINT_V1, "sequence": next_sequence,
                "previous_checkpoint_sha256": head, "observation": observation}, fence, guard, evidence=evidence)
            sequence, head = next_sequence, committed_head

        def finish(result):
            journal.write_checkpoint(operation_root, "result", {"schema_version": journal.SOURCE_RESULT_CHECKPOINT_V2,
                "observation_count": sequence, "observation_head_sha256": head, "result": result}, fence, guard)
        assertion = {key: value for key, value in claim.items() if key != "operation_id"}
        for result, request, _ in experience.source_publications(state_root):
            previous = {key: value for key, value in request["domain"].items() if key != "operation_id"}
            if previous == assertion:
                known = {"status": "ALREADY_KNOWN", "operation_id": claim["operation_id"],
                         "publication": result, "reason": "SAME_VERIFIED_SOURCE_CONTRACT"}
                finish(known)
                return 0, {**known, "knowledge": experience.export_source_knowledge(state_root)}
        try:
            evidence = {}
            for index, (ref, path) in enumerate(evidence_paths.items(), 1):
                raw = read_regular_bytes(path, maximum_bytes=MAX_EVIDENCE_BYTES)
                matches = len(raw) == ref.byte_length and hashlib.sha256(raw).hexdigest() == ref.sha256
                observed_ref = ref if matches else source_ref(raw, journal.SOURCE_INPUT_BYTES_V1)
                head = journal.write_checkpoint(operation_root, f"input-{index:08d}",
                    {"input_ref": ref.to_dict(), "observed_ref": observed_ref.to_dict(),
                     "identity_matches": matches, "previous_checkpoint_sha256": head},
                    fence, guard, evidence={observed_ref: raw})
                if not matches:
                    raise ValueError("source dependency bytes differ from their declared identity")
                evidence[ref] = raw
            verification = verify_source_claim(repo_root=project.declaration.repo_root, claim=claim,
                external_evidence=evidence, attempt_id=AttemptId("source-verification-1"),
                observation_sink=retain_observation)
        except (ValueError, OSError, TypeError, GitWorkspaceError, PersistenceViolation) as exc:
            if fence.current_epoch() % 2:
                raise  # A failed journal mutation cannot authorize another write.
            failure = {"status": "INFRA_ERROR" if isinstance(exc, (OSError, GitWorkspaceError, PersistenceViolation)) else "REFUSED",
                "operation_id": claim["operation_id"], "reason": str(exc), "error_class": type(exc).__name__}
            finish(failure)
            return 2, failure
        if type(verification) is not SourceVerification:
            finish(verification)
            return (0 if verification["status"] == "RETAINED" else 2), verification
        owners = ReusableVerificationAuthority(project.declaration.repo_root, project.declaration.environment_profile_id,
            project.authority_handle, project.library, project.attestation_store, project.lifecycle_store,
            project.admission_journal, project.fence, None, project.compatibility_history)
        authority = PublicationAuthority(owners, project.taint_store, _builder_runtime_identity(project.declaration), ())
        publisher = PublicationStore(root=state_root / "publications", authority=authority)
        request = authority.prepare_source(verification)
        published = publisher.publish(request)
        if published is None:
            raise ValueError("source publication was quarantined")
        result = {"status": "PUBLISHED", "operation_id": claim["operation_id"],
            "verification_ref": verification.reference.to_dict(), "elapsed_ns": verification.payload()["elapsed_ns"],
            "publication": published.payload()}
        finish(result)
        return 0, {**result, "knowledge": experience.export_source_knowledge(state_root)}


def execute_source_recall(*, state_root: Path, input_path: Path):
    try:
        project = open_gold_project(state_root)
        return 0, experience.recall_source_experience(state_root=project.declaration.state_root,
            entitlements=None if project.declaration.entitlements is None else project.declaration.entitlements.to_dict(), query=read_input_json(input_path))
    except (OSError, ValueError, TypeError, KeyError, PersistenceViolation, JournalAdapterViolation) as exc:
        return 2, {"status": "REFUSED", "reason": str(exc)}
