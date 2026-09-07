"""§33: execute real arms, retain all results and preserve C2's scope."""

from pathlib import Path

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from acceptance.stage4.stage16._paired_case import paired_case, REPOSITORY
from acceptance.stage4.stage16.assessment import assess
from acceptance.stage4.stage16.harness import Experiment


def test_external_platform_executes_both_real_arms_and_reopens_without_repeat(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNAPSE_ACCEPTANCE_PROVIDER_KEY", "controlled-provider-credential")
    with provider_endpoint() as (endpoint, requests):
        protocol, cases = paired_case(tmp_path / "inputs", endpoint)
        experiment = Experiment(tmp_path / "experiment", repository=REPOSITORY, protocol=protocol)
        allocations = experiment.run_all(approve_pending=True)
        assert [slot["state"] for slot in allocations] == ["FINISHED", "FINISHED"], allocations
        assert len(requests) == 2
        result = assess(experiment)
        pair, = result["pairs"]
        assert pair["status"] == "DIAGNOSTIC_COMPLETE", result
        assert pair["provider_token_difference"] == 0
        assert {a: v["provider_tokens"] for a, v in pair["observations"].items()} == {"BASELINE": 18, "GOLD": 18}
        assert pair["c2"]["status"] == "INVALID_GOLD_WITH_CARRY"
        assert pair["c2"]["performance_claim_allowed"] is False
        assert pair["causal_isolation"] == "EXECUTION_POLICIES_DIFFER"
        assert pair["execution_policies_comparable"] is False
        assert pair["parameter_alignment"]["execution_policy"]["status"] == "MISMATCH"
        assert pair["mechanism"]["status"] == "MECHANISM_NOT_ACTIVATED"
        assert pair["economic_claim"] == "NOT_AUTHORIZED_BY_ACCEPTANCE"
        before = experiment.history()
        monkeypatch.delenv("SYNAPSE_ACCEPTANCE_PROVIDER_KEY")
        reopened = Experiment(experiment.root, repository=REPOSITORY)
        assert reopened.run_all() == allocations
        assert reopened.history() == before
        assert assess(reopened) == result
        assert len(requests) == 2
        baseline = next(slot for slot in allocations if slot["arm"] == "BASELINE")
        path = Path(baseline["receipt"]["result_ref"]["path"])
        raw = path.read_bytes()
        path.unlink()
        try:
            missing = assess(reopened)
            assert missing["pairs"][0]["status"] == "INCOMPLETE"
            assert missing["pairs"][0]["provider_token_difference"] is None
            assert missing["provider_token_differences"]["mean"] is None
            assert reopened.history() == before
        finally:
            path.write_bytes(raw)
        assert assess(reopened) == result
