"""Stage 4 permit, budget, codec, and durable starting state acceptance shard."""

from __future__ import annotations

from tests.stage4_gold_replay_support import *  # noqa: F403


def test_one_expensive_opcode_cannot_overshoot_the_budget() -> None:
    """The gas check is a preflight, not a post-mortem.

    The earlier form compared gas *already spent* against the budget, so an
    opcode costing more than the whole remaining budget still executed and was
    noticed on the next iteration — and if it was the last instruction, never.
    ``LLM_EVAL`` costs 25 against a budget of 3 here, and must not run at all.
    """

    from synapse.cvm import GAS_COSTS

    activity = recorded_llm_call()
    prepared, _ = scripted_prepared(["LLM_EVAL"], activities=(activity,), gas_budget=3)
    assert GAS_COSTS["LLM_EVAL"] > 3, "this case needs an opcode costlier than the budget"
    result = run_scripted(
        prepared, opcodes=["LLM_EVAL"], on_step=consuming_step(),
        gas_after=lambda gas: gas - GAS_COSTS["LLM_EVAL"],
    )
    assert result.failure_reason is R.ReplayFailureReason.GAS_EXHAUSTED
    assert result.steps_executed == 0, "the opcode executed before the budget was checked"
    assert result.transition_hash_chain == ()

def test_a_budget_that_covers_the_opcode_still_runs_it() -> None:
    """Refusing everything expensive is not the fix; the arithmetic has to be right.

    The subject is the preflight's arithmetic, and nothing else. A budget exactly
    equal to the opcode's cost covers it, so the step is taken and the cost is
    charged — as opposed to a preflight that refuses anything it cannot pay for
    twice.

    The transcript is deliberately not asserted. A scripted machine takes one
    step while the behaviour's own contract describes the six its real program
    takes, so this run departs from that contract by construction; saying it
    should not would be asking a gas case to also be a transcript case, and the
    transcript has its own.
    """

    from synapse.cvm import GAS_COSTS

    activity = recorded_llm_call()
    cost = GAS_COSTS["LLM_EVAL"]
    prepared, _transitions = scripted_prepared(
        ["LLM_EVAL"],
        activities=(activity,),
        activity_ids=(activity.activity_identity,),
        gas_budget=cost,
    )
    result = run_scripted(
        prepared, opcodes=["LLM_EVAL"], on_step=consuming_step(),
        gas_after=lambda gas: gas - cost,
    )
    assert result.steps_executed == 1, "a budget that covers the opcode refused it"
    assert result.gas_consumed == cost
    assert result.failure_reason is not R.ReplayFailureReason.GAS_EXHAUSTED

def test_a_machine_running_dry_is_an_exhausted_budget_not_a_broken_machine() -> None:
    """The machine's own pool binds too, and it is named for what it is.

    A machine allowed to run past its pool raises ``OutOfEnergy``, which this
    executor would record as ``MACHINE_FAULT`` — an exhausted budget reported as
    broken infrastructure, which is the misclassification NR-10 is about. So the
    pool is checked in the same preflight as the request budget: whichever binds
    first, the answer is that this replay could not afford the next transition.

    The two limits are deliberately not required to be equal. A resumed machine
    carries whatever its predecessor left, so a fresh budget larger than that
    remainder is the ordinary case and not an incompatibility.
    """

    # Less gas than the behaviour needs, on the real machine: the pool binds
    # first and the preflight answers GAS_EXHAUSTED rather than letting the
    # machine raise OutOfEnergy and be recorded as a fault.
    prepared, transitions = scripted_prepared(["ADD", "SUB", "MUL"], gas_budget=3)
    result = prepared.run()
    assert R.status_for_reason(result.failure_reason) is R.ReplayStatus.REPLAY_FAILED
    assert result.failure_reason is R.ReplayFailureReason.GAS_EXHAUSTED
    assert result.status is not R.ReplayStatus.INFRA_ERROR
    # Two separate claims, and the second is the one this case exists for. The
    # run stopped short of the behaviour it was supposed to reproduce, and it
    # stopped *without* spending more than it held — a machine allowed to run
    # past its pool would have raised OutOfEnergy and been recorded as a fault.
    assert result.steps_executed < len(transitions), "the run reached the end anyway"
    assert result.gas_consumed <= 3, "the machine executed past the gas it held"

