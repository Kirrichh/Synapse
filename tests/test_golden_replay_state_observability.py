"""Golden-replay state observability (alpha3e-p1 schema 2).

Schema 1 recorded an interpreter snapshot that carried neither ``output_buffer``
nor ``vm_snapshots``, while ``state_sanity_from_snapshot()`` read both. Eight of
the fifteen documented stable state fields therefore resolved to the same
constant for every program: ``output_hash`` was always the hash of an empty
list, and every VM-derived depth was the default. The strict suite compared
those constants against themselves and validated nothing about program output.

Schema 2 records both fields. Schema 1 artifacts stay replayable: fields their
snapshot could not observe are excluded from comparison rather than compared
against a value the artifact never captured.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse.golden_replay import (
    SCHEMA_VERSION,
    STATE_SANITY_OBSERVABILITY_SCHEMA,
    DeterministicReplayError,
    ReplayArtifactError,
    _run_interpreter_from_source,
    comparable_state_sanity,
    record_source,
    replay_mock_artifact,
    state_sanity_from_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
LEGACY_STRICT_DIR = ROOT / "tests" / "golden_replays_alpha3e" / "strict"

PRINT_FIVE = 'let x = 2 + 3\nprint(x)\n'
PRINT_TEXT = 'print("different output")\n'
SILENT = 'let x = 2 + 3\n'

SCHEMA_2_FIELDS = (
    "output_hash",
    "final_ip",
    "stack_depth",
    "call_frame_depth",
    "guard_stack_depth",
    "context_stack_depth",
    "policy_stack_depth",
    "guard_violation_active",
)


# ---------------------------------------------------------------------------
# The snapshot now carries what the validator reads
# ---------------------------------------------------------------------------


def test_interpreter_snapshot_carries_output_and_vm_state() -> None:
    interpreter = _run_interpreter_from_source(PRINT_FIVE)
    snapshot = interpreter.snapshot()
    assert "output_buffer" in snapshot
    assert "vm_snapshots" in snapshot
    assert snapshot["output_buffer"] == ["5"]


def test_output_hash_tracks_program_output() -> None:
    printed = state_sanity_from_snapshot(_run_interpreter_from_source(PRINT_FIVE).snapshot())
    other = state_sanity_from_snapshot(_run_interpreter_from_source(PRINT_TEXT).snapshot())
    silent = state_sanity_from_snapshot(_run_interpreter_from_source(SILENT).snapshot())

    assert printed["output_hash"] != other["output_hash"]
    assert printed["output_hash"] != silent["output_hash"]
    # An empty output is a real observation, not the former universal constant.
    assert silent["output_hash"] == state_sanity_from_snapshot({"output_buffer": []})["output_hash"]


def test_snapshot_round_trip_preserves_output_buffer() -> None:
    from synapse.interpreter import Interpreter

    interpreter = _run_interpreter_from_source(PRINT_FIVE)
    restored = Interpreter.restore_snapshot(interpreter.snapshot())
    assert restored.output_buffer == ["5"]
    assert restored.vm_snapshots == []


def test_restore_tolerates_snapshots_without_the_new_fields() -> None:
    from synapse.interpreter import Interpreter

    snapshot = _run_interpreter_from_source(PRINT_FIVE).snapshot()
    snapshot.pop("output_buffer")
    snapshot.pop("vm_snapshots")
    restored = Interpreter.restore_snapshot(snapshot)
    assert restored.output_buffer == []
    assert restored.vm_snapshots == []


def test_restored_output_buffer_is_detached_from_the_snapshot() -> None:
    from synapse.interpreter import Interpreter

    snapshot = _run_interpreter_from_source(PRINT_FIVE).snapshot()
    restored = Interpreter.restore_snapshot(snapshot)
    restored.output_buffer.append("injected")
    assert snapshot["output_buffer"] == ["5"]


# ---------------------------------------------------------------------------
# Freshly recorded artifacts
# ---------------------------------------------------------------------------


def test_recorded_artifact_uses_the_current_schema(tmp_path: Path) -> None:
    manifest = record_source(PRINT_FIVE, tmp_path / "artifact")
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert SCHEMA_VERSION >= STATE_SANITY_OBSERVABILITY_SCHEMA


def test_recorded_artifact_replays_without_drift(tmp_path: Path) -> None:
    record_source(PRINT_FIVE, tmp_path / "artifact")
    result = replay_mock_artifact(tmp_path / "artifact")
    assert result.drift == 0
    assert result.state_sanity["output_hash"]


def test_recorded_output_hash_distinguishes_programs(tmp_path: Path) -> None:
    first = record_source(PRINT_FIVE, tmp_path / "first")
    second = record_source(PRINT_TEXT, tmp_path / "second")
    assert (
        first["final"]["state_sanity"]["output_hash"]
        != second["final"]["state_sanity"]["output_hash"]
    )


def test_tampered_recorded_output_is_detected(tmp_path: Path) -> None:
    """Mutation killer: recorded output must actually gate the replay.

    Under schema 1 this manipulation was invisible, because ``output_hash`` was
    the same constant regardless of what the program printed.
    """

    artifact = tmp_path / "artifact"
    record_source(PRINT_FIVE, artifact)
    manifest_path = artifact / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    other = record_source(PRINT_TEXT, tmp_path / "other")
    manifest["final"]["state_sanity"]["output_hash"] = other["final"]["state_sanity"][
        "output_hash"
    ]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    with pytest.raises(DeterministicReplayError, match="drift"):
        replay_mock_artifact(artifact)


# ---------------------------------------------------------------------------
# Schema-1 artifacts remain replayable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "artifact",
    sorted(p for p in LEGACY_STRICT_DIR.iterdir() if p.is_dir()),
    ids=lambda p: p.name,
)
def test_legacy_schema_1_artifacts_still_replay(artifact: Path) -> None:
    manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert replay_mock_artifact(artifact).drift == 0


def test_schema_1_comparison_drops_unobservable_fields() -> None:
    sanity = state_sanity_from_snapshot(_run_interpreter_from_source(PRINT_FIVE).snapshot())
    projected = comparable_state_sanity(sanity, schema_version=1)
    for field in SCHEMA_2_FIELDS:
        assert field not in projected
    assert "history_hash" in projected
    assert "locals_keys" in projected


def test_schema_2_comparison_keeps_every_field() -> None:
    sanity = state_sanity_from_snapshot(_run_interpreter_from_source(PRINT_FIVE).snapshot())
    projected = comparable_state_sanity(sanity, schema_version=SCHEMA_VERSION)
    assert projected == sanity
    for field in SCHEMA_2_FIELDS:
        assert field in projected


def test_unknown_future_schema_version_fails_closed(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    record_source(PRINT_FIVE, artifact)
    manifest_path = artifact / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = SCHEMA_VERSION + 1
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    with pytest.raises(ReplayArtifactError, match="schema_version"):
        replay_mock_artifact(artifact)


@pytest.mark.parametrize("bad", [True, "2", 2.0, None, 0, -1])
def test_malformed_schema_version_fails_closed(tmp_path: Path, bad: object) -> None:
    artifact = tmp_path / "artifact"
    record_source(PRINT_FIVE, artifact)
    manifest_path = artifact / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = bad
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    with pytest.raises(ReplayArtifactError):
        replay_mock_artifact(artifact)
