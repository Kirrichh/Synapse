"""Typed contract for external coding-worker candidates.

Invariants:

* worker_status does not express task completion.
* PROPOSED_PATCH means "the worker left a diff candidate", not "the task is solved".
* patch != accepted change; worker_report != evidence; mini-run tests = diagnostics.
* the worker adapter never declares a task verified or complete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional


class ExternalWorkerStatus(str, Enum):
    PROPOSED_PATCH = "PROPOSED_PATCH"
    NO_PATCH = "NO_PATCH"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


class ExternalWorkerTokenStatus(str, Enum):
    PROVIDER_REPORTED = "PROVIDER_REPORTED"
    TOOL_REPORTED = "TOOL_REPORTED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class ExternalWorkerUsage:
    token_status: ExternalWorkerTokenStatus
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    thinking_tokens: Optional[int]
    total_tokens: Optional[int]
    thinking_included: bool
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_status": self.token_status.value,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens,
            "total_tokens": self.total_tokens,
            "thinking_included": self.thinking_included,
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class WorkerReport:
    summary: Optional[str] = None
    failure_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True)
class ExternalCodingWorkerResult:
    worker_status: ExternalWorkerStatus
    diff_text: Optional[str]
    touched_files: tuple[str, ...]
    usage: ExternalWorkerUsage
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    worker_report: WorkerReport = field(default_factory=WorkerReport)

    def __post_init__(self) -> None:
        object.__setattr__(self, "touched_files", tuple(self.touched_files))
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_status": self.worker_status.value,
            "diff_text": self.diff_text,
            "touched_files": list(self.touched_files),
            "usage": self.usage.to_dict(),
            "diagnostics": dict(self.diagnostics),
            "worker_report": self.worker_report.to_dict(),
        }
