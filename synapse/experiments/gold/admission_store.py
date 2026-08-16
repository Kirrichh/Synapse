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
from pathlib import Path
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
from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
    decode_stage4_canonical_bytes,
)
from .contracts import GateKind, gate_stage_index
from .persistence import (
    PersistenceFailureCode,
    PersistenceViolation,
    StoreMutationFencePort,
    StoreMutationTicket,
    append_journal_payload,
    ensure_directory,
    require_store_mutation_fence,
    require_ticket_of_coordinator,
    scan_journal,
    store_transaction,
)

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


# ---------------------------------------------------------------------------
# Opaque causal artifacts that belong to admission history
# ---------------------------------------------------------------------------


ADMISSION_CAUSAL_FRAME_V1 = "synapse.stage4.gold.admission-causal-frame/v1"
ADMISSION_CAUSAL_FILE_V1 = "causal.journal"
ADMISSION_CAUSAL_GENESIS = b"synapse.stage4.gold.admission-causal-genesis/v1"
RETRIEVAL_DECISION_SCHEMA_V1 = "synapse.stage4.gold.retrieval-decision/v1"


class AdmissionCausalRecordKind(str, Enum):
    """The closed set intentionally contains no gate or compatibility record."""

    RETRIEVAL_DECISION = "RETRIEVAL_DECISION"


class CausalHistoryFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
    HISTORY_TORN = "HISTORY_TORN"
    HISTORY_CORRUPT = "HISTORY_CORRUPT"
    HISTORY_ROLLED_BACK = "HISTORY_ROLLED_BACK"
    HISTORY_FORKED = "HISTORY_FORKED"
    STALE_PARENT = "STALE_PARENT"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    COORDINATOR_MISMATCH = "COORDINATOR_MISMATCH"
    RECORD_MISSING = "RECORD_MISSING"
    RECORD_DUPLICATE = "RECORD_DUPLICATE"
    RECEIPT_INVALID = "RECEIPT_INVALID"
    ADMISSION_ROOT_UNCONFIRMED = "ADMISSION_ROOT_UNCONFIRMED"


class CausalHistoryViolation(RuntimeError):
    def __init__(self, failure_code: CausalHistoryFailureCode, detail: str) -> None:
        if type(failure_code) is not CausalHistoryFailureCode:
            raise TypeError("failure_code must be an exact CausalHistoryFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _causal_fail(code: CausalHistoryFailureCode, detail: str) -> CausalHistoryViolation:
    return CausalHistoryViolation(code, detail)


def _causal_canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _causal_anchors(payloads: tuple[bytes, ...]) -> tuple[str, ...]:
    running = hashlib.sha256(ADMISSION_CAUSAL_GENESIS).digest()
    result = [running.hex()]
    for payload in payloads:
        running = hashlib.sha256(running + hashlib.sha256(payload).digest()).digest()
        result.append(running.hex())
    return tuple(result)


def _causal_admission_extends(store: AdmissionHistoryPort, anchor: str) -> bool:
    try:
        result = store.extends(anchor)
    except Exception as exc:
        raise _causal_fail(
            CausalHistoryFailureCode.ADMISSION_ROOT_UNCONFIRMED,
            "admission history could not confirm the causal artifact root",
        ) from exc
    if type(result) is not bool:
        raise _causal_fail(
            CausalHistoryFailureCode.TYPE_MISMATCH,
            "admission history extends must return an exact bool",
        )
    return result


_CAUSAL_ARTIFACT_SEAL = object()
_CAUSAL_RECEIPT_SEAL = object()


@dataclass(frozen=True, init=False)
class AdmissionCausalArtifact:
    record_kind: AdmissionCausalRecordKind
    record_ref: HashBoundRef
    canonical_bytes: bytes
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object):
        raise TypeError("AdmissionCausalArtifact is produced only by create_causal_artifact")


