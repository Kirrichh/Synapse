"""Legitimacy profile of learned habits: Gold's check of a court birth and its behavior.

The memory court (a separate subsystem, never imported here) proposes the birth
of a learned habit: its frozen habit and trigger records, the criteria it met,
the independence of its witnesses, the tool contracts it is bound to, and the
recorded external results of its basis episodes. Gold does not re-judge
experience; it establishes what it can check itself before its gates admit a
behavior: both records carry their own content identity in the memory domains,
the habit names that trigger, every criterion the court asserts holds, and every
evidence reference is retained with its exact bytes. The admitted behavior is a
typed pure procedure whose only output is the frozen habit's identity key, so
executing it grants no effect; the runtime executes the frozen action pattern
only through its gateway, and only while Gold admits this behavior for the
current tool binding.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import re

from . import behavior as B
from .canonicalization import HashBoundRef, RefKind
from .contracts import ActorIdentity, AttemptId, IdentityDomain, RunId, compute_record_id
from .provenance import OBSERVED_EXTERNAL_INPUT_V1, ExternalInputKind, ObservedExternalInput
from .source_verification import canonical, source_ref

LEARNED_HABIT_CLAIM_V1 = "synapse.memory.learned-habit-birth-claim/v1"
LEARNED_HABIT_VERIFICATION_V1 = "synapse.stage4.gold.learned-habit-verification/v1"
LEARNED_HABIT_PROFILE_V1 = "synapse.stage4.gold.learned-habit-behavior/v1"
#: Policy identity of learned-habit admission (identifier syntax of gate envelopes).
LEARNED_HABIT_POLICY_V1 = "learned-habit-admission.v1"
#: The same policy as a lifecycle authority version.
LEARNED_HABIT_LIFECYCLE_POLICY_V1 = "synapse.memory/learned-habit-admission/v1"
MEMORY_EVIDENCE_V1 = "synapse.memory.evidence/v1"
CONSOLIDATION_JUDGE = ActorIdentity("synapse.memory.consolidation-judge")
LEARNED_HABIT_VERIFIER = ActorIdentity("synapse.gold.learned-habit-verifier")
MAX_EVIDENCE_BYTES = 16 * 1024 * 1024
_CLAIM_FIELDS = {"schema_version", "project_identity", "revision", "consolidation_id", "habit", "trigger",
                 "criteria", "independence", "successor_of", "tool_binding_sha256", "tool_contracts", "policy",
                 "environment", "evidence"}
_INPUTS = {"policy": ("memory-court-policy", "synapse.memory/court-policy/v1", RefKind.CONTRACT_CONDITION),
           "environment": ("memory-configuration", "synapse.memory/configuration/v1", RefKind.ARTIFACT)}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SEAL = object()
_KINDS = {"learned_habit": (IdentityDomain.MEMORY_LEARNED_HABIT, "hab_"),
          "habit_trigger": (IdentityDomain.MEMORY_HABIT_TRIGGER, "trg_")}


def _record(value, kind) -> dict:
    domain, prefix = _KINDS[kind]
    if type(value) is not dict or value.get("kind") != kind or "id" not in value:
        raise ValueError(f"learned habit claim lacks its {kind} record")
    body = {key: item for key, item in value.items() if key != "id"}
    expected = prefix + compute_record_id(domain=domain, canonical_bytes=canonical(body)).digest_sha256
    if value["id"] != expected:
        raise ValueError(f"{kind} record differs from its content identity")
    return value


def _criteria(claim) -> None:
    """Every criterion the court asserts for a pool birth; a successor asserts its predecessor."""
    if claim["successor_of"] is not None:
        if claim["criteria"] is not None or claim["habit"]["supersedes"] != claim["successor_of"]:
            raise ValueError("a successor names exactly the habit it supersedes")
        return
    criteria = claim["criteria"]
    if (type(criteria) is not dict or criteria.get("all_success") is not True
            or criteria.get("all_verifiable") is not True or criteria.get("concrete") is not True
            or criteria.get("independence") != "independent" or claim["independence"].get("verdict") != "independent"
            or claim["habit"]["supersedes"] is not None):
        raise ValueError("a learned birth needs every criterion established")


def inspect_learned_habit_claim(value) -> dict:
    """The court's birth claim, with the identities and assertions Gold can check."""
    if type(value) is not dict or set(value) != _CLAIM_FIELDS or value["schema_version"] != LEARNED_HABIT_CLAIM_V1:
        raise ValueError("learned habit claim has an unknown contract")
    habit, trigger = _record(value["habit"], "learned_habit"), _record(value["trigger"], "habit_trigger")
    if habit["trigger"] != trigger["id"] or habit["origin"] != "learned" or habit["layer"] != 2:
        raise ValueError("a learned habit names another trigger or layer")
    if (type(value["project_identity"]) is not str or _SHA256.fullmatch(value["project_identity"]) is None
            or type(value["tool_binding_sha256"]) is not str or _SHA256.fullmatch(value["tool_binding_sha256"]) is None
            or type(value["revision"]) is not str or re.fullmatch(r"[0-9a-f]{40}", value["revision"]) is None):
        raise ValueError("learned habit claim names no project, revision or tool binding")
    RunId(value["consolidation_id"])
    if (type(value["tool_contracts"]) is not list or not value["tool_contracts"]
            or any(type(item) is not dict or type(item.get("name")) is not str for item in value["tool_contracts"])
            or type(value["policy"]) is not dict or type(value["environment"]) is not dict):
        raise ValueError("a learned behavior is bound to admitted tool contracts, a policy and a configuration")
    _criteria(value)
    if type(value["evidence"]) is not list or any(HashBoundRef.from_dict(item).schema_id != MEMORY_EVIDENCE_V1
                                                  for item in value["evidence"]):
        raise ValueError("learned habit evidence names recorded external results")
    if value["successor_of"] is None and not value["evidence"]:
        raise ValueError("a pool birth names the recorded results of its basis")
    return value