def test_the_result_codec_is_enforced_and_not_merely_declared() -> None:
    """JSON has many spellings of one value; an identity must have one.

    Every byte string below parses, and each has a different digest and therefore
    a different activity identity. Accepting them would let two identities name
    one injected value — the collision activity identity exists to prevent, run
    backwards. So the bytes must be the ones this codec would have produced.
    """

    for raw in (b" 1 ", b'{"b":1,"a":2}', b"[1,  2]", b'{ "a": 1 }'):
        with pytest.raises(R.ReplayViolation) as excinfo:
            RVC.decode_recorded_result(raw)
        assert excinfo.value.failure_code is R.ReplayFailureCode.RESULT_NOT_DECODABLE

    for value in (1, "text", None, True, [1, 2], {"a": 1, "b": 2}):
        raw = RVC.encode_recorded_result(value)
        assert RVC.decode_recorded_result(raw) == value
        assert RVC.encode_recorded_result(RVC.decode_recorded_result(raw)) == raw

def test_a_non_canonical_recorded_result_stops_the_replay() -> None:
    """And it stops it at consumption, where the bytes become a machine value."""

    _, program, records = effect_fixture()
    payload = dict(records[0]["payload"])
    sloppy = b'{ "b": 2, "a": 1 }'
    activity = governed_activity(
        kind=ACT.ActivityKind(payload["kind"]),
        inputs=ACT.ActivityInputs.from_dict(payload["inputs"]),
        position=ACT.ActivityPosition.from_dict(payload["position"]),
        result=sloppy,
    )
    channel = channel_for(activity, budget=8)
    adapter = vm_adapter(program)
    adapter.attach_channel(channel)
    with pytest.raises(R.ReplayViolation) as excinfo:
        for _ in range(10):
            adapter.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.RESULT_NOT_DECODABLE

def test_a_structural_double_cannot_reach_the_production_entry_point() -> None:
    """The finding this closes: a scripted port reaching REPLAY_IDENTICAL.

    ``ReplayMachinePort`` is structural, and a port answers every question about
    itself — its program hash, its transitions, its gas, its snapshot digest. An
    object answering all four consistently produces a result that says a
    behaviour replayed identically while no machine executed anything. That is a
    manufactured proof of reproducibility, and it was reachable through the
    public entry point.

    The seam below still accepts it, deliberately: a scripted transcript is how
    the machine-misbehaviour cases are written, and they cannot be written on a
    real machine. What changed is that the seam is one call lower than the door.
    """

    import inspect

    # There is no argument left to pass a double through. An exact-type check on
    # a ``machines`` parameter was the first repair; removing the parameter is
    # the second and the stronger one, because a caller never holds the object a
    # verdict will be read off.
    for entry in (RC.run_governed_replay, RC.resume_governed_replay):
        assert "machines" not in inspect.signature(entry).parameters, (
            f"{entry.__name__} still accepts a machine from its caller"
        )
    source = inspect.getsource(R._execute_prepared)
    assert "_machines_from_manifest" in source, (
        "the executor no longer builds its machines from the manifest"
    )

def test_the_double_would_otherwise_have_produced_an_identity() -> None:
    """Stated rather than assumed: the refused object is one that *would* pass.

    Without this the case above proves only that some object was refused, which
    is true of any object. The double is handed the behaviour's *own* expected
    transcript to narrate, one admissible opcode step per transition, and it
    reaches a clean run with a matched transcript — a manufactured proof of
    reproducibility, produced by an object that executed nothing.

    Driven raw on purpose, and it stays that way: the governed path builds its
    machines from the manifest and has no argument to pass this object through,
    which is exactly the property the case above states. What is shown here is
    that the object the public path refuses is a *successful* fake rather than
    merely some object.
    """

    prepared, transitions = scripted_prepared(["ADD", "SUB"])
    result = run_scripted(
        prepared,
        opcodes=["ADD"] * len(transitions),
        hash_script=list(transitions),
    )
    assert result.failure_reason is None, "the double did not produce a clean run"
    assert result.transcript_matched, "the double did not reproduce the contract"
    assert result.transition_hash_chain == transitions, (
        "the double narrated a transcript other than the behaviour's own"
    )
    assert result.first_unexpected_index is None, (
        "the double departed from the transcript somewhere"
    )

