"""A crash while retention changes a case's storage form leaves one consistent version (refinement §9).

Retention records its acts before any body leaves the owner's store D. The
process running a session dies exactly between that record and the removal.
Afterwards the fact is recorded once, the body is still there, and memory
state has not changed. The next ordinary session completes the removal from
the record — it does not decide again — and its report applies the act once.
Recovery grants no new authority: the admitted habits and the boundary are
those the consolidation had already produced.
"""
from __future__ import annotations

import os
import subprocess
import sys

from acceptance.memory import _stock as stock
from acceptance.memory._world import REPOSITORY


def _dying_session(world, run_id):
    program = world.root / f"{run_id}.syn"
    program.write_text(stock.PROGRAM)
    inputs = world.root / f"{run_id}.input.json"
    inputs.write_text('{"item": "%s", "task_name": "%s"}' % (run_id, run_id))
    completed = subprocess.run(
        [sys.executable, "-B", "-m", "acceptance.memory._crash_driver", str(world.state), str(world.configuration_path),
         str(program), str(world.runs), run_id, str(inputs)],
        cwd=REPOSITORY, env={**os.environ, "PYTHONPATH": str(REPOSITORY)}, capture_output=True, text=True, timeout=900)
    return completed.returncode


def test_a_crash_between_the_recorded_act_and_the_removal_is_completed_once(tmp_path):
    world = stock.world(tmp_path)
    stock.count(world, "first")
    medium, low = stock.cases(world, "first")["medium"], stock.cases(world, "first")["low"]
    evidence = world.evidence()

    # The second session's consolidation commits, retention records its acts, and the process dies.
    assert _dying_session(world, "second") == 9
    passes = world.owner().retention_passes()
    assert len(passes) == 1
    recorded = {(act["qid"], act["act"]) for act in passes[0]["acts"]}
    assert {(medium["qid"], "raw_deleted"), (low["qid"], "tail")} <= recorded
    # Consistent: the bodies are still in custody and the state has not applied anything yet.
    assert evidence.get(medium["raw_ref"]) is not None and evidence.gone(medium["raw_ref"]) is None
    state = world.owner().state()
    assert state["quanta"][medium["qid"]]["retention_state"] == "full" and state["retention"]["cursor"] == 0
    boundary = world.reports()[-1]["snapshot_boundary_after"]
    habits = state["habits"]

    # The next ordinary session completes the recorded removal; the act is applied exactly once.
    stock.count(world, "third")
    assert evidence.get(medium["raw_ref"]) is None and evidence.gone(medium["raw_ref"])["reason"] == "compacted"
    passes = world.owner().retention_passes()
    per_case = [act for item in passes for act in item["acts"] if act.get("qid") == medium["qid"]]
    assert len(per_case) == 1
    applied = [act for report in world.reports() for act in report["retention"]["applied"]
               if act.get("qid") == medium["qid"]]
    assert len(applied) == 1
    state = world.owner().state()
    assert state["quanta"][medium["qid"]]["retention_state"] == "replay_only"
    # No new authority from recovery: no birth, no habit, the earlier boundary still names the same habits.
    assert state["habits"] == habits == {}
    assert all(report["births"] == [] for report in world.reports())
    assert boundary is not None
