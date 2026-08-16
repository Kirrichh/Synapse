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
from synapse.experiments.gold import admission_journal as J
from synapse.experiments.gold import admission_store as S
from synapse.experiments.gold import coordination as C
from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind

from tests.test_stage4_gold_admission import (
    entitlement,
    BOUNDARY_REF,
    NOW,
    anchors,
    controller,
)


def coordinator(root: Path):
    """The real coordinator, on disk, for one root.

    Not a double. The previous version of this file defined an in-memory `Fence`
    with an integer it moved on request, and that object was a second
    implementation of the coordination semantics — it decided when the epoch
    changed, so every test below was written against its behaviour rather than
    against the production protocol. It also had no coordinator identity, which
    is how the suite came to hand the point of use one fence while the journal it
    read held another and call the result green.

    Every scenario in this file is now produced the way it happens in production:
    by a real writer opening a real interval on a real coordinator.

    Taken from ``tests.gold_store_fence`` rather than constructed here, because
    one root must have exactly one coordinator: the journal below is attached
    through the same helper, and a reader holding a second coordinator over the
    same stores is the arrangement this round exists to make impossible.
    """

    from tests.gold_store_fence import fence_for

    return fence_for(root)


def mutate(fence) -> None:
    """One complete authority mutation: even → odd → even, two frames."""

    from synapse.experiments.gold.persistence import store_transaction

    with store_transaction(fence):
        pass


def tearing_head_reader(fence, *, after: int = 1):
    """A head reader that lets a real writer land part-way through the capture.

    This is what a torn read *is*: the capture consults the stores, and between
    two of those consultations some other writer completes a transaction. The
    fence is not persuaded to report anything — it reports the truth, and the
    truth is that the world moved.
    """

    from tests.test_stage4_gold_admission import anchors

    seen = {"reads": 0}

    def read():
        seen["reads"] += 1
        if seen["reads"] == after:
            mutate(fence)
        return anchors()

    return read


def test_a_quiet_world_yields_a_fenced_observation(tmp_path: Path) -> None:
    """Even at entry, the same even value at exit, bound to this coordinator.

    All three parts are the claim. The epoch alone would say the world was
    quiet; the coordinator identity is what says *which* world, and an
    observation that did not record it could be confirmed later by any fence
    that happened to be sitting at the same number.
    """

    fence = coordinator(tmp_path)
    entry = fence.current_epoch()
    assert entry % 2 == 0, "a settled coordinator is even"

    state = C.read_current_authority_state(controller(), fence=fence)

    assert state.window.entry_epoch == state.exit_epoch == entry
    assert state.window.coordinator_id == fence.coordinator_id()
    assert state.head_set.boundary_ref == BOUNDARY_REF
    assert C.require_untorn_state(state, fence=fence) is state.head_set


def test_a_store_moving_mid_read_makes_the_observation_torn(tmp_path: Path) -> None:
    """The kill for mix-and-match, and the reason the fence exists.

    Nothing here is malformed. The reader answers, every anchor validates, and
    the resulting set would pass every structural check — it simply describes
    two different moments. A real writer completes a real transaction while the
    heads are being gathered, so entry and exit are both even and two apart, and
    there is no way from here to tell which values came from before the change.
    """

    fence = coordinator(tmp_path)
    entry = fence.current_epoch()
    control = controller(heads=tearing_head_reader(fence))

    with pytest.raises(C.FenceViolation) as excinfo:
        C.read_current_authority_state(control, fence=fence)
    assert excinfo.value.failure_code is C.FenceFailureCode.OBSERVATION_TORN
    assert fence.current_epoch() == entry + 2, (
        "the interference was one complete transaction, not a half-open one"
    )


