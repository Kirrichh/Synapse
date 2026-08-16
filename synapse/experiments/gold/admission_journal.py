"""Filesystem-backed implementations of the admission ports.

Until now `DecisionJournalPort`, `AdmissionHistoryPort` and `SnapshotFencePort`
existed only as Protocols. Every algorithm built on them was exercised against
classes defined inside test files, which means the durability and coordination
properties were demonstrated against objects that keep their records in a Python
list. NR-06 draws the line exactly there: tests are an acceptance layer and must
not be where a production semantic lives. A journal that has never touched a
filesystem has not been shown to survive a restart, and a fence backed by an
attribute cannot detect anything a second process does.

So this owner supplies the concrete side. It invents no durability of its own:
`persistence` already provides an append-only journal with framing, `fsync`, a
magic header, torn-tail detection and byte limits, and this is an adapter over
those primitives rather than a second implementation of them.

The Protocols are structural, so nothing here imports `admission`,
`admission_store` or `coordination` — the classes satisfy the ports by having
the right methods, which keeps this module free of any dependency on the
semantics it serves.

**What the fence does and does not promise.** `FileSnapshotFence` publishes a
stable coordinator identity and a monotonic epoch that is *odd while a mutation
interval is open*. A reader that takes the epoch before and after its work can
therefore detect a torn cross-store read, which is what fail-closed needs.

It is still not a distributed lock — it does not prevent a concurrent writer.
What has changed is the second limit. This module used to state that it could
not detect a store that mutated without advancing the epoch, and that limit was
load-bearing: four of the six authority mutation sites did exactly that. The
epoch is no longer something a writer advances by remembering to. Opening an
interval yields a ticket, every authority-mutating primitive in `persistence`
requires one, and a writer that skips the protocol does not write unfenced — it
does not write. Two writes are exempt and are named for it
(`append_coordinator_epoch_frame`, `publish_coordinator_metadata`): they are the
coordinator's own bookkeeping, and requiring a ticket to establish the identity a
ticket carries would be a cycle rather than a safeguard.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import secrets
from enum import Enum
import hashlib
import os
from pathlib import Path

from .persistence import (
    ExclusiveStoreLock,
    MAX_JOURNAL_FRAMES_V1,
    PersistenceFailureCode,
    PersistenceViolation,
    StoreMutationFencePort,
    StoreMutationTicket,
    _close_store_mutation_ticket,
    _mint_store_mutation_ticket,
    append_coordinator_epoch_frame,
    append_journal_payload,
    ensure_directory,
    publish_coordinator_metadata,
    read_regular_bytes,
    require_ticket_of_coordinator,
    scan_journal,
    store_transaction,
)

#: The genesis value the anchor chain starts from. Domain-separated so an anchor
#: from this journal cannot collide with a digest from anywhere else.
JOURNAL_GENESIS = b"synapse.stage4.gold.admission-journal-genesis/v1"

#: The leaf names this adapter owns inside a fence directory.
FENCE_EPOCH_JOURNAL_NAME = "fence.journal"
FENCE_IDENTITY_NAME = "coordinator.id"
FENCE_LOCK_NAME = "coordinator.lock"


class JournalAdapterFailureCode(str, Enum):
    """Why a filesystem-backed port refused."""

    TYPE_MISMATCH = "TYPE_MISMATCH"
    RECORD_ABSENT = "RECORD_ABSENT"
    JOURNAL_CORRUPT = "JOURNAL_CORRUPT"
    EPOCH_CORRUPT = "EPOCH_CORRUPT"
    EPOCH_EXHAUSTED = "EPOCH_EXHAUSTED"
    #: The record is durable and the shared mutation epoch did not move, so a
    #: fenced reader cannot see that this store changed. Not an outage: a retry
    #: would append a second copy of a decision that already landed.
    FENCE_NOT_ADVANCED = "FENCE_NOT_ADVANCED"
    #: A caller offered no open interval for its append. A wiring defect, not an
    #: outage: retrying reproduces it forever, and reporting it as unavailable
    #: sends an operator to look at a filesystem that is working perfectly.
    MUTATION_NOT_FENCED = "MUTATION_NOT_FENCED"
    #: The ticket was valid once and its interval has closed. Kept apart from
    #: MUTATION_NOT_FENCED because the fix is in a different place: this caller
    #: has a lifetime bug, not a missing transaction.
    MUTATION_INTERVAL_CLOSED = "MUTATION_INTERVAL_CLOSED"
    #: The interval belongs to another coordinator, so it says nothing about this
    #: journal's readers. Answering "unavailable" would have hidden the one
    #: arrangement this round exists to make impossible.
    MUTATION_COORDINATOR_MISMATCH = "MUTATION_COORDINATOR_MISMATCH"
    #: A writer tried to open an interval while one was already open — a nested
    #: or concurrent transaction, or a crash that left the epoch odd. Distinct
    #: from MUTATION_ABORTED, which names the abandonment itself: this is what
    #: the *next* writer is told, and it is told to stop rather than to build on
    #: a store whose state nobody has established.
    MUTATION_INTERVAL_OPEN = "MUTATION_INTERVAL_OPEN"
    #: A recovery was attempted on a coordinator that is already settled. Refused
    #: rather than treated as a no-op: a recovery that quietly did nothing would
    #: let a caller believe it had closed an interval that some other writer is
    #: about to open.
    MUTATION_INTERVAL_NOT_OPEN = "MUTATION_INTERVAL_NOT_OPEN"
    #: A mutation interval was opened and abandoned. The store may have changed
    #: partially, so the epoch stays odd and readers keep refusing until an
    #: explicit recovery decides what happened. Distinct from FENCE_NOT_ADVANCED,
    #: where the write demonstrably landed and only the mark is missing.
    MUTATION_ABORTED = "MUTATION_ABORTED"


class JournalAdapterViolation(RuntimeError):
    """A typed adapter error carrying no record payload."""

    def __init__(self, failure_code: JournalAdapterFailureCode, detail: str) -> None:
        if type(failure_code) is not JournalAdapterFailureCode:
            raise TypeError("failure_code must be an exact JournalAdapterFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: JournalAdapterFailureCode, detail: str) -> JournalAdapterViolation:
    return JournalAdapterViolation(code, detail)


def _unavailable(detail: str):
    """Report an outage the way the gates declare it.

    The gates distinguish a store that could not be reached from an adapter that
    broke its contract, and they do it by exception type. An adapter that raised
    its own error for an outage would be classified as broken, which sends an
    incident analysis to the wrong place — so the declared type is imported
    lazily here rather than making this module depend on the gate owner.
    """

    from .admission import GateDependencyUnavailable

    return GateDependencyUnavailable(detail)


def _scan_or_classify(path: Path, *, corrupt_code: JournalAdapterFailureCode, what: str):
    """Scan a journal, keeping *unreachable* and *not ours* apart.

    A store that could not be read is an outage and the caller may retry it. A
    file that is present and does not carry our magic is a different fact: the
    path holds something else, and no amount of retrying changes that. NR-10
    forbids collapsing the two, so the magic mismatch is reported as corruption
    under this adapter's own type while every other read failure is declared as
    a dependency outage.

    The directory is provisioned on the read path deliberately. A journal that
    has never been appended to has an empty committed history, and an empty
    history is a definite answer — the genesis anchor — not an outage. Reporting
    it as unreachable would be the same substitution NR-10 forbids, only in the
    other direction. `persistence` takes the same view: `scan_journal` itself
    initializes the file it is asked to read.
    """

    try:
        ensure_directory(path.parent)
        return scan_journal(path)
    except PersistenceViolation as exc:
        if exc.failure_code is PersistenceFailureCode.JOURNAL_MAGIC_MISMATCH:
            raise _fail(corrupt_code, f"{what} is present but is not this adapter's journal") from exc
        raise _unavailable(f"{what} could not be read") from exc
    except OSError as exc:
        raise _unavailable(f"{what} could not be read") from exc


def _anchor_chain(digests: tuple[str, ...]) -> tuple[str, ...]:
    """Every prefix anchor, oldest first, ending with the current one.

    One walk rather than one per prefix: `extends` asks whether an anchor is any
    prefix of the committed history, and recomputing the whole chain per prefix
    turns a linear question into a quadratic one on a path a gate takes on every
    admission.
    """

    running = hashlib.sha256(JOURNAL_GENESIS).digest()
    chain = [running.hex()]
    for digest in digests:
        running = hashlib.sha256(running + bytes.fromhex(digest)).digest()
        chain.append(running.hex())
    return tuple(chain)


@dataclass(frozen=True)
class FileAdmissionJournal:
    """An append-only decision journal on disk.

    Satisfies both `DecisionJournalPort` and `AdmissionHistoryPort` structurally.
    Every read rescans the file rather than caching: a cached view is a snapshot
    of the past presented as the present, and the whole point of asking a journal
    whether it still contains a record is that the answer can have changed.

    `mutation_fence` is required for the reason it is required of every other
    authority store: this journal is one of the six surfaces a fenced §22 head
    capture reads, and a decision appended without advancing the epoch is a
    mutation the coordinated read cannot see.
    """

    path: Path
    mutation_fence: StoreMutationFencePort

    def _digests(self) -> tuple[str, ...]:
        result = _scan_or_classify(
            self.path,
            corrupt_code=JournalAdapterFailureCode.JOURNAL_CORRUPT,
            what="the admission journal",
        )
        return tuple(hashlib.sha256(frame.payload).hexdigest() for frame in result.frames)

    def append_record(self, payload: bytes, *, ticket: StoreMutationTicket | None = None) -> None:
        """Append one decision record, inside an interval.

        ``ticket`` is how a larger transaction says "this append is part of me".
        The point of use appends a fresh Consumption Gate decision in the middle of
        a transaction it already owns; opening a second interval there would drive
        the epoch back to even *inside* that transaction and let a reader observe
        the half-finished middle as a settled world. So the outer ticket is
        threaded in and verified rather than re-derived — and verified against this
        store's own coordinator, because a ticket from somewhere else describes an
        interval this journal's readers never see.

        With no ticket the append is its own whole transaction and opens its own
        interval. That is not a bypass: it is the same protocol with a transaction
        of one step.
        """

        if type(payload) is not bytes or not payload:
            raise _fail(
                JournalAdapterFailureCode.TYPE_MISMATCH,
                "a journal record must be non-empty bytes",
            )
        # Everything that can be established by *reading* happens before the
        # interval opens. A path holding somebody else's file, or a store that
        # cannot be reached at all, is a fact about the world, and finding it out
        # from inside an interval would bury a precise diagnosis under an abort —
        # and would wedge the coordinator on odd for a write that provably never
        # started. Only the write itself belongs inside.
        self._digests()
        try:
            ensure_directory(self.path.parent)
            if ticket is None:
                with store_transaction(self.mutation_fence) as own:
                    append_journal_payload(self.path, payload, ticket=own)
            else:
                require_ticket_of_coordinator(
                    ticket, coordinator_id=self.mutation_fence.coordinator_id()
                )
                append_journal_payload(self.path, payload, ticket=ticket)
        except PersistenceViolation as exc:
            if exc.failure_code is PersistenceFailureCode.JOURNAL_MAGIC_MISMATCH:
                raise _fail(
                    JournalAdapterFailureCode.JOURNAL_CORRUPT,
                    "the admission journal path holds a file this adapter did not write",
                ) from exc
            if exc.failure_code is PersistenceFailureCode.FENCE_NOT_ADVANCED:
                # Not an outage: the record is durable. What failed is the part
                # that lets a concurrent fenced read notice it, and calling that
                # unavailable would invite a retry that appends a second copy.
                raise _fail(
                    JournalAdapterFailureCode.FENCE_NOT_ADVANCED,
                    "the decision landed but the mutation fence did not advance",
                ) from exc
            # A defective interval is a wiring fault and must not be dressed as an
            # outage. Each of the three keeps its own code, because "you opened no
            # transaction", "your transaction has ended" and "your transaction
            # belongs to someone else" are fixed in three different places.
            protocol = {
                PersistenceFailureCode.MUTATION_NOT_FENCED: (
                    JournalAdapterFailureCode.MUTATION_NOT_FENCED,
                    "the append was offered no open mutation interval",
                ),
                PersistenceFailureCode.MUTATION_INTERVAL_CLOSED: (
                    JournalAdapterFailureCode.MUTATION_INTERVAL_CLOSED,
                    "the mutation interval offered for the append has already closed",
                ),
                PersistenceFailureCode.MUTATION_COORDINATOR_MISMATCH: (
                    JournalAdapterFailureCode.MUTATION_COORDINATOR_MISMATCH,
                    "the mutation interval belongs to a different coordinator",
                ),
            }.get(exc.failure_code)
            if protocol is not None:
                raise _fail(*protocol) from exc
            raise _unavailable("the admission journal refused the append") from exc
        except OSError as exc:
            raise _unavailable("the admission journal refused the append") from exc

    def contains_record(self, digest: str) -> bool:
        return digest in self._digests()

    def current_anchor(self) -> str:
        return _anchor_chain(self._digests())[-1]

    def extends(self, anchor: str) -> bool:
        """True when ``anchor`` is a prefix of the committed history.

        Extension rather than equality, because a journal legitimately grows: a
        receipt witnessed the anchor at its own commit, and later appends must
        not invalidate it. What must invalidate it is a *fork* — a history
        rebuilt in another order, where the record survives but the anchor it
        witnessed is no longer a prefix.
        """

        return anchor in _anchor_chain(self._digests())

    def record_position(self, digest: str) -> int:
        """Where the record sits, or a typed refusal saying it is not there.

        Absence is reported under this adapter's own type rather than as a
        dependency outage. The journal was read successfully and gave a definite
        answer; calling that "unavailable" would tell the caller to retry a
        question that is already settled, which is exactly the substitution NR-10
        forbids.
        """

        for index, item in enumerate(self._digests()):
            if item == digest:
                return index
        raise _fail(
            JournalAdapterFailureCode.RECORD_ABSENT,
            "the record is not in the committed history",
        )


@dataclass(frozen=True)
class FileSnapshotFence:
    """The coordinator: one identity, one epoch, one way to open an interval.

    The epoch is one counter across every authority store, because a per-store
    counter cannot tell a reader that lifecycle moved while taint was being read.
    Advancing it is not the writers' job any more — writers no longer *call*
    anything to advance it. They open a transaction here, receive a ticket, and
    the primitives refuse to write without one. The earlier arrangement, where a
    store politely called ``bump``, made "invisible to this fence" a thing a
    writer could choose.

    The counter is the frame count of an append-only journal rather than a number
    in a file. A read-modify-write counter is not monotonic under concurrency —
    two writers both read *n* and both write *n+1*, and the second mutation
    becomes invisible to exactly the readers the fence exists to protect. An
    `O_APPEND` frame cannot be lost that way, so the count only ever rises. The
    cost is that reading the epoch scans the journal, which `persistence` bounds
    at `MAX_JOURNAL_FRAMES_V1` frames; past that this fence refuses rather than
    silently wrapping.
    """

    directory: Path

    @property
    def _epoch_path(self) -> Path:
        return self.directory / FENCE_EPOCH_JOURNAL_NAME

    def _read_epoch(self) -> int:
        result = _scan_or_classify(
            self._epoch_path,
            corrupt_code=JournalAdapterFailureCode.EPOCH_CORRUPT,
            what="the snapshot fence epoch journal",
        )
        return len(result.frames)

    def coordinator_id(self) -> str:
        """A stable identity for this coordinator, persisted beside its epoch.

        Minted once per directory and read thereafter, so two `FileSnapshotFence`
        objects over the same directory are the same coordinator and two over
        different directories never are. The check that uses it is what makes the
        earlier acceptance arrangement impossible: a real journal holding one
        fence while the reader was handed an unrelated double.
        """

        try:
            ensure_directory(self.directory)
        except PersistenceViolation as exc:
            # A coordinator directory that cannot be provisioned is an outage,
            # declared the way the gates classify one. Letting the persistence
            # error through would file a missing mount as a broken adapter.
            raise _unavailable("the coordinator directory could not be reached") from exc
        path = self.directory / FENCE_IDENTITY_NAME
        if not path.exists():
            try:
                # The one write that cannot be fenced, through the primitive named
                # for that exemption: a ticket carries a coordinator identity, and
                # this is the call that establishes one. Fencing it would be a
                # cycle, so the exemption is made visible instead of implicit.
                publish_coordinator_metadata(
                    self.directory,
                    final_name=FENCE_IDENTITY_NAME,
                    value=secrets.token_hex(16).encode("ascii"),
                )
            except PersistenceViolation as exc:
                raise _unavailable("the coordinator identity could not be written") from exc
        try:
            return read_regular_bytes(path, maximum_bytes=64).decode("ascii")
        except (PersistenceViolation, UnicodeDecodeError) as exc:
            raise _fail(
                JournalAdapterFailureCode.EPOCH_CORRUPT,
                "the coordinator identity file is not one this adapter wrote",
            ) from exc

    @property
    def lock_path(self) -> Path:
        """Where a writer takes exclusion for a whole critical section."""

        return self.directory / FENCE_LOCK_NAME

    @contextmanager
    def exclusive(self):
        """Hold this coordinator's writer lock for a whole critical section.

        The epoch makes interference *visible*; this makes it *impossible* for
        the span it covers, and the two are needed for different reasons. A
        read-decide-write sequence that only detects interference can detect it
        forever without ever completing, and the point of use is exactly such a
        sequence: it reads the world, decides against it, and writes a decision
        that is only meaningful if the world did not move in between.

        Exposed as a context manager rather than as ``lock_path`` so that nothing
        outside this adapter has to know what kind of lock it is, and so that a
        caller cannot take it twice by taking it under two names.
        """

        ensure_directory(self.directory)
        with ExclusiveStoreLock(self.lock_path):
            yield

    @contextmanager
    def mutating(self):
        """Open one mutation interval and hand out the ticket that authorises it.

        Two frames, one on each side, so the epoch is **odd for exactly as long as
        the write is in flight**. That is the seqlock invariant, and it is what the
        earlier `bump` could not provide: a count advanced after an append lets a
        reader take its entry epoch, observe the appended record, take its exit
        epoch and find no change, because the mark had not happened yet. Marking
        the instant *before* instead opens the symmetric window. Only marking the
        interval closes both.

        The yielded ticket is what makes the interval enforceable rather than
        advisory. Every authority primitive requires one, so a writer that skips
        this context manager does not write unfenced — it does not write.

        A writer that dies inside leaves the count odd. That is deliberate and it
        is the fail-closed answer: a store whose last write neither completed nor
        rolled back is not a store a reader may combine with others.
        """

        # Refuse to open on an odd epoch, and refuse for two reasons that happen
        # to have the same fix. An interval already open means a nested or
        # concurrent transaction, and marking again would drive the count *even*
        # in the middle of both — the counter would then advertise a settled
        # store precisely when two writers are inside it. An interval left odd by
        # a crash means the store's last write neither completed nor rolled back,
        # and continuing on top of it would bury that fact under a fresh write.
        # Either way the coordinator stays closed until an explicit recovery has
        # looked, which is what fail-closed means here.
        if self._read_epoch() % 2:
            raise _fail(
                JournalAdapterFailureCode.MUTATION_INTERVAL_OPEN,
                "a mutation interval is already open and was never closed",
            )
        opened = self._mark("open")
        ticket = _mint_store_mutation_ticket(
            coordinator_id=self.coordinator_id(), interval_epoch=opened
        )
        try:
            yield ticket
        except (KeyboardInterrupt, SystemExit):
            # A process being terminated is not this module's news to reclassify.
            # The interval is left open, which is the whole fail-closed answer,
            # and the signal travels on untouched.
            _close_store_mutation_ticket(ticket)
            raise
        except BaseException as exc:
            # The ticket dies with the interval even though the interval stays
            # open. Those are not in tension: the odd epoch is a *statement to
            # readers* that the store is unsettled, while the ticket is a
            # *capability*, and leaving a capability alive after its transaction
            # failed would let the caller's `except` clause keep writing under an
            # interval nobody will ever close.
            _close_store_mutation_ticket(ticket)
            # Deliberately *not* closed. The interval stays open, the epoch stays
            # odd, and every reader keeps refusing until someone looks.
            #
            # An earlier revision closed in a `finally`, which is the reflex and is
            # wrong here: a mutation that raised may have changed the store
            # partially, and returning the epoch to even tells every reader the
            # store is settled. The caller saw its exception; the readers saw a
            # quiet world. Nothing in this module can know whether the write
            # landed, so it must not answer as though it did.
            #
            # This makes an ordinary caught exception as blocking as a crash, and
            # that is the intended cost: the two are indistinguishable from
            # outside, so they get the same fail-closed answer until an explicit
            # recovery decides.
            raise _fail(
                JournalAdapterFailureCode.MUTATION_ABORTED,
                "a mutation was abandoned mid-interval and the store state is unknown",
            ) from exc
        _close_store_mutation_ticket(ticket)
        self._mark("close")

    def _mark(self, phase: str) -> int:
        current = self._read_epoch()
        if current >= MAX_JOURNAL_FRAMES_V1:
            raise _fail(
                JournalAdapterFailureCode.EPOCH_EXHAUSTED,
                "the fence epoch journal is full and can no longer record a mutation",
            )
        try:
            append_coordinator_epoch_frame(
                self._epoch_path, phase.encode("ascii") + os.urandom(16)
            )
        except PersistenceViolation as exc:
            raise _unavailable("the snapshot fence could not be marked") from exc
        except OSError as exc:
            raise _unavailable("the snapshot fence could not be marked") from exc
        return self._read_epoch()

    def current_epoch(self) -> int:
        return self._read_epoch()

    def recover_abandoned_interval(self) -> int:
        """Close an interval whose writer never came back, deliberately.

        This is the "explicit recovery" the odd epoch waits for, and it is a
        separate public operation rather than something a store does on the way
        past for one reason: **the interval is shared**. If lifecycle crashed
        mid-write, the library reopening its own files has established nothing
        whatever about lifecycle's state, and letting it clear the mark would
        turn one component's local recovery into a global claim that everything
        is settled.

        So the caller must be a party that has established the state of every
        store under this coordinator — in practice an operator or a recovery
        procedure, not a store constructor. Nothing is repaired here. The mark
        records that somebody has looked, which is a different fact and the only
        one this module is in a position to write down.
        """

        if self._read_epoch() % 2 == 0:
            raise _fail(
                JournalAdapterFailureCode.MUTATION_INTERVAL_NOT_OPEN,
                "there is no abandoned interval to recover",
            )
        # A distinct phase name, so the epoch journal says which edges were
        # ordinary completions and which were somebody deciding an abandoned
        # transaction was over. An operator reading it can tell them apart.
        return self._mark("recover")


__all__ = [
    "FENCE_EPOCH_JOURNAL_NAME",
    "JOURNAL_GENESIS",
    "FileAdmissionJournal",
    "FileSnapshotFence",
    "JournalAdapterFailureCode",
    "JournalAdapterViolation",
]
