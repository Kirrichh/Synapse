"""Stage 4 OD-10 — the prepared phase: a reference execution, and the manifest it issues.

An adapter of ``replay.py``, not a second owner. It carries the part of that
module's responsibility which belongs to the *preparation* of an attempt rather
than to the attempt: running the behaviour once as a reference, recording what
that run reached, and issuing the manifest a governed replay is later measured
against. The owner keeps what a replay *is* — the records, the machine
integration, the execution contract — and this file keeps the sequence that
happens before a run is asked for.

Nothing here is a new entry point. A reference capture is a phase of the
lifecycle that already existed: an attempt was always prepared before it was
executed, and preparing it used to mean a caller stating what the run should
produce. The correction is that the statement is now an observation.

What a reference capture establishes is reproducibility, and only that. It is not
an oracle, it does not establish `FULL`, and it says nothing about whether the
behaviour is correct.
"""

from __future__ import annotations

from dataclasses import dataclass
import json

from .canonicalization import HashBoundRef
from .contracts import (
    ActorIdentity,
    IdentityDomain,
    RecordId,
    SchemaVersion,
)
from .point_of_use import ProductionAuthorityBinding, validate_production_authority_binding
from .activity_policy import activity_policy_decision_ref
from .replay import (
    _CAPTURE_AUTHORITY_SEAL,
    _CAPTURE_SEAL,
    _CHANNEL_SEAL,
    _PreparedReplay,
    CognitiveVMReplayAdapter,
    ProductionReplayBinding,
    RecordedActivityChannel,
    ReferenceReplayCapture,
    ReplayFailureCode,
    ReplayFailureReason,
    ReplayRecordContext,
    ReplaySubject,
    _capture_payload,
    _check_execution_contract,
    _drive_one_behavior,
    _evaluate_governed_activities,
    _envelope_for,
    _fail,
    _natural,
    _snapshot_bytes_of,
    capability_profile_digest,
    require_prepared_replay,
    _issue_manifest_from_capture,
    require_manifest_projects_capture,
    transcript_root,
    validate_production_replay_binding,
    validate_reference_capture,
)

@dataclass(frozen=True, init=False)
class ReferenceCaptureAuthority:
    """The platform authority that may seal a capture and issue a manifest from it.

    It declares no actor. An earlier revision took a ``capture_authority_actor``
    from its caller and checked only that the name differed from the executor's,
    which made the authority a string the caller chose — the same
    caller-declared-identity defect the actor set exists to prevent, arriving
    through the preparation phase.

    What an authority is here is a *position*: it holds the exact production
    binding, the coordinator that binding writes through, and the execution
    domain those two define. It is minted from a validated binding and from
    nothing else, so there is no name to forge and nothing to compare against an
    actor set. §9.4 is untouched: no eighth role is introduced, and the same
    ``replay_executor_actor`` performs both phases.
    """

    binding: ProductionReplayBinding
    authority: ProductionAuthorityBinding
    fence: object
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ReferenceCaptureAuthority:
        raise TypeError("ReferenceCaptureAuthority is issued only by its factory")


def create_reference_capture_authority(
    *,
    binding: ProductionReplayBinding,
) -> ReferenceCaptureAuthority:
    """Take the authority position this binding defines.

    Nothing is supplied but the binding, and the binding is revalidated here
    rather than trusted from whenever it was assembled.
    """

    binding = validate_production_replay_binding(binding)
    validate_production_authority_binding(binding.authority)
    payload = object.__new__(ReferenceCaptureAuthority)
    object.__setattr__(payload, "binding", binding)
    object.__setattr__(payload, "authority", binding.authority)
    object.__setattr__(payload, "fence", binding.fence)
    object.__setattr__(payload, "_trusted_seal", _CAPTURE_AUTHORITY_SEAL)
    return payload


def require_reference_capture_authority(
    value: object, *, binding: ProductionReplayBinding
) -> ReferenceCaptureAuthority:
    """Refuse an authority that is not sealed, or belongs to another binding."""

    if (
        type(value) is not ReferenceCaptureAuthority
        or getattr(value, "_trusted_seal", None) is not _CAPTURE_AUTHORITY_SEAL
    ):
        raise _fail(
            ReplayFailureCode.TRUSTED_OBJECT_FORGED,
            "a reference capture requires a sealed capture authority",
        )
    if value.binding is not binding or value.fence is not binding.fence:
        raise _fail(
            ReplayFailureCode.ADMISSION_NOT_CURRENT,
            "this capture authority belongs to another production binding",
        )
    return value


