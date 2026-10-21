"""Mock replay serves what the recording drew (determinism contract section 8.2, rule 1).

A recording appends every value ``time``, ``random`` and ``uuid`` drew as a
``side_effect`` event. ``replay --mock`` serves those values from the artifact
in order instead of drawing new ones, so a program whose control flow and
output depend on them replays exactly, and a draw beyond the record fails
closed like an exhausted LLM cache entry.

Identities a run derives from its run id (the fallback trace ids of VM and
memory events) are reproduced only under the same run id: the manifest keeps
the recording's run id and the replay restores it. An artifact recorded before
the run id was kept replays under a fresh one, as it always did.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse.builtins import BUILTINS
from synapse.golden_replay import (
    DeterministicReplayError,
    RecordedSideEffects,
    ReplayArtifactError,
    record_source,
    replay_mock_artifact,
)

ON_CALL = '''
agent Dispatcher {
    model "mock"
    fn handoff(name) {
        return llm(prompt "Write a handoff note for {name}")
    }
}
let team = ["Ann", "Bob", "Cid", "Dan"]
let roll = random()
let engineer = team[int(roll * len(team))]
if roll > 0.5 {
    print("night shift: " + engineer)
} else {
    print("day shift: " + engineer)
}
let ticket = uuid()
let opened = time()
let d = Dispatcher()
print(d.handoff(engineer) + " " + ticket + " " + str(opened))
'''

VM_DRAW = '''
compile vm {
    source "let r = random() let t = time() let pick = 1 if r > 0.5 { pick = 2 }"
    bind code
}
run vm {
    source code
    gas 500
    bind out
}
print(out.locals)
'''


def _refuse_live_draw(monkeypatch: pytest.MonkeyPatch) -> None:
    def live(*_args):
        raise AssertionError("mock replay drew a live value")

    for name in ("time", "random", "uuid"):
        monkeypatch.setitem(BUILTINS, name, live)


def _manifest(artifact: Path) -> dict:
    return json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))


def test_recorded_time_random_and_uuid_are_served_not_drawn(tmp_path: Path, monkeypatch) -> None:
    artifact = tmp_path / "on-call"
    record_source(ON_CALL, artifact)
    history = json.loads((artifact / "history.json").read_text(encoding="utf-8"))
    assert [event["name"] for event in history if event["type"] == "side_effect"] == ["random", "uuid", "time"]

    _refuse_live_draw(monkeypatch)
    first = replay_mock_artifact(artifact)
    second = replay_mock_artifact(artifact)

    assert first.drift == second.drift == 0
    assert first.final_history_hash == second.final_history_hash == _manifest(artifact)["final"]["final_history_hash"]


def test_a_draw_beyond_the_record_fails_closed() -> None:
    served = RecordedSideEffects([
        # An intention cascade named "random" leaves an event with that name: it is no draw.
        {"type": "intention_cascade_created", "name": "random", "cascade_id": "cascade-1"},
        {"type": "side_effect", "name": "random", "args": [], "result": 0.25},
        {"type": "llm_call", "result": "not a draw"},
    ]).registry({"len": len})

    assert served["len"] is len
    assert served["random"]() == 0.25
    with pytest.raises(DeterministicReplayError, match="random occurred more often than recorded"):
        served["random"]()
    with pytest.raises(DeterministicReplayError, match="uuid occurred more often than recorded"):
        served["uuid"]()


def test_a_vm_program_replays_under_the_recorded_run_id(tmp_path: Path, monkeypatch) -> None:
    artifact = tmp_path / "vm-draw"
    manifest = record_source(VM_DRAW, artifact)
    assert manifest["metadata"]["run_id"].startswith("run-")
    history = json.loads((artifact / "history.json").read_text(encoding="utf-8"))
    assert any(event.get("trace_id") for event in history)

    _refuse_live_draw(monkeypatch)
    assert replay_mock_artifact(artifact).drift == 0


def test_a_vm_program_recorded_without_its_run_id_still_drifts(tmp_path: Path) -> None:
    artifact = tmp_path / "vm-legacy"
    record_source(VM_DRAW, artifact)
    manifest = _manifest(artifact)
    del manifest["metadata"]["run_id"]
    (artifact / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(DeterministicReplayError, match="drift detected"):
        replay_mock_artifact(artifact)


def test_an_artifact_without_run_id_replays_as_before(tmp_path: Path) -> None:
    artifact = tmp_path / "legacy"
    record_source('let x = 2 + 3\nprint(x)\n', artifact)
    manifest = _manifest(artifact)
    del manifest["metadata"]["run_id"]
    (artifact / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert replay_mock_artifact(artifact).drift == 0


@pytest.mark.parametrize("run_id", [42, "", ["run-x"]])
def test_a_malformed_run_id_is_an_artifact_error(tmp_path: Path, run_id) -> None:
    artifact = tmp_path / "malformed"
    record_source('print("x")\n', artifact)
    manifest = _manifest(artifact)
    manifest["metadata"]["run_id"] = run_id
    (artifact / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReplayArtifactError, match="run_id must be a non-empty string"):
        replay_mock_artifact(artifact)


INCIDENT_MEMORY = '''
agent Lead { model "mock" }
let self = Lead
memory palace "Incidents" {
    rooms { episodic semantic }
    backend sqlite
    bind palace
}
imprint into palace.episodic { content "pool exhausted on db" confidence 0.9 source "rca" bind first }
imprint into palace.episodic { content "pool exhausted on cache" confidence 0.9 source "rca" bind second }
recall from palace.episodic { query "pool exhausted" threshold 0.1 limit 5 bind found }
integrate "draft" {
    imprint into palace.semantic { content "root is the cache" confidence 0.5 source "draft" bind draft }
    assert false, "the draft names the wrong root"
} on fail rollback
imprint into palace.semantic { content "root is the db pool" confidence 0.9 source "rca" bind verified }
intention cascade "Remediate" {
    mission "Restore service"
    task "Roll back the deploy"
    bind plan
}
plan weave with [self] under "Ops" {
    intention plan
    timeout 60
    bind execution
}
print(first + " " + second + " " + plan.id)
print(found)
'''


def test_memory_and_intention_identities_replay_without_drift(tmp_path: Path, monkeypatch) -> None:
    # Imprint ids, creation times and cascade ids come from the run, never from uuid4 or the host clock.
    artifact = tmp_path / "incident-memory"
    record_source(INCIDENT_MEMORY, artifact)
    history = json.loads((artifact / "history.json").read_text(encoding="utf-8"))
    assert not [event for event in history if event["type"] == "side_effect"]

    _refuse_live_draw(monkeypatch)
    assert replay_mock_artifact(artifact).drift == 0



def test_held_records_keep_distinct_names_across_a_rolled_back_imprint() -> None:
    from synapse import Interpreter, compile_to_ast

    interp = Interpreter()
    interp.interpret(compile_to_ast(INCIDENT_MEMORY))
    held = interp.memory_palaces["Incidents"].content()["records"]
    assert [record["content"] for record in held] == [
        "pool exhausted on db", "pool exhausted on cache", "root is the db pool", "Plan weave completed: completed"]
    # The rolled-back draft gave its position back: the sequence continues from what the palace holds.
    assert [record["sequence"] for record in held] == [0, 1, 2, 3]
    assert len({record["id"] for record in held}) == len(held)
