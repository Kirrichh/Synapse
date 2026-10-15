"""§33: preregistration fixes complete allocation and prevents carry leakage."""

import json
from pathlib import Path
import subprocess

import pytest

from acceptance.stage4.stage16.protocol import Protocol, canonical, preregister, source


def protocol_case(root, *, replicates=2):
    pairs = []
    for index in range(replicates):
        inputs = {}
        for arm in ("BASELINE", "GOLD"):
            directory = root / f"{index}-{arm}"
            directory.mkdir(parents=True)
            data = {"arm": arm, "repo_root": str(directory / "repo"), "run_root": str(directory / "run")}
            subprocess.run(["git", "init", "-q", data["repo_root"]], check=True)
            subprocess.run(["git", "-C", data["repo_root"], "-c", "user.name=Acceptance", "-c", "user.email=acceptance@example.invalid",
                "commit", "-q", "--allow-empty", "-m", "initial"], check=True)
            if arm == "GOLD":
                data["state_root"] = str(directory / "knowledge")
                Path(data["state_root"]).mkdir()
                (Path(data["state_root"]) / "project.json").write_bytes(b"{}")
                knowledge = directory / "knowledge.json"
                knowledge.write_bytes(b"{}")
                declaration = directory / "declaration.json"
                declaration.write_bytes(canonical({"knowledge_path": str(knowledge)}))
                data["declaration_ref"] = source(declaration)
            path = directory / "input.json"
            path.write_bytes(canonical(data))
            inputs[arm] = source(path)
        pairs.append({"pair_id": f"pair-{index}", "task_id": "task", "replicate_id": index, "inputs": inputs})
    return preregister(experiment_id="acceptance", pairs=pairs, repository=Path(__file__).resolve().parents[3],
        specification={"version": "test-protocol", "sha256": "a" * 64}, seed=17)


def test_allocation_is_complete_counterbalanced_and_survives_serialization(tmp_path):
    protocol = protocol_case(tmp_path, replicates=4)
    schedule = Protocol(protocol.raw).schedule()
    assert schedule == protocol.schedule()
    assert len(schedule) == 8
    assert len({slot["slot_id"] for slot in schedule}) == 8
    orders = [tuple(slot["arm"] for slot in schedule if slot["replicate_id"] == index) for index in range(4)]
    assert orders.count(("BASELINE", "GOLD")) == orders.count(("GOLD", "BASELINE")) == 2


def test_duplicate_replicate_and_selected_success_policy_are_refused(tmp_path):
    protocol = protocol_case(tmp_path)
    duplicate = protocol.payload()
    duplicate["pairs"][1]["replicate_id"] = duplicate["pairs"][0]["replicate_id"]
    with pytest.raises(ValueError, match="replicate"):
        Protocol(canonical(duplicate))
    selective = protocol.payload()
    selective["reporting_policy"] = "SELECTED_SUCCESS_ONLY"
    with pytest.raises(ValueError, match="selective"):
        Protocol(canonical(selective))


def test_changed_input_and_cross_arm_repository_alias_are_refused(tmp_path):
    protocol = protocol_case(tmp_path)
    ref = protocol.payload()["pairs"][0]["inputs"]["BASELINE"]
    path = Path(ref["path"])
    original = path.read_bytes()
    value = json.loads(original)
    value["arm"] = "GOLD"
    path.write_bytes(canonical(value))
    with pytest.raises(ValueError, match="changed"):
        protocol.validate_inputs()
    path.write_bytes(original)
    other = protocol.payload()["pairs"][0]["inputs"]["GOLD"]
    other_path = Path(other["path"])
    gold = json.loads(other_path.read_bytes())
    gold["repo_root"] = value["repo_root"]
    other_path.write_bytes(canonical(gold))
    revised = protocol.payload()
    revised["pairs"][0]["inputs"]["GOLD"] = source(other_path)
    revised["initial_inputs"][source(other_path)["sha256"]] = revised["initial_inputs"].pop(other["sha256"])
    with pytest.raises(ValueError, match="overlaps"):
        Protocol(canonical(revised)).validate_inputs()
