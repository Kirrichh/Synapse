"""Independent verification and exact authority for a prospective publication.

The first extraction profile uses Stage 12's independently verified negative
knowledge. The publisher cannot supply executable bytes, grant flags or a
success label. Existing attestation, taint and admission owners establish their
own facts; this evaluator binds their complete records into one write set.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib

from .. import admission as A, library_admission as LA, taint as T
from ..authority_config import create_gate_evaluator_declaration
from ..behavior import (BehaviorBlob, BehaviorManifest, SynapseBehaviorUnit, create_behavior_blob,
                        create_behavior_manifest, compile_behavior_unit, behavior_unit_from_dict)
from ..behavior import (AbsenceDetail, AbsenceDetailKind, AbsencePolicy, BehaviorKind, ConditionRef,
    ContractField, DefaultKind, DefaultValue, InlineProgram, InputContract, OutputContract, ReplayContract,
    ReplayResultClass, ValueType, VerificationContract, VerificationResultClass, create_behavior_unit)
from ..bindings import BINDING_SCHEMA_V1, BINDING_MEDIA_TYPE_V1
from ..canonicalization import HashBoundRef, RefKind
from ..contracts import (ActorIdentity, AuthorityIdentity, AuthorityRole, GateKind, RepositoryRevision, RunId, AttemptId,
                         record_id_reference_from_dict, validate_record_id)
from ..gate_findings import consumption_finding_from_effective_taint
from ..lifecycle import LifecycleContext, LifecycleScope, LifecycleState, LIFECYCLE_CONTEXT_V1
from ..provenance import (
    BehaviorAttestation, BuilderRuntimeIdentity, ObservedExternalInput,
    OracleObservation, configure_platform_attester,
    behavior_attestation_to_ref, validate_behavior_attestation,
)
from ..runner.c1_boundary import C1VerificationEvidence
from ..stage10.context_codec import encode_canonical, decode_canonical
from ..stage12.reusable import (ReusableVerificationAuthority, rejected_patch_domain, create_rejected_patch_guard,
    read_reusable_use_context, inspect_reusable_use_context, verified_patch_domain, create_verified_patch_guard)
from ..stage12.verification_contract import VerificationRecord, require_verification_record, inspect_verification_record
from ..stage12.outcome import evaluate_attempt_outcome, inspect_outcome, FinalStatus
from ..source_verification import (SourceVerification, SOURCE_VERIFICATION_V1, SOURCE_CLAIM_V1,
    SOURCE_EXTRACTOR, SOURCE_VERIFIER, SOURCE_POLICY_V1, inspect_source_verification, source_ref)
from ..stage10.task_contract import TASK_CONTRACT_SCHEMA_V3
from .rejected_patch_profile import (REJECTED_PATCH_GUARD_V3, REJECTED_PATCH_GUARD_V4,
    REJECTED_PATCH_GUARD_V5, build_partial_patch_guard,
    VERIFIED_PATCH_DOMAIN_V1, VERIFIED_PATCH_GUARD_V1, build_verified_patch_guard)


PUBLICATION_POLICY_V2 = "stage13-atomic-publication/v2"
PUBLICATION_POLICY_V3 = "stage13-atomic-publication/v3"
REQUEST_SCHEMA_V3 = "synapse.stage4.gold.publication-request/v3"
REQUEST_SCHEMA_V4 = "synapse.stage4.gold.publication-request/v4"
SOURCE_REQUEST_V1 = "synapse.stage4.gold.source-publication-request/v1"
SOURCE_APPLICABILITY_V1 = "synapse.stage4.gold.source-applicability/v1"
DECISION_SCHEMA_V3 = "synapse.stage4.gold.publication-authority-decision/v3"
REFUSAL_SCHEMA_V1 = "synapse.stage4.gold.publication-refusal/v1"
EXTRACTOR = ActorIdentity("synapse.gold.rejected-patch-extractor")
POSITIVE_EXTRACTOR = ActorIdentity("synapse.gold.verified-patch-extractor")
EVALUATOR = AuthorityIdentity("synapse.gold.publication-authority")
_SEAL = object()


class PublicationDecisionKind(str, Enum):
    AUTHORIZE_PUBLICATION = "AUTHORIZE_PUBLICATION"
    REJECT_PUBLICATION = "REJECT_PUBLICATION"
    QUARANTINE = "QUARANTINE"
    REQUIRE_HUMAN_REVIEW = "REQUIRE_HUMAN_REVIEW"


class PublicationViolation(ValueError):
    """Publication evidence or exact transaction authority is inconsistent."""


def _refusal(facts):
    """The closed extraction policy; task correctness remains Stage 12's claim."""
    c1 = facts["c1"]
    if facts["failure_codes"]:
        return PublicationDecisionKind.QUARANTINE, "VERIFICATION_INVALID"
    if facts["interrupted"] or c1 is not None and c1["infra_error"]:
        return PublicationDecisionKind.REJECT_PUBLICATION, "EXECUTION_INCOMPLETE"
    if facts["refused"] or c1 is not None and c1["refused"]:
        return PublicationDecisionKind.REJECT_PUBLICATION, "EXECUTION_REFUSED"
    if c1 is None or facts["plan"] is None:
        return PublicationDecisionKind.REJECT_PUBLICATION, "INDEPENDENT_PROOF_UNAVAILABLE"
    if c1["no_candidate"]:
        return PublicationDecisionKind.REJECT_PUBLICATION, "NO_VERIFIED_CANDIDATE"
    if c1["oracle_resolved"] is not False:
        return PublicationDecisionKind.REJECT_PUBLICATION, "EXTRACTION_PROFILE_UNSUPPORTED"
    return None


def reference(payload, schema=REQUEST_SCHEMA_V3):
    raw = encode_canonical(payload)
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(RefKind.ARTIFACT, digest, schema, digest, len(raw), "application/json")


def request_reference(payload):
    """Version the positive request while preserving every historical ref."""
    return reference(payload, REQUEST_SCHEMA_V4 if payload["schema_version"] == REQUEST_SCHEMA_V4 else REQUEST_SCHEMA_V3)


def _require_verification(value):
    if type(value) is SourceVerification:
        value.payload()
        return value
    return require_verification_record(value)


def _source_request(value):
    return value["schema_version"] == SOURCE_REQUEST_V1