class LearnedHabitVerification:
    """Sealed result of Gold's check of one birth claim; it does not grant admission."""

    def __new__(cls, *args, **kwargs):
        raise TypeError("learned habit verification is produced by its verifier")

    def payload(self) -> dict:
        if (type(self) is not LearnedHabitVerification or self._seal is not _SEAL
                or self._identity != (self._raw, self.evidence)):
            raise ValueError("learned habit verification changed after observation")
        value = json.loads(self._raw)
        inspect_learned_habit_verification(value, evidence=dict(self.evidence))
        return value

    @property
    def reference(self) -> HashBoundRef:
        return source_ref(canonical(self.payload()), LEARNED_HABIT_VERIFICATION_V1, RefKind.ARTIFACT)

    def to_dict(self) -> dict:
        value = self.payload()
        return {"schema_version": LEARNED_HABIT_VERIFICATION_V1, "payload": value,
                "verification_ref": source_ref(canonical(value), LEARNED_HABIT_VERIFICATION_V1,
                                               RefKind.ARTIFACT).to_dict()}


def _retained(evidence, raw_ref) -> bytes:
    ref = HashBoundRef.from_dict(raw_ref)
    raw = evidence.get(ref)
    if type(raw) is not bytes or len(raw) != ref.byte_length or hashlib.sha256(raw).hexdigest() != ref.sha256:
        raise ValueError("learned habit verification lost retained evidence")
    return raw


def inspect_learned_habit_verification(value, *, evidence) -> dict:
    """Reopen the claim and every retained byte; never mint authority."""
    if (type(value) is not dict or set(value) != {"schema_version", "claim", "claim_ref", "operation_id",
                                                  "verification_attempt_id", "verifier", "habit_key"}
            or value["schema_version"] != LEARNED_HABIT_VERIFICATION_V1
            or value["verifier"] != LEARNED_HABIT_VERIFIER.to_dict()):
        raise ValueError("learned habit verification has an unknown contract")
    claim = inspect_learned_habit_claim(value["claim"])
    claim_bytes = canonical(claim)
    if (source_ref(claim_bytes, LEARNED_HABIT_CLAIM_V1, RefKind.CONTRACT_CONDITION).to_dict() != value["claim_ref"]
            or _retained(evidence, value["claim_ref"]) != claim_bytes
            or value["operation_id"] != claim["consolidation_id"]
            or value["habit_key"] != claim["habit"]["id"].partition("_")[2]):
        raise ValueError("learned habit verification differs from its claim")
    AttemptId(value["verification_attempt_id"])
    for item in claim["evidence"]:
        _retained(evidence, item)
    for ref, raw in observed_inputs(claim)[1].items():
        if _retained(evidence, ref.to_dict()) != raw:
            raise ValueError("learned habit verification lost an observed input")
    return value


