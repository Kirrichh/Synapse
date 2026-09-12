"""Heavy shard: accepted operation graph -> real Mini/C1 -> each actual check."""
from acceptance.stage4.stage16._multi_target_case import execute_multi_target_case


def test_task_verification_steps_execute_in_order_and_resume_without_repeating(tmp_path, monkeypatch):
    completed, result, publication = execute_multi_target_case(
        tmp_path, monkeypatch, omit_second=False, verification_commands=True)
    assert completed['outcome_status'] == 'FULL'
    assert result.structured_outcome['payload']['status'] == 'FULL'
