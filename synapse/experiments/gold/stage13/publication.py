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
from ..behavior import create_behavior_blob, create_behavior_manifest, compile_behavior_unit, behavior_unit_from_dict
from ..canonicalization import HashBoundRef, RefKind
from ..contracts import (ActorIdentity, AuthorityIdentity, AuthorityRole, GateKind, RepositoryRevision,
                         record_id_reference_from_dict, validate_record_id)
from ..gate_findings import consumption_finding_from_effective_taint
from ..lifecycle import LifecycleContext, LifecycleScope, LifecycleState, LIFECYCLE_CONTEXT_V1
from ..provenance import (
    BuilderRuntimeIdentity, ExternalInputKind, ObservedExternalInput, OBSERVED_EXTERNAL_INPUT_V1,
    OracleObservation, ORACLE_OBSERVATION_V1, configure_platform_attester,
    behavior_attestation_to_ref, validate_behavior_attestation,
)
from ..runner.c1_boundary import C1VerificationEvidence
from ..stage10.context_codec import encode_canonical, decode_canonical
from ..stage12.reusable import ReusableVerificationAuthority, rejected_patch_domain, create_rejected_patch_guard
from ..stage12.verification import require_verification_record, inspect_verification_record


PUBLICATION_POLICY_V1 = "stage13-atomic-publication/v1"
REQUEST_SCHEMA_V1 = "synapse.stage4.gold.publication-request/v1"
DECISION_SCHEMA_V1 = "synapse.stage4.gold.publication-authority-decision/v1"
EXTRACTOR = ActorIdentity("synapse.gold.rejected-patch-extractor")
EVALUATOR = AuthorityIdentity("synapse.gold.publication-authority")
_SEAL = object()


class PublicationDecisionKind(str, Enum):
    AUTHORIZE_PUBLICATION = "AUTHORIZE_PUBLICATION"
    REJECT_PUBLICATION = "REJECT_PUBLICATION"
    QUARANTINE = "QUARANTINE"
    REQUIRE_HUMAN_REVIEW = "REQUIRE_HUMAN_REVIEW"


class PublicationViolation(ValueError):
    """Publication evidence or exact transaction authority is inconsistent."""


def reference(payload, schema=REQUEST_SCHEMA_V1):
    raw = encode_canonical(payload)
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(RefKind.ARTIFACT, digest, schema, digest, len(raw), "application/json")


def _transaction_contract(value):
    return {name: value[name] for name in ("subject_ref", "attestation_ref", "taint", "lifecycle_context",
        "required_transition", "grant", "compatibility", "request_ref")}


