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
from ..canonicalization import HashBoundRef, RefKind
from ..contracts import (ActorIdentity, AuthorityIdentity, AuthorityRole, GateKind, RepositoryRevision,
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
                                read_reusable_use_context, inspect_reusable_use_context)
from ..stage12.verification_contract import VerificationRecord, require_verification_record, inspect_verification_record
from ..stage12.outcome import evaluate_attempt_outcome, inspect_outcome


PUBLICATION_POLICY_V2 = "stage13-atomic-publication/v2"
REQUEST_SCHEMA_V3 = "synapse.stage4.gold.publication-request/v3"
DECISION_SCHEMA_V3 = "synapse.stage4.gold.publication-authority-decision/v3"
REFUSAL_SCHEMA_V1 = "synapse.stage4.gold.publication-refusal/v1"
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
        "retention_roots": request["evidence_refs"], "outcome_ref": request["outcome"]["outcome_ref"],
    }


def inspect_publication_decision(value, *, request, registration):
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
    facts = inspect_verification_record(request["verification"])
    outcome = inspect_outcome(request["outcome"])
    unit = behavior_unit_from_dict(request["unit"])
    blob = create_behavior_blob(unit)
    manifest = create_behavior_manifest(unit, blob, compiler_binding=compile_behavior_unit(unit))
    subject = LA.write_subject_ref(content_key=unit.content_key, manifest_id=manifest.manifest_id)
    context = LifecycleContext.from_dict(request["lifecycle_context"])
    domain_ref = reference(request["domain"])
    use_record = inspect_reusable_use_context(request["use_context"], base_revision=request["domain"]["base_revision"],
                                              task_contract_ref=request["domain"]["task_contract_ref"])
    attestation = request["attestation"]
    if (any(attestation[name] != use_record[name] for name in
            ("repository_revision", "task_contract_ref", "policy_inputs", "environment_inputs", "tool_inputs", "oracle_observation"))
            or replace(HashBoundRef.from_dict(request["use_context"]["ref"]), kind=RefKind.SOURCE_EVIDENCE).to_dict()
               not in attestation["source_refs"]):
        raise PublicationViolation("publication provenance differs from its admitted future-use context")
    if (outcome["verification"] != request["verification"] or facts["reusable_candidates"] or facts["publication"] is not None
            or request["schema_version"] != REQUEST_SCHEMA_V3 or request["manifest"] != manifest.to_dict(unit=unit, blob=blob)
            or context.scope is not LifecycleScope.REVISION or context.context_id != domain_ref.sha256
            or value["schema_version"] != DECISION_SCHEMA_V3 or value["authority_identity"] != EVALUATOR.to_dict()
            or value["decision_kind"] != PublicationDecisionKind.AUTHORIZE_PUBLICATION.value
            or value["subject_ref"] != subject.to_dict() or value["request_ref"] != reference(request).to_dict()
            or value["transaction_id"] != "pub-" + reference(request["identity"]).sha256
            or value["verification_ref"] != request["verification"]["verification_ref"]
            or value["lifecycle_context"] != context.to_dict() or value["taint"] != request["taint"]
            or value["attestation_ref"] != request["attestation_ref"]
            or value["policy_version"] != request["run_policy_version"]
            or value["required_transition"] != ["ATTESTED", "ADMITTED", "INDEXED"]
            or value["grant"] != {"scopes": [context.context_id], "capabilities": [], "oracles": []}
            or value["compatibility"] != {"profile": "exact-admitted-use-context/v1", "domain": request["domain"],
                                          "context_ref": request["use_context"]["ref"]}
            or value["reason_codes"] != ["INDEPENDENT_NEGATIVE_PROOF", "EXACT_PURE_GUARD", "SCOPED_ADMISSION"]
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
    required_actors = {EXTRACTOR.value, value["publisher_identity"]["component_id"],
        request["attestation"]["attester_identity"]["value"],
        request["builder_input"]["builder_actor_identity"]["value"], request["domain"]["oracle_identity"],
        use_record["oracle_observation"]["oracle_identity"]["value"], use_record["consumer_actor"]["value"]}
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
    _identity: tuple[object, ...]
    evidence: tuple[tuple[HashBoundRef, bytes], ...]
    verification: VerificationRecord
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
    prepared_write: LA.PreparedLibraryWrite | None
    request: PublicationRequest | None
    verification: VerificationRecord
    _identity: tuple[object, ...]

    def __new__(cls, *args, **kwargs):
        raise TypeError("publication decisions are evaluator-only")

    def payload(self):
        if type(self) is not PublicationAuthorityDecision or self._seal is not _SEAL:
            raise PublicationViolation("publication authority is not evaluator-produced")
        if (self._raw, self.prepared_write, self.request, self.verification) != self._identity:
            raise PublicationViolation("publication decision changed after independent evaluation")
        require_verification_record(self.verification)
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

    def prepare(self, *, verification, manifest, context, c1) -> PublicationRequest | PublicationAuthorityDecision:
        """Derive a complete candidate or an explicit independent refusal."""
        self.validate()
        facts = require_verification_record(verification).payload()
        if ((c1 is None and facts["c1"] is not None)
                or c1 is not None and (type(c1) is not C1VerificationEvidence or facts["c1"] != c1.payload())
                or facts["manifest_sha256"] != manifest.manifest_sha256
                or facts["context_sha256"] != context.context_sha256):
            raise PublicationViolation("publication verification names different execution evidence")
        if _refusal(facts) is not None:
            return self._decline(verification=verification, manifest=manifest)
        task_ref = HashBoundRef.from_dict(facts["task_contract_ref"])
        domain, domain_ref = rejected_patch_domain(manifest=manifest, task_contract_ref=task_ref, c1=c1)
        unit = create_rejected_patch_guard(manifest=manifest, task_contract_ref=task_ref, c1=c1)
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
            producer_actor_ids=(EXTRACTOR,))
        taint = T.classify_source_taint(authority_handle=self.stores.authority_handle, subject_ref=behavior_attestation_to_ref(attestation),
            taint_classes=(T.TaintClass.ORACLE_DERIVED, T.TaintClass.TRUSTED_PLATFORM_DERIVED),
            producer_actor_ids=(EXTRACTOR,), source_actor_ids=(ActorIdentity(manifest.config.oracle_name),),
            admission_actor_ids=(ActorIdentity(EVALUATOR.value),), consumer_actor_ids=())
        lifecycle_context = LifecycleContext(LIFECYCLE_CONTEXT_V1, LifecycleScope.REVISION, domain_ref.sha256)
        payload = {"schema_version": REQUEST_SCHEMA_V3, "run_policy_version": manifest.versions.policy_version,
            "source_actor_ids": [{"value": actor} for actor in sorted({item.value for item in self.source_actors} | {
                EXTRACTOR.value,
                builder.builder_actor_identity.value, self.stores.authority_handle.configuration.platform_attester_actor.value,
                manifest.config.oracle_name, use_record["oracle_observation"]["oracle_identity"]["value"],
                use_record["consumer_actor"]["value"], self.stores.library._publisher_identity.component_id})],
            "identity": {"manifest_sha256": manifest.manifest_sha256, "context_sha256": context.context_sha256,
                         "policy": PUBLICATION_POLICY_V2, "verification_ref": verification.reference.to_dict()},
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
            evaluator_component_version=PUBLICATION_POLICY_V2, policy_version=value["run_policy_version"],
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
            grant_probe=lambda: granted, producer_actor=EXTRACTOR)
        authority = LA.create_production_write_authority_binding(controller, library=stores.library,
            publisher_identity=stores.library._publisher_identity, journal=stores.admission_journal, fence=stores.fence,
            source_actors=(*self.source_actors, self.builder.builder_actor_identity,
                           stores.authority_handle.configuration.platform_attester_actor,
                           request.attestation.oracle_observation.oracle_identity,
                           ActorIdentity(value["domain"]["oracle_identity"]),
                           ActorIdentity(value["use_context"]["record"]["consumer_actor"]["value"])))
        prepared = LA.prepare_library_write(authority, unit=request.unit, blob=request.blob, manifest=request.manifest,
                                             requested=A.RequestedEnvelope(granted.scopes, (), ()))
        payload = {"schema_version": DECISION_SCHEMA_V3, "decision_kind": PublicationDecisionKind.AUTHORIZE_PUBLICATION.value,
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
            "compatibility": {"profile": "exact-admitted-use-context/v1", "domain": value["domain"],
                              "context_ref": value["use_context"]["ref"]},
            **_scope_contract(value), "sequence": mutation_ticket.interval_epoch,
            "reason_codes": ["INDEPENDENT_NEGATIVE_PROOF", "EXACT_PURE_GUARD", "SCOPED_ADMISSION"]}
        payload["transaction_contract_sha256"] = reference(_transaction_contract(payload)).sha256
        decision = object.__new__(PublicationAuthorityDecision)
        raw = encode_canonical(payload)
        for name, item in dict(_raw=raw, _seal=_SEAL, prepared_write=prepared,
                              request=request, verification=request.verification,
                              _identity=(raw, prepared, request, request.verification)).items():
            object.__setattr__(decision, name, item)
        decision.payload()
        return decision
