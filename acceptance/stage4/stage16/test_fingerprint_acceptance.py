"""§33: same task name cannot hide different actual arm contracts."""

from pathlib import Path

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from acceptance.stage4.stage16._paired_case import paired_case, REPOSITORY
from acceptance.stage4.stage16.assessment import assess
from acceptance.stage4.stage16.harness import Experiment
from acceptance.stage4.stage16.protocol import canonical, preregister, read_source, source


def test_real_arms_with_different_scope_do_not_form_a_comparable_pair(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNAPSE_ACCEPTANCE_PROVIDER_KEY", "controlled-provider-credential")
    with provider_endpoint() as (endpoint, requests):
        original, _ = paired_case(tmp_path / "inputs", endpoint)
        design = original.payload()
        pairs = design["pairs"]
        ref = pairs[0]["inputs"]["BASELINE"]
        definition = read_source(ref)
        definition["task"]["allowed_scope"] = ["src"]
        Path(ref["path"]).write_bytes(canonical(definition))
        pairs[0]["inputs"]["BASELINE"] = source(ref["path"])
        protocol = preregister(experiment_id="scope-mismatch", pairs=pairs, repository=REPOSITORY,
            specification=design["specification"], seed=design["seed"])
        experiment = Experiment(tmp_path / "experiment", repository=REPOSITORY, protocol=protocol)
        assert all(slot["state"] == "FINISHED" for slot in experiment.run_all(approve_pending=True))
        assert len(requests) == 2
        pair, = assess(experiment)["pairs"]
        assert pair["status"] == "INCOMPLETE", pair
        assert {"code": "ARM_FINGERPRINT_MISMATCH", "axes": ["task"]} in pair["errors"]
        assert pair["provider_token_difference"] is None
        assert pair["activation_eligible"] is False