def create_causal_artifact(
    *,
    record_kind: AdmissionCausalRecordKind,
    record_ref: HashBoundRef,
    canonical_bytes: bytes,
) -> AdmissionCausalArtifact:
    if record_kind is not AdmissionCausalRecordKind.RETRIEVAL_DECISION:
        raise _causal_fail(CausalHistoryFailureCode.TYPE_MISMATCH, "causal artifact kind is not declared")
    if type(record_ref) is not HashBoundRef or record_ref.kind is not RefKind.ARTIFACT:
        raise _causal_fail(CausalHistoryFailureCode.TYPE_MISMATCH, "causal artifact ref must be exact")
    if record_ref.schema_id != RETRIEVAL_DECISION_SCHEMA_V1:
        raise _causal_fail(CausalHistoryFailureCode.TYPE_MISMATCH, "causal artifact schema is not RetrievalDecision")
    if type(canonical_bytes) is not bytes:
        raise _causal_fail(CausalHistoryFailureCode.TYPE_MISMATCH, "causal artifact bytes must be exact")
    if (
        hashlib.sha256(canonical_bytes).hexdigest() != record_ref.sha256
        or len(canonical_bytes) != record_ref.byte_length
    ):
        raise _causal_fail(CausalHistoryFailureCode.TYPE_MISMATCH, "causal artifact ref does not bind its bytes")
    result = object.__new__(AdmissionCausalArtifact)
    object.__setattr__(result, "record_kind", record_kind)
    object.__setattr__(result, "record_ref", record_ref)
    object.__setattr__(result, "canonical_bytes", canonical_bytes)
    object.__setattr__(result, "_trusted_seal", _CAUSAL_ARTIFACT_SEAL)
    return validate_causal_artifact(result)


def validate_causal_artifact(value: AdmissionCausalArtifact) -> AdmissionCausalArtifact:
    if type(value) is not AdmissionCausalArtifact or getattr(value, "_trusted_seal", None) is not _CAUSAL_ARTIFACT_SEAL:
        raise _causal_fail(CausalHistoryFailureCode.TYPE_MISMATCH, "causal artifact is not factory sealed")
    if value.record_kind is not AdmissionCausalRecordKind.RETRIEVAL_DECISION:
        raise _causal_fail(CausalHistoryFailureCode.TYPE_MISMATCH, "causal artifact kind is not declared")
    if type(value.record_ref) is not HashBoundRef or type(value.canonical_bytes) is not bytes:
        raise _causal_fail(CausalHistoryFailureCode.TYPE_MISMATCH, "causal artifact fields are malformed")
    if value.record_ref.schema_id != RETRIEVAL_DECISION_SCHEMA_V1:
        raise _causal_fail(CausalHistoryFailureCode.TYPE_MISMATCH, "causal artifact schema is not RetrievalDecision")
    if (
        value.record_ref.kind is not RefKind.ARTIFACT
        or hashlib.sha256(value.canonical_bytes).hexdigest() != value.record_ref.sha256
        or len(value.canonical_bytes) != value.record_ref.byte_length
    ):
        raise _causal_fail(CausalHistoryFailureCode.TYPE_MISMATCH, "causal artifact ref does not bind its bytes")
    return value


@dataclass(frozen=True, init=False)
class AdmissionCausalReceipt:
    record_kind: AdmissionCausalRecordKind
    record_ref: HashBoundRef
    sequence: int
    parent_anchor: str
    history_anchor: str
    admission_history_root: str
    coordinator_id: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object):
        raise TypeError("AdmissionCausalReceipt is produced only by FileAdmissionCausalStore")


@dataclass(frozen=True)
class _CausalFrame:
    artifact: AdmissionCausalArtifact
    sequence: int
    parent_anchor: str
    admission_history_root: str
    coordinator_id: str
    frame_bytes: bytes


def _causal_frame_bytes(
    artifact: AdmissionCausalArtifact,
    *,
    sequence: int,
    parent_anchor: str,
    admission_history_root: str,
    coordinator_id: str,
) -> bytes:
    validate_causal_artifact(artifact)
    return _causal_canonical(
        {
            "schema_version": ADMISSION_CAUSAL_FRAME_V1,
            "record_kind": artifact.record_kind.value,
            "record_ref": artifact.record_ref.to_dict(),
            "canonical_record": artifact.canonical_bytes.decode("utf-8"),
            "sequence": sequence,
            "parent_anchor": parent_anchor,
            "admission_history_root": admission_history_root,
            "coordinator_id": coordinator_id,
        }
    )


