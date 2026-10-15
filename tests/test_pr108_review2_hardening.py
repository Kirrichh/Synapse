"""Acceptance coverage for the second external review of the PR108 fixes.

Each test pins one finding of the 2026-10-13 archive review (round 2):

* S1 - a legacy single-result artifact of a program whose identical llm()
      prompts repeat must keep replaying with drift=0: the legacy entry is a
      reusable answer, not a one-occurrence queue.
* S2 - llm calls and thought steps sharing one prompt serialize into the
      artifact cache in true execution order (thought first when thought ran
      first), and the merged order is what mock replay serves.
* S3 - a derived registry far over the memory event budget publishes inside
      a byte budget: historic owners degrade to counts-only publication with
      the decision ref their grounds rebuild from, the active owner keeps
      its full profile, and the frame's share of the event budget stays
      positive.
* S4 - a candidate that was proposed but never judged (INFRA_ERROR /
      UNCERTAIN attempt, later refusal without a candidate) does not
      establish DEFECT_ESTABLISHED; a judged assertion still does.
* S5 - a policy declared under a soft keyword name is referenced by the same
      name in every `under <policy>` position.

The negative controls are part of the acceptance: a check that cannot fail
is not evidence.
"""
import json

import pytest

from synapse.builtins import LLMBackend
from synapse.experiments.gold.owner_profiles import (
    ACTIVE_MEMORY_FRAME_V4,
    MAX_REGISTRY_PUBLICATION_BYTES,
    OWNER_REGISTRY_V1,
    _category,
    apply_owner_profiles,
    bound_owner_registry,
    build_owner_registry,
)
from synapse.experiments.gold.project_agents import _frame_byte_budget
from synapse.experiments.gold.project_frame_storage import MAX_FRAME_PUBLICATION_BYTES
from synapse.experiments.gold.source_verification import canonical
from synapse.experiments.gold.stage10.intent import EffectDisposition, EffectKind
from synapse.golden_replay import (
    CacheOnlyLLMBackend,
    _merge_llm_cache,
    extract_llm_cache,
    record_source,
    replay_mock_artifact,
)
from synapse.lexer import Lexer
from synapse.parser import Parser

ELEMENT = "src/element_a.py"


# --------------------------------------------------------------------------
# Synthetic court machinery (same shapes the consolidation court emits)
# --------------------------------------------------------------------------
def episode(run_id, outcome, verdict, paths, attempts, assertions=()):
    observation = {
        "schema_version": "synapse.stage4.gold.episode-outcome/v1",
        "outcome_ref": {"transaction_id": f"t-{run_id}", "ref": {"sha256": run_id, "media_type": "x"}},
        "run_id": run_id,
        "requirement": {
            "task_contract_ref": {"sha256": f"tc-{run_id}", "media_type": "x"},
            "repository_revision": "r1", "paths": list(paths),
            "outcome": outcome, "run_status": "FULL" if outcome == "FULFILLED" else "FAIL",
            "controller_status": "FULL" if outcome == "FULFILLED" else "FAIL",
            "basis": [{"attempt_id": 1, "role": "VERIFICATION", "ref": {"sha256": "v", "media_type": "x"}}],
        },
        "attempts": attempts, "recovery": None, "uncertainties": []}
    learning = {"schema_version": "synapse.stage4.gold.episode-learning/v1",
                "outcome_ref": observation["outcome_ref"], "assertions": list(assertions), "unresolved": []}
    return {"outcome": {"transaction_id": f"o-{run_id}", "ref": {"sha256": run_id, "media_type": "x"}},
            "verdict": verdict, "reasons": ["INDEPENDENTLY_VERIFIED"],
            "decision": {"transaction_id": f"d-{run_id}", "ref": {"sha256": run_id, "media_type": "x"}},
            "result_ref": {"sha256": run_id}, "learning": learning, "observation": observation}


def court_of(judged):
    return {"decision": {"transaction_id": "d-head", "ref": {"sha256": "head"}},
            "judged": list(judged), "pending": [], "subjects": {}}


class _Ref:
    def to_dict(self):
        return {"sha256": "task", "media_type": "x"}


class _Effect:
    def __init__(self, cid, kind, path):
        self.constraint_id, self.kind, self.subject_path = cid, EffectKind(kind), path
        self.disposition = EffectDisposition.EXPECTED


class _Task:
    reference = _Ref()

    def __init__(self, paths):
        self.effects = [_Effect(f"e{i}", "PATH_MODIFIED", p) for i, p in enumerate(paths)]


class ScriptedBackend(LLMBackend):
    """Deterministic backend whose repeated identical prompts answer differently."""

    def __init__(self, responses):
        super().__init__(default_model="mock", provider="scripted", mode="mock")
        self.responses = list(responses)
        self.seen = []

    def complete(self, prompt, model=None, temperature=0.7, max_tokens=100, privacy_context=None):
        text = self.responses[len(self.seen)]
        self.seen.append(text)
        return text


LLM_REPEAT_SOURCE = '''
fn main() {
    let first = llm("analyze cluster")
    print(first)
    let second = llm("analyze cluster")
    print(second)
}
'''

THOUGHT_LLM_SOURCE = '''
fn main() {
    let observed = thought { "analyze cluster" }
    print(observed)
    let direct = llm("analyze cluster")
    print(direct)
}
'''


# --------------------------------------------------------------------------
# S1: legacy single-result artifacts of repeated identical llm prompts
# --------------------------------------------------------------------------
def test_legacy_single_result_artifact_of_repeated_llm_prompts_replays(tmp_path):
    """The exact review case: two identical llm() prompts recorded by the
    previous version (one entry, the last result) replay with drift=0."""
    artifact = tmp_path / "legacy"
    record_source(LLM_REPEAT_SOURCE, artifact, source_path="acceptance/llm_repeat.syn",
                  llm_backend=ScriptedBackend(["observation-1", "observation-1"]))
    cache = json.loads((artifact / "llm_cache.mock.json").read_text())
    legacy = {key: {"prompt_hash": value["prompt_hash"], "model": value["model"],
                    "result": value["results"][-1]}
              for key, value in cache.items()}
    (artifact / "llm_cache.mock.json").write_text(json.dumps(legacy))
    result = replay_mock_artifact(artifact)
    assert result.drift == 0


def test_legacy_single_result_entry_is_reusable_not_exhaustible():
    import hashlib
    key = f"{hashlib.sha256(b'k').hexdigest()}:mock"
    legacy = CacheOnlyLLMBackend({key: {"prompt_hash": key.split(':')[0], "model": "mock",
                                        "result": "old-style"}})
    assert legacy.complete("k") == "old-style"
    assert legacy.complete("k") == "old-style"
    assert legacy.complete("k") == "old-style"  # never exhausted, as before


def test_occurrence_list_entry_still_exhausts():
    """Negative control: the reusable rule is only for the legacy format."""
    import hashlib
    key = f"{hashlib.sha256(b'k').hexdigest()}:mock"
    modern = CacheOnlyLLMBackend({key: {"prompt_hash": key.split(':')[0], "model": "mock",
                                        "results": ["only-once"]}})
    assert modern.complete("k") == "only-once"
    with pytest.raises(Exception, match="more often than recorded"):
        modern.complete("k")


def test_new_recording_of_repeated_llm_prompts_still_roundtrips(tmp_path):
    """The current format keeps per-occurrence answers and replays them."""
    artifact = tmp_path / "modern"
    record_source(LLM_REPEAT_SOURCE, artifact, source_path="acceptance/llm_repeat.syn",
                  llm_backend=ScriptedBackend(["observation-1", "observation-2"]))
    cache = json.loads((artifact / "llm_cache.mock.json").read_text())
    assert all(len(value["results"]) == 2 for value in cache.values())
    result = replay_mock_artifact(artifact)
    assert result.drift == 0


# --------------------------------------------------------------------------
# S2: one observation order for llm calls and thought steps
# --------------------------------------------------------------------------
def test_thought_first_merge_orders_by_execution_position():
    """The review transcript: thought ran first (observation-1), llm second
    (observation-2). The thought step saw an empty history (position 0); the
    llm_call event was appended at index 0 once its call completed."""
    import hashlib

    prompt = "analyze cluster"
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    key = f"{prompt_hash}:mock"
    history = [{"type": "llm_call", "prompt": prompt, "prompt_hash": prompt_hash, "model": None,
                "temperature": 0.7, "max_tokens": 100, "result": "observation-2"}]
    thought = {key: {"prompt_hash": prompt_hash, "model": "mock",
                     "results": ["observation-1"], "positions": [0]}}
    merged = _merge_llm_cache(extract_llm_cache(history), thought)
    assert merged[key]["results"] == ["observation-1", "observation-2"]

    replay = CacheOnlyLLMBackend(merged)
    assert [replay.complete(prompt), replay.complete(prompt)] == ["observation-1", "observation-2"]


def test_llm_first_merge_orders_by_execution_position():
    """The mirrored order: llm ran first (its event sits at index 0), the
    thought step ran after (the history already held one event)."""
    import hashlib

    prompt = "analyze cluster"
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    key = f"{prompt_hash}:mock"
    history = [{"type": "llm_call", "prompt": prompt, "prompt_hash": prompt_hash, "model": None,
                "temperature": 0.7, "max_tokens": 100, "result": "observation-1"}]
    thought = {key: {"prompt_hash": prompt_hash, "model": "mock",
                     "results": ["observation-2"], "positions": [1]}}
    merged = _merge_llm_cache(extract_llm_cache(history), thought)
    assert merged[key]["results"] == ["observation-1", "observation-2"]


def test_positionless_sources_keep_the_previous_contract():
    """A backend never wired to an interpreter records no positions: llm
    occurrences keep preceding thought occurrences, each in source order."""
    import hashlib

    prompt = "analyze cluster"
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    key = f"{prompt_hash}:mock"
    history = [{"type": "llm_call", "prompt": prompt, "prompt_hash": prompt_hash, "model": None,
                "temperature": 0.7, "max_tokens": 100, "result": "observation-2"}]
    thought = {key: {"prompt_hash": prompt_hash, "model": "mock",
                     "results": ["observation-1"], "positions": [None]}}
    merged = _merge_llm_cache(extract_llm_cache(history), thought)
    assert merged[key]["results"] == ["observation-2", "observation-1"]


def test_mixed_thought_and_llm_program_roundtrips(tmp_path):
    """End-to-end record -> replay of a program whose thought step and llm
    call use different prompts (the DSL cannot spell the wrapped thought
    prompt in a string literal): the shared-order machinery must not disturb
    the ordinary roundtrip."""
    artifact = tmp_path / "mixed"
    record_source(THOUGHT_LLM_SOURCE, artifact, source_path="acceptance/thought_llm.syn",
                  llm_backend=ScriptedBackend(["observation-1", "observation-2"]))
    result = replay_mock_artifact(artifact)
    assert result.drift == 0


def test_spoiled_merged_order_must_drift(tmp_path):
    """Negative control: reversing a merged occurrence list must be visible."""
    artifact = tmp_path / "spoiled"
    record_source(LLM_REPEAT_SOURCE, artifact, source_path="acceptance/llm_repeat.syn",
                  llm_backend=ScriptedBackend(["observation-1", "observation-2"]))
    cache = json.loads((artifact / "llm_cache.mock.json").read_text())
    assert any(len(value["results"]) == 2 for value in cache.values())
    for value in cache.values():
        if len(value["results"]) == 2:
            value["results"] = list(reversed(value["results"]))
    (artifact / "llm_cache.mock.json").write_text(json.dumps(cache))
    with pytest.raises(Exception, match="drift"):
        replay_mock_artifact(artifact)


# --------------------------------------------------------------------------
# S3: published registry bytes are bounded
# --------------------------------------------------------------------------
def _heavy_judged(episodes=256, paths=32, active_episodes=0):
    patch = "a" * 64
    assertions = [{"assertion_id": "as1",
                   "claim": {"task_contract_ref": {"sha256": "tc", "media_type": "x"},
                             "repository_revision": "r1", "patch_sha256": patch},
                   "status": "REFUTED",
                   "origin": {"transaction_id": "p1", "result_ref": {"sha256": "pr"},
                              "verification_ref": {"sha256": "vr"}, "oracle_result_ref": {"sha256": "or"},
                              "report_ref": {"sha256": "rr"}},
                   "generalization": "NOT_ESTABLISHED"},
                  {"assertion_id": "as2",
                   "claim": {"task_contract_ref": {"sha256": "tc2", "media_type": "x"},
                             "repository_revision": "r1", "patch_sha256": "b" * 64},
                   "status": "REFUTED",
                   "origin": {"transaction_id": "p2", "result_ref": {"sha256": "pr2"},
                              "verification_ref": {"sha256": "vr2"}, "oracle_result_ref": {"sha256": "or2"},
                              "report_ref": {"sha256": "rr2"}},
                   "generalization": "NOT_ESTABLISHED"}]
    historic = [f"src/mod_{i}.py" for i in range(paths)]
    attempts = [{"attempt_id": 1, "result_sha256": "rs", "operation": "INDEPENDENT_ATTEMPT",
                 "status": "FAIL", "outcome": "NOT_FULFILLED", "reasons": ["NO_CANDIDATE"],
                 "oracle_resolved": True, "candidate": {"patch_sha256": patch, "revision": "r1"},
                 "evidence": [{"role": "VERIFICATION", "ref": {"sha256": "v", "media_type": "x"}}]}]
    judged = [episode(f"hist-{i}", "NOT_FULFILLED", "COUNTED", historic, attempts, assertions)
              for i in range(episodes)]
    judged += [episode(f"act-{i}", "NOT_FULFILLED", "COUNTED", [ELEMENT], attempts, assertions)
               for i in range(active_episodes)]
    return judged


def test_foreign_history_volume_never_breaks_the_frame_event_budget():
    """The review probe: 256 episodes over 32 paths (33 unique paths, far
    below the roster budget) produce a registry heavier than the whole memory
    event; the publication must degrade instead of failing the frame."""
    registry = build_owner_registry(project_identity="proj1", court=court_of(_heavy_judged()),
                                    task=_Task([ELEMENT]), active_paths=[ELEMENT])
    assert len(canonical(registry)) > MAX_FRAME_PUBLICATION_BYTES  # the derived registry is heavy
    published = bound_owner_registry(registry, active_paths=[ELEMENT])
    assert published["publication"]["mode"] == "COMPACT_HISTORY"
    assert len(canonical(published)) <= MAX_REGISTRY_PUBLICATION_BYTES
    # The active owner keeps its full profile; historic owners carry counts.
    owners = {owner["path"]: owner for owner in published["owners"]}
    assert owners[ELEMENT].get("history_publication") != "COUNTS_ONLY"
    historic = owners["src/mod_0.py"]
    assert historic["history_publication"] == "COUNTS_ONLY"
    assert historic["counts"]["declarations"] == 256
    assert historic["counts"]["defects"] == 256
    # Grounds stay available: every compacted owner names the decision ref
    # its full profile rebuilds from.
    assert historic["grounds"]["decision"] == registry["decision"]
    # The frame's share of the event budget is positive again.
    assert _frame_byte_budget({"profile": "v4", "owner_registry": published}) > 0


def test_small_registry_publishes_unchanged():
    judged = [episode("run-a", "FULFILLED", "COUNTED", [ELEMENT],
                      attempts=[{"attempt_id": 1, "result_sha256": "rs", "operation": "INDEPENDENT_ATTEMPT",
                                 "status": "FULL", "outcome": "FULFILLED", "reasons": ["INDEPENDENTLY_VERIFIED"],
                                 "oracle_resolved": True, "candidate": None, "evidence": []}])]
    registry = build_owner_registry(project_identity="proj1", court=court_of(judged),
                                    task=_Task([ELEMENT]), active_paths=[ELEMENT])
    published = bound_owner_registry(registry, active_paths=[ELEMENT])
    assert published == registry  # no publication section, byte-identical


def test_counts_only_fallback_when_the_active_scope_is_too_heavy():
    """Forcing a budget the active owner's full history cannot fit degrades
    every owner to counts and still keeps the grounds; a budget nothing can
    fit refuses explicitly."""
    # 60 heavy episodes on the active path: its full profile alone (~150 KB)
    # cannot fit a 64 KB budget, while 33 counts-only owners (~14 KB) can.
    registry = build_owner_registry(project_identity="proj1",
                                    court=court_of(_heavy_judged(episodes=2, active_episodes=60)),
                                    task=_Task([ELEMENT]), active_paths=[ELEMENT])
    published = bound_owner_registry(registry, max_bytes=64 * 1024, active_paths=[ELEMENT])
    assert published["publication"]["mode"] == "COUNTS_ONLY"
    assert all(owner["history_publication"] == "COUNTS_ONLY" for owner in published["owners"])
    assert all(owner["grounds"]["decision"] == registry["decision"] for owner in published["owners"])
    assert len(canonical(published)) <= 64 * 1024
    with pytest.raises(ValueError, match="byte budget"):
        bound_owner_registry(registry, max_bytes=64, active_paths=[ELEMENT])


def test_frame_enrichment_reads_counts_only_owners():
    """apply_owner_profiles enriches the frame identically from a compacted
    publication, so a reader of the event sees the same element states."""
    registry = build_owner_registry(project_identity="proj1",
                                    court=court_of(_heavy_judged(episodes=2, active_episodes=8)),
                                    task=_Task([ELEMENT]), active_paths=[ELEMENT])
    full_frame = {"schema_version": ACTIVE_MEMORY_FRAME_V4, "memory_kinds": ["Identity"], "elements": [
        {"element_id": "e1", "owner_id": "element-owner-e1", "path": ELEMENT,
         "target_state": {"task": 1}, "current_state": {"rev": "r1"},
         "development_delta": {"status": "X"}, "memory": {"Identity": {}}}]}
    enriched_full = apply_owner_profiles(json.loads(json.dumps(full_frame)), registry)
    published = bound_owner_registry(registry, max_bytes=64 * 1024, active_paths=[ELEMENT])
    enriched_compact = apply_owner_profiles(json.loads(json.dumps(full_frame)), published)
    element_full = enriched_full["elements"][0]
    element_compact = enriched_compact["elements"][0]
    assert element_full["target_state"]["owner_declarations"] == \
        element_compact["target_state"]["owner_declarations"]
    assert element_full["current_state"]["owner_counts"] == element_compact["current_state"]["owner_counts"]
    assert element_full["development_delta"]["owner_summary"] == \
        element_compact["development_delta"]["owner_summary"]


