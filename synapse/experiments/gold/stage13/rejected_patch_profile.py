"""Pure identity and program contracts for exact verified patch outcomes.

Evidence derivation remains with the C1 verifier. Its future-use attestation
passes the ordinary compatibility policy without a profile-specific exception.
"""

from ..behavior import (
    AbsenceDetail, AbsenceDetailKind, AbsencePolicy, BehaviorKind, ConditionRef,
    ContractField, DefaultKind, DefaultValue, InlineProgram, InputContract,
    OutputContract, ReplayContract, ReplayResultClass, ValueType,
    VerificationContract, VerificationResultClass, create_behavior_unit,
    TYPED_PURE_REPLAY_PROFILE_V1,
)
from ..canonicalization import HashBoundRef, RefKind

REJECTED_PATCH_DOMAIN_V2 = "synapse.stage4.gold.rejected-patch-domain/v2"
REJECTED_PATCH_GUARD_V3 = "synapse.stage4.gold.rejected-patch-guard/v3"
REJECTED_PATCH_GUARD_V4 = "synapse.stage4.gold.rejected-patch-guard/v4"
VERIFIED_PATCH_DOMAIN_V1 = "synapse.stage4.gold.verified-patch-domain/v1"
VERIFIED_PATCH_GUARD_V1 = "synapse.stage4.gold.verified-patch-guard/v1"


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
        replay_contract=ReplayContract(REJECTED_PATCH_GUARD_V3, transitions, (), (), (ReplayResultClass.MATCH,)),
        verification_contract=VerificationContract(
            REJECTED_PATCH_GUARD_V3, VerificationResultClass.BEHAVIOR_REJECTED,
            ("exact-patch-did-not-resolve-task",), (report,), (oracle,),
        ),
    )


def rejected_guard_inputs(*, repository_revision, task_contract_sha256):
    """Translate the frozen task/revision into lossless bounded CVM values."""
    if (type(repository_revision) is not str or len(repository_revision) != 40
            or any(char not in "0123456789abcdef" for char in repository_revision)):
        raise ValueError("guard inputs require an exact Git commit")
    revision = [int(repository_revision[index:index + 13], 16) for index in range(0, 40, 13)]
    return {**{f"revision_word_{index}": word for index, word in enumerate(revision)},
            **{f"task_word_{index}": word for index, word in enumerate(fingerprint_words(task_contract_sha256))}}


def build_conditional_rejected_patch_guard(*, domain, domain_ref, report, oracle, transitions=()):
    """Compute whether a verified negative fact applies to the current task/base.

    The complete domain, including patch, C1 policy and oracle configuration,
    remains independently checked at use. This computation narrows relevance;
    it never generalizes a failed patch to other methods or tasks.
    """
    return _conditional_patch_fact(domain=domain, domain_ref=domain_ref, report=report, oracle=oracle,
        transitions=transitions, behavior_kind=BehaviorKind.REJECTED_HYPOTHESIS_GUARD,
        output_name="applicable_rejected_domain", profile=REJECTED_PATCH_GUARD_V4,
        result=VerificationResultClass.BEHAVIOR_REJECTED, claim="exact-patch-did-not-resolve-task")


def build_verified_patch_guard(*, domain, domain_ref, report, oracle, transitions=(), patch_ref=None):
    """A scoped positive observation, never permission to repeat its effects.

    The C1 evidence owner must independently establish the original successful
    patch. Replay only computes whether its task/base identity matches; fresh
    execution and verification remain required for the new task occurrence.
    """
    if patch_ref is not None and (type(patch_ref) is not HashBoundRef or patch_ref.kind is not RefKind.ARTIFACT
                                  or patch_ref.schema_id != "synapse.stage4.gold.c1-patch-bytes/v1"
                                  or patch_ref.ref_id != patch_ref.sha256 or not patch_ref.byte_length
                                  or patch_ref.sha256 != domain["patch_sha256"]):
        raise ValueError("retained patch differs from its verified domain")
    return _conditional_patch_fact(domain=domain, domain_ref=domain_ref, report=report, oracle=oracle,
        transitions=transitions, behavior_kind=BehaviorKind.REPOSITORY_FACT_CHECK,
        output_name="applicable_verified_patch", profile=VERIFIED_PATCH_GUARD_V1,
        result=VerificationResultClass.CONTRACT_SATISFIED, claim="exact-patch-resolved-task", patch_ref=patch_ref)


def _conditional_patch_fact(*, domain, domain_ref, report, oracle, transitions,
                            behavior_kind, output_name, profile, result, claim, patch_ref=None):
    inputs = rejected_guard_inputs(repository_revision=domain["base_revision"],
                                   task_contract_sha256=domain["task_contract_ref"]["sha256"])
    def words(values):
        return {"node": "list", "elements": [{"node": "literal", "value_kind": "INT", "value": item}
                                              for item in values]}
    comparisons = [{"node": "binary", "operator": "EQ", "left": {"node": "variable", "name": name},
                    "right": {"node": "literal", "value_kind": "INT", "value": value}}
                   for name, value in sorted(inputs.items())]
    condition = comparisons[0]
    for comparison in comparisons[1:]:
        condition = {"node": "binary", "operator": "AND", "left": condition, "right": comparison}
    program = InlineProgram.from_dict({"form": "INLINE_IR_V1", "ir": {
        "schema_version": "synapse.stage4.gold.canonical-program-ir/v1",
        "program": {"node": "program", "statements": [{"node": "if",
            "condition": condition,
            "then_body": [{"node": "return", "value": words(fingerprint_words(domain_ref.sha256))}],
            "else_body": [{"node": "return", "value": words([])}]}]}}})
    fields = tuple(ContractField(name, ValueType.INTEGER, AbsencePolicy.DEFAULTED,
        DefaultValue(DefaultKind.VALUE, value), AbsenceDetail(AbsenceDetailKind.NONE))
        for name, value in sorted(inputs.items()))
    output = ContractField(output_name, ValueType.LIST, AbsencePolicy.REQUIRED,
        DefaultValue(DefaultKind.ABSENT), AbsenceDetail(AbsenceDetailKind.NONE))
    return create_behavior_unit(behavior_kind=behavior_kind,
        canonical_program=program, input_contract=InputContract(fields, ()),
        output_contract=OutputContract((output,), ()), capability_requirements=(),
        binding_refs=(), source_evidence_refs=(report,), artifact_refs=(oracle,) if patch_ref is None else (oracle, patch_ref),
        replay_contract=ReplayContract(TYPED_PURE_REPLAY_PROFILE_V1, tuple(transitions), (), (), (ReplayResultClass.MATCH,)),
        verification_contract=VerificationContract(profile, result, (claim,), (report,), (oracle,)))
