import subprocess
import sys

import pytest

from synapse import run
from synapse.interpreter import RuntimeError as SynapseRuntimeError


SOURCE = '''agent Worker {
    model "mock"
    fn existing_method() {
        return "ok"
    }
}

let worker = Worker()
worker.method_that_does_not_exist()
'''


def assert_missing_method_contract(combined_output: str) -> None:
    assert "Worker" in combined_output
    assert "method_that_does_not_exist" in combined_output
    assert "has no member or method" in combined_output


def test_missing_agent_method_raises_explicit_runtime_error() -> None:
    with pytest.raises(SynapseRuntimeError) as exc_info:
        run(SOURCE)

    assert_missing_method_contract(str(exc_info.value))


def test_missing_agent_method_file_mode_cli_exits_nonzero() -> None:
    result = subprocess.run(
        [sys.executable, "main.py", "personal_slice/reproduction/missing_method.syn"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert_missing_method_contract(result.stdout + result.stderr)


def test_missing_agent_method_c_mode_cli_exits_nonzero() -> None:
    result = subprocess.run(
        [sys.executable, "main.py", "-c", SOURCE],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert_missing_method_contract(result.stdout + result.stderr)
