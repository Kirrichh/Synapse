"""Application/CLI acceptance: an instruction ceiling is not a successful run."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from synapse import Interpreter, compile_to_ast, run
from synapse.bytecode import CognitiveCompiler
from synapse.cvm import CognitiveVM, VMState, VMStepLimitExceeded


def test_component_step_quanta_remain_resumable_with_identical_final_state():
    program = CognitiveCompiler().compile(compile_to_ast(
        "let n = 0 while n < 50 { n = n + 1 }"))
    whole = CognitiveVM(program, VMState(gas_remaining=10_000))
    chunks = CognitiveVM(program, VMState(gas_remaining=10_000))
    whole.run()
    assert chunks.run(max_steps=7)["halted"] is False
    while not chunks.halted:
        chunks.run(max_steps=7)
    assert chunks.state.to_dict() == whole.state.to_dict()


def test_application_ceiling_unwinds_context_as_failure():
    interp = Interpreter()
    vm = CognitiveVM(CognitiveCompiler().compile(compile_to_ast(
        'context "outer" { let i = 0 while i < 50 { i = i + 1 } }')),
        VMState(gas_remaining=10_000))
    bridge = interp.runtime.vm
    vm.host = bridge.get_cvm_callback_adapter(vm)
    with pytest.raises(VMStepLimitExceeded):
        bridge.run_cvm_with_context_safety(vm, max_steps=20)
    closed = [e for e in interp.execution_history if e.get("type") == "context_exited"]
    assert len(closed) == 1
    assert closed[0]["unwind_reason"] == "exception:VMStepLimitExceeded"
    assert vm.state.context_stack == []
    assert interp.context_tracker.stack == []


@pytest.mark.parametrize("gas,error", [(100_000, "STEP_LIMIT_REACHED"), (10, "OUT_OF_ENERGY")])
def test_run_vm_binds_explicit_failure_and_records_same_result(gas, error):
    source = "let acc = 12345 for i in range(500) { acc = (acc * 48271) % 2147483647 }"
    wrapper = f"compile vm {{ source {json.dumps(source)} bind code }} run vm {{ source code gas {gas} bind result }}"
    interp = Interpreter()
    run(wrapper, interp)
    result = interp.global_env.get("result")
    assert result["halted"] is False and result["error"] == error
    assert interp.execution_history[-1]["result"] == result
    if error == "STEP_LIMIT_REACHED":
        assert result["steps"] == 10_000
        assert result["snapshot"]["state"]["gas_remaining"] > 0


def test_canonical_cli_exposes_instruction_limit_failure():
    root = Path(__file__).resolve().parents[2]
    source = "let i = 0 while i < 2000 { i = i + 1 }"
    command = (f"compile vm {{ source {json.dumps(source)} bind code }} "
               'run vm { source code gas 100000 bind result } print(result["error"])')
    env = {**os.environ, "PYTHONPATH": str(root)}
    observed = subprocess.run([sys.executable, "-B", "-m", "synapse", "run", "-c", command],
                              cwd=root, env=env, text=True, capture_output=True, timeout=30)
    assert observed.returncode == 0, observed.stderr
    assert observed.stdout.strip() == "STEP_LIMIT_REACHED"
