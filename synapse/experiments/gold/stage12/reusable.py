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
    behavior_unit_from_dict, compile_behavior_unit,
)
from ..canonicalization import HashBoundRef, RefKind
from ..contracts import AttemptId, GateKind, RepositoryRevision, record_id_reference_from_dict, validate_record_id
from ..compatibility import COMPATIBILITY_CONTEXT_V1
from ..compatibility_store import FileCompatibilityStore
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
from ..runner.records import RecordKind, RunRecordStore
from ..runner.attempt_knowledge import basis_from_payload
from ..runner.attempt_knowledge_store import basis_record_key
from ..stage10.context_codec import decode_canonical, encode_canonical
from ..stage13.rejected_patch_profile import (REJECTED_PATCH_DOMAIN_V2, REJECTED_PATCH_GUARD_V3,
    REJECTED_PATCH_GUARD_V4, build_rejected_patch_guard, build_conditional_rejected_patch_guard,
    VERIFIED_PATCH_DOMAIN_V1, VERIFIED_PATCH_GUARD_V1, build_verified_patch_guard)
from ..replay import replay_machine_execution_context
from ..replay_vm_adapter import certify_literal_return_transitions, observe_typed_pure_invocation


REUSABLE_CANDIDATE_SCHEMA_V2 = "synapse.stage4.gold.reusable-candidate/v2"
REUSABLE_CANDIDATE_SCHEMA_V3 = "synapse.stage4.gold.reusable-candidate/v3"


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
    source_run_store: RunRecordStore | None
    compatibility_history: FileCompatibilityStore

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
        if (self.source_run_store is not None and type(self.source_run_store) is not RunRecordStore
                or type(self.compatibility_history) is not FileCompatibilityStore):
            raise TypeError("publication requires actual histories; a source origin has no Gold run")
        self.attestation_store.require_handle(self.authority_handle)
        self.lifecycle_store.require_handle(self.authority_handle)
        if any(owner.mutation_fence is not self.fence for owner in (
                self.library, self.attestation_store, self.lifecycle_store, self.admission_journal)):
            raise ValueError("reusable evidence owners must share one authority fence")


def read_reusable_use_context(*, authority, manifest, context, task_contract_ref):
    """Read the producer's admitted context, never invent a future-use environment.

    C1 verifies a patch on its resulting revision. The inert negative fact is
    useful at the original base. Its attestation therefore binds the actual
    pre-C1 context; the post-patch report and oracle remain source evidence.
    """
    authority.validate()
    if authority.source_run_store is None:
        raise ValueError("attempt reuse requires its actual Gold run history")
    stored = authority.source_run_store.get(kind=RecordKind.ATTEMPT_CONTEXT, key=str(context.attempt_index))
    basis_record = authority.source_run_store.get(kind=RecordKind.ATTEMPT_KNOWLEDGE_BASIS,
                                                   key=basis_record_key(context.attempt_index))
    if stored is None or stored.payload != context.stored_dict() or basis_record is None:
        raise ValueError("future use requires the actual persisted attempt and admission basis")
    basis = basis_from_payload(basis_record.payload)
    if (not basis.point_of_use_admitted or basis.digest() != context.phase_refs.knowledge_basis_sha256
            or basis.run_id != manifest.run_id.value or basis.attempt_id != context.attempt_id.value):
        raise ValueError("future use context differs from this attempt's completed admission")
    ref = basis.consumer_context_ref
    raw = authority.compatibility_history.resolve_ref(ref)
    record = decode_canonical(raw)
    inspect_reusable_use_context({"ref": ref.to_dict(), "record": record},
        base_revision=manifest.config.base_revision, task_contract_ref=task_contract_ref.to_dict())
    return {"ref": ref.to_dict(), "record": record}


