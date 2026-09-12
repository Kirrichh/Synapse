"""§35: a lost external receipt resumes the same canonical Gold run."""

import subprocess
import sys

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from acceptance.stage4.stage16._paired_case import paired_case, REPOSITORY
from acceptance.stage4.stage16.assessment import assess
from acceptance.stage4.stage16.harness import Experiment


def test_process_dies_after_gold_pause_and_recovery_uses_its_existing_run(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNAPSE_ACCEPTANCE_PROVIDER_KEY", "controlled-provider-credential")
    with provider_endpoint() as (endpoint, requests):
        protocol, cases = paired_case(tmp_path / "inputs", endpoint)
        assert protocol.schedule()[0]["arm"] == "GOLD"
        experiment = Experiment(tmp_path / "experiment", repository=REPOSITORY, protocol=protocol)
        script = '''
import os, sys
from acceptance.stage4.stage16.harness import Experiment
original = Experiment._append
def append(self, slot, kind, payload):
    if kind == "APPROVAL_REQUIRED":
        os._exit(73)
    return original(self, slot, kind, payload)
Experiment._append = append
Experiment(sys.argv[1], repository=sys.argv[2]).run_next()
'''
        child = subprocess.run([sys.executable, "-B", "-c", script, str(experiment.root), str(REPOSITORY)],
            cwd=REPOSITORY, capture_output=True, text=True, timeout=300)
        assert child.returncode == 73, child.stderr
        assert experiment.allocations()[0]["state"] == "STARTED"
        assert cases[0].run_root.is_dir()
        assert requests == []
        recovered = Experiment(experiment.root, repository=REPOSITORY)
        assert all(slot["state"] == "FINISHED" for slot in recovered.run_all(approve_pending=True))
        assert len(requests) == 2
        pair, = assess(recovered)["pairs"]
        assert pair["status"] == "INCOMPLETE", pair
        assert pair["observations"]["GOLD"]["provider_tokens"] == 18
        assert pair["observations"]["GOLD"]["duration"]["status"] == "INCOMPLETE"
        assert pair["observations"]["GOLD"]["external_action_duration_ns"] is None
        assert pair["external_action_duration_difference_ns"] is None
        events = [event for event in recovered.history() if event["slot_id"] == protocol.schedule()[0]["slot_id"]]
        assert sum(event["kind"] == "STARTED" for event in events) == 1
        assert sum(event["kind"] == "RESUMING" for event in events) == 2
