"""Command execution and verification helpers for controlled changes."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import subprocess
import time

from .contract import CommandExpectation


@dataclass
class PhaseResult:
    name: str
    status: str
    command: list[str] | None
    returncode: int | None
    stdout: str
    stderr: str
    duration_ms: int
    diagnostics: list[str]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def _decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", "replace")


def phase_from_status(name: str, status: str, diagnostics: list[str]) -> PhaseResult:
    return PhaseResult(name=name, status=status, command=None, returncode=None, stdout="", stderr="", duration_ms=0, diagnostics=diagnostics)


def run_command(
    command: tuple[str, ...] | list[str],
    cwd: str | Path,
    phase_name: str,
    *,
    input_bytes: bytes | None = None,
    timeout_seconds: int = 60,
) -> PhaseResult:
    start = time.monotonic()
    try:
        completed = subprocess.run(
            list(command),
            cwd=str(cwd),
            input=input_bytes,
            capture_output=True,
            timeout=timeout_seconds,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        status = "PASS" if completed.returncode == 0 else "FAIL"
        diagnostics = [] if completed.returncode == 0 else [f"return code {completed.returncode} != 0"]
        return PhaseResult(
            name=phase_name,
            status=status,
            command=list(command),
            returncode=completed.returncode,
            stdout=_decode(completed.stdout),
            stderr=_decode(completed.stderr),
            duration_ms=duration_ms,
            diagnostics=diagnostics,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return PhaseResult(
            name=phase_name,
            status="FAIL",
            command=list(command),
            returncode=None,
            stdout=_decode(exc.stdout),
            stderr=_decode(exc.stderr),
            duration_ms=duration_ms,
            diagnostics=[f"timeout after {timeout_seconds}s"],
        )


def run_expected_command(
    command: tuple[str, ...] | list[str],
    cwd: str | Path,
    expectation: CommandExpectation,
    phase_name: str,
) -> PhaseResult:
    start = time.monotonic()
    try:
        completed = subprocess.run(
            list(command),
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=expectation.timeout_seconds,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        stdout = completed.stdout
        stderr = completed.stderr
        combined = stdout + stderr
        diagnostics: list[str] = []

        if expectation.expected_exit_codes is not None and completed.returncode not in expectation.expected_exit_codes:
            diagnostics.append(
                f"return code {completed.returncode} not in expected {list(expectation.expected_exit_codes)}"
            )
        if expectation.expected_nonzero_exit and completed.returncode == 0:
            diagnostics.append("expected non-zero exit code, got 0")
        for needle in expectation.combined_output_contains:
            if needle not in combined:
                diagnostics.append(f"combined output missing required text: {needle!r}")
        for needle in expectation.combined_output_not_contains:
            if needle in combined:
                diagnostics.append(f"combined output contained forbidden text: {needle!r}")

        return PhaseResult(
            name=phase_name,
            status="FAIL" if diagnostics else "PASS",
            command=list(command),
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            diagnostics=diagnostics,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return PhaseResult(
            name=phase_name,
            status="FAIL",
            command=list(command),
            returncode=None,
            stdout=_decode(exc.stdout),
            stderr=_decode(exc.stderr),
            duration_ms=duration_ms,
            diagnostics=[f"timeout after {expectation.timeout_seconds}s"],
        )
