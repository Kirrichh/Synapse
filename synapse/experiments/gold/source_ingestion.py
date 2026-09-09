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
from .contracts import AttemptId, record_id_reference_from_dict
from .knowledge_environment import open_gold_project, _builder_runtime_identity
from .persistence import (
    commit_snapshot_transaction, committed_transaction_exists, ensure_directory,
    read_committed_snapshot_transaction, read_regular_bytes, require_directory, stage_snapshot_transaction, store_transaction,
    PersistenceViolation,
)
from .run_inputs import read_input_json
from .source_verification import (
    canonical, inspect_source_claim, verify_source_claim, SourceVerification, MAX_EVIDENCE_BYTES,
    SOURCE_CLAIM_V1, SOURCE_CLAIM_V2, source_ref,
)
from .stage12.reusable import ReusableVerificationAuthority
from .stage13.publication import PublicationAuthority, SOURCE_REQUEST_V1
from .stage13.publication_store import PublicationStore, PublicationResult
from .stage10.context_codec import decode_canonical


SOURCE_INGESTION_V1 = "synapse.stage4.gold.source-ingestion/v1"
SOURCE_INGESTION_V2 = "synapse.stage4.gold.source-ingestion/v2"
SOURCE_CHECKPOINT_V2 = "synapse.stage4.gold.source-checkpoint/v2"
SOURCE_OBSERVATION_CHECKPOINT_V1 = "synapse.stage4.gold.source-observation-checkpoint/v1"
SOURCE_RESULT_CHECKPOINT_V2 = "synapse.stage4.gold.source-result-checkpoint/v2"
SOURCE_INPUT_BYTES_V1 = "synapse.stage4.gold.source-input-bytes/v1"


def _checkpoint(root, name, payload, fence, guard, *, evidence=None):
    evidence = {} if evidence is None else evidence
    if sum(len(raw) for raw in evidence.values()) > MAX_EVIDENCE_BYTES:
        raise ValueError("source checkpoint exceeds its retained byte budget")
    members = {}
    for ref, value in evidence.items():
        if (type(ref) is not HashBoundRef or type(value) is not bytes
                or len(value) != ref.byte_length or hashlib.sha256(value).hexdigest() != ref.sha256):
            raise ValueError("source checkpoint evidence differs from its identity")
        members[ref.sha256] = value
    raw = canonical({"schema_version": SOURCE_CHECKPOINT_V2, "payload": payload,
                     "evidence_refs": [ref.to_dict() for ref in sorted(evidence, key=lambda ref: canonical(ref.to_dict()))]})
    members["record.json"] = raw
    with store_transaction(fence, guard=guard) as ticket:
        staged = stage_snapshot_transaction(root, transaction_id=name, members=members,
                                            maximum_bytes=MAX_EVIDENCE_BYTES, ticket=ticket)
        commit_snapshot_transaction(root, transaction_id=name, members=staged,
            boundary_id=hashlib.sha256(raw).hexdigest(), marker_sha256=hashlib.sha256(raw).hexdigest(), ticket=ticket)
    return hashlib.sha256(raw).hexdigest()


def _read_checkpoint(root, name):
    value = _read_retained_checkpoint(root, name)[0]
    return value["result"] if value.get("schema_version") == SOURCE_RESULT_CHECKPOINT_V2 else value


def _read_retained_checkpoint(root, name):
    marker, members = read_committed_snapshot_transaction(root, transaction_id=name)
    raw = members.get("record.json")
    if (raw is None or marker["marker_sha256"] != hashlib.sha256(raw).hexdigest()
            or marker["boundary_id"] != hashlib.sha256(raw).hexdigest()):
        raise ValueError("source operation lost its committed checkpoint")
    value = decode_canonical(raw)
    digest = hashlib.sha256(raw).hexdigest()
    if type(value) is not dict:
        raise ValueError("source operation checkpoint must be an exact object")
    if value.get("schema_version") != SOURCE_CHECKPOINT_V2:
        if set(members) != {"record.json"}:
            raise ValueError("legacy source checkpoint has unexpected evidence")
        return value, {}, digest
    if (set(value) != {"schema_version", "payload", "evidence_refs"}
            or type(value["payload"]) is not dict or type(value["evidence_refs"]) is not list):
        raise ValueError("source checkpoint has an unknown shape")
    evidence = {}
    for item in value["evidence_refs"]:
        ref = HashBoundRef.from_dict(item)
        held = members.get(ref.sha256)
        if (ref in evidence or held is None or len(held) != ref.byte_length
                or hashlib.sha256(held).hexdigest() != ref.sha256):
            raise ValueError("source operation lost retained evidence")
        evidence[ref] = held
    if set(members) != {"record.json", *(ref.sha256 for ref in evidence)}:
        raise ValueError("source checkpoint has undeclared evidence")
    return value["payload"], evidence, digest


