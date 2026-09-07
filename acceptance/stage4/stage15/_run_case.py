"""External operator input fixture for the actual canonical Stage 15 runtime."""

from contextlib import contextmanager
import json
from pathlib import Path
import sys

from acceptance.stage4.stage11._project_inputs import project_input_case
from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from synapse.experiments.gold.run_inputs import EXPERIMENT_INPUT_SCHEMA_V2
from synapse.worker.provider_transport import MINI_ACCOUNTING_PROFILE


@contextmanager
def completed_run(root, monkeypatch, *, provider_command=None, oracle_result=None, usage_total=18):
    case = project_input_case(root)
    if oracle_result is not None:
        from acceptance.stage4.stage11._oracle_process import create_oracle_process
        create_oracle_process(root / "harness", (oracle_result,))
    monkeypatch.setenv("SYNAPSE_ACCEPTANCE_PROVIDER_KEY", "controlled-provider-credential")
    options = {"usage_total": usage_total}
    if provider_command is not None:
        options["command"] = provider_command
    with provider_endpoint(**options) as (endpoint, requests):
        value = json.loads(case.input_path.read_text())
        value["schema_version"] = EXPERIMENT_INPUT_SCHEMA_V2
        value["config"]["model"] = "gpt-4o-mini"
        value["worker"] = {"provider": "mini", "command": [str(Path(sys.executable).parent / "mini")],
            "model": "gpt-4o-mini", "timeout_seconds": 60, "max_steps": 3, "cost_limit": "1",
            "accounting": {"profile": MINI_ACCOUNTING_PROFILE, "endpoint": endpoint,
                           "credential_env": "SYNAPSE_ACCEPTANCE_PROVIDER_KEY"}}
        case.input_path.write_text(json.dumps(value))
        code, pending = case.start()
        assert code == 3, pending
        assert not requests
        code, finished = case.approve(pending)
        assert code == 0, finished
        assert len(requests) == 1
        yield case, finished, requests
