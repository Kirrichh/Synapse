"""Pure text-edit proposals over the worker's separate local information.

This Mini interpretation profile never reads a repository, runs code or applies
a patch. Source material is evidence for a proposal, not execution authority or
proof of correctness. The existing caller must independently apply and verify
the returned patch against its current task and repository.
"""

import base64
import difflib
import hashlib
import json
from pathlib import PurePosixPath
import re

from .input_contract import LocalInformationInput, WorkerInputViolation, WorkerTaskInput


LOCAL_EDIT_PROFILE_V1 = "mini-2.4.6-local-edit-proposals/v1"
LOCAL_EDIT_PROFILE_V2 = "mini-2.4.6-local-edit-proposals/v2"
LOCAL_EDIT_PROFILE_V3 = "mini-2.4.6-local-edit-proposals/v3"
LOCAL_EDIT_PROFILE_V4 = "mini-2.4.6-local-edit-proposals/v4"
LOCAL_EDIT_PROFILES = frozenset({LOCAL_EDIT_PROFILE_V1, LOCAL_EDIT_PROFILE_V2, LOCAL_EDIT_PROFILE_V3, LOCAL_EDIT_PROFILE_V4})
LOCAL_EDIT_PROPOSAL_V1 = "synapse.worker.local-edit-proposal/v1"
LOCAL_EDIT_RESULT_V1 = "synapse.worker.local-edit-result/v1"
LOCAL_EDIT_RESULT_V2 = "synapse.worker.local-edit-result/v2"
LOCAL_EDIT_RESULT_V3 = "synapse.worker.local-edit-result/v3"
LOCAL_EDIT_RESULT_V4 = "synapse.worker.local-edit-result/v4"
LOCAL_EDIT_COMMAND = "synapse-local-edit "
MAX_ALTERNATIVES = 8
MAX_EDITS = 16
MAX_SOURCE_BYTES = 1024 * 1024
MAX_PROPOSAL_BYTES = 256 * 1024
MAX_RETAINED_PATCHES = 128
_RESULT_FIELDS = {"schema_version", "profile", "task_sha256", "information_sha256", "proposal", "candidates",
                  "selected_index", "selection_rule", "status", "diff_text", "touched_files", "execution"}

LOCAL_EDIT_INSTRUCTIONS = '''
This run uses the local text-edit proposal profile. The bash tool is a transport
for one typed proposal; no shell command is executed. Its command must be
synapse-local-edit followed by one JSON object:
{"schema_version":"synapse.worker.local-edit-proposal/v1","alternatives":[
  {"edits":[{"path":"relative/path.py","old":"exact existing text","new":"replacement text"}]}]}
Propose at most eight alternatives, in preference order, using only the public
task. Each alternative contains at most sixteen edits, with one edit per path.
The local consumer checks the exact old text against separately supplied source
material. It returns an unverified patch proposal to the caller for independent
application and verification. Local material and results are never returned to
the model. An empty alternatives list means no supported proposal. Do not issue
shell commands, read files, invent tool results, or request local information.
'''


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _proposal_bytes(value):
    # Edit strings are literal source/replacement data. NFC normalization would
    # change the requested patch and collapse distinct public alternatives.
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise WorkerInputViolation("local proposal is not exact UTF-8 JSON") from exc