def test_a_torn_read_leaves_the_coordinator_usable(tmp_path: Path) -> None:
    """A detected tear must not become a stuck system.

    The lease this test used to be about is gone; the property it was protecting
    is not. A barrier that turned every tear it caught into a permanently blocked
    coordinator would be a worse outcome than the tear, so the refusal is checked
    to leave the epoch settled and the next read to succeed.
    """

    fence = coordinator(tmp_path)
    with pytest.raises(C.FenceViolation):
        C.read_current_authority_state(controller(heads=tearing_head_reader(fence)), fence=fence)

    assert fence.current_epoch() % 2 == 0, "a torn read must not leave an interval open"
    state = C.read_current_authority_state(controller(), fence=fence)
    assert state.window.entry_epoch == state.exit_epoch


def test_a_mutation_already_in_flight_refuses_before_any_head_is_read(tmp_path: Path) -> None:
    """Odd at entry is not a tear, and it does not get the tear's code.

    Nothing has been read when this refuses, which is the whole difference: the
    caller has lost nothing and may retry the moment the interval closes. Telling
    it the observation was torn would report a change that landed inside a window
    that never opened.
    """

    from synapse.experiments.gold.persistence import store_transaction

    fence = coordinator(tmp_path)
    reads: list[int] = []

    with store_transaction(fence):
        assert fence.current_epoch() % 2 == 1
        with pytest.raises(C.FenceViolation) as excinfo:
            C.read_current_authority_state(
                controller(heads=lambda: reads.append(1) or anchors()), fence=fence
            )
    assert excinfo.value.failure_code is C.FenceFailureCode.MUTATION_IN_FLIGHT_AT_ENTRY
    assert reads == [], "the heads must not be read at all while a write is in flight"


def test_a_mutation_that_opens_during_the_read_gets_its_own_refusal(tmp_path: Path) -> None:
    """Odd at exit is a third condition, distinct from both of the above.

    Some of the heads predate the interval and the rest may not, so the values
    are discarded — but unlike the entry case something *is* underway, and unlike
    the torn case it has not finished, so the caller cannot yet be told what
    changed.
    """

    from synapse.experiments.gold.persistence import store_transaction

    fence = coordinator(tmp_path)
    opened: list[object] = []

    def read_and_open():
        # A real writer that begins a transaction and has not finished it when
        # the capture takes its exit epoch.
        interval = store_transaction(fence)
        interval.__enter__()
        opened.append(interval)
        return anchors()

    with pytest.raises(C.FenceViolation) as excinfo:
        C.read_current_authority_state(controller(heads=read_and_open), fence=fence)
    assert excinfo.value.failure_code is C.FenceFailureCode.MUTATION_IN_FLIGHT_AT_EXIT
    opened[0].__exit__(None, None, None)


def test_an_epoch_that_goes_backwards_is_refused_separately(tmp_path: Path) -> None:
    """A decreasing epoch is not a tear — it is a store that rewound.

    Both refuse, and folding them together would tell an operator the wrong
    thing: a tear means two writers raced, a rewind means history was rolled
    back, and those get investigated in different places. The rewind is produced
    by truncating the coordinator's own append-only journal, which is what a
    restored-from-backup coordinator looks like on disk.
    """

    from synapse.experiments.gold.persistence import JOURNAL_FRAME_MAGIC_V1

    fence = coordinator(tmp_path)
    for _ in range(3):
        mutate(fence)
    epoch_path = fence.directory / "fence.journal"

    def rewind_then_answer():
        epoch_path.write_bytes(JOURNAL_FRAME_MAGIC_V1)
        return anchors()

    with pytest.raises(C.FenceViolation) as excinfo:
        C.read_current_authority_state(controller(heads=rewind_then_answer), fence=fence)
    assert excinfo.value.failure_code is C.FenceFailureCode.EPOCH_WENT_BACKWARDS


def test_a_world_that_moves_after_the_read_is_no_longer_current(tmp_path: Path) -> None:
    """Coherent at capture and current at use are two different claims."""

    fence = coordinator(tmp_path)
    state = C.read_current_authority_state(controller(), fence=fence)
    C.require_untorn_state(state, fence=fence)

    mutate(fence)
    with pytest.raises(C.FenceViolation) as excinfo:
        C.require_untorn_state(state, fence=fence)
    assert excinfo.value.failure_code is C.FenceFailureCode.OBSERVATION_TORN


