"""§33/NR-13: real counterbalanced replicates preserve every failed attempt."""

import json

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from acceptance.stage4.stage16._paired_case import paired_case, REPOSITORY
from acceptance.stage4.stage16.assessment import assess
from acceptance.stage4.stage16.harness import Experiment


def test_real_replicates_retain_all_attempts_and_isolate_raw_carry(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNAPSE_ACCEPTANCE_PROVIDER_KEY", "controlled-provider-credential")
    with provider_endpoint() as (endpoint, requests):
        protocol, _ = paired_case(tmp_path / "inputs", endpoint, replicates=2, max_attempts=2)
        experiment = Experiment(tmp_path / "experiment", repository=REPOSITORY, protocol=protocol)
        allocations = experiment.run_all(approve_pending=True)
        assert all(slot["state"] == "FINISHED" for slot in allocations), allocations
        result = assess(experiment)
        assert len(result["pairs"]) == 2
        assert {tuple(pair["execution_order"]) for pair in result["pairs"]} == {("BASELINE", "GOLD"), ("GOLD", "BASELINE")}
        identities = set()
        for pair in result["pairs"]:
            assert pair["status"] == "DIAGNOSTIC_COMPLETE", pair
            for arm, observed in pair["observations"].items():
                # Gold's frozen stop policy requires new knowledge to retry;
                # this no-candidate response supplies none. The upper attempt
                # limit is shared, actual attempt counts need not be equal.
                count = 2 if arm == "BASELINE" else 1
                assert len(observed["attempts"]) == count
                assert observed["provider_tokens"] == 18 * count
                identities.add(observed["result_identity"])
            assert pair["provider_token_difference"] == 18
            assert pair["economic_claim"] == "NOT_AUTHORIZED_BY_ACCEPTANCE"
        assert len(identities) == 4
        assert len(requests) == 6
        offset = 0
        for slot in allocations:
            first = requests[offset]
            assert "RAW BASELINE RETRY CONTEXT" not in json.dumps(first)
            if slot["arm"] == "BASELINE":
                assert "RAW BASELINE RETRY CONTEXT" in json.dumps(requests[offset + 1])
                offset += 2
            else:
                assert slot["receipt"]["result"]["result"]["terminal_decision"] == "STOP_NO_PROGRESS"
                offset += 1
        assert result["provider_token_differences"]["n"] == 2
        assert result["provider_token_differences"]["sample_stddev"] == "0"