def verify_learned_habit(claim, evidence: dict) -> LearnedHabitVerification:
    """Check a court birth claim against its retained evidence bytes."""
    claim = inspect_learned_habit_claim(claim)
    claim_bytes = canonical(claim)
    claim_ref = source_ref(claim_bytes, LEARNED_HABIT_CLAIM_V1, RefKind.CONTRACT_CONDITION)
    retained = {**{HashBoundRef.from_dict(item): evidence.get(HashBoundRef.from_dict(item))
                   for item in claim["evidence"]}, **observed_inputs(claim)[1], claim_ref: claim_bytes}
    value = {"schema_version": LEARNED_HABIT_VERIFICATION_V1, "claim": claim, "claim_ref": claim_ref.to_dict(),
             "operation_id": claim["consolidation_id"], "verification_attempt_id": "learned-habit-verification-1",
             "verifier": LEARNED_HABIT_VERIFIER.to_dict(), "habit_key": claim["habit"]["id"].partition("_")[2]}
    raw = canonical(value)
    if len(raw) + sum(len(item) for item in retained.values() if type(item) is bytes) > MAX_EVIDENCE_BYTES:
        raise ValueError("learned habit evidence exceeds its retained byte budget")
    verified = object.__new__(LearnedHabitVerification)
    items = tuple(retained.items())
    for name, item in {"_raw": raw, "evidence": items, "_identity": (raw, items), "_seal": _SEAL}.items():
        object.__setattr__(verified, name, item)
    verified.payload()
    return verified


def _field(name, kind):
    return B.ContractField(name, kind, B.AbsencePolicy.REQUIRED, B.DefaultValue(B.DefaultKind.ABSENT),
                           B.AbsenceDetail(B.AbsenceDetailKind.NONE))


def build_learned_habit_behavior(facts) -> B.SynapseBehaviorUnit:
    """The pure procedure whose only result is the frozen habit's identity key."""
    key = facts["habit_key"]
    claim_ref = HashBoundRef.from_dict(facts["claim_ref"])
    program = B.InlineProgram.from_dict({"form": "INLINE_IR_V1", "ir": {
        "schema_version": "synapse.stage4.gold.canonical-program-ir/v1",
        "program": {"node": "program", "statements": [{"node": "return", "value": {
            "node": "list", "elements": [{"node": "literal", "value_kind": "INT", "value": int(key[index:index + 13], 16)}
                                         for index in range(0, 64, 13)]}}]}}})
    proof = source_ref(canonical(facts), LEARNED_HABIT_VERIFICATION_V1, RefKind.ARTIFACT)
    return B.create_behavior_unit(
        behavior_kind=B.BehaviorKind.PROCEDURE, canonical_program=program,
        input_contract=B.InputContract((), ()),
        output_contract=B.OutputContract((_field("learned_habit_key", B.ValueType.LIST),), ()),
        capability_requirements=(), binding_refs=(), source_evidence_refs=(replace_kind(claim_ref),),
        artifact_refs=(proof,),
        replay_contract=B.ReplayContract(B.TYPED_PURE_REPLAY_PROFILE_V2, (), (), (), (B.ReplayResultClass.MATCH,)),
        verification_contract=B.VerificationContract(LEARNED_HABIT_PROFILE_V1, B.VerificationResultClass.OBSERVATION_MATCH,
                                                     ("frozen-learned-habit-identity",), (replace_kind(claim_ref),),
                                                     (proof,)))


def replace_kind(ref: HashBoundRef) -> HashBoundRef:
    return replace(ref, kind=RefKind.SOURCE_EVIDENCE)


def observed_inputs(claim) -> tuple[dict, dict[HashBoundRef, bytes]]:
    """The policy, configuration and tool contracts a birth depended on, as observed inputs with bytes."""
    inputs: dict[str, list] = {"policy_inputs": [], "environment_inputs": [], "tool_inputs": []}
    retained: dict[HashBoundRef, bytes] = {}
    entries = [("policy_inputs", ExternalInputKind.POLICY, *_INPUTS["policy"], claim["policy"]),
               ("environment_inputs", ExternalInputKind.ENVIRONMENT, *_INPUTS["environment"], claim["environment"])]
    entries += [("tool_inputs", ExternalInputKind.TOOL, item["name"], "synapse.memory/tool-contract/v1",
                 RefKind.ARTIFACT, item) for item in sorted(claim["tool_contracts"], key=lambda item: item["name"])]
    for field, kind, name, version, ref_kind, value in entries:
        raw = canonical(value)
        ref = source_ref(raw, version.replace("/", ".", 1), ref_kind)
        inputs[field].append(ObservedExternalInput(OBSERVED_EXTERNAL_INPUT_V1, kind, name, version, ref))
        retained[ref] = raw
    return inputs, retained
