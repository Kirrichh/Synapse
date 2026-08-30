"""Stage 4 durable authority and restart recovery acceptance shard."""

from __future__ import annotations

from tests.stage4_gold_replay_support import *  # noqa: F403


def test_compiler_live_drift_is_revalidated_and_refused_before_request_or_machine() -> None:
    """The compiler sits between two real Stage-3/Consumption evaluations."""

    from tests.test_stage4_gold_compatibility import _fresh_platform_observation

    unit, _ = pure_behavior()
    core = published_core(unit)
    provider = WORLD.platform_observation_provider(core)
    original = provider.observation

    def drifting_compiler(value):
        compiled = compile_behavior_unit(value)
        provider.observation = _fresh_platform_observation(
            WORLD.world(core).world,
            environment_version="synapse.stage4.environment/v999",
        )
        return compiled

    prepared = prepare_for(unit, compiler=drifting_compiler)
    requests_before = len(prepared.bundle.replay_store.recorded_request_refs())
    calls_before = provider.calls
    try:
        with pytest.raises(Exception) as excinfo:
            # The public governed path. What is under test is the drift the
            # compiler introduces between the two admissions, so the refusal has
            # to come from the gate rather than from anything about the machine.
            prepared.run()
    finally:
        provider.observation = original
    assert getattr(excinfo.value, "failure_code", None) is not None
    assert provider.calls >= calls_before + 2
    assert len(prepared.bundle.replay_store.recorded_request_refs()) == requests_before

def test_production_binding_derives_execution_actors_from_the_sealed_set() -> None:
    """No caller identity can replace the executor or consumer the evaluator sealed."""

    from synapse.experiments.gold import activity_policy as AP

    unit, _ = pure_behavior()
    prepared = prepare_for(unit)
    final = WORLD.admission_request(prepared.core, prepared.extra)
    handle = WORLD.authority_handle(prepared.core, prepared.extra)
    declaration = AP.create_activity_policy_declaration(
        authority_handle=handle,
        evaluator_identity=AuthorityIdentity(EXECUTOR.value),
        evaluator_component_id="stage9-self-approving-policy",
        evaluator_component_version="synapse.stage4.activity-policy/v1",
        policy_version=POLICY,
        dispositions={
            kind: ACT.ActivityDisposition.RECORDED_CONSUMABLE for kind in ACT.ActivityKind
        },
        trusted_clock=lambda: NOW,
    )
    actors = AP.create_activity_policy_actor_set(
        authority_handle=handle,
        producer_actor=ActorIdentity("actual-producer"),
        recorder_actor=ActorIdentity("actual-recorder"),
        worker_actor=ActorIdentity("actual-worker"),
        model_actor=ActorIdentity("actual-model"),
        replay_executor_actor=ActorIdentity("false-executor-name"),
        machine_adapter_actor=ActorIdentity("actual-machine-adapter"),
        consumer_actor=ActorIdentity("actual-consumer"),
    )
    proof = AP.create_activity_policy_independence_proof(
        declaration=declaration, actor_set=actors
    )
    evaluator = AP.configure_activity_policy_evaluator(
        declaration=declaration,
        actor_set=actors,
        independence_proof=proof,
        lifecycle_store=final.binding.lifecycle_store,
        taint_store=final.binding.taint_store,
        trusted_clock=lambda: NOW,
    )
    binding = RC.create_production_replay_binding(
        authority=final.binding,
        initial_admission=prepared.admission,
        final_admission=final,
        activity_policy_evaluator=evaluator,
        activity_store=prepared.bundle.activity_store,
        activity_policy_store=prepared.bundle.activity_policy_store,
        replay_store=prepared.bundle.replay_store,
        artifact_reader=prepared.artifact_reader,
    )
    assert binding.executor_actor == actors.replay_executor_actor
    assert binding.consumer_actor == actors.consumer_actor
    parameters = __import__("inspect").signature(
        RC.create_production_replay_binding
    ).parameters
    assert "executor_actor" not in parameters and "consumer_actor" not in parameters

