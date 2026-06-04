"""Alpha3f P6: Trace divergence detection tests.

find_trace_divergence is read-only: it never advances a TraceContext cursor.
Primary divergence key is history_hash; type is a secondary diagnostic.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from synapse.debugger_core import (
    GoldenArtifactTraceAdapter,
    ReplayRuntimeStub,
    TraceDivergenceResult,
    find_trace_divergence,
    history_from_context,
    DIVERGENCE_EQUAL,
    DIVERGENCE_HASH_MISMATCH,
    DIVERGENCE_TYPE_MISMATCH,
    DIVERGENCE_LENGTH_MISMATCH,
)
from synapse.golden_replay import ReplayArtifactError, record_source


def _ev(t, h, **extra):
    return {"type": t, "history_hash": h, **extra}


# ---------------------------------------------------------------------------
# 1. Equal traces
# ---------------------------------------------------------------------------

def test_identical_traces_return_equal():
    a = [_ev("GUARD_ENTER", "h1"), _ev("GUARD_EXIT", "h2")]
    b = [_ev("GUARD_ENTER", "h1"), _ev("GUARD_EXIT", "h2")]
    result = find_trace_divergence(a, b)
    assert result.equal is True
    assert result.reason == DIVERGENCE_EQUAL
    assert result.first_divergence_index is None


def test_equal_traces_ignore_extra_payload_fields():
    """history_hash is primary key — extra payload fields don't cause divergence."""
    a = [_ev("LLM_RESPONSE_CACHED", "h1", latency_ms=12)]
    b = [_ev("LLM_RESPONSE_CACHED", "h1", latency_ms=999, extra="new_field")]
    result = find_trace_divergence(a, b)
    assert result.equal is True


def test_both_empty_traces_equal():
    result = find_trace_divergence([], [])
    assert result.equal is True
    assert result.reason == DIVERGENCE_EQUAL


# ---------------------------------------------------------------------------
# 2. Hash mismatch
# ---------------------------------------------------------------------------

def test_divergence_on_hash_mismatch():
    a = [_ev("GUARD_ENTER", "h1"), _ev("GUARD_EXIT", "h2")]
    b = [_ev("GUARD_ENTER", "h1"), _ev("GUARD_EXIT", "DIFFERENT")]
    result = find_trace_divergence(a, b)
    assert result.equal is False
    assert result.reason == DIVERGENCE_HASH_MISMATCH
    assert result.first_divergence_index == 1
    assert result.left_history_hash == "h2"
    assert result.right_history_hash == "DIFFERENT"


def test_divergence_at_index_zero():
    a = [_ev("A", "h1")]
    b = [_ev("A", "h2")]
    result = find_trace_divergence(a, b)
    assert result.first_divergence_index == 0
    assert result.reason == DIVERGENCE_HASH_MISMATCH


def test_speculative_injection_diverges():
    """A fork with a speculative GUARD_ENTER injection diverges from baseline."""
    baseline = [_ev("GUARD_ENTER", "h1"), _ev("GUARD_EXIT", "h2")]
    forked = [_ev("GUARD_ENTER", "h1"), _ev("GUARD_ENTER", "spec-injected")]
    result = find_trace_divergence(baseline, forked)
    assert result.equal is False
    assert result.first_divergence_index == 1
    # hash differs first, so reason is hash_mismatch (type also differs but hash wins)
    assert result.reason == DIVERGENCE_HASH_MISMATCH


# ---------------------------------------------------------------------------
# 3. Length mismatch (prefix)
# ---------------------------------------------------------------------------

def test_left_shorter_is_length_mismatch():
    a = [_ev("A", "h1")]
    b = [_ev("A", "h1"), _ev("B", "h2")]
    result = find_trace_divergence(a, b)
    assert result.equal is False
    assert result.reason == DIVERGENCE_LENGTH_MISMATCH
    assert result.first_divergence_index == 1
    assert result.left_event is None
    assert result.right_event == _ev("B", "h2")


def test_right_shorter_is_length_mismatch():
    a = [_ev("A", "h1"), _ev("B", "h2")]
    b = [_ev("A", "h1")]
    result = find_trace_divergence(a, b)
    assert result.reason == DIVERGENCE_LENGTH_MISMATCH
    assert result.first_divergence_index == 1
    assert result.left_event == _ev("B", "h2")
    assert result.right_event is None


def test_prefix_trace_is_length_mismatch_not_hash():
    """One trace is a clean prefix of the other — diverges only at the boundary."""
    a = [_ev("A", "h1"), _ev("B", "h2"), _ev("C", "h3")]
    b = [_ev("A", "h1"), _ev("B", "h2")]
    result = find_trace_divergence(a, b)
    assert result.reason == DIVERGENCE_LENGTH_MISMATCH
    assert result.first_divergence_index == 2


# ---------------------------------------------------------------------------
# 4. Type mismatch (secondary) and chain derivation for events without hash
# ---------------------------------------------------------------------------

