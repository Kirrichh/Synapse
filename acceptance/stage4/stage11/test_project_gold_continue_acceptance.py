"""One heavy shard: a new CLI process continues the actual project histories."""

import json

from synapse.experiments.gold.run_inputs import reopen_frozen_inputs
from synapse.experiments.gold.runner_composition import compose_frozen_gold_run
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.vocabulary import AttemptOutcome, TerminalDecisionKind
from acceptance.stage4.stage11._project_inputs import project_input_case
from acceptance.stage4.stage11._oracle_process import create_oracle_process


def test_new_cli_process_resumes_continue_with_seed_and_c1_feedback_from_disk(tmp_path):
    case = project_input_case(tmp_path, max_attempts=2, outcomes=("PATCH", "PATCH"))
    create_oracle_process(tmp_path / "harness", (False, True))
    code, pending = case.start()
    assert code == 3, pending
    code, granted = case.cli("approve", pending["request_path"], "--store", case.run_root / "approvals")
    assert code == 0, granted
    composition = compose_frozen_gold_run(reopen_frozen_inputs(case.run_root))
    controller = composition.controller
    # Stop precisely at an existing durable boundary, using the real owner's
    # phase methods. No candidate source, probe, replay or worker is injected.
    with composition.record_recovery.session() as session:
        prepared = controller._prepare_attempt(session=session, attempt_index=1, previous_context=None)
        controller._attempt_materializer.execute_prepared_attempt(
            session=session, attempt_index=1, prepared_inputs=prepared,
        )
        state = load_run_state(session.store)
        assert state.attempts[0].result.outcome is AttemptOutcome.UNRESOLVED
        decision = controller._record_tail_decision(session, state)
        assert decision.decision is TerminalDecisionKind.CONTINUE
    first_result = state.attempts[0].result.result_sha256
    assert case.worker.calls == 1
    case.input_path.unlink()
    case.knowledge_path.unlink()
    code, resumed = case.cli("project", "resume", "--run-dir", case.run_root)
    assert code == 0 and resumed["status"] == "GOLD_RESOLVED", resumed
    assert case.worker.calls == 2
    assert json.loads((tmp_path / "harness" / "oracle_state.json").read_text())["calls"] == 2
    recovered = load_run_state(compose_frozen_gold_run(reopen_frozen_inputs(case.run_root)).record_store)
    assert recovered.attempts[0].result.result_sha256 == first_result
    assert len(recovered.attempts) == 2
