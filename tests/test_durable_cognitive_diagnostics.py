"""A refused durable run of the cognitive profile names the refusal at its innermost statement.

The cognitive profile is the durable path of a run with a memory owner. It takes a named run lock
(``filelock``, a runtime dependency of the package), so it runs with the package's dependencies installed,
outside the dependency-free P2a suite of ``test_durable_execution.py``
(``RFC-ASYNC-EXECUTION-AMENDMENT-04``).
"""
from __future__ import annotations

import json
from pathlib import Path

import synapse.application as app


def test_a_cognitive_profile_refusal_names_the_innermost_statement_line(tmp_path: Path):
    program = tmp_path / "cognitive.syn"
    program.write_text(
        'fn plan(x) {\n    return x\n}\ndream {\n    let a = "LITERAL-MARKER-2c90"\n    tool("ship", {})\n}\n',
        encoding="utf-8",
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # A memory owner selects the cognitive profile; the refusal comes before the run ever uses it.
    profile_selector = object()

    result = app.execute_durable_run(app.DurableRunRequest(
        source_path=program, state_dir=state_dir, run_id="run-cognitive", memory=profile_selector))

    assert result.exit_code == 25
    assert result.diagnostics == (
        f"synapse: {program}:6: UNSUPPORTED_DURABLE_OPERATION_OR_REASON: durable cognitive profile: "
        "tool is an external effect and cannot run inside dream or integrate",
    )
    assert "LITERAL-MARKER-2c90" not in json.dumps(result.public_payload) + "".join(result.diagnostics)
    assert not (state_dir / "run-cognitive.json").exists()
