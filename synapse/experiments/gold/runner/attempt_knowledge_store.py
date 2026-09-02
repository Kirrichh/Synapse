"""Keep an attempt's knowledge basis in the run's own record store.

The owner in ``attempt_knowledge`` states what a basis is and what it means for
one attempt to follow another. It does not know where bytes live, and must not:
a module that both decided continuation and chose its own storage could answer
the question from a place nobody else can check.

So this is the adapter, and it is deliberately thin. It converts between the
sealed record and the canonical mapping the store holds, turns a missing key
into ``None`` rather than an exception -- a first attempt legitimately has no
predecessor -- and leaves every rule about what a basis *means* on the owner's
side.

The basis lives in ``RunRecordStore``, as one more kind of run record, because a
run's records already are content-addressed, fenced and audited there. A private
store beside it would be a second place a run's history lives, and recovery
would have to know about both.
"""

from __future__ import annotations

from synapse.experiments.gold.persistence import StoreMutationTicket

from .attempt_knowledge import (
    AttemptKnowledgeBasis,
    basis_from_payload,
)
from .records import RecordKind, RunRecordStore
from .vocabulary import GoldRunFailureCode, GoldRunViolation


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


def basis_record_key(attempt_index: int) -> str:
    """One basis per attempt, named by the attempt it describes."""

    if type(attempt_index) is not int or attempt_index < 1:
        raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt index must be one-based")
    return f"attempt-{attempt_index}"


class RunRecordAttemptKnowledgeBasisStore:
    """The production ``AttemptKnowledgeBasisPort`` over one run's record store."""

    def __init__(self, store: RunRecordStore) -> None:
        if type(store) is not RunRecordStore:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run record store must be exact")
        self._store = store

    def put_basis(self, basis: AttemptKnowledgeBasis, *, ticket: StoreMutationTicket) -> str:
        """Publish this attempt's basis and return the digest that names it.

        The store refuses a second, different payload under one key, so an
        attempt cannot quietly restate what it admitted after the fact.
        """

        if type(basis) is not AttemptKnowledgeBasis:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "basis must be exact")
        return self._store.put(
            kind=RecordKind.ATTEMPT_KNOWLEDGE_BASIS,
            key=basis_record_key(basis.attempt_index),
            canonical_payload=basis.payload(),
            ticket=ticket,
        )

    def get_basis(self, *, attempt_index: int) -> tuple[AttemptKnowledgeBasis, str] | None:
        """Read one attempt's basis back, or report that none was written.

        ``None`` means no record, which is the honest answer for a run's first
        attempt. Anything present but unreadable raises instead: a basis that
        cannot be validated is not an absent one, and the caller must be able to
        tell those apart to stay fail-closed.
        """

        stored = self._store.get(
            kind=RecordKind.ATTEMPT_KNOWLEDGE_BASIS, key=basis_record_key(attempt_index)
        )
        if stored is None:
            return None
        basis = basis_from_payload(stored.payload)
        if basis.attempt_index != attempt_index:
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "the stored basis describes another attempt",
            )
        return basis, stored.sha256


__all__ = ["RunRecordAttemptKnowledgeBasisStore", "basis_record_key"]
