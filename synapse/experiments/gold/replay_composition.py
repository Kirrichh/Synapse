"""Stage 4 OD-10 — the production composition root for a governed replay.

The one place that holds every side. ``replay.py`` owns what a replay is and may
not import a concrete adapter; ``replay_vm_adapter.py`` owns the protected-core
integration; ``replay_capture.py`` drives machines and decides nothing;
``replay_store.py`` holds bytes and knows no rules. None of them can
assemble a run, and that is deliberate — a module able to assemble one would be
able to point it at whatever it liked.

This module does the assembling, and it is the only module allowed to. It imports
the owner and the concrete adapters, checks their exact types, and sequences the
preparation of an attempt: evaluate the governing policy, make the starting state
and the decisions durable, revalidate, run the reference execution on the exact
machine, seal what it observed as a record the owner defines, and — separately —
issue the manifest a later governed run is measured against.

Every rule it applies belongs to the owner and is called by name. Nothing here
decides whether a capture may be published, what a capture record contains, or
which actors a decision concerns; it decides only the order in which those
questions are asked, which is the one thing a composition root is for.

That is also why one attempt's *whole* governed replay is assembled here rather
than by whoever runs the attempt. Reference execution, manifest and governed run
are three phases whose order is load-bearing, and the party that owns the order
is the party that imports every side of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .activities import (
    ActivityInputs,
    ActivityKind,
    ActivityPosition,
    ActivityRecordContext,
    RecordedActivity,
    activity_ref,
    compute_activity_identity,
    record_activity,
)
from .activity_policy import (
    activity_policy_decision_ref,
    issue_activity_recorder_entitlement,
    require_activity_policy_evaluator,
    require_consumable_activity_decision,
)
from .activity_provenance import record_activity_production_provenance
from .activity_store import activity_result_ref
from .canonicalization import HashBoundRef
from .contracts import RepositoryRevision
from .library_program_artifacts import (
    LibraryProgramArtifactReader,
    validate_library_program_artifact_reader,
)
from .point_of_use import (
    ProductionAuthorityBinding,
    validate_production_authority_binding,
)
from .replay_attempt_boundary import recover_interrupted_replay_attempts
from .coordination import settle_exclusive_mutation
from .replay import (
    ProductionReplayBinding,
    REPLAY_MACHINE_ADAPTER_ID_V1_E1,
    ReferenceCaptureAuthority,
    ReferenceReplayCapture,
    ReplayFailureCode,
    RecordedActivityChannel,
    _CHANNEL_SEAL,
    _create_production_replay_binding,
    _evaluate_governed_activities,
    _fail,
    _issue_manifest_from_capture,
    _natural,
    _prepare_replay,
    _resume_governed_replay,
    _run_governed_replay,
    ReplaySubject,
    capability_profile_digest,
    create_reference_capture_authority,
    require_prepared_replay,
    require_publishable_capture,
    require_reference_capture_authority,
    replay_machine_execution_context,
    require_settled_execution_world,
    seal_reference_capture,
    validate_production_replay_binding,
)
from .replay_capture import (
    build_reference_machines,
    drive_reference_execution,
    reference_machine_snapshots,
    restore_reference_machines,
)
from .replay_store import FileReplayStore, require_production_replay_store
from .replay_machine_binding import (
    ProductionReplayMachineFactory,
    _PRODUCTION_MACHINE_FACTORY_SEAL,
    require_production_replay_machine_factory,
)
from .replay_vm_adapter import CognitiveVMReplayMachineFactory

ACTIVITY_RECORDER_COMPONENT_V1 = "synapse.stage4.gold.activity-recorder.v1"

__all__ = [
    "AttemptReplayBindings",
    "GoldAttemptReplay",
    "ReplayBudgets",
    "capture_reference_replay",
    "create_production_replay_binding",
    "create_reference_capture_authority",
    "publish_replay_manifest",
    "record_observed_activity",
    "replay_one_governed_attempt",
    "require_exact_replay_composition",
    "resume_governed_replay",
    "run_governed_replay",
]


def create_production_replay_binding(
    *,
    authority: ProductionAuthorityBinding,
    initial_admission: object,
    final_admission: object,
    activity_policy_evaluator: object,
    activity_store: object,
    activity_policy_store: object,
    replay_store: object,
    artifact_reader: LibraryProgramArtifactReader,
) -> ProductionReplayBinding:
    """Assemble the owner with the one exact production machine factory.

    No factory argument is exposed. A caller may select the authority domain and
    the durable stores it owns, but it cannot substitute the machine that a
    replay verdict will be read from. The owner validates the binding contract;
    this root supplies and checks the concrete adapters it is forbidden to name.
    """

    authority = validate_production_authority_binding(authority)
    evaluator = require_activity_policy_evaluator(activity_policy_evaluator)
    require_production_replay_store(replay_store)
    reader = validate_library_program_artifact_reader(artifact_reader)
    if replay_store.mutation_fence is not authority.fence:
        raise _fail(
            ReplayFailureCode.ADMISSION_NOT_CURRENT,
            "the replay history belongs to another authority coordinator",
        )
    if reader.mutation_fence is not authority.fence:
        raise _fail(
            ReplayFailureCode.ADMISSION_NOT_CURRENT,
            "the program artifact reader belongs to another authority coordinator",
        )
    recovered = recover_interrupted_replay_attempts(
        store=replay_store,
        fence=authority.fence,
        settle=settle_exclusive_mutation,
    )
    if recovered:
        raise _fail(
            ReplayFailureCode.ADMISSION_NOT_CURRENT,
            "interrupted replay attempts were recovered; fresh admissions are required",
        )

    delegate = CognitiveVMReplayMachineFactory()
    if type(delegate) is not CognitiveVMReplayMachineFactory:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "production replay requires the exact CognitiveVM machine factory",
        )
    machine_factory = ProductionReplayMachineFactory(
        delegate,
        expected_adapter_id=REPLAY_MACHINE_ADAPTER_ID_V1_E1,
        _seal=_PRODUCTION_MACHINE_FACTORY_SEAL,
    )
    binding = _create_production_replay_binding(
        authority=authority,
        initial_admission=initial_admission,
        final_admission=final_admission,
        activity_policy_evaluator=evaluator,
        activity_store=activity_store,
        activity_policy_store=activity_policy_store,
        replay_store=replay_store,
        artifact_resolver=reader,
        machine_factory=machine_factory,
    )
    return require_exact_replay_composition(binding)


def require_exact_replay_composition(binding: ProductionReplayBinding) -> ProductionReplayBinding:
    """Assert the exact concrete adapters the owner is not allowed to name.

    ``replay.py`` declares the history and machine-factory ports and checks what
    it can without concrete types. Importing either implementation would invert
    the ownership. This root legitimately imports every side, so it verifies both
    the exact durable history and the exact factory/identity bound into the
    immutable production configuration.
    """

    binding = validate_production_replay_binding(binding)
    require_production_replay_store(binding.replay_store)
    if type(binding.replay_store) is not FileReplayStore:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "a governed replay runs against the exact production replay history",
        )
    machine_factory = require_production_replay_machine_factory(
        binding.machine_factory, expected_adapter_id=REPLAY_MACHINE_ADAPTER_ID_V1_E1
    )
    delegate = machine_factory.delegate
    if (
        type(delegate) is not CognitiveVMReplayMachineFactory
        or delegate.adapter_id() != REPLAY_MACHINE_ADAPTER_ID_V1_E1
    ):
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "a governed replay runs on the exact production CognitiveVM adapter",
        )
    reader = validate_library_program_artifact_reader(binding.artifact_resolver)
    if reader.mutation_fence is not binding.fence:
        raise _fail(
            ReplayFailureCode.ADMISSION_NOT_CURRENT,
            "the exact Library artifact reader changed coordinator",
        )
    return binding


def capture_reference_replay(
    *,
    admission: object,
    binding: ProductionReplayBinding,
    subjects: tuple[ReplaySubject, ...],
    compiler: object,
    activity_refs: tuple[HashBoundRef, ...],
    capture_authority: ReferenceCaptureAuthority,
    gas_budget: int,
    cognitive_budget: int,
    step_limit: int,
    resumed_from_result_ref: HashBoundRef | None = None,
) -> HashBoundRef:
    """Run the reference execution and record what it reached. The prepared phase.

    This is where an expected outcome stops being anybody's statement. The run
    happens on the exact machine, over the same durable activity history a
    governed replay uses, through the same transition driver — so no external
    call happens and no second execution semantics exists. Whatever that run
    reaches is what the capture says.

    A continuation names only ``resumed_from_result_ref``. It resolves that
    result in this exact store and restores from the terminal references the
    result itself recorded, rather than re-running the earlier attempt or being
    told where to start: a restart must not depend on the whole lineage still
    being resolvable, one damaged early link must not make every later
    continuation unreachable, and where a continuation begins is a fact about
    the predecessor rather than a parameter.

    The reference run has to succeed. A capture of a run that diverged, ran out
    of budget or faulted would be a manifest saying "the expected outcome is this
    failure", and a later replay reproducing it would be reported as identical.
    Such a capture is still made durable first and refused second: the record
    exists either way, and what does not happen is a manifest.
    """

    from .persistence import store_transaction

    binding = require_exact_replay_composition(binding)
    require_reference_capture_authority(capture_authority, binding=binding)
    prepared = _prepare_replay(
        admission=admission,
        binding=binding,
        subjects=subjects,
        compiler=compiler,
        activity_refs=activity_refs,
    )
    prepared = require_prepared_replay(prepared, binding=binding)
    fence = binding.fence
    for name, amount in (
        ("gas_budget", gas_budget),
        ("cognitive_budget", cognitive_budget),
        ("step_limit", step_limit),
    ):
        _natural(amount, name, maximum=2**53)

    bindings = prepared.bindings
    admitted = prepared.admitted
    envelope = admitted.envelope
    execution_context = replay_machine_execution_context(
        run_id=envelope.run_id,
        attempt_id=envelope.attempt_id,
        repository_revision=envelope.repository_revision,
        environment_profile_id=envelope.environment_profile_id,
        policy_version=admitted.policy_version,
    )

    if resumed_from_result_ref is not None:
        # A continuation names its predecessor and nothing else. It used to take
        # the starting snapshot references as an argument, which put the choice
        # of where a continuation starts back in the caller's hands — the very
        # thing the durable terminal reference exists to remove.
        predecessor = binding.replay_store.require_result(resumed_from_result_ref)
        snapshot_refs = tuple(
            item.terminal_snapshot_ref for item in predecessor.observations
        )
        if len(snapshot_refs) != len(bindings):
            raise _fail(
                ReplayFailureCode.MACHINE_COUNT_MISMATCH,
                "the predecessor did not record one terminal state for each behavior",
            )
        machines = restore_reference_machines(
            tuple(binding.replay_store.open_snapshot(item) for item in snapshot_refs),
            machine_factory=binding.machine_factory,
            execution_context=execution_context,
            gas_budget=gas_budget,
        )
    else:
        # Built from the admitted programs, so a fresh run's starting state is a
        # consequence of what was admitted rather than an object handed in.
        machines = build_reference_machines(
            prepared.programs,
            machine_factory=binding.machine_factory,
            execution_context=execution_context,
            gas_budget=gas_budget,
        )
        with store_transaction(fence) as ticket:
            snapshot_refs = tuple(
                binding.replay_store.put_snapshot(item, ticket=ticket)
                for item in reference_machine_snapshots(machines)
            )

    initial_digests = tuple(machine.snapshot_digest() for machine in machines)

    # Freshly, before a single recorded byte reaches a machine. A record already
    # in the store is not thereby consumable *now*: it may have been forbidden
    # since, its policy version may have moved, or the lifecycle and taint
    # anchors the decision rested on may have advanced.
    decisions = _evaluate_governed_activities(prepared, binding=binding)
    decision_refs = tuple(activity_policy_decision_ref(item) for item in decisions)

    # Durable, and then read back. An earlier revision evaluated the decisions,
    # put their references into the capture and never wrote the decisions
    # themselves — so a capture named policy records that existed only inside the
    # call that made it. What permitted a reference run has to outlive the run,
    # for the same reason the run's own snapshots do.
    if decisions:
        with store_transaction(fence) as ticket:
            for decision, expected in zip(decisions, decision_refs):
                stored = binding.activity_policy_store.append_decision(
                    decision,
                    evaluator=binding.activity_policy_evaluator,
                    consumption=binding.consumption_provenance,
                    ticket=ticket,
                )
                if stored.to_dict() != expected.to_dict():
                    raise _fail(
                        ReplayFailureCode.IDENTITY_MISMATCH,
                        "a durable reference-phase policy decision changed identity",
                    )
    for activity, reference in zip(prepared.ledger.recorded(), decision_refs):
        production = (
            binding.activity_policy_store.require_production_provenance_for_activity(
                activity.production_provenance_ref,
                evaluator=binding.activity_policy_evaluator,
                activity=activity,
            )
        )
        restored = binding.activity_policy_store.require_decision(
            reference, evaluator=binding.activity_policy_evaluator
        )
        require_consumable_activity_decision(
            restored,
            evaluator=binding.activity_policy_evaluator,
            activity=activity,
            consumer_context_ref=prepared.admitted.consumer_context_ref,
            boundary_ref=prepared.admitted.boundary_ref,
            run_id=prepared.admitted.envelope.run_id,
            attempt_id=prepared.admitted.envelope.attempt_id,
            environment_profile_id=prepared.admitted.envelope.environment_profile_id,
            capability_profile_digest=capability_profile_digest(),
            production=production,
            consumption=binding.consumption_provenance,
        )

    # The last revalidation, and it is last on purpose. Every preparatory write
    # above opened a mutation interval and moved the head the admission was taken
    # against, so a check made before them would have been a check of a world
    # this call then changed. Nothing caller-controlled runs between here and the
    # first transition.
    require_settled_execution_world(
        prepared.admitted,
        snapshot_manifest_ref=prepared.snapshot_manifest_ref,
        authority=binding.authority,
    )

    channel = RecordedActivityChannel(
        prepared.ledger, cognitive_budget, binding.activity_store, _seal=_CHANNEL_SEAL
    )
    try:
        runs = drive_reference_execution(
            bindings=bindings,
            machines=machines,
            channel=channel,
            gas_budget=gas_budget,
            step_limit=step_limit,
        )
    finally:
        channel.close()

    structural_histories = tuple(
        machine.structural_history_bytes() for machine in machines
    )
    with store_transaction(fence) as ticket:
        structural_history_refs = tuple(
            binding.replay_store.put_structural_history(raw, ticket=ticket)
            for raw in structural_histories
        )
        capture, incomplete = seal_reference_capture(
            prepared=prepared,
            binding=binding,
            runs=runs,
            machines=machines,
            snapshot_refs=snapshot_refs,
            initial_digests=initial_digests,
            structural_history_refs=structural_history_refs,
            decision_refs=decision_refs,
            gas_budget=gas_budget,
            cognitive_budget=cognitive_budget,
            step_limit=step_limit,
            resumed_from_result_ref=resumed_from_result_ref,
        )
        reference = binding.replay_store.append_capture(capture, ticket=ticket)
    if incomplete:
        # Durable first, refused second. The snapshots this preparation already
        # wrote are durable, so raising without a record would leave blobs nobody
        # can account for and no record of why preparing stopped.
        reason = capture.contract_failure_reason
        raise _fail(
            ReplayFailureCode.CAPTURE_NOT_CONFORMANT,
            "the reference execution did not complete: "
            + (reason.value if reason is not None else "unknown"),
        )
    return reference


def publish_replay_manifest(
    *,
    binding: ProductionReplayBinding,
    capture_authority: ReferenceCaptureAuthority,
    capture_ref: HashBoundRef,
) -> HashBoundRef:
    """Turn a durable reference capture into the manifest a run is measured by.

    The manifest authority takes no expected values. It used to take an expected
    transcript root and expected terminal digests as arguments, and moving the
    moment they were written earlier did not change whose values they were — the
    party asking for the run still said what the run was supposed to produce. Now
    the only input is a reference to a capture the store already holds, and every
    expected value in the manifest is read out of it.

    Whether that capture *may* be published is not decided here. It is a rule
    about what a replay record means, so the owner states it and this call asks —
    including the continuation exemption, which is why the predecessor is
    resolved in this exact store first: an exemption granted for the presence of
    a field would be an exemption for a field the caller filled in.
    """

    from .persistence import store_transaction

    binding = require_exact_replay_composition(binding)
    require_reference_capture_authority(capture_authority, binding=binding)
    capture: ReferenceReplayCapture = binding.replay_store.require_capture(capture_ref)
    continuation = None
    if capture.capture_resumed_from_result_ref is not None:
        continuation = binding.replay_store.require_result(
            capture.capture_resumed_from_result_ref
        )
    require_publishable_capture(capture, binding=binding, continuation=continuation)
    manifest = _issue_manifest_from_capture(
        authority=binding.authority,
        capture=capture,
        capture_ref=capture_ref,
    )
    with store_transaction(binding.fence) as ticket:
        return binding.replay_store.append_manifest(manifest, ticket=ticket)


def record_observed_activity(
    *,
    binding: ProductionReplayBinding,
    kind: ActivityKind,
    inputs: ActivityInputs,
    position: ActivityPosition,
    result: bytes,
) -> RecordedActivity:
    """Persist one live result and its subject-bound provenance in exact order.

    The caller supplies only facts observed at the effect boundary.  Policy,
    actors, execution identity, component identity and time all come from the
    sealed production configuration.  Each durable object is read back before
    the next object is authorized, so a record can never outrun either its exact
    result bytes or the provenance that entitled its recorder.
    """

    from .persistence import store_transaction

    binding = require_exact_replay_composition(binding)
    evaluator = require_activity_policy_evaluator(
        binding.activity_policy_evaluator
    )

    # The activity result codec is part of the frozen VM integration.  Merely
    # hashing arbitrary bytes would bind the record to bytes while leaving the
    # value those bytes denote ambiguous.
    machine_factory = require_production_replay_machine_factory(
        binding.machine_factory,
        expected_adapter_id=REPLAY_MACHINE_ADAPTER_ID_V1_E1,
    )
    delegate = machine_factory.delegate
    if type(delegate) is not CognitiveVMReplayMachineFactory:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "activity recording requires the exact production VM adapter",
        )
    delegate.validate_recorded_result(result)
    expected_result_ref = activity_result_ref(result)
    controller = binding.authority.controller
    context = ActivityRecordContext(
        run_id=controller.run_id,
        attempt_id=controller.attempt_id,
        repository_revision=RepositoryRevision.git_commit(
            controller.repository_revision
        ),
        environment_profile_id=controller.environment_profile_id,
        producer_component=ACTIVITY_RECORDER_COMPONENT_V1,
    )
    expected_identity = compute_activity_identity(
        kind=kind,
        inputs=inputs,
        policy_version=evaluator.declaration.policy_version,
        position=position,
        result_sha256=expected_result_ref.sha256,
        result_ref=expected_result_ref,
    )
    existing = tuple(
        item
        for item in binding.activity_store.recorded_activities()
        if item.activity_identity == expected_identity
    )
    if existing:
        restored = existing[0]
        envelope = restored.envelope
        if (
            len(existing) != 1 or restored.kind is not kind
            or restored.inputs != inputs or restored.position != position
            or restored.policy_version != evaluator.declaration.policy_version
            or restored.result_ref.to_dict() != expected_result_ref.to_dict()
            or envelope.run_id != context.run_id or envelope.attempt_id != context.attempt_id
            or envelope.repository_revision != context.repository_revision
            or envelope.environment_profile_id != context.environment_profile_id
            or envelope.producer_component != context.producer_component
        ):
            raise _fail(
                ReplayFailureCode.IDENTITY_MISMATCH,
                "an existing activity identity belongs to another observed occurrence",
            )
        if binding.activity_store.open_result(restored.result_ref) != result:
            raise _fail(
                ReplayFailureCode.IDENTITY_MISMATCH,
                "the existing activity result differs from the observed bytes",
            )
        binding.activity_policy_store.require_production_provenance_for_activity(
            restored.production_provenance_ref,
            evaluator=evaluator,
            activity=restored,
        )
        return restored
    with store_transaction(binding.fence) as ticket:
        stored_result_ref = binding.activity_store.put_result(result, ticket=ticket)
    if stored_result_ref.to_dict() != expected_result_ref.to_dict():
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "the durable activity result changed its content identity",
        )
    if binding.activity_store.open_result(stored_result_ref) != result:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "the durable activity result changed during read-back",
        )

    production = record_activity_production_provenance(
        evaluator.provenance_authority,
        kind=kind,
        inputs=inputs,
        position=position,
        result=result,
        result_ref=stored_result_ref,
        context=context,
    )
    with store_transaction(binding.fence) as ticket:
        production_ref = binding.activity_policy_store.append_production_provenance(
            production,
            evaluator=evaluator,
            ticket=ticket,
        )
    restored_production = binding.activity_policy_store.require_production_provenance(
        production_ref,
        evaluator=evaluator,
    )
    if restored_production.to_dict() != production.to_dict():
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "the durable activity production provenance changed during read-back",
        )

    entitlement = issue_activity_recorder_entitlement(
        evaluator,
        production=restored_production,
    )
    observed = record_activity(
        kind=kind,
        inputs=inputs,
        position=position,
        result=result,
        result_ref=stored_result_ref,
        context=context,
        entitlement=entitlement,
    )
    observed_ref = activity_ref(observed)
    with store_transaction(binding.fence) as ticket:
        binding.activity_store.append_record(observed, ticket=ticket)
    restored = binding.activity_store.require_record(observed_ref)
    binding.activity_policy_store.require_production_provenance_for_activity(
        restored.production_provenance_ref,
        evaluator=evaluator,
        activity=restored,
    )
    if restored.to_dict() != observed.to_dict():
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "the durable activity record changed during read-back",
        )
    return restored


def run_governed_replay(
    *,
    admission: object,
    binding: ProductionReplayBinding,
    subjects: tuple[ReplaySubject, ...],
    compiler: object,
    activity_refs: tuple[HashBoundRef, ...],
    manifest_ref: HashBoundRef,
    gas_budget: int,
    cognitive_budget: int,
    step_limit: int,
):
    """Execute through the one exact production composition."""

    binding = require_exact_replay_composition(binding)
    return _run_governed_replay(
        admission=admission,
        binding=binding,
        subjects=subjects,
        compiler=compiler,
        activity_refs=activity_refs,
        manifest_ref=manifest_ref,
        gas_budget=gas_budget,
        cognitive_budget=cognitive_budget,
        step_limit=step_limit,
    )


def resume_governed_replay(
    *,
    admission: object,
    binding: ProductionReplayBinding,
    subjects: tuple[ReplaySubject, ...],
    compiler: object,
    manifest_ref: HashBoundRef,
    resumed_from_result_ref: HashBoundRef,
    gas_budget: int,
    cognitive_budget: int,
    step_limit: int,
):
    """Resume through the same exact composition and durable predecessor."""

    binding = require_exact_replay_composition(binding)
    return _resume_governed_replay(
        admission=admission,
        binding=binding,
        subjects=subjects,
        compiler=compiler,
        manifest_ref=manifest_ref,
        resumed_from_result_ref=resumed_from_result_ref,
        gas_budget=gas_budget,
        cognitive_budget=cognitive_budget,
        step_limit=step_limit,
    )


@dataclass(frozen=True)
class ReplayBudgets:
    """The three budgets one governed replay runs under."""

    gas_budget: int
    cognitive_budget: int
    step_limit: int


@dataclass(frozen=True)
class AttemptReplayBindings:
    """The Stage 9 durable stores and the policy evaluator one attempt replays through.

    Five objects rather than one store, because they answer different questions
    and a deployment may back them differently: what a replay recorded, what
    activities happened, what the policy decided about them, how that policy is
    evaluated, and where a behavior's program bytes are read from.
    """

    replay_store: object
    activity_store: object
    activity_policy_store: object
    activity_policy_evaluator: object
    artifact_reader: object


def replay_one_governed_attempt(
    *,
    bindings: AttemptReplayBindings,
    subjects: tuple[ReplaySubject, ...],
    compiler: object,
    admission_source: Callable[[], object],
    budgets: ReplayBudgets,
    reference_budgets: ReplayBudgets | None = None,
    observed_activities: tuple[tuple[ActivityKind, ActivityInputs, ActivityPosition, bytes], ...] = (),
):
    """Take one attempt's reference execution and replay it under governance.

    The order is the whole of it, and it is why this is here rather than at each
    call site.

    Every Stage 9 write -- the observed activities, the reference capture, the
    manifest issued from it -- happens *before* the attempt's final admission is
    taken. Appending to a Stage 9 store opens a mutation interval on the shared
    coordinator, so an admission settled earlier would find its epoch advanced
    by its own preparation and the run would correctly refuse itself. An
    assembly free to take the admission first looks identical and fails only
    once a store is actually written to.

    The manifest is issued per attempt and cannot be prepared ahead of one:
    ``_require_manifest_describes`` compares its run, attempt, revision,
    environment profile and policy version against the admission the run is
    crossing under, and refuses a manifest issued for another execution
    identity. So the reference execution belongs to this attempt's preparation,
    exactly as ``capture_reference_replay`` says of itself.

    The reference run is taken under budgets that let it finish. Starving the
    run that is being judged is a legitimate thing to ask for; starving the
    observation it is judged *against* would only mean there is nothing to judge
    it by, so ``reference_budgets`` is separate and defaults to the same.
    """

    if type(bindings) is not AttemptReplayBindings:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "attempt replay bindings must be exact")
    if type(budgets) is not ReplayBudgets:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "replay budgets must be exact")
    if type(subjects) is not tuple or not subjects:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH, "a governed replay needs its admitted subjects"
        )
    if not callable(admission_source):
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "the admission source must be callable")
    reference = budgets if reference_budgets is None else reference_budgets
    if type(reference) is not ReplayBudgets:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "reference budgets must be exact")

    #: Asked here, not in ``replay.py``: the owner defines the history contract
    #: and may not import the adapter that implements it, so the exactness is
    #: asserted by the one party that imports both.
    require_production_replay_store(bindings.replay_store)

    initial_admission = admission_source()
    capture_binding = _attempt_replay_binding(
        bindings,
        initial_admission=admission_source(),
        final_admission=admission_source(),
    )
    activity_refs = tuple(
        activity_ref(
            record_observed_activity(
                binding=capture_binding,
                kind=kind,
                inputs=inputs,
                position=position,
                result=result,
            )
        )
        for kind, inputs, position, result in observed_activities
    )
    #: One executor for both phases. §9.4 names seven actors and a reference run
    #: adds no eighth; the two phases are told apart by phase and by record
    #: identity. What must stay separate is the authority that seals the
    #: capture, which is not an executor at all.
    capture_authority = create_reference_capture_authority(binding=capture_binding)
    capture_ref = capture_reference_replay(
        admission=capture_binding.initial_admission,
        binding=capture_binding,
        subjects=subjects,
        compiler=compiler,
        activity_refs=activity_refs,
        capture_authority=capture_authority,
        gas_budget=reference.gas_budget,
        cognitive_budget=reference.cognitive_budget,
        step_limit=reference.step_limit,
    )
    manifest_ref = publish_replay_manifest(
        #: The same binding the capture was taken through: its executor is the
        #: one that will consume the manifest, which is what the authority
        #: independence check is about.
        binding=capture_binding,
        capture_authority=capture_authority,
        capture_ref=capture_ref,
    )

    #: Only now. Everything above wrote to a Stage 9 store.
    governed_binding = _attempt_replay_binding(
        bindings,
        initial_admission=initial_admission,
        final_admission=admission_source(),
    )
    return run_governed_replay(
        admission=initial_admission,
        binding=governed_binding,
        subjects=subjects,
        compiler=compiler,
        activity_refs=activity_refs,
        manifest_ref=manifest_ref,
        gas_budget=budgets.gas_budget,
        cognitive_budget=budgets.cognitive_budget,
        step_limit=budgets.step_limit,
    )


def _attempt_replay_binding(
    bindings: AttemptReplayBindings,
    *,
    initial_admission: object,
    final_admission: object,
) -> ProductionReplayBinding:
    """One phase's binding over the five durable sides this deployment supplies."""

    return create_production_replay_binding(
        authority=final_admission.binding,
        initial_admission=initial_admission,
        final_admission=final_admission,
        activity_policy_evaluator=bindings.activity_policy_evaluator,
        activity_store=bindings.activity_store,
        activity_policy_store=bindings.activity_policy_store,
        replay_store=bindings.replay_store,
        artifact_reader=bindings.artifact_reader,
    )


class GoldAttemptReplay:
    """One attempt's governed replay, as the port a run's input source calls.

    Sealed to one attempt on purpose. A replay result carries the observations a
    worker's context is built from, and handing the same one to a second attempt
    would let that attempt report knowledge it never admitted for itself.
    """

    def __init__(
        self,
        *,
        bindings: AttemptReplayBindings,
        subjects: tuple[ReplaySubject, ...],
        compiler: object,
        admission_source: Callable[[], object],
        budgets: ReplayBudgets,
        reference_budgets: ReplayBudgets | None = None,
    ) -> None:
        self._bindings = bindings
        self._subjects = subjects
        self._compiler = compiler
        self._admission_source = admission_source
        self._budgets = budgets
        self._reference_budgets = reference_budgets
        self._replayed: object | None = None

    def replay_for_attempt(self, *, manifest, attempt_index: int):
        """Replay this attempt's admitted behaviors, once."""

        del manifest, attempt_index
        if self._replayed is not None:
            raise _fail(
                ReplayFailureCode.ADMISSION_NOT_CURRENT,
                "an attempt's governed replay may be taken only once",
            )
        self._replayed = replay_one_governed_attempt(
            bindings=self._bindings,
            subjects=self._subjects,
            compiler=self._compiler,
            admission_source=self._admission_source,
            budgets=self._budgets,
            reference_budgets=self._reference_budgets,
        )
        return self._replayed
