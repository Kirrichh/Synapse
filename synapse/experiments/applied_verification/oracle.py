"""Oracle abstractions for Stage 3A baseline measurement."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import time
from typing import Protocol

from .contract import BaselineTask, OracleResult


class OracleRunner(Protocol):
    def verify(self, worktree_path: Path, task: BaselineTask) -> OracleResult:
        ...


@dataclass(frozen=True)
class CommandOracleRunner:
    command: tuple[str, ...]
    timeout_seconds: int = 60

    def verify(self, worktree_path: Path, task: BaselineTask) -> OracleResult:
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                list(self.command),
                cwd=str(worktree_path),
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            return OracleResult(
                resolved=False,
                returncode=None,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                duration_seconds=time.perf_counter() - started,
                diagnostics={
                    "infra_error": True,
                    "failure_reason": "oracle_timeout",
                    "task_id": task.task_id,
                },
            )
        except OSError as exc:
            return OracleResult(
                resolved=False,
                returncode=None,
                stdout="",
                stderr="",
                duration_seconds=time.perf_counter() - started,
                diagnostics={
                    "infra_error": True,
                    "failure_reason": "oracle_command_error",
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                    "task_id": task.task_id,
                },
            )
        return OracleResult(
            resolved=completed.returncode == 0,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            duration_seconds=time.perf_counter() - started,
            diagnostics={
                "infra_error": False,
                "task_id": task.task_id,
            },
        )
