"""Stage 3A command oracle tests."""

from __future__ import annotations

import sys

from synapse.experiments.swebench.contract import BaselineTask
from synapse.experiments.swebench.oracle import CommandOracleRunner


def _task() -> BaselineTask:
    return BaselineTask("task", "instance", "statement", ("allowed.py",))


def test_command_oracle_resolves_on_returncode_zero(tmp_path):
    oracle = CommandOracleRunner((sys.executable, "-c", "print('ok')"))

    result = oracle.verify(tmp_path, _task())

    assert result.resolved is True
    assert result.returncode == 0
    assert "ok" in result.stdout


def test_command_oracle_unresolved_on_nonzero_returncode(tmp_path):
    oracle = CommandOracleRunner((sys.executable, "-c", "import sys; print('bad'); sys.exit(2)"))

    result = oracle.verify(tmp_path, _task())

    assert result.resolved is False
    assert result.returncode == 2
    assert "bad" in result.stdout
    assert result.diagnostics["infra_error"] is False


def test_command_oracle_timeout_is_infra_diagnostic(tmp_path):
    oracle = CommandOracleRunner((sys.executable, "-c", "import time; time.sleep(2)"), timeout_seconds=1)

    result = oracle.verify(tmp_path, _task())

    assert result.resolved is False
    assert result.returncode is None
    assert result.diagnostics["infra_error"] is True
    assert result.diagnostics["failure_reason"] == "oracle_timeout"
