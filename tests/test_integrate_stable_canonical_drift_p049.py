"""Alpha3g P0.4.9: read-only Integrate stable-canonical drift analysis.

This suite is the SI5-prep gate for future Integrate hash-path migration. It
uses the existing integrate golden recording helper to create temporary artifact
snapshots, then reads only ``history.json`` / ``manifest.json`` payloads and
compares ``alpha3g.local-json.v1`` against ``stable-canonical.v1``.

No consumer is migrated in this test. It does not edit artifacts, switch
StateOverlay/Integrate profiles, import ``interpreter.py`` directly, or mutate
existing golden fixtures. The output is a go/no-go guard for a later flagged
Integrate migration patch.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from synapse.canonical_service import DriftCategory, compare_profile_hashes
from synapse.golden_replay import record_integrate_artifact


INTEGRATE_HASH_PATH_SCENARIOS: dict[str, tuple[str, bool]] = {
    "integrate_committed_basic": (
        """\
let x = 1
integrate x {
    x = 2
} on fail rollback
""",
        False,
    ),
    "integrate_committed_body_skipped": (
        """\
let x = 1
integrate x {
    x = 2
} on fail rollback
""",
        False,
    ),
    "integrate_noop_read_only_outer_env": (
        """\
let x = 1
integrate x {
    let read_only = x
} on fail rollback
""",
        False,
    ),
    "integrate_aborted_barrier_violation": (
        """\
let x = 1
integrate x {
    print("forbidden in i2 barrier")
} on fail rollback
""",
        True,
    ),
    "integrate_state_hash_round_trip": (
        """\
let a = 10
let b = 20
integrate a {
    a = 99
} on fail rollback
""",
        False,
    ),
}

SAFE_INTEGRATE_DRIFT_CATEGORIES = {DriftCategory.NONE.value}


def _record_integrate_corpus(tmp_path: Path) -> list[tuple[str, list[dict[str, Any]], dict[str, Any]]]:
    corpus: list[tuple[str, list[dict[str, Any]], dict[str, Any]]] = []
    for name, (source, expect_abort) in sorted(INTEGRATE_HASH_PATH_SCENARIOS.items()):
        artifact_dir = tmp_path / name
        record_integrate_artifact(
            source,
            artifact_dir,
            source_path=f"{name}.syn",
            expect_abort=expect_abort,
        )
        history = json.loads((artifact_dir / "history.json").read_text(encoding="utf-8"))
        manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
        corpus.append((name, history, manifest))
    return corpus


def _iter_integrate_hash_path_fragments(
    fixture_name: str,
    history: list[dict[str, Any]],
    manifest: dict[str, Any],
):
    """Yield real Integrate event/hash-path payload fragments for drift analysis."""

    for event in history:
        if event.get("type") == "integrate_committed":
            yield (
                f"{fixture_name}:committed.event_hash_fields",
                {
                    "schema_version": event.get("schema_version"),
                    "pre_state_hash": event.get("pre_state_hash"),
                    "post_state_hash": event.get("post_state_hash"),
                    "write_set_hash": event.get("write_set_hash"),
                    "nondeterminism_barrier_violated": event.get("nondeterminism_barrier_violated"),
                },
            )
            write_set = event.get("write_set", []) or []
            yield f"{fixture_name}:committed.write_set", write_set
            for entry_index, entry in enumerate(write_set):
                yield f"{fixture_name}:committed.write_set[{entry_index}]", entry
                yield (
                    f"{fixture_name}:committed.write_set[{entry_index}].hash_fields",
                    {
                        "path": entry.get("path"),
                        "op": entry.get("op"),
                        "old_value_hash": entry.get("old_value_hash"),
                        "new_value_hash": entry.get("new_value_hash"),
                    },
                )
                if "new_value" in entry:
                    yield f"{fixture_name}:committed.write_set[{entry_index}].new_value", entry["new_value"]
        elif event.get("type") == "integrate_aborted":
            yield (
                f"{fixture_name}:aborted.event_hash_fields",
                {
                    "schema_version": event.get("schema_version"),
                    "pre_state_hash": event.get("pre_state_hash"),
                    "barrier_op": event.get("barrier_op"),
                },
            )
            yield f"{fixture_name}:aborted.overlay_summary", event.get("overlay_summary", {})
            yield (
                f"{fixture_name}:aborted.abort_reason",
                {
                    "abort_reason": event.get("abort_reason"),
                    "exception_type": event.get("exception_type"),
                    "message": event.get("message"),
                    "barrier_op": event.get("barrier_op"),
                },
            )

    yield f"{fixture_name}:manifest.final_state_sanity", manifest["final"]["state_sanity"]


def _drift_observations(tmp_path: Path):
    observations = []
    for fixture_name, history, manifest in _record_integrate_corpus(tmp_path):
        for label, payload in _iter_integrate_hash_path_fragments(fixture_name, history, manifest):
            comparison = compare_profile_hashes(payload)
            observations.append((label, comparison))
    return observations


def test_integrate_hash_path_payloads_have_no_breaking_stable_drift(tmp_path):
    observations = _drift_observations(tmp_path)
    assert len(observations) == 28

    categories = Counter(comparison.drift_category for _, comparison in observations)
    assert categories == Counter({DriftCategory.NONE.value: 28})

    for label, comparison in observations:
        assert comparison.drift_category in SAFE_INTEGRATE_DRIFT_CATEGORIES, (
            f"unexpected Integrate migration drift in {label}: {comparison.drift_category}; "
            f"local_error={comparison.local_error!r}; stable_error={comparison.stable_error!r}"
        )
        assert comparison.local_hash == comparison.stable_hash, f"hash drift in {label}"


def test_integrate_hash_path_drift_corpus_covers_commit_abort_and_sanity_shapes(tmp_path):
    observations = _drift_observations(tmp_path)
    labels = {label for label, _ in observations}

    assert "integrate_committed_basic:committed.event_hash_fields" in labels
    assert "integrate_committed_basic:committed.write_set" in labels
    assert "integrate_committed_basic:committed.write_set[0].new_value" in labels
    assert "integrate_aborted_barrier_violation:aborted.overlay_summary" in labels
    assert "integrate_aborted_barrier_violation:aborted.abort_reason" in labels
    assert "integrate_state_hash_round_trip:manifest.final_state_sanity" in labels


def test_integrate_migration_drift_report_records_go_verdict():
    report = Path("docs/INTEGRATE-MIGRATION-DRIFT-REPORT.md").read_text(encoding="utf-8")
    assert "Verdict: GO" in report
    assert "observed payload fragments: 28" in report
    assert "breaking drift found: 0" in report
    assert "integrate_committed_basic" in report
    assert "integrate_aborted_barrier_violation" in report