def _decode_causal_frame(raw: bytes) -> _CausalFrame:
    try:
        data = decode_stage4_canonical_bytes(
            raw,
            profile_id=STAGE4_CANONICAL_PROFILE_V1,
            codec_id=STABLE_CANONICAL_CODEC_ID,
        )
        fields = {
            "schema_version", "record_kind", "record_ref", "canonical_record",
            "sequence", "parent_anchor", "admission_history_root", "coordinator_id",
        }
        if type(data) is not dict or set(data) != fields or data["schema_version"] != ADMISSION_CAUSAL_FRAME_V1:
            raise ValueError("frame shape")
        text = data["canonical_record"]
        if type(text) is not str:
            raise ValueError("canonical bytes")
        artifact = create_causal_artifact(
            record_kind=AdmissionCausalRecordKind(data["record_kind"]),
            record_ref=HashBoundRef.from_dict(data["record_ref"]),
            canonical_bytes=text.encode("utf-8"),
        )
        sequence = data["sequence"]
        parent = data["parent_anchor"]
        admission_root = data["admission_history_root"]
        coordinator_id = data["coordinator_id"]
        if type(sequence) is not int or type(sequence) is bool or sequence <= 0:
            raise ValueError("sequence")
        if any(type(item) is not str or len(item) != 64 for item in (parent, admission_root)):
            raise ValueError("anchor")
        if type(coordinator_id) is not str or len(coordinator_id) != 32:
            raise ValueError("coordinator")
        return _CausalFrame(artifact, sequence, parent, admission_root, coordinator_id, raw)
    except CausalHistoryViolation:
        raise
    except Exception as exc:
        raise _causal_fail(CausalHistoryFailureCode.HISTORY_CORRUPT, "causal history frame is malformed") from exc


