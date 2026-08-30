"""Stage 4 execution limits and recorded activity channel acceptance shard."""

from __future__ import annotations

from tests.stage4_gold_replay_support import *  # noqa: F403


@pytest.mark.parametrize(
    "disposition",
    [
        ACT.ActivityDisposition.FORBIDDEN_IN_REPLAY,
        ACT.ActivityDisposition.REQUIRES_FRESH_AUTHORITY,
    ],
    ids=lambda item: item.value,
)
def test_a_non_consumable_activity_never_reaches_a_machine(disposition) -> None:
    """OD-10 refuses before compilation, which is earlier than the side effect.

    ``FORBIDDEN_IN_REPLAY`` is a refusal and ``REQUIRES_FRESH_AUTHORITY`` is a
    statement that a live call would be needed — during a replay that is also a
    refusal, and never a weaker permission that ripens with time. Both are
    answered by the activity policy evaluator, so the run stops before anything
    is compiled rather than at the channel.
    """

    activity = recorded_llm_call(policy_disposition=disposition)
    prepared, _ = scripted_prepared(["ADD", "LLM_EVAL"], activities=(activity,))
    with pytest.raises(R.ReplayViolation) as excinfo:
        prepared.run()
    assert excinfo.value.failure_code is R.ReplayFailureCode.ACTIVITY_NOT_GOVERNED

def test_a_forbidden_host_call_fails_before_its_side_effect() -> None:
    """The policy refusal occurs before channel attachment and machine movement."""

    activity = recorded_llm_call(
        policy_disposition=ACT.ActivityDisposition.FORBIDDEN_IN_REPLAY
    )
    prepared, _ = scripted_prepared(["LLM_EVAL"], activities=(activity,))
    with pytest.raises(R.ReplayViolation) as excinfo:
        # The public governed path, with the real machine it builds itself: the
        # refusal is the policy's and it lands before any channel is attached.
        prepared.run()
    assert excinfo.value.failure_code is R.ReplayFailureCode.ACTIVITY_NOT_GOVERNED

def test_gas_exhaustion_is_a_typed_failure() -> None:
    prepared, _ = scripted_prepared(["ADD", "SUB", "MUL"], gas_budget=2)
    result = run_scripted(prepared, opcodes=["ADD", "SUB", "MUL"], gas=100)
    assert R.status_for_reason(result.failure_reason) is R.ReplayStatus.REPLAY_FAILED
    assert result.failure_reason is R.ReplayFailureReason.GAS_EXHAUSTED
    assert result.steps_executed == 2

def test_an_exhausted_cognitive_budget_is_a_typed_failure() -> None:
    """A separate bound from gas: it limits reliance on external results."""

    first = recorded_llm_call(prompt=b"one", sequence=1)
    second = recorded_llm_call(prompt=b"two", sequence=2)

    def step(port, opcode):
        if opcode == "LLM_EVAL":
            index = json.loads(port.snapshot_bytes())["index"]
            port.channel.resolve(
                kind=ACT.ActivityKind.LLM_CALL,
                inputs=ACT.activity_inputs(prompt=b"one" if index == 0 else b"two"),
                position=ACT.ActivityPosition(
                    program_hash="sha256:scripted", instruction_pointer=0,
                    frame_depth=0, sequence=1 if index == 0 else 2,
                ),
            )

    prepared, _ = scripted_prepared(
        ["LLM_EVAL", "LLM_EVAL"], activities=(first, second,), cognitive_budget=1
    )
    result = run_scripted(prepared, opcodes=["LLM_EVAL", "LLM_EVAL"], on_step=step)
    assert result.failure_reason is R.ReplayFailureReason.COGNITIVE_BUDGET_EXHAUSTED

def test_a_step_limit_is_a_typed_failure() -> None:
    prepared, _ = scripted_prepared(["ADD", "SUB", "MUL"], step_limit=2)
    result = run_scripted(prepared, opcodes=["ADD", "SUB", "MUL"])
    assert result.failure_reason is R.ReplayFailureReason.STEP_LIMIT_REACHED

def test_an_unknown_host_call_stops_the_run_before_executing_it() -> None:
    prepared, _ = scripted_prepared(["ADD", "SUB"])
    result = run_scripted(prepared, opcodes=["ADD", "NOT_AN_OPCODE"])
    assert result.failure_reason is R.ReplayFailureReason.UNKNOWN_HOST_CALL
    assert result.steps_executed == 1

def test_a_faulting_machine_escapes_the_driver_without_a_behavior_verdict() -> None:
    def explode(port, opcode):
        if opcode == "SUB":
            raise ZeroDivisionError("machine fault")

    prepared, _ = scripted_prepared(["ADD", "SUB"])
    with pytest.raises(ZeroDivisionError, match="machine fault"):
        run_scripted(prepared, opcodes=["ADD", "SUB"], on_step=explode)

def test_gas_that_increases_is_refused_outright() -> None:
    prepared, _ = scripted_prepared(["ADD", "SUB"])
    with pytest.raises(R.ReplayViolation) as excinfo:
        run_scripted(prepared, opcodes=["ADD", "SUB"], gas_after=lambda gas: gas + 5)
    assert excinfo.value.failure_code is R.ReplayFailureCode.GAS_NOT_MONOTONE

