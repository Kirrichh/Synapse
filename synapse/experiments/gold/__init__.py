"""Stage 4 Gold common contracts and package boundary.

The integrated Stage 4 runtime is not implemented by this package boundary.

What *is* implemented here is the boundary itself: which modules this package
contains, what normative responsibility each one holds, and which of them are
adapters attached to an owner rather than owners in their own right. That map
used to live in the architecture suite, which had it backwards — a test that
defines the ownership map is a test grading its own answer, and a rule nothing
in production states is a rule production has not made. The map is a governance
fact about this package, so the package declares it and the suite reads it.
"""

#: §12 recommended ownership map. Each entry is one normative responsibility.
#:
#: An entry here is a claim that this module holds exactly one subject and that
#: no other module holds it. Adding a module without adding it to one of the two
#: maps below fails the architecture tripwire, which is the point: a file that
#: nobody can say the responsibility of is a file whose responsibility drifts.
STAGE4_OWNERSHIP_MAP: dict[str, str] = {
    "__init__.py": "package boundary",
    "contracts.py": "common IDs, enums, schema envelopes",
    "behavior.py": "SynapseBehaviorUnit, BehaviorBlob, BehaviorManifest",
    "bindings.py": "Python/Document/Requirement binding resolution",
    "canonicalization.py": "canonical profiles, ContentKey, migration relations",
    "library.py": "immutable CAS, index metadata, collision/corruption checks",
    "provenance.py": "BehaviorAttestation and trusted attester boundary",
    "taint.py": "SourceTaintProfile, TaintAuthorityDecision, derivation records",
    "lifecycle.py": "append-only lifecycle, supersession, revocation",
    "retrieval.py": "queries, candidates, ranking, conflicts, decisions",
    "compatibility.py": "CompatibilityContext/Evidence/Decision, revalidation",
    "knowledge.py": "RepositoryKnowledgeSnapshot, AtomicSnapshotBoundary",
    "admission.py": "ingestion/publication/retrieval/consumption gates",
    "replay.py": "CognitiveVM integration and ReplayResult",
    "activities.py": "governed external activities and recorded results",
    "activity_policy.py": "Activity Policy authority and evaluator entitlement",
    "intent.py": "IntentCandidate",
    "planning.py": "OperationPlanCandidate, PlanAuthorityDecision",
    "context.py": "typed worker context and rendering/envelope",
    "runner.py": "multi-attempt Gold run controller",
    "verification.py": "C1/C2 adapter coordination and FULL predicate",
    "outcome.py": "StructuredOutcome",
    "publication.py": "PublicationAuthorityDecision execution",
    "lineage.py": "DAG nodes/edges and reconstruction",
    "telemetry.py": "canonical records, completeness, reconciliation",
    "events.py": "read-only EventStream",
    "paired.py": "Baseline/Gold execution harness",
    "persistence.py": "run manifests, recovery and integrity",
}

#: Adapters attached to a declared owner. An adapter is *not* a new §12 owner and
#: carries no responsibility of its own: it holds part of one owner's normative
#: responsibility, placed in its own file under the repository owner's standing
#: decision that large modules are extended through adapters rather than grown in
#: place.
#:
#: There is deliberately no line count here. NR-04 states that no numeric LOC
#: threshold is introduced and that the blocking criterion is a file leaving its
#: single normative responsibility. An earlier revision of this comment cited
#: "past 2500 lines" as though it were the rule; it is not, and quoting an
#: invented threshold in a tripwire's own justification is how a house convention
#: gets mistaken for a normative requirement by the next reader.
#:
#: What the architecture suite enforces from this map is the part NR-04 does care
#: about: an adapter must attach to a real owner, depend on it, and never be
#: depended on by it. A file that wanted its own responsibility would fail to be
#: an adapter and would have to be argued as a §12 owner on its own merits.
STAGE4_OWNER_ADAPTERS: dict[str, str] = {
    "point_of_use.py": "admission.py",
    "gate_findings.py": "admission.py",
    "authority_config.py": "contracts.py",
    "frozen_candidates.py": "contracts.py",
    "coordination.py": "admission.py",
    "admission_store.py": "admission.py",
    "admission_journal.py": "persistence.py",
    "library_admission.py": "admission.py",
    "behavior_program_artifacts.py": "behavior.py",
    "library_program_artifacts.py": "library.py",
    "compatibility_store.py": "compatibility.py",
    "knowledge_store.py": "knowledge.py",
    # The one NR-03 adapter point into the protected core: machine construction
    # and restore, canonical snapshot/result codecs, dispatch refusal, exact
    # transition cost and recorded-result injection. The replay owner declares
    # the ports and semantics; this adapter implements them without owning
    # admission, policy, persistence or execution orchestration.
    "replay_vm_adapter.py": "replay.py",
    # The raw reference execution: build or restore the exact machines, drive
    # the admitted set once, report what each behaviour did. It decides nothing
    # — the authority position, the publication rules and the capture record
    # itself are the owner's, and the sequencing is the composition root's.
    "replay_capture.py": "replay.py",
    # §23's durability clause, one adapter per owner, as OD-10/V1 freezes the
    # ownership. Each holds the part of its owner's responsibility that the owner
    # is not allowed to hold itself: where exact bytes live is I/O, and
    # ``activities.py`` says in its own docstring that it performs none.
    #
    # ``replay_store.py`` was briefly declared an owner of its own, which was
    # wrong twice over. It carries no §12 responsibility — a durable request and
    # result history is ``replay.py``'s own record-keeping, not a second subject
    # — and declaring it an owner concealed a cycle: it imports the replay
    # contracts, and ``create_production_replay_binding`` imported
    # ``FileReplayStore`` back. That cycle was first inverted through a
    # registration slot inside the owner, which the adapter filled on import —
    # a first-writer hole, since anything registering a class before
    # ``replay_store`` was imported became the production store for the process.
    # The slot is gone: the owner checks only what it can check without the type,
    # and the exact type is asserted by the composition root that legitimately
    # imports both. That is what makes this line honest.
    "activity_policy_store.py": "activity_policy.py",
    "activity_store.py": "activities.py",
    "replay_store.py": "replay.py",
}

#: Composition roots: the modules that assemble an owner with its concrete
#: adapters. A root is neither a §12 owner nor an adapter, and it needs its own
#: category because it is the one thing the star topology deliberately permits to
#: touch every side. Calling it an adapter would either forbid what it exists to
#: do or quietly license every adapter to do the same.
#:
#: What holds a root honest is the rule in the other direction: nothing inside
#: the package may import one. A root that other modules imported would become a
#: shared bag of whatever was convenient, and the edges it is allowed to have
#: would leak to everything that reached through it. It is imported by an entry
#: point or by an acceptance layer, and by nothing here.
STAGE4_COMPOSITION_ROOTS: dict[str, str] = {
    "library_composition.py": "library.py",
    "replay_composition.py": "replay.py",
}

__all__: tuple[str, ...] = (
    "STAGE4_COMPOSITION_ROOTS",
    "STAGE4_OWNERSHIP_MAP",
    "STAGE4_OWNER_ADAPTERS",
)
