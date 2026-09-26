"""§35/NR-13: interrupted legacy effects remain visible and are never repeated."""

from pathlib import Path
import subprocess
import sys

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from acceptance.stage4.stage16._paired_case import paired_case, REPOSITORY
from acceptance.stage4.stage16.assessment import assess
from acceptance.stage4.stage16.harness import Experiment
from acceptance.stage4.stage16.protocol import Protocol, canonical, read_source


def test_process_dies_after_real_baseline_effects_without_substituting_a_new_run(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNAPSE_ACCEPTANCE_PROVIDER_KEY", "controlled-provider-credential")
    with provider_endpoint() as (endpoint, requests):
        protocol, _ = paired_case(tmp_path / "inputs", endpoint)
        value = protocol.payload()
        for seed in range(100):
            value["seed"] = seed
            protocol = Protocol(canonical(value))
            if protocol.schedule()[0]["arm"] == "BASELINE":
                break
        assert protocol.schedule()[0]["arm"] == "BASELINE"
        experiment = Experiment(tmp_path / "experiment", repository=REPOSITORY, protocol=protocol)
        script = '''
import os, sys
from acceptance.stage4.stage16.harness import Experiment
original = Experiment._append
def append(self, slot, kind, payload):
    if kind == "FINISHED":
        os._exit(73)
    return original(self, slot, kind, payload)
Experiment._append = append
Experiment(sys.argv[1], repository=sys.argv[2]).run_next()
'''
        child = subprocess.run([sys.executable, "-B", "-c", script, str(experiment.root), str(REPOSITORY)],
            cwd=REPOSITORY, capture_output=True, text=True, timeout=90)
        assert child.returncode == 73, child.stderr
        assert len(requests) == 1
        allocation = experiment.allocations()[0]
        definition = read_source(allocation["input_ref"])
        assert (Path(definition["run_root"]) / "result.json").is_file()
        result = Experiment(experiment.root, repository=REPOSITORY).run_next()
        assert result["state"] == "INTERRUPTED"
        assert result["receipt"]["usage"] is None
        assert result["receipt"]["effects_repeated"] is False
        assert len(requests) == 1
        report = assess(experiment)
        assert len(report["pairs"]) == 1
        assert report["pairs"][0]["status"] == "INCOMPLETE"
        assert report["pairs"][0]["provider_token_difference"] is None
        assert experiment.allocations()[1]["state"] == "PLANNED"
