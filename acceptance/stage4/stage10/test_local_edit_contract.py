"""Local applicability changes a patch proposal; it never establishes success."""

import base64
from copy import deepcopy
import hashlib

import pytest

from synapse.canonical_values import canonical_json_bytes
from synapse.worker.input_contract import LocalInformationInput, WorkerInputViolation, WorkerTaskInput
from synapse.worker.local_edits import (
    LOCAL_EDIT_COMMAND, LOCAL_EDIT_PROPOSAL_V1, LOCAL_EDIT_PROFILE_V1,
    parse_local_edit_command, propose_local_edits, validate_local_edit_result,
)


def task(*, revision="a" * 40):
    return WorkerTaskInput(canonical_json_bytes({
        "schema_version": "synapse.worker.task-input/v1", "statement": "Replace the subtraction in src/calc.py with addition.",
        "repository_revision": revision, "allowed_scope": ["src"], "capabilities": ["repository.edit"],
        "effects": [{"kind": "PATH_MODIFIED", "disposition": "EXPECTED", "subject_path": "src/calc.py"}],
        "acceptance": [{"kind": "CONTRACT_CONDITION", "argv": []}]}))


def encoded(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def information(*, source="def add(a, b):\n    return a - b\n", revision="a" * 40, status="REJECTED", extra=()):
    records = [] if source is None else [{"role": "REFERENCE", "media_type": "application/json",
        "content_base64url": encoded(canonical_json_bytes({"repository_revision": revision, "status": status,
            "applicability": "UNASSESSED", "sources": [{"path": "src/calc.py", "content_base64url": encoded(source.encode())}]}))}]
    return LocalInformationInput(canonical_json_bytes({"schema_version": "synapse.worker.local-information-input/v1",
                                                       "items": records + list(extra)}))


def proposal(*replacements):
    return {"schema_version": LOCAL_EDIT_PROPOSAL_V1, "alternatives": [
        {"edits": [{"path": "src/calc.py", "old": old, "new": new}]} for old, new in replacements]}


def feedback(patch, outcome, *, role="EXECUTION_OBSERVATION"):
    return {"role": role, "media_type": "application/json", "content_base64url": encoded(canonical_json_bytes({
        "evaluated_patch_sha256": hashlib.sha256(patch.encode()).hexdigest(), "oracle_resolved": outcome}))}


def test_local_source_selects_an_applicable_variant_and_retains_all_reasons():
    private = information()
    public = task()
    result = propose_local_edits(task=public, information=private,
        proposal=proposal(("return x - y", "return x + y"), ("return a - b", "return a + b"),
                          ("return a - b", "return a * b")))
    assert result["selected_index"] == 1
    assert [item["applicability"] for item in result["candidates"]] == ["INAPPLICABLE", "APPLICABLE", "APPLICABLE"]
    assert result["candidates"][0]["reason"] == "OLD_TEXT_NOT_UNIQUE"
    assert "-    return a - b\n+    return a + b\n" in result["diff_text"]
    assert result["execution"] == "NO_REPOSITORY_EFFECTS"
    assert result["status"] == "UNVERIFIED_PATCH_PROPOSAL"
    assert validate_local_edit_result(result, task_sha256=hashlib.sha256(public.canonical_bytes).hexdigest(),
                                      information_sha256=private.sha256) == result


@pytest.mark.parametrize("status", ["PUBLISHED", "REJECTED", "INTERRUPTED", "INFRA_ERROR", "UNKNOWN"])
def test_recipe_outcome_does_not_discard_its_captured_source_or_ban_a_method(status):
    result = propose_local_edits(task=task(), information=information(status=status),
                                proposal=proposal(("a - b", "a + b")))
    assert result["selected_index"] == 0


@pytest.mark.parametrize("private,reason", [
    (information(source=None), "SOURCE_UNAVAILABLE"),
    (information(revision="b" * 40), "SOURCE_UNAVAILABLE"),
    (information(source="a - b\na - b\n"), "OLD_TEXT_NOT_UNIQUE"),
    (information(source="a - b"), "UNSUPPORTED_SOURCE_LINE_ENDINGS"),
    (information(extra=information(source="different\n").to_dict()["items"]), "SOURCE_AMBIGUOUS"),
])
def test_unavailable_or_ambiguous_information_cannot_become_a_fabricated_patch(private, reason):
    result = propose_local_edits(task=task(), information=private, proposal=proposal(("a - b", "a + b")))
    assert result["candidates"][0]["reason"] == reason
    assert result["status"] == "NO_APPLICABLE_PROPOSAL"
    assert result["diff_text"] is None


def test_exact_independent_negative_feedback_excludes_only_that_patch_before_effects():
    variants = proposal(("a - b", "a * b"), ("a - b", "a + b"))
    first = propose_local_edits(task=task(), information=information(), proposal=variants)
    bad_patch = first["diff_text"]
    after = propose_local_edits(task=task(), information=information(extra=[feedback(bad_patch, False)]), proposal=variants)
    assert after["selected_index"] == 1
    assert after["candidates"][0]["reason"] == "EXACT_VERIFIED_PATCH_REJECTED"
    for observation in [feedback(bad_patch, None), feedback(bad_patch, False, role="REFERENCE")]:
        unknown = propose_local_edits(task=task(), information=information(extra=[observation]), proposal=variants)
        assert unknown["selected_index"] == 0
    contradictory = propose_local_edits(task=task(), information=information(extra=[
        feedback(bad_patch, False), feedback(bad_patch, True)]), proposal=variants)
    assert contradictory["selected_index"] == 0
    assert contradictory["candidates"][0]["feedback"] == "CONFLICTING"


@pytest.mark.parametrize("change", [
    lambda value: value.update(capabilities=["repository.read"]),
    lambda value: value.update(allowed_scope=["elsewhere"]),
    lambda value: value["effects"].append({"kind": "PATH_MODIFIED", "disposition": "FORBIDDEN", "subject_path": "src"}),
])
def test_local_material_cannot_widen_task_constraints(change):
    data = task().to_dict()
    change(data)
    result = propose_local_edits(task=WorkerTaskInput(canonical_json_bytes(data)), information=information(),
                                proposal=proposal(("a - b", "a + b")))
    assert result["candidates"][0]["reason"] == "OUTSIDE_TASK_EDIT_CONSTRAINTS"


@pytest.mark.parametrize("command", ["touch OWNED", LOCAL_EDIT_COMMAND + '{"schema_version":1,"schema_version":2}',
    LOCAL_EDIT_COMMAND + canonical_json_bytes(proposal(("a", "b")) | {"shell": "touch OWNED"}).decode(),
    LOCAL_EDIT_COMMAND + '{"schema_version":"synapse.worker.local-edit-proposal/v1","alternatives":false}'])
def test_proposal_protocol_has_no_shell_or_ambiguous_json_escape(command):
    with pytest.raises(WorkerInputViolation):
        parse_local_edit_command(command)


@pytest.mark.parametrize("change", [
    lambda value: value.update(selected_index=True),
    lambda value: value.update(diff_text="forged patch"),
    lambda value: value.update(information_sha256="f" * 64),
    lambda value: value.update(touched_files=["outside.py"]),
    lambda value: value["candidates"][0].update(reason="UNKNOWN", applicability="INAPPLICABLE"),
])
def test_result_transport_cannot_change_the_selected_proposal_or_input_binding(change):
    public, private = task(), information()
    result = propose_local_edits(task=public, information=private, proposal=proposal(("a - b", "a + b")))
    changed = deepcopy(result)
    change(changed)
    with pytest.raises(WorkerInputViolation):
        validate_local_edit_result(changed, task_sha256=result["task_sha256"], information_sha256=private.sha256)


def test_worker_profile_is_explicit_and_legacy_declarations_keep_their_meaning():
    from synapse.experiments.gold.stage10_composition import decode_worker_configuration
    from synapse.worker.input_contract import SPLIT_INPUT_PROFILE_V1
    declaration = {"provider": "mini", "command": ["mini"], "model": "gemini-3.1-flash-lite",
                   "timeout_seconds": 60, "max_steps": 3, "cost_limit": "1"}
    assert decode_worker_configuration(declaration).input_profile == SPLIT_INPUT_PROFILE_V1
    assert decode_worker_configuration(declaration | {"input_profile": LOCAL_EDIT_PROFILE_V1}).input_profile == LOCAL_EDIT_PROFILE_V1
    with pytest.raises(ValueError):
        decode_worker_configuration(declaration | {"input_profile": "unknown"})


def test_overlapping_matches_are_ambiguous_and_unicode_line_characters_remain_text():
    ambiguous = propose_local_edits(task=task(), information=information(source="ababa\n"),
                                   proposal=proposal(("aba", "fixed")))
    assert ambiguous["candidates"][0]["reason"] == "OLD_TEXT_NOT_UNIQUE"
    unicode_text = propose_local_edits(task=task(), information=information(source="old\u2028text\n"),
                                      proposal=proposal(("old", "new")))
    assert "@@ -1 +1 @@" in unicode_text["diff_text"]
    assert "-old\u2028text\n+new\u2028text\n" in unicode_text["diff_text"]


@pytest.mark.parametrize("path", ["../escape", "src/../../escape", "src/.GIT/config", "C:escape", "src/a b.py"])
def test_unsupported_patch_paths_are_refused(path):
    value = proposal(("a", "b"))
    value["alternatives"][0]["edits"][0]["path"] = path
    with pytest.raises(WorkerInputViolation):
        parse_local_edit_command(LOCAL_EDIT_COMMAND + canonical_json_bytes(value).decode())
