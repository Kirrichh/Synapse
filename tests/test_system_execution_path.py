from __future__ import annotations

import io
import os
from pathlib import Path
import subprocess
import sys

import pytest

import main as legacy_main
import synapse
import synapse.application as app
import synapse.cli as cli

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_cmd(args: list[str], cwd: Path | None = None, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        args,
        cwd=cwd or REPO_ROOT,
        input=stdin,
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )


def test_package_entry_point_is_thin_adapter_and_help_surfaces_match():
    module = REPO_ROOT / "synapse" / "__main__.py"
    text = module.read_text(encoding="utf-8")
    assert "from .cli import main" in text
    forbidden = ["argparse", "Interpreter", "Lexer", "Parser", "record_source", "execute_controlled_change"]
    assert not any(token in text for token in forbidden)

    package_help = run_cmd([sys.executable, "-m", "synapse", "--help"])
    cli_help = run_cmd([sys.executable, "-m", "synapse.cli", "--help"])
    assert package_help.returncode == cli_help.returncode == 0
    assert package_help.stdout == cli_help.stdout
    assert "{run,repl,replay,debug,metrics,change}" in package_help.stdout


def test_synapse_cli_run_delegates_file_execution(monkeypatch, capsys, tmp_path):
    source = tmp_path / "program.syn"
    source.write_text('print("delegated")\n', encoding="utf-8")
    captured = {}

    def fake_execute_file(request):
        captured["request"] = request
        return app.RuntimeExecutionResult(status="OK", exit_code=0, output="delegated")

    monkeypatch.setattr(cli, "execute_file", fake_execute_file)
    assert cli.main(["run", str(source)]) == 0
    assert captured["request"] == app.FileExecutionRequest(path=source, record=False, output_dir=None, layer="strict")
    assert capsys.readouterr().out.strip() == "delegated"


def test_synapse_cli_record_delegates_to_application(monkeypatch, capsys, tmp_path):
    source = tmp_path / "program.syn"
    source.write_text('print("record")\n', encoding="utf-8")
    output = tmp_path / "artifact"
    captured = {}

    def fake_execute_file(request):
        captured["request"] = request
        return app.RuntimeExecutionResult(
            status="RECORDED",
            exit_code=0,
            output="",
            artifact=app.ReplayArtifactSummary(
                recorded=str(output),
                program_hash="sha256:test",
                final_history_hash="hash",
                history_length=1,
            ),
        )

    monkeypatch.setattr(cli, "execute_file", fake_execute_file)
    assert cli.main(["run", str(source), "--record", "--output", str(output)]) == 0
    assert captured["request"] == app.FileExecutionRequest(path=source, record=True, output_dir=output, layer="strict")
    assert '"recorded"' in capsys.readouterr().out


def test_synapse_cli_source_and_repl_delegate(monkeypatch):
    captured = {}

    def fake_source(request):
        captured["source"] = request
        return app.RuntimeExecutionResult(status="OK", exit_code=0, output="source-output")

    def fake_repl(request, *, stdin, stdout, stderr):
        captured["repl"] = (request, stdin, stdout, stderr)
        return app.ReplResult(status="OK", exit_code=0)

    monkeypatch.setattr(cli, "execute_runtime_source", fake_source)
    monkeypatch.setattr(cli, "run_runtime_repl", fake_repl)
    assert cli.main(["run", "-c", 'print("x")']) == 0
    assert captured["source"] == app.SourceExecutionRequest('print("x")')
    assert cli.main(["repl"]) == 0
    assert isinstance(captured["repl"][0], app.ReplRequest)


def test_main_py_is_legacy_adapter_for_file_source_and_repl(monkeypatch, capsys, tmp_path):
    source = tmp_path / "legacy.syn"
    source.write_text('print("legacy")\n', encoding="utf-8")
    captured = {}

    def fake_execute_file(request):
        captured["file"] = request
        return app.RuntimeExecutionResult(status="OK", exit_code=0, output="legacy")

    def fake_execute_source(request):
        captured["source"] = request
        return app.RuntimeExecutionResult(status="OK", exit_code=0, output="inline")

    def fake_repl(request, *, stdin, stdout, stderr):
        captured["repl"] = request
        return app.ReplResult(status="OK", exit_code=0)

    monkeypatch.setattr(legacy_main, "execute_file", fake_execute_file)
    monkeypatch.setattr(legacy_main, "execute_runtime_source", fake_execute_source)
    monkeypatch.setattr(legacy_main, "run_runtime_repl", fake_repl)

    assert legacy_main.main([str(source)]) == 0
    assert captured["file"] == app.FileExecutionRequest(path=source)
    assert legacy_main.main(["-c", 'print("inline")']) == 0
    assert captured["source"] == app.SourceExecutionRequest('print("inline")')
    assert legacy_main.main(["--repl"]) == 0
    assert isinstance(captured["repl"], app.ReplRequest)
    assert "legacy" in capsys.readouterr().out