def inspect_publication_decision(value, *, request, registration):
    """Validate retained authority against its anchored request and gate records.

    This is an integrity reader, never a constructor of executable authority.
    The write path still requires the live evaluator-produced decision.
    """
    facts = inspect_verification_record(request["verification"])
    unit = behavior_unit_from_dict(request["unit"])
    blob = create_behavior_blob(unit)
    manifest = create_behavior_manifest(unit, blob, compiler_binding=compile_behavior_unit(unit))
    subject = LA.write_subject_ref(content_key=unit.content_key, manifest_id=manifest.manifest_id)
    context = LifecycleContext.from_dict(request["lifecycle_context"])
    domain_ref = reference(request["domain"])
    if (request["schema_version"] != REQUEST_SCHEMA_V1 or request["manifest"] != manifest.to_dict(unit=unit, blob=blob)
            or context.scope is not LifecycleScope.REVISION or context.context_id != domain_ref.sha256
            or value["schema_version"] != DECISION_SCHEMA_V1 or value["authority_identity"] != EVALUATOR.to_dict()
            or value["decision_kind"] != PublicationDecisionKind.AUTHORIZE_PUBLICATION.value
            or value["subject_ref"] != subject.to_dict() or value["request_ref"] != reference(request).to_dict()
            or value["transaction_id"] != "pub-" + reference(request["identity"]).sha256
            or value["verification_ref"] != request["verification"]["verification_ref"]
            or value["lifecycle_context"] != context.to_dict() or value["taint"] != request["taint"]
            or value["policy_version"] != request["run_policy_version"]
            or value["required_transition"] != ["ATTESTED", "ADMITTED", "INDEXED"]
            or value["grant"] != {"scopes": [context.context_id], "capabilities": [], "oracles": []}
            or value["compatibility"] != {"profile": "exact-rejected-patch-domain/v1", "domain": request["domain"]}
            or value["transaction_contract_sha256"] != reference(_transaction_contract(value)).sha256):
        raise PublicationViolation("retained decision differs from its exact verified publication contract")
    attestation_ref = HashBoundRef.from_dict(value["attestation_ref"])
    attestation_bytes = encode_canonical(request["attestation"])
    if attestation_ref.sha256 != hashlib.sha256(attestation_bytes).hexdigest() or attestation_ref.byte_length != len(attestation_bytes):
        raise PublicationViolation("publication attestation reference differs from its retained bytes")
    actors = tuple(ActorIdentity.from_dict(item) for item in value["source_actor_ids"])
    actor_values = sorted(item.value for item in actors)
    required_actors = {EXTRACTOR.value, value["publisher_identity"]["component_id"],
        request["attestation"]["attester_identity"]["value"],
        request["builder_input"]["builder_actor_identity"]["value"], request["domain"]["oracle_identity"]}
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
                or gate.envelope.run_id.value != facts["run_id"] or gate.envelope.attempt_id.value != facts["attempt_id"]):
            raise PublicationViolation("retained gate differs from its authority or execution context")
        A.require_dimension_evidence(gate)
        gates.append(gate)
    A.require_publication_grant(gates[1], granted=A.GrantEnvelope((context.context_id,), (), (), value["policy_version"]))
    if gates[1].predecessor_decision_digest != gates[0].gate_decision_id.digest_sha256:
        raise PublicationViolation("publication lost its ingestion predecessor")


@dataclass(frozen=True, init=False)
class PublicationRequest:
    _raw: bytes
    _identity: tuple
    evidence: tuple
    verification: object
    unit: object
    blob: object
    manifest: object
    attestation: object
    taint: object
    context: LifecycleContext

    def __new__(cls, *args, **kwargs):
        raise TypeError("publication requests are derived from platform verification")

    def payload(self):
        if (self._raw, self.verification, self.unit, self.blob, self.manifest,
                self.attestation, self.taint, self.context, self.evidence) != self._identity:
            raise PublicationViolation("publication request no longer matches its factory identity")
        value = decode_canonical(self._raw)
        require_verification_record(self.verification)
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
    prepared_write: LA.PreparedLibraryWrite
    request: PublicationRequest
    _identity: tuple

    def __new__(cls, *args, **kwargs):
        raise TypeError("publication decisions are evaluator-only")

    def payload(self):
        if type(self) is not PublicationAuthorityDecision or self._seal is not _SEAL:
            raise PublicationViolation("publication authority is not evaluator-produced")
        if (self._raw, self.prepared_write, self.request) != self._identity:
            raise PublicationViolation("publication decision changed after independent evaluation")
        self.request.payload()
        value = decode_canonical(self._raw)
        if (value["ingestion"] != decode_canonical(self.prepared_write.ingestion.canonical_bytes())
                or value["publication"] != decode_canonical(self.prepared_write.publication.canonical_bytes())):
            raise PublicationViolation("publication gates differ from the authorized records")
        return value

    @property
    def reference(self):
        return reference(self.payload(), DECISION_SCHEMA_V1)


@dataclass(frozen=True)
class PublicationAuthority:
    stores: ReusableVerificationAuthority
    taint_store: T.TaintHistoryStore
    builder: BuilderRuntimeIdentity
    source_actors: tuple[ActorIdentity, ...]

    def validate(self):
        self.stores.validate()
        if type(self.taint_store) is not T.TaintHistoryStore or self.taint_store.mutation_fence is not self.stores.fence:
            raise PublicationViolation("publication taint history belongs to another coordinator")
        self.taint_store.require_handle(self.stores.authority_handle)
        self.builder.to_dict()
        if type(self.source_actors) is not tuple or any(type(item) is not ActorIdentity for item in self.source_actors):
            raise TypeError("publication sources must be configured actor identities")
        participants = {item.value for item in self.source_actors} | {
            EXTRACTOR.value, self.stores.library._publisher_identity.component_id,
            self.builder.builder_actor_identity.value,
            self.stores.authority_handle.configuration.platform_attester_actor.value,
        }
        if EVALUATOR.value in participants:
            raise PublicationViolation("publication evaluator must be independent of every source and executor")

    def prepare(self, *, verification, manifest, context, c1):
        """Derive a complete candidate; unsupported or unsuccessful proof yields none."""
        self.validate()
        facts = require_verification_record(verification).payload()
        if (type(c1) is not C1VerificationEvidence or facts["c1"] != c1.payload()
                or facts["manifest_sha256"] != manifest.manifest_sha256
                or facts["context_sha256"] != context.context_sha256):
            raise PublicationViolation("publication verification names different execution evidence")
        if (facts["failure_codes"] or facts["interrupted"] or facts["refused"] or facts["plan"] is None
                or facts["c1"]["oracle_resolved"] is not False or facts["c1"]["infra_error"]
                or facts["c1"]["no_candidate"] or facts["c1"]["refused"]):
            return None
        task_ref = HashBoundRef.from_dict(facts["task_contract_ref"])
        domain, domain_ref = rejected_patch_domain(manifest=manifest, task_contract_ref=task_ref, c1=c1)
        unit = create_rejected_patch_guard(manifest=manifest, task_contract_ref=task_ref, c1=c1)
        blob = create_behavior_blob(unit)
        behavior_manifest = create_behavior_manifest(unit, blob, compiler_binding=compile_behavior_unit(unit))
        subject = LA.write_subject_ref(content_key=unit.content_key, manifest_id=behavior_manifest.manifest_id)
        revision = RepositoryRevision.git_commit(facts["c1"]["verified_revision"])
        builder = replace(self.builder, repository_revision=revision)
        clock = lambda: datetime.now(timezone.utc)
        attester = configure_platform_attester(authority_handle=self.stores.authority_handle,
                                               builder_runtime_identity=builder, trusted_clock=clock)
        report_ref = replace(HashBoundRef.from_dict(facts["c1"]["report_ref"]), kind=RefKind.SOURCE_EVIDENCE)
        oracle_ref = replace(HashBoundRef.from_dict(facts["c1"]["oracle_result_ref"]), kind=RefKind.SOURCE_EVIDENCE)
        policy = {"policy": PUBLICATION_POLICY_V1, "domain": domain, "verification_ref": verification.reference.to_dict()}
        environment = {"environment_profile_id": self.stores.environment_profile_id, "environment_kind": manifest.config.environment_kind}
        external = lambda kind, name, payload: ObservedExternalInput(
            OBSERVED_EXTERNAL_INPUT_V1, kind, name, PUBLICATION_POLICY_V1,
            replace(reference(payload), kind=RefKind.CONTRACT_CONDITION) if kind is ExternalInputKind.POLICY else reference(payload))
        observed = attester.observe(authority_handle=self.stores.authority_handle,
            repository_revision=revision, base_revision=RepositoryRevision.git_commit(manifest.config.base_revision),
            task_contract_ref=task_ref,
            policy_inputs=(external(ExternalInputKind.POLICY, "publication-policy", policy),),
            environment_inputs=(external(ExternalInputKind.ENVIRONMENT, "publication-environment", environment),),
            tool_inputs=(external(ExternalInputKind.TOOL, "publication-builder", builder.to_dict()),),
            source_refs=(report_ref,), verification_refs=(report_ref,),
            oracle_observation=OracleObservation(ORACLE_OBSERVATION_V1, ActorIdentity(manifest.config.oracle_name), revision, task_ref, oracle_ref))
        attestation = attester.attest(authority_handle=self.stores.authority_handle, observed=observed,
            subject_content_key=unit.content_key, producer_run_id=manifest.run_id, producer_attempt_id=context.attempt_id,
            producer_actor_ids=(EXTRACTOR,))
        taint = T.classify_source_taint(authority_handle=self.stores.authority_handle, subject_ref=subject,
            taint_classes=(T.TaintClass.ORACLE_DERIVED, T.TaintClass.TRUSTED_PLATFORM_DERIVED),
            producer_actor_ids=(EXTRACTOR,), source_actor_ids=(ActorIdentity(manifest.config.oracle_name),),
            admission_actor_ids=(ActorIdentity(EVALUATOR.value),), consumer_actor_ids=())
        lifecycle_context = LifecycleContext(LIFECYCLE_CONTEXT_V1, LifecycleScope.REVISION, domain_ref.sha256)
        payload = {"schema_version": REQUEST_SCHEMA_V1, "run_policy_version": manifest.versions.policy_version,
            "source_actor_ids": [{"value": actor} for actor in sorted({item.value for item in self.source_actors} | {
                EXTRACTOR.value,
                builder.builder_actor_identity.value, self.stores.authority_handle.configuration.platform_attester_actor.value,
                manifest.config.oracle_name, self.stores.library._publisher_identity.component_id})],
            "identity": {"manifest_sha256": manifest.manifest_sha256, "context_sha256": context.context_sha256,
                         "policy": PUBLICATION_POLICY_V1, "verification_ref": verification.reference.to_dict()},
            "verification": verification.to_dict(), "unit": unit.to_dict(), "manifest": behavior_manifest.to_dict(unit=unit, blob=blob),
            "attestation": attestation.to_dict(), "taint": taint.to_dict(), "domain": domain,
            "lifecycle_context": lifecycle_context.to_dict(), "policy_input": policy,
            "environment_input": environment, "builder_input": builder.to_dict()}
        evidence = c1.retained_artifacts()
        payload["evidence_refs"] = [ref.to_dict() for ref, raw in evidence]
        result = object.__new__(PublicationRequest)
        for name, value in dict(_raw=encode_canonical(payload), verification=verification, unit=unit, blob=blob,
                                manifest=behavior_manifest, attestation=attestation, taint=taint, context=lifecycle_context, evidence=evidence).items():
            object.__setattr__(result, name, value)
        object.__setattr__(result, "_identity", (result._raw, verification, unit, blob, behavior_manifest,
                                               attestation, taint, lifecycle_context, evidence))
        result.payload()
        return result

    def evaluate(self, request, *, mutation_ticket):
        """Evaluate prepared physical proof; mint exact scope before admission/index."""
        self.validate()
        value = request.payload()
        stores = self.stores
        subject = LA.write_subject_ref(content_key=request.unit.content_key, manifest_id=request.manifest.manifest_id)
        attestation_ref = behavior_attestation_to_ref(request.attestation)
        lifecycle = stores.lifecycle_store.current_state(subject_ref=attestation_ref, context=request.context)
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
            evaluator_component_version=PUBLICATION_POLICY_V1, policy_version=value["run_policy_version"],
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
                subject_ref=attestation_ref, context=request.context) is LifecycleState.ATTESTED,
            grant_probe=lambda: granted, producer_actor=EXTRACTOR)
        authority = LA.create_production_write_authority_binding(controller, library=stores.library,
            publisher_identity=stores.library._publisher_identity, journal=stores.admission_journal, fence=stores.fence,
            source_actors=(*self.source_actors, self.builder.builder_actor_identity,
                           stores.authority_handle.configuration.platform_attester_actor,
                           request.attestation.oracle_observation.oracle_identity))
        prepared = LA.prepare_library_write(authority, unit=request.unit, blob=request.blob, manifest=request.manifest,
                                             requested=A.RequestedEnvelope(granted.scopes, (), ()))
        payload = {"schema_version": DECISION_SCHEMA_V1, "decision_kind": PublicationDecisionKind.AUTHORIZE_PUBLICATION.value,
            "request_ref": reference(value).to_dict(), "transaction_id": request.transaction_id,
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
            "compatibility": {"profile": "exact-rejected-patch-domain/v1", "domain": value["domain"]},
            "reason_codes": ["INDEPENDENT_NEGATIVE_PROOF", "EXACT_PURE_GUARD", "SCOPED_ADMISSION"]}
        payload["transaction_contract_sha256"] = reference(_transaction_contract(payload)).sha256
        decision = object.__new__(PublicationAuthorityDecision)
        raw = encode_canonical(payload)
        for name, item in dict(_raw=raw, _seal=_SEAL, prepared_write=prepared,
                              request=request, _identity=(raw, prepared, request)).items():
            object.__setattr__(decision, name, item)
        decision.payload()
        return decision
