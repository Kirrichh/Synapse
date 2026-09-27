"""Learning through the canonical launch: consolidation, birth, loading and applicability.

Durable sessions of one connected project recover a refused search on the
slow path. The court consolidates every session once; after three verified,
independently attested recoveries in three tasks it births a learned habit,
Gold admits its behavior, and the next ordinary session loads it and executes
it on the fast path — inside its typed scope only, and only while Gold admits
it. Copies of one observation never add a vote.
"""
from __future__ import annotations

from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld
from synapse.experiments.gold.contracts import ActorIdentity
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.learned_habit_lifecycle import withdraw_learned_habit


def _learn(world, routes):
    for index, route in enumerate(routes):
        world.run(travel.PROGRAM, f"learn-{index}", travel.inputs(f"task-{index}", route))


def test_verified_recovery_is_born_loaded_and_bounded_by_its_scope(tmp_path):
    world = MemoryWorld(tmp_path, travel.tools(), provenance=travel.independent_provenance())
    _learn(world, ["BUS", "YVR", "MSQ"])

    # Consolidation: each session is applied once, in one chain; the third births.
    reports = world.reports()
    assert [len(report["births"]) for report in reports] == [0, 0, 1]
    assert [[entry["run_id"] for entry in report["window"]["sessions"]] for report in reports] == [
        ["learn-0"], ["learn-1"], ["learn-2"]]
    assert [update["reasons"] for update in reports[0]["pool_updates"]] == [
        ["repeatability_below_threshold", "single_task"]]
    birth, = reports[-1]["births"]
    assert birth["gates"]["admitted"] is True and birth["criteria"]["tasks"] == 3
    # The quota answer attests another claim; the booking system observes the same flights.
    assert birth["independence"]["verdict"] == "independent"
    assert birth["independence"]["witnesses"] == ["flights:acct1", "gds:ops"]
    assert all(world.opening(f"learn-{index}")["learned"] == [] for index in range(3))

    # Loading: the next ordinary session loads the admitted habit and it acts on the fast path.
    searches = len(world.calls("flights"))
    world.run(travel.PROGRAM, "inside", travel.inputs("task-inside", "TBS"))
    learned, = world.opening("inside")["learned"]
    assert learned["habit_id"] == birth["habit_id"]
    activated, = world.events("inside", "habit_activated")
    assert activated["habit_id"] == birth["habit_id"] and world.events("inside", "slow_path_used") == []
    # The environment saw the learned procedure: refused search, quota, repeat, independent observation.
    assert world.calls("flights")[searches:] == [{"route": "TBS"}, {"route": "TBS"}]
    assert world.calls("quota_status")[-1] == {"route": "TBS"} and world.calls("booking_state")[-1] == {"route": "TBS"}

    # Applicability: directly outside its typed scope (a domestic route) it does not fire.
    world.run(travel.PROGRAM, "outside", travel.inputs("task-outside", "LED"))
    assert world.events("outside", "habit_activated") == []
    assert len(world.events("outside", "slow_path_used")) == 1
    assert world.opening("outside")["learned"] == world.opening("inside")["learned"]

    # A completed session re-entered changes neither memory nor the environment.
    journal, environment = world.journal(), world.world()
    code, _, _ = world.resume("inside")
    assert world.journal() == journal and world.world() == environment, code

    # Trust: every fire the court counted or conserved names a recorded activation.
    fires = {(run, event["trigger_event_id"]) for run in ("inside", "outside")
             for event in world.events(run, "habit_activated")}
    conserved = [(item["run_id"], item["event_id"]) for report in world.reports()
                 for entry in report["pending_evidence"] for item in entry["events"]]
    assert conserved and set(conserved) <= fires

    # Not admitted, not loaded: the governing operator withdraws the behavior in Gold.
    withdraw_learned_habit(open_gold_project(world.state), birth["gates"]["publication"],
                           operator=ActorIdentity("acceptance.operator"), reason="acceptance withdrawal")
    world.run(travel.PROGRAM, "withdrawn", travel.inputs("task-withdrawn", "EVN"))
    assert world.opening("withdrawn")["learned"] == [] and world.events("withdrawn", "habit_activated") == []
    assert len(world.events("withdrawn", "slow_path_used")) == 1
    verdict = world.reports()[-1]["legitimacy"][birth["habit_id"]]
    assert verdict["admitted"] is False and verdict["state"] == "SUPERSEDED"


def test_copies_of_one_observation_do_not_add_votes(tmp_path):
    # Three tasks repeat one search and receive one answer: one observation, copied.
    world = MemoryWorld(tmp_path, travel.tools(busy_every_time=("BUS",)), provenance=travel.independent_provenance())
    _learn(world, ["BUS", "BUS", "BUS"])
    reports = world.reports()
    assert [len(report["births"]) for report in reports] == [0, 0, 0]
    update, = reports[-1]["pool_updates"]
    assert update["criteria"]["distinct_episodes"] == 1 and update["criteria"]["copies"] == 2
    assert "repeatability_below_threshold" in update["reasons"]
    assert len(world.calls("flights")) == 6  # The environment really answered every search.
