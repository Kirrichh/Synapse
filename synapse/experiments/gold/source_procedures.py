"""Typed task-coverage procedures extracted from independently verified sources.

Publication supplies verified bindings; an attempt supplies its actual target
bindings. The existing CVM computes applicability and the uncovered obligation
count. This profile grants neither a shell action nor a correctness verdict.
Historical command recipes remain retained evidence for separately authorised
verification operations.
"""

from . import behavior as B
from .canonicalization import HashBoundRef, RefKind
from .source_verification import SOURCE_VERIFICATION_V1, canonical, source_ref

SOURCE_COVERAGE_PROFILE_V1 = "synapse.stage4.gold.source-task-coverage/v1"


def _field(name, kind):
    return B.ContractField(name, kind, B.AbsencePolicy.REQUIRED,
        B.DefaultValue(B.DefaultKind.ABSENT), B.AbsenceDetail(B.AbsenceDetailKind.NONE))


def coverage_program():
    variable = lambda name: {"node": "variable", "name": name}
    literal = lambda value: {"node": "literal", "value_kind": "INT", "value": value}
    binary = lambda op, left, right: {"node": "binary", "operator": op, "left": left, "right": right}
    def returned(eligible):
        return {"node": "return", "value": {"node": "list", "elements": [
            literal(eligible), variable("matched"),
            binary("SUB", variable("required"), variable("matched")),
        ]}}
    condition = binary("AND",
        binary("GT", variable("matched"), literal(0)),
        binary("LTE", variable("matched"), variable("required")))
    return B.InlineProgram.from_dict({"form": "INLINE_IR_V1", "ir": {
        "schema_version": "synapse.stage4.gold.canonical-program-ir/v1",
        "program": {"node": "program", "statements": [{
            "node": "if", "condition": condition,
            "then_body": [returned(1)], "else_body": [returned(0)],
        }]}}})


def build_source_coverage_behavior(facts, bindings):
    knowledge = HashBoundRef.from_dict(facts["knowledge_ref"])
    proof = source_ref(canonical(facts), SOURCE_VERIFICATION_V1, RefKind.ARTIFACT)
    return B.create_behavior_unit(
        behavior_kind=B.BehaviorKind.PROCEDURE, canonical_program=coverage_program(),
        input_contract=B.InputContract((_field("required", B.ValueType.INTEGER),
                                       _field("matched", B.ValueType.INTEGER)), ()),
        output_contract=B.OutputContract((_field("coverage", B.ValueType.LIST),), ()),
        capability_requirements=(), binding_refs=tuple(bindings),
        source_evidence_refs=(knowledge,), artifact_refs=(proof,),
        replay_contract=B.ReplayContract(B.TYPED_PURE_REPLAY_PROFILE_V2, (), (), (), (B.ReplayResultClass.MATCH,)),
        verification_contract=B.VerificationContract(
            SOURCE_COVERAGE_PROFILE_V1, B.VerificationResultClass.OBSERVATION_MATCH,
            ("task-binding-coverage-from-verified-source",), (knowledge,), (proof,)),
    )


def source_coverage_inputs(unit, target_refs):
    if unit.core.verification_contract.profile_id != SOURCE_COVERAGE_PROFILE_V1:
        raise ValueError("source has no declared coverage procedure")
    if type(target_refs) is not tuple or any(type(ref) is not HashBoundRef for ref in target_refs):
        raise ValueError("coverage needs exact target references")
    if len(set(target_refs)) != len(target_refs):
        raise ValueError("coverage targets are repeated")
    targets = set(target_refs)
    return {"required": len(targets), "matched": len(targets & set(unit.core.binding_refs))}


def validate_source_coverage(value, *, inputs):
    expected = [int(0 < inputs["matched"] <= inputs["required"]),
                inputs["matched"], inputs["required"] - inputs["matched"]]
    if canonical(value) != canonical(expected):
        raise ValueError("replayed coverage differs from its bound task obligations")
    return value
