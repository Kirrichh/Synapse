"""The effectiveness automaton judges experience, not windows (review R6).

Pure data through the court's decision entry, window after window. One stream
of verified fires is split into windows in different ways; the automaton must
not reach opposite conclusions from the same stream:

* rare successes (one per window) promote the habit exactly as a single window
  of the same successes does — too little experience is never a demotion;
* errors clustered in the stream demote the habit however the stream is cut,
  and the fires after the demotion count in the new state the same way;
* a promotion reached before later errors is the window's transition, and the
  errors demote the promoted habit at the next consolidation — the order the
  stream reached them, never the other way round;
* rare errors among successes never demote;
* an event delivered twice counts once;
* after a change of environment, recent errors demote an active habit whatever
  its long history of successes;
* disuse is its own cause, and every transition names its cause;
* a fire whose body attempted what the contracts forbid archives the habit at
  that consolidation, whatever its trust and successes — a serious violation
  waits for no statistics.

Trust is held fixed (no update below an unreachable minimum evidence) so the
automaton's own rules are what is observed.
"""
from __future__ import annotations

import pytest

from acceptance.memory import _court_data as data

FIXED_TRUST = {"min_evidence": 1000, "t1_trust": 0.7, "t1_fires": 5, "t1_tasks": 2, "t3_fires": 5,
               "t3_signal": 0.65, "t4_fires": 5, "t4_signal": 0.7, "m_idle": 6}


def _run(stream, split, **metadata):
    """The stream of outcomes cut into windows of the given sizes; the transitions and the final metadata."""
    assert sum(split) == len(stream)
    config, state, ids = data.world(FIXED_TRUST, q=metadata)
    habit_id = ids["q"]
    trigger_id = state["habits"][habit_id]["trigger_id"]
    fires = [data.fire(habit_id, trigger_id, index, success) for index, success in enumerate(stream)]
    moves, start = [], 0
    for size in split:
        state, decision = data.window(config, state, fires[start:start + size])
        moves.extend((item["rule"], item["to"], item["cause"]) for item in decision["sections"]["transitions"])
        start += size
    return moves, state["habits"][habit_id]


def _view(metadata):
    return {key: metadata[key] for key in ("state", "fires_in_state", "signals_in_state", "signal_sum_in_state",
                                           "tail", "fires_since_birth")}


@pytest.mark.parametrize("split", [(5,), (1, 1, 1, 1, 1), (2, 3), (4, 1)])
def test_rare_successes_promote_like_one_window_of_them(split):
    moves, metadata = _run([True] * 5, split, trust=0.9)
    assert moves == [("T1", "active", "sufficient_experience")] and metadata["state"] == "active"


@pytest.mark.parametrize("split", [(1, 1, 1), (3,)])
def test_too_little_experience_keeps_the_state(split):
    moves, metadata = _run([True] * 3, split, trust=0.9)
    assert moves == [] and metadata["state"] == "born"


ERRORS = [True, True, False, False, False, True, True]


def test_clustered_errors_demote_however_the_stream_is_cut():
    results = [_run(ERRORS, split, state="active") for split in ((7,), (1,) * 7, (3, 4), (5, 2), (4, 3))]
    for moves, _ in results:
        assert moves == [("T3", "probation", "confirmed_errors")]
    # The two successes after the demotion count in probation, whatever window they arrived in.
    assert all(_view(metadata) == _view(results[0][1]) for _, metadata in results)
    assert results[0][1]["fires_in_state"] == 2 and results[0][1]["tail"] == [1.0, 1.0]


PROMOTED_THEN_FAILING = [True] * 5 + [False] * 5


@pytest.mark.parametrize("split", [(10, 0), (5, 5, 0), (1,) * 10 + (0,), (7, 3, 0)])
def test_a_promotion_reached_before_errors_comes_first(split):
    # The trailing empty window is the next consolidation: it judges what the last window carried over.
    moves, metadata = _run(PROMOTED_THEN_FAILING, split, trust=0.9)
    assert moves == [("T1", "active", "sufficient_experience"), ("T3", "probation", "confirmed_errors")]
    assert metadata["state"] == "probation"


@pytest.mark.parametrize("split", [(15,), (1,) * 15, (5, 5, 5), (7, 8)])
def test_rare_errors_among_successes_never_demote(split):
    moves, metadata = _run(([True] * 4 + [False]) * 3, split, state="active")
    assert moves == [] and metadata["state"] == "active"


def test_an_event_delivered_twice_counts_once():
    config, state, ids = data.world(FIXED_TRUST, q={"state": "active"})
    habit_id = ids["q"]
    trigger_id = state["habits"][habit_id]["trigger_id"]
    fires = [data.fire(habit_id, trigger_id, index, index > 1) for index in range(4)]
    # Two failures, one delivered three times: two errors among four signals, below the evidence of the rule.
    state, decision = data.window(config, state, [*fires, fires[0], fires[0]])
    assert decision["sections"]["transitions"] == []
    assert decision["habits"][habit_id]["signals_in_state"] == 4
    assert [item["why"] for item in decision["sections"]["excluded_signals"]] == ["repeated_event"] * 2


def test_recent_errors_after_a_change_demote_whatever_the_history():
    history = {"state": "active", "fires_in_state": 200, "signals_in_state": 200, "signal_sum_in_state": 200.0,
               "tail": [1.0] * 5}
    moves, metadata = _run([False, False], (1, 1), **history)
    assert moves == [("T3", "probation", "confirmed_errors")]
    # The cumulative mean (198/202) would never have noticed.
    assert metadata["state"] == "probation"


@pytest.mark.parametrize("state_name, rule, target", [("born", "T2", "probation"), ("probation", "T5", "dormant")])
def test_disuse_is_its_own_cause(state_name, rule, target):
    moves, _ = _run([], (0,) * 6, state=state_name)
    assert moves == [(rule, target, "disuse")]
    moves, _ = _run([], (0,) * 5, state=state_name)
    assert moves == []


@pytest.mark.parametrize("state_name, trust", [("born", 0.9), ("active", 0.95), ("probation", 0.6)])
def test_a_contract_violation_archives_at_once_whatever_the_experience(state_name, trust):
    config, state, ids = data.world(FIXED_TRUST, q={"state": state_name, "trust": trust})
    habit_id = ids["q"]
    trigger_id = state["habits"][habit_id]["trigger_id"]
    fires = [data.fire(habit_id, trigger_id, index, True) for index in range(6)]
    # One success among them whose body also attempted what the contracts forbid; nothing waits for statistics.
    fires[2] = {**fires[2], "refusals": ["a hidden repeat of an unresolved operation: the effect is unknown"]}
    state, decision = data.window(config, state, fires)
    assert [(item["rule"], item["to"], item["cause"]) for item in decision["sections"]["transitions"]][-1:] == [
        ("TV", "dormant", "contract_violation")]
    assert decision["habits"][habit_id]["state"] == "dormant"
    assert decision["sections"]["contract_violations"] == [
        {"habit_id": habit_id, "layer": 2, "run_id": "run-0002", "event_id": "ev-0002",
         "refusals": ["a hidden repeat of an unresolved operation: the effect is unknown"]}]