def test_the_machines_the_executor_builds_are_the_real_adapter() -> None:
    """Constructed, not accepted — and constructed as the one real machine."""

    prepared = pure_prepared()
    governed = prepared._governed()
    binding = governed["binding"]
    manifest = binding.replay_store.require_manifest(
        prepared.manifest_ref(binding.replay_store)
    )
    built = R._machines_from_manifest(
        manifest,
        binding=binding,
        gas_budget=prepared.arguments["gas_budget"],
        attempt_boundary=SimpleNamespace(entering=lambda *_args: None),
    )
    assert built and all(type(item) is RVM.CognitiveVMReplayAdapter for item in built)
    assert built[0].program_hash() == prepared.program_hash

def test_the_real_adapter_is_accepted_by_the_production_entry_point() -> None:
    """The check is exact, not prohibitive: the real machine still runs."""

    record = golden("pure_add_v1")
    prepared = pure_prepared()
    result = prepared.run()
    assert result.status is R.ReplayStatus.REPLAY_IDENTICAL
    assert result.transition_hash_chain == tuple(record["expected_transition_ids"])

def test_the_replay_body_refuses_to_run_without_a_permit() -> None:
    """Privacy is a convention; the permit is the check.

    ``_execute_replay_body`` takes a request, machines and a store, and nothing
    in that signature says the admission still holds, that the OD-10 decisions
    were persisted, or that the coordinator settled afterwards. A caller inside
    the package could assemble all three and execute a replay with none of it
    having happened.
    """

    prepared, _ = scripted_prepared(["ADD"])
    request = prepared.request()
    port = ScriptedPort(program=prepared.program_hash, host_abi=prepared.host_abi,
                        opcodes=["ADD"], gas=prepared.arguments["gas_budget"])
    for counterfeit in (None, object(), "permit"):
        with pytest.raises(R.ReplayViolation) as excinfo:
            R._execute_replay_body(
                request,
                machines=(port,),
                activity_store=prepared.bundle.activity_store,
                permit=counterfeit,
                binding=prepared._last_binding,
                # Present because the signature requires it, and never reached:
                # the permit is checked before a transition is taken, so a body
                # that called this would already have failed the case.
                store_snapshot=lambda raw: pytest.fail(
                    "a body without a permit stored a terminal snapshot"
                ),
                attempt_boundary=object(),
            )
        assert excinfo.value.failure_code is R.ReplayFailureCode.TRUSTED_OBJECT_FORGED
    assert port._index == 0, "a body without a permit still executed"

def test_a_receipt_cannot_be_constructed_by_a_caller() -> None:
    """And there is one name for it. The compatibility alias is gone: a second
    name for a governed type is a second thing to look up and to keep true."""

    assert not hasattr(R, "ReplayExecutionPermit"), "the old alias is back"
    for arguments in ((), ("some-ref",)):
        with pytest.raises(TypeError):
            R.ReplayExecutionReceipt(*arguments)  # type: ignore[call-arg]

def test_a_receipt_is_not_issued_for_a_request_the_store_never_held() -> None:
    """There is no minter left, and issuing is not a formality either.

    ``_mint_execution_permit`` used to hand a permit to anyone holding a request
    object, which made the permit a restatement of the call rather than evidence
    about it. What replaced it re-reads the durable record: a request the store
    has never seen gets no receipt, so the body's later re-derivation cannot be
    satisfied by a caller that simply built a request.
    """

    assert not hasattr(R, "_mint_execution_permit"), "the free minter is back"
    prepared = pure_prepared()
    request = prepared.request()
    binding = prepared._last_binding
    # The precondition is about *this* request, not about an empty store: one
    # store serves every case in this suite, so by the time this runs it holds
    # the requests of every run before it. What matters is that this particular
    # request was built and never executed.
    recorded = [item.to_dict() for item in binding.replay_store.recorded_request_refs()]
    assert R.replay_request_ref(request).to_dict() not in recorded, (
        "this request is already durable, so the case would prove nothing"
    )
    with binding.fence.exclusive() as coordinator_guard:
        with pytest.raises(R.ReplayViolation) as excinfo:
            R._issue_execution_receipt(
                request, binding=binding, coordinator_guard=coordinator_guard
            )
    assert excinfo.value.failure_code is R.ReplayFailureCode.TRUSTED_OBJECT_FORGED

