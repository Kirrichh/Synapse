"""Court contract: one credit per witness, no authority from uncertainty, one chain.

Pure judgements use declared data. Journal cases use the real owner store with
outcomes whose physical runs do not exist, so their basis stays pending.
"""
from copy import deepcopy
import itertools
import multiprocessing
from pathlib import Path

import pytest

from synapse.experiments.gold.project_court import (
    COURT_POLICY_V1, EMPTY_COURT_STATE, consolidate_court, judge, read_court)
from synapse.experiments.gold.project_episode_outcome import EPISODE_OUTCOME_V1
from synapse.experiments.gold.project_learning import EPISODE_LEARNING_V1
from synapse.experiments.gold.project_memory_store import ProjectMemoryStore

TASK = {"schema_id": "task", "sha256": "1" * 64}
REVISION = "2" * 40
PATCH, OTHER = "b" * 64, "c" * 64


def entry(name, requirement="FULFILLED", assertions=(), attempts=()):
    outcome = {"outcome": name}
    return {"input": {"outcome": outcome, "learning": {"learning": name}, "observation": {"observation": name},
                      "pending": None},
            "learning": {"schema_version": EPISODE_LEARNING_V1, "outcome_ref": outcome,
                         "assertions": list(assertions), "unresolved": []},
            "observation": {"schema_version": EPISODE_OUTCOME_V1, "outcome_ref": outcome, "run_id": name,
                            "requirement": {"task_contract_ref": TASK, "repository_revision": REVISION,
                                            "paths": ["src/calc.py"], "outcome": requirement, "run_status": "X",
                                            "controller_status": "X", "basis": []},
                            "attempts": list(attempts), "recovery": None, "uncertainties": []}}


def pending(name, reason="BASIS_UNAVAILABLE"):
    return {"input": {"outcome": {"outcome": name}, "learning": None, "observation": None, "pending": reason}}


def assertion(status="CONFIRMED", origin="one", patch=PATCH):
    return {"assertion_id": "a" * 64, "claim": {"task_contract_ref": TASK, "repository_revision": REVISION,
            "patch_sha256": patch}, "status": status, "origin": {"transaction_id": origin},
            "generalization": "NOT_ESTABLISHED"}


def states(state):
    return {value["subject"]["patch_sha256"]: value["state"] for value in state["subjects"].values()}


def test_repeated_inputs_and_copied_witnesses_are_credited_once():
    entries = [entry("first", assertions=[assertion()])] + [
        entry("copy-%d" % index, assertions=[assertion()]) for index in range(100)]
    body, state = judge(EMPTY_COURT_STATE, entries)
    assert judge(EMPTY_COURT_STATE, deepcopy(entries)) == (body, state)
    assert len(body["credits"]) == 1 and len(body["excluded"]) == 100
    assert {item["reason"] for item in body["excluded"]} == {"REPEATED_ORIGIN"}
    subject, = state["subjects"].values()
    assert len(subject["support"]) == 1 and subject["state"] == "ADMITTED"
    with pytest.raises(ValueError, match="judged exactly once"):
        judge(state, [entry("first", assertions=[assertion(origin="new")])])


@pytest.mark.parametrize("requirement", ["UNCERTAIN", "UNVERIFIABLE"])
def test_an_unknown_effect_or_invalid_proof_credits_neither_side(requirement):
    attempt = {"attempt_id": "a1", "outcome": requirement, "reasons": ["VERIFICATION_INFRASTRUCTURE"]}
    body, state = judge(EMPTY_COURT_STATE, [entry("run", requirement, [assertion(), assertion("REFUTED", "two", OTHER)],
                                                  attempts=[attempt])])
    verdict, = body["verdicts"]
    assert verdict["verdict"] == "DEFERRED" and verdict["reasons"] == ["VERIFICATION_INFRASTRUCTURE"]
    assert body["credits"] == [] and state["subjects"] == {} and state["judged"] == [{"outcome": "run"}]


def test_admission_threshold_refutation_and_sticky_conflict():
    assert COURT_POLICY_V1["admission_minimum_support"] == 1
    _, below = judge(EMPTY_COURT_STATE, [entry("refuted", "NOT_FULFILLED", [assertion("REFUTED", "r1")])])
    assert states(below) == {PATCH: "REFUTED"}  # Zero support is below the threshold.
    _, equal = judge(EMPTY_COURT_STATE, [entry("one", assertions=[assertion(origin="s1")])])
    assert states(equal) == {PATCH: "ADMITTED"}  # One support equals the threshold.
    body, above = judge(equal, [entry("two", assertions=[assertion(origin="s2")])])
    assert states(above) == {PATCH: "ADMITTED"} and body["transitions"] == []
    body, conflict = judge(above, [entry("refuting", "NOT_FULFILLED", [assertion("REFUTED", "r1")])])
    assert states(conflict) == {PATCH: "SLOW_ONLY"}
    assert [(item["from"], item["to"]) for item in body["transitions"]] == [("ADMITTED", "SLOW_ONLY")]
    _, later = judge(conflict, [entry("more", assertions=[assertion(origin="s%d" % index) for index in range(3, 20)])])
    assert states(later) == {PATCH: "SLOW_ONLY"}  # More support never lifts a contradiction.
    with pytest.raises(ValueError, match="fulfilled requirement"):
        judge(EMPTY_COURT_STATE, [entry("partial", "PARTIAL", [assertion()])])


