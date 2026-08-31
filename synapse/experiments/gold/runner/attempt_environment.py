"""The opened Gold authority world one run prepares its attempts against.

One responsibility: hold, as one sealed and immutable binding, the durable
Stage 3/7/8/9 participants that already exist before a run starts — the
authority handle, the behavior library, the three authority histories, the
admission journal and causal store, the compatibility history, the gate
controller and its admitted chain, the admitted subject set and the committed
knowledge snapshot those subjects were frozen under.

The environment is deliberately *opened*, never minted here. Publishing
behaviors into a library, attesting them and taking them through lifecycle is
a separate concern with its own owners; by the time a run starts, that work is
history. What this module adds is the guarantee that every participant a run
touches came from one coordinator and one authority configuration, so an
attempt cannot be prepared from two different durable worlds.

It holds no policy of its own: what a run *does* with these participants is
decided by ``attempt_input_source``, and the §22 admission itself is taken by
``delivery`` at the point of use.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from synapse.experiments.gold.admission import ConfiguredGateController
from synapse.experiments.gold.admission_journal import FileAdmissionJournal
from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.compatibility import CompatibilityEvaluatorDeclaration
from synapse.experiments.gold.compatibility_store import FileCompatibilityStore
from synapse.experiments.gold.contracts import ActorIdentity, AttemptId
from synapse.experiments.gold.library import BehaviorLibrary

from .vocabulary import GoldRunFailureCode, GoldRunViolation


_ATTEMPT_ENVIRONMENT_SEAL = object()


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


class GoldAttemptEnvironment:
    """Immutable identity snapshot of the durable world a run reads."""

    __slots__ = (
        "_authority_handle",
        "_admitted_handle",
        "_declaration",
        "_library",
        "_repo_root",
        "_lifecycle_store",
        "_attestation_store",
        "_taint_store",
        "_admission_journal",
        "_admission_causal_history",
        "_compatibility_history",
        "_knowledge_store",
        "_controller",
        "_chain",
        "_chain_evidence",
        "_entitlements",
        "_requested",
        "_knowledge_snapshot_ref",
        "_consumer_context_ref",
        "_subjects",
        "_supported",
        "_snapshot_attempt_id",
        "_snapshot_evaluator_declaration",
        "_snapshot_actor_set",
        "_snapshot_independence_proof",
        "_observation",
        "_observation_provider",
        "_evidence_resolver",
        "_conflict_assessor",
        "_retriever_actor",
        "_consumer_actor",
        "_score_provider_actor",
        "_trusted_clock",
        "_identity_snapshot",
        "_trusted_seal",
    )

    def __new__(cls, *args: object, **kwargs: object) -> "GoldAttemptEnvironment":
        raise TypeError("GoldAttemptEnvironment is factory-created")

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("GoldAttemptEnvironment is immutable")

    def __delattr__(self, name: str) -> None:
        raise TypeError("GoldAttemptEnvironment is immutable")

    @property
    def authority_handle(self) -> object:
        return self._authority_handle

    @property
    def admitted_handle(self) -> object:
        return self._admitted_handle

    @property
    def declaration(self) -> CompatibilityEvaluatorDeclaration:
        return self._declaration

    @property
    def library(self) -> BehaviorLibrary:
        return self._library

    @property
    def repo_root(self) -> Path:
        return self._repo_root

    @property
    def lifecycle_store(self) -> object:
        return self._lifecycle_store

    @property
    def attestation_store(self) -> object:
        return self._attestation_store

    @property
    def taint_store(self) -> object:
        return self._taint_store

    @property
    def admission_journal(self) -> FileAdmissionJournal:
        return self._admission_journal

    @property
    def admission_causal_history(self) -> object:
        return self._admission_causal_history

    @property
    def compatibility_history(self) -> FileCompatibilityStore:
        return self._compatibility_history

    @property
    def knowledge_store(self) -> object:
        return self._knowledge_store

    @property
    def controller(self) -> ConfiguredGateController:
        return self._controller

    @property
    def chain(self) -> object:
        return self._chain

    @property
    def chain_evidence(self) -> object:
        return self._chain_evidence

    @property
    def entitlements(self) -> object:
        return self._entitlements

    @property
    def requested(self) -> object:
        return self._requested

    @property
    def knowledge_snapshot_ref(self) -> HashBoundRef:
        return self._knowledge_snapshot_ref

    @property
    def consumer_context_ref(self) -> HashBoundRef:
        return self._consumer_context_ref

    @property
    def subjects(self) -> tuple[HashBoundRef, ...]:
        return self._subjects

    @property
    def supported(self) -> tuple[tuple[object, object, object], ...]:
        return self._supported

    @property
    def snapshot_attempt_id(self) -> AttemptId:
        return self._snapshot_attempt_id

    @property
    def snapshot_evaluator_declaration(self) -> object:
        return self._snapshot_evaluator_declaration

    @property
    def snapshot_actor_set(self) -> object:
        return self._snapshot_actor_set

    @property
    def snapshot_independence_proof(self) -> object:
        return self._snapshot_independence_proof

    @property
    def observation(self) -> object:
        return self._observation

    @property
    def observation_provider(self) -> Callable[[], object]:
        return self._observation_provider

    @property
    def evidence_resolver(self) -> Callable[[object], object]:
        return self._evidence_resolver

    @property
    def conflict_assessor(self) -> Callable[..., object]:
        return self._conflict_assessor

    @property
    def retriever_actor(self) -> ActorIdentity:
        return self._retriever_actor

    @property
    def consumer_actor(self) -> ActorIdentity:
        return self._consumer_actor

    @property
    def score_provider_actor(self) -> ActorIdentity:
        return self._score_provider_actor

    @property
    def trusted_clock(self) -> Callable[[], datetime]:
        return self._trusted_clock


def create_gold_attempt_environment(
    *,
    authority_handle: object,
    admitted_handle: object,
    declaration: CompatibilityEvaluatorDeclaration,
    library: BehaviorLibrary,
    repo_root: Path,
    lifecycle_store: object,
    attestation_store: object,
    taint_store: object,
    admission_journal: FileAdmissionJournal,
    admission_causal_history: object,
    compatibility_history: FileCompatibilityStore,
    knowledge_store: object,
    controller: ConfiguredGateController,
    chain: object,
    chain_evidence: object,
    entitlements: object,
    requested: object,
    knowledge_snapshot_ref: HashBoundRef,
    consumer_context_ref: HashBoundRef,
    subjects: tuple[HashBoundRef, ...],
    supported: tuple[tuple[object, object, object], ...],
    snapshot_attempt_id: AttemptId,
    snapshot_evaluator_declaration: object,
    snapshot_actor_set: object,
    snapshot_independence_proof: object,
    observation: object,
    observation_provider: Callable[[], object],
    evidence_resolver: Callable[[object], object],
    conflict_assessor: Callable[..., object],
    retriever_actor: ActorIdentity,
    consumer_actor: ActorIdentity,
    score_provider_actor: ActorIdentity,
    trusted_clock: Callable[[], datetime],
) -> GoldAttemptEnvironment:
    """Seal one opened durable world, or refuse an incoherent participant set.

    The checks are the ones a wrong binding survives: an inexact library or
    history type, a non-absolute repository root, a candidate universe that is
    empty or disagrees with the supported subject set, and callables that are
    not callable. Everything deeper — that these stores share a coordinator and
    an authority configuration — is checked by the owners themselves when the
    run actually uses them, which is the only place those checks mean anything.
    """

    if type(library) is not BehaviorLibrary:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "library must be the exact behavior library")
    if type(admission_journal) is not FileAdmissionJournal:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "admission journal must be the exact file journal")
    if type(compatibility_history) is not FileCompatibilityStore:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "compatibility history must be the exact file store")
    if type(controller) is not ConfiguredGateController:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "gate controller must be exact")
    if type(repo_root) is not type(Path()) or not repo_root.is_absolute():
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "repository root must be an exact absolute Path")
    if type(snapshot_attempt_id) is not AttemptId:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "snapshot attempt id must be exact")
    for name, reference in (
        ("knowledge_snapshot_ref", knowledge_snapshot_ref),
        ("consumer_context_ref", consumer_context_ref),
    ):
        if type(reference) is not HashBoundRef:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be an exact HashBoundRef")
    if type(subjects) is not tuple or not subjects or any(
        type(item) is not HashBoundRef for item in subjects
    ):
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "the admitted subject set must be a non-empty tuple of exact refs",
        )
    if type(supported) is not tuple or not supported or any(
        type(item) is not tuple or len(item) != 3 for item in supported
    ):
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "the supported behavior set must be a non-empty tuple of unit/descriptor/entry triples",
        )
    for name, actor in (
        ("retriever_actor", retriever_actor),
        ("consumer_actor", consumer_actor),
        ("score_provider_actor", score_provider_actor),
    ):
        if type(actor) is not ActorIdentity:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be an exact actor identity")
    for name, item in (
        ("observation_provider", observation_provider),
        ("evidence_resolver", evidence_resolver),
        ("conflict_assessor", conflict_assessor),
        ("trusted_clock", trusted_clock),
    ):
        if not callable(item):
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be callable")

    result = object.__new__(GoldAttemptEnvironment)
    fields = {
        "_authority_handle": authority_handle,
        "_admitted_handle": admitted_handle,
        "_declaration": declaration,
        "_library": library,
        "_repo_root": repo_root,
        "_lifecycle_store": lifecycle_store,
        "_attestation_store": attestation_store,
        "_taint_store": taint_store,
        "_admission_journal": admission_journal,
        "_admission_causal_history": admission_causal_history,
        "_compatibility_history": compatibility_history,
        "_knowledge_store": knowledge_store,
        "_controller": controller,
        "_chain": chain,
        "_chain_evidence": chain_evidence,
        "_entitlements": entitlements,
        "_requested": requested,
        "_knowledge_snapshot_ref": knowledge_snapshot_ref,
        "_consumer_context_ref": consumer_context_ref,
        "_subjects": subjects,
        "_supported": supported,
        "_snapshot_attempt_id": snapshot_attempt_id,
        "_snapshot_evaluator_declaration": snapshot_evaluator_declaration,
        "_snapshot_actor_set": snapshot_actor_set,
        "_snapshot_independence_proof": snapshot_independence_proof,
        "_observation": observation,
        "_observation_provider": observation_provider,
        "_evidence_resolver": evidence_resolver,
        "_conflict_assessor": conflict_assessor,
        "_retriever_actor": retriever_actor,
        "_consumer_actor": consumer_actor,
        "_score_provider_actor": score_provider_actor,
        "_trusted_clock": trusted_clock,
    }
    for name, item in fields.items():
        object.__setattr__(result, name, item)
    object.__setattr__(result, "_identity_snapshot", tuple(fields.values()))
    object.__setattr__(result, "_trusted_seal", _ATTEMPT_ENVIRONMENT_SEAL)
    return require_gold_attempt_environment(result)


def require_gold_attempt_environment(value: object) -> GoldAttemptEnvironment:
    """Refuse a forged environment or one whose bindings were replaced."""

    if (
        type(value) is not GoldAttemptEnvironment
        or getattr(value, "_trusted_seal", None) is not _ATTEMPT_ENVIRONMENT_SEAL
    ):
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "an exact sealed attempt environment is required",
        )
    snapshot = getattr(value, "_identity_snapshot", None)
    current = tuple(
        object.__getattribute__(value, name)
        for name in GoldAttemptEnvironment.__slots__
        if name not in ("_identity_snapshot", "_trusted_seal")
    )
    if snapshot is None or len(snapshot) != len(current) or any(
        held is not observed for held, observed in zip(snapshot, current)
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "the attempt environment no longer holds the bindings it was sealed with",
        )
    return value


__all__ = [
    "GoldAttemptEnvironment",
    "create_gold_attempt_environment",
    "require_gold_attempt_environment",
]
