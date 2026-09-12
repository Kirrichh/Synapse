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

from synapse.canonical_values import canonical_json_bytes

from .input_contract import LocalInformationInput, WorkerInputViolation, WorkerTaskInput


LOCAL_EDIT_PROFILE_V1 = "mini-2.4.6-local-edit-proposals/v1"
LOCAL_EDIT_PROPOSAL_V1 = "synapse.worker.local-edit-proposal/v1"
LOCAL_EDIT_RESULT_V1 = "synapse.worker.local-edit-result/v1"
LOCAL_EDIT_COMMAND = "synapse-local-edit "
MAX_ALTERNATIVES = 8
MAX_EDITS = 16
MAX_SOURCE_BYTES = 1024 * 1024
MAX_PROPOSAL_BYTES = 256 * 1024

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


def propose_local_edits(*, task: WorkerTaskInput, information: LocalInformationInput, proposal: dict):
    """Assess every supplied alternative and select the first applicable one.

    Applicability here means exact local text binding and task scope only. It
    does not assert behavioral success, replay completion or independent proof
    that a previous method failed. No arbitrary prose is interpreted.
    """
    if type(task) is not WorkerTaskInput or type(information) is not LocalInformationInput:
        raise WorkerInputViolation("local edit requires exact separate worker inputs")
    proposal = parse_local_edit_command(LOCAL_EDIT_COMMAND + canonical_json_bytes(proposal).decode())
    requirement = task.to_dict()
    targets = {item["subject_path"] for item in requirement["effects"]
               if item["kind"] == "PATH_MODIFIED" and item["disposition"] == "EXPECTED"}
    forbidden = {item["subject_path"] for item in requirement["effects"]
                 if item["kind"] == "PATH_MODIFIED" and item["disposition"] == "FORBIDDEN" and item["subject_path"] is not None}
    sources = _source_inventory(information, requirement["repository_revision"])
    feedback = _execution_feedback(information)
    candidates, selected, selected_patch = [], None, None
    selected_paths = []
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
        candidate = {"index": index, "proposal_sha256": _digest(canonical_json_bytes(alternative)),
                     "applicability": "APPLICABLE" if reason is None else "INAPPLICABLE", "reason": reason,
                     "patch_sha256": patch_sha,
                     "feedback": "CONFLICTING" if len(feedback.get(patch_sha, ())) > 1 else "NO_CONFLICT",
                     "source_bindings": bindings if patch is not None else []}
        candidates.append(candidate)
        if reason is None and selected is None:
            selected, selected_patch = index, patch
            selected_paths = [binding["path"] for binding in bindings]
    return {"schema_version": LOCAL_EDIT_RESULT_V1, "profile": LOCAL_EDIT_PROFILE_V1,
            "task_sha256": _digest(task.canonical_bytes), "information_sha256": information.sha256,
            "proposal": proposal, "candidates": candidates, "selected_index": selected,
            "selection_rule": "FIRST_APPLICABLE_IN_PUBLIC_PROPOSAL_ORDER",
            "status": "NO_APPLICABLE_PROPOSAL" if selected is None else "UNVERIFIED_PATCH_PROPOSAL",
            "diff_text": selected_patch, "touched_files": selected_paths,
            "execution": "NO_REPOSITORY_EFFECTS"}


def validate_local_edit_result(value, *, task_sha256, information_sha256):
    """Validate the local result transport, without granting correctness/authority."""
    fields = {"schema_version", "profile", "task_sha256", "information_sha256", "proposal", "candidates",
              "selected_index", "selection_rule", "status", "diff_text", "touched_files", "execution"}
    if (type(value) is not dict or set(value) != fields or value["schema_version"] != LOCAL_EDIT_RESULT_V1
            or value["profile"] != LOCAL_EDIT_PROFILE_V1 or value["task_sha256"] != task_sha256
            or value["information_sha256"] != information_sha256
            or value["execution"] != "NO_REPOSITORY_EFFECTS"
            or value["selection_rule"] != "FIRST_APPLICABLE_IN_PUBLIC_PROPOSAL_ORDER"):
        raise WorkerInputViolation("local result differs from the dispatched profile or inputs")
    proposal = parse_local_edit_command(LOCAL_EDIT_COMMAND + canonical_json_bytes(value["proposal"]).decode())
    candidates = value["candidates"]
    if type(candidates) is not list or len(candidates) != len(proposal["alternatives"]):
        raise WorkerInputViolation("local result lacks its complete alternative inventory")
    first = None
    reasons = {"OUTSIDE_TASK_EDIT_CONSTRAINTS", "SOURCE_UNAVAILABLE", "SOURCE_AMBIGUOUS",
               "UNSUPPORTED_SOURCE_LINE_ENDINGS", "OLD_TEXT_NOT_UNIQUE", "NO_CHANGE",
               "UNSUPPORTED_RESULT_TEXT", "PATCH_TOO_LARGE", "EXACT_VERIFIED_PATCH_REJECTED"}
    for index, candidate in enumerate(candidates):
        if (type(candidate) is not dict or set(candidate) != {"index", "proposal_sha256", "applicability", "reason",
                "patch_sha256", "feedback", "source_bindings"} or type(candidate["index"]) is not int
                or candidate["index"] != index
                or candidate["proposal_sha256"] != _digest(canonical_json_bytes(proposal["alternatives"][index]))
                or candidate["feedback"] not in {"CONFLICTING", "NO_CONFLICT"}):
            raise WorkerInputViolation("local result has an invalid alternative binding")
        applicable = candidate["reason"] is None
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
        if applicable and first is None:
            first = index
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
