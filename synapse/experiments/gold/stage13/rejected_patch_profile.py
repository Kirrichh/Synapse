"""Pure identity and program contract for the exact rejected-patch fingerprint.

Evidence derivation remains with the C1 verifier. Compatibility may recognize
this closed inert profile without depending on the execution or store owners.
"""

from ..behavior import (
    AbsenceDetail, AbsenceDetailKind, AbsencePolicy, BehaviorKind, ConditionRef,
    ContractField, DefaultKind, DefaultValue, InlineProgram, InputContract,
    OutputContract, ReplayContract, ReplayResultClass, ValueType,
    VerificationContract, VerificationResultClass, create_behavior_unit,
)
from ..canonicalization import HashBoundRef, RefKind

REJECTED_PATCH_DOMAIN_V2 = "synapse.stage4.gold.rejected-patch-domain/v2"
REJECTED_PATCH_GUARD_V2 = "synapse.stage4.gold.rejected-patch-guard/v2"


def fingerprint_words(digest):
    """Losslessly encode SHA-256 in five integers within the canonical IR range."""
    if type(digest) is not str or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("a fingerprint must be a canonical SHA-256 digest")
    return [int(digest[index:index + 13], 16) for index in range(0, 64, 13)]


def build_rejected_patch_guard(*, domain_ref, report, oracle, transitions=()):
    """Build the one inert negative-fact profile; this grants no authority."""
    condition = ConditionRef(domain_ref.ref_id, domain_ref.schema_id, domain_ref.sha256,
                             domain_ref.byte_length, domain_ref.media_type)
    program = InlineProgram.from_dict({"form": "INLINE_IR_V1", "ir": {
        "schema_version": "synapse.stage4.gold.canonical-program-ir/v1",
        "program": {"node": "program", "statements": [{"node": "return", "value": {
            "node": "list", "elements": [
                {"node": "literal", "value_kind": "INT", "value": word}
                for word in fingerprint_words(domain_ref.sha256)
            ],
        }}]},
    }})
    field = ContractField("rejected_domain_key", ValueType.LIST, AbsencePolicy.REQUIRED,
                          DefaultValue(DefaultKind.ABSENT), AbsenceDetail(AbsenceDetailKind.NONE))
    return create_behavior_unit(
        behavior_kind=BehaviorKind.REJECTED_HYPOTHESIS_GUARD, canonical_program=program,
        input_contract=InputContract((), (condition,)), output_contract=OutputContract((field,), (condition,)),
        capability_requirements=(), binding_refs=(), source_evidence_refs=(report,), artifact_refs=(oracle,),
        replay_contract=ReplayContract(REJECTED_PATCH_GUARD_V2, transitions, (), (), (ReplayResultClass.MATCH,)),
        verification_contract=VerificationContract(
            REJECTED_PATCH_GUARD_V2, VerificationResultClass.BEHAVIOR_REJECTED,
            ("exact-patch-did-not-resolve-task",), (report,), (oracle,),
        ),
    )


def is_inert_rejected_patch_guard(unit):
    """Recognize exact bytes, not a worker-supplied behavior label."""
    core = unit.core
    if (core.behavior_kind is not BehaviorKind.REJECTED_HYPOTHESIS_GUARD
            or len(core.input_contract.preconditions) != 1 or len(core.source_evidence_refs) != 1
            or len(core.artifact_refs) != 1 or core.capability_requirements or core.binding_refs):
        return False
    condition = core.input_contract.preconditions[0]
    if condition.condition_schema_id != REJECTED_PATCH_DOMAIN_V2:
        return False
    domain_ref = HashBoundRef(RefKind.CONTRACT_CONDITION, condition.condition_id, condition.condition_schema_id,
                             condition.sha256, condition.byte_length, condition.media_type)
    expected = build_rejected_patch_guard(domain_ref=domain_ref, report=core.source_evidence_refs[0], oracle=core.artifact_refs[0],
                                        transitions=core.replay_contract.expected_transition_ids)
    return unit.to_dict() == expected.to_dict()
