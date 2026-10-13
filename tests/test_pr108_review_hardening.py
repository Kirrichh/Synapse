"""Acceptance coverage for the external review of the PR108 physical-test fixes.

Each test pins one finding of the 2026-10-13 archive review:

* R1 - the owner registry reads the complete court history, not the frame's
  bounded working cut, so an element keeps its declarations after hundreds
  of unrelated episodes.
* R2 - a path budget overrun degrades the roster to the active owners plus
  subsystem rollups instead of refusing the new task.
* R3 - a counted NOT_FULFILLED episode without a candidate or a judged
  assertion is TASK_FAILED_NO_EVIDENCE, not an established defect.
* R4 - the frame is enriched with counts only and the registry bytes are
  subtracted from the frame's share of the memory event budget.
* R5 - soft keywords accepted in declaration positions are accepted in the
  matching reference positions (threshold names, metric lists, backend and
  role names).
* R6 - the thought-chain cache retains every occurrence of a repeated
  prompt in order; replay serves them one by one and fails on exhaustion.
* R7 - end-to-end record -> replay --mock of repeated identical thought
  prompts, with a spoiled-cache negative control that must drift.

The negative controls are part of the acceptance: a check that cannot fail
is not evidence.
"""
import json

import pytest

from synapse.builtins import LLMBackend
from synapse.experiments.gold.owner_profiles import (
    ACTIVE_MEMORY_FRAME_V4,
    MAX_REGISTRY_OWNERS,
    OWNER_REGISTRY_V1,
    _category,
    apply_owner_profiles,
    build_owner_registry,
)
from synapse.experiments.gold.project_agents import MAX_PRIOR_EPISODES, _frame_byte_budget
from synapse.experiments.gold.project_memory_store import MAX_MEMORY_EVENT_BYTES
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
ANCHOR = "examples/production_hardening.py"


# --------------------------------------------------------------------------
# Synthetic court machinery (same shapes the consolidation court emits)
# --------------------------------------------------------------------------
def episode(run_id, outcome, verdict, paths, patch="a" * 64, confirmed=True,
            with_candidate=True, with_assertions=True):
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
        "attempts": ([{"attempt_id": 1, "result_sha256": "rs", "operation": "INDEPENDENT_ATTEMPT",
                       "status": "FULL", "outcome": "FULFILLED", "reasons": ["INDEPENDENTLY_VERIFIED"],
                       "oracle_resolved": True,
                       "candidate": ({"patch_sha256": patch, "revision": "r1"} if with_candidate else None),
                       "evidence": [{"role": "VERIFICATION", "ref": {"sha256": "v", "media_type": "x"}}]}]
                     if with_candidate else
                     [{"attempt_id": 1, "result_sha256": "rs", "operation": "INDEPENDENT_ATTEMPT",
                       "status": "FAIL", "outcome": "NOT_FULFILLED", "reasons": ["NO_CANDIDATE"],
                       "oracle_resolved": True, "candidate": None, "evidence": []}]),
        "recovery": None, "uncertainties": []}
    learning = {
        "schema_version": "synapse.stage4.gold.episode-learning/v1",
        "outcome_ref": observation["outcome_ref"],
        "assertions": ([{"assertion_id": "as1",
                         "claim": {"task_contract_ref": observation["requirement"]["task_contract_ref"],
                                   "repository_revision": "r1", "patch_sha256": patch},
                         "status": "CONFIRMED" if confirmed else "REFUTED",
                         "origin": {"transaction_id": "p1", "result_ref": {"sha256": "pr"},
                                    "verification_ref": {"sha256": "vr"}, "oracle_result_ref": {"sha256": "or"},
                                    "report_ref": {"sha256": "rr"}},
                         "generalization": "NOT_ESTABLISHED"}] if with_assertions else []),
        "unresolved": []}
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


THOUGHT_REPEAT_SOURCE = '''
fn main() {
    let first = thought { "inspect the element" }
    print(first)
    let second = thought { "inspect the element" }
    print(second)
}
'''


# --------------------------------------------------------------------------
# R1: full court history survives the frame's working cut
# --------------------------------------------------------------------------
def test_registry_reads_full_court_history_beyond_the_frame_cut():
    judged = [episode("run-a", "FULFILLED", "COUNTED", [ELEMENT])]
    judged += [episode(f"fill-{i}", "FULFILLED", "COUNTED", [f"src/other_{i}.py"])
               for i in range(MAX_PRIOR_EPISODES * 2)]
    registry = build_owner_registry(project_identity="proj1", court=court_of(judged),
                                    task=_Task([ELEMENT]), active_paths=[ELEMENT])
    owner = [item for item in registry["owners"] if item["path"] == ELEMENT][0]
    assert registry["judged_episodes"] == MAX_PRIOR_EPISODES * 2 + 1
    assert len(owner["target_state"]["declarations"]) == 1
    assert owner["current_state"]["episodes_seen"] == 1


