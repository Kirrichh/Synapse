"""Step 2 of the conflict ladder: only a verified comparison resolves a conflict (review R5).

The competitors are born at equal trust: the shared trigger is slow-only from
their birth. The slow path keeps recording what each recovery achieves, and the
court keeps those outcomes across windows. A configured advisor is asked —
twice, with different phrasing and order — only once the outcomes form enough
comparable pairs, and its answer is recorded as a proposal:

* On ordinary routes both recoveries succeed. The advisor agrees that the
  other would have been better; the comparison finds no difference, so nothing
  is lifted and the next session stays on the slow path.
* On full routes the environment answers the repeated search only once a slot
  is held: the capacity recovery succeeds and the quota recovery fails in the
  same recorded situation. The advisor proposes the opposite; the comparison
  names the capacity recovery, the court lifts the ban, and the quota habit
  yields to it. At run time both apply, the loser gives way before any effect
  and the winner recovers the next full route.

The advisor is a recorded ``reason`` tool: asking it produces no effect in the
environment.
"""
from __future__ import annotations

import json

from acceptance.memory import _competitors as competitors
from acceptance.memory._world import answer, tool

QUESTION = "would_B_outcome_be_better"
POLICY = {"counterfactual_min_pairs": 1}


def _judge(verdict):
    return tool("judge", "judge:ops", [answer({"ok": True, "answer": verdict, "reason": "as the judge sees it"},
                                              when={"question": QUESTION})], server="judge", role="reason")


def _court_records(world):
    journal = world.owner().gateway_root / "journal.jsonl"
    return [record for record in map(json.loads, journal.read_text().splitlines())
            if str(record["body"].get("run_id", "")).startswith("court:")]


def _learn(world):
    first, second = competitors.learn_competitors(world)
    conflict, = world.reports()[-1]["conflicts"]
    assert conflict["step"] == 3 and world.reports()[-1]["slow_only_triggers"] == [conflict["trigger"]]
    return {competitors.recovery_of(world, item["habit_id"]): item["habit_id"] for item in (first, second)}, conflict


def _slow(world, run_id, route, recovery, *, reactions=1):
    world.run(competitors.PROGRAM, run_id, competitors.inputs(run_id, route, recovery))
    assert world.opening(run_id)["learned"] == [] and len(world.events(run_id, "slow_path_used")) == reactions
    return world.reports()[-1]


def test_an_agreeing_advisor_never_lifts_a_ban_the_outcomes_do_not_support(tmp_path):
    world = competitors.world(tmp_path, _judge("yes"), parameters=POLICY, advisor="judge")
    habits, trigger = _learn(world)

    _slow(world, "capacity-ordinary", "VNO", "capacity")
    report = _slow(world, "quota-ordinary", "TLL", "quota")
    conflict, = report["conflicts"]
    advice = conflict["advice"]
    assert advice["basis"] == "proposal" and advice["agreed"] is True and advice["calls"] == ["yes", "yes"]
    assert advice["comparison"]["winner"] is None
    assert advice["comparison"]["reason"] == "no_difference_established" and advice["comparison"]["pairs"] == 1
    assert conflict["step"] == 3 and report["slow_only_triggers"] == [trigger["trigger"]]
    _slow(world, "still", "HEL", "quota")


def test_a_verified_comparison_resolves_the_conflict_against_the_advisor(tmp_path):
    world = competitors.world(tmp_path, _judge("no"), parameters=POLICY, advisor="judge")
    habits, trigger = _learn(world)
    capacity, quota = habits["capacity"], habits["quota"]

    # The capacity recovery holds a slot and succeeds on a full route: one side of a pair only.
    report = _slow(world, "capacity-full", "OSL", "capacity")
    conflict, = report["conflicts"]
    assert conflict["step"] == 3 and conflict["advice"]["basis"] == "insufficient_comparable_outcomes"
    assert world.calls("judge") == []

    # The quota recovery fails on a full route in the same recorded situation: the pair is complete.
    effects = world.world()["effects"]
    # (The repeat is refused again: the slow path reacts to that refusal too.)
    report = _slow(world, "quota-full", "ARN", "quota", reactions=2)
    conflict, = report["conflicts"]
    comparison = conflict["advice"]["comparison"]
    assert (comparison["winner"], comparison["pairs"]) == (capacity, 1)
    assert comparison["better"] == {capacity: 1, quota: 0}
    assert conflict["step"] == 2 and conflict["slow_only_lifted"] is True
    assert conflict["resolution"] == f"{capacity}_stays_{quota}_probation"
    assert conflict["advice"]["basis"] == "proposal" and conflict["advice"]["calls"] == ["no", "no"]
    assert report["slow_only_triggers"] == []
    assert report["apply"]["habits"][quota]["yields_to"] == [capacity]

    # The advisor was asked twice, in two phrasings, and changed nothing in the environment.
    asked = world.calls("judge")
    assert [item["question"] for item in asked] == [QUESTION, QUESTION]
    assert sorted(item["variant"] for item in asked) == [0, 1]
    assert world.world()["effects"] == effects  # Neither the failed recovery nor the advice did anything.
    court = _court_records(world)
    assert court and {record["body"]["tool"] for record in court if record["kind"] == "STARTED"} == {"judge"}

    # Both apply to the next full route: the quota habit gives way before any effect, the winner recovers.
    quota_calls = len(world.calls("quota_status"))
    world.run(competitors.PROGRAM, "resolved", competitors.inputs("resolved", "CPH", "quota"))
    assert sorted(item["habit_id"] for item in world.opening("resolved")["learned"]) == sorted([capacity, quota])
    held, = world.events("resolved", "habit_suppressed")
    assert (held["habit_id"], held["reason"], held["competitor"]) == (quota, "yields_to_rival", capacity)
    fired, = world.events("resolved", "habit_activated")
    assert fired["habit_id"] == capacity and fired["outcome"] == "success"
    assert world.events("resolved", "slow_path_used") == [] and len(world.calls("quota_status")) == quota_calls
    assert {"effect": "slot_held", "tool": "reserve_slot", "args": {"route": "CPH"}} in world.world()["effects"]
