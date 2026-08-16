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

A ``SnapshotFencePort`` publishes a monotonic epoch that is **odd for exactly as
long as a mutation interval is open** and even otherwise. A capture takes the
epoch, reads the six heads, and takes it again: even at both ends and equal
means no interval was open at either edge and none opened and closed in between,
which is what "one moment" reduces to. Any other combination is refused rather
than repaired — there is no way to tell which of the six values came from before
the change.

This replaced a lease. The lease named a read window and, as the module then
admitted, excluded nobody; naming an attempt is not a property the comparison
uses, so it went. What did not go is the honesty about scope: the epoch makes a
torn read *detectable*, and detection is what fail-closed needs, but it does not
make the read atomic. ``exclusive`` on the port is the stronger primitive, and it
is used only where detection genuinely is not enough — a sequence that reads,
decides, and then writes.
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
    validate_authority_head_set,
    require_configured_gate_controller,
)

#: The non-public surface this adapter takes from the admission owner. Declared
#: for the same reason ``point_of_use.ADAPTER_PRIVATE_SEAM`` is.
ADAPTER_PRIVATE_SEAM = ("_trusted_clock",)

_LEASE_SEAL = object()


class FenceFailureCode(str, Enum):
    """Why a fenced read was refused.

    Separate from the head-set failures on purpose: a torn observation is not a
    stale one, and an operator reading the record should not have to guess which
    happened. The same reasoning applies *within* this enum, which is why five
    conditions that all used to answer ``OBSERVATION_TORN`` no longer do. They
    call for different responses:

    * ``MUTATION_IN_FLIGHT_AT_ENTRY`` — a writer was already mid-interval when the
      read began. Nothing was read; retrying once the interval closes is correct
      and costs nothing.
    * ``MUTATION_IN_FLIGHT_AT_EXIT`` — the interval opened *during* the read. Some
      of the six heads are from before it and some may be from after, so the
      values gathered are discarded rather than reconciled.
    * ``OBSERVATION_TORN`` — entry and exit epochs are both even and differ, so at
      least one complete mutation landed inside the window. Retrying is correct,
      but unlike the entry case something did change, and a caller that keeps
      seeing this is losing a race it should be told about.
    * ``EPOCH_WENT_BACKWARDS`` — a monotone counter decreased. No retry can help;
      the coordinator's own state is untrustworthy and this is an incident.
    * ``COORDINATOR_MISMATCH`` — the barrier and the store it claims to cover
      belong to different coordinators. Retrying reproduces it forever; it is a
      wiring defect, and answering "torn" would have sent an operator looking for
      a concurrent writer that does not exist.

    ``COORDINATOR_UNKNOWN`` is deliberately not the same as ``COORDINATOR_MISMATCH``
    (NR-10): a store attached to nothing at all has not been shown to disagree,
    it has been shown to be unaccounted for.
    """

    TYPE_MISMATCH = "TYPE_MISMATCH"
    TRUSTED_OBJECT_FORGED = "TRUSTED_OBJECT_FORGED"
    FENCE_UNAVAILABLE = "FENCE_UNAVAILABLE"
    COORDINATOR_UNKNOWN = "COORDINATOR_UNKNOWN"
    COORDINATOR_MISMATCH = "COORDINATOR_MISMATCH"
    MUTATION_IN_FLIGHT_AT_ENTRY = "MUTATION_IN_FLIGHT_AT_ENTRY"
    MUTATION_IN_FLIGHT_AT_EXIT = "MUTATION_IN_FLIGHT_AT_EXIT"
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

    `current_epoch` counts mutation marks across *every* authority store attached
    to this coordinator — one counter for all of them, because a per-store counter
    cannot tell a consumer that lifecycle moved while taint was being read.

    `coordinator_id` is what makes "attached to this coordinator" checkable. The
    port used to be satisfied by shape alone, and the acceptance suites duly
    handed the point of use one fence while the journal it read held another: two
    objects with the right methods, no relationship, every structural check green.

    `mutating` marks an interval rather than an instant. The earlier `bump`
    advanced a counter *after* an append, which cannot make a torn read
    detectable: a reader takes its entry epoch, observes the appended record,
    takes its exit epoch and finds no change, because the mark has not happened
    yet. Marking before instead opens the symmetric window.

    `exclusive` is the part the epoch cannot supply. Detecting interference is
    enough for a reader, which can retry; it is not enough for a read-decide-write
    sequence, which can detect interference indefinitely and never finish. The
    point of use is such a sequence, so it holds this for its whole transaction
    and the epoch then reports only the interval it opened itself.
    """

    def coordinator_id(self) -> str: ...

    def current_epoch(self) -> int: ...

    def mutating(self): ...

    def exclusive(self): ...


def require_snapshot_fence(value: object) -> SnapshotFencePort:
    if not isinstance(value, SnapshotFencePort):
        raise _fail(FenceFailureCode.TYPE_MISMATCH, "fence does not implement the coordination port")
    for name in ("coordinator_id", "current_epoch", "mutating", "exclusive"):
        if not callable(getattr(value, name, None)):
            raise _fail(FenceFailureCode.TYPE_MISMATCH, f"fence is missing {name}")
    return value


@dataclass(frozen=True, init=False)
class CoordinatedReadWindow:
    """The bracket a seqlock read is taken inside.

    Named for what it is. Its predecessor was called a lease, and the fence's own
    docstring admitted the lease excluded nobody — so the name promised a property
    no caller ever received. What this records is the epoch the read opened at and
    the coordinator it belongs to, which is exactly what the exit comparison needs
    and nothing more.
    """

    coordinator_id: str
    entry_epoch: int
    opened_at_utc: datetime
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CoordinatedReadWindow:
        raise TypeError("CoordinatedReadWindow is produced only by read_current_authority_state")


def validate_read_window(value: CoordinatedReadWindow) -> CoordinatedReadWindow:
    if (
        type(value) is not CoordinatedReadWindow
        or getattr(value, "_trusted_seal", None) is not _LEASE_SEAL
    ):
        raise _fail(FenceFailureCode.TRUSTED_OBJECT_FORGED, "read window is not fence produced")
    if type(value.entry_epoch) is not int or value.entry_epoch < 0:
        raise _fail(FenceFailureCode.TYPE_MISMATCH, "a read window needs an exact epoch")
    if type(value.coordinator_id) is not str or not value.coordinator_id:
        raise _fail(FenceFailureCode.TYPE_MISMATCH, "a read window names its coordinator")
    return value


def _call(fence_call: Callable[[], object], expected: type, what: str) -> object:
    try:
        result = fence_call()
    except (FenceViolation, AdmissionViolation, GateDependencyUnavailable):
        raise
    except Exception as exc:
        raise _fail(FenceFailureCode.FENCE_UNAVAILABLE, f"the fence could not {what}") from exc
    if type(result) is not expected:
        raise _fail(FenceFailureCode.TYPE_MISMATCH, f"the fence did not {what} as an exact value")
    return result


def _settled_epoch(
    fence: SnapshotFencePort,
    *,
    what: str,
    odd_code: FenceFailureCode,
    odd_detail: str,
) -> int:
    """Read the epoch and refuse it while a write is in flight.

    Odd means some writer is between its opening and closing mark. The stores are
    mid-change, and a reader that proceeded would be combining values whose
    relationship to each other is unknown — the exact condition the counter
    exists to expose, so it is refused rather than waited out here.

    The caller supplies the code because *where* the odd value was seen is the
    whole of what distinguishes the two cases: before the heads were gathered
    nothing has been read yet, after them a half-read set is being thrown away.
    """

    epoch = _call(fence.current_epoch, int, what)
    if epoch % 2:
        raise _fail(odd_code, odd_detail)
    return epoch


def require_same_coordinator(fence: SnapshotFencePort, *, participants: tuple[object, ...]) -> None:
    """Every store read under this fence must be attached to this coordinator.

    A participant attached elsewhere contributes a counter that moves for
    unrelated reasons and stays still for the relevant ones. That is worse than no
    barrier, because the record then states that a coherent moment was verified.
    """

    require_snapshot_fence(fence)
    mine = _call(fence.coordinator_id, str, "report its coordinator")
    for participant in participants:
        theirs = getattr(participant, "mutation_fence", None)
        if theirs is None:
            raise _fail(
                FenceFailureCode.COORDINATOR_UNKNOWN,
                "a participating store is attached to no coordinator at all",
            )
        if _call(theirs.coordinator_id, str, "report its coordinator") != mine:
            raise _fail(
                FenceFailureCode.COORDINATOR_MISMATCH,
                "a participating store is attached to a different coordinator",
            )


@dataclass(frozen=True, init=False)
class FencedAuthorityState:
    """Six heads and the window they were read inside."""

    head_set: AuthorityHeadSet
    window: CoordinatedReadWindow
    exit_epoch: int
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> FencedAuthorityState:
        raise TypeError("FencedAuthorityState is produced only by read_current_authority_state")


def validate_fenced_authority_state(value: FencedAuthorityState) -> FencedAuthorityState:
    if (
        type(value) is not FencedAuthorityState
        or getattr(value, "_trusted_seal", None) is not _LEASE_SEAL
    ):
        raise _fail(FenceFailureCode.TRUSTED_OBJECT_FORGED, "fenced state is not fence produced")
    validate_read_window(value.window)
    if type(value.exit_epoch) is not int or value.exit_epoch < 0:
        raise _fail(FenceFailureCode.TYPE_MISMATCH, "a fenced state needs an exact exit epoch")
    if value.exit_epoch % 2:
        raise _fail(
            FenceFailureCode.MUTATION_IN_FLIGHT_AT_EXIT,
            "a fenced state cannot have ended with a mutation in flight",
        )
    # Re-checked rather than trusted to construction: the record is passed
    # around, and a validator that only looked at the seal would accept a state
    # whose two epochs had been made to disagree after the fact.
    if value.exit_epoch != value.window.entry_epoch:
        raise _fail(
            FenceFailureCode.OBSERVATION_TORN,
            "the fenced state's epochs disagree, so it describes no single moment",
        )
    validate_authority_head_set(value.head_set)
    return value


def read_current_authority_state(
    controller: ConfiguredGateController,
    *,
    fence: SnapshotFencePort,
    participants: tuple[object, ...] = (),
) -> FencedAuthorityState:
    """Read every authority head inside one seqlock window, or refuse.

    Entry and exit epochs must be equal and even. Equal means no mutation interval
    opened or closed while the six values were gathered; even means none was open
    at either end. Together they say the values describe one moment, which two
    readings of a counter advanced *after* the fact could never establish.

    `participants` are the stores whose state this observation will be combined
    with. Each must be attached to this same coordinator, because a counter that
    does not count their mutations answers a different question than the one
    being asked.
    """

    require_configured_gate_controller(controller)
    require_snapshot_fence(fence)
    require_same_coordinator(fence, participants=tuple(participants))

    entry_epoch = _settled_epoch(
        fence,
        what="report its epoch",
        odd_code=FenceFailureCode.MUTATION_IN_FLIGHT_AT_ENTRY,
        odd_detail="a mutation was already in flight, so the read never began",
    )
    window = object.__new__(CoordinatedReadWindow)
    object.__setattr__(window, "coordinator_id", _call(fence.coordinator_id, str, "report its coordinator"))
    object.__setattr__(window, "entry_epoch", entry_epoch)
    object.__setattr__(window, "opened_at_utc", controller._trusted_clock())
    object.__setattr__(window, "_trusted_seal", _LEASE_SEAL)
    validate_read_window(window)

    head_set = capture_authority_heads(controller)
    exit_epoch = _call(fence.current_epoch, int, "report its epoch")
    # Backwards first: a decreasing counter is not a tear and not an in-flight
    # write, and testing it after the parity check would let an odd decrease be
    # reported as an ordinary concurrent writer.
    if exit_epoch < entry_epoch:
        raise _fail(
            FenceFailureCode.EPOCH_WENT_BACKWARDS,
            "the fence epoch decreased, which an append-only world cannot do",
        )
    if exit_epoch % 2:
        raise _fail(
            FenceFailureCode.MUTATION_IN_FLIGHT_AT_EXIT,
            "a mutation opened while the observation was being taken",
        )
    # Entry and exit being *equal* is deliberately not re-tested here. The state
    # built below is validated before it is returned, and that validation already
    # refuses two epochs that disagree — so a copy of the comparison at this line
    # is the same rule written twice, and the mutation campaign proved it by
    # deleting this one and killing nothing. The rule lives in
    # ``validate_fenced_authority_state``, where it also covers a record that was
    # tampered with after construction, which a check here never could.

    state = object.__new__(FencedAuthorityState)
    object.__setattr__(state, "head_set", head_set)
    object.__setattr__(state, "window", window)
    object.__setattr__(state, "exit_epoch", exit_epoch)
    object.__setattr__(state, "_trusted_seal", _LEASE_SEAL)
    return validate_fenced_authority_state(state)


def require_untorn_state(
    value: FencedAuthorityState,
    *,
    fence: SnapshotFencePort,
) -> AuthorityHeadSet:
    """Confirm at the point of use that the world has not moved since the read.

    A fenced observation proves coherence *at capture time*. Whether it is still
    current is a different question, and this is where it is asked: the epoch now
    must still be the one the window opened at, and must not be odd. This is the
    fence-level analogue of ``require_current_heads`` — cheaper, because one
    counter answers for all six stores, and strictly weaker, because it says only
    that nothing moved and not what the values are. Both run; neither replaces
    the other.
    """

    validate_fenced_authority_state(value)
    require_snapshot_fence(fence)
    # Identity before epoch, and not because of ordering aesthetics: a second
    # coordinator sitting at the same even number would otherwise confirm this
    # observation perfectly, which is precisely the substitution the window
    # records a coordinator to prevent.
    if _call(fence.coordinator_id, str, "report its coordinator") != value.window.coordinator_id:
        raise _fail(
            FenceFailureCode.COORDINATOR_MISMATCH,
            "this observation was taken under a different coordinator",
        )
    now = _settled_epoch(
        fence,
        what="report its epoch",
        odd_code=FenceFailureCode.MUTATION_IN_FLIGHT_AT_EXIT,
        odd_detail="a mutation is in flight, so this observation cannot be confirmed",
    )
    if now < value.window.entry_epoch:
        raise _fail(
            FenceFailureCode.EPOCH_WENT_BACKWARDS,
            "the fence epoch decreased, which an append-only world cannot do",
        )
    if now != value.window.entry_epoch:
        raise _fail(
            FenceFailureCode.OBSERVATION_TORN,
            "an authority store has moved since this observation was taken",
        )
    return value.head_set


def settle_after_own_mutation(
    value: FencedAuthorityState,
    *,
    fence: SnapshotFencePort,
    own_intervals: int,
) -> int:
    """Confirm the world moved by exactly this transaction's own writes, and stop.

    ``require_untorn_state`` asks whether nothing has changed, which is the right
    question right up until the caller changes something itself. The point of use
    reads the world, decides against it and then *appends its own decision*: after
    that the epoch is necessarily two higher per interval it opened, and demanding
    equality would report the transaction's own write as external interference.

    So this asks the sharper question. The epoch must be exactly the entry epoch
    plus two per interval opened here — no more, because a larger value means some
    other writer landed inside the transaction; no less, because a smaller one
    means an interval this transaction believes it closed did not. Both are torn
    observations and neither is repairable, so both refuse.

    The returned value is the epoch the result binds to: the final even one, after
    the append, not the entry epoch that described a world this transaction has
    since changed.
    """

    validate_fenced_authority_state(value)
    require_snapshot_fence(fence)
    if type(own_intervals) is not int or own_intervals < 0:
        raise _fail(FenceFailureCode.TYPE_MISMATCH, "an interval count must be an exact count")
    if _call(fence.coordinator_id, str, "report its coordinator") != value.window.coordinator_id:
        raise _fail(
            FenceFailureCode.COORDINATOR_MISMATCH,
            "this observation was taken under a different coordinator",
        )
    now = _settled_epoch(
        fence,
        what="report its epoch",
        odd_code=FenceFailureCode.MUTATION_IN_FLIGHT_AT_EXIT,
        odd_detail="a mutation is in flight, so this transaction cannot be settled",
    )
    expected = value.window.entry_epoch + 2 * own_intervals
    if now < value.window.entry_epoch:
        raise _fail(
            FenceFailureCode.EPOCH_WENT_BACKWARDS,
            "the fence epoch decreased, which an append-only world cannot do",
        )
    if now != expected:
        raise _fail(
            FenceFailureCode.OBSERVATION_TORN,
            "a store other than this transaction's own moved during it",
        )
    return now


def settle_exclusive_mutation(
    *,
    fence: SnapshotFencePort,
    coordinator_id: str,
    entry_epoch: int,
    own_intervals: int,
) -> int:
    """Settle a guarded write that does not require a six-head boundary read.

    Publication precedes the attempt boundary whose candidate universe will
    later include it, so it cannot manufacture an ``AuthorityHeadSet`` merely
    to obtain an epoch bracket.  Its fresh authority facts are the gate probes
    evaluated while the coordinator is exclusively held.  This helper verifies
    the same exact coordinator/epoch transition used by point of use without
    pretending a pre-publication boundary already exists.
    """

    require_snapshot_fence(fence)
    if type(coordinator_id) is not str or not coordinator_id:
        raise _fail(FenceFailureCode.TYPE_MISMATCH, "a coordinator identity is required")
    if type(entry_epoch) is not int or entry_epoch < 0 or entry_epoch % 2:
        raise _fail(FenceFailureCode.TYPE_MISMATCH, "entry epoch must be exact and settled")
    if type(own_intervals) is not int or own_intervals < 0:
        raise _fail(FenceFailureCode.TYPE_MISMATCH, "an interval count must be exact")
    if _call(fence.coordinator_id, str, "report its coordinator") != coordinator_id:
        raise _fail(FenceFailureCode.COORDINATOR_MISMATCH, "the guarded write changed coordinators")
    now = _settled_epoch(
        fence,
        what="report its epoch",
        odd_code=FenceFailureCode.MUTATION_IN_FLIGHT_AT_EXIT,
        odd_detail="the guarded write left a mutation in flight",
    )
    if now != entry_epoch + 2 * own_intervals:
        raise _fail(
            FenceFailureCode.OBSERVATION_TORN,
            "the guarded write did not close exactly its own mutation intervals",
        )
    return now


def require_exact_history_advancements(
    before: Mapping[str, tuple[str, int]],
    after: Mapping[str, tuple[str, int]],
    *,
    expected_advances: Mapping[str, int],
    entry_epoch: int,
    final_epoch: int,
) -> None:
    """Prove that one interval changed exactly its declared durable histories."""

    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        raise _fail(FenceFailureCode.TYPE_MISMATCH, "authority cursors must be mappings")
    if set(before) != set(after) or set(before) != set(expected_advances):
        raise _fail(FenceFailureCode.TYPE_MISMATCH, "authority cursor domains differ")
    if (
        type(entry_epoch) is not int
        or type(final_epoch) is not int
        or entry_epoch < 0
        or final_epoch != entry_epoch + 2
        or final_epoch % 2
    ):
        raise _fail(
            FenceFailureCode.OBSERVATION_TORN,
            "one atomic authority write must close exactly one mutation interval",
        )
    for domain in before:
        pre = before[domain]
        post = after[domain]
        advance = expected_advances[domain]
        if (
            type(pre) is not tuple
            or type(post) is not tuple
            or len(pre) != 2
            or len(post) != 2
            or type(pre[0]) is not str
            or type(post[0]) is not str
            or type(pre[1]) is not int
            or type(post[1]) is not int
            or type(advance) is not int
            or advance < 0
        ):
            raise _fail(FenceFailureCode.TYPE_MISMATCH, "authority cursor is malformed")
        if post[1] != pre[1] + advance:
            raise _fail(
                FenceFailureCode.OBSERVATION_TORN,
                f"{domain} history did not advance by its exact own record count",
            )
        if advance == 0 and post[0] != pre[0]:
            raise _fail(
                FenceFailureCode.OBSERVATION_TORN,
                f"{domain} changed although this transaction did not write it",
            )
        if advance > 0 and post[0] == pre[0]:
            raise _fail(
                FenceFailureCode.OBSERVATION_TORN,
                f"{domain} sequence advanced without a new durable anchor",
            )


__all__ = [
    "ADAPTER_PRIVATE_SEAM",
    "CoordinatedReadWindow",
    "FenceFailureCode",
    "FenceViolation",
    "FencedAuthorityState",
    "SnapshotFencePort",
    "read_current_authority_state",
    "require_snapshot_fence",
    "require_exact_history_advancements",
    "require_untorn_state",
    "settle_after_own_mutation",
    "settle_exclusive_mutation",
    "validate_read_window",
    "validate_fenced_authority_state",
]