def inspect_reusable_use_context(value, *, base_revision, task_contract_ref):
    """Check retained context identity; only the history reader supplies authority."""
    if type(value) is not dict or set(value) != {"ref", "record"}:
        raise ValueError("future-use context has an unknown contract")
    ref, record = HashBoundRef.from_dict(value["ref"]), value["record"]
    raw = encode_canonical(record)
    identity = record_id_reference_from_dict(record["context_id"])
    validate_record_id(identity, canonical_bytes=encode_canonical({key: item for key, item in record.items() if key != "context_id"}))
    if (ref.schema_id != COMPATIBILITY_CONTEXT_V1 or record["schema_version"] != COMPATIBILITY_CONTEXT_V1
            or ref.kind is not RefKind.ARTIFACT or ref.ref_id != identity.digest_sha256
            or ref.sha256 != hashlib.sha256(raw).hexdigest() or ref.byte_length != len(raw)
            or record["repository_revision"] != RepositoryRevision.git_commit(base_revision).to_dict()
            or record["task_contract_ref"] != task_contract_ref):
        raise ValueError("future-use context lost its exact identity, base or task")
    return record


def rejected_patch_domain(*, manifest, task_contract_ref, c1: C1VerificationEvidence):
    """Derive the entire future-use domain from the existing C1 reader."""
    return _patch_outcome_domain(manifest=manifest, task_contract_ref=task_contract_ref,
        c1=c1, expected_outcome=False, schema=REJECTED_PATCH_DOMAIN_V2)


def verified_patch_domain(*, manifest, task_contract_ref, c1: C1VerificationEvidence):
    """An exact positive C1 observation; publication separately requires FULL."""
    return _patch_outcome_domain(manifest=manifest, task_contract_ref=task_contract_ref,
        c1=c1, expected_outcome=True, schema=VERIFIED_PATCH_DOMAIN_V1)


def _patch_outcome_domain(*, manifest, task_contract_ref, c1, expected_outcome, schema):
    if type(c1) is not C1VerificationEvidence:
        raise TypeError("a patch observation needs boundary-sealed C1 evidence")
    facts = c1.payload()
    if (facts["oracle_resolved"] is not expected_outcome or facts["infra_error"] or facts["refused"]
            or facts["no_candidate"] or not facts["commands_complete"]
            or any(facts[key] is None for key in ("evidence_ref", "report_ref", "oracle_result_ref",
                                                 "verified_patch_sha256", "verified_revision"))):
        raise ValueError("a patch observation requires its exact independent outcome and complete C1 proof")
    domain = {
        "schema_version": schema,
        "base_revision": manifest.config.base_revision,
        "task_contract_ref": task_contract_ref.to_dict(),
        "command_policy_ref": facts["command_policy_ref"],
        "patch_sha256": facts["verified_patch_sha256"],
        "oracle_identity": manifest.config.oracle_name,
        "environment_kind": manifest.config.environment_kind,
        "policy_sha256": manifest.versions.policy_sha256,
        "replay_gas_budget": manifest.config.budgets.replay_gas_budget,
    }
    raw = encode_canonical(domain)
    digest = hashlib.sha256(raw).hexdigest()
    return domain, HashBoundRef(RefKind.CONTRACT_CONDITION, digest, schema,
                               digest, len(raw), "application/json")


def create_rejected_patch_guard(*, manifest, task_contract_ref, c1: C1VerificationEvidence,
                               profile_version=REJECTED_PATCH_GUARD_V3):
    """Construct the exact verified negative fact; this grants no admission."""
    domain, domain_ref = rejected_patch_domain(manifest=manifest, task_contract_ref=task_contract_ref, c1=c1)
    facts = c1.payload()
    report = replace(HashBoundRef.from_dict(facts["report_ref"]), kind=RefKind.SOURCE_EVIDENCE)
    oracle = HashBoundRef.from_dict(facts["oracle_result_ref"])
    if profile_version not in {REJECTED_PATCH_GUARD_V3, REJECTED_PATCH_GUARD_V4}:
        raise ValueError("unknown rejected-patch replay profile")
    arguments = dict(domain_ref=domain_ref, report=report, oracle=oracle)
    builder = build_rejected_patch_guard
    if profile_version == REJECTED_PATCH_GUARD_V4:
        builder = build_conditional_rejected_patch_guard
        arguments["domain"] = domain
    provisional = builder(**arguments)
    machine_context = replay_machine_execution_context(run_id=manifest.run_id,
        attempt_id=AttemptId("literal-certificate"),
        repository_revision=RepositoryRevision.git_commit(manifest.config.base_revision),
        environment_profile_id=manifest.config.environment_kind, policy_version=manifest.versions.policy_version)
    if profile_version == REJECTED_PATCH_GUARD_V4:
        transitions, _ = observe_typed_pure_invocation(provisional, inputs={},
            gas_budget=manifest.config.budgets.replay_gas_budget, step_limit=1_000, execution_context=machine_context)
    else:
        transitions = certify_literal_return_transitions(compile_behavior_unit(provisional).program,
            gas_budget=manifest.config.budgets.replay_gas_budget, execution_context=machine_context)
    return builder(**arguments, transitions=transitions)


