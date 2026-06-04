"""Alpha3e golden replay release-gate tests.

Layer 1 is a strict golden suite. Layer 2 remains a corpus smoke check and is
not frozen as a hash baseline.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse.golden_replay import (
    DEFAULT_VIRTUAL_CLOCK_START,
    DeterministicReplayError,
    record_source,
    replay_mock_artifact,
    state_sanity_from_snapshot,
)
from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.builtins import LLMBackend

ROOT = Path(__file__).resolve().parents[1]
STRICT_DIR = ROOT / "tests" / "golden_replays_alpha3e" / "strict"
STRICT_ARTIFACTS = sorted(p for p in STRICT_DIR.iterdir() if p.is_dir())


def parse_source(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


@pytest.mark.parametrize("artifact", STRICT_ARTIFACTS, ids=lambda p: p.name)
def test_layer1_strict_golden_replay_has_zero_drift(artifact: Path):
    result = replay_mock_artifact(artifact)
    assert result.drift == 0
    assert result.history_length >= 0
    assert result.program_hash.startswith("sha256:")


@pytest.mark.parametrize("artifact", STRICT_ARTIFACTS, ids=lambda p: p.name)
def test_layer1_artifacts_include_virtual_clock_contract(artifact: Path):
    manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["layer"] == "strict"
    env = manifest["environment"]
    assert env["clock_mode"] == "deterministic"
    assert env["virtual_clock_start"] == DEFAULT_VIRTUAL_CLOCK_START
    assert env["clock_step"] == 1


def test_replay_never_calls_provider(monkeypatch):
    def fail_provider(*args, **kwargs):  # pragma: no cover - should never run
        raise AssertionError("provider dispatch must not be called during mock replay")

    monkeypatch.setattr(LLMBackend, "complete", fail_provider)
    result = replay_mock_artifact(STRICT_DIR / "llm_cached")
    assert result.drift == 0


def test_missing_llm_cache_entry_is_deterministic_failure(tmp_path: Path):
    src = 'let a = llm "hello"\nprint(a)\n'
    artifact = tmp_path / "llm_missing_cache"
    record_source(src, artifact, source_path="tests/golden_sources_alpha3e/llm_missing_cache.syn")
    (artifact / "llm_cache.mock.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(DeterministicReplayError, match="missing cache entry"):
        replay_mock_artifact(artifact)


def test_stable_state_validator_ignores_new_internal_snapshot_fields(tmp_path: Path):
    src = 'let x = 1\nprint(x)\n'
    artifact = tmp_path / "stable_state"
    record_source(src, artifact, source_path="tests/golden_sources_alpha3e/stable_state.syn")
    snapshot_path = artifact / "vm_snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    before = state_sanity_from_snapshot(snapshot)
    snapshot["new_debug_counter"] = 999
    snapshot["temporary_cache_object"] = {"opaque": True}
    after = state_sanity_from_snapshot(snapshot)
    assert after == before


def test_layer2_corpus_parse_smoke_all_examples():
    example_paths = sorted((ROOT / "examples").glob("*.syn"))
    assert len(example_paths) >= 40
    failures = []
    for path in example_paths:
        try:
            parse_source(path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - failure path reports all files
            failures.append(f"{path.name}: {exc}")
    assert not failures
