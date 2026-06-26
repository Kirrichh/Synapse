"""Manual live-proof entrypoint for the external coding-worker adapter."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

from synapse.change.workspace import cleanup_worktree, create_detached_worktree, find_repo_root

from .mini_adapter import MiniAdapterConfig, run_mini_worker


NOT_RUN_REASON = "mini-swe-agent or GEMINI_API_KEY not provided"


def main() -> int:
    config = MiniAdapterConfig.from_env()
    if not os.environ.get("GEMINI_API_KEY") or shutil.which(config.command[0]) is None:
        print("manual live worker run: NOT_RUN")
        print(f"reason: {NOT_RUN_REASON}")
        return 0

    repo_root = find_repo_root(Path.cwd())
    task_path = repo_root / "personal_slice" / "task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    worktree = create_detached_worktree(repo_root, "HEAD")
    try:
        result = run_mini_worker(
            worktree.path,
            task,
            tuple(str(path) for path in task.get("allowed_scope", ())),
            config=config,
        )
    finally:
        cleanup_worktree(worktree, keep=False)

    print(f"worker_status={result.worker_status.value}")
    print(f"token_status={result.usage.token_status.value}")
    print(f"total_tokens={result.usage.total_tokens}")
    print(f"thinking_included={str(result.usage.thinking_included).lower()}")
    print(f"touched_files={','.join(result.touched_files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
