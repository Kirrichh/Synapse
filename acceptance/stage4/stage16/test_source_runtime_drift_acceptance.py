"""Source knowledge verified in another runtime is refused for that reason.

The source verifier records the Python runtime it observed: interpreter, platform and installed
distributions. A distribution installed after ``project learn`` leaves the consumer in another runtime than
the verified one. The run's compatibility evidence names an environment mismatch for the task contract and
for the verifier's result, no other dimension fails, and the worker receives nothing.
"""
import json
import os

from acceptance.stage4.stage16._source_inputs import consumer_case
from synapse.experiments.gold.persistence import scan_journal


def _source_decisions(run_root):
    """The compatibility decisions the run's retrieval recorded, with their dimensions."""
    records = []
    for journal in sorted(run_root.glob("retrieval/*/compatibility/compatibility.journal")):
        for frame in scan_journal(journal, create_if_missing=False).frames:
            record = json.loads(json.loads(frame.payload)["canonical_record"])
            if "decision_kind" in record and "evidence" in record:
                records.append(record)
    return records


def test_a_distribution_installed_after_learning_is_an_environment_mismatch(tmp_path, monkeypatch):
    case, _ = consumer_case(tmp_path)
    installed = tmp_path / "installed-after-learning"
    (installed / "acceptance_drift-1.0.dist-info").mkdir(parents=True)
    (installed / "acceptance_drift-1.0.dist-info" / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: acceptance-drift\nVersion: 1.0\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, (str(installed), os.environ.get("PYTHONPATH")))))

    code, pending = case.start()
    assert code == 3, pending
    code, completed = case.approve(pending)

    assert code != 0 and not case.worker.exists(), completed
    decision, = _source_decisions(case.run_root)
    failed = {item["dimension"]: item["reason"] for item in decision["evidence"]["dimensions"]
              if item["result"] == "FAIL"}
    assert failed == {"task_contract_identity": "ENVIRONMENT_MISMATCH", "oracle": "ENVIRONMENT_MISMATCH"}
