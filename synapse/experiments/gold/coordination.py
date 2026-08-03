"""Reading every authority head as one observation instead of six.

``capture_authority_heads`` used to call a single reader and treat the result as
an instant. The module said so plainly — *one call is one call, not one instant*
— and that honesty did not make it safe: a reader is free to consult lifecycle,
provenance, taint, admission, compatibility and the boundary at six different
moments, and the set it returns then describes no world that ever existed. Each
anchor is individually valid, the identity checks all pass, and a consumption
decision can rest on a fresh admission anchor beside a lifecycle anchor captured
before a revoke. That is the mix-and-match §22 forbids, and no amount of
validating the *result* detects it, because the result is well-formed.

The fix has to happen while the read is in progress, so this module puts a fence
around it.

A ``SnapshotFencePort`` publishes a monotonic epoch that advances whenever any
authority store mutates, and hands out leases. A capture acquires a lease, reads
under it, and confirms at release that the epoch has not moved. If it has, some
store changed mid-read: the observation is torn, and a torn observation is
refused rather than repaired — there is no way to tell which of the six values
came from before the change.

What this gives and what it does not, stated plainly. The lease makes a torn
read *detectable*, and detection is what fail-closed needs: a capture either
describes one coherent moment or it fails. It does not make the read atomic. A
fence port backed by a real lock would additionally make tearing impossible; one
backed only by an epoch counter makes it visible. Both satisfy the barrier, and
which one is in use is a property of the injected port, not of this module — so
this module refuses to claim the stronger of the two.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Mapping, Protocol, runtime_checkable

from .admission import (
    AdmissionFailureCode,
    AdmissionViolation,
    AuthorityHeadSet,
    ConfiguredGateController,
    GateDependencyUnavailable,
    capture_authority_heads,
    require_configured_gate_controller,
)

#: The non-public surface this adapter takes from the admission owner. Declared
#: for the same reason ``point_of_use.ADAPTER_PRIVATE_SEAM`` is.
ADAPTER_PRIVATE_SEAM = ("_trusted_clock",)

_LEASE_SEAL = object()


class FenceFailureCode(str, Enum):
    """Why a fenced read was refused. Separate from the head-set failures on
    purpose: a torn observation is not a stale one, and an operator reading the
    record should not have to guess which happened."""

    TYPE_MISMATCH = "TYPE_MISMATCH"
    TRUSTED_OBJECT_FORGED = "TRUSTED_OBJECT_FORGED"
    FENCE_UNAVAILABLE = "FENCE_UNAVAILABLE"
    LEASE_NOT_HELD = "LEASE_NOT_HELD"
    OBSERVATION_TORN = "OBSERVATION_TORN"
    EPOCH_WENT_BACKWARDS = "EPOCH_WENT_BACKWARDS"


class FenceViolation(ValueError):
    """A typed, fail-closed fencing error carrying no subject payload."""

    def __init__(self, failure_code: FenceFailureCode, detail: str) -> None:
        if type(failure_code) is not FenceFailureCode:
            raise TypeError("failure_code must be an exact FenceFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: FenceFailureCode, detail: str) -> FenceViolation:
    return FenceViolation(code, detail)


@runtime_checkable
class SnapshotFencePort(Protocol):
    """What a coordinating store must offer for a read to be fenced.

    ``current_epoch`` advances whenever *any* authority store mutates — one
    counter across all of them, because a per-store counter cannot tell a
    consumer that lifecycle moved while taint was being read.
    """

    def acquire_lease(self) -> str: ...

    def current_epoch(self) -> int: ...

    def release_lease(self, lease_id: str) -> None: ...


def require_snapshot_fence(value: object) -> SnapshotFencePort:
    if not isinstance(value, SnapshotFencePort):
        raise _fail(FenceFailureCode.TYPE_MISMATCH, "fence does not implement the coordination port")
    for name in ("acquire_lease", "current_epoch", "release_lease"):
        if not callable(getattr(value, name, None)):
            raise _fail(FenceFailureCode.TYPE_MISMATCH, f"fence is missing {name}")
    return value


@dataclass(frozen=True, init=False)
class CoordinatedFenceLease:
    """A held lease and the epoch the world was at when it was taken."""

    lease_id: str
    entry_epoch: int
    acquired_at_utc: datetime
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CoordinatedFenceLease:
        raise TypeError("CoordinatedFenceLease is produced only by acquire_fence_lease")

    def to_dict(self) -> dict[str, object]:
        validate_fence_lease(self)
        return {
            "lease_id": self.lease_id,
            "entry_epoch": self.entry_epoch,
            "acquired_at_utc": self.acquired_at_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }


def validate_fence_lease(value: CoordinatedFenceLease) -> CoordinatedFenceLease:
    if type(value) is not CoordinatedFenceLease or getattr(value, "_trusted_seal", None) is not _LEASE_SEAL:
        raise _fail(FenceFailureCode.TRUSTED_OBJECT_FORGED, "lease is not factory sealed")
    if type(value.lease_id) is not str or not value.lease_id or len(value.lease_id) > 200:
        raise _fail(FenceFailureCode.TYPE_MISMATCH, "lease id is invalid")
    if type(value.entry_epoch) is not int or type(value.entry_epoch) is bool or value.entry_epoch < 0:
        raise _fail(FenceFailureCode.TYPE_MISMATCH, "epoch must be a non-negative exact int")
    if type(value.acquired_at_utc) is not datetime or value.acquired_at_utc.tzinfo is not timezone.utc:
        raise _fail(FenceFailureCode.TYPE_MISMATCH, "lease timestamp must be exact UTC")
    return value


def _call(fence_call: Callable[[], object], expected: type, what: str) -> object:
    """Invoke a fence operation and demand an exact answer.

    Same three-way split the gate probes use, and for the same reason: a store
    that cannot be reached is an outage, a store that answers with the wrong
    type is a broken adapter, and conflating them sends an incident analysis to
    the wrong place.
    """

    try:
        result = fence_call()
    except (FenceViolation, AdmissionViolation):
        raise
    except GateDependencyUnavailable as exc:
        raise _fail(FenceFailureCode.FENCE_UNAVAILABLE, f"the fence could not {what}") from exc
    if type(result) is not expected:
        raise _fail(
            FenceFailureCode.TYPE_MISMATCH,
            f"the fence returned {type(result).__name__} where {expected.__name__} was required",
        )
    return result


def acquire_fence_lease(
    fence: SnapshotFencePort,
    *,
    trusted_clock: Callable[[], datetime],
) -> CoordinatedFenceLease:
    """Take a lease and record the epoch the world was at."""

    require_snapshot_fence(fence)
    if not callable(trusted_clock):
        raise _fail(FenceFailureCode.TYPE_MISMATCH, "trusted_clock must be callable")
    lease_id = _call(fence.acquire_lease, str, "issue a lease")
    epoch = _call(fence.current_epoch, int, "report its epoch")
    now = trusted_clock()
    if type(now) is not datetime or now.tzinfo is not timezone.utc:
        raise _fail(FenceFailureCode.TYPE_MISMATCH, "trusted clock did not return exact UTC")

    lease = object.__new__(CoordinatedFenceLease)
    object.__setattr__(lease, "lease_id", lease_id)
    object.__setattr__(lease, "entry_epoch", epoch)
    object.__setattr__(lease, "acquired_at_utc", now)
    object.__setattr__(lease, "_trusted_seal", _LEASE_SEAL)
    return validate_fence_lease(lease)


@dataclass(frozen=True, init=False)
class FencedAuthorityState:
    """A head observation that is known to describe one moment.

    Carrying the lease and both epochs is the point: a reader of this record can
    see that the world did not move between the two reads, rather than being
    asked to assume it.
    """

    head_set: AuthorityHeadSet
    lease: CoordinatedFenceLease
    exit_epoch: int
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> FencedAuthorityState:
        raise TypeError("FencedAuthorityState is produced only by read_current_authority_state")


def validate_fenced_authority_state(value: FencedAuthorityState) -> FencedAuthorityState:
    if type(value) is not FencedAuthorityState or getattr(value, "_trusted_seal", None) is not _LEASE_SEAL:
        raise _fail(FenceFailureCode.TRUSTED_OBJECT_FORGED, "fenced state is not factory sealed")
    validate_fence_lease(value.lease)
    if type(value.exit_epoch) is not int or type(value.exit_epoch) is bool:
        raise _fail(FenceFailureCode.TYPE_MISMATCH, "exit epoch must be an exact int")
    if value.exit_epoch != value.lease.entry_epoch:
        raise _fail(
            FenceFailureCode.OBSERVATION_TORN,
            "a store moved while the observation was being taken",
        )
    return value


def read_current_authority_state(
    controller: ConfiguredGateController,
    *,
    fence: SnapshotFencePort,
) -> FencedAuthorityState:
    """Read every authority head under one lease, or refuse.

    The epoch is read before and after. Equal means no authority store mutated
    while the six values were being gathered, so they describe one moment. Not
    equal means they may not, and there is no way from here to tell which of
    them came from before the change — so the observation is refused rather than
    partially trusted.

    The lease is released on every path, including the failing ones. A fence
    that leaked leases on error would turn a detected tear into a stuck system,
    which is a worse outcome than the tear.
    """

    require_configured_gate_controller(controller)
    require_snapshot_fence(fence)
    lease = acquire_fence_lease(fence, trusted_clock=controller._trusted_clock)
    try:
        head_set = capture_authority_heads(controller)
        exit_epoch = _call(fence.current_epoch, int, "report its epoch")
        if exit_epoch < lease.entry_epoch:
            raise _fail(
                FenceFailureCode.EPOCH_WENT_BACKWARDS,
                "the fence epoch decreased, which an append-only world cannot do",
            )
        if exit_epoch != lease.entry_epoch:
            raise _fail(
                FenceFailureCode.OBSERVATION_TORN,
                "a store moved while the observation was being taken",
            )
    finally:
        _release(fence, lease)

    state = object.__new__(FencedAuthorityState)
    object.__setattr__(state, "head_set", head_set)
    object.__setattr__(state, "lease", lease)
    object.__setattr__(state, "exit_epoch", exit_epoch)
    object.__setattr__(state, "_trusted_seal", _LEASE_SEAL)
    return validate_fenced_authority_state(state)


def _release(fence: SnapshotFencePort, lease: CoordinatedFenceLease) -> None:
    """Release a lease without letting the release mask the original failure."""

    try:
        fence.release_lease(lease.lease_id)
    except (FenceViolation, AdmissionViolation):
        raise
    except GateDependencyUnavailable:
        # A fence that cannot confirm the release is an outage, and the caller is
        # already either failing or holding a verified observation. Raising here
        # would replace a precise diagnosis with a vaguer one.
        return


def require_untorn_state(
    value: FencedAuthorityState,
    *,
    fence: SnapshotFencePort,
) -> AuthorityHeadSet:
    """Confirm at the point of use that the world has not moved since the read.

    A fenced observation proves coherence *at capture time*. Whether it is still
    current is a different question, and this is where it is asked: the epoch now
    must still be the one the lease recorded. This is the fence-level analogue of
    ``require_current_heads`` — cheaper, because one counter answers for all six
    stores, and strictly weaker, because it says only that nothing moved and not
    what the values are. Both run; neither replaces the other.
    """

    validate_fenced_authority_state(value)
    require_snapshot_fence(fence)
    now = _call(fence.current_epoch, int, "report its epoch")
    if now != value.lease.entry_epoch:
        raise _fail(
            FenceFailureCode.OBSERVATION_TORN,
            "an authority store has moved since this observation was taken",
        )
    return value.head_set


__all__ = [
    "ADAPTER_PRIVATE_SEAM",
    "CoordinatedFenceLease",
    "FenceFailureCode",
    "FenceViolation",
    "FencedAuthorityState",
    "SnapshotFencePort",
    "acquire_fence_lease",
    "read_current_authority_state",
    "require_snapshot_fence",
    "require_untorn_state",
    "validate_fence_lease",
    "validate_fenced_authority_state",
]
