"""Alpha3g I6: Integrate golden-fixture replay conformance tests.

Verifies that the Alpha3g i2-skeleton integrate replay path is end-to-end
stable: record via LIVE → verify artifact integrity → replay via REPLAY applier
→ assert zero drift.

Coverage (per INTEGRATE-IMPLEMENTATION-PLAN.md §8):
  integrate_committed_basic         — commit, write-set applied in LIVE, consumed in REPLAY
  integrate_committed_body_skipped  — proves REPLAY does not execute body (body has a print side-effect)
  integrate_noop_empty_write_set    — commit with no dirty env bindings
  integrate_aborted_barrier_violation — abort recorded; state unchanged in REPLAY
  integrate_replay_hash_mismatch    — tampered artifact raises DeterministicReplayError
  integrate_pre_vs_post_state_hash  — post_state_hash in artifact matches env after replay

Each fixture is recorded dynamically inside the test (no pre-committed artifact
files), which keeps the fixture in sync with the current interpreter and avoids
a separate maintenance burden for stored JSON blobs.

Existing strict golden fixtures (tests/golden_replays_alpha3e/strict/) are
not modified. This suite only adds new integrate-specific recording.
"""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

import pytest

from synapse.golden_replay import (
    DeterministicReplayError,
    record_integrate_artifact,
    replay_integrate_artifact,
)
from synapse.interpreter import (
    Interpreter,
    IntegrateIsolationViolation,
    ReplayIntegrityError,
    RuntimeMode,
)
from synapse.lexer import Lexer
from synapse.parser import Parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(src: str):
    return Parser(Lexer(src).scan_tokens()).parse()


def _run_live_i2(src: str) -> Interpreter:
    interp = Interpreter()
    interp.integrate_i2_skeleton_enabled = True
    interp.source_code = src
    interp.interpret(_parse(src))
    return interp


# ---------------------------------------------------------------------------
# 1. Basic committed replay
# ---------------------------------------------------------------------------

def test_integrate_committed_basic(tmp_path):
    """LIVE commits x=2; REPLAY applies recorded write-set without body."""
    src = """\
let x = 1
integrate x {
    x = 2
} on fail rollback
"""
    manifest = record_integrate_artifact(src, tmp_path / "art", source_path="basic.syn")
    assert manifest["final"]["history_length"] == 1
    assert manifest["final"]["final_history_hash"]

    result = replay_integrate_artifact(tmp_path / "art")
    assert result.drift == 0
    assert result.history_length == 1


# ---------------------------------------------------------------------------
# 2. REPLAY does not execute body (body-skip proof)
# ---------------------------------------------------------------------------

def test_integrate_committed_body_skipped_in_replay(tmp_path):
    """Body writes x=999 and prints — REPLAY must not execute it; result = 2."""
    src_live = """\
let x = 1
integrate x {
    x = 2
} on fail rollback
"""
    src_replay = """\
let x = 1
integrate x {
    x = 999
    print("body must not execute in replay")
} on fail rollback
"""
    # Record with the live source (x=2)
    record_integrate_artifact(src_live, tmp_path / "art", source_path="live.syn")

    # Now replay manually using the modified body source.
    # If the body executed, x would be 999. replay_integrate_i4_event must skip it.
    art_path = tmp_path / "art"
    manifest = json.loads((art_path / "manifest.json").read_text())
    expected_history = json.loads((art_path / "history.json").read_text())

    replay = Interpreter()
    replay.integrate_i2_skeleton_enabled = True
    replay.execution_history = copy.deepcopy(expected_history)
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.replay_cursor = 0
    replay.source_code = src_replay
    replay.interpret(_parse(src_replay))

    # x must be 2 (from recorded write-set), not 999 (from replay body)
    assert replay.global_env.get("x") == 2
    # Body print would have added to output_buffer if executed
    assert "body must not execute" not in "\n".join(replay.output_buffer)


# ---------------------------------------------------------------------------
# 3. No-op: empty write-set
# ---------------------------------------------------------------------------

def test_integrate_noop_empty_write_set(tmp_path):
    """Integrate body that only reads x (no write to outer env) — write-set for x stays empty.

    Note: variables defined inside the integrate body (like `local_only`) are
    tracked by the overlay. A true 'no mutation to pre-existing outer env vars'
    scenario is what we test: x is read but never reassigned.
    """
    src = """\
let x = 1
integrate x {
    let read_only = x
} on fail rollback
"""
    manifest = record_integrate_artifact(src, tmp_path / "art", source_path="noop.syn")
    history = json.loads((tmp_path / "art" / "history.json").read_text())
    committed = [e for e in history if e.get("type") == "integrate_committed"]
    assert len(committed) == 1
    write_set = committed[0].get("write_set", [])
    # x was only read, not written → must not appear as dirty in write_set
    x_entries = [e for e in write_set if e.get("path") == "/env/x"]
    assert len(x_entries) == 0, f"x should not be in write_set (read-only), got {x_entries}"

    result = replay_integrate_artifact(tmp_path / "art")
    assert result.drift == 0