def test_a_coordinator_that_is_not_this_one_is_refused_by_name(tmp_path: Path) -> None:
    """A matching epoch is not an identity, and this is the case that proves it.

    Two real coordinators, deliberately parked on the same even number. Every
    value a shape check can see agrees. Only the recorded identity separates
    them, and the refusal must say so — reporting a tear would send an operator
    hunting a concurrent writer that does not exist.
    """

    mine = coordinator(tmp_path / "mine")
    theirs = coordinator(tmp_path / "theirs")
    state = C.read_current_authority_state(controller(), fence=mine)

    assert theirs.current_epoch() == mine.current_epoch(), (
        "the substitution is only interesting while the numbers agree"
    )
    assert theirs.coordinator_id() != mine.coordinator_id()

    with pytest.raises(C.FenceViolation) as excinfo:
        C.require_untorn_state(state, fence=theirs)
    assert excinfo.value.failure_code is C.FenceFailureCode.COORDINATOR_MISMATCH


def test_a_participating_store_on_another_coordinator_is_refused(tmp_path: Path) -> None:
    """The arrangement the previous suite made and could not see.

    The reader's barrier counts one coordinator's mutations; the journal it is
    about to be combined with answers to another. Both are real, both work, and
    the reader's epoch is silent about every write the journal takes.
    """

    mine = coordinator(tmp_path / "mine")
    journal = _durable_journal(tmp_path / "theirs")
    assert journal.mutation_fence.coordinator_id() != mine.coordinator_id()

    with pytest.raises(C.FenceViolation) as excinfo:
        C.read_current_authority_state(controller(), fence=mine, participants=(journal,))
    assert excinfo.value.failure_code is C.FenceFailureCode.COORDINATOR_MISMATCH


def test_a_participant_attached_to_nothing_is_a_separate_refusal(tmp_path: Path) -> None:
    """Unattached is not the same as attached elsewhere (NR-10).

    A store on another coordinator has been shown to disagree. A store on none at
    all has been shown to be unaccounted for, and the two are fixed differently:
    one is miswiring, the other is a store nobody ever coordinated.
    """

    class Unattached:
        pass

    with pytest.raises(C.FenceViolation) as excinfo:
        C.read_current_authority_state(
            controller(), fence=coordinator(tmp_path), participants=(Unattached(),)
        )
    assert excinfo.value.failure_code is C.FenceFailureCode.COORDINATOR_UNKNOWN


def test_an_unreachable_fence_is_an_outage_not_an_admission(tmp_path: Path) -> None:
    """A coordinator that cannot answer admits nothing.

    Produced on disk rather than by a stub that raises on request: the epoch
    journal is replaced by a file this adapter did not write, which is what a
    corrupted or foreign coordinator directory actually looks like.
    """

    fence = coordinator(tmp_path)
    (fence.directory / "fence.journal").write_bytes(b"not a journal this adapter wrote")

    with pytest.raises(C.FenceViolation) as excinfo:
        C.read_current_authority_state(controller(), fence=fence)
    assert excinfo.value.failure_code is C.FenceFailureCode.FENCE_UNAVAILABLE


@pytest.mark.parametrize("bogus", [None, "9", 9.0, True])
def test_a_fence_answering_with_the_wrong_type_is_a_contract_violation(bogus) -> None:
    """Same three-way split the gate probes use: broken adapter, not outage.

    The stub here answers with the wrong *type*, which is malformedness rather
    than a second version of the coordination semantics — there is nothing to
    implement, only a contract to break.
    """

    class Bogus:
        def coordinator_id(self) -> str:
            return "bogus"

        def current_epoch(self):
            return bogus

        def mutating(self):
            raise AssertionError("nothing here should reach a mutation")

        def exclusive(self):
            raise AssertionError("nothing here should take a lock")

    with pytest.raises(C.FenceViolation) as excinfo:
        C.read_current_authority_state(controller(), fence=Bogus())
    assert excinfo.value.failure_code is C.FenceFailureCode.TYPE_MISMATCH


