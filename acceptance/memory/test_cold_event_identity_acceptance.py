"""A wake consumes the events it matched, never another run's example with the same event id (finding R8 on
556624d).

Flights and trains are two procedures of one shape (``_travel`` and its renamed rail copy, with sources of its own):
a refused search recovered on the slow path. The flight recovery is learned in three tasks and, unused while two
rail recoveries are collected, archived for disuse. Then one task meets two refused flight searches and one refused
rail search: the two flight events wake the archived flight habit (T6). An event id is unique within its run only —
the events that woke it carry an id the earlier rail examples carry too — and the wake consumes exactly the events
it matched, by run and id: the third rail example joins the two before it and the rail procedure is born from all
three runs. Without the wake (control) the same three rail examples teach it too.
"""
from __future__ import annotations

import copy

import pytest

from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld

RENAMES = {"flights": "trains", "quota_status": "rail_quota", "booking_state": "rail_state"}


def _rail_tools():
    found = copy.deepcopy(travel.tools())
    for item in found:
        item["name"], item["source"] = RENAMES[item["name"]], f"rail-{item['source']}"
        if "state_check_for" in item["contract"]:
            item["contract"]["state_check_for"] = "trains"
    return found


def _body(source):
    return source[source.index("context "):source.index('print("searched")')]


TRAIN = travel.PROGRAM
for _old, _new in RENAMES.items():
    TRAIN = TRAIN.replace(f'"{_old}"', f'"{_new}"')
TRAIN = TRAIN.replace('"search"', '"train-search"')
#: The rail segment declared beside the flight one, in a task that searches flights twice and trains once.
_RAIL = (TRAIN[TRAIN.index("let req"):TRAIN.index("let plan")].replace("let req =", "let req_b =")
         .replace("let anchor =", "let anchor_b =").replace("let seg =", "let seg_b =")
         .replace('"requirement": req', '"requirement": req_b').replace('"anchor": anchor', '"anchor": anchor_b'))
COMBINED = (travel.PROGRAM.replace("let plan =", _RAIL + "let plan =")
            .replace('"segments": [seg]', '"segments": [seg, seg_b]')
            .replace('print("searched")', _body(travel.PROGRAM).replace('{"route": route}', '{"route": second}')
                     + _body(TRAIN).replace('{"route": route}', '{"route": train_route}') + 'print("searched")'))


@pytest.mark.parametrize("wake", [True, False], ids=["woken", "not-woken"])
def test_a_wake_consumes_only_the_events_it_matched(tmp_path, wake):
    provenance = travel.independent_provenance()
    provenance.update({f"rail-{source}": entry for source, entry in list(provenance.items())})
    world = MemoryWorld(tmp_path, travel.tools() + _rail_tools(), provenance=provenance,
                        parameters={"m_idle": 1, "n_medium_windows": 100, "n_low_windows": 100})
    for index, route in enumerate(["BUS", "YVR", "MSQ"]):
        world.run(travel.PROGRAM, f"fly-{index}", travel.inputs(f"fly-{index}", route))
    flight, = [birth for report in world.reports() for birth in report["births"]]
    for index, route in enumerate(["BUS", "YVR"]):
        world.run(TRAIN, f"rail-{index}", travel.inputs(f"rail-{index}", route))
    state = world.owner().state()
    assert state["habits"][flight["habit_id"]]["state"] == "dormant"
    rail, = [entry for entry in state["pool"].values() if entry["status"] != "born"]
    assert sorted(item["run_id"] for item in rail["episodes"]) == ["rail-0", "rail-1"]

    if wake:
        world.run(COMBINED, "last", {**travel.inputs("last", "TBS"), "second": "EVN", "train_route": "MSQ"})
    else:
        world.run(TRAIN, "last", travel.inputs("last", "MSQ"))
    report = world.reports()[-1]
    if wake:
        assert [(item["habit_id"], item["rule"]) for item in report["transitions"]
                if item["habit_id"] == flight["habit_id"]] == [(flight["habit_id"], "T6")]
        check, = report["cold_checks"]["dormant_matches"]
        assert {item["run_id"] for item in check["matches"]} == {"last"} and len(check["matches"]) == 2
        # The woken events share an id with an earlier rail example, in another run.
        assert {item["event_id"] for item in check["matches"]} & {item["event_id"] for item in rail["episodes"]}
    birth, = report["births"]
    assert sorted(item["run_id"] for item in birth["episodes"]) == ["last", "rail-0", "rail-1"]
    assert birth["habit_id"] != flight["habit_id"]