class FileAdmissionCausalStore:
    """Filesystem-backed opaque RetrievalDecision history.

    The store deliberately cannot validate RetrievalDecision semantics: it does
    not import the retrieval owner.  It validates the closed artifact envelope,
    exact canonical bytes/ref binding and the admission-history root observed at
    the append.
    """

    def __init__(
        self,
        root: Path,
        *,
        mutation_fence: StoreMutationFencePort,
        admission_history: AdmissionHistoryPort,
    ) -> None:
        if not isinstance(root, Path):
            raise _causal_fail(CausalHistoryFailureCode.TYPE_MISMATCH, "causal root must be a Path")
        try:
            require_store_mutation_fence(mutation_fence)
        except PersistenceViolation as exc:
            raise _causal_fail(CausalHistoryFailureCode.TYPE_MISMATCH, "causal history requires a mutation fence") from exc
        require_admission_history(admission_history)
        participant_fence = getattr(admission_history, "mutation_fence", None)
        if participant_fence is None or participant_fence.coordinator_id() != mutation_fence.coordinator_id():
            raise _causal_fail(CausalHistoryFailureCode.COORDINATOR_MISMATCH, "admission and causal histories use different coordinators")
        self._root = root
        self._mutation_fence = mutation_fence
        self._admission_history = admission_history
        ensure_directory(root)
        self._frames()

    @property
    def mutation_fence(self) -> StoreMutationFencePort:
        return self._mutation_fence

    @property
    def path(self) -> Path:
        return self._root / ADMISSION_CAUSAL_FILE_V1

    def _frames(self) -> tuple[_CausalFrame, ...]:
        try:
            scanned = scan_journal(self.path)
        except PersistenceViolation as exc:
            code = CausalHistoryFailureCode.HISTORY_TORN if exc.failure_code is PersistenceFailureCode.JOURNAL_TORN_TAIL else CausalHistoryFailureCode.HISTORY_CORRUPT
            raise _causal_fail(code, "causal history could not be reconstructed") from exc
        if scanned.torn_tail:
            raise _causal_fail(CausalHistoryFailureCode.HISTORY_TORN, "causal history has a torn tail")
        frames = tuple(_decode_causal_frame(item.payload) for item in scanned.frames)
        anchors = _causal_anchors(tuple(item.frame_bytes for item in frames))
        coordinator_id = self._mutation_fence.coordinator_id()
        seen: set[tuple[str, str]] = set()
        for index, frame in enumerate(frames, start=1):
            if frame.sequence != index:
                raise _causal_fail(CausalHistoryFailureCode.SEQUENCE_GAP, "causal history sequence has a gap")
            if frame.parent_anchor != anchors[index - 1]:
                raise _causal_fail(CausalHistoryFailureCode.HISTORY_FORKED, "causal history parent is not its exact prefix")
            if frame.coordinator_id != coordinator_id:
                raise _causal_fail(CausalHistoryFailureCode.COORDINATOR_MISMATCH, "causal history belongs to another coordinator")
            if not _causal_admission_extends(self._admission_history, frame.admission_history_root):
                raise _causal_fail(CausalHistoryFailureCode.ADMISSION_ROOT_UNCONFIRMED, "causal artifact names a non-ancestor admission root")
            key = (frame.artifact.record_ref.ref_id, frame.artifact.record_ref.sha256)
            if key in seen:
                raise _causal_fail(CausalHistoryFailureCode.RECORD_DUPLICATE, "causal history repeats one record")
            seen.add(key)
        return frames

    def current_anchor(self) -> str:
        frames = self._frames()
        return _causal_anchors(tuple(item.frame_bytes for item in frames))[-1]

    def current_sequence(self) -> int:
        return len(self._frames())

    def extends(self, anchor: str) -> bool:
        if type(anchor) is not str or len(anchor) != 64:
            raise _causal_fail(CausalHistoryFailureCode.TYPE_MISMATCH, "causal anchor must be a sha256")
        frames = self._frames()
        return anchor in _causal_anchors(tuple(item.frame_bytes for item in frames))

    def contains_ref(self, ref: HashBoundRef) -> bool:
        if type(ref) is not HashBoundRef:
            raise _causal_fail(CausalHistoryFailureCode.TYPE_MISMATCH, "causal ref must be exact")
        return any(item.artifact.record_ref.to_dict() == ref.to_dict() for item in self._frames())

    def resolve_ref(self, ref: HashBoundRef) -> bytes:
        if type(ref) is not HashBoundRef:
            raise _causal_fail(CausalHistoryFailureCode.TYPE_MISMATCH, "causal ref must be exact")
        for item in self._frames():
            if item.artifact.record_ref.to_dict() == ref.to_dict():
                return item.artifact.canonical_bytes
        raise _causal_fail(CausalHistoryFailureCode.RECORD_MISSING, "causal artifact is absent")

    def append_artifact(
        self,
        artifact: AdmissionCausalArtifact,
        *,
        expected_parent_anchor: str,
        ticket: StoreMutationTicket | None = None,
    ) -> AdmissionCausalReceipt:
        validate_causal_artifact(artifact)
        if type(expected_parent_anchor) is not str or len(expected_parent_anchor) != 64:
            raise _causal_fail(CausalHistoryFailureCode.TYPE_MISMATCH, "expected parent must be a sha256")
        if ticket is None:
            refused: CausalHistoryViolation | None = None
            receipt: AdmissionCausalReceipt | None = None
            with store_transaction(self._mutation_fence) as own:
                try:
                    receipt = self._append(
                        artifact,
                        expected_parent_anchor=expected_parent_anchor,
                        ticket=own,
                    )
                except CausalHistoryViolation as exc:
                    if exc.failure_code not in {
                        CausalHistoryFailureCode.STALE_PARENT,
                        CausalHistoryFailureCode.RECORD_DUPLICATE,
                    }:
                        raise
                    refused = exc
            if refused is not None:
                raise refused
            if receipt is None:
                raise _causal_fail(CausalHistoryFailureCode.STORE_UNAVAILABLE, "causal append produced no receipt")
            return receipt
        require_ticket_of_coordinator(ticket, coordinator_id=self._mutation_fence.coordinator_id())
        return self._append(artifact, expected_parent_anchor=expected_parent_anchor, ticket=ticket)

    def append_retrieval_decision(
        self,
        *,
        canonical_bytes: bytes,
        record_ref: HashBoundRef,
        expected_parent_anchor: str,
        ticket: StoreMutationTicket | None = None,
    ) -> AdmissionCausalReceipt:
        """Append an opaque retrieval artifact without importing its owner."""

        artifact = create_causal_artifact(
            record_kind=AdmissionCausalRecordKind.RETRIEVAL_DECISION,
            record_ref=record_ref,
            canonical_bytes=canonical_bytes,
        )
        return self.append_artifact(
            artifact,
            expected_parent_anchor=expected_parent_anchor,
            ticket=ticket,
        )

    def _append(
        self,
        artifact: AdmissionCausalArtifact,
        *,
        expected_parent_anchor: str,
        ticket: StoreMutationTicket,
    ) -> AdmissionCausalReceipt:
        frames = self._frames()
        parent = _causal_anchors(tuple(item.frame_bytes for item in frames))[-1]
        if parent != expected_parent_anchor:
            raise _causal_fail(CausalHistoryFailureCode.STALE_PARENT, "causal history moved before append")
        if any(item.artifact.record_ref.to_dict() == artifact.record_ref.to_dict() for item in frames):
            raise _causal_fail(CausalHistoryFailureCode.RECORD_DUPLICATE, "causal artifact is already committed")
        admission_root = self._admission_history.current_anchor()
        if not _causal_admission_extends(self._admission_history, admission_root):
            raise _causal_fail(CausalHistoryFailureCode.ADMISSION_ROOT_UNCONFIRMED, "admission root is not current history")
        sequence = len(frames) + 1
        coordinator_id = self._mutation_fence.coordinator_id()
        raw = _causal_frame_bytes(
            artifact,
            sequence=sequence,
            parent_anchor=parent,
            admission_history_root=admission_root,
            coordinator_id=coordinator_id,
        )
        try:
            append_journal_payload(self.path, raw, ticket=ticket)
        except PersistenceViolation as exc:
            raise _causal_fail(CausalHistoryFailureCode.STORE_UNAVAILABLE, "causal artifact could not be appended") from exc
        committed = self._frames()
        if len(committed) != sequence or committed[-1].artifact.record_ref.to_dict() != artifact.record_ref.to_dict():
            raise _causal_fail(CausalHistoryFailureCode.HISTORY_FORKED, "causal append did not become the exact tail")
        history_anchor = _causal_anchors(tuple(item.frame_bytes for item in committed))[-1]
        receipt = object.__new__(AdmissionCausalReceipt)
        object.__setattr__(receipt, "record_kind", artifact.record_kind)
        object.__setattr__(receipt, "record_ref", artifact.record_ref)
        object.__setattr__(receipt, "sequence", sequence)
        object.__setattr__(receipt, "parent_anchor", parent)
        object.__setattr__(receipt, "history_anchor", history_anchor)
        object.__setattr__(receipt, "admission_history_root", admission_root)
        object.__setattr__(receipt, "coordinator_id", coordinator_id)
        object.__setattr__(receipt, "_trusted_seal", _CAUSAL_RECEIPT_SEAL)
        return validate_causal_receipt(receipt, store=self)