def _compatibility_contract(request):
    if _source_request(request):
        return {"profile": SOURCE_APPLICABILITY_V1, "claim_ref": request["verification"]["payload"]["claim_ref"],
                "verification_ref": request["verification"]["verification_ref"]}
    return {"profile": "exact-admitted-use-context/v1", "domain": request["domain"],
            "context_ref": request["use_context"]["ref"]}


def _publication_reasons(request):
    if request["schema_version"] == REQUEST_SCHEMA_V4:
        return ["INDEPENDENT_FULL_PROOF", "EXACT_VERIFIED_PATCH", "SCOPED_ADMISSION"]
    return (["INDEPENDENT_SOURCE_PROOF", "RETAINED_SOURCE_KNOWLEDGE", "SCOPED_ADMISSION"]
            if _source_request(request) else ["INDEPENDENT_NEGATIVE_PROOF", "EXACT_PURE_GUARD", "SCOPED_ADMISSION"])


def _build_source_behavior(facts, transitions):
    """Compile the verified knowledge identity, preserving its source contract.

    Replay returns the retained knowledge identity. It never repeats a recipe's
    external command or represents the old command result as a fresh result.
    """
    knowledge = HashBoundRef.from_dict(facts["knowledge_ref"])
    claim = HashBoundRef.from_dict(facts["claim_ref"])
    condition = ConditionRef(claim.ref_id, claim.schema_id, claim.sha256, claim.byte_length, claim.media_type)
    program = InlineProgram.from_dict({"form": "INLINE_IR_V1", "ir": {
        "schema_version": "synapse.stage4.gold.canonical-program-ir/v1",
        "program": {"node": "program", "statements": [{"node": "return", "value": {
            "node": "list", "elements": [{"node": "literal", "value_kind": "INT", "value":
                int(knowledge.sha256[index:index + 13], 16)} for index in range(0, 64, 13)]}}]}}})
    bindings = []
    for binding in facts["bindings"]:
        raw = encode_canonical({k: v for k, v in binding.items() if k != "binding_id"})
        bindings.append(HashBoundRef(RefKind.BINDING, binding["binding_id"]["value"], BINDING_SCHEMA_V1,
            hashlib.sha256(raw).hexdigest(), len(raw), BINDING_MEDIA_TYPE_V1))
    proof = source_ref(encode_canonical(facts), SOURCE_VERIFICATION_V1, RefKind.ARTIFACT)
    field = ContractField("verified_knowledge_key", ValueType.LIST, AbsencePolicy.REQUIRED,
        DefaultValue(DefaultKind.ABSENT), AbsenceDetail(AbsenceDetailKind.NONE))
    return create_behavior_unit(behavior_kind=BehaviorKind[facts["claim"]["kind"]], canonical_program=program,
        input_contract=InputContract((), (condition,)), output_contract=OutputContract((field,), (condition,)),
        capability_requirements=(), binding_refs=tuple(bindings), source_evidence_refs=(knowledge,),
        artifact_refs=(proof,), replay_contract=ReplayContract(SOURCE_VERIFICATION_V1, tuple(transitions), (), (), (ReplayResultClass.MATCH,)),
        verification_contract=VerificationContract(SOURCE_VERIFICATION_V1, VerificationResultClass.OBSERVATION_MATCH,
            (facts["knowledge"]["meaning"],), (knowledge,), (proof,)))


def _transaction_contract(value):
    return {name: value[name] for name in ("subject_ref", "attestation_ref", "taint", "lifecycle_context",
        "required_transition", "grant", "compatibility", "request_ref", "subject_refs", "publication_scope",
        "index_visibility", "retention_roots", "outcome_ref", "sequence")}


def _scope_contract(request):
    unit = behavior_unit_from_dict(request["unit"])
    return {
        "subject_refs": {"behavior": unit.content_key.to_dict(), "manifest": request["manifest"]["manifest_id"],
            "attestation": request["attestation_ref"],
            "bindings": request["manifest"]["binding_refs"], "evidence": request["evidence_refs"]},
        "publication_scope": {"namespace": "library", "domain": request["domain"]},
        "index_visibility": {"searchable": True, "content_key": unit.content_key.value,
            "manifest_id": record_id_reference_from_dict(request["manifest"]["manifest_id"]).value,
            "behavior_kind": unit.core.behavior_kind.value},
        "retention_roots": request["evidence_refs"],
        "outcome_ref": None if _source_request(request) else request["outcome"]["outcome_ref"],
    }


def inspect_publication_decision(value, *, request, registration, retained_evidence=None):
    """Validate retained authority against its anchored request and gate records.

    This is an integrity reader, never a constructor of executable authority.
    The write path still requires the live evaluator-produced decision.
    """
    fields = {"schema_version", "decision_kind", "request_ref", "transaction_id", "authority_identity",
        "source_actor_ids", "authority_declaration", "independence_proof", "publisher_identity", "policy_version",
        "verification_ref", "subject_ref", "attestation_ref", "taint", "lifecycle_context", "required_transition",
        "grant", "ingestion", "publication", "compatibility", "reason_codes", "transaction_contract_sha256",
        "subject_refs", "publication_scope", "index_visibility", "retention_roots", "outcome_ref", "sequence"}
    if type(value) is not dict or set(value) != fields:
        raise PublicationViolation("publication decision has an unknown contract")
    source = _source_request(request)
    if source:
        facts = inspect_source_verification(request["verification"]["payload"], evidence=retained_evidence)
        if (request["verification"]["schema_version"] != SOURCE_VERIFICATION_V1
                or source_ref(encode_canonical(facts), SOURCE_VERIFICATION_V1, RefKind.ARTIFACT).to_dict()
                   != request["verification"]["verification_ref"]
                or request["outcome"] is not None or request["use_context"] is not None
                or request["domain"] != facts["claim"]):
            raise PublicationViolation("source publication changed its independently verified origin")
        source_unit = _build_source_behavior(facts, request["source_transitions"])
        if request["unit"] != source_unit.to_dict():
            raise PublicationViolation("source behavior exceeds independently verified knowledge")
    else:
        facts = inspect_verification_record(request["verification"])
        outcome = inspect_outcome(request["outcome"])
        if request["schema_version"] == REQUEST_SCHEMA_V4:
            unit = behavior_unit_from_dict(request["unit"])
            domain = request["domain"]
            if (outcome["status"] != FinalStatus.FULL.value
                    or facts["task_contract_ref"]["schema_id"] != TASK_CONTRACT_SCHEMA_V3
                    or domain["schema_version"] != VERIFIED_PATCH_DOMAIN_V1
                    or domain["task_contract_ref"] != facts["task_contract_ref"]
                    or domain["patch_sha256"] != facts["c1"]["verified_patch_sha256"]
                    or domain["command_policy_ref"] != facts["c1"]["command_policy_ref"]
                    or request["identity"]["policy"] != PUBLICATION_POLICY_V3):
                raise PublicationViolation("positive publication lacks its exact independent FULL proof")
            patch_ref = next((ref for ref in unit.core.artifact_refs
                if ref.schema_id == "synapse.stage4.gold.c1-patch-bytes/v1"), None)
            if patch_ref is not None:
                patch_bytes = None if retained_evidence is None else retained_evidence.get(patch_ref)
                if (type(patch_bytes) is not bytes or len(patch_bytes) != patch_ref.byte_length
                        or hashlib.sha256(patch_bytes).hexdigest() != patch_ref.sha256
                        or patch_ref.to_dict() not in request["evidence_refs"]):
                    raise PublicationViolation("positive behavior lost its exact independently retained patch")
            expected = build_verified_patch_guard(domain=domain,
                domain_ref=replace(reference(domain, VERIFIED_PATCH_DOMAIN_V1), kind=RefKind.CONTRACT_CONDITION),
                report=replace(HashBoundRef.from_dict(facts["c1"]["report_ref"]), kind=RefKind.SOURCE_EVIDENCE),
                oracle=HashBoundRef.from_dict(facts["c1"]["oracle_result_ref"]),
                transitions=unit.core.replay_contract.expected_transition_ids,
                patch_ref=patch_ref)
            if unit.to_dict() != expected.to_dict():
                raise PublicationViolation("positive behavior exceeds its independently verified patch observation")
        elif request["unit"]["core"]["verification_contract"]["profile_id"] == REJECTED_PATCH_GUARD_V5:
            unit = behavior_unit_from_dict(request["unit"])
            domain = request["domain"]
            c1 = facts["c1"]
            if (facts["task_contract_ref"]["schema_id"] != TASK_CONTRACT_SCHEMA_V3
                    or c1 is None or c1["oracle_resolved"] is not False or not c1["commands_complete"]
                    or c1["infra_error"] or c1["refused"] or c1["no_candidate"]
                    or domain["patch_sha256"] != c1["verified_patch_sha256"]
                    or domain["command_policy_ref"] != c1["command_policy_ref"]
                    or domain["task_contract_ref"] != facts["task_contract_ref"]):
                raise PublicationViolation("partial patch lacks its completed C1 and negative whole-task proof")
            patches = [ref for ref in unit.core.artifact_refs
                       if ref.schema_id == "synapse.stage4.gold.c1-patch-bytes/v1"]
            if len(patches) != 1:
                raise PublicationViolation("partial patch lost its unique retained material")
            patch_ref, = patches
            patch_bytes = None if retained_evidence is None else retained_evidence.get(patch_ref)
            if (type(patch_bytes) is not bytes or len(patch_bytes) != patch_ref.byte_length
                    or hashlib.sha256(patch_bytes).hexdigest() != patch_ref.sha256
                    or patch_ref.to_dict() not in request["evidence_refs"]):
                raise PublicationViolation("partial patch differs from its physically retained material")
            expected = build_partial_patch_guard(domain=domain,
                domain_ref=replace(reference(domain, domain["schema_version"]), kind=RefKind.CONTRACT_CONDITION),
                report=replace(HashBoundRef.from_dict(c1["report_ref"]), kind=RefKind.SOURCE_EVIDENCE),
                oracle=HashBoundRef.from_dict(c1["oracle_result_ref"]), patch_ref=patch_ref,
                transitions=unit.core.replay_contract.expected_transition_ids)
            if unit.to_dict() != expected.to_dict():
                raise PublicationViolation("partial patch exceeds its two independently checked claims")
    unit = behavior_unit_from_dict(request["unit"])
    blob = create_behavior_blob(unit)
    manifest = create_behavior_manifest(unit, blob, compiler_binding=compile_behavior_unit(unit))
    subject = LA.write_subject_ref(content_key=unit.content_key, manifest_id=manifest.manifest_id)
    context = LifecycleContext.from_dict(request["lifecycle_context"])
    domain_ref = reference(request["domain"])
    use_record = None if source else inspect_reusable_use_context(request["use_context"], base_revision=request["domain"]["base_revision"],
                                              task_contract_ref=request["domain"]["task_contract_ref"])
    attestation = request["attestation"]
    if not source and (any(attestation[name] != use_record[name] for name in
            ("repository_revision", "task_contract_ref", "policy_inputs", "environment_inputs", "tool_inputs", "oracle_observation"))
            or replace(HashBoundRef.from_dict(request["use_context"]["ref"]), kind=RefKind.SOURCE_EVIDENCE).to_dict()
               not in attestation["source_refs"]):
        raise PublicationViolation("publication provenance differs from its admitted future-use context")
    if (not source and (outcome["verification"] != request["verification"] or facts["reusable_candidates"] or facts["publication"] is not None)
            or request["schema_version"] not in {REQUEST_SCHEMA_V3, REQUEST_SCHEMA_V4, SOURCE_REQUEST_V1} or request["manifest"] != manifest.to_dict(unit=unit, blob=blob)
            or context.scope is not LifecycleScope.REVISION or context.context_id != domain_ref.sha256
            or value["schema_version"] != DECISION_SCHEMA_V3 or value["authority_identity"] != EVALUATOR.to_dict()
            or value["decision_kind"] != PublicationDecisionKind.AUTHORIZE_PUBLICATION.value
            or value["subject_ref"] != subject.to_dict() or value["request_ref"] != request_reference(request).to_dict()
            or value["transaction_id"] != "pub-" + reference(request["identity"]).sha256
            or value["verification_ref"] != request["verification"]["verification_ref"]
            or value["lifecycle_context"] != context.to_dict() or value["taint"] != request["taint"]
            or value["attestation_ref"] != request["attestation_ref"]
            or value["policy_version"] != request["run_policy_version"]
            or value["required_transition"] != ["ATTESTED", "ADMITTED", "INDEXED"]
            or value["grant"] != {"scopes": [context.context_id], "capabilities": [], "oracles": []}
            or value["compatibility"] != _compatibility_contract(request)
            or value["reason_codes"] != _publication_reasons(request)
            or value["transaction_contract_sha256"] != reference(_transaction_contract(value)).sha256):
        raise PublicationViolation("retained decision differs from its exact verified publication contract")
    if (any(value[name] != item for name, item in _scope_contract(request).items())
            or type(value["sequence"]) is not int or value["sequence"] < 1 or value["sequence"] % 2 != 1):
        raise PublicationViolation("publication changed its exact subjects, visibility, retention or causal sequence")
    attestation_ref = HashBoundRef.from_dict(value["attestation_ref"])
    attestation_bytes = encode_canonical(request["attestation"])
    if attestation_ref.sha256 != hashlib.sha256(attestation_bytes).hexdigest() or attestation_ref.byte_length != len(attestation_bytes):
        raise PublicationViolation("publication attestation reference differs from its retained bytes")
    actors = tuple(ActorIdentity.from_dict(item) for item in value["source_actor_ids"])
    actor_values = sorted(item.value for item in actors)
    required_actors = {value["publisher_identity"]["component_id"],
        request["attestation"]["attester_identity"]["value"],
        request["builder_input"]["builder_actor_identity"]["value"]}
    required_actors.update({SOURCE_EXTRACTOR.value, SOURCE_VERIFIER.value} if source else {
        (POSITIVE_EXTRACTOR if request["schema_version"] == REQUEST_SCHEMA_V4 else EXTRACTOR).value,
        request["domain"]["oracle_identity"], use_record["oracle_observation"]["oracle_identity"]["value"],
        use_record["consumer_actor"]["value"]})
    if source:
        if (any(attestation[name] != facts["claim"][name] for name in ("policy_inputs", "environment_inputs", "tool_inputs"))
                or attestation["repository_revision"] != RepositoryRevision.git_commit(facts["claim"]["revision"]).to_dict()
                or attestation["task_contract_ref"] != facts["claim_ref"]
                or attestation["oracle_observation"]["oracle_identity"] != SOURCE_VERIFIER.to_dict()
                or attestation["oracle_observation"]["task_contract_ref"] != facts["claim_ref"]
                or attestation["oracle_observation"]["result_ref"] != replace(HashBoundRef.from_dict(request["verification"]["verification_ref"]), kind=RefKind.SOURCE_EVIDENCE).to_dict()):
            raise PublicationViolation("source attestation rewrote the producer's observations")
    if (EVALUATOR.value in actor_values or not required_actors.issubset(actor_values)
            or value["source_actor_ids"] != request["source_actor_ids"]):
        raise PublicationViolation("publication authority is not independent of its complete actor set")
    declaration, proof = value["authority_declaration"], value["independence_proof"]
    for record, field in ((declaration, "declaration_id"), (proof, "proof_id")):
        validate_record_id(record_id_reference_from_dict(record[field]),
                           canonical_bytes=encode_canonical({key: item for key, item in record.items() if key != field}))
    if (declaration["evaluator_identity"] != EVALUATOR.to_dict() or proof["evaluator_identity"] != EVALUATOR.to_dict()
            or declaration["policy_version"] != value["policy_version"] or proof["source_actor_ids"] != actor_values
            or proof["declaration_id"] != declaration["declaration_id"]
            or proof["configuration_id"] != declaration["configuration_id"]
            or declaration["configuration_id"] != request["attestation"]["configuration_id"]
            or proof["independence_reason"] != "EVALUATOR_DISJOINT_FROM_SUBJECT_AND_CONFIGURED_ACTORS"):
        raise PublicationViolation("retained independence proof does not match configured authority")
    gates = []
    for name, kind in (("ingestion", GateKind.INGESTION), ("publication", GateKind.PUBLICATION)):
        gate = A.gate_decision_from_dict(value[name], expected_ref=HashBoundRef.from_dict(registration[name]["ref"]))
        if (registration[name]["record"] != value[name] or not gate.admitted or gate.gate_kind is not kind
                or gate.subject_refs != (subject,) or gate.authority_identity != EVALUATOR
                or gate.configuration_digest != declaration["configuration_id"]["digest_sha256"]
                or gate.evaluator_declaration_digest != hashlib.sha256(encode_canonical({key: item for key, item in declaration.items() if key != "declaration_id"})).hexdigest()
                or gate.independence_proof_digest != proof["proof_id"]["digest_sha256"]
                or gate.policy_version != value["policy_version"] or gate.envelope is None
                or gate.envelope.run_id.value != facts["operation_id" if source else "run_id"]
                or gate.envelope.attempt_id.value != facts["verification_attempt_id" if source else "attempt_id"]):
            raise PublicationViolation("retained gate differs from its authority or execution context")
        A.require_dimension_evidence(gate)
        gates.append(gate)
    A.require_publication_grant(gates[1], granted=A.GrantEnvelope((context.context_id,), (), (), value["policy_version"]))
    if gates[1].predecessor_decision_digest != gates[0].gate_decision_id.digest_sha256:
        raise PublicationViolation("publication lost its ingestion predecessor")


