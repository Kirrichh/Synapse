"""A forget removes what the forgotten case recorded, and nothing a retained case or a later observation rests on
(findings R3 and R4 on 556624d).

Every session counts items of the stock through the gateway; store D addresses a recorded result by its content,
so the same answer to the same request is one body, whichever case recorded it.

* R4 — A counts X; B counts X and Y; C counts Y and Z. The operator forgets A: B carries A's result and is
  forgotten with it, while Y, which C still carries, stays — C remains retained with every recorded result present
  and its session reproduces exactly; with C counting Q instead of Y (control) nothing of C is touched either.
* R3 — the operator forgets a case that counted X, and a later session counts X again: the same content, observed
  anew after the forget. The retention passes after it complete the forget's pass again and keep the new
  observation's result; the new case reproduces exactly and the forgotten one stays forgotten. Counting another
  item after the forget (control) keeps its result too.
"""
from __future__ import annotations

import pytest

from acceptance.memory import _stock as stock
from synapse.memory_consolidation.configuration import read_memory_configuration
from synapse.memory_consolidation.court.custody import reproduce_session, reproduces
from synapse.memory_consolidation.factory import MemoryFactory

CHAIN = stock.PROGRAM.replace('let counted = tool("inventory", {"item": item})',
                              'let first = tool("inventory", {"item": item})\n'
                              '  let counted = tool("inventory", {"item": second})')
MAINTENANCE = '''memory palace "clerk" { rooms { episodic procedural } consolidate during dream }
consolidate palace
print("maintained")'''


def _world(root):
    return stock.world(root, parameters={"n_medium_windows": 100, "n_low_windows": 100, "k_rollup": 100})


def _forget(world, qid):
    code, payload, stderr = world.memory("forget", "--quantum", qid, "--reason", "data subject request",
                                         "--operator", "acceptance.operator")
    assert code == 0 and payload["status"] == "RECORDED", (payload, stderr)
    return payload["acts"]


def _exact(world, case):
    """Whether the case's session, re-executed from store D alone, reproduces its exact recorded events."""
    ports = MemoryFactory(world.state, read_memory_configuration(world.configuration_path)).ports()
    result = reproduce_session(ports.reproduce, world.evidence(), case["replay_ref"]["data_ref"])
    return result["status"] == "reproduced" and reproduces(result["history"], case["replay_ref"]["positions"],
                                                           case["canonical"]["trace_ref"])


@pytest.mark.parametrize("shared", [True, False], ids=["c-shares-y-with-b", "c-shares-nothing"])
def test_a_forget_never_removes_what_a_retained_case_rests_on(tmp_path, shared):
    world = _world(tmp_path)
    cases = {}
    for run_id, first, second in (("A", "X", "X"), ("B", "X", "Y"), ("C", "Y" if shared else "Q", "Z")):
        world.run(CHAIN, run_id, {"task_name": run_id, "item": first, "second": second})
        cases[run_id] = stock.cases(world, run_id)["medium"]
    a, b, c = (set(cases[run_id]["evidence_refs"]) for run_id in "ABC")
    assert a & b and not a & c and bool(b & c) is shared and _exact(world, cases["C"])
    acts = _forget(world, cases["A"]["qid"])
    assert sorted(act["qid"] for act in acts) == sorted([cases["A"]["qid"], cases["B"]["qid"]])
    world.run(MAINTENANCE, "maintenance", {})
    state, evidence = world.owner().state(), world.evidence()
    assert [state["quanta"][cases[run_id]["qid"]]["retention_state"] for run_id in "ABC"] == [
        "forgotten", "forgotten", "full"]
    assert all(evidence.get(ref) is None for ref in a)  # What A recorded is gone, with B's raw trace.
    assert evidence.get(cases["B"]["raw_ref"]) is None
    assert all(evidence.get(ref) is not None for ref in c) and _exact(world, cases["C"])


@pytest.mark.parametrize("item", ["X", "Z"], ids=["same-answer-again", "another-item"])
def test_an_answer_observed_after_a_forget_keeps_its_result(tmp_path, item):
    world = _world(tmp_path)
    stock.count(world, "old")
    old = stock.cases(world, "old")["medium"]
    _forget(world, old["qid"])
    assert all(world.evidence().get(ref) is None for ref in old["evidence_refs"])
    world.run(stock.PROGRAM, "fresh", {"task_name": "fresh", "item": "old" if item == "X" else item})
    world.run(MAINTENANCE, "maintenance", {})  # Another retention pass: the forget's pass is completed again.
    fresh = stock.cases(world, "fresh")["medium"]
    assert bool(set(old["evidence_refs"]) & set(fresh["evidence_refs"])) is (item == "X")
    state = world.owner().state()
    assert state["quanta"][old["qid"]]["retention_state"] == "forgotten"
    assert all(world.evidence().get(ref) is not None for ref in fresh["evidence_refs"]) and _exact(world, fresh)
