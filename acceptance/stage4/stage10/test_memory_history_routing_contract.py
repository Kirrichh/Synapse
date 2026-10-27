"""Current exact applicability remains independent of historical ambiguity."""
import hashlib

import pytest

from acceptance.stage4.stage10.test_local_edit_contract import (
    encoded, feedback, information, proposal, task,
)
from synapse.canonical_values import canonical_json_bytes
from synapse.experiments.gold.source_delivery import _adaptive_memory_items
from synapse.worker.local_edits import propose_local_edits, propose_verified_memory, validate_local_edit_result


SOURCE = "def add(a, b):\n    return a - b\n"
SOURCES = (SOURCE, "# second\n" + SOURCE, "# third\n# second\n" + SOURCE)
REVISIONS = ("a" * 40, "b" * 40, "c" * 40)


def _hint(revision, patches, *, profile="exact-verified-patch-memory/v1", generalization="NOT_ESTABLISHED"):
    return {"role": "REFERENCE", "media_type": "application/json", "content_base64url": encoded(
        canonical_json_bytes({"repository_revision": revision, "procedural_memory": {
            "profile": profile, "generalization": generalization, "patches": sorted(patches)}}))}


def _history_case():
    patches = tuple(propose_local_edits(task=task(revision=revision),
        information=information(source=source, revision=revision),
        proposal=proposal(("a - b", "a + b")))["diff_text"]
        for source, revision in zip(SOURCES, REVISIONS))
    history = [{"repository_revision": revision, "diff_text": patch,
                "patch_sha256": hashlib.sha256(patch.encode()).hexdigest()}
               for revision, patch in zip(REVISIONS, patches)]
    return patches, history


def _historical_items(history, profile):
    if profile == "adaptive-verified-patch-memory/v1":
        return _adaptive_memory_items({"court": {"adaptive_patches": history}})
    items = []
    for entry in history:
        items.extend(({"role": "HISTORICAL_EXECUTION_OBSERVATION", "media_type": "application/json", "content_base64url": encoded(
            canonical_json_bytes({"historical_patch_observation": {"repository_revision": entry["repository_revision"],
                "patch_sha256": entry["patch_sha256"], "oracle_resolved": True}}))},
            {"role": "REFERENCE", "media_type": "text/plain",
             "content_base64url": encoded(entry["diff_text"].encode())},
            _hint(entry["repository_revision"], [entry["patch_sha256"]], profile=profile)))
    return items


def _current_items(patch):
    return [feedback(patch, True),
        {"role": "REFERENCE", "media_type": "text/plain", "content_base64url": encoded(patch.encode())},
        _hint(REVISIONS[-1], [hashlib.sha256(patch.encode()).hexdigest()])]


def _current_information(items):
    return information(source=SOURCES[-1], revision=REVISIONS[-1], extra=items)


@pytest.mark.parametrize("profile", ["adaptive-verified-patch-memory/v1", "exact-verified-patch-memory/v1"])
def test_multiple_historical_revisions_do_not_suppress_the_unique_current_exact_route(profile):
    patches, history = _history_case()
    public = task(revision=REVISIONS[-1])
    current_items = _current_items(patches[-1])
    baseline = propose_verified_memory(task=public, information=_current_information(current_items))
    assert baseline["planning_route"] == "EXACT_MEMORY"
    private = _current_information(current_items + _historical_items(history[:2], profile))
    result = propose_verified_memory(task=public, information=private)
    assert result is not None
    assert result["planning_route"] == "EXACT_MEMORY"
    assert result["diff_text"] == baseline["diff_text"] == patches[-1]
    assert result["candidate_origins"] == baseline["candidate_origins"]
    assert result["status"] == "UNVERIFIED_PATCH_PROPOSAL"
    assert result["execution"] == "NO_REPOSITORY_EFFECTS"
    assert validate_local_edit_result(result, task_sha256=result["task_sha256"],
                                     information_sha256=private.sha256) == result