def _path(value):
    if (type(value) is not str or not value or "\\" in value or "\x00" in value
            or any(ord(char) < 32 or ord(char) > 126 for char in value)
            or any(char in value for char in ('"', '\t', ' ', ':'))):
        raise WorkerInputViolation("local edit requires a supported repository path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part.lower() in {".", "..", ".git"} for part in path.parts):
        raise WorkerInputViolation("local edit requires a supported repository path")
    return value


def parse_local_edit_command(command):
    """Parse public syntax without consulting or disclosing local material."""
    if (type(command) is not str or not command.startswith(LOCAL_EDIT_COMMAND)
            or len(command.encode("utf-8")) > MAX_PROPOSAL_BYTES):
        raise WorkerInputViolation("action is outside the local edit proposal profile")
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    try:
        value = json.loads(command[len(LOCAL_EDIT_COMMAND):], object_pairs_hook=unique)
        if (type(value) is not dict or set(value) != {"schema_version", "alternatives"}
                or value["schema_version"] != LOCAL_EDIT_PROPOSAL_V1
                or type(value["alternatives"]) is not list
                or len(value["alternatives"]) > MAX_ALTERNATIVES):
            raise ValueError("unsupported proposal")
        for alternative in value["alternatives"]:
            if (type(alternative) is not dict or set(alternative) != {"edits"}
                    or type(alternative["edits"]) is not list or not 0 < len(alternative["edits"]) <= MAX_EDITS):
                raise ValueError("unsupported alternative")
            paths = set()
            for edit in alternative["edits"]:
                if type(edit) is not dict or set(edit) != {"path", "old", "new"}:
                    raise ValueError("unsupported edit")
                path = _path(edit["path"])
                if path in paths:
                    raise ValueError("repeated target")
                paths.add(path)
                if (type(edit["old"]) is not str or not edit["old"] or type(edit["new"]) is not str
                        or "\x00" in edit["old"] or "\x00" in edit["new"]):
                    raise ValueError("unsupported edit text")
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise WorkerInputViolation("action is outside the local edit proposal profile") from exc
    return value


def _source_inventory(information, revision):
    """Read retained source bytes as data, regardless of the learning outcome.

    The source-experience port explicitly distinguishes retention from admission.
    A failed or unknown recipe does not invalidate the captured file and cannot
    itself become a method prohibition. Conflicting bytes remain ambiguous.
    """
    sources = {}
    for item in information.to_dict()["items"]:
        if item["role"] != "REFERENCE" or item["media_type"] != "application/json":
            continue
        try:
            encoded = item["content_base64url"]
            value = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        except (ValueError, UnicodeError, RecursionError):
            continue
        if (type(value) is not dict or value.get("repository_revision") != revision
                or value.get("applicability") != "UNASSESSED"
                or type(value.get("sources")) is not list):
            continue
        for source in value["sources"]:
            if type(source) is not dict or set(source) != {"path", "content_base64url"}:
                continue
            try:
                path = _path(source["path"])
                encoded = source["content_base64url"].encode("ascii")
                raw = base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
                if (not 0 < len(raw) <= MAX_SOURCE_BYTES or base64.urlsafe_b64encode(raw).rstrip(b"=") != encoded
                        or b"\x00" in raw):
                    continue
                raw.decode("utf-8")
            except (WorkerInputViolation, ValueError, UnicodeError, AttributeError):
                continue
            sources.setdefault(path, set()).add(raw)
    return sources


def _covers(scope, path):
    return any(path == root or path.startswith(root + "/") for root in scope)


def _execution_feedback(information):
    """Consume only the caller's exact, current-task execution-observation port.

    Source recipe failures and rejected prose are not this port. Conflicting
    observations do not establish an exclusion. The neutral port's caller owns
    independent verification and its task binding; Mini cannot mint that proof.
    """
    outcomes = {}
    for item in information.to_dict()["items"]:
        if item["role"] != "EXECUTION_OBSERVATION" or item["media_type"] != "application/json":
            continue
        try:
            text = item["content_base64url"]
            value = json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))
        except (ValueError, UnicodeError, RecursionError):
            continue
        if (type(value) is dict and set(value) == {"evaluated_patch_sha256", "oracle_resolved"}
                and type(value["evaluated_patch_sha256"]) is str
                and re.fullmatch(r"[0-9a-f]{64}", value["evaluated_patch_sha256"])
                and type(value["oracle_resolved"]) is bool):
            outcomes.setdefault(value["evaluated_patch_sha256"], set()).add(value["oracle_resolved"])
    return outcomes


