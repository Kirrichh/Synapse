"""P0.6.26 regression tests for AS2GateController concurrency guardrails."""
from __future__ import annotations

import threading

from synapse.runtime.as2_gate_controller import (
    AS2GateControllerSkeleton,
    AS2GateDecisionKind,
    AS2ProviderFailureReasonCode,
)


def test_p0626_provider_failure_threshold_is_atomic_under_concurrent_calls() -> None:
    """Race guard: provider timeout threshold increment and audit remain atomic."""

    controller = AS2GateControllerSkeleton(provider_failure_threshold=2)
    results: list[AS2GateDecisionKind] = []
    lock = threading.Lock()

    def call(index: int) -> None:
        decision = controller.handle_provider_failure(
            AS2ProviderFailureReasonCode.TIMEOUT,
            correlation_id=f"corr-thread-{index}",
            provider_name="memory",
        )
        with lock:
            results.append(decision.kind)

    threads = [threading.Thread(target=call, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    systemic = sum(1 for kind in results if kind is AS2GateDecisionKind.SYSTEMIC_DISABLE)
    assert len(results) == 4
    assert systemic >= 1