@dataclass(frozen=True, init=False)
class PublicationRequest:
    _raw: bytes
    _identity: tuple[object, ...]
    evidence: tuple[tuple[HashBoundRef, bytes], ...]
    verification: VerificationRecord | SourceVerification
    unit: SynapseBehaviorUnit
    blob: BehaviorBlob
    manifest: BehaviorManifest
    attestation: BehaviorAttestation
    taint: T.SourceTaintProfile
    context: LifecycleContext

    def __new__(cls, *args, **kwargs):
        raise TypeError("publication requests are derived from platform verification")

    def payload(self):
        if (self._raw, self.verification, self.unit, self.blob, self.manifest,
                self.attestation, self.taint, self.context, self.evidence) != self._identity:
            raise PublicationViolation("publication request no longer matches its factory identity")
        value = decode_canonical(self._raw)
        _require_verification(self.verification)
        if (value["verification"] != self.verification.to_dict() or value["unit"] != self.unit.to_dict()
                or value["manifest"] != self.manifest.to_dict(unit=self.unit, blob=self.blob) or value["attestation"] != self.attestation.to_dict()
                or value["taint"] != self.taint.to_dict() or value["lifecycle_context"] != self.context.to_dict()):
            raise PublicationViolation("publication request changed after verification")
        return value

    @property
    def transaction_id(self):
        value = self.payload()
        return "pub-" + reference(value["identity"]).sha256