@pytest.mark.parametrize("profile", ["adaptive-verified-patch-memory/v1", "exact-verified-patch-memory/v1"])
def test_historical_ambiguity_still_requires_ordinary_planning_without_an_exact_candidate(profile):
    patches, history = _history_case()
    public = task(revision=REVISIONS[-1])
    individual = [propose_verified_memory(task=public, information=_current_information(
        _historical_items([entry], profile))) for entry in history[:2]]
    assert all(result["planning_route"] == "ADAPT_MEMORY" for result in individual)
    assert individual[0]["diff_text"] == individual[1]["diff_text"] == patches[-1]
    ambiguous = _current_information(_historical_items(history[:2], profile))
    assert propose_verified_memory(task=public, information=ambiguous) is None


def test_conflicting_history_is_not_resolved_by_discarding_an_inapplicable_method():
    _, history = _history_case()
    different_source = SOURCE.replace("a - b", "b - a")
    different_patch = propose_local_edits(task=task(revision=REVISIONS[1]),
        information=information(source=different_source, revision=REVISIONS[1]),
        proposal=proposal(("b - a", "a + b")))["diff_text"]
    history[1] = {"repository_revision": REVISIONS[1], "diff_text": different_patch,
                  "patch_sha256": hashlib.sha256(different_patch.encode()).hexdigest()}
    profile = "adaptive-verified-patch-memory/v1"
    public = task(revision=REVISIONS[-1])
    first = propose_verified_memory(task=public, information=_current_information(_historical_items(history[:1], profile)))
    assert first["planning_route"] == "ADAPT_MEMORY"
    assert propose_verified_memory(task=public,
        information=_current_information(_historical_items(history[1:2], profile))) is None
    assert propose_verified_memory(task=public,
        information=_current_information(_historical_items(history[:2], profile))) is None


@pytest.mark.parametrize("conflicting_patches", [[], ["d" * 64]])
def test_current_exact_declaration_conflicts_still_refuse_automation(conflicting_patches):
    patches, history = _history_case()
    items = _current_items(patches[-1]) + _historical_items(history[:2], "adaptive-verified-patch-memory/v1")
    items.append(_hint(REVISIONS[-1], conflicting_patches))
    assert propose_verified_memory(task=task(revision=REVISIONS[-1]),
                                   information=_current_information(items)) is None


def test_history_does_not_override_current_negative_feedback():
    patches, history = _history_case()
    items = _current_items(patches[-1]) + _historical_items(history[:2], "adaptive-verified-patch-memory/v1")
    items.append(feedback(patches[-1], False))
    assert propose_verified_memory(task=task(revision=REVISIONS[-1]),
                                   information=_current_information(items)) is None


def test_history_cannot_admit_an_undeclared_current_patch():
    patches, history = _history_case()
    items = _current_items(patches[-1])[:-1] + _historical_items(history[:2], "adaptive-verified-patch-memory/v1")
    assert propose_verified_memory(task=task(revision=REVISIONS[-1]),
                                   information=_current_information(items)) is None


def test_historical_success_is_not_current_execution_feedback():
    _, history = _history_case()
    historical = _historical_items(history[:1], "adaptive-verified-patch-memory/v1")
    private = information(source=SOURCE, revision=REVISIONS[-1], extra=historical)
    assert propose_verified_memory(task=task(revision=REVISIONS[-1]), information=private) is None
    assert all(item["role"] != "EXECUTION_OBSERVATION" for item in historical)
    rejected = information(source=SOURCE, revision=REVISIONS[-1],
                           extra=historical + [feedback(history[0]["diff_text"], False)])
    assert propose_verified_memory(task=task(revision=REVISIONS[-1]), information=rejected) is None


def test_historical_declaration_without_success_evidence_does_not_adapt():
    patches, _ = _history_case()
    digest = hashlib.sha256(patches[0].encode()).hexdigest()
    items = [{"role": "REFERENCE", "media_type": "text/plain",
              "content_base64url": encoded(patches[0].encode())}, _hint(REVISIONS[0], [digest])]
    private = information(source=SOURCES[-1], revision=REVISIONS[-1], extra=items)
    assert propose_verified_memory(task=task(revision=REVISIONS[-1]), information=private) is None


