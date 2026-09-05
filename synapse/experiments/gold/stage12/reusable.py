"""Independent verification and durable admission of reusable attempt outputs.

The first supported domain is an exact rejected-patch guard. Its program emits
the fingerprint of a hypothesis a retained C1 oracle actually rejected. This
is negative knowledge for duplicate detection, never permission to apply a
patch or a prediction about a different repository/task. The verifier derives
the entire behavior, including its contract, from that independent evidence.

Registration consumes an existing Library write and its committed gates. It
does not publish, grant admission, run an oracle, or manufacture useful reuse.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from pathlib import Path

from .. import admission as A
from ..behavior import (
    AbsenceDetail, AbsenceDetailKind, AbsencePolicy, BehaviorKind, ConditionRef,
    ContractField, DefaultKind, DefaultValue, InlineProgram, InputContract,
    OutputContract, ReplayContract, ReplayResultClass, ValueType,
    VerificationContract, VerificationResultClass, behavior_unit_from_dict,
    compile_behavior_unit, create_behavior_unit,
)
from ..canonicalization import HashBoundRef, RefKind
from ..contracts import GateKind, RepositoryRevision, record_id_reference_from_dict
from ..admission_journal import FileAdmissionJournal, FileSnapshotFence
from ..library import BehaviorLibrary
from ..lifecycle import LifecycleStore
from ..contracts import Stage4AuthorityHandle, require_stage4_authority_handle
from ..library_admission import validate_write_admission_evidence, write_subject_ref
from ..lifecycle import LifecycleContext, LifecycleScope, LIFECYCLE_CONTEXT_V1
from ..provenance import (
    BehaviorAttestation, BehaviorAttestationStore, BuilderRuntimeIdentity, behavior_attestation_to_ref,
    require_behavior_attestation_consumable,
)
from ..runner.c1_boundary import C1VerificationEvidence
from ..runner.records import RecordKind
from ..stage10.context_codec import decode_canonical, encode_canonical


REUSABLE_CANDIDATE_SCHEMA_V1 = "synapse.stage4.gold.reusable-candidate/v1"
REJECTED_PATCH_DOMAIN_V1 = "synapse.stage4.gold.rejected-patch-domain/v1"
REJECTED_PATCH_GUARD_V1 = "synapse.stage4.gold.rejected-patch-guard/v1"


@dataclass(frozen=True)
class ReusableVerificationAuthority:
    """Physical evidence owners bound by composition, with one read fence."""

    repository_root: Path
    environment_profile_id: str
    authority_handle: Stage4AuthorityHandle
    library: BehaviorLibrary
    attestation_store: BehaviorAttestationStore
    lifecycle_store: LifecycleStore
    admission_journal: FileAdmissionJournal
    fence: FileSnapshotFence

    def __post_init__(self):
        self.validate()

    def validate(self):
        if (type(self) is not ReusableVerificationAuthority or type(self.repository_root) is not type(Path())
                or not self.repository_root.is_absolute() or type(self.environment_profile_id) is not str
                or not self.environment_profile_id or type(self.library) is not BehaviorLibrary
                or type(self.attestation_store) is not BehaviorAttestationStore
                or type(self.lifecycle_store) is not LifecycleStore
                or type(self.admission_journal) is not FileAdmissionJournal or type(self.fence) is not FileSnapshotFence):
            raise TypeError("reusable verification requires exact evidence owners")
        require_stage4_authority_handle(self.authority_handle)
        self.attestation_store.require_handle(self.authority_handle)
        self.lifecycle_store.require_handle(self.authority_handle)
        if any(owner.mutation_fence is not self.fence for owner in (
                self.library, self.attestation_store, self.lifecycle_store, self.admission_journal)):
            raise ValueError("reusable evidence owners must share one authority fence")


def rejected_patch_domain(*, manifest, task_contract_ref, c1: C1VerificationEvidence):
    """Derive the entire future-use domain from the existing C1 reader."""
    if type(c1) is not C1VerificationEvidence:
        raise TypeError("a reusable guard needs boundary-sealed C1 evidence")
    facts = c1.payload()
    if (facts["oracle_resolved"] is not False or facts["infra_error"] or facts["refused"]
            or facts["no_candidate"] or not facts["commands_complete"]
            or any(facts[key] is None for key in ("evidence_ref", "report_ref", "oracle_result_ref",
                                                 "verified_patch_sha256", "verified_revision"))):
        raise ValueError("a rejected-patch guard requires a coherent negative oracle and complete C1 proof")
    domain = {
        "schema_version": REJECTED_PATCH_DOMAIN_V1,
        "base_revision": manifest.config.base_revision,
        "task_contract_ref": task_contract_ref.to_dict(),
        "command_policy_ref": facts["command_policy_ref"],
        "patch_sha256": facts["verified_patch_sha256"],
        "oracle_identity": manifest.config.oracle_name,
        "environment_kind": manifest.config.environment_kind,
        "policy_sha256": manifest.versions.policy_sha256,
    }
    raw = encode_canonical(domain)
    digest = hashlib.sha256(raw).hexdigest()
    return domain, HashBoundRef(RefKind.CONTRACT_CONDITION, digest, REJECTED_PATCH_DOMAIN_V1,
                               digest, len(raw), "application/json")


def create_rejected_patch_guard(*, manifest, task_contract_ref, c1: C1VerificationEvidence):
    """Construct the exact verified negative fact; this grants no admission."""
    _, domain_ref = rejected_patch_domain(manifest=manifest, task_contract_ref=task_contract_ref, c1=c1)
    facts = c1.payload()
    report = replace(HashBoundRef.from_dict(facts["report_ref"]), kind=RefKind.SOURCE_EVIDENCE)
    oracle = HashBoundRef.from_dict(facts["oracle_result_ref"])
    condition = ConditionRef(domain_ref.ref_id, domain_ref.schema_id, domain_ref.sha256,
                             domain_ref.byte_length, domain_ref.media_type)
    program = InlineProgram.from_dict({"form": "INLINE_IR_V1", "ir": {
        "schema_version": "synapse.stage4.gold.canonical-program-ir/v1",
        "program": {"node": "program", "statements": [{"node": "return", "value": {
            "node": "list", "elements": [
                {"node": "literal", "value_kind": "INT", "value": byte}
                for byte in bytes.fromhex(domain_ref.sha256)
            ],
        }}]},
    }})
    field = ContractField("rejected_domain_key", ValueType.LIST, AbsencePolicy.REQUIRED,
                          DefaultValue(DefaultKind.ABSENT), AbsenceDetail(AbsenceDetailKind.NONE))
    return create_behavior_unit(
        behavior_kind=BehaviorKind.REJECTED_HYPOTHESIS_GUARD, canonical_program=program,
        input_contract=InputContract((), (condition,)), output_contract=OutputContract((field,), (condition,)),
        capability_requirements=(), binding_refs=(), source_evidence_refs=(report,), artifact_refs=(oracle,),
        replay_contract=ReplayContract(REJECTED_PATCH_GUARD_V1, (), (), (), (ReplayResultClass.MATCH,)),
        verification_contract=VerificationContract(
            REJECTED_PATCH_GUARD_V1, VerificationResultClass.BEHAVIOR_REJECTED,
            ("exact-patch-did-not-resolve-task",), (report,), (oracle,),
        ),
    )


def verify_reusable_candidate(value, *, authority, manifest, context, task_contract_ref, c1):
    """Reopen bytes, provenance, lifecycle and committed independent admission."""
    if type(authority) is not ReusableVerificationAuthority:
        raise TypeError("reusable verification needs bound platform stores")
    authority.validate()
    fields = {"schema_version", "manifest_sha256", "context_sha256", "unit", "manifest_id",
              "attestation", "lifecycle_context", "ingestion", "publication", "journal_anchor", "journal_sequence", "domain"}
    if type(value) is not dict or set(value) != fields or value["schema_version"] != REUSABLE_CANDIDATE_SCHEMA_V1:
        raise ValueError("reusable candidate has an unknown contract")
    if value["manifest_sha256"] != manifest.manifest_sha256 or value["context_sha256"] != context.context_sha256:
        raise ValueError("reusable candidate belongs to another attempt")
    domain, domain_ref = rejected_patch_domain(manifest=manifest, task_contract_ref=task_contract_ref, c1=c1)
    if value["domain"] != domain:
        raise ValueError("reusable candidate widens its verified domain")
    expected = create_rejected_patch_guard(manifest=manifest, task_contract_ref=task_contract_ref, c1=c1)
    declared = behavior_unit_from_dict(value["unit"])
    if declared.to_dict() != expected.to_dict():
        raise ValueError("reusable behavior differs from the independently verified guard")
    with authority.fence.exclusive():
        if authority.fence.current_epoch() % 2:
            raise ValueError("reusable admission has an unsettled authority interval")
        loaded = authority.library.get_verified_behavior(declared.content_key, record_id_reference_from_dict(value["manifest_id"]))
        if (loaded.unit.to_dict() != expected.to_dict()
                or loaded.manifest.compiler_binding != compile_behavior_unit(expected)
                or loaded.manifest.binding_refs):
            raise ValueError("reusable executable or its manifest differs from verified bytes")
        raw = value["attestation"]
        facts = c1.payload()
        revision = RepositoryRevision.git_commit(facts["verified_revision"])
        attestation = BehaviorAttestation.from_dict(
            raw, authority_handle=authority.authority_handle, expected_subject_content_key=declared.content_key,
            expected_builder_runtime_identity=BuilderRuntimeIdentity.from_dict(raw["builder_runtime_identity"]),
            expected_attester_identity=authority.authority_handle.configuration.platform_attester_actor,
            expected_repository_revision=revision,
        )
        report_ref = replace(HashBoundRef.from_dict(facts["report_ref"]), kind=RefKind.SOURCE_EVIDENCE)
        oracle_ref = replace(HashBoundRef.from_dict(facts["oracle_result_ref"]), kind=RefKind.SOURCE_EVIDENCE)
        if (attestation.producer_run_id != manifest.run_id or attestation.producer_attempt_id != context.attempt_id
                or attestation.task_contract_ref != task_contract_ref
                or attestation.base_revision != RepositoryRevision.git_commit(manifest.config.base_revision)
                or report_ref not in attestation.verification_refs
                or report_ref not in attestation.source_refs
                or attestation.oracle_observation.result_ref != oracle_ref
                or attestation.oracle_observation.task_contract_ref != task_contract_ref
                or attestation.oracle_observation.verified_repository_revision != revision):
            raise ValueError("reusable provenance does not describe this attempt's independently verified output")
        lifecycle_context = LifecycleContext(LIFECYCLE_CONTEXT_V1, LifecycleScope.REVISION, domain_ref.sha256)
        if value["lifecycle_context"] != lifecycle_context.to_dict():
            raise ValueError("reusable admission names a different future-use domain")
        require_behavior_attestation_consumable(
            attestation=attestation, expected_subject_content_key=declared.content_key,
            authority_handle=authority.authority_handle, attestation_store=authority.attestation_store,
            lifecycle_store=authority.lifecycle_store, lifecycle_context=lifecycle_context,
        )
        subject = write_subject_ref(content_key=loaded.unit.content_key, manifest_id=loaded.manifest.manifest_id)
        decisions = []
        for field_name, gate in (("ingestion", GateKind.INGESTION), ("publication", GateKind.PUBLICATION)):
            item = value[field_name]
            if type(item) is not dict or set(item) != {"ref", "record"}:
                raise ValueError("reusable admission lacks an exact gate record")
            ref = HashBoundRef.from_dict(item["ref"])
            decision = A.gate_decision_from_dict(item["record"], expected_ref=ref)
            envelope = decision.envelope
            if (decision.gate_kind is not gate or not decision.admitted or decision.subject_refs != (subject,)
                    or decision.configuration_digest != authority.authority_handle.configuration_id.digest_sha256
                    or decision.policy_version != manifest.versions.policy_version
                    or envelope is None or envelope.run_id != manifest.run_id or envelope.attempt_id != context.attempt_id
                    or envelope.repository_revision != revision
                    or envelope.environment_profile_id != authority.environment_profile_id
                    or decision.authority_identity.value in {actor.value for actor in attestation.producer_actor_ids}
                    or not authority.admission_journal.contains_record_at(value["journal_anchor"], value["journal_sequence"], ref.sha256)):
                raise ValueError("reusable admission is missing, foreign, stale or self-approved")
            A.require_dimension_evidence(decision)
            decisions.append(decision)
        A.require_gate_predecessor(decisions[0], expected_gate=GateKind.INGESTION, subject_refs=(subject,))
        A.require_publication_grant(decisions[1], granted=A.GrantEnvelope(
            (domain_ref.sha256,), (), (), manifest.versions.policy_version,
        ))
        if decisions[1].predecessor_decision_digest != decisions[0].gate_decision_id.digest_sha256:
            raise ValueError("publication admission has another ingestion predecessor")
        if authority.admission_journal.record_position(A.gate_decision_ref(decisions[0]).sha256) >= authority.admission_journal.record_position(A.gate_decision_ref(decisions[1]).sha256):
            raise ValueError("reusable admission decisions were not committed in causal order")
        return {
            "behavior_ref": subject.to_dict(), "verification_ref": facts["report_ref"],
            "oracle_result_ref": facts["oracle_result_ref"], "domain_ref": domain_ref.to_dict(),
            "domain": domain, "attestation_ref": behavior_attestation_to_ref(attestation).to_dict(),
            "admission_ref": A.gate_decision_ref(decisions[1]).to_dict(),
        }


def register_reusable_candidate(*, session, authority, manifest, context, task_contract_ref,
                                c1, unit, behavior_manifest, attestation, write_evidence):
    """Attach an actual admitted output before the immutable attempt result."""
    from ..runner.run_recovery import PendingRunRecord
    from ..runner.run_progress import load_attempt_progress, AttemptProgressPhase, require_progress_payload
    from ..runner.c1_boundary import restore_c1_authority_receipt

    write = validate_write_admission_evidence(write_evidence)
    if session.store.get(kind=RecordKind.ATTEMPT_RESULT, key=str(context.attempt_index)) is not None:
        raise ValueError("a completed attempt cannot acquire retrospective reusable output")
    stored = session.store.get(kind=RecordKind.ATTEMPT_CONTEXT, key=str(context.attempt_index))
    if stored is None or stored.payload != context.stored_dict():
        raise ValueError("reusable output requires its actual durable attempt context")
    progress = load_attempt_progress(session.store, manifest=manifest, context=context).latest
    if progress is None or progress.phase is not AttemptProgressPhase.C1_COMPLETED:
        raise ValueError("reusable registration requires durable C1 completion")
    raw, ref = require_progress_payload(progress)
    receipt = restore_c1_authority_receipt(raw, expected_ref=ref)
    if c1.payload()["c1_result_ref"] != receipt.c1_result_ref.to_dict():
        raise ValueError("reusable verification comes from another C1 attempt")
    if (write.result.content_key != unit.content_key or write.result.manifest_id != behavior_manifest.manifest_id):
        raise ValueError("write evidence belongs to another reusable output")
    for decision, receipt in zip((write.ingestion, write.publication), write.receipts):
        A.require_committed_decision(receipt, decision=decision, journal=authority.admission_journal)
    domain, domain_ref = rejected_patch_domain(manifest=manifest, task_contract_ref=task_contract_ref, c1=c1)
    value = {
        "schema_version": REUSABLE_CANDIDATE_SCHEMA_V1,
        "manifest_sha256": manifest.manifest_sha256, "context_sha256": context.context_sha256,
        "unit": unit.to_dict(), "manifest_id": behavior_manifest.manifest_id.to_dict(),
        "attestation": attestation.to_dict(), "domain": domain,
        "lifecycle_context": LifecycleContext(LIFECYCLE_CONTEXT_V1, LifecycleScope.REVISION, domain_ref.sha256).to_dict(),
        "journal_anchor": write.receipts[-1].journal_anchor,
        "journal_sequence": authority.admission_journal.record_position(A.gate_decision_ref(write.publication).sha256) + 1,
        **{name: {"ref": A.gate_decision_ref(decision).to_dict(), "record": decode_canonical(decision.canonical_bytes())}
           for name, decision in (("ingestion", write.ingestion), ("publication", write.publication))},
    }
    verify_reusable_candidate(value, authority=authority, manifest=manifest, context=context,
                              task_contract_ref=task_contract_ref, c1=c1)
    return session.put(PendingRunRecord(kind=RecordKind.REUSABLE_CANDIDATE,
                                       key=str(context.attempt_index), payload=value))


def inspect_reusable_projection(candidates, *, c1, task_contract_ref):
    """Check the closed reusable proof projection without restoring authority."""
    if len(candidates) > 1:
        raise ValueError("one attempt can establish only its exact rejected-patch guard")
    for item in candidates:
        fields = {"behavior_ref", "verification_ref", "oracle_result_ref", "domain_ref", "domain", "attestation_ref", "admission_ref"}
        if type(item) is not dict or set(item) != fields:
            raise ValueError("reusable proof has an unknown shape")
        for name, kind in (("behavior_ref", RefKind.ARTIFACT), ("verification_ref", RefKind.ARTIFACT),
                           ("oracle_result_ref", RefKind.ARTIFACT), ("attestation_ref", RefKind.SOURCE_EVIDENCE),
                           ("admission_ref", RefKind.GATE_DECISION), ("domain_ref", RefKind.CONTRACT_CONDITION)):
            if HashBoundRef.from_dict(item[name]).kind is not kind:
                raise ValueError("reusable proof reference kind is invalid")
        domain = item["domain"]
        domain_ref = HashBoundRef.from_dict(item["domain_ref"])
        raw_domain = encode_canonical(domain)
        if (type(domain) is not dict or set(domain) != {"schema_version", "base_revision", "task_contract_ref",
                "command_policy_ref", "patch_sha256", "oracle_identity", "environment_kind", "policy_sha256"}
                or domain.get("schema_version") != REJECTED_PATCH_DOMAIN_V1
                or domain_ref.schema_id != REJECTED_PATCH_DOMAIN_V1 or domain_ref.ref_id != domain_ref.sha256
                or domain_ref.sha256 != hashlib.sha256(raw_domain).hexdigest() or domain_ref.byte_length != len(raw_domain)
                or c1 is None or c1["oracle_resolved"] is not False or c1["infra_error"] or c1["refused"]
                or c1["no_candidate"] or not c1["commands_complete"] or c1["evidence_ref"] is None
                or item["verification_ref"] != c1["report_ref"] or item["verification_ref"] is None
                or item["oracle_result_ref"] != c1["oracle_result_ref"] or item["oracle_result_ref"] is None
                or domain["task_contract_ref"] != task_contract_ref
                or domain["command_policy_ref"] != c1["command_policy_ref"]
                or domain["patch_sha256"] != c1["verified_patch_sha256"]):
            raise ValueError("reusable claim contradicts its independent verification domain")