def test_a_read_window_cannot_be_built_outside_the_factory() -> None:
    with pytest.raises(TypeError):
        C.CoordinatedReadWindow()
    with pytest.raises(TypeError):
        C.FencedAuthorityState()


def test_a_resealed_fenced_state_is_refused(tmp_path: Path) -> None:
    state = C.read_current_authority_state(controller(), fence=coordinator(tmp_path))
    object.__setattr__(state, "_trusted_seal", object())
    with pytest.raises(C.FenceViolation) as excinfo:
        C.validate_fenced_authority_state(state)
    assert excinfo.value.failure_code is C.FenceFailureCode.TRUSTED_OBJECT_FORGED


def test_a_fenced_state_whose_epochs_disagree_is_refused_after_the_fact(tmp_path: Path) -> None:
    """Validation re-checks the invariant rather than trusting construction."""

    state = C.read_current_authority_state(controller(), fence=coordinator(tmp_path))
    object.__setattr__(state, "exit_epoch", state.window.entry_epoch + 2)
    with pytest.raises(C.FenceViolation) as excinfo:
        C.validate_fenced_authority_state(state)
    assert excinfo.value.failure_code is C.FenceFailureCode.OBSERVATION_TORN


@pytest.mark.parametrize("missing", ["coordinator_id", "current_epoch", "mutating", "exclusive"])
def test_an_incomplete_fence_is_refused(missing: str) -> None:
    """The port is the seqlock contract, and no alias stands in for the old one.

    ``acquire_lease``/``release_lease`` are not accepted, not aliased and not
    shimmed. A fence that offers them and nothing else is missing every method
    this module uses, which is the accurate answer.
    """

    class Partial:
        pass

    for name in ("coordinator_id", "current_epoch", "mutating", "exclusive"):
        if name != missing:
            setattr(Partial, name, lambda self, *args: 1)

    with pytest.raises(C.FenceViolation) as excinfo:
        C.require_snapshot_fence(Partial())
    assert excinfo.value.failure_code is C.FenceFailureCode.TYPE_MISMATCH


def test_the_removed_lease_port_is_not_accepted_under_any_name() -> None:
    """No shim, no alias, no compatible façade — the directive, checked.

    An object offering exactly the old lease API is not a coordinator. If some
    later change reintroduced ``acquire_lease`` as a synonym for anything here,
    this is where it would show up.
    """

    class OldLeasePort:
        def acquire_lease(self) -> str:
            return "lease-1"

        def current_epoch(self) -> int:
            return 4

        def release_lease(self, lease_id: str) -> None:
            return None

    with pytest.raises(C.FenceViolation) as excinfo:
        C.require_snapshot_fence(OldLeasePort())
    assert excinfo.value.failure_code is C.FenceFailureCode.TYPE_MISMATCH
    assert not hasattr(C, "CoordinatedFenceLease")
    assert not hasattr(C, "acquire_fence_lease")
    assert not hasattr(C, "validate_fence_lease")


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