def validate_causal_receipt(
    value: AdmissionCausalReceipt,
    *,
    store: FileAdmissionCausalStore | None = None,
) -> AdmissionCausalReceipt:
    if type(value) is not AdmissionCausalReceipt or getattr(value, "_trusted_seal", None) is not _CAUSAL_RECEIPT_SEAL:
        raise _causal_fail(CausalHistoryFailureCode.RECEIPT_INVALID, "causal receipt is not store sealed")
    if value.record_kind is not AdmissionCausalRecordKind.RETRIEVAL_DECISION or type(value.record_ref) is not HashBoundRef:
        raise _causal_fail(CausalHistoryFailureCode.RECEIPT_INVALID, "causal receipt fields are malformed")
    if type(value.sequence) is not int or value.sequence <= 0:
        raise _causal_fail(CausalHistoryFailureCode.RECEIPT_INVALID, "causal receipt sequence is invalid")
    for item in (value.parent_anchor, value.history_anchor, value.admission_history_root):
        if type(item) is not str or len(item) != 64:
            raise _causal_fail(CausalHistoryFailureCode.RECEIPT_INVALID, "causal receipt anchor is invalid")
    if type(value.coordinator_id) is not str or len(value.coordinator_id) != 32:
        raise _causal_fail(CausalHistoryFailureCode.RECEIPT_INVALID, "causal receipt coordinator is invalid")
    if store is not None:
        if store.mutation_fence.coordinator_id() != value.coordinator_id:
            raise _causal_fail(CausalHistoryFailureCode.COORDINATOR_MISMATCH, "causal receipt belongs to another coordinator")
        frames = store._frames()
        if len(frames) < value.sequence:
            raise _causal_fail(CausalHistoryFailureCode.HISTORY_ROLLED_BACK, "causal receipt sequence was rolled back")
        frame = frames[value.sequence - 1]
        if frame.artifact.record_ref.to_dict() != value.record_ref.to_dict():
            raise _causal_fail(CausalHistoryFailureCode.HISTORY_FORKED, "causal receipt position contains another record")
        if not store.extends(value.history_anchor):
            raise _causal_fail(CausalHistoryFailureCode.HISTORY_FORKED, "causal receipt anchor is not a prefix")
        if not _causal_admission_extends(store._admission_history, value.admission_history_root):
            raise _causal_fail(CausalHistoryFailureCode.ADMISSION_ROOT_UNCONFIRMED, "receipt admission root is not a prefix")
    return value


__all__ = [
    "ADAPTER_PRIVATE_SEAM",
    "ADMISSION_CAUSAL_FILE_V1",
    "ADMISSION_CAUSAL_FRAME_V1",
    "AdmissionCausalArtifact",
    "AdmissionCausalReceipt",
    "AdmissionCausalRecordKind",
    "AdmissionHistoryPort",
    "CausalHistoryFailureCode",
    "CausalHistoryViolation",
    "ChainCommitEvidence",
    "FileAdmissionCausalStore",
    "HistoryFailureCode",
    "HistoryViolation",
    "RETRIEVAL_DECISION_SCHEMA_V1",
    "chain_lineage_digest",
    "commit_gate_chain",
    "create_causal_artifact",
    "recover_chain_evidence",
    "require_admission_history",
    "require_committed_chain",
    "validate_causal_artifact",
    "validate_causal_receipt",
    "validate_chain_commit_evidence",
]
