from synapse import Lexer, Parser, Interpreter, VMResumeSyncError, VMTamperDetectedError, UnknownOpcodeError
from synapse.cvm import CognitiveVM
from synapse.bytecode import BytecodeProgram, Instruction


def compile_ast(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


def run(source: str, interp=None):
    interp = interp or Interpreter()
    interp.source_code = source
    interp.interpret(compile_ast(source))
    return interp


def test_checkpoint_at_ip_saved_with_canonical_snapshot():
    source = '''
compile vm { source "let x = 1" bind code }
run vm { source code gas 100 cognitive_budget 5 checkpoint "after_init" at_ip 1 bind partial }
'''
    interp = run(source)
    assert "after_init" in interp.vm_checkpoints
    snap = interp.vm_checkpoints["after_init"]
    assert snap["version"] == "2.1"
    assert snap["ip"] == 1
    assert snap["cognitive_budget_remaining"] == 5
    assert "last_processed_event_id" in snap
    assert "history_hash" in snap
    assert any(e.get("type") == "vm_checkpoint_saved" and e.get("label") == "after_init" for e in interp.execution_history)


def test_resume_from_checkpoint_continues_with_saved_ip():
    source = '''
compile vm { source "let x = 1" bind code }
run vm { source code gas 100 checkpoint "after_init" at_ip 1 bind partial }
run vm { resume_from "after_init" gas 100 cognitive_budget 3 bind final }
'''
    interp = run(source)
    assert any(e.get("type") == "vm_resumed" and e.get("from_checkpoint") == "after_init" for e in interp.execution_history)
    assert interp.global_env.get("final")["halted"] is True


def test_tamper_detection_on_resume():
    interp = run('''
compile vm { source "let x = 1" bind code }
run vm { source code gas 100 checkpoint "after_init" at_ip 1 bind partial }
''')
    # mutate an event that belongs to the checkpoint prefix
    interp.execution_history[0]["type"] = "tampered"
    try:
        interp.source_code = 'run vm { resume_from "after_init" gas 100 bind final }'
        interp.interpret(compile_ast(interp.source_code))
        assert False, "expected VMTamperDetectedError"
    except VMTamperDetectedError:
        pass


def test_resume_sync_error_when_log_behind_checkpoint():
    interp = run('''
compile vm { source "let x = 1" bind code }
run vm { source code gas 100 checkpoint "after_init" at_ip 1 bind partial }
''')
    interp.execution_history.clear()
    try:
        interp.source_code = 'run vm { resume_from "after_init" gas 100 bind final }'
        interp.interpret(compile_ast(interp.source_code))
        assert False, "expected VMResumeSyncError"
    except VMResumeSyncError:
        pass


def test_host_abi_fallback_no_exception():
    program = BytecodeProgram(instructions=[Instruction("RECALL", "episodic", "deployment"), Instruction("HALT")], constants=[], version="2.1")
    vm = CognitiveVM(program)
    result = vm.run()
    assert result["halted"] is True
    assert result["stack"][0]["status"] == "fallback"
    assert result["stack"][0]["host_call"] == "host.memory.recall"


def test_gas_refund_on_cache_hit():
    program = BytecodeProgram(instructions=[Instruction("RECALL", "episodic", "cached"), Instruction("HALT")], constants=[], version="2.1")
    def host(op, a, b):
        return {"status": "ok", "from_cache": True}
    vm = CognitiveVM(program, host=host)
    vm.state.gas_remaining = 10
    result = vm.run()
    assert result["gas_remaining"] > 5


def test_unknown_custom_opcode_rejected():
    program = BytecodeProgram(instructions=[Instruction("CUSTOM_MAGIC"), Instruction("HALT")], constants=[], version="2.1")
    vm = CognitiveVM(program)
    try:
        vm.run()
        assert False, "expected UnknownOpcodeError"
    except UnknownOpcodeError:
        pass