def _handle_and_chain(control, journal, *, authority: str = "gate-authority"):
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
        **entitlement(authority=authority),
    )
    evidence = S.commit_gate_chain(chain, store=journal, trusted_clock=lambda: NOW)
    receipts = evidence.receipts
    head_set = A.capture_authority_heads(control)
    handle = A.admit_for_consumption(
        chain, controller=control, subject_refs=SUBJECTS,
        consumer_context_ref=CONTEXT_REF, boundary_ref=BOUNDARY_REF,
        policy_version="policy-v1", receipts=receipts, head_set=head_set, journal=journal,
        **entitlement(authority=authority),
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
        fence=coordinator(tmp_path), requested=REQUEST,
        **entitlement(),
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
    fence = coordinator(tmp_path)

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
            **entitlement(),
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.NOT_ADMITTED


def test_a_torn_world_yields_no_fresh_admission(tmp_path: Path) -> None:
    """Everything below the capture decides against it, so a torn read stops first."""

    from synapse.experiments.gold import point_of_use as P
    from tests.test_stage4_gold_admission import Journal, REQUEST

    control = controller()
    journal = _durable_journal(tmp_path)
    fence = coordinator(tmp_path)
    chain, handle, evidence = _handle_and_chain(control, journal)

    # The tearing reader belongs to the call under test, not to the setup: the
    # chain above must be built against a quiet world or the refusal here could
    # be about something the harness did.
    torn = controller(heads=tearing_head_reader(fence))
    before = journal._digests()

    with pytest.raises(C.FenceViolation) as excinfo:
        P.admit_for_use_now(
            handle, controller=torn, chain=chain, evidence=evidence, journal=journal,
            fence=fence, requested=REQUEST,
            **entitlement(),
        )
    assert excinfo.value.failure_code is C.FenceFailureCode.OBSERVATION_TORN
    assert journal._digests() == before, "a torn capture must not have written a verdict"


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
        fence=coordinator(tmp_path), requested=REQUEST,
        **entitlement(),
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
        fence=coordinator(tmp_path), requested=REQUEST,
        **entitlement(),
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
    fence = coordinator(tmp_path)
    journal = _durable_journal(tmp_path)
    chain, handle, evidence = _handle_and_chain(control, journal)

    class Interfering:
        """The real journal, with another writer trying to land as the write does.

        A delegating wrapper rather than a subclass of the in-memory double: the
        point of use asks this store for positions and anchors now, so a stand-in
        that cannot answer them would fail for the wrong reason and prove nothing
        about the window this test is named for.

        The interference is a *real* attempted transaction, which is what makes
        the outcome meaningful: a wrapper that merely incremented a counter would
        have been asserting against an invented mechanism.
        """

        def __getattr__(self, name):
            return getattr(journal, name)

        def append_record(self, payload: bytes, *, ticket=None) -> None:
            journal.append_record(payload, ticket=ticket)
            mutate(fence)

    with pytest.raises(J.JournalAdapterViolation) as excinfo:
        P.admit_for_use_now(
            handle, controller=control, chain=chain, evidence=evidence, journal=Interfering(),
            fence=fence, requested=REQUEST,
            **entitlement(),
        )
    # The second writer never gets to write: the interval is open, so its own
    # attempt to open one is refused, and the transaction it interrupted ends as
    # an abort rather than reporting success.
    assert excinfo.value.failure_code is J.JournalAdapterFailureCode.MUTATION_ABORTED
    assert fence.current_epoch() % 2 == 1, "an abandoned transaction stays fail-closed"


def test_a_writer_landing_after_the_interval_is_detected_as_drift(tmp_path: Path) -> None:
    """The other side of the window: interference the exclusion did not cover.

    An uncoordinated writer — one that never took this coordinator's lock — can
    still land between a transaction's own interval closing and its settle check.
    Its write is a complete, legitimate-looking transaction, so the epoch is even
    and two too high, and equality is what catches it.
    """

    fence = coordinator(tmp_path)
    state = C.read_current_authority_state(controller(), fence=fence)

    mutate(fence)  # this transaction's own append
    assert C.settle_after_own_mutation(state, fence=fence, own_intervals=1) == (
        state.window.entry_epoch + 2
    )

    mutate(fence)  # somebody else's
    with pytest.raises(C.FenceViolation) as excinfo:
        C.settle_after_own_mutation(state, fence=fence, own_intervals=1)
    assert excinfo.value.failure_code is C.FenceFailureCode.OBSERVATION_TORN


def test_an_interval_left_open_by_a_crash_blocks_the_next_writer(tmp_path: Path) -> None:
    """Fail-closed survives the process, which is the point of putting it on disk.

    The interval is opened and abandoned exactly as a crash would leave it. A
    fresh coordinator object over the same directory — a new process, as far as
    the store is concerned — finds the epoch odd and refuses to write on top of
    it. Nothing here repairs anything: an explicit recovery is a decision, and
    this module does not get to make it on the way past.
    """

    from synapse.experiments.gold.persistence import store_transaction

    fence = coordinator(tmp_path)
    interval = store_transaction(fence)
    interval.__enter__()
    assert fence.current_epoch() % 2 == 1

    reopened = J.FileSnapshotFence(fence.directory)
    assert reopened.coordinator_id() == fence.coordinator_id(), "same directory, same coordinator"

    with pytest.raises(J.JournalAdapterViolation) as excinfo:
        mutate(reopened)
    assert excinfo.value.failure_code is J.JournalAdapterFailureCode.MUTATION_INTERVAL_OPEN

    with pytest.raises(C.FenceViolation) as caught:
        C.read_current_authority_state(controller(), fence=reopened)
    assert caught.value.failure_code is C.FenceFailureCode.MUTATION_IN_FLIGHT_AT_ENTRY

    # Retire the abandoned interval deterministically rather than leaving it to
    # be collected: the abort is what a crash produces, and producing it here
    # keeps the epoch odd — which is the state this test is about.
    with pytest.raises(J.JournalAdapterViolation):
        interval.__exit__(RuntimeError, RuntimeError("the writer died"), None)
    assert reopened.current_epoch() % 2 == 1


def test_the_point_of_use_refuses_a_journal_on_another_coordinator(tmp_path: Path) -> None:
    """The arrangement that made the previous suite green while proving nothing.

    Both objects are real and both work. The reader's barrier counts one
    coordinator's mutations and the journal it is about to combine them with
    answers to another, so the epoch it compares is silent about every write that
    journal takes. The old port was satisfied by shape, so this passed every
    structural check there was.

    Nothing must be written on the way out: a refusal that had already appended
    its fresh verdict would have committed a decision it then declined to make.
    """

    from synapse.experiments.gold import point_of_use as P
    from tests.test_stage4_gold_admission import REQUEST

    control = controller()
    journal = _durable_journal(tmp_path / "journals")
    stranger = coordinator(tmp_path / "elsewhere")
    chain, handle, evidence = _handle_and_chain(control, journal)
    before = journal._digests()

    assert stranger.coordinator_id() != journal.mutation_fence.coordinator_id()

    with pytest.raises(C.FenceViolation) as excinfo:
        P.admit_for_use_now(
            handle, controller=control, chain=chain, evidence=evidence, journal=journal,
            fence=stranger, requested=REQUEST,
            **entitlement(),
        )
    assert excinfo.value.failure_code is C.FenceFailureCode.COORDINATOR_MISMATCH
    assert journal._digests() == before, "a refused admission writes nothing"


def test_no_second_writer_can_act_while_the_point_of_use_is_deciding(tmp_path: Path) -> None:
    """Exclusion, which the epoch cannot supply and no case here demanded.

    The campaign found this: deleting ``fence.exclusive()`` from
    ``admit_for_use_now`` killed nothing. Every other case here proves
    interference is *detected*, and detection is not enough for a sequence that
    reads the world and then writes to it — such a sequence can detect
    interference forever and never finish.

    The attempt is made from inside the transaction, through a probe the gate
    consults while deciding, by a second coordinator object over the same
    directory. That is a genuine second writer: same lock file, different
    process as far as the filesystem is concerned.
    """

    from synapse.experiments.gold import point_of_use as P
    from synapse.experiments.gold.persistence import PersistenceFailureCode, PersistenceViolation
    from tests.test_stage4_gold_admission import REQUEST, compat_finding

    fence = coordinator(tmp_path)
    journal = _durable_journal(tmp_path)
    refused: list[str] = []

    def probe(item, ctx):
        # Consulted inside the transaction, which is exactly where a second
        # writer would try to slip in.
        try:
            with J.FileSnapshotFence(fence.directory).exclusive():
                refused.append("acquired")
        except PersistenceViolation as exc:
            refused.append(exc.failure_code.value)
        return compat_finding(item, ctx)

    control = controller(compat=probe)
    chain, handle, evidence = _handle_and_chain(control, journal)
    refused.clear()

    knowledge = P.admit_for_use_now(
        handle, controller=control, chain=chain, evidence=evidence, journal=journal,
        fence=fence, requested=REQUEST,
        **entitlement(),
    )

    assert refused, "the probe must actually have tried to take the writer lock"
    assert set(refused) == {PersistenceFailureCode.LOCK_BUSY.value}, (
        f"a second writer got in while the point of use was deciding: {refused}"
    )
    P.validate_current_admitted_knowledge(knowledge)


def test_the_handle_recheck_refuses_a_journal_on_another_coordinator(tmp_path: Path) -> None:
    """The same substitution, on the read-only point-of-use path.

    ``require_current_admitted_handle`` writes nothing, which is exactly why it
    was easy to leave its journal uncoordinated: with no append there is no
    obviously missing interval. But its claim is a *coherent* observation of six
    stores, and a journal counted by a different coordinator is silent about
    every write it takes. The campaign found this by deleting the participant
    binding and killing nothing.
    """

    from synapse.experiments.gold import point_of_use as P
    from tests.test_stage4_gold_admission import Journal, admitted_handle, full_chain

    stranger = coordinator(tmp_path / "elsewhere")
    control, journal, handle = admitted_handle()
    consumption = full_chain(control)[3]
    assert journal.mutation_fence.coordinator_id() != stranger.coordinator_id()

    # The handle's own coordinator confirms it, which is the control.
    P.require_current_admitted_handle(
        handle, controller=control, journal=journal, consumption_decision=consumption,
        fence=journal.mutation_fence,
    )

    with pytest.raises(C.FenceViolation) as excinfo:
        P.require_current_admitted_handle(
            handle, controller=control, journal=journal, consumption_decision=consumption,
            fence=stranger,
        )
    assert excinfo.value.failure_code is C.FenceFailureCode.COORDINATOR_MISMATCH


def test_the_point_of_uses_own_append_is_not_mistaken_for_drift(tmp_path: Path) -> None:
    """The other half of the previous test, and the one that keeps it honest.

    A check that refused any movement at all would pass the drift case for the
    wrong reason — it would be refusing the transaction's own write. So the
    quiet path is asserted too: exactly one interval, opened by this call,
    epoch entry+2, and the result bound to that final even value rather than to
    the entry epoch it has since made stale.
    """

    from synapse.experiments.gold import point_of_use as P
    from tests.test_stage4_gold_admission import REQUEST

    control = controller()
    fence = coordinator(tmp_path)
    journal = _durable_journal(tmp_path)
    chain, handle, evidence = _handle_and_chain(control, journal)
    entry = fence.current_epoch()

    knowledge = P.admit_for_use_now(
        handle, controller=control, chain=chain, evidence=evidence, journal=journal,
        fence=fence, requested=REQUEST,
        **entitlement(),
    )

    assert fence.current_epoch() == entry + 2, "one interval, and it was this call's own"
    assert knowledge.observed_epoch == entry + 2, (
        "the result binds to the settled epoch after its own append, not before it"
    )
    assert knowledge.observed_epoch % 2 == 0


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
            fence=coordinator(tmp_path), requested=REQUEST,
            **entitlement(),
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
            fence=coordinator(tmp_path), requested=REQUEST,
            **entitlement(),
        )
    assert caught.value.failure_code is A.AdmissionFailureCode.JOURNAL_ROLLED_BACK