def test_no_step_is_taken_for_a_request_the_store_does_not_hold(monkeypatch) -> None:
    """Мутант A13: снята durable-запись запроса, исполнение продолжилось.

    The one write the receipt rests on. With it removed the run reaches exactly
    the same point it always did — decisions evaluated and durable, admission
    checked, coordinator settled, machines built — and then stops, because the
    receipt is issued off the store rather than off the call. The scripted port
    is the witness: it counts its own steps, and a body that ran anyway would
    have moved it.
    """

    prepared, _ = scripted_prepared(["ADD"])
    port = ScriptedPort(program=prepared.program_hash, host_abi=prepared.host_abi,
                        opcodes=["ADD"], gas=prepared.arguments["gas_budget"])

    def without_the_request(request, *, decisions, binding, ticket):
        # Everything the real one does, except making the request durable.
        for decision in decisions:
            binding.activity_policy_store.append_decision(
                decision,
                evaluator=binding.activity_policy_evaluator,
                consumption=binding.consumption_provenance,
                ticket=ticket,
            )

    monkeypatch.setattr(R, "_persist_authority_and_request", without_the_request)
    with pytest.raises(R.ReplayViolation) as excinfo:
        prepared.run()
    assert excinfo.value.failure_code is R.ReplayFailureCode.TRUSTED_OBJECT_FORGED
    assert port._index == 0, "the body executed for a request nothing recorded"

def test_a_history_shaped_like_the_store_is_refused_where_the_type_is_known() -> None:
    """Мутант A14: точная проверка типа истории заменена на структурную.

    The registry that used to make this check was a first-writer hole — whatever
    registered a class first became the production store for the process. What
    replaced it splits the question: the owner checks what it can check without
    the type, and the composition root checks the type. Both halves are stated
    here, because a double that satisfies the first half and is refused only by
    the second is the whole point.
    """

    from synapse.experiments.gold.replay_store import ReplayStoreViolation

    bundle = pure_prepared().bundle
    real = bundle.replay_store

    class ShapedLikeAStore:
        """Every operation the owner asks for, on the authority's own fence."""

        mutation_fence = real.mutation_fence

        def __getattr__(self, name):
            if name in R._REPLAY_HISTORY_OPERATIONS:
                return getattr(real, name)
            raise AttributeError(name)

    double = ShapedLikeAStore()
    # The owner's structural check passes: the operations are there and the
    # coordinator is the real one.
    assert R.require_replay_history(double, fence=real.mutation_fence) is double
    # The composition root's does not.
    with pytest.raises(ReplayStoreViolation) as excinfo:
        R_STORE.require_production_replay_store(double)
    assert excinfo.value.failure_code is R_STORE.ReplayStoreFailureCode.TYPE_MISMATCH
    # And there is no slot left for a double to claim before the real store does.
    assert not hasattr(R, "register_replay_history_type"), "the registry is back"

def test_a_durable_execution_claim_and_transition_entry_are_single_use(monkeypatch) -> None:
    """A receipt reused is one run claiming another run's evidence.

    The receipt under test is production's own: the body is wrapped so the object
    it was handed can be looked at afterwards, rather than assembled here.
    """

    seen: list[dict] = []
    real_body = R._execute_replay_body

    def capture(request, **kwargs):
        seen.append({"request": request, "permit": kwargs["permit"], "binding": kwargs["binding"]})
        return real_body(request, **kwargs)

    monkeypatch.setattr(R, "_execute_replay_body", capture)
    assert pure_prepared().run().status is R.ReplayStatus.REPLAY_IDENTICAL
    monkeypatch.undo()

    first = seen[0]
    binding, request = first["binding"], first["request"]

    # The receipt the run itself was issued was spent by the run.
    with pytest.raises(R.ReplayViolation) as excinfo:
        R._spend_execution_permit(first["permit"], request=request, binding=binding)
    assert excinfo.value.failure_code is R.ReplayFailureCode.TRUSTED_OBJECT_FORGED

    # The same receipt cannot enter the transition body a second time either.
    with pytest.raises(R.ReplayViolation) as excinfo:
        R._enter_execution_permit(first["permit"], request=request, binding=binding)
    assert excinfo.value.failure_code is R.ReplayFailureCode.TRUSTED_OBJECT_FORGED

    # Discarding the object does not reset the durable CAS. A fresh exclusive
    # window still cannot issue another receipt for the already executed request.
    with binding.fence.exclusive() as coordinator_guard:
        with pytest.raises(R.ReplayViolation) as excinfo:
            R._issue_execution_receipt(
                request, binding=binding, coordinator_guard=coordinator_guard
            )
    assert excinfo.value.failure_code is R.ReplayFailureCode.TRUSTED_OBJECT_FORGED

