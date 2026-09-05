"""Persisted input references require actual, unchanged evidence bytes."""

from acceptance.stage4.stage11._project_inputs import project_input_case


def test_changed_evidence_is_refused_before_replay_or_model_execution(tmp_path):
    case = project_input_case(tmp_path)
    code, pending = case.start()
    assert code == 3, pending
    evidence = next((tmp_path / "evidence").iterdir())
    evidence.write_bytes(b"substituted evidence")
    code, refused = case.approve(pending)
    assert code == 1 and refused["status"] == "GOLD_UNAVAILABLE", refused
    assert "evidence file changed" in refused["detail"]
    assert case.worker.calls == 0
