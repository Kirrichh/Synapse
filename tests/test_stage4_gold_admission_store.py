"""Stage 4 durable four-gate lineage (Patch 8 repair, round 9).

§22 requires decisions to be immutable, persisted and linked in lineage. Only
the consumption verdict was ever required to be durable; the three that made it
meaningful were not, so after a restart an auditor had a final answer and no
working. These tests are about the four being one durable run — present, in
order, linked, contiguous, and still there when asked again.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib

import pytest

from synapse.experiments.gold import admission as A
from synapse.experiments.gold import admission_store as S
from synapse.experiments.gold.contracts import GateKind

from tests.test_stage4_gold_admission import (
    entitlement,
    BOUNDARY_REF,
    CONTEXT_REF,
    FROZEN_SET_REF,
    NOW,
    REQUEST,
    SUBJECTS,
    controller,
    full_chain,
)


class Store:
    """An append-only journal that can also say where a record sits.

    Position is the part that matters here: anchors prove inclusion, and two
    records can both be included with a third between them.
    """

    def __init__(self, *, failing: bool = False, blind: bool = False) -> None:
        self._records: list[bytes] = []
        self._failing = failing
        self._blind = blind

    def append_record(self, payload: bytes) -> None:
        if self._failing:
            raise A.GateDependencyUnavailable("store unavailable")
        self._records.append(payload)

    def contains_record(self, digest: str) -> bool:
        return any(hashlib.sha256(item).hexdigest() == digest for item in self._records)

    def _anchor_after(self, count: int) -> str:
        running = hashlib.sha256(b"journal-genesis").digest()
        for item in self._records[:count]:
            running = hashlib.sha256(running + hashlib.sha256(item).digest()).digest()
        return running.hex()

    def current_anchor(self) -> str:
        return self._anchor_after(len(self._records))

    def extends(self, anchor: str) -> bool:
        return any(self._anchor_after(count) == anchor for count in range(len(self._records) + 1))

    def record_position(self, digest: str) -> int:
        if self._blind:
            raise A.GateDependencyUnavailable("store unavailable")
        for index, item in enumerate(self._records):
            if hashlib.sha256(item).hexdigest() == digest:
                return index
        raise A.GateDependencyUnavailable("record is absent")

    def rollback(self, keep: int) -> None:
        del self._records[keep:]

    def forget(self, digest: str) -> None:
        """Drop one record while leaving the rest — a targeted loss, not a truncation."""

        self._records = [
            item for item in self._records if hashlib.sha256(item).hexdigest() != digest
        ]

    def insert_at(self, index: int, payload: bytes) -> None:
        self._records.insert(index, payload)


def built_chain(control=None, *, authority: str = "gate-authority"):
    """A built chain, with the verifier's entitlement matching the deciding authority.

    ``authority`` is threaded rather than defaulted because entitlement is now
    re-established from the verifier's own declaration: a chain decided by one
    authority and verified against another's declaration is refused, which is the
    point of the check and a nuisance in a helper that hard-codes one name.
    """

    control = control or controller(authority=authority)
    decisions = full_chain(control)
    chain = A.build_gate_decision_chain(
        ingestion=decisions[0], publication=decisions[1],
        retrieval=decisions[2], consumption=decisions[3],
        **entitlement(authority=authority),
    )
    return control, chain


def test_a_committed_chain_records_all_four_decisions_in_order() -> None:
    _, chain = built_chain()
    store = Store()
    evidence = S.commit_gate_chain(chain, store=store, trusted_clock=lambda: NOW)

    assert len(evidence.receipts) == 4
    assert evidence.first_position == 0
    for gate, digest in zip(GateKind, evidence.decision_digests):
        assert store.contains_record(digest), f"{gate.value} was not written"
    assert store.record_position(evidence.decision_digests[0]) == 0
    assert store.record_position(evidence.decision_digests[3]) == 3
    S.require_committed_chain(evidence, chain=chain, store=store)


def test_the_durable_order_is_the_order_the_reasoning_ran() -> None:
    """A journal holding all four out of order records something that did not happen."""

    _, chain = built_chain()
    store = Store()
    evidence = S.commit_gate_chain(chain, store=store, trusted_clock=lambda: NOW)
    positions = [store.record_position(item) for item in evidence.decision_digests]
    assert positions == [0, 1, 2, 3]


def test_a_chain_interleaved_with_other_records_is_refused() -> None:
    """The kill for contiguity, and why anchors alone cannot supply it.

    A writer that slips its own records between the four leaves everything else
    intact: each decision is included, each anchor still extends, no history is
    forked. Only the positions show that the chain is not one run. Inserting
    *after the fact* would not isolate this — that rewrites history and the
    anchor check catches it first — so the interleaving happens during the
    commit, which is when it would really happen.
    """

    class Chatty(Store):
        def append_record(self, payload: bytes) -> None:
            super().append_record(payload)
            super().append_record(b"an unrelated record")

    _, chain = built_chain()
    store = Chatty()
    with pytest.raises(S.HistoryViolation) as excinfo:
        S.commit_gate_chain(chain, store=store, trusted_clock=lambda: NOW)
    assert excinfo.value.failure_code is S.HistoryFailureCode.CHAIN_NOT_CONTIGUOUS


def test_a_chain_whose_positions_stop_being_consecutive_is_refused_at_use() -> None:
    """The same property, asked again at the point of use rather than at commit."""

    _, chain = built_chain()
    store = Store()
    evidence = S.commit_gate_chain(chain, store=store, trusted_clock=lambda: NOW)

    class Shifted(Store):
        def record_position(self, digest: str) -> int:
            position = Store.record_position(self, digest)
            return position + (10 if position >= 2 else 0)

    shifted = Shifted()
    shifted._records = list(store._records)
    with pytest.raises(S.HistoryViolation) as excinfo:
        S.require_committed_chain(evidence, chain=chain, store=shifted)
    assert excinfo.value.failure_code is S.HistoryFailureCode.CHAIN_NOT_CONTIGUOUS


def test_a_chain_whose_earlier_decisions_were_rolled_back_is_refused() -> None:
    """A consumption receipt says nothing about the three verdicts behind it."""

    _, chain = built_chain()
    store = Store()
    evidence = S.commit_gate_chain(chain, store=store, trusted_clock=lambda: NOW)

    store.rollback(2)
    with pytest.raises((S.HistoryViolation, A.AdmissionViolation)):
        S.require_committed_chain(evidence, chain=chain, store=store)


def test_recovery_is_the_same_check_and_not_a_weaker_one() -> None:
    """§22 forbids restart producing a status the state does not support."""

    _, chain = built_chain()
    store = Store()
    evidence = S.commit_gate_chain(chain, store=store, trusted_clock=lambda: NOW)
    assert S.recover_chain_evidence(evidence, chain=chain, store=store) is evidence

    store.rollback(1)
    with pytest.raises((S.HistoryViolation, A.AdmissionViolation)):
        S.recover_chain_evidence(evidence, chain=chain, store=store)


def test_a_chain_whose_link_was_edited_is_refused_before_anything_is_written() -> None:
    """A chain whose decisions do not name each other is not a chain.

    Editing the link breaks the decision's own identity first, so that is what
    fires — and it is the stronger barrier, since it catches the edit without
    needing to know anything about chains. The linkage check in this owner is
    defence in depth for a chain assembled some other way; ``build_gate_decision_chain``
    will not produce an unlinked one, so there is no legitimate route to it.
    """

    _, chain = built_chain()
    object.__setattr__(chain.consumption, "predecessor_decision_digest", "0" * 64)
    store = Store()
    with pytest.raises(A.AdmissionViolation) as excinfo:
        S.commit_gate_chain(chain, store=store, trusted_clock=lambda: NOW)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_IDENTITY_MISMATCH
    assert store._records == [], "nothing may be written before the chain validates"


def test_an_unavailable_store_writes_no_evidence() -> None:
    _, chain = built_chain()
    with pytest.raises(A.AdmissionViolation) as excinfo:
        S.commit_gate_chain(chain, store=Store(failing=True), trusted_clock=lambda: NOW)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.JOURNAL_UNAVAILABLE


def test_a_store_that_cannot_report_positions_yields_no_evidence() -> None:
    """Inclusion without position is not enough, so its absence is an outage."""

    _, chain = built_chain()
    with pytest.raises(S.HistoryViolation) as excinfo:
        S.commit_gate_chain(chain, store=Store(blind=True), trusted_clock=lambda: NOW)
    assert excinfo.value.failure_code is S.HistoryFailureCode.STORE_UNAVAILABLE


@pytest.mark.parametrize("missing", ["record_position", "extends", "current_anchor"])
def test_an_incomplete_store_is_refused(missing: str) -> None:
    class Partial:
        pass

    for name in ("append_record", "contains_record", "current_anchor", "extends", "record_position"):
        if name != missing:
            setattr(Partial, name, lambda self, *args: 1)

    with pytest.raises(S.HistoryViolation) as excinfo:
        S.require_admission_history(Partial())
    assert excinfo.value.failure_code is S.HistoryFailureCode.TYPE_MISMATCH


def test_evidence_cannot_be_built_outside_the_factory() -> None:
    with pytest.raises(TypeError):
        S.ChainCommitEvidence()


def test_a_resealed_evidence_record_is_refused() -> None:
    _, chain = built_chain()
    store = Store()
    evidence = S.commit_gate_chain(chain, store=store, trusted_clock=lambda: NOW)
    object.__setattr__(evidence, "_trusted_seal", object())
    with pytest.raises(S.HistoryViolation) as excinfo:
        S.validate_chain_commit_evidence(evidence)
    assert excinfo.value.failure_code is S.HistoryFailureCode.TRUSTED_OBJECT_FORGED


def test_evidence_missing_a_receipt_is_refused() -> None:
    """One receipt per gate, and three is not four."""

    _, chain = built_chain()
    store = Store()
    evidence = S.commit_gate_chain(chain, store=store, trusted_clock=lambda: NOW)
    object.__setattr__(evidence, "receipts", evidence.receipts[:3])
    with pytest.raises(S.HistoryViolation) as excinfo:
        S.validate_chain_commit_evidence(evidence)
    assert excinfo.value.failure_code is S.HistoryFailureCode.CHAIN_INCOMPLETE


def test_a_receipt_filed_against_the_wrong_decision_is_refused() -> None:
    _, chain = built_chain()
    store = Store()
    evidence = S.commit_gate_chain(chain, store=store, trusted_clock=lambda: NOW)
    swapped = (evidence.decision_digests[1], *evidence.decision_digests[1:])
    object.__setattr__(evidence, "decision_digests", swapped)
    with pytest.raises(S.HistoryViolation) as excinfo:
        S.validate_chain_commit_evidence(evidence)
    assert excinfo.value.failure_code in (
        S.HistoryFailureCode.CHAIN_NOT_LINKED,
        S.HistoryFailureCode.CHAIN_NOT_DURABLE,
    )


def test_the_lineage_digest_changes_when_any_decision_does() -> None:
    """One name for the whole chain, and it must cover all of it."""

    _, first = built_chain()
    _, second = built_chain(authority="second-authority")
    left = S.commit_gate_chain(first, store=Store(), trusted_clock=lambda: NOW)
    right = S.commit_gate_chain(second, store=Store(), trusted_clock=lambda: NOW)
    assert S.chain_lineage_digest(left) != S.chain_lineage_digest(right)
    assert S.chain_lineage_digest(left) == S.chain_lineage_digest(left)


def test_every_receipt_is_re_verified_at_use_not_only_the_last() -> None:
    """The kill for checking the final verdict and calling the chain proven.

    A truncating rollback cannot isolate this — it takes the last decision with
    it, so a check that only looks at the last still fails. The case that
    separates them is a targeted loss: an *earlier* decision disappears while
    the consumption verdict and its receipt stay perfectly valid. That is
    exactly the state a chain must not be usable in, and exactly the state a
    last-receipt-only check calls fine.
    """

    _, chain = built_chain()
    store = Store()
    evidence = S.commit_gate_chain(chain, store=store, trusted_clock=lambda: NOW)

    store.forget(evidence.decision_digests[0])
    assert store.contains_record(evidence.decision_digests[3]), "the last verdict is untouched"
    A.require_committed_decision(
        evidence.receipts[3], decision=chain.consumption, journal=store
    ) if False else None

    with pytest.raises((S.HistoryViolation, A.AdmissionViolation)):
        S.require_committed_chain(evidence, chain=chain, store=store)


def test_a_chain_presented_out_of_stage_order_is_refused() -> None:
    """Four valid decisions in the wrong order record something that did not happen."""

    _, chain = built_chain()
    ingestion, publication = chain.ingestion, chain.publication
    object.__setattr__(chain, "ingestion", publication)
    object.__setattr__(chain, "publication", ingestion)

    store = Store()
    with pytest.raises(S.HistoryViolation) as excinfo:
        S.commit_gate_chain(chain, store=store, trusted_clock=lambda: NOW)
    assert excinfo.value.failure_code is S.HistoryFailureCode.CHAIN_OUT_OF_ORDER
    assert store._records == [], "nothing may be written before the order is checked"


def test_the_lineage_digest_covers_every_decision_not_just_the_first() -> None:
    """One name for the whole chain has to change when any part of it does.

    Two chains from different controllers differ at their very first decision,
    so comparing those would pass even for a digest that read nothing but the
    first. These two share ingestion and publication exactly — ingestion is
    context-free, and publication does not see a consumer either — and diverge
    only at retrieval, where the consumer context first enters. A digest that
    stopped at the first decision would call them the same chain.
    """

    from synapse.experiments.gold.canonicalization import RefKind
    from tests.test_stage4_gold_admission import ref as make_ref

    control = controller()
    other_context = make_ref(RefKind.ARTIFACT, "another-consumer")

    def chain_for(context):
        ingestion = A.evaluate_ingestion_gate(control, subject_refs=SUBJECTS)
        publication = A.evaluate_publication_gate(
            control, subject_refs=SUBJECTS, requested=REQUEST, predecessor=ingestion
        )
        retrieval = A.evaluate_retrieval_gate(
            control, subject_refs=SUBJECTS, consumer_context_ref=context,
            boundary_ref=BOUNDARY_REF, frozen_candidate_set_ref=FROZEN_SET_REF,
            requested=REQUEST, predecessor=publication,
        )
        consumption = A.evaluate_consumption_gate(
            control, subject_refs=SUBJECTS, consumer_context_ref=context,
            boundary_ref=BOUNDARY_REF, requested=REQUEST, predecessor=retrieval,
        )
        return A.build_gate_decision_chain(
            ingestion=ingestion, publication=publication,
            retrieval=retrieval, consumption=consumption,
            **entitlement(),
        )

    left = S.commit_gate_chain(chain_for(CONTEXT_REF), store=Store(), trusted_clock=lambda: NOW)
    right = S.commit_gate_chain(chain_for(other_context), store=Store(), trusted_clock=lambda: NOW)

    assert left.decision_digests[0] == right.decision_digests[0], "the chains must share a start"
    assert left.decision_digests[2] != right.decision_digests[2], "and diverge later"
    assert S.chain_lineage_digest(left) != S.chain_lineage_digest(right)
