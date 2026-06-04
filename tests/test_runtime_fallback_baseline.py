"""Alpha.3-C2 runtime fallback audit baseline."""
import hashlib
import json
from pathlib import Path

from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.interpreter import Interpreter


def parse_source(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


def test_living_habits_phase_c_alpha3c2_runtime_fallback_baseline_matches_spec():
    """Runtime statement-level fallback telemetry must match alpha3c2 baseline."""
    program_path = Path("examples/living_habits_phase_c.syn")
    source = program_path.read_text()
    source_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()

    baseline_path = Path("tests/baselines/living_habits_phase_c_alpha3c2.json")
    baseline = json.loads(baseline_path.read_text())

    if baseline["source_sha256"] != source_sha256:
        raise AssertionError(
            "BASELINE_SOURCE_MISMATCH: "
            f"expected {baseline['source_sha256']}, got {source_sha256}"
        )

    interp = Interpreter()
    interp.source_code = source
    interp.interpret(parse_source(source))
    metrics = interp.metrics_snapshot()
    expected = baseline["expected"]

    assert metrics["vm_fallbacks_total"] == expected["vm_fallbacks_total"]
    assert metrics["vm_fallback_by_node_type"] == expected["vm_fallback_by_node_type"]
    assert metrics["vm_fallback_by_reason"] == expected["vm_fallback_by_reason"]
    assert metrics["vm_coverage_ratio"] == expected["vm_coverage_ratio"]
    assert metrics["context_entries_total"] == expected["context_entries_total"]
    assert metrics["context_exits_total"] == expected["context_exits_total"]

    fallback_events = [e for e in interp.execution_history if e.get("type") == "vm_fallback"]
    assert {e.get("compiler_phase") for e in fallback_events} <= {"compile_stmt", "runtime_routing"}
    assert all(e.get("ip_at_fallback") is None for e in fallback_events)
    assert all(e.get("source_sha256") == source_sha256 for e in fallback_events)
