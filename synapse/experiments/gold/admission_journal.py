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
monotonic epoch that mutating components advance through `bump`, plus advisory
leases. It makes a torn cross-store read *detectable* by any reader that takes
the epoch before and after, and that is what fail-closed needs. It is not a
distributed lock: it does not prevent a concurrent writer, and it cannot detect
one that mutates a store without bumping the epoch. Both limits are properties
of this implementation and are stated rather than left to be discovered.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import os
from pathlib import Path

from .persistence import (
    MAX_JOURNAL_FRAMES_V1,
    PersistenceFailureCode,
    PersistenceViolation,
    append_journal_payload,
    atomic_replace_metadata,
    ensure_directory,
    read_regular_bytes,
    scan_journal,
)

#: The genesis value the anchor chain starts from. Domain-separated so an anchor
#: from this journal cannot collide with a digest from anywhere else.
JOURNAL_GENESIS = b"synapse.stage4.gold.admission-journal-genesis/v1"

#: The leaf names this adapter owns inside a fence directory.
FENCE_EPOCH_JOURNAL_NAME = "fence.journal"
FENCE_LEASE_NAME = "lease"

MAX_LEASE_ID_BYTES = 256


class JournalAdapterFailureCode(str, Enum):
    """Why a filesystem-backed port refused."""

    TYPE_MISMATCH = "TYPE_MISMATCH"
    RECORD_ABSENT = "RECORD_ABSENT"
    JOURNAL_CORRUPT = "JOURNAL_CORRUPT"
    EPOCH_CORRUPT = "EPOCH_CORRUPT"
    EPOCH_EXHAUSTED = "EPOCH_EXHAUSTED"


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
    """

    path: Path

    def _digests(self) -> tuple[str, ...]:
        result = _scan_or_classify(
            self.path,
            corrupt_code=JournalAdapterFailureCode.JOURNAL_CORRUPT,
            what="the admission journal",
        )
        return tuple(hashlib.sha256(frame.payload).hexdigest() for frame in result.frames)

    def append_record(self, payload: bytes) -> None:
        if type(payload) is not bytes or not payload:
            raise _fail(
                JournalAdapterFailureCode.TYPE_MISMATCH,
                "a journal record must be non-empty bytes",
            )
        try:
            ensure_directory(self.path.parent)
            append_journal_payload(self.path, payload)
        except PersistenceViolation as exc:
            if exc.failure_code is PersistenceFailureCode.JOURNAL_MAGIC_MISMATCH:
                raise _fail(
                    JournalAdapterFailureCode.JOURNAL_CORRUPT,
                    "the admission journal path holds a file this adapter did not write",
                ) from exc
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
    """A monotonic epoch and advisory leases, both on disk.

    The epoch is one counter across every authority store, because a per-store
    counter cannot tell a reader that lifecycle moved while taint was being read.
    Advancing it is the writers' job: ``bump`` is what a mutating component calls,
    and a store that mutates without calling it is invisible to this fence.

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

    @property
    def _lease_path(self) -> Path:
        return self.directory / FENCE_LEASE_NAME

    def _read_epoch(self) -> int:
        result = _scan_or_classify(
            self._epoch_path,
            corrupt_code=JournalAdapterFailureCode.EPOCH_CORRUPT,
            what="the snapshot fence epoch journal",
        )
        return len(result.frames)

    def bump(self) -> int:
        """Advance the epoch. Called by whatever just mutated an authority store."""

        current = self._read_epoch()
        if current >= MAX_JOURNAL_FRAMES_V1:
            raise _fail(
                JournalAdapterFailureCode.EPOCH_EXHAUSTED,
                "the fence epoch journal is full and can no longer record a mutation",
            )
        try:
            append_journal_payload(self._epoch_path, os.urandom(16))
        except PersistenceViolation as exc:
            raise _unavailable("the snapshot fence could not be advanced") from exc
        except OSError as exc:
            raise _unavailable("the snapshot fence could not be advanced") from exc
        return self._read_epoch()

    def acquire_lease(self) -> str:
        """Take an advisory lease.

        Advisory, and named so: this does not exclude a second holder, and it is
        not asked to. The coordinated read detects interference by comparing the
        epoch at entry and exit, so exclusion would add a way to deadlock an
        authority read without adding a property the algorithm relies on. What
        the lease provides is an identity for one read window, so the two epoch
        readings provably belong to the same attempt.
        """

        lease_id = hashlib.sha256(os.urandom(32)).hexdigest()
        try:
            ensure_directory(self.directory)
            atomic_replace_metadata(
                self.directory,
                final_name=FENCE_LEASE_NAME,
                value=lease_id.encode("ascii"),
                maximum_bytes=MAX_LEASE_ID_BYTES,
            )
        except PersistenceViolation as exc:
            raise _unavailable("the snapshot fence could not issue a lease") from exc
        except OSError as exc:
            raise _unavailable("the snapshot fence could not issue a lease") from exc
        return lease_id

    def current_epoch(self) -> int:
        return self._read_epoch()

    def release_lease(self, lease_id: str) -> None:
        """Drop the recorded window, but only if it is still this one.

        A lease that was superseded is released by doing nothing. Unlinking
        unconditionally would let a slow holder erase the record of a window it
        no longer owns, which would make the file describe a moment that never
        happened.
        """

        if type(lease_id) is not str or not lease_id or len(lease_id) > MAX_LEASE_ID_BYTES:
            raise _fail(JournalAdapterFailureCode.TYPE_MISMATCH, "lease id is invalid")
        try:
            if not self._lease_path.exists():
                return
            recorded = read_regular_bytes(self._lease_path, maximum_bytes=MAX_LEASE_ID_BYTES)
            if recorded != lease_id.encode("ascii"):
                return
            self._lease_path.unlink()
        except FileNotFoundError:
            return
        except PersistenceViolation as exc:
            raise _unavailable("the snapshot fence could not release the lease") from exc
        except OSError as exc:
            raise _unavailable("the snapshot fence could not release the lease") from exc


__all__ = [
    "FENCE_EPOCH_JOURNAL_NAME",
    "FENCE_LEASE_NAME",
    "JOURNAL_GENESIS",
    "FileAdmissionJournal",
    "FileSnapshotFence",
    "JournalAdapterFailureCode",
    "JournalAdapterViolation",
]