@dataclass(frozen=True, init=False)
class PublicationAuthorityDecision:
    _raw: bytes
    _seal: object
    prepared_write: LA.PreparedLibraryWrite | None
    request: PublicationRequest | None
    verification: VerificationRecord | SourceVerification
    _identity: tuple[object, ...]

    def __new__(cls, *args, **kwargs):
        raise TypeError("publication decisions are evaluator-only")

    def payload(self):
        if type(self) is not PublicationAuthorityDecision or self._seal is not _SEAL:
            raise PublicationViolation("publication authority is not evaluator-produced")
        if (self._raw, self.prepared_write, self.request, self.verification) != self._identity:
            raise PublicationViolation("publication decision changed after independent evaluation")
        _require_verification(self.verification)
        value = decode_canonical(self._raw)
        if self.request is None:
            expected = _refusal(self.verification.payload())
            if (self.prepared_write is not None or expected is None
                    or value["verification"] != self.verification.to_dict()
                    or (value["decision_kind"], value["reason_codes"]) != (expected[0].value, [expected[1]])
                    or value["authorized_write_set"] != []):
                raise PublicationViolation("refusal differs from its independently verified policy inputs")
            return value
        self.request.payload()
        if (value["ingestion"] != decode_canonical(self.prepared_write.ingestion.canonical_bytes())
                or value["publication"] != decode_canonical(self.prepared_write.publication.canonical_bytes())):
            raise PublicationViolation("publication gates differ from the authorized records")
        return value

    @property
    def reference(self):
        value = self.payload()
        return reference(value, value["schema_version"])


