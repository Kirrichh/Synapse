"""AS2 runtime wiring skeleton (Alpha3g P0.6.19 hardening).

This module is the first runtime-owned AS2 preparation path. It remains a
hardening-stage skeleton:

- it proceeds only when the supplied gate state is ``ENABLED_FOR_TEST``;
- it consumes an already assembled Host Pre-Stage payload;
- it delegates Host Pre-Stage preparation to the existing bridge boundary;
- it validates the resulting ``PreparedAS2Inputs`` through the standalone AS2
  adapter validation API;
- it returns immutable typed outcomes with stable reason codes and
  correlation identifiers;
- in P0.6.27 it may optionally delegate a validated ``WiringSuccess`` to an
  injected projection handoff dependency when ``projection_handoff_enabled`` is
  explicitly set to ``True``.

It intentionally does not implement production Host providers, does not persist
or mutate gate state, does not call projection directly, does not construct
AgentSnapshot, does not perform CAS/storage I/O, and does not import legacy
runtime layers. The only approved production projection caller remains
``synapse/runtime/as2_projection_handoff.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from synapse.agent_snapshot_adapter import (
    AS2AdapterError,
    AdapterIdentityContextIncompleteError,
    AdapterIdentityContextMissingError,
    validate_as2_inputs,
)
from synapse.agent_snapshot_bridge import (
    AS2BridgeInputError,
    AS2HostPreStageBridgeDisabledError,
    HostPreStageIOError,
    PreparedAS2Inputs,
    prepare_as2_inputs_from_host_prestage,
)


class AS2WiringGateState(str, Enum):
    """Skeleton gate states copied from the P0.6.17 RFC.

    ``ENABLED`` is intentionally absent. Runtime wiring is only allowed under
    ``ENABLED_FOR_TEST`` until a later production-readiness vote.
    """

    DISABLED_BY_DEFAULT = "disabled_by_default"
    ENABLED_FOR_TEST = "enabled_for_test"
    DISABLED_AGENT_QUARANTINE = "disabled_agent_quarantine"
    DISABLED_SYSTEMIC = "disabled_systemic"
    DISABLED_OPERATOR_OVERRIDE = "disabled_operator_override"


class AS2WiringOutcomeKind(str, Enum):
    """Stable discriminants for immutable wiring outcome dataclasses."""

    SUCCESS = "success"
    GATE_CLOSED = "gate_closed"
    BRIDGE_DISABLED = "bridge_disabled"
    AGENT_QUARANTINE_REQUESTED = "agent_quarantine_requested"
    SYSTEMIC_DISABLE_REQUESTED = "systemic_disable_requested"
    PROJECTION_COMPLETED = "projection_completed"
    PROJECTION_SKIPPED = "projection_skipped"


class AS2WiringReasonCode(str, Enum):
    """P0.6.19 strict reason-code taxonomy for wiring outcomes."""

    GATE_DISABLED_BY_DEFAULT = "gate_disabled_by_default"
    GATE_DISABLED_SYSTEMIC = "gate_disabled_systemic"
    GATE_DISABLED_OPERATOR = "gate_disabled_operator"
    GATE_DISABLED_QUARANTINE = "gate_disabled_quarantine"

    BRIDGE_SAFETY_DISABLED = "bridge_safety_disabled"

    VALIDATION_FAILED_AGENT_SCOPE = "validation_failed_agent_scope"
    MISSING_IDENTITY_CONTEXT = "missing_identity_context"
    MALFORMED_PAYLOAD_AGENT = "malformed_payload_agent"
    INVALID_CAPABILITY_GRANT = "invalid_capability_grant"

    VALIDATION_FAILED_SYSTEMIC = "validation_failed_systemic"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PAYLOAD_CLASSIFICATION_FAILED = "payload_classification_failed"
    UNEXPECTED_PREPARATION_FAILURE = "unexpected_preparation_failure"

    PROJECTION_COMPLETED = "projection_completed"
    PROJECTION_DENIED = "projection_denied"
    PROJECTION_DUPLICATE = "projection_duplicate"
    PROJECTION_HANDOFF_NOT_CONFIGURED = "projection_handoff_not_configured"


def _new_correlation_id() -> str:
    """Generate a root correlation identifier for skeleton-mode calls."""

    return str(uuid4())


@dataclass(frozen=True)
class AS2WiringOutcome:
    """Base immutable runtime wiring skeleton outcome."""

    correlation_id: str = field(default_factory=_new_correlation_id)

    @property
    def kind(self) -> AS2WiringOutcomeKind:
        raise NotImplementedError("AS2WiringOutcome subclasses must expose kind")


@dataclass(frozen=True)
class WiringSuccess(AS2WiringOutcome):
    """AS2 Host Pre-Stage payload prepared and validated successfully."""

    prepared_inputs: PreparedAS2Inputs | None = None

    def __post_init__(self) -> None:
        if self.prepared_inputs is None:
            raise ValueError("WiringSuccess requires PreparedAS2Inputs")

    @property
    def kind(self) -> AS2WiringOutcomeKind:
        return AS2WiringOutcomeKind.SUCCESS


@dataclass(frozen=True)
class WiringGateClosed(AS2WiringOutcome):
    """Skeleton gate did not permit AS2 runtime wiring."""

    current_state: AS2WiringGateState = AS2WiringGateState.DISABLED_BY_DEFAULT
    reason: str = "AS2 runtime wiring gate is not ENABLED_FOR_TEST"
    reason_code: AS2WiringReasonCode = AS2WiringReasonCode.GATE_DISABLED_BY_DEFAULT

    @property
    def kind(self) -> AS2WiringOutcomeKind:
        return AS2WiringOutcomeKind.GATE_CLOSED


@dataclass(frozen=True)
class WiringBridgeDisabled(AS2WiringOutcome):
    """Bridge preparation layer is disabled by its local safety flag.

    This is distinct from ``WiringGateClosed``: the skeleton gate may be open,
    but the bridge-level preparation boundary remains intentionally locked.
    """

    reason: str = "AS2_HOST_PRESTAGE_BRIDGE_ENABLED is False"
    reason_code: AS2WiringReasonCode = AS2WiringReasonCode.BRIDGE_SAFETY_DISABLED

    @property
    def kind(self) -> AS2WiringOutcomeKind:
        return AS2WiringOutcomeKind.BRIDGE_DISABLED


@dataclass(frozen=True)
class WiringAgentQuarantineRequest(AS2WiringOutcome):
    """Caller should quarantine only the affected agent/payload."""

    agent_id: str | None = None
    reason: str = "AS2 Host Pre-Stage payload rejected"
    reason_code: AS2WiringReasonCode = AS2WiringReasonCode.MALFORMED_PAYLOAD_AGENT

    @property
    def kind(self) -> AS2WiringOutcomeKind:
        return AS2WiringOutcomeKind.AGENT_QUARANTINE_REQUESTED


@dataclass(frozen=True)
class WiringSystemicDisableRequest(AS2WiringOutcome):
    """Caller/control plane should request global AS2 wiring disable."""

    reason: str = "AS2 runtime wiring encountered a systemic failure"
    reason_code: AS2WiringReasonCode | str = AS2WiringReasonCode.UNEXPECTED_PREPARATION_FAILURE
    failure_context: Mapping[str, Any] | None = None

    @property
    def kind(self) -> AS2WiringOutcomeKind:
        return AS2WiringOutcomeKind.SYSTEMIC_DISABLE_REQUESTED


@dataclass(frozen=True)
class WiringProjectionCompleted(AS2WiringOutcome):
    """Projection handoff completed and returned stable artifact hashes.

    The runtime wiring layer receives artifact ownership metadata from the
    handoff layer but does not import or retain AgentSnapshot objects.
    """

    snapshot_hash: str = ""
    derivation_record_hash: str | None = None
    reason_code: AS2WiringReasonCode = AS2WiringReasonCode.PROJECTION_COMPLETED

    def __post_init__(self) -> None:
        if not self.snapshot_hash:
            raise ValueError("WiringProjectionCompleted requires snapshot_hash")

    @property
    def kind(self) -> AS2WiringOutcomeKind:
        return AS2WiringOutcomeKind.PROJECTION_COMPLETED


@dataclass(frozen=True)
class WiringProjectionSkipped(AS2WiringOutcome):
    """Projection handoff was skipped without systemic escalation.

    ``DUPLICATE`` maps here as a hash-only idempotency outcome: callers must
    not expect an AgentSnapshot object on this path. CAS-backed lookup by
    ``snapshot_hash`` is future scope and remains locked in P0.6.27.
    """

    reason: str = "Projection handoff skipped"
    reason_code: AS2WiringReasonCode = AS2WiringReasonCode.PROJECTION_DENIED
    snapshot_hash: str | None = None
    derivation_record_hash: str | None = None

    @property
    def kind(self) -> AS2WiringOutcomeKind:
        return AS2WiringOutcomeKind.PROJECTION_SKIPPED


_GATE_CLOSED_REASON_CODES: Mapping[AS2WiringGateState, AS2WiringReasonCode] = {
    AS2WiringGateState.DISABLED_BY_DEFAULT: AS2WiringReasonCode.GATE_DISABLED_BY_DEFAULT,
    AS2WiringGateState.DISABLED_AGENT_QUARANTINE: AS2WiringReasonCode.GATE_DISABLED_QUARANTINE,
    AS2WiringGateState.DISABLED_SYSTEMIC: AS2WiringReasonCode.GATE_DISABLED_SYSTEMIC,
    AS2WiringGateState.DISABLED_OPERATOR_OVERRIDE: AS2WiringReasonCode.GATE_DISABLED_OPERATOR,
}


@dataclass(frozen=True)
class AS2WiringGateEvaluator:
    """Skeleton-level gate evaluator, not a production AS2GateController."""

    gate_state: AS2WiringGateState

    def is_open_for_test(self) -> bool:
        return self.gate_state is AS2WiringGateState.ENABLED_FOR_TEST

    def evaluate(self, *, correlation_id: str | None = None) -> WiringGateClosed | None:
        """Return ``None`` when wiring may proceed, otherwise a gate outcome."""

        if self.is_open_for_test():
            return None
        return WiringGateClosed(
            correlation_id=correlation_id or _new_correlation_id(),
            current_state=self.gate_state,
            reason_code=_GATE_CLOSED_REASON_CODES[self.gate_state],
        )


def process_host_prestage(
    host_prestage_payload: Mapping[str, Any],
    *,
    gate_state: AS2WiringGateState,
    correlation_id: str | None = None,
    projection_handoff_enabled: bool = False,
    projection_handoff: Any | None = None,
) -> AS2WiringOutcome:
    """Run the P0.6.19-hardened AS2 runtime wiring skeleton.

    ``correlation_id`` is preferably supplied by the Host/Pipeline. When it is
    absent, the skeleton creates a root correlation identifier for hardening and
    fixture use. The function is intentionally side-effect free with respect to
    gate state: it does not persist, mutate, or transition the AS2 runtime gate.
    It returns typed outcomes that a control-plane component may consume. When
    ``projection_handoff_enabled`` is ``False`` (the default), P0.6.18-P0.6.26
    behavior is preserved and successful validation returns ``WiringSuccess``.
    When explicitly enabled, the validated inputs are delegated to the injected
    handoff dependency. Runtime wiring never calls projection directly.
    """

    cid = correlation_id or _new_correlation_id()
    gate_closed = AS2WiringGateEvaluator(gate_state).evaluate(correlation_id=cid)
    if gate_closed is not None:
        return gate_closed

    try:
        prepared = prepare_as2_inputs_from_host_prestage(host_prestage_payload)
        validate_as2_inputs(**prepared.to_validate_kwargs())
    except AS2HostPreStageBridgeDisabledError as exc:
        return WiringBridgeDisabled(correlation_id=cid, reason=str(exc))
    except HostPreStageIOError as exc:
        return _systemic_disable_outcome(
            exc,
            correlation_id=cid,
            reason_code=AS2WiringReasonCode.PROVIDER_UNAVAILABLE,
        )
    except (AdapterIdentityContextMissingError, AdapterIdentityContextIncompleteError) as exc:
        return _agent_quarantine_outcome(
            exc,
            host_prestage_payload,
            correlation_id=cid,
            reason_code=AS2WiringReasonCode.MISSING_IDENTITY_CONTEXT,
        )
    except AS2BridgeInputError as exc:
        return _agent_quarantine_outcome(
            exc,
            host_prestage_payload,
            correlation_id=cid,
            reason_code=_bridge_input_reason_code(exc),
        )
    except AS2AdapterError as exc:
        return _agent_quarantine_outcome(
            exc,
            host_prestage_payload,
            correlation_id=cid,
            reason_code=AS2WiringReasonCode.VALIDATION_FAILED_AGENT_SCOPE,
        )
    except Exception as exc:  # pragma: no cover - defensive systemic boundary
        return _systemic_disable_outcome(
            exc,
            correlation_id=cid,
            reason_code=AS2WiringReasonCode.UNEXPECTED_PREPARATION_FAILURE,
        )

    success = WiringSuccess(correlation_id=cid, prepared_inputs=prepared)
    if not projection_handoff_enabled:
        return success
    if projection_handoff is None:
        return WiringProjectionSkipped(
            correlation_id=cid,
            reason="Projection handoff was enabled but no handoff dependency was provided",
            reason_code=AS2WiringReasonCode.PROJECTION_HANDOFF_NOT_CONFIGURED,
        )
    return _map_projection_handoff_result(
        projection_handoff.execute_projection(
            prepared,
            correlation_id=cid,
        )
    )


def _map_projection_handoff_result(result: Any) -> AS2WiringOutcome:
    """Map an injected handoff result to a runtime-wiring outcome.

    The import is intentionally lazy to avoid a module import cycle:
    ``as2_projection_handoff`` imports wiring outcome classes, while wiring only
    needs result discriminants when the optional P0.6.27 handoff path is used.
    """

    from synapse.runtime.as2_projection_handoff import AS2ProjectionHandoffResultKind

    if result.kind is AS2ProjectionHandoffResultKind.COMPLETED:
        return WiringProjectionCompleted(
            correlation_id=result.correlation_id,
            snapshot_hash=result.snapshot_hash or "",
            derivation_record_hash=result.derivation_record_hash,
        )
    if result.kind is AS2ProjectionHandoffResultKind.DENIED:
        return WiringProjectionSkipped(
            correlation_id=result.correlation_id,
            reason="Projection handoff denied by control plane",
            reason_code=AS2WiringReasonCode.PROJECTION_DENIED,
        )
    if result.kind is AS2ProjectionHandoffResultKind.DUPLICATE:
        return WiringProjectionSkipped(
            correlation_id=result.correlation_id,
            reason="Projection handoff duplicate; hash-only idempotency result",
            reason_code=AS2WiringReasonCode.PROJECTION_DUPLICATE,
            snapshot_hash=result.snapshot_hash,
            derivation_record_hash=result.derivation_record_hash,
        )
    if result.kind in {
        AS2ProjectionHandoffResultKind.AGENT_QUARANTINE,
        AS2ProjectionHandoffResultKind.SYSTEMIC_FAILURE,
    }:
        if result.outcome is None:
            return WiringSystemicDisableRequest(
                correlation_id=result.correlation_id,
                reason="Projection handoff returned failure without a typed wiring outcome",
                reason_code=AS2WiringReasonCode.UNEXPECTED_PREPARATION_FAILURE,
                failure_context={"handoff_result_kind": result.kind.value},
            )
        return result.outcome
    return WiringSystemicDisableRequest(
        correlation_id=getattr(result, "correlation_id", _new_correlation_id()),
        reason="Unknown projection handoff result",
        reason_code=AS2WiringReasonCode.UNEXPECTED_PREPARATION_FAILURE,
        failure_context={"handoff_result_kind": str(getattr(result, "kind", "unknown"))},
    )

def _bridge_input_reason_code(exc: AS2BridgeInputError) -> AS2WiringReasonCode:
    name = exc.__class__.__name__
    if "Capability" in name:
        return AS2WiringReasonCode.INVALID_CAPABILITY_GRANT
    if "Identity" in name:
        return AS2WiringReasonCode.MISSING_IDENTITY_CONTEXT
    if "UnexpectedField" in name:
        return AS2WiringReasonCode.PAYLOAD_CLASSIFICATION_FAILED
    return AS2WiringReasonCode.MALFORMED_PAYLOAD_AGENT


def _agent_quarantine_outcome(
    exc: BaseException,
    host_prestage_payload: Mapping[str, Any],
    *,
    correlation_id: str,
    reason_code: AS2WiringReasonCode,
) -> WiringAgentQuarantineRequest:
    return WiringAgentQuarantineRequest(
        correlation_id=correlation_id,
        agent_id=_extract_agent_id(host_prestage_payload),
        reason=str(exc),
        reason_code=reason_code,
    )


def _systemic_disable_outcome(
    exc: BaseException,
    *,
    correlation_id: str,
    reason_code: AS2WiringReasonCode,
) -> WiringSystemicDisableRequest:
    return WiringSystemicDisableRequest(
        correlation_id=correlation_id,
        reason=str(exc),
        reason_code=reason_code,
        failure_context={"error_type": exc.__class__.__name__},
    )


def _extract_agent_id(host_prestage_payload: Mapping[str, Any]) -> str | None:
    """Best-effort extraction of agent identity for quarantine outcomes."""

    explicit_agent_id = host_prestage_payload.get("agent_id")
    if isinstance(explicit_agent_id, str) and explicit_agent_id:
        return explicit_agent_id

    identity_context = host_prestage_payload.get("adapter_identity_context")
    if not isinstance(identity_context, Mapping):
        return None

    for key in ("agent_id", "adapter_id", "identity_ref"):
        value = identity_context.get(key)
        if isinstance(value, str) and value:
            return value

    identity_seed = identity_context.get("identity_seed")
    if isinstance(identity_seed, Mapping):
        alias = identity_seed.get("alias")
        if isinstance(alias, str) and alias:
            return alias
        namespace = identity_seed.get("namespace")
        spawn_nonce = identity_seed.get("spawn_nonce")
        if isinstance(namespace, str) and isinstance(spawn_nonce, str):
            return f"{namespace}:{spawn_nonce}"

    return None