def _retained_patch_edits(raw, sources):
    """Interpret the existing Mini unified-diff format over exact local bytes.

    This changes no file. Hunk offsets, counts and every old/context line must
    match, without fuzzy positioning. Binary, rename and other Git formats
    require another declared interpreter and are refused here.
    """
    if not 0 < len(raw) <= MAX_PROPOSAL_BYTES or b"\r" in raw or b"\x00" in raw:
        raise WorkerInputViolation("unsupported retained patch")
    text = raw.decode("utf-8")
    if not text.endswith("\n"):
        raise WorkerInputViolation("retained patch must have exact line endings")
    lines = [line + "\n" for line in text[:-1].split("\n")]
    position, edits, seen = 0, [], set()
    while position < len(lines):
        match = re.fullmatch(r"diff --git a/(\S+) b/(\S+)\n", lines[position])
        if match is None or match[1] != match[2]:
            raise WorkerInputViolation("retained patch has unsupported file headers")
        path = _path(match[1])
        if path in seen or len(seen) >= MAX_EDITS or lines[position + 1:position + 3] != [
            f"--- a/{path}\n", f"+++ b/{path}\n"]:
            raise WorkerInputViolation("retained patch has ambiguous file headers")
        seen.add(path)
        materials = sources.get(path, set())
        if len(materials) != 1:
            raise WorkerInputViolation("retained patch has no unique local source")
        before_raw, = materials
        before = before_raw.decode("utf-8")
        if not before.endswith("\n") or "\r" in before:
            raise WorkerInputViolation("retained source has unsupported line endings")
        original = [line + "\n" for line in before[:-1].split("\n")]
        position += 3
        cursor, output, hunks = 0, [], 0
        while position < len(lines) and lines[position].startswith("@@ "):
            header = re.fullmatch(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@\n", lines[position])
            if header is None:
                raise WorkerInputViolation("retained patch has an unsupported hunk")
            old_start, old_count, new_start, new_count = (
                int(header[1]), int(header[2] or 1), int(header[3]), int(header[4] or 1))
            old_at = old_start if old_count == 0 else old_start - 1
            new_at = new_start if new_count == 0 else new_start - 1
            if not cursor <= old_at <= len(original):
                raise WorkerInputViolation("retained hunk has a stale or overlapping source offset")
            output.extend(original[cursor:old_at])
            cursor = old_at
            if len(output) != new_at:
                raise WorkerInputViolation("retained hunk has another result offset")
            position += 1
            old_seen = new_seen = 0
            while old_seen < old_count or new_seen < new_count:
                if position >= len(lines) or lines[position][0] not in " +-":
                    raise WorkerInputViolation("retained patch has a truncated hunk")
                line = lines[position]
                if line[0] in " -":
                    if cursor >= len(original) or original[cursor] != line[1:]:
                        raise WorkerInputViolation("retained patch differs from the exact source")
                    cursor += 1
                    old_seen += 1
                if line[0] in " +":
                    output.append(line[1:])
                    new_seen += 1
                if old_seen > old_count or new_seen > new_count:
                    raise WorkerInputViolation("retained patch exceeds its hunk counts")
                position += 1
            hunks += 1
        output.extend(original[cursor:])
        after = "".join(output)
        if not hunks or after == before or not after.endswith("\n") or len(after.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise WorkerInputViolation("retained patch has no supported bounded change")
        edits.append({"path": path, "old": before, "new": after})
    if not edits:
        raise WorkerInputViolation("retained patch has no edits")
    return {"edits": edits}


def _propose_with_retained_patches(*, task, information, proposal, profile):
    """Bounded local methods, checked partial compositions, then public options.

    V4's caller must deliver a negative patch reference only after independent
    proof that its C1 contract passed while the whole-task oracle failed. This
    is a narrow input contract, not a claim Mini can derive from a failure label.
    A composition is new unverified data and inherits no successful outcome.
    """
    partials = profile == LOCAL_EDIT_PROFILE_V4
    proposal = parse_local_edit_command(LOCAL_EDIT_COMMAND + _proposal_bytes(proposal).decode())
    outcomes = _execution_feedback(information)
    sources = _source_inventory(information, task.to_dict()["repository_revision"])
    retained = {}
    for item in information.to_dict()["items"]:
        if item["role"] != "REFERENCE":
            continue
        encoded = item["content_base64url"]
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        digest = _digest(raw)
        if outcomes.get(digest) == {True} or partials and outcomes.get(digest) == {False}:
            retained[digest] = raw
    if len(retained) > MAX_RETAINED_PATCHES:
        raise WorkerInputViolation("retained patches exceed the local interpretation budget")
    alternatives, origins, excluded, omitted_compositions = [], [], [], []
    for digest, raw in sorted(retained.items()):
        if len(alternatives) >= MAX_ALTERNATIVES:
            excluded.append({"patch_sha256": digest, "reason": "CANDIDATE_LIMIT"})
            continue
        try:
            alternative = _retained_patch_edits(raw, sources)
            prospective = {"schema_version": LOCAL_EDIT_PROPOSAL_V1, "alternatives": alternatives + [alternative]}
            parse_local_edit_command(LOCAL_EDIT_COMMAND + _proposal_bytes(prospective).decode())
            # Preserve the exact historically verified patch encoding. Another
            # textual encoding is not silently assigned its oracle observation.
            checked = propose_local_edits(task=task, information=information,
                proposal={"schema_version": LOCAL_EDIT_PROPOSAL_V1, "alternatives": [alternative]}, profile=LOCAL_EDIT_PROFILE_V2)
            if checked["candidates"][0]["patch_sha256"] != digest:
                raise WorkerInputViolation("retained patch is inapplicable or uses another supported encoding")
        except (WorkerInputViolation, ValueError, UnicodeError, IndexError):
            excluded.append({"patch_sha256": digest, "reason": "PATCH_NOT_APPLICABLE"})
            continue
        if outcomes[digest] == {True}:
            alternatives.append(alternative)
            origins.append({"kind": "LOCAL_MEMORY", "patch_sha256": digest})
        else:
            partial_paths = {item["path"] for item in alternative["edits"]}
            for index, extension in enumerate(proposal["alternatives"]):
                combined = {"edits": sorted(alternative["edits"] + extension["edits"], key=lambda edit: edit["path"])}
                prospective = {"schema_version": LOCAL_EDIT_PROPOSAL_V1, "alternatives": alternatives + [combined]}
                reason = None
                if partial_paths.intersection(item["path"] for item in extension["edits"]):
                    reason = "OVERLAPPING_PATHS"
                elif len(alternatives) >= MAX_ALTERNATIVES:
                    reason = "CANDIDATE_LIMIT"
                else:
                    try:
                        parse_local_edit_command(LOCAL_EDIT_COMMAND + _proposal_bytes(prospective).decode())
                    except WorkerInputViolation:
                        reason = "COMPOSITION_TOO_LARGE"
                if reason is not None:
                    omitted_compositions.append({"patch_sha256": digest, "index": index, "reason": reason})
                    continue
                alternatives.append(combined)
                origins.append({"kind": "LOCAL_PARTIAL_MEMORY", "patch_sha256": digest, "index": index})
    omitted = []
    for index, alternative in enumerate(proposal["alternatives"]):
        prospective = {"schema_version": LOCAL_EDIT_PROPOSAL_V1, "alternatives": alternatives + [alternative]}
        if len(alternatives) >= MAX_ALTERNATIVES or len(_proposal_bytes(prospective)) > MAX_PROPOSAL_BYTES - len(LOCAL_EDIT_COMMAND):
            omitted.append(index)
            continue
        alternatives.append(alternative)
        origins.append({"kind": "PUBLIC_PROPOSAL", "index": index})
    effective = {"schema_version": LOCAL_EDIT_PROPOSAL_V1, "alternatives": alternatives}
    result = propose_local_edits(task=task, information=information, proposal=effective, profile=LOCAL_EDIT_PROFILE_V2)
    search = {"candidate_limit": MAX_ALTERNATIVES, "excluded_memory": excluded, "omitted_public": omitted}
    if partials:
        search["omitted_compositions"] = omitted_compositions
    return {**result, "schema_version": LOCAL_EDIT_RESULT_V4 if partials else LOCAL_EDIT_RESULT_V3, "profile": profile,
        "proposal": proposal, "effective_proposal": effective, "candidate_origins": origins,
        "search": search}


def _validate_retained_patch_result(value, *, task_sha256, information_sha256):
    partials = value.get("profile") == LOCAL_EDIT_PROFILE_V4
    extra = {"effective_proposal", "candidate_origins", "search"}
    if set(value) != _RESULT_FIELDS | extra or value.get("schema_version") != (LOCAL_EDIT_RESULT_V4 if partials else LOCAL_EDIT_RESULT_V3):
        raise WorkerInputViolation("retained-patch result has an unknown shape")
    public = parse_local_edit_command(LOCAL_EDIT_COMMAND + _proposal_bytes(value["proposal"]).decode())
    effective = parse_local_edit_command(LOCAL_EDIT_COMMAND + _proposal_bytes(value["effective_proposal"]).decode())
    validate_local_edit_result({**{key: item for key, item in value.items() if key not in extra},
        "schema_version": LOCAL_EDIT_RESULT_V2, "profile": LOCAL_EDIT_PROFILE_V2, "proposal": effective},
        task_sha256=task_sha256, information_sha256=information_sha256)
    origins = value["candidate_origins"]
    if type(origins) is not list or len(origins) != len(effective["alternatives"]):
        raise WorkerInputViolation("local method candidates lost their origins")
    memory, partial_memory, public_indices = [], [], []
    for index, origin in enumerate(origins):
        if type(origin) is not dict:
            raise WorkerInputViolation("local method origin is malformed")
        if origin.get("kind") == "LOCAL_MEMORY":
            candidate = value["candidates"][index]
            if (set(origin) != {"kind", "patch_sha256"} or public_indices
                    or origin["patch_sha256"] != candidate["patch_sha256"]
                    or candidate["prior_outcome"] is not True or candidate["applicability"] != "APPLICABLE"):
                raise WorkerInputViolation("local method is not bound to its applicable verified patch")
            memory.append(origin["patch_sha256"])
        elif partials and origin.get("kind") == "LOCAL_PARTIAL_MEMORY":
            source = origin.get("index")
            digest = origin.get("patch_sha256")
            if (set(origin) != {"kind", "patch_sha256", "index"} or public_indices
                    or type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                    or type(source) is not int or not 0 <= source < len(public["alternatives"])
                    or digest == value["candidates"][index]["patch_sha256"]):
                raise WorkerInputViolation("partial composition lost its distinct original patch and public extension")
            combined = effective["alternatives"][index]["edits"]
            extension = public["alternatives"][source]["edits"]
            if any(edit not in combined for edit in extension) or len(combined) <= len(extension):
                raise WorkerInputViolation("partial composition changed or lost its public extension")
            partial_memory.append((digest, source))
        elif origin.get("kind") == "PUBLIC_PROPOSAL":
            source = origin.get("index")
            if (set(origin) != {"kind", "index"} or type(source) is not int or not 0 <= source < len(public["alternatives"])
                    or effective["alternatives"][index] != public["alternatives"][source]):
                raise WorkerInputViolation("local method changed a public alternative")
            public_indices.append(source)
        else:
            raise WorkerInputViolation("local method origin is unknown")
    search = value["search"]
    search_fields = {"candidate_limit", "excluded_memory", "omitted_public"}
    if partials:
        search_fields.add("omitted_compositions")
    if (type(search) is not dict or set(search) != search_fields
            or type(search["candidate_limit"]) is not int or search["candidate_limit"] != MAX_ALTERNATIVES
            or type(search["excluded_memory"]) is not list or type(search["omitted_public"]) is not list
            or any(type(item) is not int for item in search["omitted_public"])
            or len(search["excluded_memory"]) + len(memory) + len({digest for digest, _ in partial_memory}) > MAX_RETAINED_PATCHES
            or memory != sorted(set(memory)) or public_indices != sorted(set(public_indices))
            or search["omitted_public"] != [index for index in range(len(public["alternatives"])) if index not in public_indices]):
        raise WorkerInputViolation("local method search lost its deterministic bounds")
    for excluded in search["excluded_memory"]:
        if (type(excluded) is not dict or set(excluded) != {"patch_sha256", "reason"}
                or type(excluded["patch_sha256"]) is not str or re.fullmatch(r"[0-9a-f]{64}", excluded["patch_sha256"]) is None
                or excluded["patch_sha256"] in memory or excluded["reason"] not in {"CANDIDATE_LIMIT", "PATCH_NOT_APPLICABLE"}):
            raise WorkerInputViolation("local method exclusion has an invalid reason or identity")
    excluded_ids = [item["patch_sha256"] for item in search["excluded_memory"]]
    if excluded_ids != sorted(set(excluded_ids)):
        raise WorkerInputViolation("local method exclusions are repeated or unordered")
    if partials:
        omitted = search["omitted_compositions"]
        if (type(omitted) is not list or len(omitted) > MAX_RETAINED_PATCHES * MAX_ALTERNATIVES
                or partial_memory != sorted(set(partial_memory))):
            raise WorkerInputViolation("partial composition search is unbounded or repeated")
        omitted_pairs = []
        for item in omitted:
            if (type(item) is not dict or set(item) != {"patch_sha256", "index", "reason"}
                    or type(item["patch_sha256"]) is not str or re.fullmatch(r"[0-9a-f]{64}", item["patch_sha256"]) is None
                    or type(item["index"]) is not int or not 0 <= item["index"] < len(public["alternatives"])
                    or item["reason"] not in {"OVERLAPPING_PATHS", "CANDIDATE_LIMIT", "COMPOSITION_TOO_LARGE"}):
                raise WorkerInputViolation("partial composition omission is malformed")
            omitted_pairs.append((item["patch_sha256"], item["index"]))
        if (omitted_pairs != sorted(set(omitted_pairs)) or set(omitted_pairs).intersection(partial_memory)
                or {digest for digest, _ in partial_memory + omitted_pairs}.intersection(memory + excluded_ids)
                or len(set(memory + excluded_ids + [digest for digest, _ in partial_memory + omitted_pairs])) > MAX_RETAINED_PATCHES):
            raise WorkerInputViolation("partial composition search has conflicting identities")
    return value


def propose_local_edits(*, task: WorkerTaskInput, information: LocalInformationInput, proposal: dict,
                        profile=LOCAL_EDIT_PROFILE_V1):
    """Assess every supplied alternative and select the first applicable one.

    Applicability here means exact local text binding and task scope only. It
    does not assert behavioral success, replay completion or independent proof
    that a previous method failed. No arbitrary prose is interpreted.
    """
    if type(task) is not WorkerTaskInput or type(information) is not LocalInformationInput:
        raise WorkerInputViolation("local edit requires exact separate worker inputs")
    if type(profile) is not str or profile not in LOCAL_EDIT_PROFILES:
        raise WorkerInputViolation("local edit requires a supported interpretation profile")
    if profile in {LOCAL_EDIT_PROFILE_V3, LOCAL_EDIT_PROFILE_V4}:
        return _propose_with_retained_patches(task=task, information=information, proposal=proposal, profile=profile)
    prefer_verified = profile == LOCAL_EDIT_PROFILE_V2
    proposal = parse_local_edit_command(LOCAL_EDIT_COMMAND + _proposal_bytes(proposal).decode())
    requirement = task.to_dict()
    targets = {item["subject_path"] for item in requirement["effects"]
               if item["kind"] == "PATH_MODIFIED" and item["disposition"] == "EXPECTED"}
    forbidden = {item["subject_path"] for item in requirement["effects"]
                 if item["kind"] == "PATH_MODIFIED" and item["disposition"] == "FORBIDDEN" and item["subject_path"] is not None}
    sources = _source_inventory(information, requirement["repository_revision"])
    feedback = _execution_feedback(information)
    candidates, selected, selected_patch = [], None, None
    selected_paths = []
    selected_priority = -1
    for index, alternative in enumerate(proposal["alternatives"]):
        patches, bindings, reason = [], [], None
        for edit in sorted(alternative["edits"], key=lambda value: value["path"]):
            path = edit["path"]
            if ("repository.edit" not in requirement["capabilities"] or path not in targets or _covers(forbidden, path)
                    or not _covers(requirement["allowed_scope"], path)):
                reason = "OUTSIDE_TASK_EDIT_CONSTRAINTS"
                break
            materials = sources.get(path, set())
            if len(materials) != 1:
                reason = "SOURCE_UNAVAILABLE" if not materials else "SOURCE_AMBIGUOUS"
                break
            raw, = materials
            before = raw.decode("utf-8")
            if not before.endswith("\n") or "\r" in before:
                reason = "UNSUPPORTED_SOURCE_LINE_ENDINGS"
                break
            start = before.find(edit["old"])
            if start < 0 or before.find(edit["old"], start + 1) >= 0:
                reason = "OLD_TEXT_NOT_UNIQUE"
                break
            after = before.replace(edit["old"], edit["new"], 1)
            if after == before:
                reason = "NO_CHANGE"
                break
            if not after.endswith("\n") or "\r" in after or len(after.encode("utf-8")) > MAX_SOURCE_BYTES:
                reason = "UNSUPPORTED_RESULT_TEXT"
                break
            patches.append(f"diff --git a/{path} b/{path}\n" + "".join(difflib.unified_diff(
                [line + "\n" for line in before[:-1].split("\n")],
                [line + "\n" for line in after[:-1].split("\n")], fromfile="a/" + path, tofile="b/" + path)))
            bindings.append({"path": path, "source_sha256": _digest(raw),
                             "result_sha256": _digest(after.encode("utf-8"))})
        patch = "".join(patches) if reason is None else None
        if patch is not None and len(patch.encode("utf-8")) > MAX_PROPOSAL_BYTES:
            reason, patch = "PATCH_TOO_LARGE", None
        patch_sha = None if patch is None else _digest(patch.encode("utf-8"))
        if patch_sha is not None and feedback.get(patch_sha) == {False}:
            reason = "EXACT_VERIFIED_PATCH_REJECTED"
        candidate = {"index": index, "proposal_sha256": _digest(_proposal_bytes(alternative)),
                     "applicability": "APPLICABLE" if reason is None else "INAPPLICABLE", "reason": reason,
                     "patch_sha256": patch_sha,
                     "feedback": "CONFLICTING" if len(feedback.get(patch_sha, ())) > 1 else "NO_CONFLICT",
                     "source_bindings": bindings if patch is not None else []}
        outcomes = feedback.get(patch_sha, set())
        if prefer_verified:
            candidate["prior_outcome"] = next(iter(outcomes)) if len(outcomes) == 1 else None
        candidates.append(candidate)
        priority = int(prefer_verified and outcomes == {True})
        if reason is None and (selected is None or priority > selected_priority):
            selected, selected_patch = index, patch
            selected_priority = priority
            selected_paths = [binding["path"] for binding in bindings]
    return {"schema_version": LOCAL_EDIT_RESULT_V2 if prefer_verified else LOCAL_EDIT_RESULT_V1, "profile": profile,
            "task_sha256": _digest(task.canonical_bytes), "information_sha256": information.sha256,
            "proposal": proposal, "candidates": candidates, "selected_index": selected,
            "selection_rule": "FIRST_VERIFIED_POSITIVE_ELSE_FIRST_APPLICABLE" if prefer_verified else "FIRST_APPLICABLE_IN_PUBLIC_PROPOSAL_ORDER",
            "status": "NO_APPLICABLE_PROPOSAL" if selected is None else "UNVERIFIED_PATCH_PROPOSAL",
            "diff_text": selected_patch, "touched_files": selected_paths,
            "execution": "NO_REPOSITORY_EFFECTS"}


def validate_local_edit_result(value, *, task_sha256, information_sha256):
    """Validate the local result transport, without granting correctness/authority."""
    if type(value) is dict and value.get("profile") in {LOCAL_EDIT_PROFILE_V3, LOCAL_EDIT_PROFILE_V4}:
        return _validate_retained_patch_result(value, task_sha256=task_sha256, information_sha256=information_sha256)
    fields = _RESULT_FIELDS
    prefer_verified = type(value) is dict and value.get("profile") == LOCAL_EDIT_PROFILE_V2
    if (type(value) is not dict or set(value) != fields
            or value["schema_version"] != (LOCAL_EDIT_RESULT_V2 if prefer_verified else LOCAL_EDIT_RESULT_V1)
            or type(value["profile"]) is not str or value["profile"] not in LOCAL_EDIT_PROFILES or value["task_sha256"] != task_sha256
            or value["information_sha256"] != information_sha256
            or value["execution"] != "NO_REPOSITORY_EFFECTS"
            or value["selection_rule"] != ("FIRST_VERIFIED_POSITIVE_ELSE_FIRST_APPLICABLE" if prefer_verified else "FIRST_APPLICABLE_IN_PUBLIC_PROPOSAL_ORDER")):
        raise WorkerInputViolation("local result differs from the dispatched profile or inputs")
    proposal = parse_local_edit_command(LOCAL_EDIT_COMMAND + _proposal_bytes(value["proposal"]).decode())
    candidates = value["candidates"]
    if type(candidates) is not list or len(candidates) != len(proposal["alternatives"]):
        raise WorkerInputViolation("local result lacks its complete alternative inventory")
    first = None
    best_priority = -1
    reasons = {"OUTSIDE_TASK_EDIT_CONSTRAINTS", "SOURCE_UNAVAILABLE", "SOURCE_AMBIGUOUS",
               "UNSUPPORTED_SOURCE_LINE_ENDINGS", "OLD_TEXT_NOT_UNIQUE", "NO_CHANGE",
               "UNSUPPORTED_RESULT_TEXT", "PATCH_TOO_LARGE", "EXACT_VERIFIED_PATCH_REJECTED"}
    for index, candidate in enumerate(candidates):
        candidate_fields = {"index", "proposal_sha256", "applicability", "reason", "patch_sha256", "feedback", "source_bindings"}
        if prefer_verified:
            candidate_fields.add("prior_outcome")
        if (type(candidate) is not dict or set(candidate) != candidate_fields or type(candidate["index"]) is not int
                or candidate["index"] != index
                or candidate["proposal_sha256"] != _digest(_proposal_bytes(proposal["alternatives"][index]))
                or candidate["feedback"] not in {"CONFLICTING", "NO_CONFLICT"}):
            raise WorkerInputViolation("local result has an invalid alternative binding")
        applicable = candidate["reason"] is None
        prior = candidate.get("prior_outcome")
        if prefer_verified and (prior is not None and type(prior) is not bool
                or candidate["feedback"] == "CONFLICTING" and prior is not None
                or (prior is False) != (candidate["reason"] == "EXACT_VERIFIED_PATCH_REJECTED")):
            raise WorkerInputViolation("local result changes its exact prior outcome")
        if (candidate["applicability"] != ("APPLICABLE" if applicable else "INAPPLICABLE")
                or not applicable and candidate["reason"] not in reasons):
            raise WorkerInputViolation("local result has an invalid applicability finding")
        has_patch = applicable or candidate["reason"] == "EXACT_VERIFIED_PATCH_REJECTED"
        if has_patch:
            if type(candidate["patch_sha256"]) is not str or re.fullmatch(r"[0-9a-f]{64}", candidate["patch_sha256"]) is None:
                raise WorkerInputViolation("local result lacks its patch identity")
            bindings = candidate["source_bindings"]
            paths = sorted(edit["path"] for edit in proposal["alternatives"][index]["edits"])
            if type(bindings) is not list or len(bindings) != len(paths):
                raise WorkerInputViolation("local result lacks its source bindings")
            for path, binding in zip(paths, bindings):
                if (type(binding) is not dict or set(binding) != {"path", "source_sha256", "result_sha256"}
                        or binding["path"] != path or any(type(binding[key]) is not str
                            or re.fullmatch(r"[0-9a-f]{64}", binding[key]) is None
                            for key in ("source_sha256", "result_sha256"))):
                    raise WorkerInputViolation("local result has an invalid source binding")
        elif candidate["patch_sha256"] is not None or candidate["source_bindings"] != []:
            raise WorkerInputViolation("inapplicable local result claims a patch")
        priority = int(prefer_verified and prior is True)
        if applicable and (first is None or priority > best_priority):
            first = index
            best_priority = priority
    selected = value["selected_index"]
    if selected != first or selected is not None and type(selected) is not int:
        raise WorkerInputViolation("local result selected another alternative")
    if selected is None:
        if (value["status"] != "NO_APPLICABLE_PROPOSAL" or value["diff_text"] is not None
                or value["touched_files"] != []):
            raise WorkerInputViolation("empty local result claims an effect")
    elif (value["status"] != "UNVERIFIED_PATCH_PROPOSAL" or type(value["diff_text"]) is not str
            or not 0 < len(value["diff_text"].encode("utf-8")) <= MAX_PROPOSAL_BYTES
            or _digest(value["diff_text"].encode("utf-8")) != candidates[selected]["patch_sha256"]
            or value["touched_files"] != [item["path"] for item in candidates[selected]["source_bindings"]]):
        raise WorkerInputViolation("selected local patch differs from its result binding")
    return value