def read_source_operations(state_root: Path):
    """Read committed operation prefixes, including failures and interruptions.

    This is a read of the existing journal, not a second memory store. A torn
    tail leaves earlier committed observations readable and the outcome
    unknown. Committed corruption is refused, never silently omitted.
    """
    project = open_gold_project(state_root)
    root = project.declaration.state_root / "source-operations"
    if not root.exists():
        return ()
    require_directory(root)
    publications = {request["domain"]["operation_id"]: result
                    for result, request, _ in _source_publications(project.declaration.state_root)}
    operations = []
    for directory in sorted(root.iterdir()):
        if (len(directory.name) != 64 or any(c not in "0123456789abcdef" for c in directory.name)
                or directory.is_symlink() or not directory.is_dir()):
            raise ValueError("source operation journal contains an unknown entry")
        fence = FileSnapshotFence(directory / "fence")
        with fence.exclusive():
            if not committed_transaction_exists(directory, transaction_id="started"):
                raise ValueError("source operation has no committed input")
            started, retained, head = _read_retained_checkpoint(directory, "started")
            claim = inspect_source_claim(started["claim"])
            if (started["claim_sha256"] != hashlib.sha256(canonical(claim)).hexdigest()
                    or directory.name != hashlib.sha256(claim["operation_id"].encode()).hexdigest()):
                raise ValueError("source operation input identity differs")
            observations, inputs = [], []
            complete = fence.current_epoch() % 2 == 0
            required_inputs = {HashBoundRef.from_dict(item["ref"]) for field in
                               ("policy_inputs", "environment_inputs", "tool_inputs") for item in claim[field]}
            input_names = sorted(item.name for item in directory.iterdir() if item.name.startswith("input-"))
            for sequence, name in enumerate(input_names, 1):
                if name != f"input-{sequence:08d}":
                    raise ValueError("source input sequence is incomplete")
                if not committed_transaction_exists(directory, transaction_id=name):
                    if name != input_names[-1]:
                        raise ValueError("source inputs follow an uncommitted interval")
                    complete = False
                    break
                value, evidence, digest = _read_retained_checkpoint(directory, name)
                if (set(value) != {"input_ref", "observed_ref", "identity_matches", "previous_checkpoint_sha256"}
                        or value["previous_checkpoint_sha256"] != head):
                    raise ValueError("source input checkpoint chain differs")
                ref = HashBoundRef.from_dict(value["input_ref"])
                observed_ref = HashBoundRef.from_dict(value["observed_ref"])
                if set(evidence) != {observed_ref} or ref not in required_inputs or ref.to_dict() in [x["input_ref"] for x in inputs]:
                    raise ValueError("source input checkpoint differs from retained bytes")
                raw = evidence[observed_ref]
                matches = len(raw) == ref.byte_length and hashlib.sha256(raw).hexdigest() == ref.sha256
                if (value["identity_matches"] is not matches
                        or observed_ref != (ref if matches else source_ref(raw, SOURCE_INPUT_BYTES_V1))):
                    raise ValueError("source input identity observation differs from retained bytes")
                inputs.append({key: value[key] for key in ("input_ref", "observed_ref", "identity_matches")})
                retained.update(evidence)
                head = digest
            names = sorted(item.name for item in directory.iterdir() if item.name.startswith("observation-"))
            if any(not item["identity_matches"] for item in inputs) and (names or any(not item["identity_matches"] for item in inputs[:-1])):
                raise ValueError("source verification continued after input identity mismatch")
            for sequence, name in enumerate(names, 1):
                if name != f"observation-{sequence:08d}":
                    raise ValueError("source operation observation sequence is incomplete")
                if not committed_transaction_exists(directory, transaction_id=name):
                    if name != names[-1]:
                        raise ValueError("source operation has observations after an uncommitted interval")
                    complete = False
                    break
                value, evidence, digest = _read_retained_checkpoint(directory, name)
                if (set(value) != {"schema_version", "sequence", "previous_checkpoint_sha256", "observation"}
                        or value["schema_version"] != SOURCE_OBSERVATION_CHECKPOINT_V1
                        or value["sequence"] != sequence or value["previous_checkpoint_sha256"] != head
                        or value["observation"]["operation_id"] != claim["operation_id"]):
                    raise ValueError("source operation observation chain differs")
                observations.append(value["observation"])
                retained.update(evidence)
                head = digest
            result = None
            if committed_transaction_exists(directory, transaction_id="result"):
                result, evidence, _ = _read_retained_checkpoint(directory, "result")
                if result.get("schema_version") == SOURCE_RESULT_CHECKPOINT_V2:
                    if (set(result) != {"schema_version", "observation_count", "observation_head_sha256", "result"}
                            or result["observation_count"] != len(observations)
                            or result["observation_head_sha256"] != head):
                        raise ValueError("source result differs from its observed history")
                    result = result["result"]
                retained.update(evidence)
            publication = publications.get(claim["operation_id"])
            if result is not None and result["status"] in {"PUBLISHED", "ALREADY_KNOWN"}:
                actual = PublicationResult(project.declaration.state_root / "publications",
                                           result["publication"]["transaction_id"]).payload()
                if actual != result["publication"]:
                    raise ValueError("source result differs from its publication")
                publication = actual
            operations.append({"claim": claim, "inputs": inputs, "observations": observations, "result": result,
                "publication": publication, "checkpoint_complete": complete, "head_sha256": head,
                "evidence": retained})
    return tuple(operations)


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
                return (0 if result["status"] == "RETAINED" else 2), result
            return 2, {"status": "INTERRUPTED", "operation_id": claim["operation_id"],
                       "reason": "SOURCE_EFFECTS_MAY_HAVE_OCCURRED"}
        if fence.current_epoch() % 2:
            return 2, {"status": "INTERRUPTED", "operation_id": claim["operation_id"],
                       "reason": "SOURCE_OPERATION_CHECKPOINT_INCOMPLETE"}
        head = _checkpoint(operation_root, "started", {"schema_version": declaration["schema_version"],
            "claim": claim, "claim_sha256": hashlib.sha256(claim_bytes).hexdigest()}, fence, guard)
        sequence = 0

        def retain_observation(observation, evidence):
            nonlocal sequence, head
            next_sequence = sequence + 1
            committed_head = _checkpoint(operation_root, f"observation-{next_sequence:08d}", {
                "schema_version": SOURCE_OBSERVATION_CHECKPOINT_V1, "sequence": next_sequence,
                "previous_checkpoint_sha256": head, "observation": observation}, fence, guard, evidence=evidence)
            sequence, head = next_sequence, committed_head

        def finish(result):
            _checkpoint(operation_root, "result", {"schema_version": SOURCE_RESULT_CHECKPOINT_V2,
                "observation_count": sequence, "observation_head_sha256": head, "result": result}, fence, guard)
        assertion = {key: value for key, value in claim.items() if key != "operation_id"}
        for result, request, _ in _source_publications(state_root):
            previous = {key: value for key, value in request["domain"].items() if key != "operation_id"}
            if previous == assertion:
                known = {"status": "ALREADY_KNOWN", "operation_id": claim["operation_id"],
                         "publication": result, "reason": "SAME_VERIFIED_SOURCE_CONTRACT"}
                finish(known)
                return 0, {**known, "knowledge": export_source_knowledge(state_root)}
        try:
            evidence = {}
            for index, (ref, path) in enumerate(evidence_paths.items(), 1):
                raw = read_regular_bytes(path, maximum_bytes=MAX_EVIDENCE_BYTES)
                matches = len(raw) == ref.byte_length and hashlib.sha256(raw).hexdigest() == ref.sha256
                observed_ref = ref if matches else source_ref(raw, SOURCE_INPUT_BYTES_V1)
                head = _checkpoint(operation_root, f"input-{index:08d}",
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
        return 0, {**result, "knowledge": export_source_knowledge(state_root)}
