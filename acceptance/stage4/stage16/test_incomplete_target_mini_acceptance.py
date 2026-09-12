"""A positive oracle alone cannot satisfy an unexecuted task effect."""
from acceptance.stage4.stage16._multi_target_case import execute_multi_target_case
from synapse.experiments.gold.runner.attempt_plan import previous_execution_feedback


def test_missing_target_stays_unresolved_even_when_the_oracle_passes(tmp_path, monkeypatch):
    completed, result, publication = execute_multi_target_case(tmp_path, monkeypatch, omit_second=True)
    assert result.oracle_resolved is True
    assert completed['outcome_status'] == 'UNRESOLVED'
    assert completed['status'] != 'GOLD_RESOLVED'
    assert publication['state'] == 'REJECTED'
    assert previous_execution_feedback(result, full_positive_required=True) == ()
