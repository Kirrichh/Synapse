"""§§33,35: complete durable allocation and fail-closed experiment recovery."""

from pathlib import Path
import sqlite3

import pytest

from acceptance.stage4.stage16.harness import Experiment
from acceptance.stage4.stage16.protocol import canonical, digest
from acceptance.stage4.stage16.test_protocol import protocol_case
from synapse.experiments.gold.persistence import ExclusiveStoreLock, PersistenceViolation


def test_concurrent_dispatch_is_refused_and_all_planned_replicates_remain(tmp_path):
    protocol = protocol_case(tmp_path / "inputs")
    experiment = Experiment(tmp_path / "experiment", repository=Path(__file__).resolve().parents[3], protocol=protocol)
    with ExclusiveStoreLock(experiment.root / "experiment.lock"):
        with pytest.raises(PersistenceViolation, match="LOCK_BUSY"):
            experiment.run_next()
    assert len(experiment.allocations()) == 4
    assert all(slot["state"] == "PLANNED" for slot in experiment.allocations())
    assert experiment.history() == []


def test_hash_valid_out_of_order_dispatch_cannot_be_recovered_as_a_valid_experiment(tmp_path):
    protocol = protocol_case(tmp_path / "inputs")
    experiment = Experiment(tmp_path / "experiment", repository=Path(__file__).resolve().parents[3], protocol=protocol)
    event = {"sequence": 1, "protocol": protocol.identity, "previous": protocol.identity,
        "slot_id": protocol.schedule()[1]["slot_id"], "kind": "STARTED", "payload": {}}
    with sqlite3.connect(experiment.database) as connection:
        connection.execute("INSERT INTO events VALUES(?,?,?)", (1, digest(event), canonical(event)))
    with pytest.raises(ValueError, match="order"):
        Experiment(experiment.root, repository=experiment.repository)


def test_initial_knowledge_drift_is_detected_before_dispatch(tmp_path):
    protocol = protocol_case(tmp_path / "inputs")
    slot = next(slot for slot in protocol.schedule() if slot["arm"] == "GOLD")
    initial = protocol.payload()["initial_inputs"][slot["input_ref"]["sha256"]]
    path = Path(initial["state"][0]["path"])
    path.write_bytes(canonical({"changed": True}))
    with pytest.raises(ValueError, match="initial knowledge"):
        protocol.validate_initial(slot)
