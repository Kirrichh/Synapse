"""A task spanning two actual source files through the canonical Gold path."""
from acceptance.stage4.stage16._multi_target_case import execute_multi_target_case


def test_every_declared_target_is_modified_and_independently_verified(tmp_path, monkeypatch):
    completed, result, publication = execute_multi_target_case(tmp_path, monkeypatch, omit_second=False)
    assert completed['status'] == 'GOLD_RESOLVED'
    assert completed['outcome_status'] == 'FULL'
    assert result.oracle_resolved is True
    assert publication['state'] == 'COMMITTED'