def create_verified_patch_guard(*, manifest, task_contract_ref, c1: C1VerificationEvidence,
                                retain_patch=False):
    """Construct positive evidence from C1; no caller-supplied success flag."""
    domain, domain_ref = verified_patch_domain(manifest=manifest, task_contract_ref=task_contract_ref, c1=c1)
    facts = c1.payload()
    if type(retain_patch) is not bool:
        raise TypeError("patch retention must be explicit")
    arguments = dict(domain=domain, domain_ref=domain_ref,
        report=replace(HashBoundRef.from_dict(facts["report_ref"]), kind=RefKind.SOURCE_EVIDENCE),
        oracle=HashBoundRef.from_dict(facts["oracle_result_ref"]))
    if retain_patch:
        patches = [ref for ref, raw in c1.retained_artifacts()
                   if ref.schema_id == "synapse.stage4.gold.c1-patch-bytes/v1"
                   and ref.sha256 == domain["patch_sha256"]]
        if len(patches) != 1:
            raise ValueError("positive observation lacks its exact retained patch")
        arguments["patch_ref"] = patches[0]
    provisional = build_verified_patch_guard(**arguments)
    machine_context = replay_machine_execution_context(run_id=manifest.run_id,
        attempt_id=AttemptId("typed-certificate"),
        repository_revision=RepositoryRevision.git_commit(manifest.config.base_revision),
        environment_profile_id=manifest.config.environment_kind, policy_version=manifest.versions.policy_version)
    transitions, _ = observe_typed_pure_invocation(provisional, inputs={},
        gas_budget=manifest.config.budgets.replay_gas_budget, step_limit=1_000, execution_context=machine_context)
    return build_verified_patch_guard(**arguments, transitions=transitions)


