"""Git adapter for the isolated checkout one attempt's worker edits (§26).

Part of ``attempt_input_source``'s responsibility, held behind its
``AttemptWorktreePort``: turning "this attempt needs a workspace at the run's
committed base revision" into git commands. It converts types and calls a
port; it owns no policy and no state.

Two properties are the reason this is production rather than a fixture. Each
attempt gets its **own** checkout, because a second attempt that inherits the
first one's working tree inherits its edits, and the run would no longer be
able to say which attempt produced a change. And the checkout is taken at the
run's frozen ``base_revision``, not at whatever the source repository points
at now, so a repository that moves under a running attempt cannot silently
change what that attempt was working from.

Git is reached through ``synapse.change.workspace``, which already owns the
repository-command boundary; the gold package does not open subprocesses of
its own.
"""

from __future__ import annotations

from pathlib import Path

from synapse.change.workspace import GitWorkspaceError, git

from .attempt_input_source import AttemptWorktreePort
from .models import GoldRunManifest
from .vocabulary import GoldRunFailureCode, GoldRunViolation


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


class GitAttemptWorktrees:
    """Clone one isolated checkout per attempt from the run's base revision."""

    def __init__(self, *, source_repo: Path, worktree_root: Path) -> None:
        for name, value in (("source_repo", source_repo), ("worktree_root", worktree_root)):
            if type(value) is not type(Path()) or not value.is_absolute():
                raise _fail(
                    GoldRunFailureCode.TYPE_MISMATCH,
                    f"{name} must be an exact absolute Path",
                )
        if not source_repo.is_dir():
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "the source repository must exist before a run prepares an attempt",
            )
        self._source_repo = source_repo
        self._worktree_root = worktree_root

    def worktree_for_attempt(
        self, *, manifest: GoldRunManifest, attempt_index: int
    ) -> Path:
        """Materialize this attempt's checkout, refusing to reuse another's.

        An existing directory is refused rather than reused or deleted: it means
        either this attempt id already ran — which the run's own records, not a
        clone, must decide — or another attempt is using that path. Removing it
        here would destroy the evidence of whichever case it is.
        """

        if type(manifest) is not GoldRunManifest:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "manifest must be exact")
        if type(attempt_index) is not int or attempt_index < 1:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "attempt index must be a one-based integer",
            )
        target = self._worktree_root / str(attempt_index)
        if target.exists():
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                f"attempt {attempt_index} already has a materialized worktree",
            )
        self._worktree_root.mkdir(parents=True, exist_ok=True)
        revision = manifest.config.base_revision
        try:
            git(
                ["clone", "--quiet", "--no-local", str(self._source_repo), str(target)],
                cwd=self._worktree_root,
            )
            git(["checkout", "--quiet", revision], cwd=target)
        except GitWorkspaceError as exc:
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                f"attempt {attempt_index} could not be materialized at {revision}",
            ) from exc
        return target


def require_attempt_worktrees(value: object) -> AttemptWorktreePort:
    """Refuse anything that does not implement the declared worktree port."""

    if not isinstance(value, AttemptWorktreePort):
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "attempt worktrees must implement AttemptWorktreePort",
        )
    return value


__all__ = ["GitAttemptWorktrees", "require_attempt_worktrees"]
