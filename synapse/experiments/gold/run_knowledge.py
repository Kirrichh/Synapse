"""Resolve frozen seed evidence against a connected project's actual stores.

Files supply evidence bytes, never permissive gate answers. Every candidate
is reopened through Library; provenance, lifecycle and the complete taint
closure are checked by their existing owners again when a gate asks.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path

from synapse.change.workspace import resolve_revision

from . import compatibility as C, gate_findings as GF, taint as T
from .admission import GrantEnvelope, GateDependencyUnavailable
from .behavior import behavior_unit_from_dict
from .bindings import binding_from_dict
from .canonicalization import HashBoundRef, RefKind
from .contracts import RepositoryRevision, record_id_reference_from_dict
from .lifecycle import LifecycleContext
from .persistence import read_regular_bytes
from .provenance import (
    BehaviorAttestation, BuilderRuntimeIdentity, OracleObservation, ObservedExternalInput,
    behavior_attestation_to_ref, configure_platform_attester, require_behavior_attestation_consumable,
)
from .run_inputs import FrozenGoldInputs, MAX_INPUT_BYTES
from .stage10.context_codec import encode_canonical


KNOWLEDGE_INPUT_SCHEMA_V1 = "synapse.stage4.gold.knowledge-input/v1"


def _taint_closure(raw, *, handle, clock):
    if type(raw) is not dict or set(raw) != {"profiles", "derivations", "decisions", "root_id"}:
        raise ValueError("seed taint closure has an unknown shape")
    profiles = tuple(T.source_taint_profile_from_dict(
        item, authority_handle=handle, expected_subject_ref=HashBoundRef.from_dict(item["subject_ref"]),
    ) for item in raw["profiles"])
    nodes = {item.profile_id.value: item for item in profiles}
    derivations = []
    for item in raw["derivations"]:
        sources = tuple(nodes[record_id_reference_from_dict(ref).value] for ref in item["source_profile_ids"])
        parents = tuple(nodes[record_id_reference_from_dict(ref).value] for ref in item["source_derivation_ids"])
        node = T.taint_derivation_from_dict(
            item, authority_handle=handle, source_profiles=sources, source_derivations=parents,
            expected_subject_ref=HashBoundRef.from_dict(item["subject_ref"]),
        )
        nodes[node.derivation_id.value] = node
        derivations.append(node)
    if len(nodes) != len(profiles) + len(derivations):
        raise ValueError("seed repeats a taint node")
    root = nodes[raw["root_id"]]
    decisions = []
    for item in raw["decisions"]:
        evaluator = T.configure_taint_authority_evaluator(
            authority_handle=handle, policy_version=item["policy_version"], trusted_clock=clock,
        )
        decisions.append(T.taint_authority_decision_from_dict(
            item, authority_handle=handle, evaluator=evaluator, current_profile=root,
            source_profiles=profiles, source_derivations=tuple(derivations),
        ))
    return root, profiles, tuple(derivations), tuple(decisions)


class RunKnowledge:
    """One frozen candidate universe, with fresh owner-backed observations."""

    def __init__(self, *, inputs: FrozenGoldInputs, project, task):
        data = inputs.data
        seed = data["knowledge"]
        if type(seed) is not dict or set(seed) != {"schema_version", "candidates", "files", "conflicts"} or seed["schema_version"] != KNOWLEDGE_INPUT_SCHEMA_V1:
            raise ValueError("knowledge inputs have an unknown schema")
        if type(seed["candidates"]) is not list or not seed["candidates"]:
            raise ValueError("Gold needs a non-empty previously admitted seed corpus")
        self.project = project
        self.inputs = inputs
        self.repo_root = Path(data["repo_root"])
        self.task = task
        self._observation = data["declaration"]["observation"]
        self._files = {}
        for item in seed["files"]:
            if type(item) is not dict or set(item) != {"ref", "path"}:
                raise ValueError("observed evidence file must contain a reference and path")
            reference = HashBoundRef.from_dict(item["ref"])
            path = Path(item["path"])
            if not path.is_absolute() or reference in self._files:
                raise ValueError("evidence paths must be absolute and references unique")
            self._files[reference] = path
            self.open_evidence(reference)
        self._evidence = {}
        self._subjects = {}
        candidates = []
        lifecycle_snapshot = project.lifecycle_store.snapshot()
        taint_anchor = project.taint_store.current_anchor()
        for item in seed["candidates"]:
            if type(item) is not dict or set(item) != {"unit", "manifest_id", "attestation", "bindings", "lifecycle_context", "taint"}:
                raise ValueError("candidate support has an unknown shape")
            declared_unit = behavior_unit_from_dict(item["unit"])
            manifest_id = record_id_reference_from_dict(item["manifest_id"])
            loaded = project.library.get_verified_behavior(declared_unit.content_key, manifest_id)
            unit, blob, manifest = loaded.unit, loaded.blob, loaded.manifest
            entry = next(entry for entry in project.library.search_index() if entry.manifest_id == manifest_id.value)
            raw_attestation = item["attestation"]
            attestation = BehaviorAttestation.from_dict(
                raw_attestation, authority_handle=project.authority_handle,
                expected_subject_content_key=unit.content_key,
                expected_builder_runtime_identity=BuilderRuntimeIdentity.from_dict(raw_attestation["builder_runtime_identity"]),
                expected_attester_identity=project.declaration.identities.platform_attester_actor,
                expected_repository_revision=RepositoryRevision.from_dict(raw_attestation["repository_revision"]),
            )
            context = LifecycleContext.from_dict(item["lifecycle_context"])
            attestation_ref = behavior_attestation_to_ref(attestation)
            lifecycle = project.lifecycle_store.require_consumable(subject_ref=attestation_ref, context=context)
            bindings = tuple(binding_from_dict(
                record, repo_root=self.repo_root, consumer_revision=attestation.repository_revision,
            ) for record in item["bindings"])
            root, profiles, derivations, decisions = _taint_closure(
                item["taint"], handle=project.authority_handle, clock=self.clock,
            )
            descriptor = C.create_compatibility_subject_descriptor(
                unit=unit, blob=blob, manifest=manifest, index_entry=entry, attestation=attestation,
                bindings=bindings, lifecycle_record=lifecycle, lifecycle_snapshot=lifecycle_snapshot,
                taint_root_basis=root, taint_history_anchor=taint_anchor,
            )
            evidence = C.create_compatibility_subject_evidence(
                descriptor=descriptor, unit=unit, blob=blob, manifest=manifest, index_entry=entry,
                attestation=attestation, bindings=bindings, taint_root_basis=root,
                taint_source_profiles=profiles, taint_derivations=derivations, taint_decisions=decisions,
                lifecycle_record=lifecycle, lifecycle_snapshot=lifecycle_snapshot, lifecycle_context=context,
                taint_history_anchor=taint_anchor,
            )
            subject = GF.candidate_subject_ref(descriptor)
            if subject in self._subjects:
                raise ValueError("seed repeats a library subject")
            self._subjects[subject] = descriptor
            self._evidence[descriptor.descriptor_id.value] = evidence
            candidates.append((unit, descriptor, entry))
        self.candidates = tuple(candidates)
        self._conflicts = {}
        for item in seed["conflicts"]:
            if type(item) is not dict or set(item) != {"left", "right", "kind", "evidence_refs"}:
                raise ValueError("conflict declaration has an unknown shape")
            key = tuple(sorted((item["left"], item["right"])))
            if key in self._conflicts or key[0] == key[1]:
                raise ValueError("conflict pair is repeated or reflexive")
            refs = tuple(HashBoundRef.from_dict(ref) for ref in item["evidence_refs"])
            if not refs:
                raise ValueError("conflict assessment requires resolvable evidence")
            for reference in refs:
                self.open_evidence(reference)
            self._conflicts[key] = (None if item["kind"] is None else C.ConflictKind(item["kind"]), refs)
        if not set(task.behavior_refs) <= set(self._subjects):
            raise ValueError("governing task names behavior outside the seed corpus")
        for reference in self._subjects:
            self.provenance_probe(reference)
            self.taint_probe(reference)

    @staticmethod
    def clock():
        return datetime.now(timezone.utc)

    def open_evidence(self, reference: HashBoundRef) -> bytes:
        path = self._files.get(reference)
        if path is None:
            raise GateDependencyUnavailable("evidence reference has no declared source file")
        raw = read_regular_bytes(path, maximum_bytes=MAX_INPUT_BYTES)
        if hashlib.sha256(raw).hexdigest() != reference.sha256 or len(raw) != reference.byte_length:
            raise GateDependencyUnavailable("observed evidence file changed")
        return raw

    def evidence_for(self, descriptor):
        evidence = self._evidence[descriptor.descriptor_id.value]
        C.validate_compatibility_subject_evidence(evidence, descriptor=descriptor)
        return evidence

    def provenance_probe(self, reference):
        evidence = self.evidence_for(self._subjects[reference])
        require_behavior_attestation_consumable(
            attestation=evidence.attestation, expected_subject_content_key=evidence.unit.content_key,
            authority_handle=self.project.authority_handle, attestation_store=self.project.attestation_store,
            lifecycle_store=self.project.lifecycle_store, lifecycle_context=evidence.lifecycle_context,
        )
        return True

    def lifecycle_probe(self, reference):
        evidence = self.evidence_for(self._subjects[reference])
        self.project.lifecycle_store.require_consumable(
            subject_ref=behavior_attestation_to_ref(evidence.attestation),
            context=evidence.lifecycle_context,
        )
        return True

    def taint_probe(self, reference):
        evidence = self.evidence_for(self._subjects[reference])
        effective = T.require_taint_consumable(
            authority_handle=self.project.authority_handle, root_basis=evidence.taint_root_basis,
            source_profiles=evidence.taint_source_profiles, derivations=evidence.taint_derivations,
            decisions=evidence.taint_decisions, history_store=self.project.taint_store,
        )
        return GF.consumption_finding_from_effective_taint(effective)

    def consumability_probe(self, reference):
        taint = self.taint_probe(reference)
        return taint.consumable and self.provenance_probe(reference) and self.lifecycle_probe(reference)

    def ref_resolver(self, reference):
        descriptor = self._subjects.get(reference)
        if descriptor is None:
            return False
        record = self.project.library.get_verified_behavior(descriptor.content_key, descriptor.manifest_id)
        return record.unit.content_key == descriptor.content_key

    def grant_probe(self):
        self.inputs.verify_project()
        grant = self.project.declaration.entitlements
        if grant is None:
            raise GateDependencyUnavailable("connected project has no operator entitlements")
        return GrantEnvelope(scopes=grant.scopes, capabilities=grant.capabilities,
                             oracles=grant.oracles, policy_version=self.inputs.manifest.versions.policy_version)

    def observe(self):
        raw = self._observation
        required = {"builder", "base_revision", "task_contract_ref", "policy_inputs", "environment_inputs",
                    "tool_inputs", "source_refs", "verification_refs", "oracle_observation"}
        if type(raw) is not dict or set(raw) != required:
            raise ValueError("platform observation declaration has an unknown shape")
        revision = RepositoryRevision.git_commit(resolve_revision(self.repo_root, "HEAD"))
        if revision.git_sha != self.task.repository_revision_sha256:
            raise GateDependencyUnavailable("repository changed since experiment freeze")
        values = {}
        for field in ("policy_inputs", "environment_inputs", "tool_inputs"):
            values[field] = tuple(ObservedExternalInput.from_dict(item) for item in raw[field])
            for item in values[field]:
                self.open_evidence(item.ref)
        for field in ("source_refs", "verification_refs"):
            values[field] = tuple(HashBoundRef.from_dict(item) for item in raw[field])
            for reference in values[field]:
                self.open_evidence(reference)
        task_ref = HashBoundRef.from_dict(raw["task_contract_ref"])
        if task_ref != self.task.reference:
            raise GateDependencyUnavailable("platform observation names a different governing task")
        oracle = OracleObservation.from_dict(raw["oracle_observation"])
        self.open_evidence(task_ref)
        self.open_evidence(oracle.result_ref)
        attester = configure_platform_attester(
            authority_handle=self.project.authority_handle,
            builder_runtime_identity=BuilderRuntimeIdentity.from_dict(raw["builder"]), trusted_clock=self.clock,
        )
        return attester.observe(
            authority_handle=self.project.authority_handle, repository_revision=revision,
            base_revision=RepositoryRevision.from_dict(raw["base_revision"]), task_contract_ref=task_ref,
            oracle_observation=oracle, **values,
        )

    def assess_conflict(self, context, left_decision, right_decision, left, right):
        key = tuple(sorted((left.content_key.value, right.content_key.value)))
        if key not in self._conflicts:
            raise GateDependencyUnavailable("seed pair has no independently evidenced conflict assessment")
        kind, refs = self._conflicts[key]
        for reference in refs:
            self.open_evidence(reference)
        return kind, refs

    def score(self, query_id, descriptor_id, score_input):
        evidence = self._evidence[descriptor_id.value]
        ranks = {ref.ref_id: index for index, ref in enumerate(self.task.behavior_refs)}
        index = ranks.get(evidence.unit.content_key.digest_sha256)
        return 0 if index is None else 1_000_000 - index

    def ranking_input_ref(self, query_id, descriptor_id):
        raw = encode_canonical({"query_id": query_id.to_dict(), "descriptor_id": descriptor_id.to_dict(),
                                "task_contract_ref": self.task.reference.to_dict()})
        digest = hashlib.sha256(raw).hexdigest()
        return HashBoundRef(RefKind.ARTIFACT, digest, "synapse.stage4.gold.declared-ranking-input/v1",
                            digest, len(raw), "application/json")