# --------------------------------------------------------------------------
# R2: path budget degrades instead of blocking the task
# --------------------------------------------------------------------------
def test_path_budget_overrun_degrades_to_rollup_instead_of_refusing():
    judged = [episode(f"hist-{i}", "FULFILLED", "COUNTED",
                      [f"src/mod_{i}_file_{j}.py" for j in range(9)])
              for i in range(MAX_REGISTRY_OWNERS // 9 + 1)]
    registry = build_owner_registry(project_identity="proj1", court=court_of(judged),
                                    task=_Task([ELEMENT]), active_paths=[ELEMENT])
    assert registry["roster_mode"] == "ACTIVE_OWNERS_WITH_SUBSYSTEM_ROLLUP"
    assert {item["path"] for item in registry["owners"]} == {ELEMENT}
    assert registry["degraded_paths"] > 0
    # The rollup keeps complete subsystem totals for the dropped paths.
    assert sum(section["declarations"] for section in registry["subsystems"]) == len(judged) * 9


def test_task_scoped_active_budget_still_refuses_explicitly():
    judged = []
    paths = [f"src/x_{i}.py" for i in range(MAX_REGISTRY_OWNERS + 1)]
    with pytest.raises(ValueError, match="active path budget"):
        build_owner_registry(project_identity="proj1", court=court_of(judged),
                             task=_Task(paths), active_paths=paths)


# --------------------------------------------------------------------------
# R3: a task failure without evidence is not an element defect
# --------------------------------------------------------------------------
def test_refused_task_without_evidence_is_not_a_defect():
    refused = episode("run-x", "NOT_FULFILLED", "COUNTED", [ELEMENT],
                      with_candidate=False, with_assertions=False)
    with_evidence = episode("run-y", "NOT_FULFILLED", "COUNTED", [ELEMENT], confirmed=False)
    assert _category(refused) == "TASK_FAILED_NO_EVIDENCE"
    assert _category(with_evidence) == "DEFECT_ESTABLISHED"

    judged = [refused]
    registry = build_owner_registry(project_identity="proj1", court=court_of(judged),
                                    task=_Task([ELEMENT]), active_paths=[ELEMENT])
    owner = registry["owners"][0]
    assert owner["development_delta"]["summary"]["task_failures"] == 1
    assert owner["development_delta"]["summary"]["defects"] == 0


# --------------------------------------------------------------------------
# R4: counts-only frame enrichment and event-budget accounting
# --------------------------------------------------------------------------
def test_frame_enrichment_is_counts_only():
    judged = [episode("run-a", "FULFILLED", "COUNTED", [ANCHOR, ELEMENT])]
    registry = build_owner_registry(project_identity="proj1", court=court_of(judged),
                                    task=_Task([ELEMENT]), active_paths=[ELEMENT])
    frame = {"schema_version": ACTIVE_MEMORY_FRAME_V4, "memory_kinds": ["Identity"], "elements": [
        {"element_id": "e1", "owner_id": "element-owner-e1", "path": ELEMENT,
         "target_state": {"task": 1}, "current_state": {"rev": "r1"},
         "development_delta": {"status": "X"}, "memory": {"Identity": {}}}]}
    applied = apply_owner_profiles(frame, registry)[
        "elements"][0]
    assert applied["target_state"]["owner_declarations"] == 1
    assert applied["current_state"]["owner_counts"]["verified"] == 1
    for absent in ("verified_history", "defect_history", "deferred_history",
                   "partial_history", "declared_history"):
        assert absent not in applied["current_state"]
        assert absent not in applied["target_state"]


def test_registry_bytes_are_subtracted_from_the_frame_event_budget():
    judged = [episode("run-a", "FULFILLED", "COUNTED", [ELEMENT])]
    registry = build_owner_registry(project_identity="proj1", court=court_of(judged),
                                    task=_Task([ELEMENT]), active_paths=[ELEMENT])
    plain = _frame_byte_budget({"profile": "v4"})
    loaded = _frame_byte_budget({"profile": "v4", "owner_registry": registry})
    assert loaded < plain
    assert plain - loaded >= len(canonical(registry))
    assert loaded < MAX_MEMORY_EVENT_BYTES


# --------------------------------------------------------------------------
# R5: declaration and reference positions agree on soft keywords
# --------------------------------------------------------------------------
@pytest.mark.parametrize("source", [
    # The exact review case: threshold named `target`, referenced by a habit.
    '''
affective threshold target { when arousal > 0.5 }
habit "demo" from pattern { activate when target }
''',
    '''
affective threshold budget { when arousal > 0.5 }
habit "demo" from pattern { suppress when budget }
''',
    'measure identity_coherence { window 5 metrics [target, state, quorum] bind m }',
    'memory palace demo { backend target }',
    'swarm fracture with [alpha, beta] { roles { target -> planner } }',
])
def test_soft_keyword_references_parse(source):
    Parser(Lexer(source).scan_tokens()).parse()


def test_hard_keywords_still_rejected_as_names():
    with pytest.raises(Exception):
        Parser(Lexer("fn main() { let true = 1 }").scan_tokens()).parse()


# --------------------------------------------------------------------------
# R6: occurrence-ordered thought cache
# --------------------------------------------------------------------------
def test_thought_history_keeps_occurrence_order():
    backend = ScriptedBackend(["observation-1", "observation-2"])
    assert backend.thought_chain(["inspect the element"]) == "observation-1"
    assert backend.thought_chain(["inspect the element"]) == "observation-2"
    stored = next(iter(backend.thought_history.values()))["results"]
    assert stored == ["observation-1", "observation-2"]


def test_mock_replay_serves_occurrences_in_order_and_fails_on_exhaustion():
    backend = ScriptedBackend(["observation-1", "observation-2"])
    backend.thought_chain(["inspect the element"])
    backend.thought_chain(["inspect the element"])
    cache = _merge_llm_cache(extract_llm_cache([]), backend.thought_history)
    replay = CacheOnlyLLMBackend(cache)
    prompt = "Step 1: inspect the element\nContext: "
    assert replay.complete(prompt) == "observation-1"
    assert replay.complete(prompt) == "observation-2"
    with pytest.raises(Exception, match="more often than recorded"):
        replay.complete(prompt)


def test_legacy_single_result_cache_still_replays():
    legacy = CacheOnlyLLMBackend({"k": {"prompt_hash": "k", "model": "mock", "result": "old-style"}})
    assert legacy.cache == {"k": ["old-style"]}


# --------------------------------------------------------------------------
# R7: end-to-end record -> replay with repeated identical prompts
# --------------------------------------------------------------------------
def test_repeated_thought_prompts_roundtrip_through_mock_replay(tmp_path):
    artifact = tmp_path / "artifact"
    record_source(THOUGHT_REPEAT_SOURCE, artifact, source_path="acceptance/thought_repeat.syn",
                  llm_backend=ScriptedBackend(["observation-1", "observation-2"]))
    result = replay_mock_artifact(artifact)
    assert result.drift == 0


def test_spoiled_occurrence_order_must_drift(tmp_path):
    """Negative control: the roundtrip test above must be able to fail."""
    artifact = tmp_path / "artifact"
    record_source(THOUGHT_REPEAT_SOURCE, artifact, source_path="acceptance/thought_repeat.syn",
                  llm_backend=ScriptedBackend(["observation-1", "observation-2"]))
    cache = json.loads((artifact / "llm_cache.mock.json").read_text())
    assert all(len(value["results"]) == 2 for value in cache.values())
    for value in cache.values():
        value["results"] = list(reversed(value["results"]))
    (artifact / "llm_cache.mock.json").write_text(json.dumps(cache))
    with pytest.raises(Exception, match="drift"):
        replay_mock_artifact(artifact)


# --------------------------------------------------------------------------
# Determinism of the derived registry
# --------------------------------------------------------------------------
def test_registry_rebuild_is_byte_identical():
    judged = [episode("run-a", "FULFILLED", "COUNTED", [ELEMENT]),
              episode("run-b", "NOT_FULFILLED", "COUNTED", [ELEMENT], confirmed=False),
              episode("run-c", "NOT_FULFILLED", "COUNTED", [ELEMENT],
                      with_candidate=False, with_assertions=False)]
    first = build_owner_registry(project_identity="proj1", court=court_of(judged),
                                 task=_Task([ELEMENT]), active_paths=[ELEMENT])
    second = build_owner_registry(project_identity="proj1", court=court_of(judged),
                                  task=_Task([ELEMENT]), active_paths=[ELEMENT])
    assert canonical(first) == canonical(second)
    assert first["schema_version"] == OWNER_REGISTRY_V1