def test_production_binding_refuses_a_protocol_compatible_factory_substitution() -> None:
    prepared = pure_prepared()
    binding = prepared._governed()["binding"]

    class ProtocolCompatibleFactory:
        def adapter_id(self) -> str:
            return R.REPLAY_MACHINE_ADAPTER_ID_V1_E1

        def build(self, program, **kwargs):
            raise AssertionError("substituted factory must never build a machine")

        def restore(self, snapshot_bytes, **kwargs):
            raise AssertionError("substituted factory must never restore a machine")

    substituted = ProtocolCompatibleFactory()
    configured = (*binding._configuration_snapshot[:-1], substituted)
    object.__setattr__(binding, "machine_factory", substituted)
    object.__setattr__(binding, "_configuration_snapshot", configured)

    with pytest.raises(R.ReplayViolation) as excinfo:
        R.validate_production_replay_binding(binding)

    assert excinfo.value.failure_code is R.ReplayFailureCode.TRUSTED_OBJECT_FORGED

def test_result_blob_without_durable_activity_record_is_refused_before_compilation() -> None:
    """A blob with no record behind it is not an activity, and nothing compiles.

    The manifest is issued first, from a healthy preparation of a behaviour that
    consumes no activity at all — so the expected outcome exists and the run is
    refused for the one thing this case is about. The activity is then named in
    the run's own history while its record was never published, and the refusal
    lands where a durable history is resolved: before a program is compiled and
    before any request exists.

    Through the public entry point rather than ``Prepared.run``, because that
    helper republishes the fixture activities on its way in and would put back
    the record this case withholds.
    """

    from synapse.experiments.gold.activity_store import (
        ActivityStoreFailureCode,
        ActivityStoreViolation,
    )

    activity = recorded_llm_call(prompt=b"blob without record")
    prepared = pure_prepared()
    manifest_ref = prepared.manifest_ref(prepared.bundle.replay_store)
    governed = prepared._governed()
    store = prepared.bundle.replay_store
    requests_before = len(store.recorded_request_refs())

    with pytest.raises(ActivityStoreViolation) as excinfo:
        RC.run_governed_replay(
            admission=prepared.admission,
            binding=governed["binding"],
            subjects=prepared.subjects,
            compiler=prepared.compiler,
            activity_refs=(ACT.activity_ref(activity),),
            manifest_ref=manifest_ref,
            gas_budget=GAS,
            cognitive_budget=8,
            step_limit=1_000,
        )
    assert excinfo.value.failure_code is ActivityStoreFailureCode.RECORD_UNKNOWN
    # Nothing was recorded, which is the claim: the refusal lands before the
    # attempt exists, so there is no machine to ask about and no request to find.
    assert len(store.recorded_request_refs()) == requests_before

@pytest.mark.parametrize("damage", ["missing", "substituted"])
def test_durable_activity_record_with_unavailable_result_blob_is_refused(damage: str) -> None:
    """The record resolves and the bytes do not, which is two different refusals.

    On a behaviour that actually consumes the activity, because the claim is
    about injecting its result: a program that never reaches for the effect
    would be refused for something else or not at all.

    The manifest is issued while the blob is intact, and the run is then made
    through the public entry point rather than through ``Prepared.run`` — that
    helper republishes the fixture activities, which would restore the very
    bytes this case damages.
    """

    from synapse.experiments.gold.activity_store import (
        ActivityStoreFailureCode,
        ActivityStoreViolation,
    )

    unit, activity = llm_artifact_behavior(prompt="record " + damage)
    prepared = prepare_for(unit, activities=(activity,))
    manifest_ref = prepared.manifest_ref(prepared.bundle.replay_store)
    governed = prepared._governed()

    blob = prepared.bundle.activity_store._blob_path(activity.result_sha256)
    original = blob.read_bytes()
    if damage == "missing":
        blob.unlink()
    else:
        blob.write_bytes(b"x" * len(original))
    try:
        with pytest.raises(ActivityStoreViolation) as excinfo:
            RC.run_governed_replay(
                admission=prepared.admission,
                binding=governed["binding"],
                subjects=prepared.subjects,
                compiler=prepared.compiler,
                activity_refs=governed["activity_refs"],
                manifest_ref=manifest_ref,
                gas_budget=GAS,
                cognitive_budget=8,
                step_limit=1_000,
            )
    finally:
        blob.write_bytes(original)
    expected = (
        ActivityStoreFailureCode.RESULT_UNAVAILABLE
        if damage == "missing"
        else ActivityStoreFailureCode.RESULT_CORRUPTED
    )
    assert excinfo.value.failure_code is expected

def test_activity_policy_decision_missing_after_restart_is_refused() -> None:
    """A continuation whose predecessor's policy decision is gone is refused.

    Driven through a binding whose policy history is genuinely empty, which is
    the part the previous revision left out: it built such a store, never passed
    it to anything, and resumed through the ordinary bundle instead — so the
    refusal it asserted came from wherever the ordinary path happened to fail
    first, and the empty store was decoration.

    Built on the artifact behaviour, because a pure program consumes no activity
    and therefore pins no policy decision for a restart to lose.
    """

    from synapse.experiments.gold.activity_policy_store import (
        ActivityPolicyStoreFailureCode,
        ActivityPolicyStoreViolation,
        FileActivityPolicyStore,
    )

    unit, activity = llm_artifact_behavior(prompt="missing policy after restart")
    prepared = prepare_for(unit, activities=(activity,))
    first = prepared.run()
    assert first.recorded_activity_refs, "the predecessor pinned no decision to lose"

    continuation = prepare_for(unit, activities=(activity,))
    manifest_ref = continuation.manifest_ref(
        prepared.bundle.replay_store, resumed_from=first
    )
    final = WORLD.admission_request(continuation.core, continuation.extra)
    empty_policy = FileActivityPolicyStore(
        WORLD.stores_root(prepared.core, prepared.extra) / "empty-policy-after-restart",
        mutation_fence=prepared.bundle.fence,
    )
    binding = RC.create_production_replay_binding(
        authority=final.binding,
        initial_admission=continuation.admission,
        final_admission=final,
        activity_policy_evaluator=prepared.bundle.evaluator,
        activity_store=prepared.bundle.activity_store,
        activity_policy_store=empty_policy,
        replay_store=prepared.bundle.replay_store,
        artifact_reader=continuation.artifact_reader,
    )
    with pytest.raises(ActivityPolicyStoreViolation) as excinfo:
        RC.resume_governed_replay(
            admission=continuation.admission,
            binding=binding,
            subjects=continuation.subjects,
            compiler=continuation.compiler,
            manifest_ref=manifest_ref,
            resumed_from_result_ref=R.replay_result_ref(first),
            gas_budget=GAS,
            cognitive_budget=8,
            step_limit=1_000,
        )
    assert excinfo.value.failure_code is ActivityPolicyStoreFailureCode.RECORD_UNKNOWN

@pytest.mark.parametrize("damage", ["torn", "tampered"])
def test_restarted_activity_policy_store_refuses_damaged_history(damage: str) -> None:
    from synapse.experiments.gold.activity_policy_store import (
        ActivityPolicyStoreFailureCode,
        ActivityPolicyStoreViolation,
        FileActivityPolicyStore,
    )

    activity = recorded_llm_call(prompt=("policy-" + damage).encode())
    prepared, _ = scripted_prepared(
        ["LLM_EVAL"], activity_ids=(activity.activity_identity,), activities=(activity,)
    )
    run_scripted(
        prepared, opcodes=["LLM_EVAL"], on_step=consuming_step(prompt=("policy-" + damage).encode())
    )
    root = WORLD.stores_root(prepared.core, prepared.extra) / ("policy-replica-" + damage)
    replica = FileActivityPolicyStore(root, mutation_fence=prepared.bundle.fence)
    raw = prepared.bundle.activity_policy_store.journal_path.read_bytes()
    if damage == "torn":
        changed = raw[:-7]
    else:
        index = len(raw) // 2
        changed = raw[:index] + bytes([raw[index] ^ 1]) + raw[index + 1 :]
    replica.journal_path.write_bytes(changed)
    with pytest.raises(ActivityPolicyStoreViolation) as excinfo:
        replica.recorded_decision_refs()
    assert excinfo.value.failure_code in {
        ActivityPolicyStoreFailureCode.HISTORY_TORN,
        ActivityPolicyStoreFailureCode.HISTORY_CORRUPT,
        ActivityPolicyStoreFailureCode.HISTORY_FORKED,
    }