def test_the_expected_outcome_is_no_longer_something_a_caller_states() -> None:
    """An expected value taken from the caller is the caller's opinion, hashed.

    ``expected_transcript_root`` and ``expected_terminal_snapshot_digests`` were
    optional keyword arguments, and the terminal digests could be omitted
    outright — so the party asking for a run also said what the run was supposed
    to produce, and could pin whatever it happened to reach. Both now come from a
    manifest resolved out of the durable history, and neither is optional.
    """

    import inspect

    for entry in (RC.run_governed_replay, RC.resume_governed_replay):
        parameters = inspect.signature(entry).parameters
        assert "manifest_ref" in parameters
        for absent in ("expected_transcript_root", "expected_terminal_snapshot_digests"):
            assert absent not in parameters, f"{entry.__name__} still takes {absent}"
    fields = R.ReplayExecutionManifest.__dataclass_fields__
    for required in (
        "expected_transcript_root", "expected_terminal_snapshot_digests",
        "initial_snapshot_refs", "initial_snapshot_digests",
        "behavior_content_keys", "program_hashes", "host_abi_versions",
    ):
        assert required in fields

def test_a_manifest_is_resolved_from_the_store_and_not_accepted() -> None:
    """A manifest assembled at the call site is not a resolved manifest."""

    prepared = pure_prepared()
    governed = prepared._governed()
    binding = governed["binding"]
    unknown = R.replay_manifest_ref(
        binding.replay_store.require_manifest(prepared.manifest_ref(binding.replay_store))
    )
    assert binding.replay_store.require_manifest(unknown) is not None

    from synapse.experiments.gold.replay_store import (
        ReplayStoreFailureCode,
        ReplayStoreViolation,
    )

    stranger = HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id="0" * 64,
        schema_id=SchemaVersion.REPLAY_EXECUTION_MANIFEST_V1_E1.value,
        sha256="0" * 64,
        byte_length=1,
        media_type="application/json",
    )
    with pytest.raises(ReplayStoreViolation) as excinfo:
        binding.replay_store.require_manifest(stranger)
    assert excinfo.value.failure_code is ReplayStoreFailureCode.RECORD_UNKNOWN