def _is_contract_departure(run: object) -> bool:
    """Whether the only thing wrong with this run is that it left its contract.

    A transcript that does not match the behaviour's ``ReplayContract`` is a fact
    about the behaviour, not a failure of the machine or of the infrastructure —
    the run completed, it simply did not do what the contract said. Such a run is
    still recorded as a capture, marked non-conformant, and refused publication.
    Anything else — a fault, an exhausted budget, a forbidden call — means the
    reference execution did not complete, and there is nothing to capture.
    """

    return run.failure_reason is ReplayFailureReason.TRANSITION_MISMATCH


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

    This is where an expected outcome stops being anybody's statement. The
    machines are built here from the admitted programs — the exact
    ``CognitiveVMReplayAdapter``, never a scripted port, because a port answers
    every question about itself and a capture taken from one would record a
    transcript nothing executed. They are driven through the same
    ``_drive_one_behavior`` a governed replay uses, over the same durable
    activity history, so no external call happens and no second execution
    semantics exists. Whatever that run reaches is what the capture says.

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

    What this establishes is reproducibility, and only that. It is not an oracle
    verdict, it does not establish `FULL`, and it says nothing about whether the
    behaviour is correct.
    """

    from .persistence import store_transaction

    binding = validate_production_replay_binding(binding)
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

    # A continuation names its predecessor and nothing else. It used to take the
    # starting snapshot references as an argument, which put the choice of where a
    # continuation starts back in the caller's hands — the very thing the durable
    # terminal reference exists to remove. The predecessor is resolved in this
    # exact store and its own recorded terminal references are used.
    if resumed_from_result_ref is not None:
        predecessor = binding.replay_store.require_result(resumed_from_result_ref)
        snapshot_refs = tuple(
            item.terminal_snapshot_ref for item in predecessor.observations
        )
        if len(snapshot_refs) != len(bindings):
            raise _fail(
                ReplayFailureCode.MACHINE_COUNT_MISMATCH,
                "the predecessor did not record one terminal state for each behavior",
            )
        machines = _machines_from_snapshots(
            snapshot_refs, binding=binding, gas_budget=gas_budget
        )
    else:
        # Built here, from the admitted programs, so a fresh run's starting state
        # is a consequence of what was admitted rather than an object handed in.
        # The programs this admission covered, compiled once inside the barrier
        # and carried here. Asking the compiler again would run whatever it
        # returned the second time, which nothing admitted.
        machines = tuple(
            CognitiveVMReplayAdapter(program, gas_budget=gas_budget)
            for program in prepared.programs
        )
        raw = tuple(_snapshot_bytes_of(machine) for machine in machines)
        with store_transaction(fence) as ticket:
            snapshot_refs = tuple(
                binding.replay_store.put_snapshot(item, ticket=ticket) for item in raw
            )

    initial_digests = tuple(machine.snapshot_digest() for machine in machines)

    # Freshly, before a single recorded byte reaches a machine. A record already
    # in the store is not thereby consumable *now*: it may have been forbidden
    # since, its policy version may have moved, or the lifecycle and taint anchors
    # the decision rested on may have advanced. A capture that injected a durable
    # result without asking would let a manifest be built on a forbidden or stale
    # activity, and every later run measured against that manifest would inherit
    # the omission. The decisions are pinned into the capture so what permitted
    # the reference run is part of the record rather than a fact about the moment.
    decisions = _evaluate_governed_activities(prepared, binding=binding)
    decision_refs = tuple(activity_policy_decision_ref(item) for item in decisions)

    channel = RecordedActivityChannel(
        prepared.ledger, cognitive_budget, binding.activity_store, _seal=_CHANNEL_SEAL
    )
    transitions: list[str] = []
    activities: list[str] = []
    terminal_digests: list[str] = []
    contract_matched = True
    contract_failure_reason: ReplayFailureReason | None = None
    incomplete = False
    try:
        for program_binding, machine in zip(bindings, machines):
            incompatible = _check_execution_contract(program_binding, machine)
            if incompatible is not None:
                raise _fail(
                    ReplayFailureCode.TYPE_MISMATCH,
                    f"the reference execution is not on the admitted program: {incompatible.value}",
                )
            machine.attach_channel(channel)
            run = _drive_one_behavior(
                binding=program_binding,
                machine=machine,
                channel=channel,
                gas_budget=gas_budget,
                step_limit=step_limit,
            )
            if run.failure_reason is not None and not _is_contract_departure(run):
                # The reference run has to *complete*. A capture of a run that
                # diverged, ran out of budget or faulted would be a manifest
                # saying "the expected outcome is this failure", and a later
                # replay reproducing it would be reported as identical.
                #
                # Whether the transcript matches the behaviour's replay contract
                # is deliberately not checked here. That contract is a separate
                # obligation about what the behaviour should do, and it is
                # checked where it belongs — in the governed replay, against the
                # run being judged. A capture states what running this program
                # deterministically produces; conflating the two would make a
                # contract mismatch look like an infrastructure failure at
                # preparation time.
                # Recorded, not merely raised. The snapshots this preparation
                # already wrote are durable, so raising here would leave blobs
                # nobody can account for and no record of why the attempt to
                # prepare stopped. A reference run that faulted, ran out of
                # budget or reached a forbidden call is a fact about this
                # program under these inputs, and the next reader deserves to
                # find it rather than infer it from orphans.
                incomplete = True
                contract_matched = False
                contract_failure_reason = run.failure_reason
                terminal_digests.append(run.terminal_snapshot_digest)
                break
            if not run.transcript_matched:
                contract_matched = False
                contract_failure_reason = run.failure_reason or (
                    ReplayFailureReason.TRANSITION_MISMATCH
                )
            transitions.extend(run.transition_hash_chain)
            activities.extend(run.consumed_activity_identities)
            terminal_digests.append(run.terminal_snapshot_digest)
    finally:
        channel.close()

    # A run that stopped early still has to describe every behaviour: the record
    # states where each machine ended, and a machine that never ran ended where it
    # started. Reporting fewer terminal states than behaviours would make the
    # capture unreadable rather than merely unsuccessful.
    while len(terminal_digests) < len(bindings):
        terminal_digests.append(machines[len(terminal_digests)].snapshot_digest())

    payload = object.__new__(ReferenceReplayCapture)
    object.__setattr__(payload, "schema_version", SchemaVersion.REFERENCE_REPLAY_CAPTURE_V1)
    object.__setattr__(payload, "knowledge_snapshot_id", prepared.snapshot_manifest_ref.ref_id)
    object.__setattr__(payload, "snapshot_manifest_ref", prepared.snapshot_manifest_ref)
    object.__setattr__(payload, "boundary_ref", prepared.admitted.boundary_ref)
    object.__setattr__(payload, "admitted_knowledge_id", prepared.admitted.knowledge_id)
    object.__setattr__(
        payload, "behavior_content_keys", tuple(item.behavior_content_key for item in bindings)
    )
    object.__setattr__(payload, "program_hashes", tuple(item.program_hash for item in bindings))
    object.__setattr__(
        payload, "host_abi_versions", tuple(item.host_abi_version for item in bindings)
    )
    object.__setattr__(payload, "initial_snapshot_refs", snapshot_refs)
    object.__setattr__(payload, "initial_snapshot_digests", initial_digests)
    object.__setattr__(payload, "recorded_activity_refs", prepared.ledger.activity_refs())
    object.__setattr__(payload, "activity_identities", prepared.ledger.activity_identities())
    object.__setattr__(payload, "capability_profile_digest", capability_profile_digest())
    object.__setattr__(payload, "gas_budget", int(gas_budget))
    object.__setattr__(payload, "cognitive_budget", int(cognitive_budget))
    object.__setattr__(payload, "step_limit", int(step_limit))
    object.__setattr__(
        payload,
        "observed_transcript_root",
        transcript_root(transitions=tuple(transitions), activities=tuple(activities)),
    )
    object.__setattr__(payload, "observed_terminal_snapshot_digests", tuple(terminal_digests))
    object.__setattr__(payload, "contract_matched", contract_matched)
    object.__setattr__(payload, "contract_failure_reason", contract_failure_reason)
    object.__setattr__(
        payload, "capture_resumed_from_result_ref", resumed_from_result_ref
    )
    object.__setattr__(payload, "activity_policy_decision_refs", decision_refs)
    object.__setattr__(payload, "replay_executor_actor", binding.executor_actor)
    object.__setattr__(payload, "_trusted_seal", _CAPTURE_SEAL)
    envelope, envelope_binding = _envelope_for(
        schema_version=SchemaVersion.REFERENCE_REPLAY_CAPTURE_V1,
        identity_domain=IdentityDomain.REFERENCE_REPLAY_CAPTURE,
        payload=_capture_payload(payload),
        admitted=prepared.admitted,
        created_at_utc=prepared.admitted.verified_at_utc,
    )
    object.__setattr__(payload, "envelope", envelope)
    object.__setattr__(payload, "envelope_binding_sha256", envelope_binding)
    object.__setattr__(payload, "capture_id", envelope.record_id)
    validate_reference_capture(payload)
    with store_transaction(fence) as ticket:
        reference = binding.replay_store.append_capture(payload, ticket=ticket)
    if incomplete:
        # Durable first, refused second. The record exists either way; what does
        # not happen is a manifest, because there is no completed execution for
        # one to project.
        raise _fail(
            ReplayFailureCode.CAPTURE_NOT_CONFORMANT,
            "the reference execution did not complete: "
            + (contract_failure_reason.value if contract_failure_reason else "unknown"),
        )
    return reference


def publish_replay_manifest(
    *,
    binding: ProductionReplayBinding,
    capture_authority: ReferenceCaptureAuthority,
    capture_ref: HashBoundRef,
    context: ReplayRecordContext,
) -> RecordId:
    """Turn a durable reference capture into the manifest a run is measured by.

    The manifest authority takes no expected values. It used to take an expected
    transcript root and expected terminal digests as arguments, and moving the
    moment they were written earlier did not change whose values they were — the
    party asking for the run still said what the run was supposed to produce. Now
    the only input is a reference to a capture the store already holds, and every
    expected value in the manifest is read out of it.

    Two refusals matter here.

    A capture that did not reproduce its behaviour's ``ReplayContract`` is kept —
    it is a true record of what running that program produced — but it may not
    become a manifest. Publishing one would make an execution that already
    departs from the contract into the expected outcome, and every later replay
    reproducing that departure would be reported as identical to it.

    And the authority sealing the capture may not be the executor whose work it
    seals. That is the only separation §9.4 supports: it names seven actors, the
    same ``replay_executor_actor`` performs both phases, and no eighth executor
    exists to be different from.
    """

    from .persistence import store_transaction

    binding = validate_production_replay_binding(binding)
    require_reference_capture_authority(capture_authority, binding=binding)
    capture = binding.replay_store.require_capture(capture_ref)
    validate_reference_capture(capture)
    # A continuation is exempt from the contract comparison, and the exemption
    # rests on a *resolved* predecessor rather than on the presence of a field.
    # "Any non-None resumed_from disables the check" would have let a capture
    # naming an unresolvable predecessor publish a non-conformant run: the field
    # was the caller's, the exemption was automatic, and nothing looked at what it
    # pointed to. Here the predecessor is required to resolve in this same store,
    # so the exemption is earned by a record that exists.
    continuation = None
    if capture.capture_resumed_from_result_ref is not None:
        continuation = binding.replay_store.require_result(
            capture.capture_resumed_from_result_ref
        )
    if not capture.contract_matched and continuation is None:
        reason = capture.contract_failure_reason
        raise _fail(
            ReplayFailureCode.CAPTURE_NOT_CONFORMANT,
            "a capture that departed from its replay contract cannot become a manifest"
            + ("" if reason is None else f": {reason.value}"),
        )
    # A continuation is exempt, and the exemption is narrow. It starts from a
    # terminal state and executes the tail of a behaviour, while the contract
    # describes the behaviour whole — so the comparison is not a weaker test of
    # the same thing, it is a test of something the record cannot be about.
    # Everything else a continuation must satisfy is checked where it belongs:
    # the starting state against the predecessor's exact terminal reference, and
    # the transcript against the manifest this capture produces.
    if capture.replay_executor_actor != binding.executor_actor:
        raise _fail(
            ReplayFailureCode.ACTIVITY_NOT_GOVERNED,
            "the capture was taken under another replay executor",
        )
    if capture.capability_profile_digest != capability_profile_digest():
        raise _fail(
            ReplayFailureCode.CAPABILITY_PROFILE_MISMATCH,
            "the capture was taken under another capability profile",
        )
    manifest = _issue_manifest_from_capture(
        authority=binding.authority,
        capture=capture,
        capture_ref=capture_ref,
        context=context,
    )
    with store_transaction(binding.fence) as ticket:
        return binding.replay_store.append_manifest(manifest, ticket=ticket)


def _machines_from_snapshots(
    references: tuple[HashBoundRef, ...],
    *,
    binding: ProductionReplayBinding,
    gas_budget: int,
) -> tuple[CognitiveVMReplayAdapter, ...]:
    """Restore exact machines from durable snapshot bytes the store holds."""

    machines: list[CognitiveVMReplayAdapter] = []
    for reference in references:
        raw = binding.replay_store.open_snapshot(reference)
        try:
            snapshot = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH, "a durable snapshot is not a machine snapshot"
            ) from exc
        machines.append(
            CognitiveVMReplayAdapter.from_snapshot(snapshot, gas_budget=gas_budget)
        )
    return tuple(machines)


__all__ = [
    "ReferenceCaptureAuthority",
    "capture_reference_replay",
    "create_reference_capture_authority",
    "publish_replay_manifest",
    "require_reference_capture_authority",
]
