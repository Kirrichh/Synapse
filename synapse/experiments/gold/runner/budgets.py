"""Run admission budgets, recomputed from durable clock and worker checkpoints.

These are admission limits between attempts. Provider requests already in
flight cannot be recalled by a subprocess controller; their full reported cost
is retained, including an overrun. Missing usage never becomes zero.
"""

from .completed_delivery_codec import restore_completed_worker_delivery
from .records import RecordKind
from .run_progress import (
    AttemptPreparationStarted,
    AttemptProgressPhase,
    load_attempt_progress,
    require_progress_payload,
)
from .vocabulary import AttemptOutcome


def preparation_budget_failure(*, store, manifest, attempts, observed_at_unix_ms: int) -> str | None:
    """Read existing execution evidence; never estimate an unknown token total."""

    first = store.get(kind=RecordKind.PREPARATION_STARTED, key="1")
    if first is None:
        return "run_clock_unavailable"
    origin = AttemptPreparationStarted.from_payload(first.payload).started_at_unix_ms
    elapsed = observed_at_unix_ms - origin
    if elapsed < 0:
        return "run_clock_unavailable"
    if elapsed >= manifest.config.budgets.maximum_wall_clock_seconds * 1_000:
        return "wall_clock_budget_exhausted"
    tokens = 0
    for attempt in attempts:
        start = store.get(kind=RecordKind.PREPARATION_STARTED, key=str(attempt.attempt_index))
        if start is None or AttemptPreparationStarted.from_payload(start.payload).started_at_unix_ms > observed_at_unix_ms:
            return "run_clock_unavailable"
        progress = load_attempt_progress(store, manifest=manifest, context=attempt.context)
        completed = progress.get(AttemptProgressPhase.WORKER_COMPLETED)
        if completed is None:
            if attempt.result is None or attempt.result.outcome is AttemptOutcome.CONTROLLER_INTERRUPTED:
                return "worker_usage_unavailable"
            continue
        raw, ref = require_progress_payload(completed)
        delivery = restore_completed_worker_delivery(raw, expected_ref=ref)
        usage = delivery.worker_result.usage
        if usage.token_status.value == "UNAVAILABLE" or type(usage.total_tokens) is not int or usage.total_tokens < 0:
            return "worker_usage_unavailable"
        tokens += usage.total_tokens
    if tokens >= manifest.config.budgets.maximum_worker_tokens:
        return "worker_token_budget_exhausted"
    return None
