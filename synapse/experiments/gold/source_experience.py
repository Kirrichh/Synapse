"""Task-scoped read projection over the existing source-operation journal.

No admission, publication or execution is granted here. Materials, checked
structural identities and actual command observations remain distinguishable.
Selection does not filter by success and does not infer method applicability
from exit status. The executable behavior library retains its existing gates.
"""

from __future__ import annotations

from pathlib import Path
import re

from .admission_journal import JournalAdapterViolation
from .canonicalization import HashBoundRef
from .contracts import RepositoryRevision, record_id_reference_from_dict
from .persistence import PersistenceViolation, committed_transaction_exists, read_committed_snapshot_transaction
from .source_operation_journal import read_source_operations
from .stage13.publication import SOURCE_REQUEST_SCHEMAS
from .stage13.publication_store import PublicationResult, retired_source_request
from .source_verification import (canonical, inspect_source_command, inspect_source_observations,
    inspect_source_verification, SOURCE_EXECUTION_RESULT_V1, inspect_source_execution_result)
from .stage10.context_codec import decode_canonical, encode_base64url
from .stage10.repository_scope import create_repository_scope


MAX_RECALL_BYTES = 16 * 1024 * 1024


def source_publications(project_root):
    """Read source history; retired profiles are not reusable knowledge."""
    root = project_root / "publications"
    if not (root / "committed").exists():
        return
    for directory in sorted((root / "committed").iterdir()):
        if not committed_transaction_exists(root / "committed", transaction_id=directory.name):
            continue
        result = PublicationResult(root, directory.name).retained_payload()
        _, prepared = read_committed_snapshot_transaction(root / "prepared", transaction_id=directory.name)
        request = decode_canonical(prepared["request.json"])
        if request["schema_version"] in SOURCE_REQUEST_SCHEMAS or retired_source_request(request):
            yield result, request, root / "prepared" / directory.name


SOURCE_RECALL_QUERY_V1 = "synapse.stage4.gold.source-recall-query/v1"
SOURCE_RECALL_V1 = "synapse.stage4.gold.source-recall/v1"
SOURCE_RECALL_PROFILE_V1 = "exact-revision-path-and-term-overlap/v1"
#: A query that names its ranking profile. v1 queries keep profile v1, byte for byte; delivery into a Gold
#: run keeps v1 until its owner selects otherwise, since changing what a run is shown confounds a comparison.
SOURCE_RECALL_QUERY_V2 = "synapse.stage4.gold.source-recall-query/v2"
SOURCE_RECALL_V2 = "synapse.stage4.gold.source-recall/v2"
#: Profile v2 ranks by tiers of evidence, compared in order, each an integer count of distinct query terms:
#: the operation's own identity (its claimed paths and bound qualified names), then the output of its
#: command, then the full text of its sources; operation_id breaks what remains equal. A large file holds
#: nearly every query term, so in v1 operations over one file tie on its text and the alphabet orders them;
#: v2 lets what an operation itself names and observed decide first (field priority, as code search ranks
#: symbol matches above content). Integers only: no floating score whose bits could differ by platform.
SOURCE_RECALL_PROFILE_V2 = "exact-revision-path-and-tiered-term-overlap/v2"
_PROFILES = {SOURCE_RECALL_PROFILE_V1: SOURCE_RECALL_V1, SOURCE_RECALL_PROFILE_V2: SOURCE_RECALL_V2}


def _query(value):
    fields = {"schema_version", "statement", "revision", "scope", "limit"}
    if (type(value) is not dict
            or not (value.get("schema_version") == SOURCE_RECALL_QUERY_V1 and set(value) == fields
                    or value.get("schema_version") == SOURCE_RECALL_QUERY_V2 and set(value) == fields | {"profile"}
                    and type(value["profile"]) is str and value["profile"] in _PROFILES)):
        raise ValueError("source recall requires an exact task query")
    RepositoryRevision.git_commit(value["revision"])
    if type(value["statement"]) is not str or not 1 <= len(value["statement"]) <= 4096:
        raise ValueError("source recall requires a bounded task statement")
    if type(value["scope"]) is not list or not value["scope"] or len(value["scope"]) > 64:
        raise ValueError("source recall requires a bounded task scope")
    scope = create_repository_scope(value["scope"])
    if list(scope.entries) != value["scope"]:
        raise ValueError("source recall scope must be canonical")
    if type(value["limit"]) is not int or not 1 <= value["limit"] <= 64:
        raise ValueError("source recall requires a bounded selection limit")
    return scope, value.get("profile", SOURCE_RECALL_PROFILE_V1)


