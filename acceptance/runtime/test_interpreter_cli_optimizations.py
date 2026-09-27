"""Separate process acceptance for startup and bounded host execution."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def process(arguments, *, fuel="20000000"):
    return subprocess.run([sys.executable, "-B", *arguments], cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT), "SYNAPSE_FUEL_LIMIT": fuel},
        capture_output=True, text=True, timeout=15)


def test_plain_cli_executes_with_optional_subsystems_unavailable():
    result = process(["-c", '''
import sys
class UnavailableOptionalSubsystems:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("synapse.llm", "synapse.change", "synapse.experiments")):
            raise AssertionError("arithmetic requested an optional subsystem: " + fullname)
sys.meta_path.insert(0, UnavailableOptionalSubsystems())
from synapse.cli import main
raise SystemExit(main(["run", "-c", "print(6 * 7)"]))
'''])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "42"


@pytest.mark.parametrize("source", ["while true {}", "fn main() { while true {} }", "for i in range(100) {}"])
def test_loop_budget_reaches_canonical_cli_error_result(source):
    result = process(["-m", "synapse", "run", "-c", source], fuel="3")
    assert result.returncode == 1
    assert "Fuel limit exceeded: 3 loop iterations" in result.stderr


def test_explicit_backend_does_not_construct_or_import_default_provider():
    result = process(["-c", '''
import sys
from synapse import Interpreter, run
class NoDefaultProvider:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("synapse.llm"):
            raise AssertionError("default provider must remain unused")
sys.meta_path.insert(0, NoDefaultProvider())
interpreter = Interpreter()
backend = object()
interpreter.llm_backend = backend
assert interpreter.llm_backend is backend
assert run("print(42)", interpreter) == "42"
'''])
    assert result.returncode == 0, result.stderr