# ---------------------------------------------------------------------------
# 4. Aborted: barrier violation
# ---------------------------------------------------------------------------

def test_integrate_aborted_barrier_violation(tmp_path):
    """Forbidden builtin inside integrate body → aborted event; state unchanged."""
    src = """\
let x = 1
integrate x {
    print("forbidden in i2 barrier")
} on fail rollback
"""
    manifest = record_integrate_artifact(
        src, tmp_path / "art", source_path="abort.syn", expect_abort=True
    )
    history = json.loads((tmp_path / "art" / "history.json").read_text())
    aborted = [e for e in history if e.get("type") == "integrate_aborted"]
    assert len(aborted) == 1, f"expected integrate_aborted, history types: {[e.get('type') for e in history]}"

    # Replay must consume the abort event, leave state unchanged, re-raise
    art_path = tmp_path / "art"
    expected_history = json.loads((art_path / "history.json").read_text())

    replay = Interpreter()
    replay.integrate_i2_skeleton_enabled = True
    replay.execution_history = copy.deepcopy(expected_history)
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.replay_cursor = 0
    replay.source_code = src
    with pytest.raises(Exception):
        replay.interpret(_parse(src))
    # x must remain 1 (abort leaves state unchanged)
    assert replay.global_env.get("x") == 1
    # All recorded events must have been consumed
    assert replay.replay_cursor == len(expected_history)


# ---------------------------------------------------------------------------
# 5. Tampered artifact → DeterministicReplayError
# ---------------------------------------------------------------------------

def test_integrate_replay_hash_mismatch_raises(tmp_path):
    """Tampering the history.json breaks chain → DeterministicReplayError."""
    src = """\
let x = 1
integrate x {
    x = 2
} on fail rollback
"""
    record_integrate_artifact(src, tmp_path / "art", source_path="tamper.syn")
    hist_path = tmp_path / "art" / "history.json"
    history = json.loads(hist_path.read_text())
    # Inject a bogus event that breaks the hash chain
    history.append({"type": "TAMPERED_EVENT", "injected": True})
    hist_path.write_text(json.dumps(history))

    with pytest.raises(DeterministicReplayError, match="chain broken"):
        replay_integrate_artifact(tmp_path / "art")


# ---------------------------------------------------------------------------
# 6. pre_state_hash / post_state_hash round-trip
# ---------------------------------------------------------------------------

def test_integrate_state_hash_round_trip(tmp_path):
    """Recorded post_state_hash must match env state after successful replay."""
    src = """\
let a = 10
let b = 20
integrate a {
    a = 99
} on fail rollback
"""
    record_integrate_artifact(src, tmp_path / "art", source_path="hash.syn")
    history = json.loads((tmp_path / "art" / "history.json").read_text())
    committed = [e for e in history if e.get("type") == "integrate_committed"]
    assert committed, "expected integrate_committed in history"
    recorded_post_hash = committed[0]["post_state_hash"]

    # Run a fresh LIVE to compute what the post-state hash should be
    live = _run_live_i2(src)
    from synapse.state_overlay import StateOverlay
    base = {"env": live.flatten_env_variables(live.global_env), "memory": {}}
    ov = StateOverlay(base)
    actual_post_hash = ov.canonical_hash()

    # They must match
    assert recorded_post_hash == actual_post_hash

    # And replay produces zero drift
    result = replay_integrate_artifact(tmp_path / "art")
    assert result.drift == 0


# ---------------------------------------------------------------------------
# 7. In-run idempotency guard preserved through golden path
# ---------------------------------------------------------------------------

def test_integrate_golden_idempotency_guard(tmp_path):
    """replay_integrate_i4_event raises if same event index applied twice."""
    src = """\
let x = 1
integrate x {
    x = 2
} on fail rollback
"""
    record_integrate_artifact(src, tmp_path / "art", source_path="idem.syn")
    art_path = tmp_path / "art"
    expected_history = json.loads((art_path / "history.json").read_text())

    replay = Interpreter()
    replay.integrate_i2_skeleton_enabled = True
    replay.execution_history = copy.deepcopy(expected_history)
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.replay_cursor = 0
    replay.source_code = src
    replay.interpret(_parse(src))

    # Attempt to replay the same integrate event again (reset cursor to replay same event)
    replay.replay_cursor = 0
    with pytest.raises(ReplayIntegrityError, match="already applied"):
        replay.interpret(_parse(src))


# ---------------------------------------------------------------------------
# 8. Existing strict Layer 1 golden suite not broken
# ---------------------------------------------------------------------------

def test_existing_strict_golden_suite_unaffected():
    """I6 must not affect the existing strict golden Layer 1 baseline."""
    from synapse.golden_replay import replay_mock_artifact
    import os
    strict_dir = Path("tests/golden_replays_alpha3e/strict")
    if not strict_dir.exists():
        pytest.skip("strict golden dir not found")
    for artifact in sorted(strict_dir.iterdir()):
        if artifact.is_dir() and (artifact / "manifest.json").exists():
            result = replay_mock_artifact(artifact)
            assert result.drift == 0, f"drift in existing fixture: {artifact.name}"
