"""Durable post-request boundary for one governed replay attempt.

This owner component sequences persistence after a replay request exists.  It
does not decide replay verdicts and it never executes a machine.  Its job is to
make the lifecycle fact unambiguous: either the store contains the sole result
for the request, or it contains an explicit incomplete/recoverable record.

Restart reconciliation materialises the latter for a request or execution claim
left dangling by process interruption.  It never retries the execution claim,
which is what prevents recovery from repeating an external effect.
"""

from __future__ import annotations

from enum import Enum
import re
from typing import Callable, Protocol, runtime_checkable

from .canonicalization import HashBoundRef
from .persistence import store_transaction
from .replay_attempt_lifecycle import (
    ReplayAttemptFailureDomain,
    ReplayAttemptPhase,
    ReplayExecutionClaim,
    ReplayIncompleteAttempt,
)


class ReplayAttemptBoundaryFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    INVALID_STATE = "INVALID_STATE"
    SNAPSHOT_READ_BACK_MISMATCH = "SNAPSHOT_READ_BACK_MISMATCH"
    RESULT_READ_BACK_MISMATCH = "RESULT_READ_BACK_MISMATCH"


class ReplayAttemptBoundaryViolation(RuntimeError):
    def __init__(
        self, failure_code: ReplayAttemptBoundaryFailureCode, detail: str
    ) -> None:
        if type(failure_code) is not ReplayAttemptBoundaryFailureCode:
            raise TypeError("failure_code must be an exact boundary failure code")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(
    code: ReplayAttemptBoundaryFailureCode, detail: str
) -> ReplayAttemptBoundaryViolation:
    return ReplayAttemptBoundaryViolation(code, detail)


@runtime_checkable
class ReplayAttemptHistoryPort(Protocol):
    mutation_fence: object

    def put_snapshot(self, raw: bytes, *, ticket: object) -> HashBoundRef: ...

    def open_snapshot(self, reference: HashBoundRef) -> bytes: ...

    def append_result(self, result: object, *, ticket: object) -> HashBoundRef: ...

    def require_result(self, reference: HashBoundRef) -> object: ...

    def append_incomplete_attempt(
        self, attempt: ReplayIncompleteAttempt, *, ticket: object
    ) -> HashBoundRef: ...

    def result_ref_for_request(
        self, request_ref: HashBoundRef
    ) -> HashBoundRef | None: ...

    def recorded_execution_claims(self) -> tuple[ReplayExecutionClaim, ...]: ...

    def unresolved_request_refs(self) -> tuple[HashBoundRef, ...]: ...

    def unresolved_execution_claims(self) -> tuple[ReplayExecutionClaim, ...]: ...