def _terms(text):
    # A transparent structural/lexical profile, with no language-model claims.
    return frozenset(re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE))


def _publication_evidence(state_root, publication):
    _, members = read_committed_snapshot_transaction(state_root / "publications" / "prepared",
                                                     transaction_id=publication["transaction_id"])
    request = decode_canonical(members["request.json"])
    evidence = {HashBoundRef.from_dict(item): members[item["sha256"]] for item in request["evidence_refs"]}
    proof = request["verification"]["payload"]
    inspect_source_verification(proof, evidence=evidence)
    return proof, evidence


def _ranked(profile, query_terms, matched_paths, claim, observed, texts):
    """How ``profile`` ranks one eligible operation: its key, compared count by count, and the match it reports.

    v1 counts the task terms found anywhere in the operation's claimed paths, bound qualified names, command
    output and sources' text. v2 counts them per tier, compared in order: the operation's own identity (its
    claimed paths and bound qualified names), its command's output, then its sources' text.
    """
    command = observed["command_result"]
    names = [*claim["sources"], *(item["qualname"] for item in observed["bindings"])]
    output = [] if command is None else [command["stdout"], command["stderr"]]
    if profile == SOURCE_RECALL_PROFILE_V1:
        terms = sorted(query_terms & _terms(" ".join([*names, *output, *texts])))
        return (len(matched_paths), len(terms)), {"paths": matched_paths, "terms": terms}
    tiers = {"identity": sorted(query_terms & _terms(" ".join(names))),
             "output": sorted(query_terms & _terms(" ".join(output))),
             "source": sorted(query_terms & _terms(" ".join(texts)))}
    return ((len(matched_paths), *(len(tiers[name]) for name in ("identity", "output", "source"))),
            {"paths": matched_paths, "terms": tiers})


