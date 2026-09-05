"""Fresh Stage 3 compatibility evidence for one consumption (§20; §22 stage 3).

A consumer may not rest on evidence gathered before the world it is about
changed. §20 makes compatibility a decision bound to a context, and §22 makes
it revalidated again immediately before the consumption it authorises — so the
evidence for one admission is minted, appended to the append-only history, and
bound into probes as one sequence. Splitting that sequence is how a probe ends
up holding a decision reached about a different world.

The ordering is the whole of it: evaluate each candidate against one shared
context, scan the whole selected set for conflicts *once* (a conflict is a
relation between candidates, so a scan that saw one at a time could not report
one), revalidate each before loading, commit everything to the history, and
only then bind the four records per subject into a consumption probe.

Like §21's assembly, this existed only inside an acceptance fixture and inside
a second copy in the run package. It is one sequence, so it is one function.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from synapse.experiments.gold import gate_findings as GF
from synapse.experiments.gold.compatibility import (
    configure_compatibility_evaluator,
    create_compatibility_context,
    evaluate_compatibility,
    evaluate_conflicts,
    revalidate_before_loading,
)
from synapse.experiments.gold.compatibility_store import (
    CompatibilityStoreFailureCode,
    CompatibilityStoreViolation,
)
from synapse.experiments.gold.contracts import ActorIdentity

from .runner.vocabulary import GoldRunFailureCode, GoldRunViolation


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


@dataclass(frozen=True)
class CompatibilityEvaluatorBindings:
    """The readings of one deployment a compatibility evaluator is configured with.

    These are callables and identities rather than data because they answer
    questions about a live world: what evidence backs this descriptor, what a
    conflict between two candidates means here, and who the three independent
    actors are. §20 requires evaluator, retriever, consumer and scorer to be
    distinct, and the evaluator itself checks that.
    """

    declaration: object
    observation: object
    observation_provider: Callable[[], object]
    evidence_resolver: Callable[[object], object]
    conflict_assessor: Callable[..., object]
    binding_repo_root: Path
    retriever_actor: ActorIdentity
    consumer_actor: ActorIdentity
    score_provider_actor: ActorIdentity

    def __post_init__(self) -> None:
        for name in ("retriever_actor", "consumer_actor", "score_provider_actor"):
            if type(getattr(self, name)) is not ActorIdentity:
                raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be an exact actor identity")
        if type(self.binding_repo_root) is not type(Path()) or not self.binding_repo_root.is_absolute():
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "binding_repo_root must be an exact absolute Path",
            )
        for name in ("observation_provider", "evidence_resolver", "conflict_assessor"):
            if not callable(getattr(self, name)):
                raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be callable")


@dataclass(frozen=True)
class MintedCompatibilityEvidence:
    """One consumption's Stage 3 records and the probes bound to them."""

    evaluator: object
    context: object
    decisions: tuple[object, ...]
    conflict_scan: object
    before_loading: tuple[object, ...]
    consumption_bindings: tuple[object, ...]
    revalidation_probe: object
    durable_revalidation_probe: object


def mint_compatibility_evidence(
    *,
    authority_handle: object,
    bindings: CompatibilityEvaluatorBindings,
    library: object,
    library_snapshot: object,
    lifecycle_store: object,
    attestation_store: object,
    taint_store: object,
    compatibility_history: object,
    candidates: tuple[tuple[object, object, object], ...],
    trusted_clock: Callable[[], datetime],
) -> MintedCompatibilityEvidence:
    """Mint, commit and bind this consumption's compatibility evidence."""

    if type(bindings) is not CompatibilityEvaluatorBindings:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "evaluator bindings must be exact")
    if type(candidates) is not tuple or not candidates:
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "compatibility evidence needs a non-empty candidate set",
        )

    evaluator = configure_compatibility_evaluator(
        authority_handle=authority_handle,
        declaration=bindings.declaration,
        evaluator_component_id=bindings.declaration.evaluator_component_id,
        evaluator_component_version=bindings.declaration.evaluator_component_version,
        trusted_clock=trusted_clock,
        platform_observation_provider=bindings.observation_provider,
        library=library,
        lifecycle_store=lifecycle_store,
        attestation_store=attestation_store,
        taint_store=taint_store,
        evidence_resolver=bindings.evidence_resolver,
        binding_repo_root=bindings.binding_repo_root,
        conflict_assessor=bindings.conflict_assessor,
        retriever_actor=bindings.retriever_actor,
        consumer_actor=bindings.consumer_actor,
        score_provider_actor=bindings.score_provider_actor,
    )
    context = create_compatibility_context(
        evaluator=evaluator,
        authority_handle=authority_handle,
        observation=bindings.observation,
        library_snapshot=library_snapshot,
        lifecycle_snapshot=lifecycle_store.snapshot(),
        consumer_actor=evaluator.consumer_actor,
    )
    decisions = tuple(
        evaluate_compatibility(
            evaluator=evaluator, context=context, descriptor=item[1], index_entry=item[2]
        )
        for item in candidates
    )
    #: One scan over the whole selected set, not one per subject: a conflict is
    #: a relation between candidates, so a scan that saw one candidate at a time
    #: could not report one.
    conflict_scan = evaluate_conflicts(
        evaluator=evaluator,
        context=context,
        decisions=decisions,
        descriptors=tuple(item[1] for item in candidates),
        considered_index_entries=tuple(item[2] for item in candidates),
        proposals=(),
    )
    before_loading = tuple(
        revalidate_before_loading(
            evaluator=evaluator,
            context=context,
            descriptor=item[1],
            original_decision=decisions[index],
        )
        for index, item in enumerate(candidates)
    )
    _commit_records(
        compatibility_history=compatibility_history,
        context=context,
        decisions=decisions,
        conflict_scan=conflict_scan,
        before_loading=before_loading,
    )
    consumption_bindings = tuple(
        GF.bind_consumption_evidence(
            descriptor=item[1],
            original_decision=decisions[index],
            before_loading=before_loading[index],
            conflict_scan=conflict_scan,
        )
        for index, item in enumerate(candidates)
    )
    return MintedCompatibilityEvidence(
        evaluator=evaluator,
        context=context,
        decisions=decisions,
        conflict_scan=conflict_scan,
        before_loading=before_loading,
        consumption_bindings=consumption_bindings,
        revalidation_probe=GF.configured_revalidation_probe(
            evaluator=evaluator, context=context, bindings=consumption_bindings
        ),
        durable_revalidation_probe=GF.configured_durable_revalidation_probe(
            evaluator=evaluator,
            context=context,
            bindings=consumption_bindings,
            compatibility_history=compatibility_history,
        ),
    )


def _commit_records(
    *,
    compatibility_history: object,
    context: object,
    decisions: tuple[object, ...],
    conflict_scan: object,
    before_loading: tuple[object, ...],
) -> None:
    """Append this consumption's evidence to the append-only history.

    A later consumption can legitimately reproduce a byte-identical record; the
    history refuses that duplicate and the refusal is the correct outcome,
    because the record it already holds is the one required. Any other refusal
    is a real failure and is not swallowed.
    """

    records: list[object] = [context]
    for decision in decisions:
        records.extend((decision.evidence, decision))
    records.append(conflict_scan)
    records.extend(before_loading)
    for record in records:
        try:
            compatibility_history.append_record(
                record, expected_parent_anchor=compatibility_history.current_anchor()
            )
        except CompatibilityStoreViolation as exc:
            if exc.failure_code is not CompatibilityStoreFailureCode.RECORD_DUPLICATE:
                raise


__all__ = [
    "CompatibilityEvaluatorBindings",
    "MintedCompatibilityEvidence",
    "mint_compatibility_evidence",
]
