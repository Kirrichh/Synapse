"""The production factory for each attempt's world (§21 lineage; plan Этап 11).

A run holds one authority world and takes one snapshot *per attempt*, and those
snapshots are a chain: attempt N's boundary names attempt N-1's as its parent
and starts where that one committed. Building one environment per run and
handing it to every attempt looked equivalent and was not -- the second attempt
then rests on evidence minted before its own predecessor ran, and §22's
revalidation has nothing to revalidate against.

This is the composition root for that chain. It is the party that knows every
side of one attempt: which boundary the run last committed, the durable roots
this attempt's retrieval writes to, and the concrete stores behind them. The
order *within* one attempt belongs to ``run_environment``, which this calls and
does not reimplement.

The parent is read from the authoritative head rather than counted from the
attempt index, so a restart finds the position the history is actually at.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from synapse.experiments.gold import point_of_use as P
from synapse.experiments.gold.admission_journal import FileAdmissionJournal
from synapse.experiments.gold.compatibility_store import FileCompatibilityStore
from synapse.experiments.gold.contracts import AttemptId, RunId
from synapse.experiments.gold.run_compatibility import CompatibilityEvaluatorBindings
from synapse.experiments.gold.run_environment import (
    AssembledRunEnvironment,
    assemble_run_environment,
)
from synapse.experiments.gold.run_gate_chain import (
    AuthorityStores,
    GateActorDeclaration,
    GateProbeBindings,
)
from synapse.experiments.gold.run_retrieval import RunRetrieval, RunRetrievalBindings
from synapse.experiments.gold.run_snapshot import SnapshotActorDeclaration, SnapshotLineage
from synapse.experiments.gold.runner.attempt_environment import GoldAttemptEnvironment
from synapse.experiments.gold.runner.attempt_input_source import (
    AttemptReplayPort,
    mint_attempt_authority,
)
from synapse.experiments.gold.runner.vocabulary import (
    GoldRunFailureCode,
    GoldRunViolation,
)


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


#: Attempt indices are one-based, and the first is the only one allowed to
#: commit a boundary with no parent.
_FIRST_ATTEMPT = 1


@dataclass(frozen=True)
class AttemptAuthorityContext:
    """What one attempt's authority has established, before replay binds to it.

    This is the seam that lets a run bind governed replay without the two
    building each other. Replay binds through admissions of *this* attempt --
    a handle, its gate chain, that chain's commit evidence and the entitlements
    it was granted under -- and all of those exist only once the attempt's own
    snapshot has been admitted. Handing replay a finished world instead would
    mean the world could not be finished until replay existed, which is the
    circle this splits.

    ``mint_admission`` is offered rather than the parts to build one, because
    minting an attempt's authority is a production sequence and a second copy of
    it would drift from the one the input source uses.
    """

    run_id: RunId
    attempt_id: AttemptId
    attempt_index: int
    environment: GoldAttemptEnvironment
    retrieval: RunRetrieval
    assembled: AssembledRunEnvironment
    parent_snapshot: object | None
    mint_admission: Callable[[], object]


@runtime_checkable
class AttemptReplayBindingPort(Protocol):
    """Bind governed replay to an attempt whose authority is already established.

    One method, and it takes the attempt's own authority rather than a run-wide
    world: a binding built from anything else would let an attempt replay under
    an admission it never crossed.
    """

    def bind(self, context: AttemptAuthorityContext) -> object: ...


@dataclass(frozen=True)
class PreparedAttemptWorld:
    """One attempt's sealed world: its authority, and the replay bound to it.

    Every field is this attempt's own. Sharing any of them with the next attempt
    is what made a run's second attempt consume the first one's decisions: the
    retrieval owner is one-shot by construction, so a reused one reports a gate
    decision taken against a world that has since moved.
    """

    environment: GoldAttemptEnvironment
    retrieval: RunRetrieval
    replay: object
    assembled: AssembledRunEnvironment
    parent_snapshot: object | None
    attempt_index: int


class GoldAttemptWorldFactory:
    """Build each attempt's world, attached to the run's chain of snapshots.

    A run holds one authority world and takes one snapshot *per attempt*, and
    those snapshots are a chain: attempt N's boundary names attempt N-1's as its
    parent and starts where that one committed. Building the world once per run
    and handing it to every attempt looked equivalent and was not — the second
    attempt then rests on evidence minted before its own predecessor ran, and
    §22's revalidation has nothing to revalidate against.

    The parent is read from the authoritative head rather than counted from the
    attempt index, so a restart finds the position the history is actually at.
    """

    def __init__(
        self,
        *,
        authority_handle: object,
        stores: AuthorityStores,
        library: object,
        repo_root: Path,
        snapshot_root: Path,
        candidates: tuple[tuple[object, object, object], ...],
        run_id: RunId,
        repository_revision: str,
        policy_version: str,
        environment_profile_id: str,
        snapshot_actors: SnapshotActorDeclaration,
        compatibility: CompatibilityEvaluatorBindings,
        gate_actors: GateActorDeclaration,
        gate_probes: GateProbeBindings,
        requested: object,
        created_at_utc: datetime,
        trusted_clock: Callable[[], datetime],
        gate_clock: Callable[[], datetime],
        ref_resolver: Callable[[object], bool],
        consumability_probe: Callable[[object], bool],
        transaction_id: str,
        retrieval_root: Path,
        retrieval_bindings: RunRetrievalBindings,
        frozen_at_utc: datetime,
        replay_binding: AttemptReplayBindingPort,
    ) -> None:
        if type(stores) is not AuthorityStores:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "authority stores must be exact")
        if type(candidates) is not tuple or not candidates:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "a run needs a non-empty candidate universe",
            )
        if type(retrieval_bindings) is not RunRetrievalBindings:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "retrieval bindings must be exact")
        if not isinstance(replay_binding, AttemptReplayBindingPort):
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "replay binding does not implement its declared port",
            )
        self._handle = authority_handle
        self._stores = stores
        self._library = library
        self._repo_root = repo_root
        self._snapshot_root = snapshot_root
        self._candidates = candidates
        self._run_id = run_id
        self._repository_revision = repository_revision
        self._policy_version = policy_version
        self._environment_profile_id = environment_profile_id
        self._snapshot_actors = snapshot_actors
        self._compatibility = compatibility
        self._gate_actors = gate_actors
        self._gate_probes = gate_probes
        self._requested = requested
        self._created_at_utc = created_at_utc
        self._trusted_clock = trusted_clock
        self._gate_clock = gate_clock
        self._ref_resolver = ref_resolver
        self._consumability_probe = consumability_probe
        self._transaction_id = transaction_id
        self._retrieval_root = Path(retrieval_root)
        self._retrieval_bindings = retrieval_bindings
        self._frozen_at_utc = frozen_at_utc
        self._replay_binding = replay_binding
        self._admissions_taken = 0

    def world_for_attempt(
        self, *, manifest: object, attempt_index: int, previous_context: object | None
    ) -> AttemptWorld:
        """Assemble this attempt's world onto the run's committed chain."""

        del manifest
        if type(attempt_index) is not int or attempt_index < _FIRST_ATTEMPT:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "attempt index must be a one-based integer",
            )
        attempt_id = AttemptId(str(attempt_index))
        parent = self._chain_position(
            attempt_index=attempt_index, previous_context=previous_context
        )
        retrieval = self._retrieval_for(attempt_id)
        assembled = assemble_run_environment(
            authority_handle=self._handle,
            stores=self._stores,
            library=self._library,
            repo_root=self._repo_root,
            snapshot_root=self._snapshot_root,
            candidates=self._candidates,
            run_id=self._run_id,
            attempt_id=attempt_id,
            repository_revision=self._repository_revision,
            policy_version=self._policy_version,
            environment_profile_id=self._environment_profile_id,
            snapshot_actors=self._snapshot_actors,
            compatibility=self._compatibility,
            gate_actors=self._gate_actors,
            gate_probes=self._gate_probes,
            requested=self._requested,
            created_at_utc=self._created_at_utc,
            trusted_clock=self._trusted_clock,
            gate_clock=self._gate_clock,
            ref_resolver=self._ref_resolver,
            consumability_probe=self._consumability_probe,
            #: One transaction per attempt. Reusing the run's would make two
            #: boundaries claim the same commit.
            transaction_id=f"{self._transaction_id}-{attempt_index}",
            lineage=SnapshotLineage(parent_snapshot=parent),
            retrieval_decision=retrieval,
        )
        #: Phase one is over: this attempt is admitted, and everything a replay
        #: binds through now exists. Phase two hands that over and takes back the
        #: binding, so the world is sealed around a replay that belongs to this
        #: attempt rather than to the run.
        context = AttemptAuthorityContext(
            run_id=self._run_id,
            attempt_id=attempt_id,
            attempt_index=attempt_index,
            environment=assembled.environment,
            retrieval=retrieval,
            assembled=assembled,
            parent_snapshot=parent,
            mint_admission=lambda: self._mint_admission(
                assembled.environment, attempt_index
            ),
        )
        replay = self._replay_binding.bind(context)
        #: Checked, not trusted. A binding that returned nothing would leave an
        #: attempt world that looks complete and has no replay to consume, and
        #: the absence would only surface once a worker asked for one.
        if not isinstance(replay, AttemptReplayPort):
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "the replay binding returned no replay implementing its declared port",
            )
        return PreparedAttemptWorld(
            environment=assembled.environment,
            retrieval=retrieval,
            replay=replay,
            assembled=assembled,
            parent_snapshot=parent,
            attempt_index=attempt_index,
        )

    def _mint_admission(
        self, environment: GoldAttemptEnvironment, attempt_index: int
    ) -> object:
        """One further admission of this attempt, for a replay phase to bind to.

        Each call mints fresh Stage 3 evidence and an authority binding over it,
        exactly as the input source does for the admission the attempt itself
        crosses. Reusing one admission across phases would make a later phase
        rest on a revalidation taken before its own preparation wrote anything.

        The moment advances on every call, because these admissions are taken
        one after another and their Stage 3 records carry no clock of their own.
        Minting two at one instant produces the same revalidation twice, and the
        append-only history refuses the repeat -- which is right, and would leave
        a run unable to bind the second phase of its own replay.
        """

        self._admissions_taken += 1
        minted = mint_attempt_authority(
            environment=environment,
            moment=environment.trusted_clock()
            + timedelta(seconds=attempt_index, microseconds=self._admissions_taken),
        )
        return P.create_point_of_use_admission_request(
            handle=environment.admitted_handle,
            binding=minted.authority_binding,
            chain=environment.chain,
            evidence=environment.chain_evidence,
            entitlements=environment.entitlements,
            requested=environment.requested,
        )

    def _chain_position(
        self, *, attempt_index: int, previous_context: object | None
    ) -> object | None:
        """Find what this attempt commits onto, refusing anything else.

        Two states are legitimate: no head at all before the first attempt, and
        the predecessor's boundary before a later one. Every other head is a
        broken chain, including this attempt's own -- a boundary for it already
        exists, so its preparation ran, and committing a second one would fork
        the chain past the point where a run could say which of the two an
        attempt consumed from.
        """

        store = self._stores.knowledge_store
        head_id = store.current_boundary_id()
        if attempt_index == _FIRST_ATTEMPT:
            if previous_context is not None:
                raise _fail(
                    GoldRunFailureCode.AUTHORITY_MISMATCH,
                    "the first attempt cannot name a predecessor context",
                )
            if head_id is not None:
                raise _fail(
                    GoldRunFailureCode.AUTHORITY_MISMATCH,
                    "a run's first attempt cannot commit onto an existing boundary",
                )
            return None
        if previous_context is None:
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "a continued attempt needs the context of the attempt before it",
            )
        if head_id is None:
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "a continued attempt found no committed parent boundary",
            )
        snapshot = store.open_current()
        boundary = snapshot.boundary
        committed = self._committed_attempt_index(boundary)
        if committed == attempt_index:
            raise _fail(
                GoldRunFailureCode.PHASE_INVALID,
                "this attempt's snapshot is already committed; its preparation "
                "must be recovered rather than taken again",
            )
        if committed != attempt_index - 1:
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "the committed head belongs to neither this attempt nor the one before it",
            )
        #: The head must be the snapshot the previous attempt actually named.
        #: Comparing the index alone would accept a boundary that carries the
        #: right number and describes another world.
        named = getattr(getattr(previous_context, "phase_refs", None), "knowledge_snapshot_ref", None)
        if named is None or boundary.manifest_ref.to_dict() != named.to_dict():
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "the committed head is not the snapshot the previous attempt named",
            )
        return snapshot

    @staticmethod
    def _committed_attempt_index(boundary: object) -> int:
        envelope = getattr(boundary, "envelope", None)
        attempt = getattr(envelope, "attempt_id", None)
        value = getattr(attempt, "value", None)
        if type(value) is not str or not value.isdigit():
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "the committed boundary does not name the attempt that took it",
            )
        return int(value)

    def _retrieval_for(self, attempt_id: AttemptId) -> RunRetrieval:
        """This attempt's own one-shot retrieval, over its own durable roots."""

        case_root = self._retrieval_root / attempt_id.value
        case_root.mkdir(parents=True, exist_ok=True)
        return RunRetrieval(
            retrieval_journal=FileAdmissionJournal(
                case_root / "retrieval-gate" / "decisions.journal",
                self._stores.mutation_fence,
            ),
            retrieval_compatibility_history=FileCompatibilityStore(
                case_root / "compatibility", mutation_fence=self._stores.mutation_fence
            ),
            knowledge_store=self._stores.knowledge_store,
            library=self._library,
            authority_handle=self._handle,
            admission_causal_history=self._stores.admission_causal_history,
            candidates=self._candidates,
            attempt_id=attempt_id,
            bindings=self._retrieval_bindings,
            trusted_clock=self._gate_clock,
            frozen_at_utc=self._frozen_at_utc,
        )


class ProjectAttemptWorlds:
    """Materialize project dependencies only after preparation is checkpointed.

    Restoring a completed or interrupted run therefore does not recreate seed
    worlds or repeat replay. A continued attempt reuses the existing factory's
    lineage rules over the same per-run snapshot store and shared project
    authority histories.
    """

    def __init__(self, *, inputs, task_contract):
        self._inputs = inputs
        self._task = task_contract
        self._factory = None

    def world_for_attempt(self, *, manifest, attempt_index, previous_context):
        if manifest.inputs_sha256 != self._inputs.sha256:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "attempt inputs differ from frozen manifest")
        if self._factory is None:
            self._factory = self._assemble()
        return self._factory.world_for_attempt(
            manifest=manifest, attempt_index=attempt_index, previous_context=previous_context,
        )

    def _assemble(self):
        from .admission import RequestedEnvelope
        from .bindings import BindingKind
        from .compatibility import create_compatibility_evaluator_declaration, COMPATIBILITY_POLICY_V1
        from .contracts import ActorIdentity, AuthorityIdentity
        from .knowledge_environment import open_gold_project
        from .knowledge_store import AuthoritativeKnowledgeStore
        from .replay_composition import ProjectAttemptReplayBinding, ReplayBudgets
        from .run_knowledge import RunKnowledge

        inputs = self._inputs
        data = inputs.data
        declaration = data["declaration"]
        manifest = inputs.manifest
        root = Path(data["run_root"])
        project = open_gold_project(Path(data["project_state_root"]), trusted_heads=data["trusted_heads"])
        knowledge = RunKnowledge(inputs=inputs, project=project, task=self._task)
        observation = knowledge.observe()
        if declaration["replay_profile"] != "pure-cvm/v1" or any(unit.core.capability_requirements for unit, _, _ in knowledge.candidates):
            raise _fail(GoldRunFailureCode.CONFIG_INVALID, "seed requires capabilities outside the frozen replay profile")
        namespace = declaration["actor_namespace"]
        actor = lambda suffix: ActorIdentity(f"{namespace}.{suffix}")
        frozen_at = datetime.fromisoformat(data["frozen_at_utc"])
        policies = tuple(item for item in observation.policy_inputs if item.version == COMPATIBILITY_POLICY_V1)
        if len(policies) != 1:
            raise _fail(GoldRunFailureCode.CONFIG_INVALID, "observation must identify the active compatibility policy")
        evaluator_declaration = create_compatibility_evaluator_declaration(
            authority_handle=project.authority_handle,
            evaluator_identity=AuthorityIdentity(f"{namespace}.compatibility-evaluator"),
            evaluator_component_id="synapse.stage4.compatibility", evaluator_component_version="synapse.stage4.compatibility/v1",
            active_policy_input=policies[0],
            allowed_behavior_kinds=tuple(sorted({unit.core.behavior_kind for unit, _, _ in knowledge.candidates}, key=lambda item: item.value)),
            allowed_binding_kinds=tuple(BindingKind), allowed_capabilities=(),
            allowed_scope=self._task.allowed_scope.entries,
            selected_set_ceiling=len(knowledge.candidates), trusted_clock=lambda: frozen_at,
        )
        compatibility = CompatibilityEvaluatorBindings(
            declaration=evaluator_declaration, observation=observation, observation_provider=knowledge.observe,
            evidence_resolver=knowledge.evidence_for, conflict_assessor=knowledge.assess_conflict,
            binding_repo_root=knowledge.repo_root, retriever_actor=actor("retriever"),
            consumer_actor=actor("consumer"), score_provider_actor=actor("scorer"),
        )
        snapshots = root / "snapshots"
        stores = AuthorityStores(
            lifecycle_store=project.lifecycle_store, attestation_store=project.attestation_store,
            taint_store=project.taint_store, admission_journal=project.admission_journal,
            admission_causal_history=project.admission_causal_history,
            compatibility_history=project.compatibility_history,
            knowledge_store=AuthoritativeKnowledgeStore(snapshots, mutation_fence=project.fence),
            mutation_fence=project.fence,
        )
        snapshot_actors = SnapshotActorDeclaration(
            producer_actor=actor("snapshot-producer"), source_actor=actor("source"),
            retriever_actor=actor("retriever"), indexer_actor=actor("indexer"), publisher_actor=actor("publisher"),
            consumer_actor=actor("consumer"), worker_actor=actor("worker"), executor_actor=actor("executor"),
            evaluator_identity=AuthorityIdentity(f"{namespace}.snapshot-evaluator"),
            evaluator_component_id="synapse.stage4.snapshot", evaluator_component_version="synapse.stage4.snapshot/v1",
            producer_component="synapse.stage4.snapshot-producer",
        )
        grant = knowledge.grant_probe()
        replay = ProjectAttemptReplayBinding(
            project=project, run_root=root, actor_namespace=namespace, frozen_at=frozen_at,
            policy_version=manifest.versions.policy_version,
            budgets=ReplayBudgets(manifest.config.budgets.replay_gas_budget,
                                  manifest.config.budgets.replay_cognitive_budget,
                                  manifest.config.budgets.replay_gas_budget),
            behavior_refs=self._task.behavior_refs,
        )
        return GoldAttemptWorldFactory(
            authority_handle=project.authority_handle, stores=stores, library=project.library,
            repo_root=knowledge.repo_root, snapshot_root=snapshots, candidates=knowledge.candidates,
            run_id=manifest.run_id, repository_revision=manifest.config.base_revision,
            policy_version=manifest.versions.policy_version, environment_profile_id=project.declaration.environment_profile_id,
            snapshot_actors=snapshot_actors, compatibility=compatibility,
            gate_actors=GateActorDeclaration(
                evaluator_identity=AuthorityIdentity(f"{namespace}.gate-evaluator"),
                evaluator_component_id="synapse.stage4.gates", evaluator_component_version="synapse.stage4.gates/v1",
                producer_actor=actor("producer"), retriever_actor=actor("retriever"), consumer_actor=actor("consumer"),
            ),
            gate_probes=GateProbeBindings(knowledge.taint_probe, knowledge.provenance_probe,
                                         knowledge.lifecycle_probe, knowledge.grant_probe),
            requested=RequestedEnvelope(scopes=grant.scopes, capabilities=("read",), oracles=("swebench",)),
            created_at_utc=frozen_at, trusted_clock=knowledge.clock, gate_clock=knowledge.clock,
            ref_resolver=knowledge.ref_resolver, consumability_probe=knowledge.consumability_probe,
            transaction_id=manifest.manifest_sha256, retrieval_root=root / "retrieval",
            retrieval_bindings=RunRetrievalBindings(
                ranking_component_id="synapse.stage4.declared-seed-order",
                ranking_component_version="synapse.stage4.declared-seed-order/v1",
                scorer=knowledge.score, input_ref_resolver=knowledge.ranking_input_ref,
                selected_set_limit=len(self._task.behavior_refs),
            ),
            frozen_at_utc=frozen_at, replay_binding=replay,
        )


__all__ = [
    "AttemptAuthorityContext",
    "AttemptReplayBindingPort",
    "GoldAttemptWorldFactory",
    "PreparedAttemptWorld",
]
