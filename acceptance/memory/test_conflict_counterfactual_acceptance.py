"""Step 2 of the conflict ladder: recorded counterfactual advice resolves a conflict (refinement §6).

The pair of competitors first reaches step 3: both on probation, the trigger
slow-only. The slow path keeps reading their experience; once both have
recorded outcomes on the shared trigger, the court asks the configured advisor
— twice, with different phrasing and order — whether the other habit's
outcome would have been better. Only its agreed answer resolves the conflict:
one stays, the other remains on probation, and the court itself lifts the
slow-only ban. The advisor is a recorded ``reason`` tool: asking it produces
no effect in the environment.
"""
from __future__ import annotations

import json

from acceptance.memory import _competitors as competitors
from acceptance.memory._world import answer, tool

QUESTION = "would_B_outcome_be_better"
POLICY = {"counterfactual_min_pairs": 1}


def _judge():
    return tool("judge", "judge:ops", [answer({"ok": True, "answer": "no", "reason": "the recorded outcomes are equal"},
                                              when={"question": QUESTION})], server="judge", role="reason")


def _court_records(world):
    journal = world.owner().gateway_root / "journal.jsonl"
    return [record for record in map(json.loads, journal.read_text().splitlines())
            if str(record["body"].get("run_id", "")).startswith("court:")]


def test_agreed_counterfactual_advice_resolves_the_conflict_and_lifts_slow_only(tmp_path):
    world = competitors.world(tmp_path, _judge(), parameters=POLICY, advisor="judge")
    first, second = competitors.learn_competitors(world)
    left, right = sorted([first["habit_id"], second["habit_id"]])

    # Step 3 first: one fire at equal trust is no counterfactual basis for the other habit.
    world.run(competitors.PROGRAM, "both", competitors.inputs("both", "VNO", "quota"))
    fired, = world.events("both", "habit_activated")
    report = world.reports()[-1]
    conflict, = report["conflicts"]
    assert conflict["step"] == 3 and conflict["advice"]["basis"] == "insufficient_recorded_outcomes"
    assert report["slow_only_triggers"] == [conflict["trigger"]]
    assert world.calls("judge") == []

    # The slow path recovers with the other habit's action: now both have recorded outcomes.
    other = next(item for item in (left, right) if item != fired["habit_id"])
    recovery = competitors.recovery_of(world, other)
    effects = world.world()["effects"]
    world.run(competitors.PROGRAM, "other", competitors.inputs("other", "TLL", recovery))
    assert world.opening("other")["learned"] == [] and len(world.events("other", "slow_path_used")) == 1
    report = world.reports()[-1]
    conflict, = report["conflicts"]
    assert conflict["step"] == 2 and conflict["slow_only_lifted"] is True
    assert conflict["resolution"] == f"{left}_stays_{right}_probation"
    advice = conflict["advice"]
    assert advice["basis"] == "recorded_outcomes" and advice["agreed"] is True and advice["calls"] == ["no", "no"]
    assert report["slow_only_triggers"] == []

    # The advisor was asked twice, in two phrasings, and changed nothing in the environment.
    asked = world.calls("judge")
    assert [item["question"] for item in asked] == [QUESTION, QUESTION]
    assert sorted(item["variant"] for item in asked) == [0, 1]
    assert world.world()["effects"] == effects
    court = _court_records(world)
    assert court and {record["body"]["tool"] for record in court if record["kind"] == "STARTED"} == {"judge"}

    # Only the court's verdict lifted the ban: the next session has its fast path again.
    world.run(competitors.PROGRAM, "resolved", competitors.inputs("resolved", "HEL", "quota"))
    assert world.opening("resolved")["learned"] and world.events("resolved", "slow_path_used") == []
    assert len(world.events("resolved", "habit_activated")) == 1
