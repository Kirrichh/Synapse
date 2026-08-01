from __future__ import annotations

from pathlib import Path

import pytest

from synapse.experiments.gold.coordination import (
    CoordinatedFenceLease,
    coordinated_store_write,
    open_coordinated_snapshot_fence,
    require_coordinated_fence_lease,
)
from synapse.experiments.gold.knowledge import create_snapshot_coordination_context
from synapse.experiments.gold.persistence import (
    PersistenceFailureCode,
    PersistenceViolation,
)

from tests.test_stage4_gold_authority_overlay import build_authority_bundle


def _context(tmp_path: Path, suffix: str = "one"):
    authority = build_authority_bundle()
    context = create_snapshot_coordination_context(
        coordination_root=(tmp_path / f"coordination-{suffix}").resolve(),
        fence_identity=f"patch78-fence-{suffix}",
        authority_binding=authority.binding,
    )
    return authority, context


def test_s4_acc_coordination_nested_store_write_reuses_exact_active_lease(
    tmp_path: Path,
) -> None:
    _, context = _context(tmp_path)
    fence = open_coordinated_snapshot_fence(context)

    with fence.acquire() as lease:
        epoch = require_coordinated_fence_lease(lease, expected_context=context)
        with coordinated_store_write(
            fence=fence,
            context=context,
            store_lock_path=(tmp_path / "store.lock").resolve(),
            fence_lease=lease,
        ) as nested:
            assert nested is lease
            nested.record_store_mutation(
                store_name="library",
                head_identity="library-head-1",
                store_sequence=1,
            )

    assert fence.current_store_heads() == (
        ("library", "library-head-1", 1, epoch),
    )


def test_s4_acc_coordination_rejects_released_foreign_and_forged_leases(
    tmp_path: Path,
) -> None:
    _, context = _context(tmp_path, "primary")
    _, foreign_context = _context(tmp_path, "foreign")
    fence = open_coordinated_snapshot_fence(context)

    with fence.acquire() as lease:
        with pytest.raises(PersistenceViolation) as foreign:
            require_coordinated_fence_lease(
                lease,
                expected_context=foreign_context,
            )
        assert foreign.value.failure_code is PersistenceFailureCode.LOCK_FAILED

    with pytest.raises(PersistenceViolation) as released:
        require_coordinated_fence_lease(lease, expected_context=context)
    assert released.value.failure_code is PersistenceFailureCode.LOCK_FAILED

    forged = object.__new__(CoordinatedFenceLease)
    with pytest.raises(PersistenceViolation) as unsealed:
        require_coordinated_fence_lease(forged, expected_context=context)
    assert unsealed.value.failure_code is PersistenceFailureCode.LOCK_FAILED


def test_s4_acc_coordination_detects_low_level_lease_mutation(tmp_path: Path) -> None:
    _, context = _context(tmp_path)
    fence = open_coordinated_snapshot_fence(context)

    with fence.acquire() as lease:
        object.__setattr__(lease, "epoch", lease.epoch + 1)
        with pytest.raises(PersistenceViolation) as raised:
            require_coordinated_fence_lease(lease, expected_context=context)

    assert raised.value.failure_code is PersistenceFailureCode.LOCK_FAILED
