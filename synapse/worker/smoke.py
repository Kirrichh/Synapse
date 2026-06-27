"""Manual live-proof entrypoint for the external coding-worker adapter."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

from synapse.change.workspace import cleanup_worktree, create_detached_worktree, find_repo_root

from .mini_adapter import MiniAdapterConfig, run_mini_worker


MINI_NOT_FOUND_REASON = "mini-swe-agent not found"
PROVIDER_CONFIG_MISSING_REASON = "worker provider config missing"


def _provider_config_present() -> bool:
    return any(
        os.environ.get(name)
        for name in (
            "SYNAPSE_MINI_WORKER_MODEL",
            "SYNAPSE_OLLAMA_API_BASE",
            "MSWEA_MODEL_NAME",
            "GEMINI_API_KEY",
        )
    )


def main() -> int:
    config = MiniAdapterConfig.from_env()
    if shutil.which(config.command[0]) is None:
        print("manual live worker run: NOT_RUN")
        print(f"reason: {MINI_NOT_FOUND_REASON}")
        return 0
    if not _provider_config_present():
        print("manual live worker run: NOT_RUN")
        print(f"reason: {PROVIDER_CONFIG_MISSING_REASON}")
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