def test_historical_observation_must_match_the_declared_revision():
    _, history = _history_case()
    items = _historical_items(history[:1], "adaptive-verified-patch-memory/v1")
    from copy import deepcopy
    import base64
    import json
    items = deepcopy(items)
    observation = items[0]
    raw = observation['content_base64url']
    body = json.loads(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))
    body['historical_patch_observation']['repository_revision'] = REVISIONS[1]
    observation['content_base64url'] = encoded(canonical_json_bytes(body))
    private = information(source=SOURCES[-1], revision=REVISIONS[-1], extra=items)
    assert propose_verified_memory(task=task(revision=REVISIONS[-1]), information=private) is None


def test_current_exact_candidate_is_not_ambiguous_with_applicable_history():
    historical_patch = propose_local_edits(task=task(revision=REVISIONS[0]),
        information=information(source=SOURCE, revision=REVISIONS[0]),
        proposal=proposal(("a - b", "a + b")))["diff_text"]
    current_patch = propose_local_edits(task=task(revision=REVISIONS[-1]),
        information=information(source=SOURCE, revision=REVISIONS[-1]),
        proposal=proposal(("a - b", "b + a")))["diff_text"]
    history = [{"repository_revision": REVISIONS[0], "diff_text": historical_patch,
                "patch_sha256": hashlib.sha256(historical_patch.encode()).hexdigest()}]
    current = _current_items(current_patch)
    public = task(revision=REVISIONS[-1])
    alone = propose_verified_memory(task=public,
        information=information(source=SOURCE, revision=REVISIONS[-1], extra=current))
    combined = propose_verified_memory(task=public, information=information(source=SOURCE,
        revision=REVISIONS[-1], extra=current + _historical_items(history, "adaptive-verified-patch-memory/v1")))
    assert alone is not None
    assert alone["planning_route"] == "EXACT_MEMORY"
    assert combined is not None
    assert combined["planning_route"] == "EXACT_MEMORY"
    assert combined["diff_text"] == current_patch

    # A current execution observation for the other patch does not make that
    # historical declaration a current exact candidate.
    observed = information(source=SOURCE, revision=REVISIONS[-1], extra=current
        + _historical_items(history, "adaptive-verified-patch-memory/v1") + [feedback(historical_patch, True)])
    observed_proposal = propose_verified_memory(task=public, information=observed)
    assert observed_proposal is not None
    assert observed_proposal["diff_text"] == current_patch


def test_two_current_exact_candidates_are_still_ambiguous():
    public = task(revision=REVISIONS[-1])
    private = information(source=SOURCE, revision=REVISIONS[-1])
    patches = [propose_local_edits(task=public, information=private,
        proposal=proposal(("a - b", replacement)))["diff_text"] for replacement in ("a + b", "b + a")]
    digests = [hashlib.sha256(patch.encode()).hexdigest() for patch in patches]
    items = [item for patch in patches for item in [feedback(patch, True),
        {"role": "REFERENCE", "media_type": "text/plain", "content_base64url": encoded(patch.encode())}]]
    items.append(_hint(REVISIONS[-1], digests))
    assert propose_verified_memory(task=public,
        information=information(source=SOURCE, revision=REVISIONS[-1], extra=items)) is None


def test_malformed_historical_claim_still_refuses_an_available_exact_candidate():
    patches, _ = _history_case()
    items = _current_items(patches[-1]) + [_hint(REVISIONS[0], ["d" * 64], generalization="ESTABLISHED")]
    assert propose_verified_memory(task=task(revision=REVISIONS[-1]),
                                   information=_current_information(items)) is None


def test_ordinary_reference_cannot_claim_historical_oracle_evidence():
    from copy import deepcopy
    _, history = _history_case()
    items = deepcopy(_historical_items(history[:1], 'adaptive-verified-patch-memory/v1'))
    items[0]['role'] = 'REFERENCE'
    private = information(source=SOURCES[-1], revision=REVISIONS[-1], extra=items)
    assert propose_verified_memory(task=task(revision=REVISIONS[-1]), information=private) is None