def test_pending_outcome_withholds_new_authority_but_keeps_restrictions():
    _, admitted = judge(EMPTY_COURT_STATE, [entry("old", assertions=[assertion(origin="s1", patch=OTHER)])])
    body, emergency = judge(admitted, [pending("lost"), entry("new", assertions=[assertion(origin="s2")]),
        entry("against", "NOT_FULFILLED", [assertion("REFUTED", "r1", OTHER)])])
    assert body["mode"] == "EMERGENCY"
    assert [item["verdict"] for item in body["verdicts"]] == ["COUNTED", "PENDING", "COUNTED"]
    assert states(emergency) == {PATCH: "OBSERVED", OTHER: "SLOW_ONLY"}
    withheld, = [item for item in body["transitions"] if item["patch_sha256"] == PATCH]
    assert withheld["withheld"] == "PENDING_OUTCOMES" and withheld["support"] == 1
    assert {"outcome": "lost"} not in emergency["judged"]  # Pending is not consumed.
    body, full = judge(emergency, [entry("lost", assertions=[assertion(origin="s3")])])
    assert body["mode"] == "FULL" and states(full) == {PATCH: "ADMITTED", OTHER: "SLOW_ONLY"}
    assert [(item["from"], item["to"], item["support"]) for item in body["transitions"]] == [("OBSERVED", "ADMITTED", 2)]


def test_permuted_inputs_do_not_choose_a_different_decision():
    entries = [entry("a", assertions=[assertion(origin="s1")]), entry("b", "NOT_FULFILLED", [assertion("REFUTED", "r1")]),
               entry("c", assertions=[assertion(origin="s2", patch=OTHER)]), pending("d")]
    results = {repr(judge(EMPTY_COURT_STATE, list(order))) for order in itertools.permutations(entries)}
    assert len(results) == 1


def _journal_outcome(root, name, identity):
    store = ProjectMemoryStore(root)
    job = (name.encode().hex() * 64)[:64]
    with store.session() as guard:
        store.put(kind="REQUESTED", job_key=job, payload={"project_identity": identity}, guard=guard)
        receipt = store.put(kind="OUTCOME_RECORDED", job_key=job, guard=guard, payload={
            "run_root": str(root / "missing" / name), "frozen_ref": {"missing": name}, "result_ref": {"missing": name}})
        return receipt, consolidate_court(store, guard, project_identity=identity)


def test_unestablished_basis_stays_pending_without_repeating_a_decision(tmp_path):
    receipt, court = _journal_outcome(tmp_path, "lost", "p" * 64)
    assert court["mode"] == "EMERGENCY" and court["pending"] == 1
    _journal_outcome(tmp_path, "foreign", "q" * 64)  # Another identity has its own court.
    store = ProjectMemoryStore(tmp_path)
    before = store.inventory()
    with store.session() as guard:
        assert consolidate_court(store, guard, project_identity="p" * 64) == court
    assert store.inventory() == before
    view = read_court(store, project_identity="p" * 64, decision=court["decision"])
    assert view["judged"] == [] and view["pending"] == [{"outcome": receipt, "reasons": ["BASIS_UNAVAILABLE"]}]


def _stream(root, name, barrier):
    barrier.wait()
    _journal_outcome(Path(root), name, "p" * 64)


def test_concurrent_task_streams_append_to_one_court_chain(tmp_path):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(4)
    processes = [context.Process(target=_stream, args=(str(tmp_path), "stream-%d" % index, barrier))
                 for index in range(4)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(300)
        assert process.exitcode == 0
    store = ProjectMemoryStore(tmp_path)
    with store.session() as guard:
        court = consolidate_court(store, guard, project_identity="p" * 64)
    decisions = [event for event, _ in store.inventory() if event["kind"] == "JUDGED"]
    view = read_court(store, project_identity="p" * 64, decision=court["decision"])
    assert court["pending"] == 4 and len(view["pending"]) == 4
    # Every decision extends its predecessor; none is a second version of the state.
    assert len({str(item["payload"]["predecessor"]) for item in decisions}) == len(decisions) <= 4
