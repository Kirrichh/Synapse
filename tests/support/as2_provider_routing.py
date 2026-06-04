"""P0.6.29 safe provider-failure routing helpers.

This module is test support only. It is the Anti-Corruption Layer between the
provider-boundary ``ProviderReasonCode`` taxonomy and the control-plane
``AS2ProviderFailureReasonCode`` taxonomy. It must never use raw
``AS2ProviderFailureReasonCode(value)`` casts because provider codes may be
introduced before the control plane grows an exact matching code.
"""
from __future__ import annotations

from synapse.runtime.as2_gate_controller import (
    AS2GateControllerSkeleton,
    AS2GateDecision,
    AS2ProviderFailureReasonCode,
)
from synapse.runtime.as2_provider_ports import ProviderFailure, ProviderReasonCode

PROVIDER_TO_GATE_REASON_MAP: dict[ProviderReasonCode, AS2ProviderFailureReasonCode] = {
    ProviderReasonCode.TIMEOUT: AS2ProviderFailureReasonCode.TIMEOUT,
    ProviderReasonCode.UNAVAILABLE: AS2ProviderFailureReasonCode.UNAVAILABLE,
    ProviderReasonCode.BACKPRESSURE_REJECTED: AS2ProviderFailureReasonCode.BACKPRESSURE_REJECTED,
    ProviderReasonCode.MISSING_REQUEST_CONTEXT: AS2ProviderFailureReasonCode.MISSING_REQUEST_CONTEXT,
    ProviderReasonCode.UNAUTHORIZED: AS2ProviderFailureReasonCode.UNAUTHORIZED,
    ProviderReasonCode.FORBIDDEN: AS2ProviderFailureReasonCode.FORBIDDEN,
    ProviderReasonCode.SCHEMA_MISMATCH: AS2ProviderFailureReasonCode.SCHEMA_MISMATCH,
    ProviderReasonCode.NOT_FOUND: AS2ProviderFailureReasonCode.NOT_FOUND,
    ProviderReasonCode.INVALID_INPUT: AS2ProviderFailureReasonCode.INVALID_INPUT,
    ProviderReasonCode.CANCELLED: AS2ProviderFailureReasonCode.CANCELLED,
}


def safe_map_provider_reason(reason: ProviderReasonCode) -> AS2ProviderFailureReasonCode:
    """Map provider reason code to gate reason code without raising ValueError.

    P0.6.30 explicitly maps all ProviderReasonCode values known to Stage 1-3.
    The fallback remains only as a safety net for future provider-boundary
    codes introduced before the control plane grows an exact equivalent.
    """

    mapped = PROVIDER_TO_GATE_REASON_MAP.get(reason)
    if mapped is not None:
        return mapped
    if reason in {ProviderReasonCode.INVALID_INPUT, ProviderReasonCode.SCHEMA_MISMATCH}:
        return AS2ProviderFailureReasonCode.SCHEMA_MISMATCH
    return AS2ProviderFailureReasonCode.TIMEOUT


def route_provider_failure_to_controller(
    controller: AS2GateControllerSkeleton,
    failure: ProviderFailure,
) -> AS2GateDecision:
    """Route ProviderFailure through AS2GateController using safe mapping."""

    return controller.handle_provider_failure(
        safe_map_provider_reason(failure.reason_code),
        correlation_id=failure.correlation_id,
        provider_name=failure.provider_name or "provider",
    )


__all__ = [
    "PROVIDER_TO_GATE_REASON_MAP",
    "route_provider_failure_to_controller",
    "safe_map_provider_reason",
]
