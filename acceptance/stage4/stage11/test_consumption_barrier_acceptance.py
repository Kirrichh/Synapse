"""§22 acceptance: the worker path crosses the consumption gate, and it bites.

Patch 8's exit criterion is that no path to a worker reaches one without a fresh
consumption decision taken at the moment of use. Stage 11 is that path, so these
checks are about the barrier being consequential rather than present: the
delivery owner takes a fresh admission, and it refuses to deliver a context that
rests on any other one. A gate that is called but cannot refuse is an
observation, not a barrier.

The last check closes the loop at the run level: a refusal is a typed attempt
outcome, the run records it, and no candidate was ever asked for.

Heavy: every case takes a real admission through ``admit_for_use_now``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import tests.gold_point_of_use_world as pou
from synapse.experiments.gold.point_of_use import CurrentAdmittedKnowledge
from synapse.experiments.gold.runner import AttemptOutcome, FallbackPolicy, GoldRunViolation
from synapse.experiments.gold.runner.delivery import deliver_attempt_context
from synapse.experiments.gold.runner_composition import run_gold_run

from acceptance.stage4.stage11._builders import (
    delivery_plan_source,
    invocation_for,
    run_world,
    scripted_transport,
    worker_context_source,
)


def test_a_delivery_rests_on_an_admission_taken_at_the_moment_of_use() -> None:
    plan = delivery_plan_source(None)
    delivery = deliver_attempt_context(
        admission_request=plan.admission_request,
        context_source=plan.context_source,
        invocation_source=plan.invocation_source,
        transport=plan.transport,
    )
    assert type(delivery.admitted) is CurrentAdmittedKnowledge
    assert delivery.context.admitted_knowledge is delivery.admitted
    assert delivery.receipt.context_id == delivery.context.context_id


def test_a_context_resting_on_an_earlier_admission_is_refused() -> None:
    """The gate decides delivery; it does not merely precede it.

    The context handed over was built from a genuine, previously granted
    admission. Every hash in it is valid and every record it names was really
    admitted — just not by the decision taken for *this* delivery. That is the
    substitution §22 forbids, and it is refused here.
    """

    stale = pou.admit(pou.admission_request())
    stale_context = worker_context_source(stale)

    with pytest.raises(GoldRunViolation) as caught:
        deliver_attempt_context(
            admission_request=pou.admission_request(),
            context_source=lambda _fresh: stale_context,
            invocation_source=invocation_for,
            transport=scripted_transport,
        )
    assert caught.value.failure_code.value == "CONSUMPTION_REFUSED"


def test_a_refused_delivery_becomes_a_recorded_attempt_and_no_candidate_is_asked_for(
    tmp_path: Path,
) -> None:
    """A run learns that an attempt was never delivered, and says so."""

    stale = pou.admit(pou.admission_request())
    stale_context = worker_context_source(stale)

    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
    )
    world.composition = _composition_with_stale_context(world, stale_context)

    result = run_gold_run(world.composition)

    assert result.attempts[0].outcome is AttemptOutcome.DELIVERY_REFUSED
    assert result.attempts[0].c1_status is None
    assert world.worker.calls == 0, "a refused delivery still asked a worker for a candidate"
    assert world.oracle.calls == 0


def _composition_with_stale_context(world, stale_context):
    """Rebuild the run's composition with a context bound to another admission."""

    from synapse.experiments.gold.runner.delivery import AttemptDeliveryPlan
    from synapse.experiments.gold.runner_composition import create_gold_run_composition
    from tests.gold_store_fence import fence_for

    from acceptance.stage4.stage11._builders import c1_boundary, phase_refs

    def plan_source(_context) -> AttemptDeliveryPlan:
        return AttemptDeliveryPlan(
            admission_request=pou.admission_request(),
            context_source=lambda _fresh: stale_context,
            invocation_source=invocation_for,
            transport=scripted_transport,
        )

    return create_gold_run_composition(
        run_root=world.run_root,
        manifest=world.manifest,
        c1_boundary=c1_boundary(world.repo, world.run_root, world.oracle),
        mutation_fence=fence_for(world.run_root),
        phase_refs_source=phase_refs,
        delivery_plan_source=plan_source,
        worker_result_source=world.worker,
        new_knowledge_available=lambda _index: False,
    )