def test_a_foreign_chain_cannot_be_substituted_for_the_handles_own(tmp_path: Path) -> None:
    """The audit's A/B substitution, and the reason value checks were not enough.

    Chain B is legitimate: a real four-gate run by another authority, committed to
    another journal, over the same subjects, consumer context, boundary and policy.
    Every check the point of use performed was a check on *values*, and values are
    exactly what two legitimate chains share. So a handle minted from A, whose own
    history had been deleted, was admitted on the strength of B.

    Identity separates them and was recorded on both sides all along — the handle
    carries the consumption decision id it was minted from and the receipt that
    committed it.
    """

    from synapse.experiments.gold import point_of_use as P
    from tests.test_stage4_gold_admission import REQUEST

    control_a = controller(authority="gate-authority-A")
    control_b = controller(authority="gate-authority-B")
    journal_a = _durable_journal(tmp_path / "a")
    journal_b = _durable_journal(tmp_path / "b")
    _, handle, _ = _handle_and_chain(control_a, journal_a, authority="gate-authority-A")
    chain_b, _, evidence_b = _handle_and_chain(control_b, journal_b, authority="gate-authority-B")

    assert chain_b.consumption.gate_decision_id.value != handle.consumption_decision_id.value
    assert chain_b.consumption.subject_refs == handle.subject_refs, (
        "the substitution is only interesting while every value agrees"
    )
    journal_a.path.unlink()
    before = journal_b._digests()

    with pytest.raises(A.AdmissionViolation) as caught:
        P.admit_for_use_now(
            handle, controller=control_b, chain=chain_b, evidence=evidence_b,
            journal=journal_b, fence=coordinator(tmp_path), requested=REQUEST,
            **entitlement(),
        )
    assert caught.value.failure_code is A.AdmissionFailureCode.CHAIN_NOT_DURABLE
    assert journal_b._digests() == before, (
        "a refused substitution must not have appended its fresh verdict on the way out"
    )


