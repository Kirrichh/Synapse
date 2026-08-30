"""§26 acceptance: the run classifies what C1 reports, separately for each case.

The five scenarios in ``runner_cases_v1.json`` are the outcomes a Gold attempt
can reach through the real C1 boundary: no candidate, infrastructure failure, an
unresolved oracle, an applied change with a resolved oracle, and an explicit
Baseline fallback. §26 requires them to stay distinct — a run that collapses any
two of them has lost the ability to say what happened.

Heavy: every case drives a real controlled change, a real bridge commit and a
real §22 crossing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse.experiments.gold.runner import FallbackPolicy
from synapse.experiments.gold.runner_composition import run_gold_run

from acceptance.stage4.stage11._builders import (
    candidate_result,
    no_candidate_result,
    run_world,
)

FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "gold" / "runner_cases_v1.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[case["case_id"] for case in CASES])
def test_each_scenario_reaches_its_own_outcome_and_status(case: dict, tmp_path: Path) -> None:
    expected = case["expected"]
    world = run_world(
        tmp_path,
        max_attempts=case["max_attempts"],
        fallback_policy=FallbackPolicy(case["fallback_policy"]),
        oracle_outcomes=[(case["oracle"]["resolved"], case["oracle"]["infra_error"])],
        worker_outcomes=None,
    )
    if case["worker"] == "NO_PATCH":
        world.worker.outcomes = [no_candidate_result()]
    else:
        world.worker.outcomes = [candidate_result(world.patch_text)]

    result = run_gold_run(world.composition)

    assert len(result.attempts) == expected["attempts"]
    assert result.final_status.value == expected["final_status"]
    assert result.attempts[-1].outcome.value == expected["last_outcome"]
    assert result.attempts[-1].c1_status == expected["c1_status"]


def test_a_fallback_run_is_never_reported_as_gold(tmp_path: Path) -> None:
    """NR-13: an explicit Baseline arm keeps its own identity."""

    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.EXPLICIT_BASELINE_ARM,
        oracle_outcomes=[(True, True)],
    )
    result = run_gold_run(world.composition)
    assert not result.final_status.value.startswith("GOLD")
    assert result.fallback_arm_id is not None
    assert result.fallback_arm_id != world.manifest.gold_run_id