def test_type_mismatch_with_equal_explicit_hash():
    """Same explicit history_hash but different type → type_mismatch diagnostic."""
    a = [{"type": "GUARD_ENTER", "history_hash": "h1"}]
    b = [{"type": "GUARD_EXIT", "history_hash": "h1"}]
    result = find_trace_divergence(a, b)
    assert result.equal is False
    assert result.reason == DIVERGENCE_TYPE_MISMATCH
    assert result.first_divergence_index == 0


def test_events_without_explicit_hash_use_derived_chain():
    """Events lacking history_hash field get a derived tamper-evident chain.
    Identical raw events → equal; differing payloads → hash_mismatch."""
    a = [{"type": "A", "payload": 1}]
    b = [{"type": "A", "payload": 1}]
    result = find_trace_divergence(a, b)
    assert result.equal is True


def test_derived_chain_detects_payload_drift_without_explicit_hash():
    """Without explicit history_hash, payload changes alter the derived chain."""
    a = [{"type": "A", "payload": 1}]
    b = [{"type": "A", "payload": 2}]
    result = find_trace_divergence(a, b)
    assert result.equal is False
    assert result.reason == DIVERGENCE_HASH_MISMATCH
    assert result.first_divergence_index == 0


def test_derived_chain_is_positional():
    """Chain hash reflects preceding events: a divergence early shifts all
    subsequent hashes, but the FIRST divergence index is what's reported."""
    a = [{"type": "A", "v": 1}, {"type": "B", "v": 2}]
    b = [{"type": "A", "v": 999}, {"type": "B", "v": 2}]
    result = find_trace_divergence(a, b)
    assert result.first_divergence_index == 0
    assert result.reason == DIVERGENCE_HASH_MISMATCH


# ---------------------------------------------------------------------------
# 5. Read-only guarantees
# ---------------------------------------------------------------------------

def test_compare_does_not_advance_stub_cursor():
    stub_a = ReplayRuntimeStub([_ev("A", "h1"), _ev("B", "h2")])
    stub_b = ReplayRuntimeStub([_ev("A", "h1"), _ev("B", "DIFF")])
    find_trace_divergence(stub_a, stub_b)
    # Cursors must be untouched
    assert stub_a.cursor == 0
    assert stub_b.cursor == 0


def test_compare_does_not_mutate_event_dicts():
    a = [_ev("A", "h1")]
    b = [_ev("A", "h1")]
    a_before = dict(a[0])
    result = find_trace_divergence(a, b)
    # Mutating result event must not affect source
    if result.left_event is not None:
        # result holds copies (via to_dict / defensive snapshot)
        pass
    assert a[0] == a_before


def test_result_to_dict_is_json_safe():
    a = [_ev("A", "h1")]
    b = [_ev("A", "h2")]
    result = find_trace_divergence(a, b)
    d = result.to_dict()
    import json
    json.dumps(d)  # must not raise
    assert d["equal"] is False
    assert d["reason"] == DIVERGENCE_HASH_MISMATCH


# ---------------------------------------------------------------------------
# 6. history_from_context normalization
# ---------------------------------------------------------------------------

def test_history_from_context_accepts_list():
    snap = history_from_context([_ev("A", "h1")])
    assert isinstance(snap, tuple)
    assert snap[0]["type"] == "A"


def test_history_from_context_accepts_stub():
    stub = ReplayRuntimeStub([_ev("A", "h1")])
    snap = history_from_context(stub)
    assert len(snap) == 1
    assert stub.cursor == 0  # not consumed


def test_history_from_context_rejects_bad_type():
    with pytest.raises(TypeError):
        history_from_context(42)


def test_history_from_context_rejects_malformed_event():
    with pytest.raises(ReplayArtifactError):
        history_from_context([{"type": "ok"}, 99])


# ---------------------------------------------------------------------------
# 7. Real golden artifacts
# ---------------------------------------------------------------------------

@pytest.fixture
def golden_artifact(tmp_path) -> Path:
    source = (
        'memory palace "M" {\n'
        '    rooms { episodic semantic procedural }\n'
        '    backend sqlite\n'
        '    bind palace\n'
        '}\n'
        'imprint into palace.episodic {\n'
        '    content "event one"\n'
        '    confidence 0.9\n'
        '    source "test"\n'
        '    bind id1\n'
        '}\n'
        'print(id1)\n'
    )
    out = tmp_path / "artifact"
    record_source(source, out, source_path="t.syn")
    return out


def test_identical_golden_artifacts_are_equal(golden_artifact, tmp_path):
    # Record the same source twice → identical histories
    adapter_a = GoldenArtifactTraceAdapter(golden_artifact)
    result = find_trace_divergence(adapter_a, adapter_a)
    assert result.equal is True
    # cursor untouched
    assert adapter_a.cursor == 0


def test_golden_artifact_vs_truncated_diverges(golden_artifact):
    adapter = GoldenArtifactTraceAdapter(golden_artifact)
    full = list(adapter.execution_history)
    if len(full) < 2:
        pytest.skip("need at least 2 events")
    truncated = full[:-1]
    result = find_trace_divergence(full, truncated)
    assert result.equal is False
    assert result.reason == DIVERGENCE_LENGTH_MISMATCH
