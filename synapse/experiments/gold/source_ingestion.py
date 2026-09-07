"""Canonical composition for a source operation and its existing publisher.

The operation journal commits STARTED before executing a recipe. An uncertain
operation cannot silently repeat external commands. Publication uses the sole
project writer; this journal owns only operation inputs and observations.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .admission_journal import FileSnapshotFence
from .canonicalization import HashBoundRef
from .contracts import AttemptId, record_id_reference_from_dict
from .knowledge_environment import open_gold_project, _builder_runtime_identity
from .persistence import (
    commit_snapshot_transaction, committed_transaction_exists, ensure_directory,
    read_committed_snapshot_transaction, read_regular_bytes, stage_snapshot_transaction, store_transaction,
)
from .run_inputs import read_input_json
from .source_verification import (
    canonical, inspect_source_claim, verify_source_claim, SourceVerification, MAX_EVIDENCE_BYTES,
)
from .stage12.reusable import ReusableVerificationAuthority
from .stage13.publication import PublicationAuthority, SOURCE_REQUEST_V1
from .stage13.publication_store import PublicationStore, PublicationResult
from .stage10.context_codec import decode_canonical


SOURCE_INGESTION_V1 = "synapse.stage4.gold.source-ingestion/v1"


def _checkpoint(root, name, payload, fence, guard):
    raw = canonical(payload)
    with store_transaction(fence, guard=guard) as ticket:
        members = stage_snapshot_transaction(root, transaction_id=name, members={"record.json": raw}, ticket=ticket)
        commit_snapshot_transaction(root, transaction_id=name, members=members,
            boundary_id=hashlib.sha256(raw).hexdigest(), marker_sha256=hashlib.sha256(raw).hexdigest(), ticket=ticket)


def _read_checkpoint(root, name):
    marker, members = read_committed_snapshot_transaction(root, transaction_id=name)
    if set(members) != {"record.json"} or marker["marker_sha256"] != hashlib.sha256(members["record.json"]).hexdigest():
        raise ValueError("source operation lost its committed checkpoint")
    return decode_canonical(members["record.json"])


def _source_publications(project_root):
    root = project_root / "publications"
    if not (root / "committed").exists():
        return
    for directory in sorted((root / "committed").iterdir()):
        if not committed_transaction_exists(root / "committed", transaction_id=directory.name):
            continue
        result = PublicationResult(root, directory.name).payload()
        _, prepared = read_committed_snapshot_transaction(root / "prepared", transaction_id=directory.name)
        request = decode_canonical(prepared["request.json"])
        if request["schema_version"] == SOURCE_REQUEST_V1:
            yield result, request, root / "prepared" / directory.name


def export_source_knowledge(state_root: Path):
    """Export pointers to physically committed evidence, never create admission."""
    open_gold_project(state_root)
    candidates, files = [], {}
    for result, request, prepared_path in _source_publications(state_root):
        registration = result["registration"]
        proof = request["verification"]["payload"]
        candidates.append({"unit": registration["unit"], "manifest_id": registration["manifest_id"],
            "attestation": registration["attestation"], "bindings": proof["bindings"],
            "lifecycle_context": registration["lifecycle_context"],
            "taint": {"profiles": [request["taint"]], "derivations": [], "decisions": [],
                "root_id": record_id_reference_from_dict(request["taint"]["profile_id"]).value},
            "source_publication": {"transaction_id": result["transaction_id"], "result_ref":
                PublicationResult(state_root / "publications", result["transaction_id"]).reference.to_dict()}})
        for raw_ref in request["evidence_refs"]:
            ref = HashBoundRef.from_dict(raw_ref)
            files[ref] = {"ref": raw_ref, "path": str(prepared_path / ref.sha256)}
    return {"schema_version": "synapse.stage4.gold.knowledge-input/v2", "candidates": candidates,
            "files": list(files.values()), "conflicts": []}


def execute_source_ingestion(*, state_root: Path, input_path: Path):
    state_root, input_path = state_root.resolve(), input_path.resolve()
    declaration = read_input_json(input_path)
    if (set(declaration) != {"schema_version", "claim", "files"}
            or declaration["schema_version"] != SOURCE_INGESTION_V1):
        raise ValueError("source ingestion has an unknown declaration")
    claim = inspect_source_claim(declaration["claim"])
    project = open_gold_project(state_root)
    if project.declaration.entitlements is None:
        raise ValueError("source ingestion requires the connected project's operator entitlements")
    grant = project.declaration.entitlements
    if "read" not in grant.capabilities or any(not any(path == scope or path.startswith(scope.rstrip("/") + "/")
            for scope in grant.scopes) for path in claim["sources"]):
        raise ValueError("source claim exceeds the project's declared read scope")
    if claim["recipe"] is not None and "execute" not in grant.capabilities:
        raise ValueError("source recipe execution is outside the project's declared capabilities")
    evidence = {}
    if type(declaration["files"]) is not list:
        raise ValueError("source ingestion needs explicit observed input files")
    for item in declaration["files"]:
        if type(item) is not dict or set(item) != {"ref", "path"}:
            raise ValueError("source evidence has an unknown declaration")
        ref, path = HashBoundRef.from_dict(item["ref"]), Path(item["path"])
        if not path.is_absolute():
            path = input_path.parent / path
        if ref in evidence:
            raise ValueError("source evidence is repeated")
        evidence[ref] = read_regular_bytes(path, maximum_bytes=MAX_EVIDENCE_BYTES)
        if len(evidence[ref]) != ref.byte_length or hashlib.sha256(evidence[ref]).hexdigest() != ref.sha256:
            raise ValueError("source dependency bytes differ from their declared identity")
    required = {HashBoundRef.from_dict(item["ref"]) for field in ("policy_inputs", "environment_inputs", "tool_inputs")
                for item in claim[field]}
    if set(evidence) != required or sum(len(raw) for raw in evidence.values()) > MAX_EVIDENCE_BYTES:
        raise ValueError("source dependency evidence is incomplete, unexpected or over budget")
    claim_bytes = canonical(claim)
    operation_key = hashlib.sha256(claim["operation_id"].encode()).hexdigest()
    operation_root = state_root / "source-operations" / operation_key
    ensure_directory(operation_root.parent)
    ensure_directory(operation_root)
    fence = FileSnapshotFence(operation_root / "fence")
    with fence.exclusive() as guard:
        if committed_transaction_exists(operation_root, transaction_id="started"):
            previous = _read_checkpoint(operation_root, "started")
            if previous["claim"] != claim:
                raise ValueError("source operation identity was reused for another claim")
            for result, request, _ in _source_publications(state_root):
                if request["domain"] == claim:
                    return 0, {"status": "PUBLISHED", "publication": result, "knowledge": export_source_knowledge(state_root)}
            if committed_transaction_exists(operation_root, transaction_id="result"):
                result = _read_checkpoint(operation_root, "result")
                if result["status"] == "ALREADY_KNOWN":
                    prior = PublicationResult(state_root / "publications", result["publication"]["transaction_id"]).payload()
                    if prior != result["publication"]:
                        raise ValueError("reused source publication changed")
                    return 0, {**result, "knowledge": export_source_knowledge(state_root)}
                return 2, result
            return 2, {"status": "INTERRUPTED", "operation_id": claim["operation_id"],
                       "reason": "SOURCE_EFFECTS_MAY_HAVE_OCCURRED"}
        if fence.current_epoch() % 2:
            return 2, {"status": "INTERRUPTED", "operation_id": claim["operation_id"],
                       "reason": "SOURCE_OPERATION_CHECKPOINT_INCOMPLETE"}
        _checkpoint(operation_root, "started", {"schema_version": SOURCE_INGESTION_V1,
            "claim": claim, "claim_sha256": hashlib.sha256(claim_bytes).hexdigest()}, fence, guard)
        assertion = {key: value for key, value in claim.items() if key != "operation_id"}
        for result, request, _ in _source_publications(state_root):
            previous = {key: value for key, value in request["domain"].items() if key != "operation_id"}
            if previous == assertion:
                known = {"status": "ALREADY_KNOWN", "operation_id": claim["operation_id"],
                         "publication": result, "reason": "SAME_VERIFIED_SOURCE_CONTRACT"}
                _checkpoint(operation_root, "result", known, fence, guard)
                return 0, {**known, "knowledge": export_source_knowledge(state_root)}
        try:
            verification = verify_source_claim(repo_root=project.declaration.repo_root, claim=claim,
                external_evidence=evidence, attempt_id=AttemptId("source-verification-1"))
        except (ValueError, OSError, TypeError) as exc:
            failure = {"status": "INFRA_ERROR" if isinstance(exc, OSError) else "REFUSED",
                "operation_id": claim["operation_id"], "reason": str(exc), "error_class": type(exc).__name__}
            _checkpoint(operation_root, "result", failure, fence, guard)
            return 2, failure
        if type(verification) is not SourceVerification:
            _checkpoint(operation_root, "result", verification, fence, guard)
            return 2, verification
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
        _checkpoint(operation_root, "result", result, fence, guard)
        return 0, {**result, "knowledge": export_source_knowledge(state_root)}
