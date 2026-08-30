"""Stage 4 dispatch and closed-value safety acceptance shard."""

from __future__ import annotations

from tests.stage4_gold_replay_support import *  # noqa: F403


def test_an_ordinary_python_callable_is_refused_before_it_is_called() -> None:
    """The refusal is pre-dispatch: the callee is still on the stack afterwards."""

    calls: list[int] = []
    adapter, state = dispatching_adapter(
        [
            {"op": "CALL", "a": 0, "b": None, "c": None},
            {"op": "HALT", "a": None, "b": None, "c": None},
        ],
        [lambda: calls.append(1)],
    )
    with pytest.raises(R.ReplayViolation) as excinfo:
        adapter.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.UNGOVERNED_DISPATCH
    assert calls == [], "the callable ran before it was refused"
    assert callable(state.stack[-1]), "the refusal disturbed the operand stack"

def test_an_ordinary_python_method_is_refused_before_it_is_called() -> None:
    """``CALL_METHOD`` reaches arbitrary Python by another route, and is closed too."""

    adapter, state = dispatching_adapter(
        [
            {"op": "CALL_METHOD", "a": "upper", "b": 0, "c": None},
            {"op": "HALT", "a": None, "b": None, "c": None},
        ],
        ["a recorded string"],
    )
    with pytest.raises(R.ReplayViolation) as excinfo:
        adapter.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.UNGOVERNED_DISPATCH
    assert state.stack[-1] == "a recorded string"

def test_a_compiled_synapse_function_still_dispatches() -> None:
    """The guard refuses arbitrary Python, not the machine's own control flow.

    A ``FunctionObject`` is an internal transition: the body it enters is the
    same governed program, and every effect inside it reaches the same channel.
    Refusing it would make the guard a ban on function calls.
    """

    program = BytecodeProgram(
        instructions=[
            Instruction("MAKE_FUNCTION", "inner", 0, 2),
            Instruction("CALL", 0),
            Instruction("HALT"),
        ],
        constants=[[]],
    )
    state = VMState(gas_remaining=GAS)
    adapter = vm_adapter(program, state=state)
    for _ in range(2):
        adapter.step()
    assert state.ip == 2, "the machine did not enter the function body"

def test_a_dispatch_the_machine_would_route_to_its_host_is_left_to_the_channel() -> None:
    """A non-callable callee is a host route, and the host route is governed.

    Refusing it here would move a governed effect into an ungoverned refusal, so
    the guard lets it through and the channel answers — which with no channel
    attached is ``CHANNEL_CLOSED``, not ``UNGOVERNED_DISPATCH``.
    """

    adapter, _state = dispatching_adapter(
        [
            {"op": "CALL", "a": 0, "b": None, "c": None},
            {"op": "HALT", "a": None, "b": None, "c": None},
        ],
        ["a value"],
    )
    with pytest.raises(R.ReplayViolation) as excinfo:
        adapter.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.CHANNEL_CLOSED

def test_refusing_a_method_dispatch_does_not_consult_the_subject() -> None:
    """The reproduction that opened this: the guard used to ask before refusing.

    ``getattr(subject, name, None)`` is a lookup, and a lookup is the subject's
    own code. The refusal arrived, but after ``__getattribute__`` had already
    run — so the guard against executing ungoverned code executed ungoverned
    code in order to decide.
    """

    subject = RecordingSubject()
    adapter, _state = dispatching_adapter(
        [
            {"op": "CALL_METHOD", "a": "upper", "b": 0, "c": None},
            {"op": "HALT", "a": None, "b": None, "c": None},
        ],
        [subject],
    )
    del subject.touches[:]
    with pytest.raises(R.ReplayViolation) as excinfo:
        adapter.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE
    assert subject.touches == [], "the refusal consulted the value it was refusing"

def test_a_canonical_subject_is_still_read_without_the_descriptor_protocol() -> None:
    """The narrow fix would be to refuse everything; that is not the fix.

    A string is a canonical machine value, so ``CALL_METHOD`` on it is still
    classified — and still refused, because ``str.upper`` is ordinary Python.
    Reading it through ``getattr_static`` is what makes the classification
    possible without a lookup.
    """

    adapter, _state = dispatching_adapter(
        [
            {"op": "CALL_METHOD", "a": "upper", "b": 0, "c": None},
            {"op": "HALT", "a": None, "b": None, "c": None},
        ],
        ["a recorded string"],
    )
    with pytest.raises(R.ReplayViolation) as excinfo:
        adapter.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.UNGOVERNED_DISPATCH

def test_the_digest_does_not_serialize_a_value_that_would_serialize_itself() -> None:
    """``snapshot_digest`` is where a replay's identity is measured.

    Running the measured object's own code inside the measurement is the worst
    place for it, and the machine's encoder reaches ``repr`` for anything it does
    not recognise. The refusal has to come before the encoder, not from it.
    """

    subject = RecordingSubject()
    adapter, state = dispatching_adapter([{"op": "HALT", "a": None, "b": None, "c": None}])
    state.stack.append(subject)
    del subject.touches[:]
    with pytest.raises(R.ReplayViolation) as excinfo:
        adapter.snapshot_digest()
    assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE
    assert subject.touches == [], "the digest ran the value's own repr"

