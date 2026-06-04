"""Alpha3g P0.4.7: read-only drift baseline for Integrate golden fixtures.

This suite uses the existing P0.3.5 integrate golden-recording helper to create
artifact snapshots under pytest's tmp_path, then reads the generated history
payloads without mutating them. The project currently stores Integrate golden
coverage as dynamic conformance scenarios rather than committed JSON fixture
folders, so this test mirrors that corpus and analyzes the resulting real
``integrate_committed`` / ``integrate_aborted`` event payloads.

No runtime consumer is switched to stable-canonical.v1 here. The test only calls
``compare_profile_hashes`` and verifies that current Integrate Category B
payloads are safe for the next flagged-migration step.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from synapse.canonical_service import DriftCategory, compare_profile_hashes
from synapse.golden_replay import record_integrate_artifact


INTEGRATE_FIXTURE_SCENARIOS: dict[str, tuple[str, bool]] = {
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


EXPECTED_SAFE_CATEGORIES = {DriftCategory.NONE.value}


def _record_fixture_corpus(tmp_path: Path) -> list[tuple[str, list[dict[str, Any]]]]:
    corpus: list[tuple[str, list[dict[str, Any]]]] = []
    for name, (source, expect_abort) in sorted(INTEGRATE_FIXTURE_SCENARIOS.items()):
        artifact_dir = tmp_path / name
        record_integrate_artifact(
            source,
            artifact_dir,
            source_path=f"{name}.syn",
            expect_abort=expect_abort,
        )
        # Read-only analysis: after artifact creation, only read history.json.
        history = json.loads((artifact_dir / "history.json").read_text(encoding="utf-8"))
        corpus.append((name, history))
    return corpus


def _iter_representative_payloads(fixture_name: str, history: list[dict[str, Any]]):
    for event_index, event in enumerate(history):
        yield f"{fixture_name}:event[{event_index}]", event
        if event.get("type") == "integrate_committed":
            for entry_index, entry in enumerate(event.get("write_set", []) or []):
                yield f"{fixture_name}:write_set[{entry_index}]", entry
                if "new_value" in entry:
                    yield f"{fixture_name}:new_value[{entry_index}]", entry["new_value"]
        if event.get("type") == "integrate_aborted":
            yield f"{fixture_name}:overlay_summary", event.get("overlay_summary", {})


def _drift_observations(tmp_path: Path):
    observations = []
    for fixture_name, history in _record_fixture_corpus(tmp_path).copy():
        for label, payload in _iter_representative_payloads(fixture_name, history):
            comparison = compare_profile_hashes(payload)
            observations.append((label, comparison))
    return observations


def test_integrate_golden_fixture_payloads_have_no_breaking_stable_drift(tmp_path):
    """Current I6 Integrate payloads are stable-profile compatible.

    This is the SI4-prep go/no-go guard: no current Integrate golden payload may
    produce a breaking/rejected drift category before StateOverlay gets a
    profile selector in a later patch.
    """
    observations = _drift_observations(tmp_path)
    assert observations, "expected at least one Integrate fixture payload to analyze"

    categories = Counter(comparison.drift_category for _, comparison in observations)
    assert categories == Counter({DriftCategory.NONE.value: len(observations)})

    for label, comparison in observations:
        assert comparison.drift_category in EXPECTED_SAFE_CATEGORIES, (
            f"unexpected drift in {label}: {comparison.drift_category}; "
            f"local_error={comparison.local_error!r}; stable_error={comparison.stable_error!r}"
        )
        assert comparison.local_hash == comparison.stable_hash, f"hash drift in {label}"


def test_integrate_drift_report_corpus_covers_commit_abort_and_write_set_shapes(tmp_path):
    corpus = _record_fixture_corpus(tmp_path)
    event_types = Counter(event.get("type") for _, history in corpus for event in history)
    assert event_types["integrate_committed"] >= 4
    assert event_types["integrate_aborted"] >= 1

    write_set_paths = [
        entry.get("path")
        for _, history in corpus
        for event in history
        for entry in (event.get("write_set", []) or [])
    ]
    assert "/env/x" in write_set_paths
    assert "/env/a" in write_set_paths
    assert "/env/read_only" in write_set_paths


def test_drift_report_document_records_go_verdict():
    report = Path("docs/MIGRATION-DRIFT-REPORT.md").read_text(encoding="utf-8")
    assert "Verdict: GO" in report
    assert "breaking drift found: 0" in report
    assert "integrate_committed_basic" in report
    assert "integrate_aborted_barrier_violation" in report
