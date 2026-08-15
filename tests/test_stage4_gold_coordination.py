"""Stage 4 fenced current-state capture (Patch 8 repair, round 8).

§22 requires the consumption gate to see one coherent world. A single reader
call is not that: it may consult six stores at six moments and return a set that
describes no world that ever existed, with every anchor individually valid. The
fence makes that detectable, and these tests are mostly about the detection —
the happy path is one test, the ways a torn or dishonest read is refused are the
rest.
"""

from __future__ import annotations

from pathlib import Path

from datetime import datetime, timezone
import hashlib

import pytest

from synapse.experiments.gold import admission as A
from synapse.experiments.gold import admission_store as S
from synapse.experiments.gold import coordination as C
from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind

from tests.test_stage4_gold_admission import (
    BOUNDARY_REF,
    NOW,
    anchors,
    controller,
)


class Fence:
    """An in-memory coordinating store.

    ``tear_after`` makes the epoch advance part-way through a capture, which is
    the whole scenario the fence exists for: nothing is malformed, the reader
    answers normally, and the six values simply do not belong to one moment.
    """

    def __init__(self, *, epoch: int = 7, tear_after: int | None = None, failing: str | None = None) -> None:
        self.epoch = epoch
        self.leases: list[str] = []
        self.released: list[str] = []
        self._reads = 0
        self._tear_after = tear_after
        self._failing = failing

    def acquire_lease(self) -> str:
        if self._failing == "acquire":
            raise A.GateDependencyUnavailable("fence unavailable")
        lease_id = f"lease-{len(self.leases) + 1}"
        self.leases.append(lease_id)
        return lease_id

    def current_epoch(self) -> int:
        if self._failing == "epoch":
            raise A.GateDependencyUnavailable("fence unavailable")
        self._reads += 1
        if self._tear_after is not None and self._reads > self._tear_after:
            self.epoch += 1
        return self.epoch

    def release_lease(self, lease_id: str) -> None:
        if self._failing == "release":
            raise A.GateDependencyUnavailable("fence unavailable")
        self.released.append(lease_id)


def test_a_quiet_world_yields_a_fenced_observation() -> None:
    fence = Fence()
    state = C.read_current_authority_state(controller(), fence=fence)

    assert state.lease.entry_epoch == state.exit_epoch == 7
    assert state.head_set.boundary_ref == BOUNDARY_REF
    assert fence.released == fence.leases, "the lease must be released"
    assert C.require_untorn_state(state, fence=fence) is state.head_set


def test_a_store_moving_mid_read_makes_the_observation_torn() -> None:
    """The kill for mix-and-match, and the reason the fence exists.

    Nothing here is malformed. The reader answers, every anchor validates, and
    the resulting set would pass every structural check — it simply describes
    two different moments. Only the epoch moving between entry and exit reveals
    it, and there is no way from here to tell which values came from before, so
    the observation is refused rather than repaired.
    """

    fence = Fence(tear_after=1)
    with pytest.raises(C.FenceViolation) as excinfo:
        C.read_current_authority_state(controller(), fence=fence)
    assert excinfo.value.failure_code is C.FenceFailureCode.OBSERVATION_TORN


def test_a_torn_read_still_releases_its_lease() -> None:
    """A detected tear must not become a stuck system.

    A fence that leaked leases on the failing path would turn the thing the
    fence was built to catch into an outage, which is a worse outcome than the
    tear it detected.
    """

    fence = Fence(tear_after=1)
    with pytest.raises(C.FenceViolation):
        C.read_current_authority_state(controller(), fence=fence)
    assert fence.released == fence.leases != []


def test_an_epoch_that_goes_backwards_is_refused_separately() -> None:
    """A decreasing epoch is not a tear — it is a store that rewound.

    Both refuse, and folding them together would tell an operator the wrong
    thing: a tear means two writers raced, a rewind means history was rolled
    back, and those get investigated in different places.
    """

    class Rewinding(Fence):
        def current_epoch(self) -> int:
            self._reads += 1
            return 9 if self._reads == 1 else 4

    fence = Rewinding()
    with pytest.raises(C.FenceViolation) as excinfo:
        C.read_current_authority_state(controller(), fence=fence)
    assert excinfo.value.failure_code is C.FenceFailureCode.EPOCH_WENT_BACKWARDS


def test_a_world_that_moves_after_the_read_is_no_longer_current() -> None:
    """Coherent at capture and current at use are two different claims."""

    fence = Fence()
    state = C.read_current_authority_state(controller(), fence=fence)
    C.require_untorn_state(state, fence=fence)

    fence.epoch += 1
    with pytest.raises(C.FenceViolation) as excinfo:
        C.require_untorn_state(state, fence=fence)
    assert excinfo.value.failure_code is C.FenceFailureCode.OBSERVATION_TORN


@pytest.mark.parametrize("failing", ["acquire", "epoch"])
def test_an_unreachable_fence_is_an_outage_not_an_admission(failing: str) -> None:
    fence = Fence(failing=failing)
    with pytest.raises(C.FenceViolation) as excinfo:
        C.read_current_authority_state(controller(), fence=fence)
    assert excinfo.value.failure_code is C.FenceFailureCode.FENCE_UNAVAILABLE


def test_a_fence_that_cannot_confirm_a_release_does_not_mask_the_result() -> None:
    """Release is best-effort on purpose.

    The caller either already holds a verified observation or is already
    failing for a precise reason. Raising from the release path would replace a
    precise diagnosis with a vaguer one, and would fail a capture that actually
    succeeded.
    """

    fence = Fence(failing="release")
    state = C.read_current_authority_state(controller(), fence=fence)
    assert state.exit_epoch == state.lease.entry_epoch
    assert fence.released == []


@pytest.mark.parametrize("bogus", [None, "9", 9.0, True])
def test_a_fence_answering_with_the_wrong_type_is_a_contract_violation(bogus) -> None:
    """Same three-way split the gate probes use: broken adapter, not outage."""

    class Bogus(Fence):
        def current_epoch(self):
            return bogus

    with pytest.raises(C.FenceViolation) as excinfo:
        C.read_current_authority_state(controller(), fence=Bogus())
    assert excinfo.value.failure_code is C.FenceFailureCode.TYPE_MISMATCH


def test_a_lease_cannot_be_built_outside_the_factory() -> None:
    with pytest.raises(TypeError):
        C.CoordinatedFenceLease()
    with pytest.raises(TypeError):
        C.FencedAuthorityState()


def test_a_resealed_fenced_state_is_refused() -> None:
    fence = Fence()
    state = C.read_current_authority_state(controller(), fence=fence)
    object.__setattr__(state, "_trusted_seal", object())
    with pytest.raises(C.FenceViolation) as excinfo:
        C.validate_fenced_authority_state(state)
    assert excinfo.value.failure_code is C.FenceFailureCode.TRUSTED_OBJECT_FORGED


def test_a_fenced_state_whose_epochs_disagree_is_refused_after_the_fact() -> None:
    """Validation re-checks the invariant rather than trusting construction."""

    fence = Fence()
    state = C.read_current_authority_state(controller(), fence=fence)
    object.__setattr__(state, "exit_epoch", state.lease.entry_epoch + 1)
    with pytest.raises(C.FenceViolation) as excinfo:
        C.validate_fenced_authority_state(state)
    assert excinfo.value.failure_code is C.FenceFailureCode.OBSERVATION_TORN


@pytest.mark.parametrize("missing", ["acquire_lease", "current_epoch", "release_lease"])
def test_an_incomplete_fence_is_refused(missing: str) -> None:
    class Partial:
        pass

    for name in ("acquire_lease", "current_epoch", "release_lease"):
        if name != missing:
            setattr(Partial, name, lambda self, *args: 1)

    with pytest.raises(C.FenceViolation) as excinfo:
        C.require_snapshot_fence(Partial())
    assert excinfo.value.failure_code is C.FenceFailureCode.TYPE_MISMATCH


# ---------------------------------------------------------------------------
# Blocker 5 — the point of use re-decides rather than re-reading anchors
# ---------------------------------------------------------------------------


def _durable_journal(root: Path):
    """A real file-backed journal, because the point of use now asks a real question.

    The in-memory double answers only ``contains_record``, and the check
    ``admit_for_use_now`` performs is deliberately stronger than membership: it
    asks for contiguous positions and for the witnessed anchor still being a
    prefix of committed history. A double extended far enough to answer that would
    be a second implementation of durability living in a test file, which is the
    line NR-06 draws and the reason ``admission_journal`` exists.
    """

    from synapse.experiments.gold.admission_journal import FileAdmissionJournal
    from tests.gold_store_fence import fence_for

    return FileAdmissionJournal(root / "admission" / "decisions.journal", fence_for(root))


def _handle_and_chain(control, journal):
    """A handle over a chain committed as one contiguous durable run.

    The chain is committed through ``commit_gate_chain`` rather than four separate
    ``commit_gate_decision`` calls, because the point of use now re-verifies the
    whole run — membership, links, contiguous positions and the witnessed anchor —
    and that is the evidence such a check is made of.
    """

    from tests.test_stage4_gold_admission import (
        CONTEXT_REF,
        REQUEST,
        SUBJECTS,
        full_chain,
    )

    decisions = full_chain(control)
    chain = A.build_gate_decision_chain(
        ingestion=decisions[0], publication=decisions[1],
        retrieval=decisions[2], consumption=decisions[3],
    )
    evidence = S.commit_gate_chain(chain, store=journal, trusted_clock=lambda: NOW)
    receipts = evidence.receipts
    head_set = A.capture_authority_heads(control)
    handle = A.admit_for_consumption(
        chain, controller=control, subject_refs=SUBJECTS,
        consumer_context_ref=CONTEXT_REF, boundary_ref=BOUNDARY_REF,
        policy_version="policy-v1", receipts=receipts, head_set=head_set, journal=journal,
    )
    return chain, handle, evidence


def test_the_point_of_use_re_evaluates_rather_than_re_reading(tmp_path: Path) -> None:
    """Freshness is the evaluation happening, not the identity differing.

    Over an unchanged world a re-decision is byte-identical to the original —
    that is determinism working, and it means "fresh" cannot be demonstrated by
    comparing identities. What can be demonstrated is that the gate ran: every
    probe is consulted again, including the compatibility one, which is the
    probe a head comparison never reaches.
    """

    from synapse.experiments.gold import point_of_use as P
    from tests.test_stage4_gold_admission import Journal, REQUEST, compat_finding

    calls: list[str] = []

    def probe(item, ctx):
        calls.append(item.ref_id)
        return compat_finding(item, ctx)

    control = controller(compat=probe)
    journal = _durable_journal(tmp_path)
    chain, handle, evidence = _handle_and_chain(control, journal)
    before = len(calls)

    knowledge = P.admit_for_use_now(
        handle, controller=control, chain=chain, evidence=evidence, journal=journal,
        fence=Fence(), requested=REQUEST,
    )
    assert len(calls) > before, "the compatibility probe was not consulted again"
    P.validate_current_admitted_knowledge(knowledge)
    assert knowledge.subject_refs == handle.subject_refs


def test_environment_drift_that_moves_no_anchor_still_blocks_use(tmp_path: Path) -> None:
    """The kill for Blocker 5, and the case a head comparison cannot see.

    Compatibility depends on the live environment observation as much as on
    stored records. A compiler or environment version change makes an admitted
    behavior inapplicable while writing nothing to the compatibility store — so
    its head anchor is unmoved, the fence epoch is unmoved, and every anchor
    comparison reports a quiet world. Only re-running the gate notices.
    """

    from synapse.experiments.gold import point_of_use as P
    from tests.test_stage4_gold_admission import Journal, REQUEST, compat_finding

    drifted = {"yet": False}

    def probe(item, ctx):
        return compat_finding(item, ctx, compatible=not drifted["yet"])

    control = controller(compat=probe)
    journal = _durable_journal(tmp_path)
    chain, handle, evidence = _handle_and_chain(control, journal)
    fence = Fence()

    # Nothing in the stored world changes — only the environment the probe sees.
    drifted["yet"] = True

    fresh_head_set = A.capture_authority_heads(control)
    assert fresh_head_set.observation("compatibility").to_dict() == (
        handle.head_set.observation("compatibility").to_dict()
    ), "the compatibility anchor is unmoved, which is the whole point"

    with pytest.raises(A.AdmissionViolation) as excinfo:
        P.admit_for_use_now(
            handle, controller=control, chain=chain, evidence=evidence, journal=journal,
            fence=fence, requested=REQUEST,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.NOT_ADMITTED


def test_a_torn_world_yields_no_fresh_admission(tmp_path: Path) -> None:
    """Everything below the capture decides against it, so a torn read stops first."""

    from synapse.experiments.gold import point_of_use as P
    from tests.test_stage4_gold_admission import Journal, REQUEST

    control = controller()
    journal = _durable_journal(tmp_path)
    chain, handle, evidence = _handle_and_chain(control, journal)

    with pytest.raises(C.FenceViolation) as excinfo:
        P.admit_for_use_now(
            handle, controller=control, chain=chain, evidence=evidence, journal=journal,
            fence=Fence(tear_after=1), requested=REQUEST,
        )
    assert excinfo.value.failure_code is C.FenceFailureCode.OBSERVATION_TORN


def test_the_fresh_verdict_is_durable_before_it_admits_anything(tmp_path: Path) -> None:
    """A verdict that was not written is not a verdict."""

    from synapse.experiments.gold import point_of_use as P
    from tests.test_stage4_gold_admission import Journal, REQUEST

    control = controller()
    journal = _durable_journal(tmp_path)
    chain, handle, evidence = _handle_and_chain(control, journal)
    before = len(journal._digests())

    knowledge = P.admit_for_use_now(
        handle, controller=control, chain=chain, evidence=evidence, journal=journal,
        fence=Fence(), requested=REQUEST,
    )
    assert len(journal._digests()) == before + 1, "the fresh decision must be committed"
    assert journal.contains_record(knowledge.commit_receipt.decision_digest)


def test_the_knowledge_names_the_fresh_decision_not_the_stored_one(tmp_path: Path) -> None:
    """Over an unchanged world the two coincide, so the world has to change.

    A re-decision against identical inputs is byte-identical to the original,
    which is determinism working correctly and also means an unchanged world
    cannot distinguish "named the fresh verdict" from "named the stored one".
    Advancing the clock is enough to separate them: the fresh decision carries a
    later timestamp, so it has its own identity, and the returned knowledge must
    carry *that* one.
    """

    from synapse.experiments.gold import point_of_use as P
    from tests.test_stage4_gold_admission import LATER, Journal, REQUEST

    now = [NOW]
    control = controller(clock=lambda: now[0])
    journal = _durable_journal(tmp_path)
    chain, handle, evidence = _handle_and_chain(control, journal)

    now[0] = LATER
    knowledge = P.admit_for_use_now(
        handle, controller=control, chain=chain, evidence=evidence, journal=journal,
        fence=Fence(), requested=REQUEST,
    )
    assert knowledge.consumption_decision_id != handle.consumption_decision_id, (
        "the fresh verdict has its own identity and the knowledge must name it"
    )
    assert knowledge.commit_receipt.decision_digest != handle.commit_receipt.decision_digest


def test_a_world_that_moves_during_the_commit_admits_nothing(tmp_path: Path) -> None:
    """The window between deciding and writing is not a safe gap.

    A verdict written after the world has already changed describes a world that
    no longer exists, and the commit is exactly when that can happen — it is the
    slowest step and the one that touches durable storage. So the observation is
    re-checked after the write, not only before it.
    """

    from synapse.experiments.gold import point_of_use as P
    from tests.test_stage4_gold_admission import Journal, REQUEST

    control = controller()
    fence = Fence()
    journal = _durable_journal(tmp_path)
    chain, handle, evidence = _handle_and_chain(control, journal)

    class Slow:
        """The real journal, with the world moving as the write lands.

        A delegating wrapper rather than a subclass of the in-memory double: the
        point of use asks this store for positions and anchors now, so a stand-in
        that cannot answer them would fail for the wrong reason and prove nothing
        about the window this test is named for.
        """

        def __getattr__(self, name):
            return getattr(journal, name)

        def append_record(self, payload: bytes) -> None:
            journal.append_record(payload)
            fence.epoch += 1

    with pytest.raises(C.FenceViolation) as excinfo:
        P.admit_for_use_now(
            handle, controller=control, chain=chain, evidence=evidence, journal=Slow(),
            fence=fence, requested=REQUEST,
        )
    assert excinfo.value.failure_code is C.FenceFailureCode.OBSERVATION_TORN


def test_a_handle_whose_chain_was_rolled_back_admits_nothing(tmp_path: Path) -> None:
    """The reviewer's P0 sequence, and the test that should have come first.

    A full four-gate chain is committed, a handle is minted from it, and then the
    durable history is rolled back entirely. Round 19 admitted anyway: the point
    of use checked the chain object it was handed — an in-memory structure — ran a
    fresh consumption gate, and wrote that verdict. What remained behind the
    resulting `CurrentAdmittedKnowledge` was the one record it had just written
    itself, so §22's "decisions persisted and linked in lineage" held for a
    lineage of one.

    Minting demanded four durable receipts. Nothing re-asked at the moment of
    use, which made the handle a bearer token: its backing could be deleted and
    the holder would not notice.
    """

    from synapse.experiments.gold import point_of_use as P
    from tests.test_stage4_gold_admission import REQUEST

    control = controller()
    journal = _durable_journal(tmp_path)
    chain, handle, evidence = _handle_and_chain(control, journal)
    assert len(journal._digests()) == 4

    journal.path.unlink()
    assert journal._digests() == ()

    with pytest.raises(A.AdmissionViolation) as caught:
        P.admit_for_use_now(
            handle, controller=control, chain=chain, evidence=evidence, journal=journal,
            fence=Fence(), requested=REQUEST,
        )
    assert caught.value.failure_code is A.AdmissionFailureCode.DECISION_NOT_DURABLE
    assert journal._digests() == (), "a refused admission writes nothing"


def test_a_handle_whose_chain_was_reordered_admits_nothing(tmp_path: Path) -> None:
    """Membership is not the check, and this is the case that separates them.

    Every one of the four decisions is still in the journal. Only the order
    changed — so `contains_record` answers yes four times, and the history is
    still a record of something that did not happen. Had the point of use been
    given four `require_committed_decision` calls instead of chain recovery, this
    would pass and the barrier would be decorative in exactly the way the
    admission journal suite already warns about.
    """

    from synapse.experiments.gold import admission_journal as J
    from synapse.experiments.gold import point_of_use as P
    from synapse.experiments.gold.persistence import scan_journal
    from tests.gold_store_fence import fence_for
    from tests.test_stage4_gold_admission import REQUEST

    control = controller()
    journal = _durable_journal(tmp_path)
    chain, handle, evidence = _handle_and_chain(control, journal)
    payloads = [frame.payload for frame in scan_journal(journal.path).frames]

    forked_root = tmp_path / "forked"
    forked_root.mkdir()
    forked = J.FileAdmissionJournal(
        forked_root / "admission" / "decisions.journal", fence_for(forked_root)
    )
    for payload in [payloads[1], payloads[0], *payloads[2:]]:
        forked.append_record(payload)
    assert sorted(forked._digests()) == sorted(journal._digests()), "every record survived"

    with pytest.raises(A.AdmissionViolation) as caught:
        P.admit_for_use_now(
            handle, controller=control, chain=chain, evidence=evidence, journal=forked,
            fence=Fence(), requested=REQUEST,
        )
    assert caught.value.failure_code is A.AdmissionFailureCode.JOURNAL_ROLLED_BACK