@pytest.mark.parametrize("store_kind", ["activity", "policy", "replay"])
def test_stage9_store_from_foreign_coordinator_is_refused_before_compilation(
    tmp_path, store_kind: str
) -> None:
    from synapse.experiments.gold.activity_policy_store import FileActivityPolicyStore
    from synapse.experiments.gold.activity_store import FileActivityStore
    from synapse.experiments.gold.admission_journal import FileSnapshotFence
    from synapse.experiments.gold.replay_store import FileReplayStore

    unit, _ = pure_behavior()
    prepared = prepare_for(unit)
    final = WORLD.admission_request(prepared.core, prepared.extra)
    foreign = FileSnapshotFence(tmp_path / "foreign-coordinator")
    foreign_activity_store = FileActivityStore(
        tmp_path / "foreign-activities", mutation_fence=foreign
    )
    foreign_policy_store = FileActivityPolicyStore(
        tmp_path / "foreign-policy", mutation_fence=foreign
    )
    foreign_replay_store = FileReplayStore(
        tmp_path / "foreign-replay", mutation_fence=foreign
    )
    stores = {
        "activity": (
            foreign_activity_store,
            prepared.bundle.activity_policy_store,
            prepared.bundle.replay_store,
        ),
        "policy": (
            prepared.bundle.activity_store,
            foreign_policy_store,
            prepared.bundle.replay_store,
        ),
        "replay": (
            prepared.bundle.activity_store,
            prepared.bundle.activity_policy_store,
            foreign_replay_store,
        ),
    }[store_kind]
    with pytest.raises(R.ReplayViolation) as excinfo:
        RC.create_production_replay_binding(
            authority=final.binding,
            initial_admission=prepared.admission,
            final_admission=final,
            activity_policy_evaluator=prepared.bundle.evaluator,
            activity_store=stores[0],
            activity_policy_store=stores[1],
            replay_store=stores[2],
            artifact_reader=prepared.artifact_reader,
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.ADMISSION_NOT_CURRENT

@pytest.mark.parametrize("foreign_context", ["attempt", "boundary"])
def test_durable_policy_decision_for_another_execution_context_is_refused(
    foreign_context: str,
) -> None:
    from synapse.experiments.gold import activity_policy as AP

    # A governed run over the artifact behaviour, because the subject is a
    # *durable* policy decision and only a governed run makes one. The transition
    # driver records nothing, so its output has no request to resolve a decision
    # from — reading ``request_ref`` off it was reading a field it never had.
    unit, activity = llm_artifact_behavior(prompt="durable policy context")
    prepared = prepare_for(unit, activities=(activity,))
    result = prepared.run()
    stored_request = prepared.bundle.replay_store.request_record(result.request_ref)
    reference = HashBoundRef.from_dict(
        stored_request["payload"]["activity_policy_decision_refs"][0]
    )
    decision = prepared.bundle.activity_policy_store.require_decision(
        reference, evaluator=prepared.bundle.evaluator
    )
    durable_activity_ref = HashBoundRef.from_dict(
        stored_request["payload"]["recorded_activity_refs"][0]
    )
    durable_activity = prepared.bundle.activity_store.require_record(durable_activity_ref)
    production = prepared.bundle.activity_policy_store.require_production_provenance_for_activity(
        durable_activity.production_provenance_ref,
        evaluator=prepared.bundle.evaluator,
        activity=durable_activity,
    )
    context = {
        "consumer_context_ref": decision.consumer_context_ref,
        "boundary_ref": decision.boundary_ref,
        "run_id": decision.run_id,
        "attempt_id": decision.attempt_id,
        "environment_profile_id": decision.environment_profile_id,
        "capability_profile_digest": decision.capability_profile_digest,
        # The consuming half of §9.4, taken off the binding the run was made
        # through: what this case varies is the execution context, not who was
        # consuming, so this half has to match or it would fail for the other
        # reason.
        "consumption": prepared._last_binding.consumption_provenance,
    }
    if foreign_context == "attempt":
        context["attempt_id"] = AttemptId("foreign-policy-attempt")
    else:
        context["boundary_ref"] = OTHER_BOUNDARY_REF
    with pytest.raises(AP.ActivityPolicyViolation) as excinfo:
        AP.require_consumable_activity_decision(
            decision,
            evaluator=prepared.bundle.evaluator,
            activity=durable_activity,
            production=production,
            **context,
        )
    assert excinfo.value.failure_code is AP.ActivityPolicyFailureCode.DECISION_CONTEXT_MISMATCH

def test_governed_replay_resolves_durable_record_and_injects_exact_stored_bytes() -> None:
    """The whole §23 claim, on the public path, with nothing staged.

    An admitted behaviour whose program is a durable artifact and whose opcodes
    include ``LLM_EVAL``; the exact ``CognitiveVMReplayAdapter`` built by the
    executor from the manifest; the recorded activity resolved out of the durable
    store and its exact bytes injected where the effect would have been; a
    durable observation and result at the end. No scripted port anywhere, and no
    live producer — the only place an answer could come from is the record.

    Until the artifact endpoint existed this case could not be written. Every
    admitted behaviour's program was inline IR, inline IR is a pure language, and
    a pure program has no effect to serve from record.
    """

    from synapse.experiments.gold.activity_policy import activity_policy_decision_ref

    unit, activity = llm_artifact_behavior()
    prepared = prepare_for(unit, activities=(activity,))
    result = prepared.run()
    assert result.status is R.ReplayStatus.REPLAY_IDENTICAL
    assert result.failure_reason is None
    assert result.consumed_activity_identities == (activity.activity_identity,), (
        "the governed run did not consume the recorded activity its program calls for"
    )
    assert result.observations[0].transcript_matched
    restored = prepared.bundle.activity_store.require_record(
        result.recorded_activity_refs[0]
    )
    assert restored.activity_identity == activity.activity_identity
    assert prepared.bundle.activity_store.open_result(restored.result_ref) == R_RESULT
    request_record = prepared.bundle.replay_store.request_record(result.request_ref)
    policy_ref = HashBoundRef.from_dict(
        request_record["payload"]["activity_policy_decision_refs"][0]
    )
    decision = prepared.bundle.activity_policy_store.require_decision(
        policy_ref, evaluator=prepared.bundle.evaluator
    )
    assert activity_policy_decision_ref(decision).to_dict() == policy_ref.to_dict()

def test_resume_after_restart_resolves_exact_activity_and_policy_histories() -> None:
    """After a restart a continuation reads the histories, rather than remembering them.

    The restart is modelled the way one actually happens: fresh store objects
    over the same directories, holding nothing from the run before. What the
    continuation then resolves has to come out of those journals — the activity
    record it consumed, and the policy decision that permitted it — because
    there is nowhere else left for it to come from.

    Built on the artifact behaviour, because it is the only admitted behaviour
    that performs an effect: a continuation of a pure program would resolve an
    empty activity history and prove nothing about resolving one.
    """

    from synapse.experiments.gold.activity_policy_store import FileActivityPolicyStore
    from synapse.experiments.gold.activity_store import FileActivityStore
    from synapse.experiments.gold.replay_store import FileReplayStore

    unit, activity = llm_artifact_behavior(prompt="restart exact histories")
    prepared = prepare_for(unit, activities=(activity,))
    first = prepared.run()
    assert first.status is R.ReplayStatus.REPLAY_IDENTICAL
    assert first.recorded_activity_refs, "the first run consumed no activity to resolve"

    continuation = prepare_for(unit, activities=(activity,))
    again = continuation.resume(resumed_from=first)
    assert again.status is not R.ReplayStatus.REPLAY_INCOMPATIBLE
    assert again.recorded_activity_refs == first.recorded_activity_refs

    # The restart. Nothing below shares an object with the runs above.
    activity_store = FileActivityStore(
        prepared.bundle.activity_store.journal_path.parent,
        mutation_fence=prepared.bundle.fence,
    )
    policy_store = FileActivityPolicyStore(
        prepared.bundle.activity_policy_store.journal_path.parent,
        mutation_fence=prepared.bundle.fence,
    )
    replay_store = FileReplayStore(
        prepared.bundle.replay_store.journal_path.parent,
        mutation_fence=prepared.bundle.fence,
    )
    assert activity_store.require_record(
        again.recorded_activity_refs[0]
    ).activity_identity == activity.activity_identity
    latest = replay_store.request_record(again.request_ref)
    resolved = 0
    for raw_ref in latest["payload"]["activity_policy_decision_refs"]:
        policy_store.require_decision(
            HashBoundRef.from_dict(raw_ref), evaluator=prepared.bundle.evaluator
        )
        resolved += 1
    assert resolved == len(again.recorded_activity_refs), (
        "the continuation did not pin one durable policy decision per activity"
    )
