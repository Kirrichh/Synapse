"""External operator input fixture for the actual canonical Stage 15 runtime."""

from contextlib import contextmanager
import json

from acceptance.agents.coding_agents import use_model_agent
from acceptance.stage4.stage11._project_inputs import project_input_case
from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint


@contextmanager
def completed_run(root, monkeypatch, *, provider_command=None, oracle_result=None, usage_total=18,
                  input_profile=None):
    if input_profile is None:
        case = project_input_case(root)
    else:
        from acceptance.stage4.stage16._source_inputs import consumer_case
        case, _ = consumer_case(root, automatic_targets=True)
    if oracle_result is not None:
        from acceptance.stage4.stage11._oracle_process import create_oracle_process
        create_oracle_process(root / "harness", (oracle_result,))
    monkeypatch.setenv("SYNAPSE_ACCEPTANCE_PROVIDER_KEY", "controlled-provider-credential")
    options = {"usage_total": usage_total}
    if provider_command is not None:
        options["command"] = provider_command
    with provider_endpoint(**options) as (endpoint, requests):
        value = use_model_agent(json.loads(case.input_path.read_text()), root / "model-agent",
                                endpoint=endpoint, protocol=input_profile)
        case.input_path.write_text(json.dumps(value))
        code, pending = case.start()
        assert code == 3, pending
        assert not requests
        code, finished = case.approve(pending)
        assert code == 0, finished
        assert len(requests) == 1
        yield case, finished, requests
