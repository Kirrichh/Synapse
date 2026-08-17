from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from itertools import count

import pytest

from synapse.experiments.gold import compatibility as compatibility_module
from synapse.version import LANGUAGE_VERSION
from synapse.experiments.gold.behavior import (
    BehaviorBlob,
    BehaviorCore,
    BehaviorKind,
    BehaviorManifest,
    SynapseBehaviorUnit,
    compile_behavior_unit,
    create_behavior_blob,
    create_behavior_manifest,
    create_behavior_unit,
)
from synapse.experiments.gold.canonicalization import (
    COMPILER_ADAPTER_PROFILE_V1,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
)
from synapse.experiments.gold.bindings import (
    BINDING_CONTRACT_VERSION_V1,
    PYTHON_BINDING_RESOLVER_V1,
    BindingKind,
    PythonSymbolKind,
    binding_to_ref,
    resolve_python_binding,
)
from synapse.experiments.gold.compatibility import (
    COMPATIBILITY_POLICY_V1,
    CompatibilityDecision,
    CompatibilityDecisionKind,
    CompatibilityDimension,
    CompatibilityFailureCode,
    CompatibilityRevalidationRecord,
    CompatibilitySubjectDescriptor,
    CompatibilitySubjectEvidence,
    CompatibilityReason,
    CompatibilityValueState,
    CompatibilityViolation,
    ConflictDecisionKind,
    ConflictKind,
    DimensionResult,
    EvidenceCompleteness,
    RevalidationOutcome,
    RevalidationStage,
    create_compatibility_context,
    create_compatibility_evaluator_declaration,
    create_compatibility_subject_descriptor,
    create_compatibility_subject_evidence,
    create_conflict_evidence_proposal,
    absent_compatibility_value,
    compatibility_value,
    configure_compatibility_evaluator,
    evaluate_compatibility,
    evaluate_conflicts,
    require_configured_compatibility_evaluator,
    revalidate_before_consumption,
    revalidate_before_loading,
    validate_compatibility_context,
    validate_compatibility_decision,
    validate_compatibility_revalidation_record,
    validate_compatibility_subject_descriptor,
)
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AttemptId,
    AuthorityIdentity,
    AuthorityRole,
    IdentityDomain,
    LifecycleReasonCode,
    ReasonCode,
    RepositoryRevision,
    RunId,
    SchemaVersion,
    compute_record_id,
    create_stage4_authority_configuration,
    create_stage4_authority_handle,
)
from synapse.experiments.gold.library import (
    LIBRARY_PUBLISHER_IDENTITY_V1,
    BehaviorLibrary,
    PublisherIdentity,
)
from synapse.experiments.gold.lifecycle import (
    LIFECYCLE_CONTEXT_V1,
    LifecycleAuthorityAction,
    LifecycleContext,
    LifecycleScope,
    LifecycleState,
    SupersessionDecisionKind,
    configure_lifecycle_authority_evaluator,
    create_lifecycle_authority_proposal,
    create_revocation_decision,
    create_supersession_decision,
    open_lifecycle_store,
)
from synapse.experiments.gold.retrieval import (
    RANKING_PROFILE_V1,
    RETRIEVAL_POLICY_V1,
    CandidateDisposition,
    RetrievalFailureCode,
    RetrievalOutcome,
    RetrievalViolation,
    configure_ranking_feature_provider,
    configure_retriever,
    create_retrieval_query,
    enumerate_retrieval_candidates_durably,
    gate_selectable_candidates,
    consumer_context_ref_of,
    configure_durable_retrieval_persistence,
    select_and_load_durably,
)
from synapse.experiments.gold.provenance import (
    BUILDER_RUNTIME_IDENTITY_V1,
    OBSERVED_EXTERNAL_INPUT_V1,
    ORACLE_OBSERVATION_V1,
    BehaviorAttestation,
    BuilderRuntimeIdentity,
    ExternalInputKind,
    ObservedExternalInput,
    OracleObservation,
    PlatformObservedProvenance,
    behavior_attestation_to_ref,
    configure_platform_attester,
    open_behavior_attestation_store,
    validate_platform_observed_provenance,
)
from synapse.experiments.gold.taint import (
    SourceTaintProfile,
    TaintClass,
    classify_source_taint,
    open_taint_history_store,
)

from tests.gold_frozen_candidates import frozen_for_retriever
from tests.gold_write_admission import gate_history, publish_behavior
from tests.gold_store_fence import fence_for


NOW = datetime(2026, 3, 4, 5, 6, 7, 8, tzinfo=timezone.utc)
REVISION = RepositoryRevision.git_commit("1" * 40)
BASE_REVISION = RepositoryRevision.git_commit("2" * 40)
_BEHAVIOR_VECTORS = Path(__file__).parent / "fixtures" / "gold" / "behavior_vectors_v1.json"
_RETRIEVAL_DURABILITY_CASES = count(1)


def _retrieval_durability(retriever):
    from synapse.experiments.gold.admission_journal import FileAdmissionJournal
    from synapse.experiments.gold.admission_store import FileAdmissionCausalStore
    from synapse.experiments.gold.compatibility_store import FileCompatibilityStore

    root = retriever._library._root.parent
    fence = fence_for(root)
    case_root = root / "compatibility-retrieval" / str(next(_RETRIEVAL_DURABILITY_CASES))
    case_root.mkdir(parents=True, exist_ok=True)
    journal = FileAdmissionJournal(case_root / "decisions.journal", fence)
    causal = FileAdmissionCausalStore(
        case_root / "causal",
        admission_history=journal,
        mutation_fence=fence,
    )
    compatibility = FileCompatibilityStore(
        case_root / "compatibility",
        mutation_fence=fence,
    )
    persistence = configure_durable_retrieval_persistence(
        compatibility_history=compatibility,
        admission_causal_history=causal,
    )
    return journal, persistence





def _retrieval_entitlement():
    """The verifier's own declaration and actor set for the retrieval gate.

    Rebuilt with the same inputs the controller used rather than read off it:
    entitlement is a claim about whose copies were consulted, so consulting the
    evaluator's own object would only show a record agreeing with itself.
    """

    from synapse.experiments.gold import authority_config as AC
    from synapse.experiments.gold.contracts import (
        ActorIdentity,
        AuthorityIdentity,
        AuthorityRole,
        GateKind,
        create_stage4_authority_configuration,
        create_stage4_authority_handle,
    )

    configuration = create_stage4_authority_configuration(
        platform_attester_actor=ActorIdentity(value="gate-attester"),
        builder_actor=ActorIdentity(value="gate-builder"),
        taint_classifier_authority=AuthorityIdentity(value="gate-taint-classifier"),
        taint_reviewer_authority=AuthorityIdentity(value="gate-taint-reviewer"),
        supersession_reviewer_authority=AuthorityIdentity(value="gate-supersession"),
        revocation_reviewer_authority=AuthorityIdentity(value="gate-revocation"),
        lifecycle_writer_actor=ActorIdentity(value="gate-lifecycle-writer"),
        governing_human_authority=None,
    )
    declaration = AC.create_gate_evaluator_declaration(
        authority_handle=create_stage4_authority_handle(configuration),
        evaluator_identity=AuthorityIdentity(value="gate-authority"),
        evaluator_component_id="gate-evaluator",
        evaluator_component_version="synapse.stage4.gate-evaluator/v1",
        gate_roles={
            GateKind.INGESTION: AuthorityRole.INGESTION_GATE_EVALUATOR,
            GateKind.PUBLICATION: AuthorityRole.PUBLICATION_GATE_EVALUATOR,
            GateKind.RETRIEVAL: AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
            GateKind.CONSUMPTION: AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
        },
        policy_version="policy-v1",
        trusted_clock=lambda: NOW,
    )
    actors = (
        ActorIdentity(value="gate-producer"),
        ActorIdentity(value="gate-retriever"),
        ActorIdentity(value="gate-consumer"),
    )
    return {gate: (declaration, actors) for gate in GateKind}


def _gate_controller():
    """A §22 controller for the Patch 6 suites.

    These tests are about retrieval semantics, not admission — but that is not a
    licence to skip the gate, which is exactly the mistake the previous helper
    made. The gate runs for real with permissive probes, so the pre-existing
    assertions measure what they always measured while the barrier stays in the
    path.
    """

    from synapse.experiments.gold import admission as A
    from synapse.experiments.gold import authority_config as AC
    from synapse.experiments.gold.contracts import (
        ActorIdentity,
        AuthorityIdentity,
        AuthorityRole,
        GateKind,
        create_stage4_authority_configuration,
        create_stage4_authority_handle,
    )

    configuration = create_stage4_authority_configuration(
        platform_attester_actor=ActorIdentity(value="gate-attester"),
        builder_actor=ActorIdentity(value="gate-builder"),
        taint_classifier_authority=AuthorityIdentity(value="gate-taint-classifier"),
        taint_reviewer_authority=AuthorityIdentity(value="gate-taint-reviewer"),
        supersession_reviewer_authority=AuthorityIdentity(value="gate-supersession"),
        revocation_reviewer_authority=AuthorityIdentity(value="gate-revocation"),
        lifecycle_writer_actor=ActorIdentity(value="gate-lifecycle-writer"),
        governing_human_authority=None,
    )
    declaration = AC.create_gate_evaluator_declaration(
        authority_handle=create_stage4_authority_handle(configuration),
        evaluator_identity=AuthorityIdentity(value="gate-authority"),
        evaluator_component_id="gate-evaluator",
        evaluator_component_version="synapse.stage4.gate-evaluator/v1",
        gate_roles={
            GateKind.INGESTION: AuthorityRole.INGESTION_GATE_EVALUATOR,
            GateKind.PUBLICATION: AuthorityRole.PUBLICATION_GATE_EVALUATOR,
            GateKind.RETRIEVAL: AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
            GateKind.CONSUMPTION: AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
        },
        policy_version="policy-v1",
        trusted_clock=lambda: NOW,
    )
    grant = A.GrantEnvelope(
        scopes=("repo:x",), capabilities=("read",), oracles=("swebench",),
        policy_version="policy-v1",
    )
    requested = A.RequestedEnvelope(
        scopes=("repo:x",), capabilities=("read",), oracles=("swebench",)
    )
    controller = A.configure_gate_controller(
        declaration=declaration,
        policy_version="policy-v1",
        run_id=RunId("compatibility-gate-run"),
        attempt_id=AttemptId("compatibility-gate-attempt"),
        repository_revision="a" * 40,
        environment_profile_id="compatibility-gate-env",
        trusted_clock=lambda: NOW,
        taint_probe=lambda item: A.TaintFinding(
            consumable=True, chain_complete=True, quarantined=False, blocks_publication=False
        ),
        provenance_probe=lambda item: True,
        lifecycle_probe=lambda item: True,
        compatibility_probe=lambda item, ctx: A.CompatibilityFinding(
            compatible=True, evidence_complete=True, drifted=False,
            conflicts_unresolved=False, subject_ref=item, consumer_context_ref=ctx,
        ),
        boundary_probe=lambda item: True,
        grant_probe=lambda: grant,
        head_reader=lambda: {"boundary_ref": None, "heads": {}},
        producer_actor=ActorIdentity(value="gate-producer"),
        retriever_actor=ActorIdentity(value="gate-retriever"),
        consumer_actor=ActorIdentity(value="gate-consumer"),
    )
    return controller, requested


def _retrieve_all(*, retriever, context, query, frozen=None):
    """Enumerate, run the real retrieval gate over what was found, then load.

    The gate is not skipped and its verdict is not synthesised: ingestion and
    publication are evaluated over the enumerated subject set, the retrieval
    gate decides against that exact set, and its sealed admission is what
    reaches the loader.

    ``frozen`` defaults to a real committed §21 snapshot over everything the
    library currently holds, so these tests keep considering the candidates they
    always considered while the enumeration is genuinely constrained by a
    boundary. A test that wants the snapshot and the library to disagree passes
    its own.
    """

    from synapse.experiments.gold import admission as A

    if frozen is None:
        frozen = frozen_for_retriever(retriever)
    journal, persistence = _retrieval_durability(retriever)
    enumeration = enumerate_retrieval_candidates_durably(
        retriever=retriever, context=context, query=query, frozen=frozen,
        persistence=persistence,
    )
    controller, requested = _gate_controller()
    subjects = enumeration.subject_refs
    if not subjects:
        return select_and_load_durably(
            retriever=retriever, context=context, query=query,
            enumeration=enumeration,
            admission=gate_selectable_candidates(
                controller=controller, candidates=(),
                consumer_context_ref=consumer_context_ref_of(context),
                boundary_ref=frozen.boundary_ref,
                frozen=frozen,
                requested=requested, publication_decision=None,
                entitlements=_retrieval_entitlement(),
                journal=journal, trusted_clock=lambda: NOW,
            ),
            persistence=persistence,
        )
    ingestion = A.evaluate_ingestion_gate(controller, subject_refs=subjects)
    publication = A.evaluate_publication_gate(
        controller, subject_refs=subjects, requested=requested, predecessor=ingestion
    )
    admission = gate_selectable_candidates(
        controller=controller,
        candidates=subjects,
        consumer_context_ref=consumer_context_ref_of(context),
        boundary_ref=frozen.boundary_ref,
        frozen=frozen,
        requested=requested,
        publication_decision=publication,
        entitlements=_retrieval_entitlement(),
        journal=journal, trusted_clock=lambda: NOW,
    )
    return select_and_load_durably(
        retriever=retriever, context=context, query=query,
        enumeration=enumeration, admission=admission,
        persistence=persistence,
    )

def _ref(name: str, kind: RefKind) -> HashBoundRef:
    raw = name.encode("utf-8")
    return HashBoundRef(
        kind=kind,
        ref_id=f"test.{name}",
        schema_id=f"synapse.stage4.test.{name}/v1",
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_length=len(raw),
        media_type="application/octet-stream",
    )


def _external(kind: ExternalInputKind, name: str, version: str) -> ObservedExternalInput:
    ref_kind = RefKind.CONTRACT_CONDITION if kind is ExternalInputKind.POLICY else RefKind.ARTIFACT
    return ObservedExternalInput(
        OBSERVED_EXTERNAL_INPUT_V1,
        kind,
        name,
        version,
        _ref(name, ref_kind),
    )


def _behavior(
    output_name: str = "result",
    *,
    binding_refs: tuple[HashBoundRef, ...] = (),
    with_compiler_binding: bool = True,
) -> tuple[SynapseBehaviorUnit, BehaviorBlob, BehaviorManifest]:
    vectors = json.loads(_BEHAVIOR_VECTORS.read_text(encoding="utf-8"))
    payload = copy.deepcopy(vectors["vectors"][0]["core"])
    payload["output_contract"]["fields"][0]["name"] = output_name.replace("-", "_")
    payload["binding_refs"] = [item.to_dict() for item in binding_refs]
    core = BehaviorCore.from_dict(payload)
    unit = create_behavior_unit(
        behavior_kind=core.behavior_kind,
        canonical_program=core.canonical_program,
        input_contract=core.input_contract,
        output_contract=core.output_contract,
        capability_requirements=core.capability_requirements,
        replay_contract=core.replay_contract,
        verification_contract=core.verification_contract,
        binding_refs=core.binding_refs,
        source_evidence_refs=core.source_evidence_refs,
        artifact_refs=core.artifact_refs,
    )
    blob = create_behavior_blob(unit)
    compiler_binding = compile_behavior_unit(unit) if with_compiler_binding else None
    return unit, blob, create_behavior_manifest(unit, blob, compiler_binding=compiler_binding)


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        check=False,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    return completed.stdout.decode("ascii", "strict").strip()


def _python_binding_repo(tmp_path: Path):
    repo = tmp_path / "binding-repository"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "core.autocrlf", "false")
    target = repo / "pkg" / "symbols.py"
    target.parent.mkdir()
    target.write_text("def target():\n    return 1\n\ndef other():\n    return 2\n", encoding="utf-8", newline="\n")
    _git(repo, "add", "pkg/symbols.py")
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "Synapse Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Synapse Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000",
    })
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "binding fixture", env=env)
    revision = RepositoryRevision.git_commit(_git(repo, "rev-parse", "HEAD"))
    binding = resolve_python_binding(
        repo,
        repository_revision=revision,
        path="pkg/symbols.py",
        module="pkg.symbols",
        qualname="target",
        symbol_kind=PythonSymbolKind.FUNCTION,
        contract_version=BINDING_CONTRACT_VERSION_V1,
        resolver_version=PYTHON_BINDING_RESOLVER_V1,
    )
    return repo, binding


def _other_python_binding(repo: Path, revision: RepositoryRevision):
    return resolve_python_binding(
        repo,
        repository_revision=revision,
        path="pkg/symbols.py",
        module="pkg.symbols",
        qualname="other",
        symbol_kind=PythonSymbolKind.FUNCTION,
        contract_version=BINDING_CONTRACT_VERSION_V1,
        resolver_version=PYTHON_BINDING_RESOLVER_V1,
    )


def _python_binding_from_second_revision(repo: Path):
    target = repo / "pkg" / "symbols.py"
    target.write_text(
        "def target():\n    return 1\n\ndef other():\n    return 3\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", "pkg/symbols.py")
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "Synapse Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Synapse Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-02T00:00:00+0000",
        "GIT_COMMITTER_DATE": "2000-01-02T00:00:00+0000",
    })
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "second binding fixture", env=env)
    revision = RepositoryRevision.git_commit(_git(repo, "rev-parse", "HEAD"))
    return resolve_python_binding(
        repo,
        repository_revision=revision,
        path="pkg/symbols.py",
        module="pkg.symbols",
        qualname="other",
        symbol_kind=PythonSymbolKind.FUNCTION,
        contract_version=BINDING_CONTRACT_VERSION_V1,
        resolver_version=PYTHON_BINDING_RESOLVER_V1,
    )


def _forged_descriptor(descriptor: CompatibilitySubjectDescriptor) -> CompatibilitySubjectDescriptor:
    forged = object.__new__(CompatibilitySubjectDescriptor)
    for name, value in vars(descriptor).items():
        object.__setattr__(forged, name, value)
    return forged


def _append_admitted(store, handle, subject_ref: HashBoundRef, context: LifecycleContext):
    transitions = (
        (LifecycleState.OBSERVED, LifecycleReasonCode.PLATFORM_OBSERVATION),
        (LifecycleState.EXTRACTED, LifecycleReasonCode.EXTRACTION_COMPLETED),
        (LifecycleState.DISTILLED, LifecycleReasonCode.DISTILLATION_COMPLETED),
        (LifecycleState.VALIDATED, LifecycleReasonCode.VALIDATION_PASSED),
        (LifecycleState.ATTESTED, LifecycleReasonCode.ATTESTATION_BOUND),
        (LifecycleState.ADMITTED, LifecycleReasonCode.PUBLICATION_ADMITTED),
    )
    predecessor = None
    head = None
    for sequence, (state, reason) in enumerate(transitions, start=1):
        head = store.append(
            authority_handle=handle,
            subject_ref=subject_ref,
            context=context,
            to_state=state,
            reason_code=reason,
            evidence_refs=(_ref(f"lifecycle-{sequence}", RefKind.SOURCE_EVIDENCE),),
            expected_predecessor_record_id=predecessor,
            expected_subject_sequence=sequence,
        )
        predecessor = head.record_id.value
    assert head is not None
    return head


@dataclass
class _PlatformObservationProvider:
    observation: object
    calls: int = 0

    def __call__(self):
        self.calls += 1
        return self.observation


@dataclass
class _Harness:
    root: Path
    handle: object
    library: BehaviorLibrary
    publisher: PublisherIdentity
    lifecycle_store: object
    attestation_store: object
    taint_store: object
    observation: object
    attestation: BehaviorAttestation
    taint_profile: SourceTaintProfile
    lifecycle_context: LifecycleContext
    unit: SynapseBehaviorUnit
    blob: BehaviorBlob
    manifest: BehaviorManifest
    entry: object
    lifecycle_record: object
    lifecycle_snapshot: object
    declaration: object
    evaluator: object
    context: object
    descriptor: CompatibilitySubjectDescriptor
    decision: CompatibilityDecision
    catalog: dict[str, CompatibilitySubjectEvidence]
    conflict_matrix: dict[tuple[str, str], tuple[ConflictKind | None, tuple[HashBoundRef, ...]]]
    extra_candidates: tuple[tuple[object, ...], ...]
    attester: object
    observation_provider: _PlatformObservationProvider

    def publish_extra(self, name: str = "extra") -> None:
        unit, blob, manifest = _behavior(name)
        publish_behavior(
            self.library,
            unit,
            blob,
            manifest,
            publisher=self.publisher,
            journal_root=self.root,
        )


def _make_harness(
    tmp_path: Path,
    *,
    revoked: bool = False,
    lifecycle_state: LifecycleState | None = None,
    extra_unresolved: int = 0,
    extra_resolved: int = 0,
    bindings: tuple[object, ...] = (),
    binding_repo_root: Path | None = None,
    context_repository_revision: RepositoryRevision | None = None,
    context_policy_version: str | None = None,
    context_environment_version: str | None = None,
    context_host_version: str | None = None,
    context_tool_version: str | None = None,
    context_oracle_ref_name: str | None = None,
    oracle_actor_identity: ActorIdentity | None = None,
    producer_repository_revision: RepositoryRevision | None = None,
    context_allowed_binding_kinds: tuple[BindingKind, ...] | None = None,
    # A context that allows only the one kind its object has cannot express a
    # query that matches nothing, and "nothing matched" is a real outcome the
    # §21 constraint did not remove. Widening the declaration is how a suite says
    # "this consumer would accept another kind; the frozen world has none".
    extra_allowed_behavior_kinds: tuple[BehaviorKind, ...] = (),
    with_compiler_binding: bool = True,
) -> _Harness:
    if revoked and lifecycle_state is not None:
        raise ValueError("revoked and lifecycle_state are mutually exclusive")
    configuration = create_stage4_authority_configuration(
        platform_attester_actor=ActorIdentity("platform-attester"),
        builder_actor=ActorIdentity("platform-builder"),
        taint_classifier_authority=AuthorityIdentity("taint-classifier"),
        taint_reviewer_authority=AuthorityIdentity("taint-reviewer"),
        supersession_reviewer_authority=AuthorityIdentity("supersession-reviewer"),
        revocation_reviewer_authority=AuthorityIdentity("revocation-reviewer"),
        lifecycle_writer_actor=ActorIdentity("platform-writer"),
        governing_human_authority=AuthorityIdentity("governing-human"),
    )
    handle = create_stage4_authority_handle(configuration)
    publisher = PublisherIdentity(
        LIBRARY_PUBLISHER_IDENTITY_V1,
        "stage4-library-publisher",
        "synapse.stage4.gold.publisher-policy/v1",
    )
    library_root = tmp_path / "library"
    library_root.mkdir(parents=True)
    library = BehaviorLibrary(
        library_root, publisher_identity=publisher, mutation_fence=fence_for(tmp_path),
        write_history=gate_history(tmp_path),
    )
    binding_refs = tuple(sorted((binding_to_ref(item) for item in bindings), key=lambda item: item.ref_id))
    revision = producer_repository_revision or (bindings[0].repository_revision if bindings else REVISION)
    unit, blob, manifest = _behavior(binding_refs=binding_refs, with_compiler_binding=with_compiler_binding)
    publish_behavior(
        library, unit, blob, manifest, publisher=publisher,
        journal_root=tmp_path,
    )
    entry = next(item for item in library.search_index() if item.content_key == unit.content_key.value)
    resolved_objects: list[tuple[SynapseBehaviorUnit, BehaviorBlob, BehaviorManifest, object]] = []
    for index in range(extra_resolved):
        extra_unit, extra_blob, extra_manifest = _behavior(f"resolved-{index}")
        publish_behavior(
            library, extra_unit, extra_blob, extra_manifest, publisher=publisher,
            journal_root=tmp_path,
        )
        extra_entry = next(item for item in library.search_index() if item.content_key == extra_unit.content_key.value)
        resolved_objects.append((extra_unit, extra_blob, extra_manifest, extra_entry))
    for index in range(extra_unresolved):
        extra_unit, extra_blob, extra_manifest = _behavior(f"unresolved-{index}")
        publish_behavior(
            library, extra_unit, extra_blob, extra_manifest, publisher=publisher,
            journal_root=tmp_path,
        )

    builder = BuilderRuntimeIdentity(
        BUILDER_RUNTIME_IDENTITY_V1,
        configuration.builder_actor,
        revision,
        _ref("builder-binary", RefKind.ARTIFACT),
        "synapse.stage4.test.builder/v1",
    )
    attester = configure_platform_attester(
        authority_handle=handle,
        builder_runtime_identity=builder,
        trusted_clock=lambda: NOW,
    )
    task_ref = _ref("task-contract", RefKind.CONTRACT_CONDITION)
    policy = _external(ExternalInputKind.POLICY, "compatibility-policy", COMPATIBILITY_POLICY_V1)
    host = _external(ExternalInputKind.ENVIRONMENT, "host-abi", "synapse.stage4.host-abi/v1")
    environment = _external(ExternalInputKind.ENVIRONMENT, "runtime-environment", "synapse.stage4.environment/v1")
    compiler = _external(ExternalInputKind.TOOL, "compiler", "synapse.stage4.compiler/v1")
    oracle = OracleObservation(
        ORACLE_OBSERVATION_V1,
        ActorIdentity("trusted-oracle") if oracle_actor_identity is None else oracle_actor_identity,
        revision,
        task_ref,
        _ref("oracle-result", RefKind.SOURCE_EVIDENCE),
    )
    producer_observation = attester.observe(
        authority_handle=handle,
        repository_revision=revision,
        base_revision=BASE_REVISION,
        task_contract_ref=task_ref,
        policy_inputs=(policy,),
        environment_inputs=(host, environment),
        tool_inputs=(compiler,),
        source_refs=(_ref("source", RefKind.SOURCE_EVIDENCE),),
        verification_refs=(_ref("verification", RefKind.SOURCE_EVIDENCE),),
        oracle_observation=oracle,
    )
    attestation = attester.attest(
        authority_handle=handle,
        observed=producer_observation,
        subject_content_key=unit.content_key,
        producer_run_id=RunId("run-001"),
        producer_attempt_id=AttemptId("attempt-001"),
        producer_actor_ids=(ActorIdentity("candidate-producer"),),
    )
    attestation_store = open_behavior_attestation_store(
        mutation_fence=fence_for(tmp_path / "attestations"),
        root=tmp_path / "attestations",
        authority_handle=handle,
        platform_attester=attester,
        allow_genesis=True,
    )
    attestation_store.append(authority_handle=handle, attestation=attestation)
    subject_ref = behavior_attestation_to_ref(attestation)
    lifecycle_store = open_lifecycle_store(
        mutation_fence=fence_for(tmp_path / "lifecycle"),
        root=tmp_path / "lifecycle",
        authority_handle=handle,
        allow_genesis=True,
    )
    lifecycle_context = LifecycleContext(LIFECYCLE_CONTEXT_V1, LifecycleScope.REVISION, "revision-1")
    head = _append_admitted(lifecycle_store, handle, subject_ref, lifecycle_context)
    target_lifecycle_state = LifecycleState.REVOKED if revoked else lifecycle_state
    if target_lifecycle_state is LifecycleState.REVOKED:
        proposal = create_lifecycle_authority_proposal(
            action=LifecycleAuthorityAction.REVOKE,
            subject_ref=subject_ref,
            replacement_ref=None,
            context=lifecycle_context,
            proposer_identity=ActorIdentity("revocation-proposer"),
            producer_actor_ids=(configuration.lifecycle_writer_actor,),
            source_actor_ids=(ActorIdentity("revocation-source"),),
            evidence_refs=(_ref("revocation-evidence", RefKind.SOURCE_EVIDENCE),),
            compatibility_refs=(),
            policy_refs=(_ref("revocation-policy", RefKind.CONTRACT_CONDITION),),
            reason_codes=("REVOCATION_REQUIRED",),
            predecessor_decision_id=None,
            decision_sequence=1,
        )
        lifecycle_evaluator = configure_lifecycle_authority_evaluator(
            authority_handle=handle,
            policy_version="synapse.stage4.test.lifecycle-policy/v1",
            trusted_clock=lambda: NOW,
        )
        revocation = create_revocation_decision(
            authority_handle=handle,
            evaluator=lifecycle_evaluator,
            proposal=proposal,
            executor_identity=ActorIdentity("lifecycle-executor"),
        )
        lifecycle_store.persist_authority_decision(authority_handle=handle, decision=revocation)
        head = lifecycle_store.append(
            authority_handle=handle,
            subject_ref=subject_ref,
            context=lifecycle_context,
            to_state=LifecycleState.REVOKED,
            reason_code=LifecycleReasonCode.REVOCATION_APPROVED,
            evidence_refs=(_ref("revocation-transition", RefKind.SOURCE_EVIDENCE),),
            expected_predecessor_record_id=head.record_id.value,
            expected_subject_sequence=head.subject_sequence + 1,
            revocation_decision=revocation,
        )
    elif target_lifecycle_state is LifecycleState.SUPERSEDED:
        proposal = create_lifecycle_authority_proposal(
            action=LifecycleAuthorityAction.SUPERSEDE,
            subject_ref=subject_ref,
            replacement_ref=_ref("supersession-replacement", RefKind.ARTIFACT),
            context=lifecycle_context,
            proposer_identity=ActorIdentity("supersession-proposer"),
            producer_actor_ids=(configuration.lifecycle_writer_actor,),
            source_actor_ids=(ActorIdentity("supersession-source"),),
            evidence_refs=(_ref("supersession-evidence", RefKind.SOURCE_EVIDENCE),),
            compatibility_refs=(_ref("supersession-compatibility", RefKind.SOURCE_EVIDENCE),),
            policy_refs=(_ref("supersession-policy", RefKind.CONTRACT_CONDITION),),
            reason_codes=("SUPERSESSION_REQUIRED",),
            predecessor_decision_id=None,
            decision_sequence=1,
        )
        lifecycle_evaluator = configure_lifecycle_authority_evaluator(
            authority_handle=handle,
            policy_version="synapse.stage4.test.lifecycle-policy/v1",
            trusted_clock=lambda: NOW,
        )
        supersession = create_supersession_decision(
            authority_handle=handle,
            evaluator=lifecycle_evaluator,
            proposal=proposal,
            decision_kind=SupersessionDecisionKind.SUPERSEDE,
            executor_identity=ActorIdentity("lifecycle-executor"),
        )
        lifecycle_store.persist_authority_decision(authority_handle=handle, decision=supersession)
        head = lifecycle_store.append(
            authority_handle=handle,
            subject_ref=subject_ref,
            context=lifecycle_context,
            to_state=LifecycleState.SUPERSEDED,
            reason_code=LifecycleReasonCode.SUPERSESSION_APPROVED,
            evidence_refs=(_ref("supersession-transition", RefKind.SOURCE_EVIDENCE),),
            expected_predecessor_record_id=head.record_id.value,
            expected_subject_sequence=head.subject_sequence + 1,
            supersession_decision=supersession,
        )
    elif target_lifecycle_state in (LifecycleState.STALE, LifecycleState.QUARANTINED):
        reason = (
            LifecycleReasonCode.STALE_CONTEXT
            if target_lifecycle_state is LifecycleState.STALE
            else LifecycleReasonCode.CORRUPTION_QUARANTINE
        )
        head = lifecycle_store.append(
            authority_handle=handle,
            subject_ref=subject_ref,
            context=lifecycle_context,
            to_state=target_lifecycle_state,
            reason_code=reason,
            evidence_refs=(_ref(f"{target_lifecycle_state.value.lower()}-transition", RefKind.SOURCE_EVIDENCE),),
            expected_predecessor_record_id=head.record_id.value,
            expected_subject_sequence=head.subject_sequence + 1,
        )
    elif target_lifecycle_state is not None:
        raise ValueError("unsupported harness lifecycle state")

    taint_store = open_taint_history_store(
        mutation_fence=fence_for(tmp_path / "taint"),
        root=tmp_path / "taint",
        authority_handle=handle,
        allow_genesis=True,
    )
    taint_profile = classify_source_taint(
        authority_handle=handle,
        subject_ref=subject_ref,
        taint_classes=(TaintClass.WORKER_GENERATED,),
        producer_actor_ids=(ActorIdentity("candidate-producer"),),
        source_actor_ids=(ActorIdentity("candidate-source"),),
        admission_actor_ids=(ActorIdentity("admission-actor"),),
        consumer_actor_ids=(ActorIdentity("retrieval-consumer"),),
    )
    taint_store.append_profile(authority_handle=handle, profile=taint_profile)
    resolved_support: list[tuple[object, ...]] = []
    for index, (extra_unit, extra_blob, extra_manifest, extra_entry) in enumerate(resolved_objects):
        extra_attestation = attester.attest(
            authority_handle=handle,
            observed=producer_observation,
            subject_content_key=extra_unit.content_key,
            producer_run_id=RunId(f"run-extra-{index:03d}"),
            producer_attempt_id=AttemptId(f"attempt-extra-{index:03d}"),
            producer_actor_ids=(ActorIdentity("candidate-producer"),),
        )
        attestation_store.append(authority_handle=handle, attestation=extra_attestation)
        extra_subject_ref = behavior_attestation_to_ref(extra_attestation)
        extra_head = _append_admitted(lifecycle_store, handle, extra_subject_ref, lifecycle_context)
        extra_taint = classify_source_taint(
            authority_handle=handle,
            subject_ref=extra_subject_ref,
            taint_classes=(TaintClass.WORKER_GENERATED,),
            producer_actor_ids=(ActorIdentity("candidate-producer"),),
            source_actor_ids=(ActorIdentity("candidate-source"),),
            admission_actor_ids=(ActorIdentity("admission-actor"),),
            consumer_actor_ids=(ActorIdentity("retrieval-consumer"),),
        )
        taint_store.append_profile(authority_handle=handle, profile=extra_taint)
        resolved_support.append((extra_unit, extra_blob, extra_manifest, extra_entry, extra_attestation, extra_head, extra_taint))

    context_revision = revision if context_repository_revision is None else context_repository_revision
    context_policy = None if context_policy_version is None else _external(
        ExternalInputKind.POLICY,
        "additional-compatibility-policy",
        context_policy_version,
    )
    context_policy_inputs = (policy,) if context_policy is None else (policy, context_policy)
    context_host = host if context_host_version is None else _external(ExternalInputKind.ENVIRONMENT, "host-abi", context_host_version)
    context_environment = environment if context_environment_version is None else _external(ExternalInputKind.ENVIRONMENT, "runtime-environment", context_environment_version)
    context_compiler = compiler if context_tool_version is None else _external(ExternalInputKind.TOOL, "compiler", context_tool_version)
    context_oracle = oracle if context_repository_revision is None and context_oracle_ref_name is None else OracleObservation(
        ORACLE_OBSERVATION_V1,
        ActorIdentity("trusted-oracle"),
        context_revision,
        task_ref,
        _ref("oracle-result" if context_oracle_ref_name is None else context_oracle_ref_name, RefKind.SOURCE_EVIDENCE),
    )
    observation = producer_observation if (
        context_revision == revision
        and context_policy is None
        and context_host == host
        and context_environment == environment
        and context_compiler == compiler
        and context_oracle == oracle
    ) else attester.observe(
        authority_handle=handle,
        repository_revision=context_revision,
        base_revision=BASE_REVISION,
        task_contract_ref=task_ref,
        policy_inputs=context_policy_inputs,
        environment_inputs=(context_host, context_environment),
        tool_inputs=(context_compiler,),
        source_refs=(_ref("source", RefKind.SOURCE_EVIDENCE),),
        verification_refs=(_ref("verification", RefKind.SOURCE_EVIDENCE),),
        oracle_observation=context_oracle,
    )

    declaration = create_compatibility_evaluator_declaration(
        authority_handle=handle,
        evaluator_identity=AuthorityIdentity("compatibility-evaluator"),
        evaluator_component_id="compatibility-evaluator",
        evaluator_component_version="synapse.stage4.compatibility-evaluator/v1",
        active_policy_input=policy,
        allowed_behavior_kinds=tuple(
            sorted({unit.core.behavior_kind, *extra_allowed_behavior_kinds}, key=lambda item: item.value)
        ),
        allowed_binding_kinds=tuple(sorted({item.binding_kind for item in bindings}, key=lambda item: item.value)),
        allowed_capabilities=unit.core.capability_requirements,
        allowed_scope=tuple(sorted({item.path for item in bindings})) or ("src/a.py",),
        selected_set_ceiling=10,
        trusted_clock=lambda: NOW,
    )
    catalog: dict[str, CompatibilitySubjectEvidence] = {}
    conflict_matrix: dict[tuple[str, str], tuple[ConflictKind | None, tuple[HashBoundRef, ...]]] = {}

    def assess_conflict(context, left_decision, right_decision, left_descriptor, right_descriptor):
        key = tuple(sorted((left_descriptor.descriptor_id.value, right_descriptor.descriptor_id.value)))
        return conflict_matrix.get(key, (None, ()))

    observation_provider = _PlatformObservationProvider(observation)
    evaluator = configure_compatibility_evaluator(
        authority_handle=handle,
        declaration=declaration,
        evaluator_component_id=declaration.evaluator_component_id,
        evaluator_component_version=declaration.evaluator_component_version,
        trusted_clock=lambda: NOW,
        platform_observation_provider=observation_provider,
        library=library,
        lifecycle_store=lifecycle_store,
        attestation_store=attestation_store,
        taint_store=taint_store,
        evidence_resolver=lambda descriptor: catalog[descriptor.descriptor_id.value],
        binding_repo_root=tmp_path if binding_repo_root is None else binding_repo_root,
        conflict_assessor=assess_conflict,
        retriever_actor=ActorIdentity("retriever"),
        consumer_actor=ActorIdentity("retrieval-consumer"),
        score_provider_actor=ActorIdentity("score-provider"),
    )
    library_snapshot = library.current_snapshot().snapshot
    lifecycle_snapshot = lifecycle_store.snapshot()
    context = create_compatibility_context(
        evaluator=evaluator,
        authority_handle=handle,
        observation=observation,
        library_snapshot=library_snapshot,
        lifecycle_snapshot=lifecycle_snapshot,
        consumer_actor=evaluator.consumer_actor,
        allowed_binding_kinds=context_allowed_binding_kinds,
    )
    descriptor = create_compatibility_subject_descriptor(
        unit=unit,
        blob=blob,
        manifest=manifest,
        index_entry=entry,
        attestation=attestation,
        bindings=bindings,
        lifecycle_record=head,
        lifecycle_snapshot=lifecycle_snapshot,
        taint_root_basis=taint_profile,
        taint_history_anchor=taint_store.current_anchor(),
    )
    catalog[descriptor.descriptor_id.value] = create_compatibility_subject_evidence(
        descriptor=descriptor,
        unit=unit,
        blob=blob,
        manifest=manifest,
        index_entry=entry,
        attestation=attestation,
        bindings=bindings,
        taint_root_basis=taint_profile,
        taint_source_profiles=(),
        taint_derivations=(),
        taint_decisions=(),
        lifecycle_record=head,
        lifecycle_snapshot=lifecycle_snapshot,
        lifecycle_context=lifecycle_context,
        taint_history_anchor=taint_store.current_anchor(),
    )
    decision = evaluate_compatibility(
        evaluator=evaluator,
        context=context,
        descriptor=descriptor,
        index_entry=entry,
    )
    extra_candidates: list[tuple[object, ...]] = []
    for extra_unit, extra_blob, extra_manifest, extra_entry, extra_attestation, extra_head, extra_taint in resolved_support:
        extra_descriptor = create_compatibility_subject_descriptor(
            unit=extra_unit,
            blob=extra_blob,
            manifest=extra_manifest,
            index_entry=extra_entry,
            attestation=extra_attestation,
            bindings=(),
            lifecycle_record=extra_head,
            lifecycle_snapshot=lifecycle_snapshot,
            taint_root_basis=extra_taint,
            taint_history_anchor=taint_store.current_anchor(),
        )
        catalog[extra_descriptor.descriptor_id.value] = create_compatibility_subject_evidence(
            descriptor=extra_descriptor,
            unit=extra_unit,
            blob=extra_blob,
            manifest=extra_manifest,
            index_entry=extra_entry,
            attestation=extra_attestation,
            bindings=(),
            taint_root_basis=extra_taint,
            taint_source_profiles=(),
            taint_derivations=(),
            taint_decisions=(),
            lifecycle_record=extra_head,
            lifecycle_snapshot=lifecycle_snapshot,
            lifecycle_context=lifecycle_context,
            taint_history_anchor=taint_store.current_anchor(),
        )
        extra_decision = evaluate_compatibility(
            evaluator=evaluator,
            context=context,
            descriptor=extra_descriptor,
            index_entry=extra_entry,
        )
        extra_candidates.append((extra_unit, extra_blob, extra_manifest, extra_entry, extra_attestation, extra_head, extra_taint, extra_descriptor, extra_decision))
    return _Harness(
        tmp_path,
        handle,
        library,
        publisher,
        lifecycle_store,
        attestation_store,
        taint_store,
        observation,
        attestation,
        taint_profile,
        lifecycle_context,
        unit,
        blob,
        manifest,
        entry,
        head,
        lifecycle_snapshot,
        declaration,
        evaluator,
        context,
        descriptor,
        decision,
        catalog,
        conflict_matrix,
        tuple(extra_candidates),
        attester,
        observation_provider,
    )


def _fresh_platform_observation(
    harness: _Harness,
    *,
    additional_policy_version: str | None = None,
    omit_active_policy: bool = False,
    environment_version: str | None = None,
    tool_version: str | None = None,
    oracle_result_name: str | None = None,
):
    current = harness.observation
    policy_inputs = (
        (
            _external(
                ExternalInputKind.POLICY,
                "replacement-compatibility-policy",
                "synapse.stage4.gold.replacement-policy/v1",
            ),
        )
        if omit_active_policy
        else current.policy_inputs
    )
    if additional_policy_version is not None:
        policy_inputs = (
            *policy_inputs,
            _external(
                ExternalInputKind.POLICY,
                "additional-compatibility-policy",
                additional_policy_version,
            ),
        )
    environment_inputs = tuple(
        _external(item.kind, item.name, environment_version)
        if environment_version is not None and item.name == "runtime-environment"
        else item
        for item in current.environment_inputs
    )
    tool_inputs = tuple(
        _external(item.kind, item.name, tool_version)
        if tool_version is not None and item.name == "compiler"
        else item
        for item in current.tool_inputs
    )
    oracle = current.oracle_observation
    if oracle_result_name is not None:
        oracle = OracleObservation(
            ORACLE_OBSERVATION_V1,
            oracle.oracle_identity,
            oracle.verified_repository_revision,
            oracle.task_contract_ref,
            _ref(oracle_result_name, RefKind.SOURCE_EVIDENCE),
        )
    return harness.attester.observe(
        authority_handle=harness.handle,
        repository_revision=current.repository_revision,
        base_revision=current.base_revision,
        task_contract_ref=current.task_contract_ref,
        policy_inputs=policy_inputs,
        environment_inputs=environment_inputs,
        tool_inputs=tool_inputs,
        source_refs=current.source_refs,
        verification_refs=current.verification_refs,
        oracle_observation=oracle,
    )


def _recomputed_revalidation_with_observation(
    record: CompatibilityRevalidationRecord,
    observation: PlatformObservedProvenance,
) -> CompatibilityRevalidationRecord:
    validate_platform_observed_provenance(
        observation,
        authority_handle=record._evaluator._authority_handle,
    )
    observation_sha256 = hashlib.sha256(
        compatibility_module._canonical(
            compatibility_module._observation_payload(observation)
        )
    ).hexdigest()
    result = object.__new__(CompatibilityRevalidationRecord)
    for name, value in vars(record).items():
        object.__setattr__(result, name, value)
    object.__setattr__(result, "_observation", observation)
    object.__setattr__(result, "observation_sha256", observation_sha256)
    payload = compatibility_module._canonical(
        compatibility_module._revalidation_payload(result)
    )
    object.__setattr__(
        result,
        "revalidation_id",
        compute_record_id(
            domain=IdentityDomain.COMPATIBILITY_REVALIDATION,
            canonical_bytes=payload,
        ),
    )
    return result


@pytest.fixture
def _shared_harness(tmp_path_factory):
    return _make_harness(tmp_path_factory.mktemp("stage4-patch6-shared-compatibility"))


def _retriever_for_harness(harness: _Harness, *, scorer, required_binding_targets=()):
    provider = configure_ranking_feature_provider(
        component_id="corrective-score-provider",
        component_version="synapse.stage4.corrective-score-provider/v1",
        scoring_profile=RANKING_PROFILE_V1,
        scorer=scorer,
        input_ref_resolver=lambda query_id, descriptor_id: _ref("corrective-score-input", RefKind.ARTIFACT),
        actor_identity=harness.evaluator.score_provider_actor,
    )
    descriptor_by_key = {harness.entry.content_key: harness.descriptor}
    descriptor_by_key.update({item[3].content_key: item[7] for item in harness.extra_candidates})
    retriever = configure_retriever(
        authority_handle=harness.handle,
        evaluator=harness.evaluator,
        evaluator_declaration=harness.declaration,
        retrieval_policy=RETRIEVAL_POLICY_V1,
        trusted_clock=lambda: NOW,
        descriptor_resolver=lambda entry: descriptor_by_key[entry.content_key],
        conflict_proposal_resolver=lambda context, decisions, descriptors: (),
        ranking_provider=provider,
        library=harness.library,
    )
    query = create_retrieval_query(
        retriever=retriever,
        context=harness.context,
        requested_behavior_kinds=(harness.unit.core.behavior_kind,),
        required_binding_targets=required_binding_targets,
        selected_set_limit=1,
    )
    return retriever, query


def test_s4_p6_acc_compat_evidence_01_compatible_requires_complete_evidence_and_all_dimensions(_shared_harness) -> None:
    harness = _shared_harness
    validate_compatibility_decision(
        harness.decision,
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
    )
    assert harness.decision.decision_kind is CompatibilityDecisionKind.COMPATIBLE
    assert harness.decision.evidence.completeness is EvidenceCompleteness.COMPLETE
    assert tuple(item.dimension for item in harness.decision.evidence.dimensions) == tuple(CompatibilityDimension)
    assert len(harness.decision.evidence.dimensions) == 12
    assert all(item.result is DimensionResult.PASS for item in harness.decision.evidence.dimensions)
    assert harness.decision.evidence.authority_decision_id == harness.decision.decision_id
    assert harness.decision.independence_proof.authority_role is AuthorityRole.COMPATIBILITY_EVALUATOR
    assert harness.decision.independence_proof.reason_code is ReasonCode.COMPATIBILITY_EVALUATION_INDEPENDENT

    incomplete_evidence = object.__new__(type(harness.decision.evidence))
    for name, field_value in vars(harness.decision.evidence).items():
        object.__setattr__(incomplete_evidence, name, field_value)
    object.__setattr__(
        incomplete_evidence,
        "dimensions",
        harness.decision.evidence.dimensions[:-1],
    )
    incomplete_decision = object.__new__(type(harness.decision))
    for name, field_value in vars(harness.decision).items():
        object.__setattr__(incomplete_decision, name, field_value)
    object.__setattr__(incomplete_decision, "evidence", incomplete_evidence)
    with pytest.raises(CompatibilityViolation) as exc:
        validate_compatibility_decision(
            incomplete_decision,
            evaluator=harness.evaluator,
            context=harness.context,
            descriptor=harness.descriptor,
        )
    assert exc.value.failure_code is CompatibilityFailureCode.DIMENSION_MISSING


def test_s4_p6_acc_compat_toctou_01_state_drift_after_ranking_blocks_loading(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    assert harness.decision.decision_kind is CompatibilityDecisionKind.COMPATIBLE
    harness.publish_extra()
    result = revalidate_before_loading(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
    )
    assert result.stage is RevalidationStage.BEFORE_LOADING
    assert result.outcome is RevalidationOutcome.FAILED
    assert result.failure_code is CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED
    assert result.cause_code is CompatibilityFailureCode.SNAPSHOT_DRIFT
    assert result.observation_sha256 == harness.context.observation_sha256
    assert result.prior_revalidation_id is None


def test_s4_p6_acc_compat_toctou_02_state_drift_after_loading_blocks_consumption(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    before_loading = revalidate_before_loading(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
    )
    assert before_loading.outcome is RevalidationOutcome.PASSED
    harness.publish_extra()
    result = revalidate_before_consumption(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
        before_loading=before_loading,
    )
    assert result.stage is RevalidationStage.BEFORE_CONSUMPTION
    assert result.prior_revalidation_id == before_loading.revalidation_id
    assert result.outcome is RevalidationOutcome.FAILED
    assert result.failure_code is CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED
    assert result.cause_code is CompatibilityFailureCode.SNAPSHOT_DRIFT
    assert result.observation_sha256 == harness.context.observation_sha256


def test_s4_p6_acc_compat_context_01_is_observation_derived_and_exact_capability_bound(_shared_harness) -> None:
    harness = _shared_harness
    validate_compatibility_context(harness.context, evaluator=harness.evaluator)
    assert harness.context.repository_revision == harness.observation.repository_revision
    assert harness.context.policy_inputs == harness.observation.policy_inputs
    assert harness.context.environment_inputs == harness.observation.environment_inputs
    assert harness.context.tool_inputs == harness.observation.tool_inputs
    equal_handle = create_stage4_authority_handle(harness.handle.configuration)
    assert equal_handle.configuration_id == harness.handle.configuration_id
    assert equal_handle is not harness.handle
    with pytest.raises((CompatibilityViolation, ValueError)):
        create_compatibility_context(
            evaluator=harness.evaluator,
            authority_handle=equal_handle,
            observation=harness.observation,
            library_snapshot=harness.context.library_snapshot,
            lifecycle_snapshot=harness.context.lifecycle_snapshot,
            consumer_actor=harness.evaluator.consumer_actor,
        )


def test_s4_p6_acc_compat_descriptor_01_strict_transport_and_nested_tamper_fail_closed(_shared_harness) -> None:
    harness = _shared_harness
    raw = harness.descriptor.to_dict()
    assert "payload" not in raw
    assert "program" not in raw
    forged = _forged_descriptor(harness.descriptor)
    object.__setattr__(forged, "program_sha256", "0" * 64)
    with pytest.raises(CompatibilityViolation):
        validate_compatibility_subject_descriptor(forged)


@pytest.mark.parametrize(
    "raw_argument",
    (
        "repository_revision",
        "allowed_scope",
    ),
)
def test_s4_p6_acc_compat_dimensions_01_substitutions_are_typed(
    _shared_harness,
    raw_argument: str,
) -> None:
    harness = _shared_harness
    kwargs = {
        "unit": harness.unit,
        "blob": harness.blob,
        "manifest": harness.manifest,
        "index_entry": harness.entry,
        "attestation": harness.attestation,
        "bindings": (),
        "lifecycle_record": harness.lifecycle_record,
        "lifecycle_snapshot": harness.lifecycle_snapshot,
        "taint_root_basis": harness.taint_profile,
        "taint_history_anchor": harness.taint_store.current_anchor(),
        raw_argument: RepositoryRevision.git_commit("3" * 40) if raw_argument == "repository_revision" else ("src/b.py",),
    }
    with pytest.raises(TypeError):
        create_compatibility_subject_descriptor(**kwargs)


def test_s4_p6_acc_compat_lifecycle_01_revoked_remains_distinct(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path, revoked=True)
    assert harness.decision.decision_kind is CompatibilityDecisionKind.REVOKED
    lifecycle_dimension = next(
        item for item in harness.decision.evidence.dimensions if item.dimension is CompatibilityDimension.LIFECYCLE
    )
    assert lifecycle_dimension.result is DimensionResult.FAIL
    assert harness.decision.decision_kind is not CompatibilityDecisionKind.STALE
    assert harness.decision.decision_kind is not CompatibilityDecisionKind.QUARANTINED


def test_s4_p6_acc_compat_conflict_01_proposals_are_non_authoritative_and_full_scan_blocks(tmp_path: Path) -> None:
    first = _make_harness(tmp_path, extra_unresolved=1)
    scan = evaluate_conflicts(
        evaluator=first.evaluator,
        context=first.context,
        decisions=(first.decision,),
        descriptors=(first.descriptor,),
        considered_index_entries=first.library.search_index(),
        proposals=(),
    )
    assert scan.decision_kind is ConflictDecisionKind.SCAN_INCOMPLETE
    assert scan.request.incomplete_candidate_keys


def test_s4_p6_acc_compat_conflict_02_no_conflict_scan_is_authority_bound(_shared_harness) -> None:
    harness = _shared_harness
    scan = evaluate_conflicts(
        evaluator=harness.evaluator,
        context=harness.context,
        decisions=(harness.decision,),
        descriptors=(harness.descriptor,),
        considered_index_entries=(harness.entry,),
        proposals=(),
    )
    assert scan.decision_kind is ConflictDecisionKind.NO_CONFLICT_FOUND
    assert scan.independence_proof.authority_identity == harness.declaration.evaluator_identity
    assert scan.request.compatible_candidate_ids == (harness.descriptor.descriptor_id,)


def test_s4_p6_acc_compat_absence_01_boolean_is_not_evidence(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    with pytest.raises((TypeError, CompatibilityViolation, AttributeError)):
        validate_compatibility_decision(True, evaluator=harness.evaluator)
    with pytest.raises((TypeError, CompatibilityViolation, AttributeError)):
        harness.catalog[harness.descriptor.descriptor_id.value] = True  # type: ignore[assignment]
        evaluate_compatibility(
            evaluator=harness.evaluator,
            context=harness.context,
            descriptor=harness.descriptor,
            index_entry=harness.entry,
        )


def test_s4_p6_followup_descriptor_01_fields_are_derived_from_manifest_attestation_and_supporting_records(_shared_harness) -> None:
    harness = _shared_harness
    descriptor = harness.descriptor
    assert descriptor.content_key == harness.unit.content_key
    assert descriptor.manifest_id == harness.manifest.manifest_id
    assert descriptor.repository_revision == harness.attestation.repository_revision
    assert descriptor.task_contract_ref == harness.attestation.task_contract_ref
    assert descriptor.policy_inputs == harness.attestation.policy_inputs
    assert descriptor.environment_inputs == harness.attestation.environment_inputs
    assert descriptor.tool_inputs == harness.attestation.tool_inputs
    assert descriptor.oracle_binding == harness.attestation.oracle_observation
    with pytest.raises(TypeError):
        create_compatibility_subject_descriptor(
            unit=harness.unit,
            blob=harness.blob,
            manifest=harness.manifest,
            index_entry=harness.entry,
            attestation=harness.attestation,
            bindings=(),
            lifecycle_record=harness.lifecycle_record,
            lifecycle_snapshot=harness.lifecycle_snapshot,
            taint_root_basis=harness.taint_profile,
            taint_history_anchor=harness.taint_store.current_anchor(),
            repository_revision=RepositoryRevision.git_commit("3" * 40),
        )
    other_unit, other_blob, other_manifest = _behavior("substituted")
    with pytest.raises(CompatibilityViolation):
        create_compatibility_subject_descriptor(
            unit=other_unit,
            blob=other_blob,
            manifest=other_manifest,
            index_entry=harness.entry,
            attestation=harness.attestation,
            bindings=(),
            lifecycle_record=harness.lifecycle_record,
            lifecycle_snapshot=harness.lifecycle_snapshot,
            taint_root_basis=harness.taint_profile,
            taint_history_anchor=harness.taint_store.current_anchor(),
        )


def test_s4_p6_followup_program_01_manifest_compiler_binding_controls_program_identity(tmp_path: Path, _shared_harness) -> None:
    harness = _shared_harness
    compiler_binding = harness.manifest.compiler_binding
    assert compiler_binding is not None
    assert harness.descriptor.program_sha256 == compiler_binding.actual_program_hash
    assert harness.descriptor.compiler_version == compiler_binding.compiler_identity
    assert harness.descriptor.compiler_target == compiler_binding.compiler_target
    assert harness.descriptor.bytecode_version == compiler_binding.bytecode_version
    forged = _forged_descriptor(harness.descriptor)
    object.__setattr__(forged, "program_sha256", "0" * 64)
    with pytest.raises(CompatibilityViolation):
        validate_compatibility_subject_descriptor(forged)
    no_compiler = _make_harness(tmp_path / "missing-compiler", with_compiler_binding=False)
    assert no_compiler.descriptor.program_sha256 is None
    assert no_compiler.decision.decision_kind is CompatibilityDecisionKind.INSUFFICIENT_COMPATIBILITY_EVIDENCE


def test_s4_p6_followup_bindings_01_present_invalid_and_missing_bindings_have_distinct_results(tmp_path: Path) -> None:
    repo, binding = _python_binding_repo(tmp_path)
    harness = _make_harness(tmp_path / "harness", bindings=(binding,), binding_repo_root=repo)
    assert harness.decision.decision_kind is CompatibilityDecisionKind.COMPATIBLE
    missing = create_compatibility_subject_evidence(
        descriptor=harness.descriptor,
        unit=harness.unit,
        blob=harness.blob,
        manifest=harness.manifest,
        index_entry=harness.entry,
        attestation=harness.attestation,
        bindings=(),
        taint_root_basis=harness.taint_profile,
        taint_source_profiles=(),
        taint_derivations=(),
        taint_decisions=(),
        lifecycle_record=harness.lifecycle_record,
        lifecycle_snapshot=harness.lifecycle_snapshot,
        lifecycle_context=harness.lifecycle_context,
        taint_history_anchor=harness.taint_store.current_anchor(),
    )
    harness.catalog[harness.descriptor.descriptor_id.value] = missing
    missing_decision = evaluate_compatibility(evaluator=harness.evaluator, context=harness.context, descriptor=harness.descriptor, index_entry=harness.entry)
    assert missing_decision.decision_kind is CompatibilityDecisionKind.INSUFFICIENT_COMPATIBILITY_EVIDENCE
    invalid_binding = _other_python_binding(repo, binding.repository_revision)
    present_invalid = create_compatibility_subject_evidence(
        descriptor=harness.descriptor,
        unit=harness.unit,
        blob=harness.blob,
        manifest=harness.manifest,
        index_entry=harness.entry,
        attestation=harness.attestation,
        bindings=(invalid_binding,),
        taint_root_basis=harness.taint_profile,
        taint_source_profiles=(),
        taint_derivations=(),
        taint_decisions=(),
        lifecycle_record=harness.lifecycle_record,
        lifecycle_snapshot=harness.lifecycle_snapshot,
        lifecycle_context=harness.lifecycle_context,
        taint_history_anchor=harness.taint_store.current_anchor(),
    )
    harness.catalog[harness.descriptor.descriptor_id.value] = present_invalid
    invalid_decision = evaluate_compatibility(evaluator=harness.evaluator, context=harness.context, descriptor=harness.descriptor, index_entry=harness.entry)
    assert invalid_decision.decision_kind is CompatibilityDecisionKind.INCOMPATIBLE_BINDING
    binding_dimension = next(item for item in invalid_decision.evidence.dimensions if item.dimension is CompatibilityDimension.BINDINGS)
    assert binding_dimension.reason is CompatibilityReason.BINDING_INVALID


def test_s4_p6_followup_bindings_02_stage2_and_stage3_consume_exact_repository_bindings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, binding = _python_binding_repo(tmp_path)
    harness = _make_harness(tmp_path / "harness", bindings=(binding,), binding_repo_root=repo)
    calls: list[str] = []
    original = compatibility_module.consume_python_binding

    def observed(repo_root, candidate, *, repository_revision):
        calls.append(candidate.binding_id.value)
        if len(calls) == 2:
            raise ValueError("simulated repository drift")
        return original(repo_root, candidate, repository_revision=repository_revision)

    monkeypatch.setattr(compatibility_module, "consume_python_binding", observed)
    stage2 = revalidate_before_loading(evaluator=harness.evaluator, context=harness.context, descriptor=harness.descriptor, original_decision=harness.decision)
    assert stage2.outcome is RevalidationOutcome.PASSED
    assert calls == [binding.binding_id.value]
    stage3 = revalidate_before_consumption(evaluator=harness.evaluator, context=harness.context, descriptor=harness.descriptor, original_decision=harness.decision, before_loading=stage2)
    assert calls == [binding.binding_id.value, binding.binding_id.value]
    assert stage3.outcome is RevalidationOutcome.FAILED
    assert stage3.failure_code is CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED
    assert stage3.cause_code is CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED
    assert stage3.observation_sha256 == harness.context.observation_sha256


def test_s4_p6_followup_dimensions_01_environment_and_toolchain_map_to_distinct_decisions(tmp_path: Path) -> None:
    environment = _make_harness(
        tmp_path / "environment",
        context_environment_version="synapse.stage4.environment/v999",
    )
    toolchain = _make_harness(tmp_path / "toolchain", context_tool_version="synapse.other-compiler/v9")
    assert environment.decision.decision_kind is CompatibilityDecisionKind.INCOMPATIBLE_ENVIRONMENT
    assert toolchain.decision.decision_kind is CompatibilityDecisionKind.INCOMPATIBLE_TOOLCHAIN
    environment_dimension = next(item for item in environment.decision.evidence.dimensions if item.dimension is CompatibilityDimension.ENVIRONMENT_AND_TOOLCHAIN)
    tool_dimension = next(item for item in toolchain.decision.evidence.dimensions if item.dimension is CompatibilityDimension.ENVIRONMENT_AND_TOOLCHAIN)
    assert environment_dimension.reason is CompatibilityReason.ENVIRONMENT_MISMATCH
    assert tool_dimension.reason is CompatibilityReason.TOOLCHAIN_MISMATCH


def test_s4_p6_followup_absence_01_all_typed_states_remain_distinct_and_fail_closed(tmp_path: Path) -> None:
    values = (
        absent_compatibility_value(CompatibilityValueState.MISSING),
        absent_compatibility_value(CompatibilityValueState.UNAVAILABLE),
        absent_compatibility_value(CompatibilityValueState.UNKNOWN),
        absent_compatibility_value(CompatibilityValueState.NOT_APPLICABLE),
        compatibility_value(label="empty", exact_value=[]),
        compatibility_value(label="null", exact_value=None),
        compatibility_value(label="false", exact_value=False),
        compatibility_value(label="zero", exact_value=0),
        absent_compatibility_value(CompatibilityValueState.REDACTED),
    )
    assert tuple(item.state for item in values) == (
        CompatibilityValueState.MISSING,
        CompatibilityValueState.UNAVAILABLE,
        CompatibilityValueState.UNKNOWN,
        CompatibilityValueState.NOT_APPLICABLE,
        CompatibilityValueState.EMPTY,
        CompatibilityValueState.NULL,
        CompatibilityValueState.FALSE,
        CompatibilityValueState.ZERO,
        CompatibilityValueState.REDACTED,
    )
    assert len({item.to_dict()["state"] for item in values}) == 9
    missing_compiler = _make_harness(tmp_path / "required-missing", with_compiler_binding=False)
    compiler_dimension = next(item for item in missing_compiler.decision.evidence.dimensions if item.dimension is CompatibilityDimension.CANONICALIZATION_AND_COMPILER)
    assert compiler_dimension.producer_value.state is CompatibilityValueState.MISSING
    assert compiler_dimension.result is DimensionResult.FAIL
    assert missing_compiler.decision.decision_kind is CompatibilityDecisionKind.INSUFFICIENT_COMPATIBILITY_EVIDENCE


def test_s4_p6_corrective_authority_01_oracle_evaluator_overlap_is_rejected_and_never_filtered(tmp_path: Path) -> None:
    control = _make_harness(tmp_path / "control")
    assert control.decision.decision_kind is CompatibilityDecisionKind.COMPATIBLE
    assert control.context.oracle_observation.oracle_identity.value == "trusted-oracle"

    with pytest.raises(CompatibilityViolation) as exc:
        _make_harness(
            tmp_path / "overlap",
            oracle_actor_identity=ActorIdentity("compatibility-evaluator"),
        )
    assert exc.value.failure_code is CompatibilityFailureCode.EVALUATOR_NOT_INDEPENDENT


def test_s4_p6_corrective_bindings_03_partial_required_binding_set_is_typed_missing_evidence(tmp_path: Path) -> None:
    repo, first = _python_binding_repo(tmp_path)
    second = _other_python_binding(repo, first.repository_revision)
    bindings = tuple(sorted((first, second), key=lambda item: binding_to_ref(item).ref_id))
    harness = _make_harness(
        tmp_path / "harness",
        bindings=bindings,
        binding_repo_root=repo,
    )
    assert harness.decision.decision_kind is CompatibilityDecisionKind.COMPATIBLE
    partial = create_compatibility_subject_evidence(
        descriptor=harness.descriptor,
        unit=harness.unit,
        blob=harness.blob,
        manifest=harness.manifest,
        index_entry=harness.entry,
        attestation=harness.attestation,
        bindings=(bindings[0],),
        taint_root_basis=harness.taint_profile,
        taint_source_profiles=(),
        taint_derivations=(),
        taint_decisions=(),
        lifecycle_record=harness.lifecycle_record,
        lifecycle_snapshot=harness.lifecycle_snapshot,
        lifecycle_context=harness.lifecycle_context,
        taint_history_anchor=harness.taint_store.current_anchor(),
    )
    harness.catalog[harness.descriptor.descriptor_id.value] = partial

    decision = evaluate_compatibility(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        index_entry=harness.entry,
    )
    binding_dimension = next(
        item for item in decision.evidence.dimensions
        if item.dimension is CompatibilityDimension.BINDINGS
    )
    assert binding_dimension.result is DimensionResult.FAIL
    assert binding_dimension.reason is CompatibilityReason.REQUIRED_EVIDENCE_MISSING
    assert decision.evidence.completeness is EvidenceCompleteness.INCOMPLETE
    assert decision.decision_kind is CompatibilityDecisionKind.INSUFFICIENT_COMPATIBILITY_EVIDENCE


def test_s4_p6_corrective_dimensions_02_observed_host_abi_controls_only_host_dimension(tmp_path: Path) -> None:
    matched = _make_harness(tmp_path / "matched")
    host = _make_harness(
        tmp_path / "host",
        context_host_version="synapse.stage4.host-abi/v999",
    )
    environment = _make_harness(
        tmp_path / "environment",
        context_environment_version="synapse.stage4.environment/v999",
    )
    toolchain = _make_harness(
        tmp_path / "toolchain",
        context_tool_version="synapse.other-compiler/v9",
    )
    assert matched.decision.decision_kind is CompatibilityDecisionKind.COMPATIBLE
    assert host.decision.decision_kind is CompatibilityDecisionKind.INCOMPATIBLE_HOST_ABI
    assert environment.decision.decision_kind is CompatibilityDecisionKind.INCOMPATIBLE_ENVIRONMENT
    assert toolchain.decision.decision_kind is CompatibilityDecisionKind.INCOMPATIBLE_TOOLCHAIN

    host_dimension = next(item for item in host.decision.evidence.dimensions if item.dimension is CompatibilityDimension.HOST_ABI_AND_CAPABILITIES)
    host_environment_dimension = next(item for item in host.decision.evidence.dimensions if item.dimension is CompatibilityDimension.ENVIRONMENT_AND_TOOLCHAIN)
    environment_host_dimension = next(item for item in environment.decision.evidence.dimensions if item.dimension is CompatibilityDimension.HOST_ABI_AND_CAPABILITIES)
    environment_dimension = next(item for item in environment.decision.evidence.dimensions if item.dimension is CompatibilityDimension.ENVIRONMENT_AND_TOOLCHAIN)
    tool_host_dimension = next(item for item in toolchain.decision.evidence.dimensions if item.dimension is CompatibilityDimension.HOST_ABI_AND_CAPABILITIES)
    tool_dimension = next(item for item in toolchain.decision.evidence.dimensions if item.dimension is CompatibilityDimension.ENVIRONMENT_AND_TOOLCHAIN)
    assert host_dimension.reason is CompatibilityReason.VALUE_MISMATCH
    assert host_environment_dimension.result is DimensionResult.PASS
    assert environment_host_dimension.result is DimensionResult.PASS
    assert environment_dimension.reason is CompatibilityReason.ENVIRONMENT_MISMATCH
    assert tool_host_dimension.result is DimensionResult.PASS
    assert tool_dimension.reason is CompatibilityReason.TOOLCHAIN_MISMATCH
    producer_host = next(item for item in host.descriptor.environment_inputs if item.name == "host-abi")
    consumer_host = next(item for item in host.context.environment_inputs if item.name == "host-abi")
    assert producer_host.ref in host_dimension.producer_value.refs
    assert consumer_host.ref in host_dimension.consumer_value.refs


def test_s4_p6_corrective_context_02_disallowed_binding_kind_never_becomes_eligible(tmp_path: Path) -> None:
    repo, binding = _python_binding_repo(tmp_path)
    harness = _make_harness(
        tmp_path / "harness",
        bindings=(binding,),
        binding_repo_root=repo,
        context_allowed_binding_kinds=(),
    )
    assert harness.declaration.allowed_binding_kinds == (BindingKind.PYTHON,)
    assert harness.context.allowed_binding_kinds == ()
    assert harness.decision.decision_kind is CompatibilityDecisionKind.INCOMPATIBLE_BINDING
    score_calls: list[str] = []

    def scorer(query_id, descriptor_id, score_input):
        score_calls.append(descriptor_id.value)
        return 500_000

    retriever, query = _retriever_for_harness(harness, scorer=scorer)
    result = _retrieve_all(retriever=retriever, context=harness.context, query=query)
    candidate = result.decision.considered_candidates[0]
    assert candidate.disposition is CandidateDisposition.REJECTED
    assert candidate.ranking_feature_id is None
    assert candidate.ranking_key is None
    assert result.decision.selected_candidate_ids == ()
    assert score_calls == []

    with pytest.raises(RetrievalViolation) as exc:
        create_retrieval_query(
            retriever=retriever,
            context=harness.context,
            requested_behavior_kinds=(harness.unit.core.behavior_kind,),
            required_binding_targets=(binding,),
            selected_set_limit=1,
        )
    assert exc.value.failure_code is RetrievalFailureCode.MALFORMED_QUERY


def test_s4_p6_corrective_bindings_04_mixed_repository_revision_is_rejected_before_scoring(tmp_path: Path) -> None:
    repo, first = _python_binding_repo(tmp_path)
    second = _python_binding_from_second_revision(repo)
    assert first.repository_revision != second.repository_revision
    bindings = tuple(sorted((first, second), key=lambda item: binding_to_ref(item).ref_id))
    harness = _make_harness(
        tmp_path / "harness",
        bindings=bindings,
        binding_repo_root=repo,
        producer_repository_revision=first.repository_revision,
    )
    assert harness.descriptor.repository_revision == first.repository_revision
    assert harness.decision.decision_kind is CompatibilityDecisionKind.INCOMPATIBLE_BINDING
    score_calls: list[str] = []

    def scorer(query_id, descriptor_id, score_input):
        score_calls.append(descriptor_id.value)
        return 500_000

    retriever, query = _retriever_for_harness(harness, scorer=scorer)
    result = _retrieve_all(retriever=retriever, context=harness.context, query=query)
    candidate = result.decision.considered_candidates[0]
    assert candidate.compatibility_kind is CompatibilityDecisionKind.INCOMPATIBLE_BINDING
    assert candidate.disposition is CandidateDisposition.REJECTED
    assert candidate.ranking_feature_id is None
    assert score_calls == []
    assert result.load_decisions == ()


def test_s4_p6_corrective_toctou_03_stage3_requires_exact_stage2_chain(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path, extra_resolved=1)
    extra_descriptor = harness.extra_candidates[0][7]
    extra_decision = harness.extra_candidates[0][8]
    main_stage2 = revalidate_before_loading(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
    )
    extra_stage2 = revalidate_before_loading(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=extra_descriptor,
        original_decision=extra_decision,
    )
    assert main_stage2.outcome is RevalidationOutcome.PASSED
    assert extra_stage2.outcome is RevalidationOutcome.PASSED
    main_stage3 = revalidate_before_consumption(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
        before_loading=main_stage2,
    )
    extra_stage3 = revalidate_before_consumption(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=extra_descriptor,
        original_decision=extra_decision,
        before_loading=extra_stage2,
    )
    assert main_stage3.outcome is RevalidationOutcome.PASSED
    assert extra_stage3.outcome is RevalidationOutcome.PASSED
    assert main_stage3.prior_revalidation_id == main_stage2.revalidation_id
    assert extra_stage3.prior_revalidation_id == extra_stage2.revalidation_id

    with pytest.raises(CompatibilityViolation) as exc:
        revalidate_before_consumption(
            evaluator=harness.evaluator,
            context=harness.context,
            descriptor=harness.descriptor,
            original_decision=harness.decision,
            before_loading=extra_stage2,
        )
    assert exc.value.failure_code is CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED


def test_s4_p6_corrective_observation_01_stage2_reads_fresh_authority_bound_platform_observation(tmp_path: Path) -> None:
    cases = (
        (
            "policy",
            {"additional_policy_version": "synapse.stage4.gold.alternate-policy/v1"},
        ),
        (
            "environment",
            {"environment_version": "synapse.stage4.environment/v999"},
        ),
        (
            "toolchain",
            {"tool_version": "synapse.stage4.compiler/v999"},
        ),
        (
            "oracle",
            {"oracle_result_name": "alternate-oracle-result"},
        ),
        (
            "missing-active-policy",
            {"omit_active_policy": True},
        ),
    )
    for name, changes in cases:
        harness = _make_harness(tmp_path / name)
        fresh_observation = _fresh_platform_observation(harness, **changes)
        harness.observation_provider.observation = fresh_observation
        calls_before = harness.observation_provider.calls

        result = revalidate_before_loading(
            evaluator=harness.evaluator,
            context=harness.context,
            descriptor=harness.descriptor,
            original_decision=harness.decision,
        )

        assert harness.observation_provider.calls == calls_before + 1
        assert result.stage is RevalidationStage.BEFORE_LOADING
        assert result.context_id == harness.context.context_id
        assert result.outcome is RevalidationOutcome.FAILED
        assert result.failure_code is CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED
        assert result.cause_code is CompatibilityFailureCode.CONTEXT_MISMATCH
        assert result.observation_sha256 is not None
        assert result.observation_sha256 != harness.context.observation_sha256
        assert result._observation is fresh_observation
        validate_compatibility_revalidation_record(result)


def test_s4_p6_corrective_observation_02_stage3_observes_again_and_links_exact_stage2(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    stage2 = revalidate_before_loading(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
    )
    assert stage2.outcome is RevalidationOutcome.PASSED
    assert harness.observation_provider.calls == 1

    fresh_observation = _fresh_platform_observation(
        harness,
        environment_version="synapse.stage4.environment/v999",
    )
    harness.observation_provider.observation = fresh_observation
    stage3 = revalidate_before_consumption(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
        before_loading=stage2,
    )

    assert harness.observation_provider.calls == 2
    assert stage3.stage is RevalidationStage.BEFORE_CONSUMPTION
    assert stage3.prior_revalidation_id == stage2.revalidation_id
    assert stage3.context_id == harness.context.context_id
    assert stage3.outcome is RevalidationOutcome.FAILED
    assert stage3.failure_code is CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED
    assert stage3.cause_code is CompatibilityFailureCode.CONTEXT_MISMATCH
    assert stage3.observation_sha256 is not None
    assert stage3.observation_sha256 != stage2.observation_sha256
    assert stage3._observation is fresh_observation
    validate_compatibility_revalidation_record(stage3)


def test_s4_p6_corrective_observation_03_wrong_handle_and_provider_substitution_fail_closed(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    equal_handle = create_stage4_authority_handle(harness.handle.configuration)
    wrong_attester = configure_platform_attester(
        authority_handle=equal_handle,
        builder_runtime_identity=BuilderRuntimeIdentity(
            BUILDER_RUNTIME_IDENTITY_V1,
            equal_handle.configuration.builder_actor,
            harness.observation.repository_revision,
            _ref("equal-handle-builder", RefKind.ARTIFACT),
            "synapse.stage4.test.equal-handle-builder/v1",
        ),
        trusted_clock=lambda: NOW,
    )
    wrong_observation = wrong_attester.observe(
        authority_handle=equal_handle,
        repository_revision=harness.observation.repository_revision,
        base_revision=harness.observation.base_revision,
        task_contract_ref=harness.observation.task_contract_ref,
        policy_inputs=harness.observation.policy_inputs,
        environment_inputs=harness.observation.environment_inputs,
        tool_inputs=harness.observation.tool_inputs,
        source_refs=harness.observation.source_refs,
        verification_refs=harness.observation.verification_refs,
        oracle_observation=harness.observation.oracle_observation,
    )
    harness.observation_provider.observation = wrong_observation

    result = revalidate_before_loading(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
    )
    assert result.outcome is RevalidationOutcome.FAILED
    assert result.failure_code is CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED
    assert result.cause_code is CompatibilityFailureCode.OBSERVATION_AUTHORITY_MISMATCH
    assert result.observation_sha256 is None
    assert result._observation is None

    object.__setattr__(
        harness.evaluator,
        "_platform_observation_provider",
        lambda: harness.observation,
    )
    with pytest.raises(CompatibilityViolation) as exc:
        require_configured_compatibility_evaluator(harness.evaluator)
    assert exc.value.failure_code is CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH


def test_s4_p6_corrective_observation_04_revalidation_identity_binds_fresh_observation(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    first_observation = _fresh_platform_observation(
        harness,
        additional_policy_version="synapse.stage4.gold.alternate-policy/v1",
    )
    harness.observation_provider.observation = first_observation
    first = revalidate_before_loading(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
    )
    second_observation = _fresh_platform_observation(
        harness,
        tool_version="synapse.stage4.compiler/v999",
    )
    harness.observation_provider.observation = second_observation
    second = revalidate_before_loading(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
    )

    assert first.observation_sha256 != second.observation_sha256
    assert first.revalidation_id != second.revalidation_id
    validate_compatibility_revalidation_record(first)
    validate_compatibility_revalidation_record(second)

    forged_observation = object.__new__(type(first))
    for name, field_value in vars(first).items():
        object.__setattr__(forged_observation, name, field_value)
    object.__setattr__(forged_observation, "_observation", second_observation)
    with pytest.raises(CompatibilityViolation):
        validate_compatibility_revalidation_record(forged_observation)

    forged_digest = object.__new__(type(first))
    for name, field_value in vars(first).items():
        object.__setattr__(forged_digest, name, field_value)
    object.__setattr__(forged_digest, "_observation", second_observation)
    object.__setattr__(forged_digest, "observation_sha256", second.observation_sha256)
    with pytest.raises(CompatibilityViolation):
        validate_compatibility_revalidation_record(forged_digest)


def test_s4_p6_corrective_context_chain_01_recomputed_identity_cannot_detach_passing_observation_from_context(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    stage2 = revalidate_before_loading(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
    )
    assert stage2.outcome is RevalidationOutcome.PASSED
    assert stage2._context is harness.context
    validate_compatibility_revalidation_record(stage2)

    different_observation = _fresh_platform_observation(
        harness,
        tool_version="synapse.stage4.compiler/v999",
    )
    validate_platform_observed_provenance(
        different_observation,
        authority_handle=harness.handle,
    )
    forged = _recomputed_revalidation_with_observation(
        stage2,
        different_observation,
    )
    assert forged._context is harness.context
    assert forged._observation is different_observation
    assert forged.observation_sha256 != harness.context.observation_sha256
    assert forged.revalidation_id != stage2.revalidation_id

    with pytest.raises(CompatibilityViolation) as detached:
        validate_compatibility_revalidation_record(forged)
    assert detached.value.failure_code is CompatibilityFailureCode.CONTEXT_MISMATCH

    missing_context = object.__new__(CompatibilityRevalidationRecord)
    for name, value in vars(stage2).items():
        if name != "_context":
            object.__setattr__(missing_context, name, value)
    with pytest.raises(CompatibilityViolation) as missing:
        validate_compatibility_revalidation_record(missing_context)
    assert missing.value.failure_code is CompatibilityFailureCode.CONTEXT_MISMATCH


def test_s4_p6_corrective_context_chain_04_equal_context_clone_is_rejected_on_stage3_boundary(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    second_context = create_compatibility_context(
        evaluator=harness.evaluator,
        authority_handle=harness.handle,
        observation=harness.observation,
        library_snapshot=harness.context.library_snapshot,
        lifecycle_snapshot=harness.context.lifecycle_snapshot,
        consumer_actor=harness.evaluator.consumer_actor,
        allowed_behavior_kinds=harness.context.allowed_behavior_kinds,
        allowed_binding_kinds=harness.context.allowed_binding_kinds,
        allowed_capabilities=harness.context.allowed_capabilities,
        allowed_scope=harness.context.allowed_scope,
        selected_set_ceiling=harness.context.selected_set_ceiling,
    )
    assert second_context is not harness.context
    assert second_context.context_id == harness.context.context_id
    assert second_context.observation_sha256 == harness.context.observation_sha256

    stage2 = revalidate_before_loading(
        evaluator=harness.evaluator,
        context=second_context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
    )
    assert stage2.outcome is RevalidationOutcome.PASSED
    assert stage2._context is second_context
    calls_before = harness.observation_provider.calls

    with pytest.raises(CompatibilityViolation) as exc:
        revalidate_before_consumption(
            evaluator=harness.evaluator,
            context=harness.context,
            descriptor=harness.descriptor,
            original_decision=harness.decision,
            before_loading=stage2,
        )
    assert exc.value.failure_code is CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED
    assert harness.observation_provider.calls == calls_before


# ---------------------------------------------------------------------------
# Projection into the §22 gate vocabulary — absence is not evidence
# ---------------------------------------------------------------------------


def test_a_consumption_finding_refuses_to_be_built_without_a_conflict_scan(tmp_path: Path) -> None:
    """The kill for the fail-open the projection used to carry.

    ``conflict_scan`` defaulted to ``None`` and the projection read that as
    ``conflicts_unresolved=False``, so a caller that ran no scan produced a
    finding indistinguishable from one that scanned and found nothing. Conflict
    is a dimension the consumption gate declares it checked; NR-10 forbids
    exactly this substitution, because absent, unknown and false are three
    different states and only one of them supports an admission.
    """

    from synapse.experiments.gold.gate_findings import consumption_finding_from_revalidation
    from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind

    harness = _make_harness(tmp_path)
    stage2 = revalidate_before_loading(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
    )
    stage3 = revalidate_before_consumption(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
        before_loading=stage2,
    )
    subject = HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id="subject",
        schema_id="synapse.stage4.gold.thing/v1",
        sha256=hashlib.sha256(b"subject").hexdigest(),
        byte_length=7,
        media_type="application/json",
    )
    context_ref = HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id="consumer-ctx",
        schema_id="synapse.stage4.gold.thing/v1",
        sha256=hashlib.sha256(b"consumer-ctx").hexdigest(),
        byte_length=12,
        media_type="application/json",
    )

    with pytest.raises(TypeError):
        consumption_finding_from_revalidation(
            stage3,
            decision=harness.decision,
            subject_ref=subject,
            consumer_context_ref=context_ref,
        )

    with pytest.raises(CompatibilityViolation) as excinfo:
        consumption_finding_from_revalidation(
            stage3,
            decision=harness.decision,
            conflict_scan=None,
            subject_ref=subject,
            consumer_context_ref=context_ref,
        )
    assert excinfo.value.failure_code is CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE
