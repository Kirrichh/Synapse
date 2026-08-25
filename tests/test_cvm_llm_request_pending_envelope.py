"""Protected-core regression tests for the first-class LLM_REQUEST pause path."""
from __future__ import annotations

import ast
import json
from pathlib import Path

from synapse.bytecode import BytecodeProgram, Instruction
from synapse.cvm import CognitiveVM, VMState, VMStatus, compute_call_id


_SCHEMA_HASH = "sha256:schema-v1"
_ENGINE_PARAMS = {
    "model": "test-model",
    "model_version": "test-model-v1",
    "temperature": 0.0,
}
_CACHE_POLICY = "model_change"
_PROMPT_ENVELOPE = {
    "type": "prompt_envelope",
    "template_hash": "sha256:template-v1",
    "variables": {"subject": "replay"},
    "variables_hash": "sha256:variables-v1",
}


def _pause_llm_request() -> tuple[CognitiveVM, dict]:
    program = BytecodeProgram(
        instructions=[
            Instruction("HALT", None, None, None),
            Instruction("HALT", None, None, None),
            Instruction(
                "LLM_REQUEST",
                _SCHEMA_HASH,
                dict(_ENGINE_PARAMS),
                _CACHE_POLICY,
            ),
        ],
        constants=[],
    )
    state = VMState(ip=2, stack=[dict(_PROMPT_ENVELOPE)])
    vm = CognitiveVM(program, state=state)

    assert vm.step() is True
    assert vm.status() == VMStatus.PAUSED_HOST_CALL
    pending = vm.state.pending_host_call
    assert isinstance(pending, dict)
    return vm, pending


def test_mutant_llm_request_uses_stack_arity_instead_of_host_arity_is_killed() -> None:
    """The first-class opcode serializes four logical host-call arguments."""
    _, pending = _pause_llm_request()

    assert pending["argc"] == len(pending["args"]) == 4
    assert pending["args"] == [
        _PROMPT_ENVELOPE,
        _SCHEMA_HASH,
        _ENGINE_PARAMS,
        _CACHE_POLICY,
    ]


def test_mutant_llm_request_uses_ip_after_call_for_identity_is_killed() -> None:
    """Host-call identity binds the executed instruction, not pre-incremented IP."""
    vm, pending = _pause_llm_request()

    expected = compute_call_id(
        vm.program.program_hash,
        2,
        pending["transition_hash_at_call"],
        pending["created_at_event_id"],
        pending["frame_depth_at_call"],
    )
    wrong_ip_after_call = compute_call_id(
        vm.program.program_hash,
        pending["ip_after_call"],
        pending["transition_hash_at_call"],
        pending["created_at_event_id"],
        pending["frame_depth_at_call"],
    )

    assert pending["ip_after_call"] == 3
    assert pending["call_id"] == pending["deterministic_call_id"] == expected
    assert pending["call_id"] != wrong_ip_after_call


def test_llm_request_pending_envelope_snapshot_roundtrip_and_resume_is_compatible() -> None:
    """Envelope schema V1 still restores and resumes without re-executing the opcode."""
    vm, pending = _pause_llm_request()
    restored = CognitiveVM.restore(json.loads(json.dumps(vm.snapshot())))

    assert restored.status() == VMStatus.PAUSED_HOST_CALL
    assert restored.state.pending_host_call == pending
    ip_after_call = restored.state.ip
    transition_at_pause = restored.state.transition_hash
    result = {"text": "recorded result", "model_version": "test-model-v1"}

    restored.resume_host_call(pending["call_id"], result)

    assert restored.status() == VMStatus.RUNNING
    assert restored.state.pending_host_call is None
    assert restored.state.ip == ip_after_call == 3
    assert restored.state.stack == [result]
    assert restored.state.transition_hash != transition_at_pause


def test_protected_core_does_not_import_stage4_gold() -> None:
    """The prerequisite remains a core lifecycle repair, not Stage 4 coupling."""
    cvm_path = Path(__file__).resolve().parents[1] / "synapse" / "cvm.py"
    tree = ast.parse(cvm_path.read_text(encoding="utf-8"), filename=str(cvm_path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not {
        name
        for name in imported
        if name in {"synapse.experiments.gold", "experiments.gold"}
        or name.startswith("synapse.experiments.gold.")
        or name.startswith("experiments.gold.")
    }