def test_a_transition_does_not_hash_a_value_that_would_hash_itself() -> None:
    """The same hazard once per step: ``_hash_transition`` reprs the stack top."""

    subject = RecordingSubject()
    adapter, state = dispatching_adapter([{"op": "POP", "a": None, "b": None, "c": None}])
    state.stack.append(subject)
    del subject.touches[:]
    with pytest.raises(R.ReplayViolation) as excinfo:
        adapter.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE
    assert subject.touches == []

def test_a_machine_state_handed_in_from_outside_is_refused() -> None:
    """A state is a claim, and a snapshot is a state that arrived as bytes.

    Both entry points go through the same check, because ``VMState.from_dict``
    rebuilds whatever the bytes describe and a store is not an authority on what
    a machine value is.
    """

    _, program, _ = effect_fixture()
    with pytest.raises(R.ReplayViolation) as excinfo:
        vm_adapter(program, state=VMState(stack=[RecordingSubject()]))
    assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE

    with pytest.raises(R.ReplayViolation) as excinfo:
        vm_adapter(program, state=VMState(locals={"x": RecordingSubject()}))
    assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE

def test_every_value_bearing_field_of_a_state_is_checked_not_two_of_them() -> None:
    """Мутант A15: проверка словаря сузилась обратно до stack и locals.

    The reproduction that opened this was not "a hostile value in the stack" — it
    was that ``stack`` and ``locals`` were the only two fields anyone looked at,
    while a further six carried machine values into the same encoder. So the case
    enumerates the fields off the state itself rather than listing the ones that
    were known to leak: a field added later is covered the day it is added, and a
    narrowing of the check fails here rather than in a later audit.

    The three excluded names are excluded for a reason and not by omission.
    ``ip`` and ``gas_remaining`` are machine counters, ``transition_hash`` is the
    digest itself; none of them can hold a caller's object.
    """

    _, program, _ = effect_fixture()
    default = VMState()
    fields = sorted(set(vars(default)) - {"ip", "gas_remaining", "transition_hash"})

    for name in fields:
        planted = RecordingSubject()
        current = getattr(default, name)
        if type(current) is list:
            value: object = [planted]
        elif type(current) is dict:
            value = {"x": planted}
        else:
            value = planted
        with pytest.raises(R.ReplayViolation) as excinfo:
            vm_adapter(program, state=VMState(**{name: value}))
        assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE, (
            f"a hostile value in VMState.{name} reached the encoder"
        )
        assert planted.touches == [], f"VMState.{name} was consulted before it was refused"

def test_a_frame_and_a_function_are_walked_field_by_field() -> None:
    """Frames and admitted functions are traversed rather than trusted by type."""

    from synapse.cvm import CallFrame, FunctionObject, GuardFrame

    function_program = BytecodeProgram(
        instructions=[Instruction("MAKE_FUNCTION", "f", 0, 2),
                      Instruction("MAKE_FUNCTION", "f", 1, 2), Instruction("HALT")],
        constants=[[], ["a"]],
    )
    hostile = [
        ("call frame", "call_stack", lambda planted: CallFrame(
            return_ip=0, locals_snapshot={"x": planted}
        )),
        ("guard frame", "guard_stack", lambda planted: GuardFrame(verdict=planted)),
        ("function params", "stack", lambda planted: FunctionObject(
            name="f", params=[planted], body_ip=2, program_hash=function_program.program_hash
        )),
        ("function closure", "stack", lambda planted: FunctionObject(
            name="f", params=[], body_ip=2, closure={"c": planted},
            program_hash=function_program.program_hash
        )),
    ]
    for label, field, build in hostile:
        planted = RecordingSubject()
        with pytest.raises(R.ReplayViolation) as excinfo:
            vm_adapter(function_program, state=VMState(**{field: [build(planted)]}))
        assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE, label
        assert planted.touches == [], f"the {label} was consulted before it was refused"

    vm_adapter(
        function_program,
        state=VMState(
            call_stack=[CallFrame(return_ip=0, locals_snapshot={"x": 1}, fn_name="f",
                                  program_hash=function_program.program_hash, body_ip=2)],
            guard_stack=[GuardFrame(verdict="PASS", entered_at_history_hash="sha256:genesis")],
            stack=[FunctionObject(name="f", params=["a"], body_ip=2, closure={"c": 1},
                                  program_hash=function_program.program_hash)],
        ),
    )

