"""The one mutation fence a test harness hands to every store under one root.

Every durable authority store now takes a fence and advances it on each append,
so that `coordination.capture_authority_heads` can detect a read torn across two
of them. The fence has to be *shared* for that to mean anything: one counter
across all stores is what lets a reader learn that lifecycle moved while taint
was being read, and a per-store fence would report every observation as coherent.

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

    For suites whose subject is not coordination. The root observation still runs
    inside a lease and still compares the epoch across the read, so the barrier is
    exercised rather than bypassed — what is absent is a concurrent writer, and a
    quiet fence therefore reports every observation as coherent, which is the
    truth for a test that mutates nothing while it reads.

    A test that wants a torn read arranges a real bump, and there is one below in
    the §21 suite that does exactly that.
    """

    import tempfile

    return FileSnapshotFence(Path(tempfile.mkdtemp(prefix="quiet-fence-")))