class DurableReplayAttemptBoundary:
    """Track and persist every operation after one request became durable."""

    __slots__ = (
        "_store",
        "_fence",
        "_guard",
        "_settle",
        "_request_ref",
        "_entry_epoch",
        "_own_intervals",
        "_execution_identity",
        "_claim_counted",
        "_phase",
        "_failure_domain",
        "_completed",
    )

    def __init__(
        self,
        *,
        store: ReplayAttemptHistoryPort,
        fence: object,
        coordinator_guard: object,
        settle: Callable[..., None],
        request_ref: HashBoundRef,
        entry_epoch: int,
    ) -> None:
        if not isinstance(store, ReplayAttemptHistoryPort):
            raise _fail(
                ReplayAttemptBoundaryFailureCode.TYPE_MISMATCH,
                "attempt boundary requires the durable replay history port",
            )
        if store.mutation_fence is not fence:
            raise _fail(
                ReplayAttemptBoundaryFailureCode.TYPE_MISMATCH,
                "attempt boundary store belongs to another coordinator",
            )
        if not callable(settle):
            raise _fail(
                ReplayAttemptBoundaryFailureCode.TYPE_MISMATCH,
                "attempt boundary requires the composition settlement port",
            )
        if type(request_ref) is not HashBoundRef:
            raise _fail(
                ReplayAttemptBoundaryFailureCode.TYPE_MISMATCH,
                "attempt boundary requires an exact durable request ref",
            )
        if type(entry_epoch) is not int or entry_epoch < 0 or entry_epoch % 2:
            raise _fail(
                ReplayAttemptBoundaryFailureCode.TYPE_MISMATCH,
                "attempt boundary requires its settled entry epoch",
            )
        self._store = store
        self._fence = fence
        self._guard = coordinator_guard
        self._settle = settle
        self._request_ref = request_ref
        self._entry_epoch = entry_epoch
        self._own_intervals = 1  # the request transaction
        self._execution_identity: str | None = None
        self._claim_counted = False
        self._phase = ReplayAttemptPhase.DURABLE_POLICY_REREAD
        self._failure_domain = ReplayAttemptFailureDomain.POLICY_AUTHORITY
        self._completed = False

    def entering(
        self,
        phase: ReplayAttemptPhase,
        failure_domain: ReplayAttemptFailureDomain,
    ) -> None:
        if type(phase) is not ReplayAttemptPhase or type(failure_domain) is not ReplayAttemptFailureDomain:
            raise _fail(
                ReplayAttemptBoundaryFailureCode.TYPE_MISMATCH,
                "attempt boundary phase and failure domain must be exact",
            )
        if self._completed:
            raise _fail(
                ReplayAttemptBoundaryFailureCode.INVALID_STATE,
                "a completed attempt cannot enter another phase",
            )
        self._phase = phase
        self._failure_domain = failure_domain

    def bind_execution_identity(self, identity: str) -> None:
        if type(identity) is not str or re.fullmatch(r"[0-9a-f]{64}", identity) is None:
            raise _fail(
                ReplayAttemptBoundaryFailureCode.TYPE_MISMATCH,
                "execution identity must be an exact SHA-256 digest",
            )
        if self._execution_identity is not None and self._execution_identity != identity:
            raise _fail(
                ReplayAttemptBoundaryFailureCode.INVALID_STATE,
                "attempt boundary cannot change execution identity",
            )
        self._execution_identity = identity

    def reconcile_execution_claim(self) -> None:
        """Account for a claim even if its transaction failed after publication."""

        matching = tuple(
            item
            for item in self._store.recorded_execution_claims()
            if item.request_ref.to_dict() == self._request_ref.to_dict()
        )
        if len(matching) > 1:
            raise _fail(
                ReplayAttemptBoundaryFailureCode.INVALID_STATE,
                "a durable request has more than one execution claim",
            )
        if not matching:
            return
        self.bind_execution_identity(matching[0].execution_identity)
        if not self._claim_counted:
            self._own_intervals += 1
            self._claim_counted = True

    def store_terminal_snapshot(self, raw: bytes) -> HashBoundRef:
        if type(raw) is not bytes:
            raise _fail(
                ReplayAttemptBoundaryFailureCode.TYPE_MISMATCH,
                "terminal snapshot must be exact bytes",
            )
        self.entering(
            ReplayAttemptPhase.TERMINAL_SNAPSHOT_WRITE,
            ReplayAttemptFailureDomain.REPLAY_STORE,
        )
        with store_transaction(self._fence, guard=self._guard) as ticket:
            reference = self._store.put_snapshot(raw, ticket=ticket)
        self._own_intervals += 1
        self.entering(
            ReplayAttemptPhase.TERMINAL_SNAPSHOT_READ_BACK,
            ReplayAttemptFailureDomain.REPLAY_STORE,
        )
        if self._store.open_snapshot(reference) != raw:
            raise _fail(
                ReplayAttemptBoundaryFailureCode.SNAPSHOT_READ_BACK_MISMATCH,
                "terminal snapshot read-back differs from the bytes written",
            )
        return reference

    def complete(self, result: object) -> HashBoundRef:
        self.entering(
            ReplayAttemptPhase.RESULT_APPEND,
            ReplayAttemptFailureDomain.REPLAY_STORE,
        )
        with store_transaction(self._fence, guard=self._guard) as ticket:
            reference = self._store.append_result(result, ticket=ticket)
        self._own_intervals += 1
        restored = self._store.require_result(reference)
        if restored != result:
            raise _fail(
                ReplayAttemptBoundaryFailureCode.RESULT_READ_BACK_MISMATCH,
                "durable replay result does not read back as the result appended",
            )
        self._completed = True
        self._settle(
            fence=self._fence,
            coordinator_id=self._fence.coordinator_id(),
            entry_epoch=self._entry_epoch,
            own_intervals=self._own_intervals,
        )
        return reference

    def record_incomplete(self) -> bool:
        """Best-effort materialisation that never masks the triggering failure."""

        try:
            if self._store.result_ref_for_request(self._request_ref) is not None:
                self._completed = True
                return False
            self.reconcile_execution_claim()
            attempt = ReplayIncompleteAttempt(
                request_ref=self._request_ref,
                execution_identity=self._execution_identity,
                phase=self._phase,
                failure_domain=self._failure_domain,
            )
            with store_transaction(self._fence, guard=self._guard) as ticket:
                self._store.append_incomplete_attempt(attempt, ticket=ticket)
            self._own_intervals += 1
            self._settle(
                fence=self._fence,
                coordinator_id=self._fence.coordinator_id(),
                entry_epoch=self._entry_epoch,
                own_intervals=self._own_intervals,
            )
            return True
        except Exception:  # noqa: BLE001 - the original boundary failure wins
            return False


def recover_interrupted_replay_attempts(
    *,
    store: ReplayAttemptHistoryPort,
    fence: object,
    settle: Callable[..., None],
) -> int:
    """Materialise dangling requests/claims after restart without executing them."""

    if (
        not isinstance(store, ReplayAttemptHistoryPort)
        or store.mutation_fence is not fence
        or not callable(settle)
    ):
        raise _fail(
            ReplayAttemptBoundaryFailureCode.TYPE_MISMATCH,
            "recovery requires the exact replay history and its coordinator",
        )
    request_refs = store.unresolved_request_refs()
    if not request_refs:
        return 0
    claims = store.unresolved_execution_claims()
    attempts = tuple(
        ReplayIncompleteAttempt(
            request_ref=reference,
            execution_identity=next(
                (
                    item.execution_identity
                    for item in claims
                    if item.request_ref.to_dict() == reference.to_dict()
                ),
                None,
            ),
            phase=None,
            failure_domain=None,
        )
        for reference in request_refs
    )
    entry_epoch = fence.current_epoch()
    if type(entry_epoch) is not int or entry_epoch < 0 or entry_epoch % 2:
        raise _fail(
            ReplayAttemptBoundaryFailureCode.INVALID_STATE,
            "replay recovery requires a settled coordinator",
        )
    with fence.exclusive() as guard:
        with store_transaction(fence, guard=guard) as ticket:
            for attempt in attempts:
                store.append_incomplete_attempt(attempt, ticket=ticket)
        settle(
            fence=fence,
            coordinator_id=fence.coordinator_id(),
            entry_epoch=entry_epoch,
            own_intervals=1,
        )
    return len(attempts)


__all__ = [
    "DurableReplayAttemptBoundary",
    "ReplayAttemptBoundaryFailureCode",
    "ReplayAttemptBoundaryViolation",
    "ReplayAttemptHistoryPort",
    "recover_interrupted_replay_attempts",
]