def test_the_value_vocabulary_is_exact_and_not_merely_structural() -> None:
    """Exact value types cannot be subclassed around before serialization."""

    class SneakyDict(dict):
        def items(self):  # pragma: no cover - must never be reached
            raise AssertionError("the encoder consulted a subclass hook")

    class SneakyStr(str):
        pass

    for value in (SneakyDict(a=1), SneakyStr("x"), b"raw bytes", {1: "int key"}, object()):
        with pytest.raises(R.ReplayViolation) as excinfo:
            RVC.require_canonical_vm_value(value)
        assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE

    from synapse.cvm import FunctionObject

    function_program = BytecodeProgram(
        instructions=[Instruction("MAKE_FUNCTION", "f", 0, 1), Instruction("HALT")],
        constants=[[]],
    )
    for value in (None, True, 7, 1.5, "text", [1, "a"], (1,), {"k": [1, {"n": None}]},
                  FunctionObject(name="f", params=[], body_ip=1, closure={"c": 1},
                                 program_hash=function_program.program_hash)):
        RVC.require_canonical_vm_value(value, program=function_program)

def test_a_value_graph_too_deep_or_too_wide_is_refused_not_walked() -> None:
    """Fail-closed at the limit: what this cannot afford to check, it refuses.

    The encoder would recurse exactly as far, so a value this validator declines
    to walk is a value the machine could not have serialized either.
    """

    deep: object = "leaf"
    for _ in range(RVC.MAX_VM_VALUE_DEPTH + 2):
        deep = [deep]
    with pytest.raises(R.ReplayViolation) as excinfo:
        RVC.require_canonical_vm_value(deep)
    assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE

    wide = list(range(RVC.MAX_VM_VALUE_NODES + 2))
    with pytest.raises(R.ReplayViolation) as excinfo:
        RVC.require_canonical_vm_value(wide)
    assert excinfo.value.failure_code is R.ReplayFailureCode.RESOURCE_LIMIT_EXCEEDED

def test_a_hostile_value_hidden_inside_a_canonical_container_is_still_refused() -> None:
    """``repr`` of a list is the ``repr`` of its elements, so the check goes deep."""

    subject = RecordingSubject()
    del subject.touches[:]
    with pytest.raises(R.ReplayViolation) as excinfo:
        RVC.require_canonical_vm_value({"outer": [{"inner": subject}]})
    assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE
    assert subject.touches == []

def test_an_attempt_that_raises_after_its_request_is_still_recorded(monkeypatch) -> None:
    """NR-13: a post-request raise is durable without inventing a verdict.

    Once the request is durable this run happened. A raise between the request
    append and the result append leaves a history in which a run started and,
    to any later reader, never finished — which is exactly the shape a run that
    was allowed to start unrecorded would leave, arrived at from the other end.

    ``GAS_NOT_MONOTONE`` is the case that shows it, because it is deliberately
    raised rather than turned into a result: gas that increases is not the
    modelled cost function, so it is not an execution outcome to be reported.
    The exception still travels — the caller asked for a run and did not get one
    — and the record exists either way.

    A governed run, because the claim is about durable records: the transition
    driver writes nothing, so a raw run has no request to orphan and no result
    to find afterwards.
    """

    prepared = pure_prepared()
    store = prepared.bundle.replay_store
    requests_before = len(store.recorded_request_refs())
    results_before = len(store.recorded_result_refs())

    _governed_driver_raising(monkeypatch, R.ReplayFailureCode.GAS_NOT_MONOTONE)
    with pytest.raises(R.ReplayViolation) as excinfo:
        prepared.run()
    assert excinfo.value.failure_code is R.ReplayFailureCode.GAS_NOT_MONOTONE

    assert len(store.recorded_request_refs()) == requests_before + 1
    assert len(store.recorded_result_refs()) == results_before
    request_ref = store.recorded_request_refs()[-1]
    assert store.result_ref_for_request(request_ref) is None
    incomplete = store.recoverable_attempts()[-1]
    assert incomplete.request_ref == request_ref
    from synapse.experiments.gold.replay_attempt_lifecycle import (
        ReplayAttemptFailureDomain,
        ReplayAttemptPhase,
    )
    assert incomplete.phase is ReplayAttemptPhase.EXECUTION
    assert incomplete.failure_domain is ReplayAttemptFailureDomain.MACHINE_ADAPTER

def test_an_incomplete_attempt_is_not_a_replay_verdict(monkeypatch) -> None:
    """§26 keeps persistence failure state outside the replay verdict enum.

    A reader of the history must be able to tell "the executor broke" from "the
    behaviour diverged". Both are recorded; they are not the same status and the
    infrastructure one carries no observations to mistake for evidence.
    """

    prepared = pure_prepared()
    store = prepared.bundle.replay_store
    _governed_driver_raising(monkeypatch, R.ReplayFailureCode.GAS_NOT_MONOTONE)
    with pytest.raises(R.ReplayViolation):
        prepared.run()
    request_ref = store.recorded_request_refs()[-1]
    assert store.result_ref_for_request(request_ref) is None
    incomplete = store.recoverable_attempts()[-1]
    assert incomplete.request_ref == request_ref
    assert incomplete.state.value == "INCOMPLETE_RECOVERABLE"
    assert incomplete.state.value not in {status.value for status in R.ReplayStatus}