def recall_source_experience(*, state_root: Path, query, entitlements, operations=None, publications=None):
    """Select relevant retained experience, with explicit empty/limited results.

    Each operation is read under its own existing fence. This is a read view of
    individually committed prefixes, not an atomic Gold execution snapshot.
    """
    scope, profile = _query(query)
    grant = entitlements
    if (grant is None or "read" not in grant["capabilities"]
            or any(not any(path == entry or path.startswith(entry.rstrip("/") + "/")
                           for entry in grant["scopes"]) for path in scope.entries)):
        raise ValueError("source recall exceeds the project's declared read scope")
    query_terms = _terms(query["statement"])
    if publications is None:
        publications = {request["domain"]["operation_id"]: result for result, request, _ in source_publications(state_root)}
    eligible, excluded = [], {"revision": 0, "scope": 0}
    for operation in read_source_operations(state_root) if operations is None else operations:
        claim = operation["claim"]
        if claim["revision"] != query["revision"]:
            excluded["revision"] += 1
            continue
        matched_paths = sorted(path for path in claim["sources"] if scope.covers(path))
        if not matched_paths:
            excluded["scope"] += 1
            continue
        evidence = operation["evidence"]
        observed = inspect_source_observations(claim, operation["observations"], evidence)
        origin = "OPERATION_JOURNAL"
        observation_operation_id = claim["operation_id"]
        result, publication = operation["result"], publications.get(claim["operation_id"])
        if result is not None and result.get("schema_version") == SOURCE_EXECUTION_RESULT_V1:
            inspect_source_execution_result(result, claim=claim, observed=observed)
        if result is not None and result["status"] in {"PUBLISHED", "ALREADY_KNOWN"}:
            actual = PublicationResult(state_root / "publications", result["publication"]["transaction_id"]).retained_payload()
            if actual != result["publication"]:
                raise ValueError("source result differs from its publication")
            publication = actual
        if not operation["observations"] and result is not None and result.get("command_result") is not None:
            observed["command_result"] = inspect_source_command(result["command_result"], claim=claim)
            origin = "LEGACY_RESULT"
        # Historical and deduplicated operations can reopen their real
        # publication. No observations are invented to fill absent history.
        if publication is not None:
            proof, published_evidence = _publication_evidence(state_root, publication)
            expected = {key: value for key, value in claim.items() if key != "operation_id"}
            actual = {key: value for key, value in proof["claim"].items() if key != "operation_id"}
            if expected != actual:
                raise ValueError("source experience refers to another published claim")
            if observed["verification"] is None:
                observed.update(sources=proof["sources"], bindings=proof["bindings"],
                    command_result=proof["command_result"], verification=proof)
                origin = "PUBLISHED_PROOF"
                observation_operation_id = proof["operation_id"]
            evidence = {**evidence, **published_evidence}
        command = observed["command_result"]
        if command is None:
            execution = "STARTED_UNKNOWN" if observed["command_started"] else "NOT_OBSERVED"
        elif command["returncode"] is None or (result is not None
                and result.get("schema_version") == SOURCE_EXECUTION_RESULT_V1 and command["returncode"] < 0):
            execution = "INTERRUPTED"
        else:
            execution = "EXITED_ZERO" if command["returncode"] == 0 else "EXITED_NONZERO"
        status = result["status"] if result is not None else ("PUBLISHED" if publication is not None else "INTERRUPTED")
        record = {"operation_id": claim["operation_id"], "claim": claim, "status": status,
            "verification": "VERIFIED" if observed["verification"] is not None else "UNVERIFIED",
            "execution": execution, "applicability": "UNASSESSED", "origin": origin,
            "observation_operation_id": observation_operation_id,
            "checkpoint_complete": operation["checkpoint_complete"], "head_sha256": operation["head_sha256"],
            "sources": observed["sources"], "bindings": observed["bindings"],
            "uninterpreted": observed["uninterpreted"], "command_result": command,
            "worktree_clean": observed["worktree_clean"], "runtime_unchanged": observed["runtime_unchanged"],
            "uncaptured_sources": [path for path in claim["sources"] if path not in {s["path"] for s in observed["sources"]}],
            "inputs": operation["inputs"], "observations": operation["observations"], "result": result,
            "publication": None if publication is None else {"transaction_id": publication["transaction_id"]},
            "retained": [{"ref": ref.to_dict(), "content_base64url": encode_base64url(raw)}
                         for ref, raw in sorted(evidence.items(), key=lambda item: canonical(item[0].to_dict()))]}
        texts = [evidence[HashBoundRef.from_dict(source["ref"])].decode("utf-8", errors="replace")
                 for source in observed["sources"]]
        eligible.append((*_ranked(profile, query_terms, matched_paths, claim, observed, texts), record))
    eligible.sort(key=lambda item: (*(-count for count in item[0]), item[2]["operation_id"]))
    if profile == SOURCE_RECALL_PROFILE_V2:
        # How many eligible operations share each key: within a group only operation_id ordered them.
        groups: dict[tuple, int] = {}
        for key, _, _ in eligible:
            groups[key] = groups.get(key, 0) + 1
        eligible = [(key, {**match, "tie_group": groups[key]}, record) for key, match, record in eligible]
    selected = [{"match": match, "experience": record} for _, match, record in eligible[:query["limit"]]]
    value = {"schema_version": _PROFILES[profile], "profile": profile,
        "query": query, "state": "MATCHES" if selected else "NO_RELEVANT_EXPERIENCE",
        "selection_scope": "SOURCE_OPERATIONS", "eligible_count": len(eligible),
        "omitted_by_limit": max(0, len(eligible) - len(selected)), "excluded": excluded, "selected": selected}
    if len(canonical(value)) > MAX_RECALL_BYTES:
        raise ValueError("source recall exceeds its response budget; narrow task scope or selection limit")
    return value


def export_source_knowledge(state_root: Path):
    """Export pointers to physically committed evidence, never create admission."""
    candidates, files = [], {}
    for result, request, prepared_path in source_publications(state_root):
        if retired_source_request(request):
            continue
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