def test_the_golden_activity_record_round_trips() -> None:
    record, _, records = effect_fixture()
    rebuilt = ACT.activity_record_from_dict(records[0])
    assert rebuilt.activity_identity == record["activity_identity"]
    assert rebuilt.lookup_key == record["activity_lookup_key"]
    assert rebuilt.to_dict() == records[0]

def test_a_recorded_result_is_injected_without_a_fresh_external_call() -> None:
    """The adapter serves LLM_EVAL from record; no live producer is reachable."""

    record, program, records = effect_fixture()
    activity = rebuild_recorded_activity(records[0]["payload"])
    channel = channel_for(activity, budget=8)
    adapter = vm_adapter(program)
    adapter.attach_channel(channel)

    seen = []
    while not adapter.is_halted() and adapter.next_opcode() is not None:
        adapter.step()
        seen.append(adapter.transition_hash())

    assert seen == record["expected_transition_ids"]
    assert channel.consumed_identities() == (record["activity_identity"],)
    assert channel.consumed_lookup_keys() == (record["activity_lookup_key"],)
    assert adapter.snapshot_digest() == record["expected_terminal_snapshot_digest"]

def test_an_unrecorded_activity_stops_the_replay_instead_of_happening_again() -> None:
    _, program, _ = effect_fixture()
    channel = channel_for(budget=8)
    adapter = vm_adapter(program)
    adapter.attach_channel(channel)
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        for _ in range(10):
            adapter.step()
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.ACTIVITY_NOT_RECORDED

def test_the_adapter_refuses_an_effect_with_no_channel() -> None:
    """No channel means no recorded result, and the machine's stub is not one."""

    _, program, _ = effect_fixture()
    adapter = vm_adapter(program)
    with pytest.raises(R.ReplayViolation) as excinfo:
        for _ in range(10):
            adapter.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.CHANNEL_CLOSED

def test_a_replay_consuming_the_wrong_activity_set_fails() -> None:
    """The transitions matched, the effects did not. That is not identity."""

    activity = recorded_llm_call()
    other = recorded_llm_call(prompt=b"a different prompt", sequence=2)
    prepared, _ = scripted_prepared(
        ["ADD", "LLM_EVAL"],
        activity_ids=(other.activity_identity,),
        activities=(activity, other,),
    )
    result = run_scripted(prepared, opcodes=["ADD", "LLM_EVAL"], on_step=consuming_step())
    assert result.failure_reason is R.ReplayFailureReason.TRANSITION_MISMATCH

def test_the_channel_closes_when_the_replay_ends() -> None:
    """A channel outlives no attempt, and a machine that kept one cannot use it.

    The channel is captured as the driver hands it to the port, so the case can
    hold the same object the run held and ask it for an effect afterwards. An
    adapter that stashed a reference would get exactly this refusal.
    """

    seen = []
    prepared, _ = scripted_prepared(["ADD"])
    run_scripted(
        prepared,
        opcodes=["ADD"],
        on_step=lambda port, opcode: seen.append(port.channel),
    )
    channel = seen[0]
    with pytest.raises(R.ReplayViolation) as excinfo:
        channel.resolve(
            kind=ACT.ActivityKind.LLM_CALL,
            inputs=ACT.activity_inputs(prompt=b"explain"),
            position=ACT.ActivityPosition(
                program_hash="sha256:scripted", instruction_pointer=0, frame_depth=0, sequence=1
            ),
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.CHANNEL_CLOSED

def test_the_channel_closes_even_when_the_machine_faults() -> None:
    """And it closes on the way out of a fault, not only on the happy path."""

    seen = []

    def explode(port, opcode):
        seen.append(port.channel)
        raise RuntimeError("boom")

    prepared, _ = scripted_prepared(["ADD"])
    with pytest.raises(RuntimeError, match="boom"):
        run_scripted(prepared, opcodes=["ADD"], on_step=explode)
    assert seen and not seen[0].is_open

def test_a_channel_cannot_be_built_outside_a_replay() -> None:
    with pytest.raises(TypeError):
        R.RecordedActivityChannel(ledger(), 4, None)

def test_the_request_pins_the_activity_history_it_will_consume() -> None:
    activity = recorded_llm_call()
    prepared, _ = scripted_prepared(["ADD", "LLM_EVAL"], activities=(activity,))
    request = prepared.request()
    (durable_ref,) = request.recorded_activity_refs
    durable_activity = prepared.bundle.activity_store.require_record(durable_ref)
    assert ACT.activity_ref(durable_activity) == durable_ref
    assert durable_activity.kind is activity.kind
    assert durable_activity.inputs == activity.inputs
    assert durable_activity.position == activity.position
    assert durable_activity.result_sha256 == activity.result_sha256
    assert durable_activity.result_ref == activity.result_ref
    assert prepared.bundle.activity_store.open_result(durable_activity.result_ref) == R_RESULT
    assert request.activity_identities == (activity.activity_identity,)
