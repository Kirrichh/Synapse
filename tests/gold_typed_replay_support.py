"""Acceptance-only numeric procedure and its prospective CVM transcript."""

from synapse.experiments.gold import behavior as B
from synapse.experiments.gold import replay as R
from synapse.experiments.gold.replay_vm_adapter import CognitiveVMReplayAdapter
from tests.stage4_gold_replay_support import MACHINE_CONTEXT, GAS, _core


def field(name, value_type=B.ValueType.INTEGER, *, default=None):
    return B.ContractField(name, value_type,
        B.AbsencePolicy.REQUIRED if default is None else B.AbsencePolicy.DEFAULTED,
        B.DefaultValue(B.DefaultKind.ABSENT) if default is None else B.DefaultValue(B.DefaultKind.VALUE, default),
        B.AbsenceDetail(B.AbsenceDetailKind.NONE))


def numeric_procedure(*, left=11, right=4, output_type=B.ValueType.INTEGER, required=False,
                      profile=B.TYPED_PURE_REPLAY_PROFILE_V1):
    """Compute the absolute difference by branching; never return a content key."""
    original = _core()
    variable = lambda name: {"node": "variable", "name": name}
    binary = lambda operator, a, b: {"node": "binary", "operator": operator,
        "left": variable(a), "right": variable(b)}
    program = B.InlineProgram.from_dict({"form": "INLINE_IR_V1", "ir": {
        "schema_version": "synapse.stage4.gold.canonical-program-ir/v1",
        "program": {"node": "program", "statements": [{"node": "if",
            "condition": binary("GTE", "left", "right"),
            "then_body": [{"node": "return", "value": binary("SUB", "left", "right")}],
            "else_body": [{"node": "return", "value": binary("SUB", "right", "left")}]}]}}})
    inputs = B.InputContract((field("left", default=None if required else left),
                              field("right", default=None if required else right)), ())
    def build(transitions):
        return B.create_behavior_unit(behavior_kind=B.BehaviorKind.PROCEDURE,
            canonical_program=program, input_contract=inputs,
            output_contract=B.OutputContract((field("result", output_type),), ()),
            replay_contract=B.ReplayContract(profile,
                tuple(transitions), (), (), (B.ReplayResultClass.MATCH,)),
            capability_requirements=(), verification_contract=original.verification_contract,
            binding_refs=original.binding_refs, source_evidence_refs=original.source_evidence_refs,
            artifact_refs=original.artifact_refs)
    unit = build(())
    if profile == B.TYPED_PURE_REPLAY_PROFILE_V2:
        return unit
    machine = CognitiveVMReplayAdapter(B.compile_behavior_unit(unit).program,
        initial_values={"left": left, "right": right}, gas_budget=GAS, execution_context=MACHINE_CONTEXT)
    transitions = []
    while not machine.is_halted():
        machine.step()
        transitions.append(machine.transition_hash())
        assert len(transitions) < 64
    assert machine.read_return_value() == abs(left - right)
    return build(transitions)


def explicit_subject(prepared, *, left, right):
    old = prepared.subjects[0]
    return R.replay_subject(subject_ref=old.subject_ref, unit=old.unit,
                           inputs={"left": left, "right": right})
