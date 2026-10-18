"""A run's memory job holds its run id, so nothing refused before the run may request it.

The job a run reads from project memory is keyed by the project, the run directory and the run id, and is
requested while the run's inputs are frozen. A declaration refused for its own content (its admitted agent or
a support file changed after it was written) or a run directory that already exists is refused before that job
is requested: project memory is unchanged, and the corrected run keeps its run id even with other knowledge. A
run id whose job was requested with other inputs is refused by name, with what a new run needs.
"""
import json
from pathlib import Path

import pytest

from acceptance.stage4.stage16._source_inputs import consumer_case
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.project_memory_store import ProjectMemoryStore
from synapse.experiments.gold.run_inputs import freeze_gold_inputs
from synapse.memory_consolidation.project_port import ProjectMemoryCourt


def _memory(state):
    return ProjectMemoryStore(state, read_only=True).inventory()


def _with_limit(case, limit):
    """The run's knowledge input with another recall limit: the run reads other project experience."""
    knowledge = json.loads(case.knowledge_path.read_text())
    case.knowledge_path.write_text(json.dumps({**knowledge, "experience_limit": limit}))


@pytest.mark.parametrize(("damage", "refusal"), [
    ("agent-program", "agent executable, adapter or runtime file changed"),
    ("support-evidence", "project support evidence is repeated or changed"),
    ("support-repeated", "project support evidence is repeated or changed"),
])
def test_a_refused_declaration_requests_no_memory_job_and_its_correction_keeps_the_run_id(tmp_path, damage, refusal):
    case, _ = consumer_case(tmp_path, automatic_targets=True)
    state, court = tmp_path / "state", ProjectMemoryCourt()
    project = open_gold_project(state)
    freeze_gold_inputs(declaration_path=case.input_path, project=project, run_root=tmp_path / "earlier-run",
                       court=court)
    before = _memory(state)
    # After the declaration was written, the admitted agent's program or one of its support files changes,
    # or the knowledge input names one support file twice.
    knowledge = json.loads(case.knowledge_path.read_text())
    if damage == "agent-program":
        changed = tmp_path / "agent" / "process_agent.py"
    elif damage == "support-evidence":
        changed = Path(knowledge["files"][-1]["path"])
    else:
        changed = case.knowledge_path
    retained = changed.read_bytes()
    if damage == "support-repeated":
        changed.write_text(json.dumps({**knowledge, "files": [*knowledge["files"], knowledge["files"][-1]]}))
    else:
        changed.write_bytes(retained + b"# changed after the declaration\n")

    with pytest.raises(ValueError, match=refusal):
        freeze_gold_inputs(declaration_path=case.input_path, project=project, run_root=case.run_root, court=court)
    assert _memory(state) == before

    # Corrected, and reading other experience than the refused run would have, under the same run id.
    changed.write_bytes(retained)
    _with_limit(case, 32)
    freeze_gold_inputs(declaration_path=case.input_path, project=project, run_root=case.run_root, court=court)
    earlier = {event["job_key"] for event, _ in before}
    job = [event for event, _ in _memory(state) if event["job_key"] not in earlier]
    assert sorted(event["kind"] for event in job) == ["FRAME_COMPLETED", "REQUESTED", "STARTED"]
    request, = [event["payload"] for event in job if event["kind"] == "REQUESTED"]
    run_id = json.loads(case.input_path.read_text())["run_id"]
    assert (request["run_id"], request["run_root"]) == (run_id, str(case.run_root))


def test_a_run_id_requested_with_other_inputs_is_refused_by_name(tmp_path):
    case, _ = consumer_case(tmp_path, automatic_targets=True)
    state, court = tmp_path / "state", ProjectMemoryCourt()
    project = open_gold_project(state)
    freeze_gold_inputs(declaration_path=case.input_path, project=project, run_root=case.run_root, court=court)
    before = _memory(state)
    run_id = json.loads(case.input_path.read_text())["run_id"]

    _with_limit(case, 32)
    with pytest.raises(ValueError) as refused:
        freeze_gold_inputs(declaration_path=case.input_path, project=project, run_root=case.run_root, court=court)
    assert str(refused.value) == (f"run id {run_id!r} was already used with other inputs for this run directory; "
                                  "a new run needs another run id or run directory")
    assert _memory(state) == before


def test_a_run_into_an_existing_directory_is_refused_before_its_memory_job(tmp_path):
    case, _ = consumer_case(tmp_path, automatic_targets=True)
    state = tmp_path / "state"
    freeze_gold_inputs(declaration_path=case.input_path, project=open_gold_project(state),
                       run_root=tmp_path / "earlier-run", court=ProjectMemoryCourt())
    before = _memory(state)
    case.run_root.mkdir()

    code, refused = case.start()

    assert code == 1 and refused["status"] == "GOLD_UNAVAILABLE", refused
    assert refused["detail"] == ("the run directory already exists; a new run needs a new directory "
                                 "('project resume' continues the run started there)")
    assert _memory(state) == before and list(case.run_root.iterdir()) == []
