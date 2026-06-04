"""AS2 audit sink skeleton (Alpha3g P0.6.25).

This module defines the production-facing audit sink contract used by the
AS2GateController skeleton. It is intentionally storage-free: no file, network,
database, CAS, or other persistence I/O is performed here.

Timestamp guardrail: audit timestamps, when supplied, must be explicit inputs
from the caller/request context. They are intentionally excluded from
``record_hash()`` until production audit timestamp semantics are approved, so
ambient system time cannot make hash-chain assertions flaky or nondeterministic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from synapse.canonical_service import stable_canonical_hash


@dataclass(frozen=True)
class AS2AuditEvent:
    """Structured AS2 audit event emitted by control-plane skeletons.

    ``timestamp`` is accepted only as an explicit caller-provided value. It is
    not generated in this module and is excluded from the hash payload pending a
    future production audit decision.
    """

    event_type: str
    correlation_id: str
    reason_code: str
    from_state: str
    to_state: str
    previous_state_hash: str | None = None
    request_id: str | None = None
    agent_id: str | None = None
    timestamp: str | None = None
    snapshot_hash: str | None = None
    derivation_record_hash: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_hash_payload(self) -> dict[str, Any]:
        """Return the deterministic payload used for audit-chain hashing."""

        return {
            "agent_id": self.agent_id,
            "correlation_id": self.correlation_id,
            "detail": dict(self.detail),
            "event_type": self.event_type,
            "from_state": self.from_state,
            "previous_state_hash": self.previous_state_hash,
            "reason_code": self.reason_code,
            "request_id": self.request_id,
            "snapshot_hash": self.snapshot_hash,
            "derivation_record_hash": self.derivation_record_hash,
            "to_state": self.to_state,
        }

    def record_hash(self) -> str:
        """Return a stable canonical hash of this event's deterministic fields."""

        return stable_canonical_hash(self.to_hash_payload())


class AS2AuditSink(Protocol):
    """Protocol for receiving AS2 audit events.

    Implementations are injected into the AS2GateController skeleton. Production
    storage, CAS, and external logging integrations remain locked outside
    P0.6.25.
    """

    def record(self, event: AS2AuditEvent) -> None:
        """Record an audit event."""


class NoOpAuditSink:
    """Default audit sink for skeleton phase; intentionally performs no I/O."""

    def record(self, event: AS2AuditEvent) -> None:
        """Accept and ignore ``event`` without side effects."""

        return None
