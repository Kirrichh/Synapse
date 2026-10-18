"""A golden artifact's hash chain is integrity, not authentication (RFC-ASYNC-EXECUTION section 32).

A partial edit breaks the chain and is refused. A writer who recomputes every
hash forges an artifact that replays: nothing in the artifact authenticates its
author. Comparing it with the original artifact exposes the forgery at its first
changed event, which is the verification path for anyone holding the original.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from synapse import Interpreter
from synapse.golden_replay import (
    CacheOnlyLLMBackend,
    DeterministicReplayError,
    compile_source,
    record_source,
    replay_mock_artifact,
    state_sanity_from_snapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REVIEW = 'agent Reviewer { model "mock" }\nlet r = Reviewer()\nprint(llm "Review: drop the orders table")\n'


def _forge(original: Path, forged: Path, answer: str) -> None:
    """Rewrite the recorded model answer and recompute every hash the artifact carries."""
    shutil.copytree(original, forged)
    cache = json.loads((forged / "llm_cache.mock.json").read_text(encoding="utf-8"))
    for entry in cache.values():
        entry["results"] = [answer for _ in entry["results"]]
    (forged / "llm_cache.mock.json").write_text(json.dumps(cache), encoding="utf-8")
    manifest = json.loads((forged / "manifest.json").read_text(encoding="utf-8"))
    interp = Interpreter.restore_snapshot(json.loads((forged / "initial_vm_snapshot.json").read_text(encoding="utf-8")))
    interp.run_id = manifest["metadata"]["run_id"]
    interp.llm_backend = CacheOnlyLLMBackend(cache)
    source = (forged / "source.syn").read_text(encoding="utf-8")
    interp.source_code = source
    interp.interpret(compile_source(source))
    (forged / "history.json").write_text(json.dumps(interp.execution_history), encoding="utf-8")
    manifest["final"].update(history_length=len(interp.execution_history),
                             final_history_hash=interp.compute_history_hash(),
                             state_sanity=state_sanity_from_snapshot(interp.snapshot()))
    (forged / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_a_consistent_forgery_replays_and_comparison_with_the_original_finds_it(tmp_path: Path) -> None:
    original, forged = tmp_path / "original", tmp_path / "forged"
    record_source(REVIEW, original)
    _forge(original, forged, "LGTM, no risks at all")

    assert replay_mock_artifact(original).drift == 0
    assert replay_mock_artifact(forged).drift == 0  # The chain proves integrity, never authorship.

    compared = subprocess.run(
        [sys.executable, "-m", "synapse", "debug", "compare", str(original), str(forged)],
        cwd=REPO_ROOT, env={**os.environ, "PYTHONPATH": str(REPO_ROOT)}, capture_output=True, text=True, timeout=60,
    )
    assert compared.returncode == 7, compared.stderr
    divergence = json.loads(compared.stdout)
    assert divergence["first_divergence_index"] == 0
    assert divergence["right_event"]["result"] == "LGTM, no risks at all"


def test_an_edited_answer_without_recomputed_hashes_is_refused_as_drift(tmp_path: Path) -> None:
    original, edited = tmp_path / "original", tmp_path / "edited"
    record_source(REVIEW, original)
    shutil.copytree(original, edited)
    cache = json.loads((edited / "llm_cache.mock.json").read_text(encoding="utf-8"))
    for entry in cache.values():
        entry["results"] = ["LGTM, no risks at all" for _ in entry["results"]]
    (edited / "llm_cache.mock.json").write_text(json.dumps(cache), encoding="utf-8")

    with pytest.raises(DeterministicReplayError, match="drift detected"):
        replay_mock_artifact(edited)
