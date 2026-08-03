"""Durable lineage for the whole four-gate chain, not just its last decision.

§22 requires gate decisions to be immutable, persisted and linked in lineage.
What existed satisfied the first two for one decision: ``commit_gate_decision``
appended a payload and returned a receipt, and ``admit_for_consumption`` asked
for a receipt covering the consumption verdict alone. Ingestion, publication and
retrieval were never required to be durable at all. A ``GateDecisionChain``
linked them, but only in memory — the journal saw four opaque byte strings and
knew nothing about which gate produced them, what order they came in, or that
they belonged together.

That gap is not theoretical. A consumption ADMIT is only meaningful because
three earlier verdicts led to it; if those are absent from the durable record,
the lineage §22 asks for cannot be reconstructed after a restart, and an
auditor is left with a final answer and no working. Worse, nothing stopped a
chain being committed with its earlier decisions silently missing, because
nothing ever looked.

So this owner commits chains rather than decisions, and proves four things about
what it wrote:

*All four are present*, one per gate, in stage order.

*They are linked*: each decision's stored predecessor digest is the previous
decision's identity, re-checked against the durable bytes rather than the
in-memory objects.

*They are contiguous*: the four occupy consecutive positions in the journal.
Anchors alone cannot show this — a journal that grew between two appends still
extends both — so the store asks where each record sits, and a gap means
something was interleaved into the middle of a chain that is supposed to be one
transaction.

*They are still there*: the evidence is re-verified at the point of use against
the journal as it is now, so a rollback, a fork, or a truncation after the fact
invalidates the chain rather than being papered over by a receipt issued earlier.

This is an adapter of the admission owner: it imports ``admission`` and
``admission`` imports nothing from it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import Callable, Protocol, runtime_checkable

from .admission import (
    AdmissionFailureCode,
    AdmissionViolation,
    DecisionCommitReceipt,
    DecisionJournalPort,
    GateDecision,
    GateDecisionChain,
    GateDependencyUnavailable,
    commit_gate_decision,
    require_committed_decision,
    validate_commit_receipt,
    validate_gate_decision,
)
from .contracts import GateKind, gate_stage_index

#: The non-public surface this adapter takes from the admission owner. Nothing
#: yet — the chain semantics are built entirely on the public commit and
#: verification calls, which is the strongest form the seam can take.
ADAPTER_PRIVATE_SEAM: tuple[str, ...] = ()

_EVIDENCE_SEAL = object()

_GATE_ORDER = (GateKind.INGESTION, GateKind.PUBLICATION, GateKind.RETRIEVAL, GateKind.CONSUMPTION)


class HistoryFailureCode(str, Enum):
    """Why a chain's durable record was refused."""

    TYPE_MISMATCH = "TYPE_MISMATCH"
    TRUSTED_OBJECT_FORGED = "TRUSTED_OBJECT_FORGED"
    STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
    CHAIN_INCOMPLETE = "CHAIN_INCOMPLETE"
    CHAIN_OUT_OF_ORDER = "CHAIN_OUT_OF_ORDER"
    CHAIN_NOT_LINKED = "CHAIN_NOT_LINKED"
    CHAIN_NOT_CONTIGUOUS = "CHAIN_NOT_CONTIGUOUS"
    CHAIN_NOT_DURABLE = "CHAIN_NOT_DURABLE"
    HISTORY_ROLLED_BACK = "HISTORY_ROLLED_BACK"


