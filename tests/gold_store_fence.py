"""The one mutation fence a test harness hands to every store under one root.

Every durable authority store takes a coordinator, opens an interval around each
transaction, and cannot write at all without one — so
`coordination.read_current_authority_state` can detect a read torn across two of
them. The coordinator has to be *shared* for that to mean anything: one counter
across all stores is what lets a reader learn that lifecycle moved while taint
was being read, and a per-store coordinator would report every observation as
coherent. Its identity is what makes "shared" checkable rather than assumed.

So harnesses take theirs from here, keyed by root, and get the production
`FileSnapshotFence` rather than a stub. A test double with a counter attribute
would demonstrate the wiring against an object that cannot survive a restart or
notice a second process — the exact substitution NR-06 draws the line at, and the
reason `admission_journal` exists in the first place.

This module is deliberately not named ``test_*``: it is a fixture, not a suite.
"""

from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold.admission_journal import FileSnapshotFence

#: One fence per root, because two fences under one root are two counters and a
#: reader watching either would miss half the mutations.
_FENCES: dict[str, FileSnapshotFence] = {}


def fence_for(root: Path) -> FileSnapshotFence:
    """The shared fence for every store living under ``root``."""

    directory = Path(root) / "mutation-fence"
    key = str(directory)
    fence = _FENCES.get(key)
    if fence is None:
        directory.mkdir(parents=True, exist_ok=True)
        fence = FileSnapshotFence(directory)
        _FENCES[key] = fence
    return fence


def quiet_fence() -> FileSnapshotFence:
    """A real fence in its own directory that nothing else advances.

    For suites whose subject is not coordination. The observation still brackets
    the read with two epoch readings and still checks parity at both ends, so the
    barrier is exercised rather than bypassed — what is absent is a concurrent
    writer, and a quiet coordinator therefore reports every observation as
    coherent, which is the truth for a test that mutates nothing while it reads.

    A test that wants a torn read arranges a real transaction, and the §21 and
    coordination suites both do exactly that.
    """

    import tempfile

    return FileSnapshotFence(Path(tempfile.mkdtemp(prefix="quiet-fence-")))
