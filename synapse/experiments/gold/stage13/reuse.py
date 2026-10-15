"""Observed use of an admitted exact negative guard before the C1 boundary.

This owner consumes the real replay return and compares the actual proposed
patch. Its durable record proves a declined dispatch, never a new oracle call
or task success. Promotion policy belongs to the independent publication owner.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import subprocess

from ..canonicalization import HashBoundRef
from ..behavior import behavior_unit_from_dict
from ..contracts import record_id_reference_from_dict
from ..library_admission import write_subject_ref
from ..lifecycle import LifecycleContext, LifecycleState
from ..persistence import read_regular_bytes, read_committed_snapshot_transaction, MAX_METADATA_BYTES_V1
from ..replay_store import FileReplayStore
from ..replay_vm_adapter import read_replayed_return_value
from ..runner.attempt_authority import require_completed_delivery_authority
from ..runner.attempt_knowledge import basis_from_payload
from ..runner.attempt_knowledge_store import basis_record_key
from ..runner.c1_boundary import command_policy_reference, read_c1_authority_receipt, matches_retained_oracle_configuration
from ..runner.vocabulary import GoldRunFailureCode, GoldRunViolation
from ..runner.completed_delivery_codec import completed_worker_delivery_ref
from ..runner.delivery import require_completed_worker_delivery
from ..runner.records import RecordKind
from ..stage10.context_codec import decode_canonical, encode_canonical, decode_worker_delivery_envelope
from ..stage10.record_store import Stage10RecordKind
from ..stage10.worker_transport import WorkerCandidateStatus
from .publication import PublicationViolation, reference, REQUEST_SCHEMA_V3, REQUEST_SCHEMA_V4
from .publication_store import PublicationResult, PUBLICATION_RESULT_V3
from .rejected_patch_profile import (
    fingerprint_words, REJECTED_PATCH_GUARD_V3, REJECTED_PATCH_GUARD_V4, REJECTED_PATCH_GUARD_V5)


MECHANISM_USE_SCHEMA_V1 = "synapse.stage4.gold.mechanism-use/v1"
REUSE_GUARD_POLICY_V1 = "synapse.stage4.gold.exact-rejected-candidate-use/v1"
OBSERVER = "synapse.gold.reuse-observer"
_SEAL = object()


@dataclass(frozen=True, init=False)
class MechanismUseRecord:
    _raw: bytes
    _seal: object
    _digest: str

    def __new__(cls, *args, **kwargs):
        raise TypeError("MechanismUseRecord requires observed platform consumption")

    def payload(self):
        if (type(self) is not MechanismUseRecord or getattr(self, "_seal", None) is not _SEAL
                or type(self._raw) is not bytes or hashlib.sha256(self._raw).hexdigest() != self._digest):
            raise PublicationViolation("mechanism observation is not platform-produced")
        return decode_canonical(self._raw)

    @property
    def reference(self):
        return reference(self.payload(), MECHANISM_USE_SCHEMA_V1)

    def canonical_bytes(self):
        self.payload()
        return self._raw


def _repository_base(repo, base):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE).decode().strip()
    head = git("rev-parse", "HEAD")
    if head != base or git("status", "--porcelain", "--untracked-files=all"):
        raise PublicationViolation("guard consumption requires the unchanged frozen repository base")
    return {"revision": head, "tree": git("rev-parse", "HEAD^{tree}")}


def _delivered_replay(*, publisher, stage10_store, run_store, run_root, manifest, context, completed):
    checked = require_completed_worker_delivery(completed)
    require_completed_delivery_authority(context=context, completed=checked)
    envelope = decode_worker_delivery_envelope(stage10_store.get(
        kind=Stage10RecordKind.WORKER_DELIVERY_ENVELOPE, ref=checked.delivery_envelope_ref).payload)
    if (envelope.envelope_sha256 != checked.invocation.envelope_sha256
            or envelope.prompt_text != checked.invocation.payload_text):
        raise PublicationViolation("reuse names another delivered worker context")
    body = decode_canonical(envelope.body_bytes)
    if (body["task_policy"]["task_contract_ref"] is None
            or body["task_policy"]["knowledge_snapshot_ref"] != context.phase_refs.knowledge_snapshot_ref.to_dict()):
        raise PublicationViolation("reuse context has a different knowledge basis")
    stored = run_store.get(kind=RecordKind.ATTEMPT_KNOWLEDGE_BASIS, key=basis_record_key(context.attempt_index))
    if stored is None:
        raise PublicationViolation("reuse lacks its durable point-of-use admission")
    basis = basis_from_payload(stored.payload)
    if not basis.point_of_use_admitted or basis.digest() != context.phase_refs.knowledge_basis_sha256:
        raise PublicationViolation("reuse lacks exact completed point-of-use admission")
    replay_store = FileReplayStore(run_root / "replay" / "records", mutation_fence=publisher.authority.stores.fence)
    replay = replay_store.require_result(checked.upstream.replay_ref)
    if replay.knowledge_snapshot_id != context.phase_refs.knowledge_snapshot_ref.ref_id:
        raise PublicationViolation("replayed knowledge belongs to another snapshot")
    delivered = {item["observation_id"]["digest_sha256"]: item for item in body["replay_observations"]}
    if len(delivered) != len(body["replay_observations"]) or set(delivered) != {item.observation_id.digest_sha256 for item in replay.observations}:
        raise PublicationViolation("delivered observations differ from the actual replay")
    for item in replay.observations:
        raw = delivered[item.observation_id.digest_sha256]
        if raw["terminal_snapshot_ref"] != item.terminal_snapshot_ref.to_dict() or raw["behavior_content_key"] != item.behavior_content_key:
            raise PublicationViolation("delivered replay changed its subject or retained output")
    return checked, body, basis, replay_store, replay


def _publication_for_observation(publisher, observation):
    stores = publisher.authority.stores
    entries = [item for item in stores.library.search_index() if item.content_key == observation.behavior_content_key]
    for entry in entries:
        path = stores.library.root / "metadata" / "commit-requirements" / (entry.manifest_ref.digest_sha256 + ".json")
        if not path.exists():
            continue
        pointer = decode_canonical(read_regular_bytes(path, maximum_bytes=MAX_METADATA_BYTES_V1))
        result = PublicationResult(publisher.root, pointer["transaction_id"])
        payload = result.payload()
        _, prepared = read_committed_snapshot_transaction(publisher.root / "prepared", transaction_id=result.transaction_id)
        request = decode_canonical(prepared["request.json"])
        if payload["registration"]["manifest_id"] != record_id_reference_from_dict(request["manifest"]["manifest_id"]).to_dict():
            raise PublicationViolation("reuse publication has a different manifest")
        yield result, payload, request, prepared


def _oracle_configuration_matches(publisher, boundary, request, prepared):
    """Reuse the independently retained C2 configuration, without invoking C2."""
    if request["environment_input"]["environment_profile_id"] != publisher.authority.stores.environment_profile_id:
        return False
    ref = request["verification"]["payload"]["c1"]["oracle_result_ref"]
    return matches_retained_oracle_configuration(boundary, prepared[ref["sha256"]])


def _execution_dimensions(manifest, profile, boundary):
    return {"base_revision": manifest.config.base_revision, "task_contract_ref": profile.task_contract.reference.to_dict(),
        "command_policy_ref": command_policy_reference(boundary.command_policy).to_dict(),
        "oracle_identity": manifest.config.oracle_name,
        "environment_kind": manifest.config.environment_kind, "policy_sha256": manifest.versions.policy_sha256,
        "replay_gas_budget": manifest.config.budgets.replay_gas_budget}


def _candidate_dimensions(manifest, profile, boundary, completed):
    patch = completed.worker_result.diff_text.encode("utf-8")
    return {**_execution_dimensions(manifest, profile, boundary), "patch_sha256": hashlib.sha256(patch).hexdigest()}


def _negative_publication(request):
    """Only independent negative evidence can suppress a C1 dispatch."""
    return (request["schema_version"] == REQUEST_SCHEMA_V3
            and request["verification"]["payload"]["c1"]["oracle_resolved"] is False
            and request["unit"]["core"]["verification_contract"]["profile_id"]
                in {REJECTED_PATCH_GUARD_V3, REJECTED_PATCH_GUARD_V4, REJECTED_PATCH_GUARD_V5})



def read_replayed_execution_feedback(*, publisher, profile, boundary, manifest,
                                    admitted_subject_refs, replay_store, replay_result, current=True):
    """Read task-bound verified outcomes from actual admitted replay output.

    The observation must belong to a committed publication with independent C1
    proof and the current oracle configuration. A failed recipe, prose claim,
    missing outcome or infrastructure error cannot enter this observation port.
    Historical verification reads the original admission instead of requiring
    that its lifecycle is still current today.
    """
    from ..replay import replay_result_ref
    from ..stage10.intent import ExecutionFeedback
    from .rejected_patch_profile import REJECTED_PATCH_GUARD_V3, REJECTED_PATCH_GUARD_V4, VERIFIED_PATCH_GUARD_V1

    publisher.authority.validate()
    if type(replay_store) is not FileReplayStore or type(current) is not bool:
        raise PublicationViolation("execution feedback requires its exact replay owner")
    if OBSERVER in {item.value for item in publisher.authority.source_actors}:
        raise PublicationViolation("feedback observer cannot be a configured producer or worker")
    replay = replay_store.require_result(replay_result_ref(replay_result))
    expected = _execution_dimensions(manifest, profile, boundary)
    feedback = {}
    with publisher.authority.stores.fence.exclusive():
        for observation in replay.observations:
            for result, publication, request, prepared in _publication_for_observation(publisher, observation):
                domain = request["domain"]
                if any(domain.get(key) != value for key, value in expected.items()):
                    continue
                if not _oracle_configuration_matches(publisher, boundary, request, prepared):
                    continue
                unit = behavior_unit_from_dict(request["unit"])
                declared_profile = unit.core.verification_contract.profile_id
                if declared_profile not in {
                        REJECTED_PATCH_GUARD_V3, REJECTED_PATCH_GUARD_V4, REJECTED_PATCH_GUARD_V5, VERIFIED_PATCH_GUARD_V1}:
                    continue
                expected_outcome = declared_profile == VERIFIED_PATCH_GUARD_V1
                if (request["verification"]["payload"]["c1"]["oracle_resolved"] is not expected_outcome
                        or request["schema_version"] != (REQUEST_SCHEMA_V4 if expected_outcome else REQUEST_SCHEMA_V3)):
                    raise PublicationViolation("replayed outcome differs from its independently verified profile")
                behavior = write_subject_ref(content_key=unit.content_key,
                    manifest_id=record_id_reference_from_dict(request["manifest"]["manifest_id"]))
                if behavior not in admitted_subject_refs:
                    continue
                returned = read_replayed_return_value(observation,
                    replay_store.open_snapshot(observation.terminal_snapshot_ref))
                if declared_profile in {REJECTED_PATCH_GUARD_V4, REJECTED_PATCH_GUARD_V5, VERIFIED_PATCH_GUARD_V1} and returned == []:
                    continue
                digest = reference(domain, domain["schema_version"]).sha256
                if (type(returned) is not list or any(type(word) is not int for word in returned)
                        or returned != fingerprint_words(digest)):
                    raise PublicationViolation("replayed feedback differs from its independently verified domain")
                if current:
                    publisher.authority.stores.lifecycle_store.require_consumable(
                        subject_ref=HashBoundRef.from_dict(request["attestation_ref"]),
                        context=LifecycleContext.from_dict(request["lifecycle_context"]))
                item = ExecutionFeedback(reference(publication, PUBLICATION_RESULT_V3), domain["patch_sha256"], expected_outcome)
                feedback[item.source_result_ref] = item
    if len(feedback) > 128:
        raise PublicationViolation("replayed feedback exceeds the bounded intent contract")
    return tuple(feedback[ref] for ref in sorted(feedback, key=lambda ref: (ref.ref_id, ref.sha256)))


def verify_execution_feedback(*, intent, publisher, stage10_store, run_store, run_root,
                              manifest, context, completed, profile, boundary):
    """Independently reconstruct feedback from the retained predecessor/replay."""
    from ..runner.attempt_plan import previous_execution_feedback
    from ..runner.state_machine import load_run_state
    state = load_run_state(run_store)
    previous = None
    if context.attempt_index > 1:
        prior = [item for item in state.attempts if item.attempt_index == context.attempt_index - 1]
        if len(prior) != 1 or prior[0].result is None:
            raise PublicationViolation("execution feedback lacks its completed predecessor")
        previous = prior[0].result
    expected = previous_execution_feedback(previous,
        full_positive_required=profile.full_positive_feedback_required)
    if profile.replayed_feedback_required:
        checked, body, basis, replay_store, replay = _delivered_replay(publisher=publisher,
            stage10_store=stage10_store, run_store=run_store, run_root=run_root,
            manifest=manifest, context=context, completed=completed)
        expected += read_replayed_execution_feedback(publisher=publisher, profile=profile,
            boundary=boundary, manifest=manifest, admitted_subject_refs=basis.admitted_subject_refs,
            replay_store=replay_store, replay_result=replay, current=False)
    if intent.execution_feedback != expected:
        raise PublicationViolation("intent feedback differs from independently retained execution evidence")


def observe_rejected_candidate(*, publisher, stage10_store, run_store, run_root, manifest, context, completed, profile, boundary):
    """Consume real admitted replay output; return a sealed refusal only on an exact match."""
    if publisher is None or completed.worker_result.status is not WorkerCandidateStatus.PROPOSED_PATCH:
        return None
    publisher.authority.validate()
    if OBSERVER in {item.value for item in publisher.authority.source_actors}:
        raise PublicationViolation("reuse observer cannot be a configured producer or worker")
    checked, body, basis, replay_store, replay = _delivered_replay(publisher=publisher, stage10_store=stage10_store,
        run_store=run_store, run_root=run_root, manifest=manifest, context=context, completed=completed)
    expected = _candidate_dimensions(manifest, profile, boundary, checked)
    with publisher.authority.stores.fence.exclusive():
        for observation in replay.observations:
            for result, publication, request, prepared in _publication_for_observation(publisher, observation):
                if not _negative_publication(request):
                    continue
                domain = request["domain"]
                if any(domain.get(key) != value for key, value in expected.items()):
                    continue
                if request["identity"]["manifest_sha256"] == manifest.manifest_sha256:
                    continue
                if not _oracle_configuration_matches(publisher, boundary, request, prepared):
                    continue
                behavior = write_subject_ref(content_key=behavior_unit_from_dict(request["unit"]).content_key,
                    manifest_id=record_id_reference_from_dict(request["manifest"]["manifest_id"]))
                if behavior not in basis.admitted_subject_refs:
                    continue
                fingerprint = read_replayed_return_value(observation, replay_store.open_snapshot(observation.terminal_snapshot_ref))
                digest = reference(domain, domain["schema_version"]).sha256
                if (type(fingerprint) is not list or any(type(word) is not int for word in fingerprint)
                        or fingerprint != fingerprint_words(digest)):
                    raise PublicationViolation("actual guard output differs from its verified domain")
                stores = publisher.authority.stores
                head = stores.lifecycle_store.require_consumable(
                    subject_ref=HashBoundRef.from_dict(request["attestation_ref"]),
                    context=LifecycleContext.from_dict(request["lifecycle_context"]))
                before = _repository_base(profile.repository_root, manifest.config.base_revision)
                payload = {"schema_version": MECHANISM_USE_SCHEMA_V1, "policy_version": REUSE_GUARD_POLICY_V1,
                    "observer_identity": OBSERVER, "manifest_sha256": manifest.manifest_sha256,
                    "context_sha256": context.context_sha256, "run_id": manifest.run_id.value, "attempt_id": context.attempt_id.value,
                    "publication_transaction_id": result.transaction_id, "publication_ref": result.reference.to_dict(),
                    "behavior_ref": behavior.to_dict(), "domain": domain, "domain_fingerprint": digest,
                    "producer_verification_ref": request["verification"]["verification_ref"],
                    "producer_outcome_ref": request["outcome"]["outcome_ref"],
                    "worker_result_ref": completed_worker_delivery_ref(checked).to_dict(),
                    "delivery_ref": checked.delivery_receipt_ref.to_dict(), "context_ref": checked.worker_context_audit_ref.to_dict(),
                    "retrieval_ref": checked.upstream.retrieval_ref.to_dict(),
                    "retrieval_decision_ref": checked.upstream.retrieval_decision_ref.to_dict(),
                    "replay_ref": checked.upstream.replay_ref.to_dict(), "observation_ref": observation.observation_id.to_dict(),
                    "snapshot_ref": observation.terminal_snapshot_ref.to_dict(), "knowledge_basis_sha256": basis.digest(),
                    "lifecycle_head": head.record_id.to_dict(), "coordinator_epoch": stores.fence.current_epoch(),
                    "repository_before": before, "repository_after": _repository_base(profile.repository_root, manifest.config.base_revision),
                    "effect": "EXACT_REJECTED_C1_DISPATCH_AVOIDED", "confidence": "EXACT_DOMAIN_AND_PATCH", "avoided_dispatches": 1,
                    "counterfactual_basis": "ACCEPTED_PLAN_AND_PROPOSED_PATCH_AT_CANONICAL_C1_DISPATCH"}
                record = object.__new__(MechanismUseRecord)
                object.__setattr__(record, "_raw", encode_canonical(payload))
                object.__setattr__(record, "_seal", _SEAL)
                object.__setattr__(record, "_digest", hashlib.sha256(record._raw).hexdigest())
                return record
    return None


def verify_mechanism_use(value, *, publisher, stage10_store, run_store, run_root, manifest, context, completed, profile, boundary):
    """Reopen historical proof independently; never repeat the declined dispatch."""
    fields = {"schema_version", "policy_version", "observer_identity", "manifest_sha256", "context_sha256", "run_id", "attempt_id",
        "publication_transaction_id", "publication_ref", "behavior_ref", "domain", "domain_fingerprint", "producer_verification_ref",
        "producer_outcome_ref", "worker_result_ref", "delivery_ref", "context_ref", "retrieval_ref", "retrieval_decision_ref",
        "replay_ref", "observation_ref", "snapshot_ref", "knowledge_basis_sha256", "lifecycle_head", "coordinator_epoch",
        "repository_before", "repository_after", "effect", "confidence", "avoided_dispatches", "counterfactual_basis"}
    if type(value) is not dict or set(value) != fields or publisher is None:
        raise PublicationViolation("mechanism use has an unknown contract or physical owner")
    publisher.authority.validate()
    if OBSERVER in {item.value for item in publisher.authority.source_actors}:
        raise PublicationViolation("reuse observer cannot be a configured producer or worker")
    checked, body, basis, replay_store, replay = _delivered_replay(publisher=publisher, stage10_store=stage10_store,
        run_store=run_store, run_root=run_root, manifest=manifest, context=context, completed=completed)
    expected = {"schema_version": MECHANISM_USE_SCHEMA_V1, "policy_version": REUSE_GUARD_POLICY_V1,
        "observer_identity": OBSERVER, "manifest_sha256": manifest.manifest_sha256, "context_sha256": context.context_sha256,
        "run_id": manifest.run_id.value, "attempt_id": context.attempt_id.value,
        "worker_result_ref": completed_worker_delivery_ref(checked).to_dict(), "delivery_ref": checked.delivery_receipt_ref.to_dict(),
        "context_ref": checked.worker_context_audit_ref.to_dict(), "retrieval_ref": checked.upstream.retrieval_ref.to_dict(),
        "retrieval_decision_ref": checked.upstream.retrieval_decision_ref.to_dict(), "replay_ref": checked.upstream.replay_ref.to_dict(),
        "knowledge_basis_sha256": basis.digest(), "effect": "EXACT_REJECTED_C1_DISPATCH_AVOIDED",
        "confidence": "EXACT_DOMAIN_AND_PATCH", "avoided_dispatches": 1,
        "counterfactual_basis": "ACCEPTED_PLAN_AND_PROPOSED_PATCH_AT_CANONICAL_C1_DISPATCH"}
    if any(value[name] != expected_value for name, expected_value in expected.items()) or type(value["avoided_dispatches"]) is not int:
        raise PublicationViolation("mechanism use does not describe this completed worker delivery")
    result = PublicationResult(publisher.root, value["publication_transaction_id"])
    publication = result.payload()
    _, prepared = read_committed_snapshot_transaction(publisher.root / "prepared", transaction_id=result.transaction_id)
    request = decode_canonical(prepared["request.json"])
    if (not _negative_publication(request)
            or value["publication_ref"] != result.reference.to_dict() or value["domain"] != request["domain"]
            or value["producer_verification_ref"] != request["verification"]["verification_ref"]
            or value["producer_outcome_ref"] != request["outcome"]["outcome_ref"]
            or request["identity"]["manifest_sha256"] == manifest.manifest_sha256
            or not _oracle_configuration_matches(publisher, boundary, request, prepared)
            or any(value["domain"].get(key) != item for key, item in _candidate_dimensions(manifest, profile, boundary, checked).items())):
        raise PublicationViolation("mechanism use differs from independently verified prior negative evidence")
    subject = write_subject_ref(content_key=behavior_unit_from_dict(request["unit"]).content_key,
        manifest_id=record_id_reference_from_dict(request["manifest"]["manifest_id"]))
    if subject not in basis.admitted_subject_refs or value["behavior_ref"] != subject.to_dict():
        raise PublicationViolation("mechanism use names knowledge outside actual admission")
    selected = [item for item in replay.observations if item.observation_id.to_dict() == value["observation_ref"]]
    if len(selected) != 1:
        raise PublicationViolation("mechanism use lacks its actual replay observation")
    observation = selected[0]
    if (observation.behavior_content_key != behavior_unit_from_dict(request["unit"]).content_key.value
            or observation.terminal_snapshot_ref.to_dict() != value["snapshot_ref"]):
        raise PublicationViolation("mechanism observation names another program or output")
    fingerprint = read_replayed_return_value(observation, replay_store.open_snapshot(observation.terminal_snapshot_ref))
    digest = reference(value["domain"], value["domain"]["schema_version"]).sha256
    if (value["domain_fingerprint"] != digest or type(fingerprint) is not list
            or any(type(word) is not int for word in fingerprint) or fingerprint != fingerprint_words(digest)):
        raise PublicationViolation("observed return is not the exact independently verified domain fingerprint")
    stores = publisher.authority.stores
    head = next((item for item in stores.lifecycle_store.records() if item.record_id.to_dict() == value["lifecycle_head"]), None)
    if (head is None or head.to_state is not LifecycleState.INDEXED
            or head.subject_ref.to_dict() != request["attestation_ref"] or head.context.to_dict() != request["lifecycle_context"]
            or type(value["coordinator_epoch"]) is not int or value["coordinator_epoch"] % 2
            or value["coordinator_epoch"] < publication["interval_epoch"] or value["coordinator_epoch"] > stores.fence.current_epoch()):
        raise PublicationViolation("mechanism use lacks its historical lifecycle and coordinator binding")
    tree = subprocess.check_output(["git", "-C", str(profile.repository_root), "rev-parse", manifest.config.base_revision + "^{tree}"],
                                   stderr=subprocess.PIPE).decode().strip()
    if value["repository_before"] != {"revision": manifest.config.base_revision, "tree": tree} or value["repository_after"] != value["repository_before"]:
        raise PublicationViolation("guard outcome did not retain the exact unchanged repository base")
    if boundary.writer.path.exists():
        try:
            read_c1_authority_receipt(boundary, gold_run_id=manifest.gold_run_id, attempt_id=context.attempt_id.value)
        except GoldRunViolation as exc:
            if exc.failure_code is not GoldRunFailureCode.RECORD_MISSING:
                raise
        else:
            raise PublicationViolation("guard refusal contradicts an actual C1 execution")
    return {"record_ref": reference(value, MECHANISM_USE_SCHEMA_V1).to_dict(),
        "publication_ref": value["publication_ref"], "behavior_ref": value["behavior_ref"],
        "effect": value["effect"], "task_resolved": False, "repository_unchanged": True}