class HistoryViolation(ValueError):
    """A typed, fail-closed history error carrying no subject payload."""

    def __init__(self, failure_code: HistoryFailureCode, detail: str) -> None:
        if type(failure_code) is not HistoryFailureCode:
            raise TypeError("failure_code must be an exact HistoryFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: HistoryFailureCode, detail: str) -> HistoryViolation:
    return HistoryViolation(code, detail)


@runtime_checkable
class AdmissionHistoryPort(Protocol):
    """A decision journal that can also say *where* a record sits.

    Position is what distinguishes contiguity from mere inclusion. Two records
    can both be in the committed history and still have a third between them,
    and for a chain that is supposed to be one transaction, that difference
    matters.
    """

    def append_record(self, payload: bytes) -> None: ...

    def contains_record(self, digest: str) -> bool: ...

    def current_anchor(self) -> str: ...

    def extends(self, anchor: str) -> bool: ...

    def record_position(self, digest: str) -> int: ...


def require_admission_history(value: object) -> AdmissionHistoryPort:
    if not isinstance(value, AdmissionHistoryPort):
        raise _fail(HistoryFailureCode.TYPE_MISMATCH, "store does not implement the history port")
    for name in ("append_record", "contains_record", "current_anchor", "extends", "record_position"):
        if not callable(getattr(value, name, None)):
            raise _fail(HistoryFailureCode.TYPE_MISMATCH, f"store is missing {name}")
    return value


def _position(store: AdmissionHistoryPort, digest: str) -> int:
    try:
        result = store.record_position(digest)
    except (HistoryViolation, AdmissionViolation):
        raise
    except GateDependencyUnavailable as exc:
        raise _fail(HistoryFailureCode.STORE_UNAVAILABLE, "the history store could not be read") from exc
    if type(result) is not int or type(result) is bool or result < 0:
        raise _fail(
            HistoryFailureCode.TYPE_MISMATCH,
            "record_position must return a non-negative exact int",
        )
    return result


@dataclass(frozen=True, init=False)
class ChainCommitEvidence:
    """Proof that all four decisions of one chain are durably recorded together.

    Carrying every receipt rather than only the last is the whole point: a
    consumption receipt says the final verdict was written, and says nothing
    about the three verdicts that made it meaningful.
    """

    receipts: tuple[DecisionCommitReceipt, ...]
    decision_digests: tuple[str, ...]
    first_position: int
    committed_at_utc: datetime
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ChainCommitEvidence:
        raise TypeError("ChainCommitEvidence is produced only by commit_gate_chain")

    @property
    def final_anchor(self) -> str:
        """The journal anchor witnessed after the last decision was written."""

        validate_chain_commit_evidence(self)
        return self.receipts[-1].journal_anchor

    def to_dict(self) -> dict[str, object]:
        validate_chain_commit_evidence(self)
        return {
            "receipts": [item.to_dict() for item in self.receipts],
            "decision_digests": list(self.decision_digests),
            "first_position": self.first_position,
            "committed_at_utc": self.committed_at_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }


def validate_chain_commit_evidence(value: ChainCommitEvidence) -> ChainCommitEvidence:
    if type(value) is not ChainCommitEvidence or getattr(value, "_trusted_seal", None) is not _EVIDENCE_SEAL:
        raise _fail(HistoryFailureCode.TRUSTED_OBJECT_FORGED, "chain evidence is not factory sealed")
    if type(value.receipts) is not tuple or len(value.receipts) != len(_GATE_ORDER):
        raise _fail(
            HistoryFailureCode.CHAIN_INCOMPLETE,
            "chain evidence must carry one receipt per gate",
        )
    for receipt in value.receipts:
        validate_commit_receipt(receipt)
    if type(value.decision_digests) is not tuple or len(value.decision_digests) != len(_GATE_ORDER):
        raise _fail(HistoryFailureCode.CHAIN_INCOMPLETE, "chain evidence must carry one digest per gate")
    if len(set(value.decision_digests)) != len(value.decision_digests):
        raise _fail(HistoryFailureCode.CHAIN_NOT_LINKED, "a chain cannot contain the same decision twice")
    if type(value.first_position) is not int or type(value.first_position) is bool or value.first_position < 0:
        raise _fail(HistoryFailureCode.TYPE_MISMATCH, "first position must be a non-negative exact int")
    if type(value.committed_at_utc) is not datetime or value.committed_at_utc.tzinfo is not timezone.utc:
        raise _fail(HistoryFailureCode.TYPE_MISMATCH, "commit timestamp must be exact UTC")
    for receipt, digest in zip(value.receipts, value.decision_digests):
        if receipt.decision_digest != digest:
            raise _fail(
                HistoryFailureCode.CHAIN_NOT_DURABLE,
                "a receipt does not belong to the decision it is filed against",
            )
    return value


def _chain_decisions(chain: GateDecisionChain) -> tuple[GateDecision, ...]:
    decisions = (chain.ingestion, chain.publication, chain.retrieval, chain.consumption)
    for index, (decision, gate) in enumerate(zip(decisions, _GATE_ORDER)):
        validate_gate_decision(decision)
        if decision.gate_kind is not gate:
            raise _fail(
                HistoryFailureCode.CHAIN_OUT_OF_ORDER,
                f"position {index} holds a {decision.gate_kind.value} decision",
            )
        # gate_stage_index is zero-based, so this compares against the position
        # rather than position+1. Getting that off by one made every valid chain
        # look out of order, which is at least the safe direction to be wrong in.
        if gate_stage_index(decision.gate_kind) != index:
            raise _fail(
                HistoryFailureCode.CHAIN_OUT_OF_ORDER,
                "the chain is not in §38 stage order",
            )
    for earlier, later in zip(decisions, decisions[1:]):
        if later.predecessor_decision_digest != earlier.gate_decision_id.digest_sha256:
            raise _fail(
                HistoryFailureCode.CHAIN_NOT_LINKED,
                "a decision does not name its predecessor",
            )
    return decisions


def commit_gate_chain(
    chain: GateDecisionChain,
    *,
    store: AdmissionHistoryPort,
    trusted_clock: Callable[[], datetime],
) -> ChainCommitEvidence:
    """Write all four decisions as one durable, contiguous, linked run.

    Order matters and is not incidental: the four are appended in stage order so
    that the durable record reads the way the reasoning ran. A journal that
    received them out of order would still contain them all, and would still be
    a record of something that did not happen.
    """

    require_admission_history(store)
    if not callable(trusted_clock):
        raise _fail(HistoryFailureCode.TYPE_MISMATCH, "trusted_clock must be callable")
    decisions = _chain_decisions(chain)

    receipts: list[DecisionCommitReceipt] = []
    digests: list[str] = []
    for decision in decisions:
        receipt = commit_gate_decision(decision, journal=store, trusted_clock=trusted_clock)
        receipts.append(receipt)
        digests.append(receipt.decision_digest)

    positions = [_position(store, digest) for digest in digests]
    if positions != list(range(positions[0], positions[0] + len(positions))):
        raise _fail(
            HistoryFailureCode.CHAIN_NOT_CONTIGUOUS,
            "the four decisions are not consecutive in the journal",
        )
    now = trusted_clock()
    if type(now) is not datetime or now.tzinfo is not timezone.utc:
        raise _fail(HistoryFailureCode.TYPE_MISMATCH, "trusted clock did not return exact UTC")

    evidence = object.__new__(ChainCommitEvidence)
    object.__setattr__(evidence, "receipts", tuple(receipts))
    object.__setattr__(evidence, "decision_digests", tuple(digests))
    object.__setattr__(evidence, "first_position", positions[0])
    object.__setattr__(evidence, "committed_at_utc", now)
    object.__setattr__(evidence, "_trusted_seal", _EVIDENCE_SEAL)
    return validate_chain_commit_evidence(evidence)


def require_committed_chain(
    evidence: ChainCommitEvidence,
    *,
    chain: GateDecisionChain,
    store: AdmissionHistoryPort,
) -> ChainCommitEvidence:
    """Re-verify the whole chain against the journal as it is now.

    A receipt records what was true when it was issued. Between then and use the
    history can be rolled back, forked or truncated, and a chain whose earlier
    verdicts have vanished is no longer a chain — so every one of the four is
    re-checked for membership, for its link to its predecessor, for its position,
    and for the anchor it witnessed still being a prefix of committed history.
    """

    validate_chain_commit_evidence(evidence)
    require_admission_history(store)
    decisions = _chain_decisions(chain)

    for decision, receipt in zip(decisions, evidence.receipts):
        require_committed_decision(receipt, decision=decision, journal=store)

    positions = [_position(store, digest) for digest in evidence.decision_digests]
    if positions[0] != evidence.first_position:
        raise _fail(
            HistoryFailureCode.HISTORY_ROLLED_BACK,
            "the chain no longer begins where it was written",
        )
    if positions != list(range(evidence.first_position, evidence.first_position + len(positions))):
        raise _fail(
            HistoryFailureCode.CHAIN_NOT_CONTIGUOUS,
            "the four decisions are no longer consecutive in the journal",
        )
    return evidence


def recover_chain_evidence(
    evidence: ChainCommitEvidence,
    *,
    chain: GateDecisionChain,
    store: AdmissionHistoryPort,
) -> ChainCommitEvidence:
    """Re-establish a chain's durability after a restart.

    Recovery is deliberately not a weaker check than the point-of-use one. §22
    forbids restart producing a stronger status than the state supports, and the
    cheapest way to violate that is a recovery path that trusts stored evidence
    because re-deriving it is inconvenient. So this is the same verification,
    named separately only so a caller can say what it is doing.
    """

    return require_committed_chain(evidence, chain=chain, store=store)


def chain_lineage_digest(evidence: ChainCommitEvidence) -> str:
    """A single digest naming this chain's whole durable lineage."""

    validate_chain_commit_evidence(evidence)
    joined = "\x00".join(evidence.decision_digests).encode()
    return hashlib.sha256(joined).hexdigest()


__all__ = [
    "ADAPTER_PRIVATE_SEAM",
    "AdmissionHistoryPort",
    "ChainCommitEvidence",
    "HistoryFailureCode",
    "HistoryViolation",
    "chain_lineage_digest",
    "commit_gate_chain",
    "recover_chain_evidence",
    "require_admission_history",
    "require_committed_chain",
    "validate_chain_commit_evidence",
]