def test_a_manifest_for_other_behaviors_is_refused() -> None:
    """Resolved is not enough; it has to be resolved *about this run*.

    Two real behaviours, published into one world and admitted under one
    committed boundary. The manifest is a genuine one, issued by this authority
    from a reference capture that completed, and it is issued through the very
    same admission and store the run is prepared under — so the only thing wrong
    with it is that it describes the other execution order.

    That sameness is the point. A manifest written under a second admission
    would be refused for its coordinator, which is a true refusal about a
    different rule and would leave this one untested.
    """

    unit_a, _binding_a = pure_behavior()
    unit_b = real_behavior(literal=4242)
    primary, extra = world_of(unit_a, unit_b)
    forward = A.canonical_subject_refs(
        tuple(admitted_subject_in(item, primary, extra) for item in (unit_a, unit_b))
    )

    # One attempt, one authority, one store. Its manifest describes the reverse
    # execution order, because that is the order it prepared.
    attempt = prepare_many((unit_a, unit_b), order=tuple(reversed(forward)))
    binding = attempt._governed()["binding"]
    foreign = attempt.manifest_ref(binding.replay_store)

    # The same admission, prepared over the same admitted set in the forward
    # order. Nothing about the authority differs; only the order does — so the
    # subjects are built from ``forward`` itself rather than from the order the
    # units happen to be written in, which is what makes the two orders provably
    # different rather than accidentally the same.
    by_digest = {
        admitted_subject_in(item, primary, extra).ref_id: item for item in (unit_a, unit_b)
    }
    forward_subjects = tuple(
        R.replay_subject(subject_ref=reference, unit=by_digest[reference.ref_id])
        for reference in forward
    )
    assert tuple(item.subject_ref.ref_id for item in forward_subjects) != tuple(
        item.subject_ref.ref_id for item in attempt.subjects
    ), "the two execution orders must differ for this case to exist"
    inner = R._prepare_replay(
        admission=attempt.admission,
        binding=binding,
        subjects=forward_subjects,
        compiler=attempt.compiler,
        activity_refs=(),
    )
    with pytest.raises(R.ReplayViolation) as excinfo:
        R._execute_prepared(
            inner,
            binding=binding,
            manifest=binding.replay_store.require_manifest(foreign),
            gas_budget=GAS,
            cognitive_budget=8,
            step_limit=1_000,
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH

def test_a_starting_state_is_durable_and_verified_twice() -> None:
    """The store proves the bytes; the manifest proves those bytes are the state."""

    from synapse.experiments.gold.replay_store import (
        ReplayStoreFailureCode,
        ReplayStoreViolation,
    )

    prepared = pure_prepared()
    binding = prepared._governed()["binding"]
    manifest = binding.replay_store.require_manifest(
        prepared.manifest_ref(binding.replay_store)
    )
    reference = manifest.initial_snapshot_refs[0]
    raw = binding.replay_store.open_snapshot(reference)
    assert hashlib.sha256(raw).hexdigest() == reference.sha256

    restored = restore_vm_adapter(raw)
    assert restored.snapshot_digest() == manifest.initial_snapshot_digests[0]

    # Rewriting the blob is caught by the store's own content address.
    path = binding.replay_store._snapshot_path(reference.sha256)
    original = path.read_bytes()
    path.write_bytes(b"x" * len(original))
    try:
        with pytest.raises(ReplayStoreViolation) as excinfo:
            binding.replay_store.open_snapshot(reference)
    finally:
        path.write_bytes(original)
    assert excinfo.value.failure_code is ReplayStoreFailureCode.SNAPSHOT_CORRUPTED

def test_a_snapshot_the_store_never_held_is_not_a_starting_state() -> None:
    from synapse.experiments.gold.replay_store import (
        ReplayStoreFailureCode,
        ReplayStoreViolation,
    )

    prepared = pure_prepared()
    binding = prepared._governed()["binding"]
    orphan = R.replay_snapshot_ref(b'{"never":"published"}')
    with pytest.raises(ReplayStoreViolation) as excinfo:
        binding.replay_store.open_snapshot(orphan)
    assert excinfo.value.failure_code is ReplayStoreFailureCode.SNAPSHOT_UNAVAILABLE

def test_a_manifest_cannot_be_built_by_its_constructor() -> None:
    with pytest.raises(TypeError):
        R.ReplayExecutionManifest()  # type: ignore[call-arg]

def test_a_rewritten_manifest_does_not_survive_validation() -> None:
    prepared = pure_prepared()
    binding = prepared._governed()["binding"]
    manifest = binding.replay_store.require_manifest(
        prepared.manifest_ref(binding.replay_store)
    )
    object.__setattr__(manifest, "expected_transcript_root", "0" * 64)
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.validate_replay_manifest(manifest)
    assert excinfo.value.failure_code is R.ReplayFailureCode.IDENTITY_MISMATCH

def test_a_snapshot_that_is_not_the_state_the_manifest_recorded_is_refused() -> None:
    """The second of the two checks, and the one the store cannot make.

    The store proves the bytes are the bytes its reference names. It cannot know
    whether those bytes are the *state this manifest meant* — a manifest could
    name a perfectly intact snapshot of some other state and the store would hand
    it over without complaint. So the restored machine's own digest is compared
    with the digest the manifest recorded, and this case is what stops that
    comparison from quietly becoming decoration.
    """

    from synapse.experiments.gold.persistence import store_transaction

    prepared = pure_prepared()
    binding = prepared._governed()["binding"]
    honest = binding.replay_store.require_manifest(
        prepared.manifest_ref(binding.replay_store)
    )
    # A real, intact snapshot of a *different* state: the machine one step on.
    elsewhere = pure_adapter()
    elsewhere.step()
    raw = elsewhere.snapshot_bytes()
    with store_transaction(binding.replay_store.mutation_fence) as ticket:
        reference = binding.replay_store.put_snapshot(raw, ticket=ticket)
    assert binding.replay_store.open_snapshot(reference) == raw, "the blob is intact"

    # Edited onto the honest manifest rather than built beside it. There is no
    # public constructor left that takes expected values from a caller — a
    # manifest is issued from a capture and the store refuses one that does not
    # project it — so a manifest that points at another state is now something
    # that can only be forged, and forging it is what this case does.
    object.__setattr__(honest, "initial_snapshot_refs", (reference,))
    # ...while the manifest still claims the state it started from before.
    with pytest.raises(R.ReplayViolation) as excinfo:
        R._machines_from_manifest(
            honest,
            binding=binding,
            gas_budget=prepared.arguments["gas_budget"],
            attempt_boundary=SimpleNamespace(entering=lambda *_args: None),
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH
