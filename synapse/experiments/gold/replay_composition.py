"""Stage 4 OD-10 — the production composition root for a governed replay.

The one place that holds every side. ``replay.py`` owns what a replay is and may
not import a concrete adapter; ``replay_capture.py`` runs machines and decides
nothing; ``replay_store.py`` holds bytes and knows no rules. None of them can
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
"""

from __future__ import annotations

from .activity_policy import (
    activity_policy_decision_ref,
    require_consumable_activity_decision,
)
from .canonicalization import HashBoundRef
from .replay import (
    ProductionReplayBinding,
    ReferenceCaptureAuthority,
    ReferenceReplayCapture,
    ReplayFailureCode,
    RecordedActivityChannel,
    _CHANNEL_SEAL,
    _evaluate_governed_activities,
    _fail,
    _issue_manifest_from_capture,
    _natural,
    _PreparedReplay,
    capability_profile_digest,
    create_reference_capture_authority,
    require_prepared_replay,
    require_publishable_capture,
    require_reference_capture_authority,
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

__all__ = [
    "capture_reference_replay",
    "create_reference_capture_authority",
    "publish_replay_manifest",
    "require_exact_replay_composition",
]


def require_exact_replay_composition(binding: ProductionReplayBinding) -> ProductionReplayBinding:
    """Assert the exact durable history type the owner is not allowed to name.

    ``replay.py`` declares the history it needs as a port and checks what it can
    check without the type — that the store writes through this authority's
    coordinator. It cannot check the type itself, because importing the adapter
    that implements it would invert the ownership. The check has to be made by a
    party that legitimately imports both sides, and this module is that party.
    """

    binding = validate_production_replay_binding(binding)
    require_production_replay_store(binding.replay_store)
    if type(binding.replay_store) is not FileReplayStore:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "a governed replay runs against the exact production replay history",
        )
    return binding


def capture_reference_replay(
    *,
    prepared: _PreparedReplay,
    binding: ProductionReplayBinding,
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
    prepared = require_prepared_replay(prepared, binding=binding)
    fence = binding.fence
    for name, amount in (
        ("gas_budget", gas_budget),
        ("cognitive_budget", cognitive_budget),
        ("step_limit", step_limit),
    ):
        _natural(amount, name, maximum=2**53)

    bindings = prepared.bindings

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
            gas_budget=gas_budget,
        )
    else:
        # Built from the admitted programs, so a fresh run's starting state is a
        # consequence of what was admitted rather than an object handed in.
        machines = build_reference_machines(prepared.programs, gas_budget=gas_budget)
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

    capture, incomplete = seal_reference_capture(
        prepared=prepared,
        binding=binding,
        runs=runs,
        machines=machines,
        snapshot_refs=snapshot_refs,
        initial_digests=initial_digests,
        decision_refs=decision_refs,
        gas_budget=gas_budget,
        cognitive_budget=cognitive_budget,
        step_limit=step_limit,
        resumed_from_result_ref=resumed_from_result_ref,
    )
    with store_transaction(fence) as ticket:
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
