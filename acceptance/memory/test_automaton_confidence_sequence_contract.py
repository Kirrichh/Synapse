"""The confidence-sequence decision rule of the automaton (review R6, next stage; Howard et al. 2021).

Pure data through the court's decision entry. Under ``confidence_sequence``
the automaton watches a time-uniform bound on the mean signal of the state's
distinct tasks, after every fire — the continuous monitoring the bound is
valid for:

* the bound is the published normal-mixture boundary for observations in
  [0, 1] (checked against an independent computation), and a stream monitored
  after every observation leaves it no more often than its α (simulated);
* repeats of one task are one observation: twenty successes of one task
  promote nothing, successes in distinct tasks do;
* one stream reaches the same transitions however it is cut into windows;
* clustered errors demote; rare errors among successes do not;
* too little experience is never a cause.
"""
from __future__ import annotations

import math
import random

import pytest

from acceptance.memory import _court_data as data
from synapse.memory_consolidation.policy import PARAMETERS, cs_radius

FIXED_TRUST = {"min_evidence": 1000, "m_idle": 50}


def _reference_radius(count, alpha=0.05, rho=1.0):
    """Howard, Ramdas, McAuliffe, Sekhon (2021): normal mixture, sub-Gaussian variance 1/4 per observation."""
    v = count * 0.25
    return math.sqrt(2 * (v + rho) * math.log(math.sqrt((v + rho) / rho) * 2 / alpha)) / count


def test_the_bound_is_the_published_boundary():
    for count in (1, 2, 5, 10, 100, 1000):
        assert math.isclose(cs_radius(PARAMETERS, count), _reference_radius(count), rel_tol=1e-12)
    assert cs_radius(PARAMETERS, 0) == math.inf


@pytest.mark.parametrize("mean", [0.3, 0.7, 0.9])
def test_a_continuously_monitored_stream_leaves_the_bound_at_most_alpha_often(mean):
    rng = random.Random(int(mean * 1000))
    streams, length, escaped = 400, 200, 0
    for _ in range(streams):
        total = 0.0
        for count in range(1, length + 1):
            total += 1.0 if rng.random() < mean else 0.0
            if abs(total / count - mean) > cs_radius(PARAMETERS, count):
                escaped += 1
                break
    assert escaped / streams <= PARAMETERS["cs"]["alpha"]


def _fire(habit_id, trigger_id, index, success, task=None):
    fire = data.fire(habit_id, trigger_id, index, success)
    return {**fire, "task_id": task or fire["task_id"]}


def _run(outcomes, split, *, tasks=None, **metadata):
    config, state, ids = data.world(FIXED_TRUST, rule="confidence_sequence", q=metadata)
    habit_id = ids["q"]
    trigger_id = state["habits"][habit_id]["trigger_id"]
    fires = [_fire(habit_id, trigger_id, index, success, None if tasks is None else tasks[index])
             for index, success in enumerate(outcomes)]
    moves, start = [], 0
    for size in split:
        state, decision = data.window(config, state, fires[start:start + size])
        moves.extend((item["rule"], item["to"], item["cause"], (item.get("at") or {}).get("event_id"))
                     for item in decision["sections"]["transitions"])
        start += size
    return moves, state["habits"][habit_id]


def test_repeats_of_one_task_are_one_observation():
    moves, metadata = _run([True] * 40, (40,), tasks=["one-task"] * 40)
    assert moves == [] and metadata["cs_tasks"] == ["one-task"]
    # Continuous monitoring is paid for in experience: about thirty distinct tasks before the bound promotes.
    moves, _ = _run([True] * 40, (40,))
    assert moves and moves[0][:3] == ("T1", "active", "sufficient_experience")
    needed = next(count for count in range(1, 100) if 1.0 - _reference_radius(count) >= 0.70)
    assert moves[0][3] == f"ev-{needed - 1:04d}" and needed >= 30  # The fire of the needed-th distinct task.


@pytest.mark.parametrize("split", [(50,), (1,) * 50, (7, 43), (25, 25)])
def test_one_stream_reaches_the_same_transitions_however_it_is_cut(split):
    stream = [True] * 40 + [False] * 10
    reference, _ = _run(stream, (50,))
    moves, _ = _run(stream, split)
    assert moves == reference and reference[0][:2] == ("T1", "active")


def test_clustered_errors_demote_and_rare_errors_do_not():
    moves, metadata = _run([False] * 12, (12,), state="active")
    assert [move[:3] for move in moves] == [("T3", "probation", "confirmed_errors")]
    moves, metadata = _run(([True] * 9 + [False]) * 4, (40,), state="active")
    assert moves == [] and metadata["state"] == "active"


def test_too_little_experience_is_never_a_cause():
    moves, metadata = _run([True, False, True], (3,))
    assert moves == [] and metadata["state"] == "born"