@dataclass(frozen=True)
class PublicationAuthority:
    stores: ReusableVerificationAuthority
    taint_store: T.TaintHistoryStore
    builder: BuilderRuntimeIdentity
    source_actors: tuple[ActorIdentity, ...]
    retain_checked_partial_patch: bool = False

    def validate(self):
        self.stores.validate()
        if type(self.retain_checked_partial_patch) is not bool:
            raise TypeError("partial patch retention must be explicitly configured")
        if type(self.taint_store) is not T.TaintHistoryStore or self.taint_store.mutation_fence is not self.stores.fence:
            raise PublicationViolation("publication taint history belongs to another coordinator")
        self.taint_store.require_handle(self.stores.authority_handle)
        self.builder.to_dict()
        if type(self.source_actors) is not tuple or any(type(item) is not ActorIdentity for item in self.source_actors):
            raise TypeError("publication sources must be configured actor identities")
        participants = {item.value for item in self.source_actors} | {
            EXTRACTOR.value, POSITIVE_EXTRACTOR.value, self.stores.library._publisher_identity.component_id,
            self.builder.builder_actor_identity.value,
            self.stores.authority_handle.configuration.platform_attester_actor.value,
        }
        if EVALUATOR.value in participants:
            raise PublicationViolation("publication evaluator must be independent of every source and executor")

    def prepare(self, *, verification, manifest, context, c1) -> PublicationRequest | PublicationAuthorityDecision:
        """Derive a complete candidate or an explicit independent refusal."""
        self.validate()
        facts = require_verification_record(verification).payload()
        if ((c1 is None and facts["c1"] is not None)
                or c1 is not None and (type(c1) is not C1VerificationEvidence or facts["c1"] != c1.payload())
                or facts["manifest_sha256"] != manifest.manifest_sha256
                or facts["context_sha256"] != context.context_sha256):
            raise PublicationViolation("publication verification names different execution evidence")
        positive = (facts["task_contract_ref"]["schema_id"] == TASK_CONTRACT_SCHEMA_V3
                    and evaluate_attempt_outcome(verification).status is FinalStatus.FULL)
        if not positive and _refusal(facts) is not None:
            return self._decline(verification=verification, manifest=manifest)
        task_ref = HashBoundRef.from_dict(facts["task_contract_ref"])
        extractor = POSITIVE_EXTRACTOR if positive else EXTRACTOR
        domain_reader = verified_patch_domain if positive else rejected_patch_domain
        domain, domain_ref = domain_reader(manifest=manifest, task_contract_ref=task_ref, c1=c1)
        if positive:
            unit = create_verified_patch_guard(manifest=manifest, task_contract_ref=task_ref, c1=c1,
                                               retain_patch=True)
        else:
            guard_profile = REJECTED_PATCH_GUARD_V3
            if task_ref.schema_id == TASK_CONTRACT_SCHEMA_V3:
                guard_profile = REJECTED_PATCH_GUARD_V5 if self.retain_checked_partial_patch else REJECTED_PATCH_GUARD_V4
            unit = create_rejected_patch_guard(manifest=manifest, task_contract_ref=task_ref, c1=c1,
                profile_version=guard_profile)
        blob = create_behavior_blob(unit)
        behavior_manifest = create_behavior_manifest(unit, blob, compiler_binding=compile_behavior_unit(unit))
        subject = LA.write_subject_ref(content_key=unit.content_key, manifest_id=behavior_manifest.manifest_id)
        use_context = read_reusable_use_context(authority=self.stores, manifest=manifest, context=context,
                                                task_contract_ref=task_ref)
        use_record = inspect_reusable_use_context(use_context, base_revision=manifest.config.base_revision,
                                                  task_contract_ref=task_ref.to_dict())
        revision = RepositoryRevision.git_commit(manifest.config.base_revision)
        builder = replace(self.builder, repository_revision=revision)
        clock = lambda: datetime.now(timezone.utc)
        attester = configure_platform_attester(authority_handle=self.stores.authority_handle,
                                               builder_runtime_identity=builder, trusted_clock=clock)
        report_ref = replace(HashBoundRef.from_dict(facts["c1"]["report_ref"]), kind=RefKind.SOURCE_EVIDENCE)
        oracle_ref = replace(HashBoundRef.from_dict(facts["c1"]["oracle_result_ref"]), kind=RefKind.SOURCE_EVIDENCE)
        environment = {"environment_profile_id": self.stores.environment_profile_id, "environment_kind": manifest.config.environment_kind}
        use_ref = replace(HashBoundRef.from_dict(use_context["ref"]), kind=RefKind.SOURCE_EVIDENCE)
        observed = attester.observe(authority_handle=self.stores.authority_handle,
            repository_revision=revision, base_revision=RepositoryRevision.git_commit(manifest.config.base_revision),
            task_contract_ref=task_ref,
            **{name: tuple(ObservedExternalInput.from_dict(item) for item in use_record[name])
               for name in ("policy_inputs", "environment_inputs", "tool_inputs")},
            source_refs=(report_ref, oracle_ref, use_ref), verification_refs=(report_ref,),
            oracle_observation=OracleObservation.from_dict(use_record["oracle_observation"]))
        attestation = attester.attest(authority_handle=self.stores.authority_handle, observed=observed,
            subject_content_key=unit.content_key, producer_run_id=manifest.run_id, producer_attempt_id=context.attempt_id,
            producer_actor_ids=(extractor,))
        taint = T.classify_source_taint(authority_handle=self.stores.authority_handle, subject_ref=behavior_attestation_to_ref(attestation),
            taint_classes=(T.TaintClass.ORACLE_DERIVED, T.TaintClass.TRUSTED_PLATFORM_DERIVED),
            producer_actor_ids=(extractor,), source_actor_ids=(ActorIdentity(manifest.config.oracle_name),),
            admission_actor_ids=(ActorIdentity(EVALUATOR.value),), consumer_actor_ids=())
        lifecycle_context = LifecycleContext(LIFECYCLE_CONTEXT_V1, LifecycleScope.REVISION, domain_ref.sha256)
        payload = {"schema_version": REQUEST_SCHEMA_V4 if positive else REQUEST_SCHEMA_V3,
            "run_policy_version": manifest.versions.policy_version,
            "source_actor_ids": [{"value": actor} for actor in sorted({item.value for item in self.source_actors} | {
                extractor.value,
                builder.builder_actor_identity.value, self.stores.authority_handle.configuration.platform_attester_actor.value,
                manifest.config.oracle_name, use_record["oracle_observation"]["oracle_identity"]["value"],
                use_record["consumer_actor"]["value"], self.stores.library._publisher_identity.component_id})],
            "identity": {"manifest_sha256": manifest.manifest_sha256, "context_sha256": context.context_sha256,
                         "policy": PUBLICATION_POLICY_V3 if positive else PUBLICATION_POLICY_V2,
                         "verification_ref": verification.reference.to_dict()},
            "verification": verification.to_dict(), "outcome": evaluate_attempt_outcome(verification).to_dict(),
            "unit": unit.to_dict(), "manifest": behavior_manifest.to_dict(unit=unit, blob=blob),
            "attestation": attestation.to_dict(), "attestation_ref": behavior_attestation_to_ref(attestation).to_dict(),
            "taint": taint.to_dict(), "domain": domain,
            "lifecycle_context": lifecycle_context.to_dict(), "use_context": use_context,
            "environment_input": environment, "builder_input": builder.to_dict()}
        evidence = (*c1.retained_artifacts(), (HashBoundRef.from_dict(use_context["ref"]), encode_canonical(use_record)))
        payload["evidence_refs"] = [ref.to_dict() for ref, raw in evidence]
        result = object.__new__(PublicationRequest)
        for name, value in dict(_raw=encode_canonical(payload), verification=verification, unit=unit, blob=blob,
                                manifest=behavior_manifest, attestation=attestation, taint=taint, context=lifecycle_context, evidence=evidence).items():
            object.__setattr__(result, name, value)
        object.__setattr__(result, "_identity", (result._raw, verification, unit, blob, behavior_manifest,
                                               attestation, taint, lifecycle_context, evidence))
        result.payload()
        return result

    def prepare_source(self, verification: SourceVerification) -> PublicationRequest:
        """Bind a real source operation to the same publication authority/writer."""
        from ..provenance import ORACLE_OBSERVATION_V1
        from ..replay import replay_machine_execution_context
        from ..replay_vm_adapter import certify_literal_return_transitions
        self.validate()
        if type(verification) is not SourceVerification:
            raise TypeError("source publication needs actual sealed verification")
        facts = verification.payload()
        claim = facts["claim"]
        stores = self.stores
        revision = RepositoryRevision.git_commit(claim["revision"])
        builder = replace(self.builder, repository_revision=revision)
        run_id, attempt_id = RunId(facts["operation_id"]), AttemptId(facts["verification_attempt_id"])
        machine_context = replay_machine_execution_context(run_id=run_id, attempt_id=AttemptId("literal-certificate"),
            repository_revision=revision, environment_profile_id=stores.environment_profile_id,
            policy_version=SOURCE_POLICY_V1)
        provisional = _build_source_behavior(facts, ())
        transitions = certify_literal_return_transitions(compile_behavior_unit(provisional).program,
            gas_budget=claim["replay_gas_budget"], execution_context=machine_context)
        unit = _build_source_behavior(facts, transitions)
        blob = create_behavior_blob(unit)
        manifest = create_behavior_manifest(unit, blob, compiler_binding=compile_behavior_unit(unit))
        attester = configure_platform_attester(authority_handle=stores.authority_handle,
            builder_runtime_identity=builder, trusted_clock=lambda: datetime.now(timezone.utc))
        proof = replace(verification.reference, kind=RefKind.SOURCE_EVIDENCE)
        claim_ref = HashBoundRef.from_dict(facts["claim_ref"])
        observed = attester.observe(authority_handle=stores.authority_handle, repository_revision=revision,
            base_revision=revision, task_contract_ref=claim_ref,
            **{name: tuple(ObservedExternalInput.from_dict(item) for item in claim[name])
               for name in ("policy_inputs", "environment_inputs", "tool_inputs")},
            source_refs=tuple(HashBoundRef.from_dict(item["ref"]) for item in facts["sources"]),
            verification_refs=(proof,), oracle_observation=OracleObservation(ORACLE_OBSERVATION_V1,
                SOURCE_VERIFIER, revision, claim_ref, proof))
        attestation = attester.attest(authority_handle=stores.authority_handle, observed=observed,
            subject_content_key=unit.content_key, producer_run_id=run_id, producer_attempt_id=attempt_id,
            producer_actor_ids=(SOURCE_EXTRACTOR,))
        taint = T.classify_source_taint(authority_handle=stores.authority_handle,
            subject_ref=behavior_attestation_to_ref(attestation),
            taint_classes=(T.TaintClass.REPOSITORY_CONTENT, T.TaintClass.TRUSTED_PLATFORM_DERIVED,), producer_actor_ids=(SOURCE_EXTRACTOR,),
            source_actor_ids=(SOURCE_VERIFIER,), admission_actor_ids=(ActorIdentity(EVALUATOR.value),), consumer_actor_ids=())
        context = LifecycleContext(LIFECYCLE_CONTEXT_V1, LifecycleScope.REVISION, reference(claim).sha256)
        actors = sorted({actor.value for actor in self.source_actors} | {SOURCE_EXTRACTOR.value, SOURCE_VERIFIER.value,
            builder.builder_actor_identity.value, stores.authority_handle.configuration.platform_attester_actor.value,
            stores.library._publisher_identity.component_id})
        evidence = dict(verification.evidence)
        evidence[verification.reference] = encode_canonical(facts)
        evidence[proof] = encode_canonical(facts)
        payload = {"schema_version": SOURCE_REQUEST_V1, "run_policy_version": SOURCE_POLICY_V1,
            "source_actor_ids": [ActorIdentity(actor).to_dict() for actor in actors],
            "identity": {"origin": SOURCE_CLAIM_V1, "operation_id": run_id.value,
                "verification_ref": verification.reference.to_dict(), "policy": PUBLICATION_POLICY_V2},
            "verification": verification.to_dict(), "outcome": None, "use_context": None,
            "domain": claim, "unit": unit.to_dict(), "manifest": manifest.to_dict(unit=unit, blob=blob),
            "source_transitions": list(transitions), "attestation": attestation.to_dict(),
            "attestation_ref": behavior_attestation_to_ref(attestation).to_dict(), "taint": taint.to_dict(),
            "lifecycle_context": context.to_dict(), "builder_input": builder.to_dict(),
            "environment_input": {"environment_profile_id": stores.environment_profile_id},
            "evidence_refs": [ref.to_dict() for ref in evidence]}
        result = object.__new__(PublicationRequest)
        retained = tuple(evidence.items())
        for name, item in dict(_raw=encode_canonical(payload), verification=verification, unit=unit, blob=blob,
            manifest=manifest, attestation=attestation, taint=taint, context=context, evidence=retained).items():
            object.__setattr__(result, name, item)
        object.__setattr__(result, "_identity", (result._raw, verification, unit, blob, manifest,
            attestation, taint, context, retained))
        result.payload()
        return result

    def _decline(self, *, verification, manifest):
        kind, reason = _refusal(verification.payload())
        declaration = create_gate_evaluator_declaration(authority_handle=self.stores.authority_handle,
            evaluator_identity=EVALUATOR, evaluator_component_id="synapse.gold.publication-evaluator",
            evaluator_component_version=PUBLICATION_POLICY_V2, policy_version=manifest.versions.policy_version,
            gate_roles={GateKind.INGESTION: AuthorityRole.INGESTION_GATE_EVALUATOR,
                        GateKind.PUBLICATION: AuthorityRole.PUBLICATION_GATE_EVALUATOR},
            trusted_clock=lambda: datetime.now(timezone.utc))
        actors = tuple(ActorIdentity(name) for name in sorted({actor.value for actor in self.source_actors} | {
            EXTRACTOR.value, self.builder.builder_actor_identity.value, manifest.config.oracle_name,
            self.stores.authority_handle.configuration.platform_attester_actor.value,
            self.stores.library._publisher_identity.component_id}))
        proof = A.derive_independence_proof(declaration, actors)
        payload = {"schema_version": REFUSAL_SCHEMA_V1, "policy_version": PUBLICATION_POLICY_V2,
            "decision_kind": kind.value, "reason_codes": [reason], "authorized_write_set": [],
            "verification": verification.to_dict(), "authority_identity": EVALUATOR.to_dict(),
            "authority_declaration": declaration.to_dict(), "independence_proof": proof.to_dict(),
            "source_actor_ids": [actor.to_dict() for actor in actors]}
        result = object.__new__(PublicationAuthorityDecision)
        raw = encode_canonical(payload)
        for name, item in dict(_raw=raw, _seal=_SEAL, prepared_write=None, request=None,
                verification=verification, _identity=(raw, None, None, verification)).items():
            object.__setattr__(result, name, item)
        result.payload()
        return result

    def inspect_refusal(self, value, *, manifest, context):
        """Read a retained refusal against the configured project and exact run."""
        self.validate()
        fields = {"schema_version", "policy_version", "decision_kind", "reason_codes", "authorized_write_set",
            "verification", "authority_identity", "authority_declaration", "independence_proof", "source_actor_ids"}
        if type(value) is not dict or set(value) != fields:
            raise PublicationViolation("publication refusal has an unknown contract")
        facts = inspect_verification_record(value["verification"])
        expected = _refusal(facts)
        if (value["schema_version"] != REFUSAL_SCHEMA_V1 or value["policy_version"] != PUBLICATION_POLICY_V2
                or value["authority_identity"] != EVALUATOR.to_dict() or expected is None
                or (value["decision_kind"], value["reason_codes"]) != (expected[0].value, [expected[1]])
                or value["authorized_write_set"] != [] or facts["manifest_sha256"] != manifest.manifest_sha256
                or facts["context_sha256"] != context.context_sha256 or facts["publication"] is not None
                or facts["reusable_candidates"]):
            raise PublicationViolation("publication refusal contradicts its verified attempt")
        declaration, proof = value["authority_declaration"], value["independence_proof"]
        actors = sorted({actor.value for actor in self.source_actors} | {EXTRACTOR.value,
            self.builder.builder_actor_identity.value, manifest.config.oracle_name,
            self.stores.authority_handle.configuration.platform_attester_actor.value,
            self.stores.library._publisher_identity.component_id})
        for record, field in ((declaration, "declaration_id"), (proof, "proof_id")):
            validate_record_id(record_id_reference_from_dict(record[field]),
                canonical_bytes=encode_canonical({key: item for key, item in record.items() if key != field}))
        if (value["source_actor_ids"] != [{"value": actor} for actor in actors]
                or EVALUATOR.value in actors or proof["source_actor_ids"] != actors
                or declaration["evaluator_identity"] != EVALUATOR.to_dict()
                or proof["evaluator_identity"] != EVALUATOR.to_dict()
                or declaration["policy_version"] != manifest.versions.policy_version
                or declaration["configuration_id"] != self.stores.authority_handle.configuration_id.to_dict()
                or proof["configuration_id"] != declaration["configuration_id"]
                or proof["declaration_id"] != declaration["declaration_id"]
                or proof["independence_reason"] != "EVALUATOR_DISJOINT_FROM_SUBJECT_AND_CONFIGURED_ACTORS"):
            raise PublicationViolation("publication refusal lost its configured independent authority")
        return facts

    def evaluate(self, request: PublicationRequest, *, mutation_ticket) -> PublicationAuthorityDecision:
        """Evaluate prepared physical proof; mint exact scope before admission/index."""
        self.validate()
        value = request.payload()
        stores = self.stores
        subject = LA.write_subject_ref(content_key=request.unit.content_key, manifest_id=request.manifest.manifest_id)
        attestation_ref = behavior_attestation_to_ref(request.attestation)
        lifecycle = stores.lifecycle_store.current_state(subject_ref=attestation_ref, context=request.context,
                                                         mutation_ticket=mutation_ticket)
        if lifecycle is not LifecycleState.ATTESTED:
            raise PublicationViolation("publication lifecycle has not reached its exact attested boundary")
        validate_behavior_attestation(request.attestation,
            expected_subject_content_key=request.unit.content_key,
            expected_builder_runtime_identity=replace(self.builder, repository_revision=request.attestation.repository_revision),
            expected_attester_identity=stores.authority_handle.configuration.platform_attester_actor)
        effective = T.require_taint_consumable(authority_handle=stores.authority_handle,
            root_basis=request.taint, decisions=(), history_store=self.taint_store)
        finding = consumption_finding_from_effective_taint(effective)
        clock = lambda: datetime.now(timezone.utc)
        declaration = create_gate_evaluator_declaration(authority_handle=stores.authority_handle,
            evaluator_identity=EVALUATOR, evaluator_component_id="synapse.gold.publication-evaluator",
            evaluator_component_version=PUBLICATION_POLICY_V3 if value["schema_version"] == REQUEST_SCHEMA_V4 else PUBLICATION_POLICY_V2,
            policy_version=value["run_policy_version"],
            gate_roles={GateKind.INGESTION: AuthorityRole.INGESTION_GATE_EVALUATOR,
                        GateKind.PUBLICATION: AuthorityRole.PUBLICATION_GATE_EVALUATOR}, trusted_clock=clock)
        policy_version = declaration.policy_version
        granted = A.GrantEnvelope((request.context.context_id,), (), (), policy_version)
        def exact(ref):
            if ref != subject:
                raise PublicationViolation("publication probe names an unverified subject")
            return True
        controller = A.configure_gate_controller(declaration=declaration, policy_version=policy_version,
            run_id=request.attestation.producer_run_id, attempt_id=request.attestation.producer_attempt_id,
            repository_revision=request.attestation.repository_revision.git_sha,
            environment_profile_id=stores.environment_profile_id, trusted_clock=clock,
            taint_probe=lambda ref: finding if exact(ref) else None,
            provenance_probe=lambda ref: exact(ref) and stores.attestation_store.contains(
                authority_handle=stores.authority_handle, attestation=request.attestation, mutation_ticket=mutation_ticket),
            lifecycle_probe=lambda ref: exact(ref) and stores.lifecycle_store.current_state(
                subject_ref=attestation_ref, context=request.context, mutation_ticket=mutation_ticket) is LifecycleState.ATTESTED,
            grant_probe=lambda: granted, producer_actor=SOURCE_EXTRACTOR if _source_request(value) else
                POSITIVE_EXTRACTOR if value["schema_version"] == REQUEST_SCHEMA_V4 else EXTRACTOR)
        authority = LA.create_production_write_authority_binding(controller, library=stores.library,
            publisher_identity=stores.library._publisher_identity, journal=stores.admission_journal, fence=stores.fence,
            source_actors=tuple(ActorIdentity.from_dict(item) for item in value["source_actor_ids"]))
        prepared = LA.prepare_library_write(authority, unit=request.unit, blob=request.blob, manifest=request.manifest,
                                             requested=A.RequestedEnvelope(granted.scopes, (), ()))
        payload = {"schema_version": DECISION_SCHEMA_V3, "decision_kind": PublicationDecisionKind.AUTHORIZE_PUBLICATION.value,
            "request_ref": request_reference(value).to_dict(), "transaction_id": request.transaction_id,
            "authority_identity": EVALUATOR.to_dict(),
            "source_actor_ids": [item.to_dict() for item in authority.controller._source_actors],
            "authority_declaration": authority.controller.declaration.to_dict(),
            "independence_proof": authority.controller.independence_proof.to_dict(),
            "publisher_identity": stores.library._publisher_identity.to_dict(), "policy_version": policy_version,
            "verification_ref": request.verification.reference.to_dict(), "subject_ref": subject.to_dict(),
            "attestation_ref": attestation_ref.to_dict(), "taint": request.taint.to_dict(),
            "lifecycle_context": request.context.to_dict(), "required_transition": ["ATTESTED", "ADMITTED", "INDEXED"],
            "grant": {"scopes": list(granted.scopes), "capabilities": [], "oracles": []},
            "ingestion": decode_canonical(prepared.ingestion.canonical_bytes()),
            "publication": decode_canonical(prepared.publication.canonical_bytes()),
            "compatibility": _compatibility_contract(value),
            **_scope_contract(value), "sequence": mutation_ticket.interval_epoch,
            "reason_codes": _publication_reasons(value)}
        payload["transaction_contract_sha256"] = reference(_transaction_contract(payload)).sha256
        decision = object.__new__(PublicationAuthorityDecision)
        raw = encode_canonical(payload)
        for name, item in dict(_raw=raw, _seal=_SEAL, prepared_write=prepared,
                              request=request, verification=request.verification,
                              _identity=(raw, prepared, request, request.verification)).items():
            object.__setattr__(decision, name, item)
        decision.payload()
        return decision
