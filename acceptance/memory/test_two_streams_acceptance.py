"""Two streams of observations, one owner of changes (refinement §18 "Два потока").

Two Synapse sessions run in their own processes while Gold freezes the inputs
of a project run on the same connected project; each reaches the memory court
through its own path — the durable session's full consolidation, Gold's
element-owner job through the court port (which decides only when it finds an
unjudged tail, and otherwise pins the head). Every decision extends one chain,
and every session's history is applied exactly once, in contiguous windows.
"""
from __future__ import annotations

from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld
from acceptance.stage4.stage16._source_inputs import consumer_case
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.project_agents import read_active_memory
from synapse.experiments.gold.run_inputs import freeze_gold_inputs
from synapse.experiments.gold.source_snapshot import memory_source_basis
from synapse.memory_consolidation.project_port import ProjectMemoryCourt


def test_parallel_sessions_and_a_gold_run_share_one_court_chain(tmp_path):
    case, _ = consumer_case(tmp_path, automatic_targets=True)
    world = MemoryWorld(tmp_path / "memory", travel.tools(), provenance=travel.independent_provenance(),
                        state=tmp_path / "state")
    world.run(travel.PROGRAM, "first", travel.inputs("task-first", "BUS"))  # Binds the owner's configuration.

    streams = [world.start(travel.PROGRAM, run_id, travel.inputs(f"task-{run_id}", route))
               for run_id, route in (("left", "YVR"), ("right", "MSQ"))]
    frozen = freeze_gold_inputs(declaration_path=case.input_path, project=open_gold_project(tmp_path / "state"),
                                run_root=case.run_root, court=ProjectMemoryCourt())
    for process in streams:
        stdout, stderr = process.communicate(timeout=900)
        assert process.returncode == 0, (stdout, stderr)

    # One chain: every decision extends its predecessor.
    applied = world.owner().applied()
    receipts = [item["receipt"] for item in applied]
    assert [item["decision"]["predecessor"] for item in applied] == [None, *receipts[:-1]]
    modes = [item["decision"]["consolidation"]["mode"] for item in applied]
    # Each session ends with its own full consolidation; Gold's job adds a summary only when it finds a tail.
    assert modes.count("full") >= 3 and set(modes) <= {"full", "summary"}
    # Gold's job pinned a decision of that chain.
    snapshot = frozen.data["source_snapshot"]
    frame = read_active_memory(snapshot["project_memory"], source_snapshot=memory_source_basis(snapshot),
                               run_memory_selection=snapshot["run_memory_selection"])["active_memory_frame"]
    assert frame["court"]["decision"] in receipts

    # Every session is applied once: its windows are contiguous and cover its whole history.
    windows: dict[str, list] = {}
    for report in world.reports():
        for entry in report["window"]["sessions"]:
            windows.setdefault(entry["run_id"], []).append((entry["from"], entry["to"]))
    for run_id in ("first", "left", "right"):
        spans = sorted(windows[run_id])
        assert spans[0][0] == 0 and all(left[1] == right[0] for left, right in zip(spans, spans[1:]))
        assert spans[-1][1] == len(world.history(run_id)) - 1  # All but its own consolidation record.
    # And each recovered episode reached the pool once.
    episodes = [(item["run_id"], item["event_id"]) for report in world.reports() for birth in report["births"]
                for item in birth["episodes"]]
    assert sorted(episodes) == sorted(set(episodes))