# --------------------------------------------------------------------------
# S4: an unjudged candidate is not an established defect
# --------------------------------------------------------------------------
def test_proposed_but_unjudged_candidate_is_not_a_defect():
    """The review case: the first attempt proposed a candidate and ended
    INFRA_ERROR/UNCERTAIN, the follow-up refused without a candidate; no
    assertion was ever judged."""
    attempts = [
        {"attempt_id": 1, "result_sha256": "rs", "operation": "INDEPENDENT_ATTEMPT",
         "status": "INFRA_ERROR", "outcome": "UNCERTAIN", "reasons": ["ORACLE_UNAVAILABLE"],
         "oracle_resolved": False,
         "candidate": {"patch_sha256": "c" * 64, "revision": "r1"},
         "evidence": [{"role": "VERIFICATION", "ref": {"sha256": "v", "media_type": "x"}}]},
        {"attempt_id": 2, "result_sha256": "rs2", "operation": "INDEPENDENT_ATTEMPT",
         "status": "NO_CANDIDATE", "outcome": "NOT_FULFILLED", "reasons": ["NO_CANDIDATE"],
         "oracle_resolved": True, "candidate": None, "evidence": []},
    ]
    item = episode("run-cand", "NOT_FULFILLED", "COUNTED", [ELEMENT], attempts)
    assert _category(item) == "TASK_FAILED_NO_EVIDENCE"

    registry = build_owner_registry(project_identity="proj1", court=court_of([item]),
                                    task=_Task([ELEMENT]), active_paths=[ELEMENT])
    owner = registry["owners"][0]
    assert owner["development_delta"]["summary"]["defects"] == 0
    assert owner["development_delta"]["summary"]["task_failures"] == 1
    # The unjudged candidate stays visible as a recorded proposal.
    assert owner["current_state"]["task_failures"][0]["candidate_patches"] == ["c" * 64]


def test_judged_assertion_still_establishes_the_defect():
    """Negative control: a REFUTED assertion keeps establishing a defect."""
    patch = "a" * 64
    assertions = [{"assertion_id": "as1",
                   "claim": {"task_contract_ref": {"sha256": "tc", "media_type": "x"},
                             "repository_revision": "r1", "patch_sha256": patch},
                   "status": "REFUTED",
                   "origin": {"transaction_id": "p1", "result_ref": {"sha256": "pr"},
                              "verification_ref": {"sha256": "vr"}, "oracle_result_ref": {"sha256": "or"},
                              "report_ref": {"sha256": "rr"}},
                   "generalization": "NOT_ESTABLISHED"}]
    attempts = [{"attempt_id": 1, "result_sha256": "rs", "operation": "INDEPENDENT_ATTEMPT",
                 "status": "FAIL", "outcome": "NOT_FULFILLED", "reasons": ["C1_FAILED"],
                 "oracle_resolved": True, "candidate": {"patch_sha256": patch, "revision": "r1"},
                 "evidence": []}]
    item = episode("run-judged", "NOT_FULFILLED", "COUNTED", [ELEMENT], attempts, assertions)
    assert _category(item) == "DEFECT_ESTABLISHED"


# --------------------------------------------------------------------------
# S5: policy references agree with soft keyword declarations
# --------------------------------------------------------------------------
@pytest.mark.parametrize("source", [
    # The exact review case: policy named `target`, referenced by `under`.
    'policy target {}\ncollective dream with [] under target {}',
    'policy budget {}\ncollective dream with [] under budget {}',
    'policy alarm {}\ncollective dream with [] under alarm {}',
])
def test_policy_soft_keyword_references_parse(source):
    Parser(Lexer(source).scan_tokens()).parse()


def test_hard_keywords_still_rejected_as_policy_names():
    with pytest.raises(Exception):
        Parser(Lexer('policy target {}\ncollective dream with [] under true {}').scan_tokens()).parse()


# --------------------------------------------------------------------------
# Determinism of the bounded publication
# --------------------------------------------------------------------------
def test_bounded_publication_is_deterministic():
    registry = build_owner_registry(project_identity="proj1", court=court_of(_heavy_judged(episodes=8)),
                                    task=_Task([ELEMENT]), active_paths=[ELEMENT])
    first = bound_owner_registry(registry, active_paths=[ELEMENT])
    second = bound_owner_registry(registry, active_paths=[ELEMENT])
    assert canonical(first) == canonical(second)
    assert first["schema_version"] == OWNER_REGISTRY_V1
