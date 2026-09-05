"""One heavy shard: terminal resume reconstructs from durable runtime inputs."""

from acceptance.stage4.stage11._project_inputs import project_input_case


def test_terminal_cli_resume_does_not_reload_seed_or_repeat_worker(tmp_path):
    case = project_input_case(tmp_path)
    code, pending = case.start()
    assert code == 3, pending
    code, first = case.approve(pending)
    assert code == 0 and first["status"] == "GOLD_STOPPED_NO_PROGRESS", first
    records = (case.run_root / "gold_attempts.jsonl").read_bytes()
    case.input_path.unlink()
    case.knowledge_path.unlink()
    for path in (tmp_path / "evidence").iterdir():
        path.unlink()
    code, resumed = case.cli("project", "resume", "--run-dir", case.run_root)
    assert code == 0 and resumed == first, resumed
    assert case.worker.calls == 1
    assert (case.run_root / "gold_attempts.jsonl").read_bytes() == records