def test_static_adapters_do_not_own_execution_lifecycle():
    for relative in ["main.py", "synapse/cli.py", "synapse/__main__.py"]:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for forbidden in ["Interpreter(", "Lexer(", "Parser(", ".interpret(", "record_source("]:
            assert forbidden not in text, f"{relative} contains {forbidden}"


def test_application_layer_has_no_transport_argument_or_exit_ownership():
    text = (REPO_ROOT / "synapse" / "application.py").read_text(encoding="utf-8")
    for forbidden in ["argparse", "sys.argv", "sys.exit(", "raise SystemExit", "print("]:
        assert forbidden not in text


def test_file_execution_equivalence_between_entry_points(tmp_path):
    program = tmp_path / "program.syn"
    program.write_text('print("same-path")\n', encoding="utf-8")
    legacy = run_cmd([sys.executable, "main.py", str(program)])
    package = run_cmd([sys.executable, "-m", "synapse", "run", str(program)])
    module = run_cmd([sys.executable, "-m", "synapse.cli", "run", str(program)])
    assert legacy.returncode == package.returncode == module.returncode == 0
    assert legacy.stdout == package.stdout == module.stdout == "same-path\n"
    assert legacy.stderr == package.stderr == module.stderr == ""


def test_source_execution_equivalence_between_legacy_and_canonical_source_command():
    legacy = run_cmd([sys.executable, "main.py", "-c", 'print("inline")'])
    package = run_cmd([sys.executable, "-m", "synapse", "run", "-c", 'print("inline")'])
    assert legacy.returncode == package.returncode == 0
    assert legacy.stdout == package.stdout == "inline\n"


def test_error_and_legacy_transport_compatibility():
    assert run_cmd([sys.executable, "main.py"]).returncode == 1
    assert run_cmd([sys.executable, "main.py", "--help"]).stdout.strip() == "File not found: --help"
    assert run_cmd([sys.executable, "main.py", "missing.syn"]).stdout.strip() == "File not found: missing.syn"
    assert run_cmd([sys.executable, "main.py", "-c"], stdin='print("stdin")\n').stdout == "Synapse> stdin\n"
    assert run_cmd([sys.executable, "-m", "synapse", "run"]).returncode == 2
    bad_record = run_cmd([sys.executable, "-m", "synapse", "run", "README.md", "--record"])
    assert bad_record.returncode == 2
    assert "requires --output" in bad_record.stderr


def test_run_command_rejects_ambiguous_source_and_record_options(tmp_path):
    program = tmp_path / "program.syn"
    program.write_text('print("file")\n', encoding="utf-8")
    invalid_forms = [
        [sys.executable, "-m", "synapse", "run", str(program), "-c", 'print("source")'],
        [sys.executable, "-m", "synapse", "run", "-c", 'print("source")', "--record"],
        [sys.executable, "-m", "synapse", "run", "-c", 'print("source")', "--output", "out"],
        [sys.executable, "-m", "synapse", "run", "-c", 'print("source")', "--layer", "smoke"],
        [sys.executable, "-m", "synapse", "run"],
        [sys.executable, "-m", "synapse", "run", "--repl"],
        [sys.executable, "-m", "synapse", "run", str(program), "--output", "out"],
        [sys.executable, "-m", "synapse", "run", str(program), "--layer", "smoke"],
    ]
    for command in invalid_forms:
        completed = run_cmd(command)
        assert completed.returncode == 2, command


def test_package_repl_command_exists_and_run_repl_is_not_canonical():
    repl_help = run_cmd([sys.executable, "-m", "synapse", "repl", "--help"])
    run_repl = run_cmd([sys.executable, "-m", "synapse", "run", "--repl"])
    assert repl_help.returncode == 0
    assert run_repl.returncode == 2


def test_repl_uses_single_interpreter_and_survives_command_errors():
    stdin = io.StringIO('let x = 41\nprint(x)\nmissing_method()\nprint(x)\n')
    stdout = io.StringIO()
    stderr = io.StringIO()
    result = app.run_repl(app.ReplRequest(banner=False), stdin=stdin, stdout=stdout, stderr=stderr)
    assert result.exit_code == 0
    text = stdout.getvalue()
    assert "41" in text
    assert "Undefined function or agent" in text
    assert text.count("41") >= 2
    assert stderr.getvalue() == ""


def test_record_mode_application_uses_existing_artifact_format(tmp_path):
    program = tmp_path / "record.syn"
    program.write_text('print("record")\n', encoding="utf-8")
    output = tmp_path / "artifact"
    result = app.execute_file(app.FileExecutionRequest(path=program, record=True, output_dir=output))
    assert result.exit_code == 0
    assert result.artifact is not None
    assert (output / "manifest.json").exists()
    assert (output / "history.json").exists()


def test_public_synapse_run_remains_compatible():
    assert synapse.run('print("public")') == "public"