def verify_reusable_candidate(value, *, authority, manifest, context, task_contract_ref, c1):
    """Reopen bytes, provenance, lifecycle and committed independent admission."""
    if type(authority) is not ReusableVerificationAuthority:
        raise TypeError("reusable verification needs bound platform stores")
    authority.validate()
    fields = {"schema_version", "manifest_sha256", "context_sha256", "unit", "manifest_id",
              "attestation", "lifecycle_context", "ingestion", "publication", "journal_anchor", "journal_sequence", "domain", "publication_transaction"}
    if (type(value) is not dict or set(value) != fields
            or value["schema_version"] not in {REUSABLE_CANDIDATE_SCHEMA_V2, REUSABLE_CANDIDATE_SCHEMA_V3}):
        raise ValueError("reusable candidate has an unknown contract")
    if value["manifest_sha256"] != manifest.manifest_sha256 or value["context_sha256"] != context.context_sha256:
        raise ValueError("reusable candidate belongs to another attempt")
    positive = value["schema_version"] == REUSABLE_CANDIDATE_SCHEMA_V3
    domain_reader = verified_patch_domain if positive else rejected_patch_domain
    domain, domain_ref = domain_reader(manifest=manifest, task_contract_ref=task_contract_ref, c1=c1)
    if value["domain"] != domain:
        raise ValueError("reusable candidate widens its verified domain")
    declared = behavior_unit_from_dict(value["unit"])
    if positive:
        if declared.core.verification_contract.profile_id != VERIFIED_PATCH_GUARD_V1 or value["publication_transaction"] is None:
            raise ValueError("positive evidence requires its supported profile and atomic publication")
        expected = create_verified_patch_guard(manifest=manifest, task_contract_ref=task_contract_ref, c1=c1,
            retain_patch=any(ref.schema_id == "synapse.stage4.gold.c1-patch-bytes/v1" for ref in declared.core.artifact_refs))
    else:
        expected = create_rejected_patch_guard(manifest=manifest, task_contract_ref=task_contract_ref, c1=c1,
            profile_version=declared.core.verification_contract.profile_id)
    if declared.to_dict() != expected.to_dict():
        raise ValueError("reusable behavior differs from the independently verified guard")
    use = read_reusable_use_context(authority=authority, manifest=manifest, context=context,
                                    task_contract_ref=task_contract_ref)
    use_record = inspect_reusable_use_context(use, base_revision=manifest.config.base_revision,
                                             task_contract_ref=task_contract_ref.to_dict())
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
        revision = RepositoryRevision.git_commit(manifest.config.base_revision)
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
                or oracle_ref not in attestation.source_refs
                or replace(HashBoundRef.from_dict(use["ref"]), kind=RefKind.SOURCE_EVIDENCE) not in attestation.source_refs
                or attestation.oracle_observation.to_dict() != use_record["oracle_observation"]
                or any([item.to_dict() for item in getattr(attestation, name)] != use_record[name]
                       for name in ("policy_inputs", "environment_inputs", "tool_inputs"))):
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
        publication_ref = None
        transaction = value["publication_transaction"]
        if transaction is not None:
            from ..persistence import read_committed_snapshot_transaction
            if (type(transaction) is not dict or set(transaction) != {"transaction_id", "decision_ref"}
                    or type(transaction["transaction_id"]) is not str):
                raise ValueError("reusable publication transaction is malformed")
            root = authority.library.root.parent / "publications" / "committed"
            marker, members = read_committed_snapshot_transaction(root, transaction_id=transaction["transaction_id"])
            raw = members["result.json"]
            publication = decode_canonical(raw)
            decision = decode_canonical(members["decision.json"])
            decision_ref = HashBoundRef.from_dict(transaction["decision_ref"])
            if (publication["registration"] != value or publication["decision_ref"] != transaction["decision_ref"]
                    or marker["boundary_id"] != decision_ref.sha256
                    or hashlib.sha256(members["decision.json"]).hexdigest() != decision_ref.sha256
                    or marker["marker_sha256"] != hashlib.sha256(raw).hexdigest()
                    or decision["subject_ref"] != subject.to_dict()
                    or decision["decision_kind"] != "AUTHORIZE_PUBLICATION"):
                raise ValueError("reusable output differs from its complete atomic publication")
            digest = hashlib.sha256(raw).hexdigest()
            publication_ref = HashBoundRef(RefKind.ARTIFACT, digest, publication["schema_version"], digest,
                                           len(raw), "application/json").to_dict()
        return {
            "publication_ref": publication_ref,
            "behavior_ref": subject.to_dict(), "verification_ref": facts["report_ref"],
            "oracle_result_ref": facts["oracle_result_ref"], "domain_ref": domain_ref.to_dict(),
            "domain": domain, "attestation_ref": behavior_attestation_to_ref(attestation).to_dict(),
            "admission_ref": A.gate_decision_ref(decisions[1]).to_dict(),
        }


def register_reusable_candidate(*, session, authority, manifest, context, task_contract_ref,
                                c1, unit, behavior_manifest, attestation, write_evidence):
    """Attach an actual admitted output before the immutable attempt result."""
    write = validate_write_admission_evidence(write_evidence)
    if (write.result.content_key != unit.content_key or write.result.manifest_id != behavior_manifest.manifest_id):
        raise ValueError("write evidence belongs to another reusable output")
    for decision, receipt in zip((write.ingestion, write.publication), write.receipts):
        A.require_committed_decision(receipt, decision=decision, journal=authority.admission_journal)
    domain, domain_ref = rejected_patch_domain(manifest=manifest, task_contract_ref=task_contract_ref, c1=c1)
    value = {
        "publication_transaction": None,
        "schema_version": REUSABLE_CANDIDATE_SCHEMA_V2,
        "manifest_sha256": manifest.manifest_sha256, "context_sha256": context.context_sha256,
        "unit": unit.to_dict(), "manifest_id": behavior_manifest.manifest_id.to_dict(),
        "attestation": attestation.to_dict(), "domain": domain,
        "lifecycle_context": LifecycleContext(LIFECYCLE_CONTEXT_V1, LifecycleScope.REVISION, domain_ref.sha256).to_dict(),
        "journal_anchor": write.receipts[-1].journal_anchor,
        "journal_sequence": authority.admission_journal.record_position(A.gate_decision_ref(write.publication).sha256) + 1,
        **{name: {"ref": A.gate_decision_ref(decision).to_dict(), "record": decode_canonical(decision.canonical_bytes())}
           for name, decision in (("ingestion", write.ingestion), ("publication", write.publication))},
    }
    return register_verified_reusable_output(session=session, authority=authority, manifest=manifest,
        context=context, task_contract_ref=task_contract_ref, c1=c1, registration=value)


def register_verified_reusable_output(*, session, authority, manifest, context, task_contract_ref, c1, registration):
    """The sole run-registration boundary for an independently read admitted output."""
    from ..runner.run_recovery import PendingRunRecord
    from ..runner.run_progress import load_attempt_progress, AttemptProgressPhase, require_progress_payload
    from ..runner.c1_boundary import restore_c1_authority_receipt

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
    verify_reusable_candidate(registration, authority=authority, manifest=manifest, context=context,
                              task_contract_ref=task_contract_ref, c1=c1)
    return session.put(PendingRunRecord(kind=RecordKind.REUSABLE_CANDIDATE,
                                       key=str(context.attempt_index), payload=registration))


def inspect_reusable_projection(candidates, *, c1, task_contract_ref):
    """Check the closed reusable proof projection without restoring authority."""
    if len(candidates) > 1:
        raise ValueError("one attempt can establish only its exact supported patch observation")
    for item in candidates:
        fields = {"behavior_ref", "verification_ref", "oracle_result_ref", "domain_ref", "domain", "attestation_ref", "admission_ref", "publication_ref"}
        if type(item) is not dict or set(item) != fields:
            raise ValueError("reusable proof has an unknown shape")
        for name, kind in (("behavior_ref", RefKind.ARTIFACT), ("verification_ref", RefKind.ARTIFACT),
                           ("oracle_result_ref", RefKind.ARTIFACT), ("attestation_ref", RefKind.SOURCE_EVIDENCE),
                           ("admission_ref", RefKind.GATE_DECISION), ("domain_ref", RefKind.CONTRACT_CONDITION)):
            if HashBoundRef.from_dict(item[name]).kind is not kind:
                raise ValueError("reusable proof reference kind is invalid")
        if item["publication_ref"] is not None:
            publication_ref = HashBoundRef.from_dict(item["publication_ref"])
            if publication_ref.kind is not RefKind.ARTIFACT or publication_ref.schema_id != "synapse.stage4.gold.publication-result/v3":
                raise ValueError("reusable publication reference has an unknown contract")
        domain = item["domain"]
        positive = type(domain) is dict and domain.get("schema_version") == VERIFIED_PATCH_DOMAIN_V1
        domain_schema = VERIFIED_PATCH_DOMAIN_V1 if positive else REJECTED_PATCH_DOMAIN_V2
        domain_ref = HashBoundRef.from_dict(item["domain_ref"])
        raw_domain = encode_canonical(domain)
        if (type(domain) is not dict or set(domain) != {"schema_version", "base_revision", "task_contract_ref",
                "command_policy_ref", "patch_sha256", "oracle_identity", "environment_kind", "policy_sha256", "replay_gas_budget"}
                or domain.get("schema_version") != domain_schema
                or domain_ref.schema_id != domain_schema or domain_ref.ref_id != domain_ref.sha256
                or domain_ref.sha256 != hashlib.sha256(raw_domain).hexdigest() or domain_ref.byte_length != len(raw_domain)
                or c1 is None or c1["oracle_resolved"] is not positive or c1["infra_error"] or c1["refused"]
                or positive and item["publication_ref"] is None
                or c1["no_candidate"] or not c1["commands_complete"] or c1["evidence_ref"] is None
                or item["verification_ref"] != c1["report_ref"] or item["verification_ref"] is None
                or item["oracle_result_ref"] != c1["oracle_result_ref"] or item["oracle_result_ref"] is None
                or domain["task_contract_ref"] != task_contract_ref
                or domain["command_policy_ref"] != c1["command_policy_ref"]
                or domain["patch_sha256"] != c1["verified_patch_sha256"]):
            raise ValueError("reusable claim contradicts its independent verification domain")
