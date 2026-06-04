from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest

from synapse import cli
from synapse.golden_replay import record_source


def run_debug(argv):
    out = io.StringIO()
    err = io.StringIO()
    code = cli.run_debug_cli(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def _source(content: str) -> str:
    return (
        'memory palace "M" {\n'
        '    rooms { episodic semantic procedural }\n'
        '    backend sqlite\n'
        '    bind palace\n'
        '}\n'
        'imprint into palace.episodic {\n'
        f'    content "{content}"\n'
        '    confidence 0.9\n'
        '    source "test"\n'
        '    bind id1\n'
        '}\n'
        'print(id1)\n'
    )


@pytest.fixture
def artifact_a(tmp_path: Path) -> Path:
    out = tmp_path / "artifact_a"
    record_source(_source("event one"), out, source_path="a.syn")
    return out


@pytest.fixture
def artifact_b_same(tmp_path: Path) -> Path:
    out = tmp_path / "artifact_b_same"
    record_source(_source("event one"), out, source_path="b.syn")
    return out


@pytest.fixture
def artifact_b_different(tmp_path: Path) -> Path:
    out = tmp_path / "artifact_b_different"
    record_source(_source("event two"), out, source_path="b.syn")
    return out


def test_compare_equal_traces_exit_0(artifact_a):
    # Compare the same immutable artifact against itself. Independently recorded
    # artifacts may differ in source_path metadata even for identical source.
    code, out, err = run_debug(["compare", str(artifact_a), str(artifact_a)])

    assert code == 0, err
    assert err == ""
    payload = json.loads(out)
    assert payload["equal"] is True
    assert payload["reason"] == "equal"
    assert payload["first_divergence_index"] is None


def test_compare_divergent_traces_exit_7(artifact_a, artifact_b_different):
    code, out, err = run_debug(["compare", str(artifact_a), str(artifact_b_different)])

    assert code == 7, err
    assert err == ""
    payload = json.loads(out)
    assert payload["equal"] is False
    assert payload["reason"] in {"hash_mismatch", "length_mismatch", "type_mismatch"}
    assert payload["first_divergence_index"] is not None
    assert payload["left_history_hash"] != payload["right_history_hash"]


def test_compare_missing_path_exit_1(artifact_a, tmp_path):
    missing = tmp_path / "missing-artifact"
    code, out, err = run_debug(["compare", str(artifact_a), str(missing)])

    assert code == 1
    assert out == ""
    assert "invalid argument" in err
    assert "right artifact directory not found" in err


def test_compare_broken_artifact_exit_8(artifact_a, tmp_path):
    broken = tmp_path / "broken"
    shutil.copytree(artifact_a, broken)
    # Break schema without making it a missing-path CLI error.
    (broken / "history.json").write_text('{"not":"a-list"}', encoding="utf-8")

    code, out, err = run_debug(["compare", str(artifact_a), str(broken)])

    assert code == 8
    assert out == ""
    assert "replay artifact integrity error" in err


def test_compare_outputs_structured_json(artifact_a, artifact_b_different):
    code, out, err = run_debug(["compare", str(artifact_a), str(artifact_b_different)])

    assert code == 7, err
    payload = json.loads(out)
    assert set(payload) == {
        "equal",
        "reason",
        "first_divergence_index",
        "left_event",
        "right_event",
        "left_history_hash",
        "right_history_hash",
    }


def test_cli_compare_delegates_hash_logic_to_core(monkeypatch, artifact_a, artifact_b_same):
    calls = {"adapter": [], "divergence": 0}

    class FakeAdapter:
        def __init__(self, path):
            calls["adapter"].append(str(path))
            self.path = str(path)

    class FakeResult:
        equal = False

        def to_dict(self):
            return {
                "equal": False,
                "reason": "hash_mismatch",
                "first_divergence_index": 3,
                "left_event": {"type": "A"},
                "right_event": {"type": "B"},
                "left_history_hash": "left",
                "right_history_hash": "right",
            }

    def fake_find(left, right):
        calls["divergence"] += 1
        assert isinstance(left, FakeAdapter)
        assert isinstance(right, FakeAdapter)
        return FakeResult()

    monkeypatch.setattr(cli, "GoldenArtifactTraceAdapter", FakeAdapter)
    monkeypatch.setattr(cli, "find_trace_divergence", fake_find)

    code, out, err = run_debug(["compare", str(artifact_a), str(artifact_b_same)])

    assert code == 7, err
    assert calls["adapter"] == [str(artifact_a), str(artifact_b_same)]
    assert calls["divergence"] == 1
    assert json.loads(out)["reason"] == "hash_mismatch"
