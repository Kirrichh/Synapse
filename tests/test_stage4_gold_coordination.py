"""Stage 4 fenced current-state capture (Patch 8 repair, round 8).

§22 requires the consumption gate to see one coherent world. A single reader
call is not that: it may consult six stores at six moments and return a set that
describes no world that ever existed, with every anchor individually valid. The
fence makes that detectable, and these tests are mostly about the detection —
the happy path is one test, the ways a torn or dishonest read is refused are the
rest.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib

import pytest

from synapse.experiments.gold import admission as A
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