def test_the_evidence_must_end_in_the_receipt_the_handle_carries(tmp_path: Path) -> None:
    """The half a decision-id comparison alone would miss.

    The chain handed in is genuinely the handle's own, so the identity check on
    the consumption decision passes. The *evidence* is another chain's, so what it
    proves durable is not what this handle rests on — and without comparing the
    final receipt, the recovery below would happily verify a run the handle never
    named.

    Note what is deliberately *not* asserted here: re-committing the handle's own
    chain into a second journal produces byte-identical decisions, receipts and
    anchors, and is refused by nothing. An earlier draft of this test demanded a
    refusal for that case. There is no violation in it — the records are the same
    records, and making them the same required the same authority — so the demand
    was mine rather than the contract's, and it is withdrawn.
    """

    from synapse.experiments.gold import point_of_use as P
    from tests.test_stage4_gold_admission import REQUEST

    control = controller()
    journal = _durable_journal(tmp_path / "one")
    chain, handle, _ = _handle_and_chain(control, journal)

    other_control = controller(authority="gate-authority-other")
    other_journal = _durable_journal(tmp_path / "two")
    _, _, foreign_evidence = _handle_and_chain(other_control, other_journal, authority="gate-authority-other")
    assert foreign_evidence.receipts[-1].decision_digest != handle.commit_receipt.decision_digest

    with pytest.raises(A.AdmissionViolation) as caught:
        P.admit_for_use_now(
            handle, controller=control, chain=chain, evidence=foreign_evidence,
            journal=journal, fence=coordinator(tmp_path), requested=REQUEST,
            **entitlement(),
        )
    assert caught.value.failure_code is A.AdmissionFailureCode.CHAIN_NOT_DURABLE
    assert "does not end in the receipt" in caught.value.detail


def test_the_point_of_use_refuses_another_authoritys_entitlement(tmp_path: Path) -> None:
    """The consumer is the last verifier, and its own copies must be able to say no."""

    from synapse.experiments.gold import point_of_use as P
    from tests.test_stage4_gold_admission import REQUEST

    control = controller()
    journal = _durable_journal(tmp_path)
    chain, handle, evidence = _handle_and_chain(control, journal)

    with pytest.raises(A.AdmissionViolation) as caught:
        P.admit_for_use_now(
            handle, controller=control, chain=chain, evidence=evidence,
            journal=journal, fence=coordinator(tmp_path), requested=REQUEST,
            **entitlement(authority="an-authority-that-decided-nothing"),
        )
    assert caught.value.failure_code is A.AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT
