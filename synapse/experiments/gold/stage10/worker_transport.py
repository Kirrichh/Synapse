"""Neutral port vocabulary for exact Stage 10 worker transport."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import re
from types import MappingProxyType
from typing import Any, Mapping

from synapse.worker.input_contract import WorkerTaskInput, LocalInformationInput


WORKER_INVOCATION_SCHEMA_V1 = "synapse.stage4.gold.stage10.worker-invocation/v1"
WORKER_INVOCATION_SCHEMA_V2 = "synapse.stage4.gold.stage10.worker-invocation/v2"

class WorkerDeliveryStatus(str, Enum):
    PROCESS_STARTED = "PROCESS_STARTED"
    NOT_DISPATCHED = "NOT_DISPATCHED"


class WorkerCandidateStatus(str, Enum):
    PROPOSED_PATCH = "PROPOSED_PATCH"
    NO_PATCH = "NO_PATCH"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


class WorkerTokenStatus(str, Enum):
    PROVIDER_REPORTED = "PROVIDER_REPORTED"
    TOOL_REPORTED = "TOOL_REPORTED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class WorkerInvocation:
    invocation_id: str
    attempt_id: str
    context_id: str
    payload_text: str
    payload_sha256: str
    payload_byte_length: int
    envelope_sha256: str
    allowed_scope: tuple[str, ...]
    capabilities: tuple[str, ...]
    schema_version: str = WORKER_INVOCATION_SCHEMA_V1
    information_text: str | None = None
    information_sha256: str | None = None
    information_byte_length: int | None = None

    def __post_init__(self) -> None:
        if type(self.invocation_id) is not str or re.fullmatch(r"inv_[0-9a-f]{64}", self.invocation_id) is None:
            raise ValueError("invocation_id is malformed")
        if type(self.attempt_id) is not str or not self.attempt_id or len(self.attempt_id) > 128:
            raise ValueError("attempt_id is malformed")
        if type(self.context_id) is not str or re.fullmatch(r"ctx_[0-9a-f]{64}", self.context_id) is None:
            raise ValueError("context_id is malformed")
        if type(self.payload_text) is not str or not self.payload_text:
            raise ValueError("payload_text must be a non-empty exact string")
        encoded = self.payload_text.encode("utf-8")
        if type(self.payload_byte_length) is not int or self.payload_byte_length != len(encoded):
            raise ValueError("payload byte length does not match exact text")
        if type(self.payload_sha256) is not str or self.payload_sha256 != hashlib.sha256(encoded).hexdigest():
            raise ValueError("payload hash does not match exact text")
        if type(self.envelope_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", self.envelope_sha256) is None:
            raise ValueError("envelope_sha256 is malformed")
        for name, values in (("allowed_scope", self.allowed_scope), ("capabilities", self.capabilities)):
            if type(values) is not tuple or not values or any(type(item) is not str or not item for item in values):
                raise ValueError(f"{name} must be a non-empty tuple of strings")
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be sorted and unique")
        if self.schema_version == WORKER_INVOCATION_SCHEMA_V1:
            if any(value is not None for value in (self.information_text, self.information_sha256, self.information_byte_length)):
                raise ValueError("legacy invocation cannot claim a separate information input")
        elif self.schema_version == WORKER_INVOCATION_SCHEMA_V2:
            task = WorkerTaskInput(encoded).to_dict()
            if (task["allowed_scope"] != list(self.allowed_scope)
                    or task["capabilities"] != list(self.capabilities)):
                raise ValueError("task scope or capabilities differ from the invocation")
            if type(self.information_text) is not str:
                raise ValueError("separate information input must be exact text")
            information = LocalInformationInput(self.information_text.encode("utf-8"))
            if (type(self.information_byte_length) is not int
                    or self.information_byte_length != len(information.canonical_bytes)
                    or self.information_sha256 != information.sha256):
                raise ValueError("separate information input differs from its binding")
        else:
            raise ValueError("worker invocation schema is unknown")


@dataclass(frozen=True)
class WorkerDeliveryEvidence:
    invocation_id: str
    context_id: str
    payload_sha256: str
    payload_byte_length: int
    envelope_sha256: str
    status: WorkerDeliveryStatus
    transport_name: str
    input_schema_version: str = WORKER_INVOCATION_SCHEMA_V1
    information_sha256: str | None = None
    information_byte_length: int | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not WorkerDeliveryStatus:
            raise ValueError("delivery status must be exact")
        if type(self.transport_name) is not str or not self.transport_name or len(self.transport_name) > 128:
            raise ValueError("transport_name is malformed")
        if type(self.invocation_id) is not str or re.fullmatch(r"inv_[0-9a-f]{64}", self.invocation_id) is None:
            raise ValueError("delivery invocation_id is malformed")
        if type(self.context_id) is not str or re.fullmatch(r"ctx_[0-9a-f]{64}", self.context_id) is None:
            raise ValueError("delivery context_id is malformed")
        if type(self.payload_byte_length) is not int or self.payload_byte_length <= 0:
            raise ValueError("payload_byte_length is invalid")
        for digest in (self.payload_sha256, self.envelope_sha256):
            if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError("delivery evidence contains a malformed digest")
        if self.input_schema_version == WORKER_INVOCATION_SCHEMA_V1:
            if self.information_sha256 is not None or self.information_byte_length is not None:
                raise ValueError("legacy evidence cannot claim separate information")
        elif self.input_schema_version == WORKER_INVOCATION_SCHEMA_V2:
            if (type(self.information_sha256) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", self.information_sha256) is None
                    or type(self.information_byte_length) is not int or self.information_byte_length <= 0):
                raise ValueError("delivery evidence lacks its separate information binding")
        else:
            raise ValueError("delivery input schema is unknown")


@dataclass(frozen=True)
class WorkerCandidateUsage:
    token_status: WorkerTokenStatus
    input_tokens: int | None
    output_tokens: int | None
    thinking_tokens: int | None
    total_tokens: int | None
    thinking_included: bool
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.token_status) is not WorkerTokenStatus or type(self.thinking_included) is not bool:
            raise TypeError("worker usage fields must be exact")
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))


@dataclass(frozen=True)
class WorkerCandidateReport:
    summary: str | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class WorkerCandidateResult:
    status: WorkerCandidateStatus
    diff_text: str | None
    touched_files: tuple[str, ...]
    usage: WorkerCandidateUsage
    diagnostics: Mapping[str, Any]
    report: WorkerCandidateReport
    delivery_evidence: WorkerDeliveryEvidence

    def __post_init__(self) -> None:
        if type(self.status) is not WorkerCandidateStatus:
            raise TypeError("worker candidate status must be exact")
        if type(self.touched_files) is not tuple or any(type(item) is not str for item in self.touched_files):
            raise TypeError("worker touched_files must be a tuple of strings")
        if type(self.usage) is not WorkerCandidateUsage or type(self.report) is not WorkerCandidateReport:
            raise TypeError("worker candidate nested records must be exact")
        if type(self.delivery_evidence) is not WorkerDeliveryEvidence:
            raise TypeError("worker candidate requires delivery evidence")
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))
