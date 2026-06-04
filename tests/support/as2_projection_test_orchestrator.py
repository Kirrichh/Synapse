"""P0.6.24 test-only Projection Handoff orchestrator.

This module is intentionally under tests/support. It is allowed to call the
standalone AS2 projection function for integration tests while production bridge
and runtime skeleton modules remain projection-free by architectural guard.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from synapse.agent_snapshot import AgentSnapshot
from synapse.agent_snapshot_adapter import AdapterDerivationRecordSkeleton, project_validated_as2_inputs
from synapse.agent_snapshot_bridge import PreparedAS2Inputs
from synapse.canonical_service import stable_canonical_hash
from synapse.runtime.as2_runtime_wiring import WiringAgentQuarantineRequest, WiringSystemicDisableRequest

from tests.support.as2_control_plane_fake import HarnessReasonCode, InMemoryAS2GateController


class ProjectionHarnessOutcomeKind(str, Enum):
    """Stable discriminants for test-only projection handoff outcomes."""

    COMPLETED = "projection_completed"
    DENIED = "projection_denied"
    DUPLICATE = "projection_duplicate"
    SYSTEMIC_FAILURE = "projection_systemic_failure"
    AGENT_QUARANTINE = "projection_agent_quarantine"


class ProjectionInternalError(RuntimeError):
    """Test-only simulated core/projection invariant failure."""


class ProjectionInterruptedError(RuntimeError):
    """Test-only simulated interrupted projection failure."""


class ProjectionAgentScopedError(RuntimeError):
    """Test-only simulated agent-scoped projection failure."""

    def __init__(self, agent_id: str, message: str = "agent-scoped projection failure") -> None:
        super().__init__(message)
        self.agent_id = agent_id


@dataclass(frozen=True)
class ProjectionHarnessResult:
    """Test-only projection handoff result."""

    kind: ProjectionHarnessOutcomeKind
    correlation_id: str
    snapshot: AgentSnapshot | None = None
    derivation_record: AdapterDerivationRecordSkeleton | None = None
    snapshot_hash: str | None = None
    derivation_record_hash: str | None = None
    reason_code: str | None = None
    outcome: WiringSystemicDisableRequest | WiringAgentQuarantineRequest | None = None
    projection_call_count: int = 0


def prepared_inputs_hash(prepared_inputs: PreparedAS2Inputs) -> str:
    """Stable hash for PreparedAS2Inputs projection payload."""

    return stable_canonical_hash(prepared_inputs.to_validate_kwargs())


def derivation_record_hash(derivation_record: AdapterDerivationRecordSkeleton) -> str:
    """Stable hash for AdapterDerivationRecordSkeleton."""

    return stable_canonical_hash(asdict(derivation_record))


ProjectionFunction = Callable[..., tuple[AgentSnapshot, AdapterDerivationRecordSkeleton]]


class AS2ProjectionTestOrchestrator:
    """Test-only handoff layer implementing P0.6.23 projection rules.

    The orchestrator performs fresh Control Plane authorization immediately
    before projection, maintains an in-memory operational deduplication log, and
    maps projection failures into wiring outcomes for harness tests.
    """

    def __init__(self, controller: InMemoryAS2GateController) -> None:
        self._controller = controller
        self._dedup_log: dict[str, ProjectionHarnessResult] = {}
        self._input_hash_by_correlation_id: dict[str, str] = {}
        self.projection_call_count = 0

    def project(
        self,
        prepared_inputs: PreparedAS2Inputs,
        *,
        correlation_id: str,
        request_id: str = "test-request",
        projection_func: ProjectionFunction = project_validated_as2_inputs,
    ) -> ProjectionHarnessResult:
        """Authorize and execute a test-only projection handoff."""

        input_hash = prepared_inputs_hash(prepared_inputs)
        existing_input_hash = self._input_hash_by_correlation_id.get(correlation_id)
        if existing_input_hash is not None and existing_input_hash != input_hash:
            self._controller.record_projection_failed(
                correlation_id=correlation_id,
                request_id=request_id,
                reason_code=HarnessReasonCode.IDEMPOTENCY_CONFLICT.value,
            )
            return ProjectionHarnessResult(
                kind=ProjectionHarnessOutcomeKind.SYSTEMIC_FAILURE,
                correlation_id=correlation_id,
                reason_code=HarnessReasonCode.IDEMPOTENCY_CONFLICT.value,
                outcome=WiringSystemicDisableRequest(
                    correlation_id=correlation_id,
                    reason="same correlation_id was reused with changed PreparedAS2Inputs",
                    reason_code=HarnessReasonCode.IDEMPOTENCY_CONFLICT.value,
                    failure_context={"prepared_inputs_hash": input_hash},
                ),
                projection_call_count=self.projection_call_count,
            )
        dedup_key = self._dedup_key(correlation_id, input_hash)
        if dedup_key in self._dedup_log:
            cached = self._dedup_log[dedup_key]
            return ProjectionHarnessResult(
                kind=ProjectionHarnessOutcomeKind.DUPLICATE,
                correlation_id=correlation_id,
                snapshot=cached.snapshot,
                derivation_record=cached.derivation_record,
                snapshot_hash=cached.snapshot_hash,
                derivation_record_hash=cached.derivation_record_hash,
                reason_code="projection_duplicate",
                projection_call_count=self.projection_call_count,
            )

        self._controller.record_projection_requested(correlation_id=correlation_id, request_id=request_id)
        decision = self._controller.request_projection_approval(
            correlation_id=correlation_id,
            request_id=request_id,
        )
        if not decision.permitted:
            return ProjectionHarnessResult(
                kind=ProjectionHarnessOutcomeKind.DENIED,
                correlation_id=correlation_id,
                reason_code=decision.reason_code,
                projection_call_count=self.projection_call_count,
            )

        try:
            self.projection_call_count += 1
            snapshot, derivation = projection_func(**prepared_inputs.to_validate_kwargs())
        except ProjectionAgentScopedError as exc:
            self._controller.record_projection_failed(
                correlation_id=correlation_id,
                request_id=request_id,
                agent_id=exc.agent_id,
                reason_code=HarnessReasonCode.PROJECTION_AGENT_SCOPE_FAILURE.value,
            )
            return ProjectionHarnessResult(
                kind=ProjectionHarnessOutcomeKind.AGENT_QUARANTINE,
                correlation_id=correlation_id,
                reason_code=HarnessReasonCode.PROJECTION_AGENT_SCOPE_FAILURE.value,
                outcome=WiringAgentQuarantineRequest(
                    correlation_id=correlation_id,
                    agent_id=exc.agent_id,
                    reason=str(exc),
                    reason_code=HarnessReasonCode.PROJECTION_AGENT_SCOPE_FAILURE.value,
                ),
                projection_call_count=self.projection_call_count,
            )
        except ProjectionInterruptedError as exc:
            return self._systemic_projection_failure(
                exc,
                correlation_id=correlation_id,
                request_id=request_id,
                reason_code=HarnessReasonCode.SYSTEMIC_CORE_FAILURE_INTERRUPTED.value,
            )
        except ProjectionInternalError as exc:
            return self._systemic_projection_failure(
                exc,
                correlation_id=correlation_id,
                request_id=request_id,
                reason_code=HarnessReasonCode.SYSTEMIC_CORE_FAILURE.value,
            )

        snapshot_hash = snapshot.snapshot_hash()
        derivation_hash = derivation_record_hash(derivation)
        self._controller.record_projection_completed(
            correlation_id=correlation_id,
            request_id=request_id,
            agent_id=snapshot.agent_id,
            snapshot_hash=snapshot_hash,
            derivation_record_hash=derivation_hash,
        )
        result = ProjectionHarnessResult(
            kind=ProjectionHarnessOutcomeKind.COMPLETED,
            correlation_id=correlation_id,
            snapshot=snapshot,
            derivation_record=derivation,
            snapshot_hash=snapshot_hash,
            derivation_record_hash=derivation_hash,
            projection_call_count=self.projection_call_count,
        )
        self._input_hash_by_correlation_id[correlation_id] = input_hash
        self._dedup_log[dedup_key] = result
        return result

    def _systemic_projection_failure(
        self,
        exc: Exception,
        *,
        correlation_id: str,
        request_id: str,
        reason_code: str,
    ) -> ProjectionHarnessResult:
        self._controller.record_projection_failed(
            correlation_id=correlation_id,
            request_id=request_id,
            reason_code=reason_code,
        )
        return ProjectionHarnessResult(
            kind=ProjectionHarnessOutcomeKind.SYSTEMIC_FAILURE,
            correlation_id=correlation_id,
            reason_code=reason_code,
            outcome=WiringSystemicDisableRequest(
                correlation_id=correlation_id,
                reason=str(exc),
                reason_code=reason_code,
                failure_context={"error_type": exc.__class__.__name__},
            ),
            projection_call_count=self.projection_call_count,
        )

    def _dedup_key(self, correlation_id: str, input_hash: str) -> str:
        return stable_canonical_hash(
            {
                "correlation_id": correlation_id,
                "prepared_inputs_hash": input_hash,
                "type": "as2_projection_handoff_dedup_key",
            }
        )
