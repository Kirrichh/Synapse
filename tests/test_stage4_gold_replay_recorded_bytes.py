"""Stage 4 exact recorded bytes and replay persistence acceptance shard."""

from __future__ import annotations

from tests.stage4_gold_replay_support import *  # noqa: F403


def test_the_channel_produces_the_exact_bytes_the_record_names() -> None:
    """The value, not a description of it, and byte-identical to what was stored."""

    activity, channel, _ = effect_run(GOLDEN_EFFECT_RESULT)
    raw = channel.open_result(activity)
    assert raw == GOLDEN_EFFECT_RESULT
    assert hashlib.sha256(raw).hexdigest() == activity.result_sha256
    assert RVC.decode_recorded_result(raw) == "the recorded model answer"

def test_the_recorded_bytes_are_what_the_machine_carries_forward() -> None:
    """Change only the recorded result, and the machine's state changes with it.

    The effect's value is pushed and then popped, so the observable difference
    is the state *at* the transition that consumed it. A run that injected a
    description keyed on identity, or a constant stub, would reach the same
    state under both records — which is exactly the defect this replaces.
    """

    record, _, _ = effect_fixture()
    _, _, golden_digests = effect_run(GOLDEN_EFFECT_RESULT)
    _, _, other_digests = effect_run(RVC.encode_recorded_result("a different answer"))
    assert golden_digests[2] != other_digests[2], "the injected value did not reach the machine"
    assert golden_digests[:2] == other_digests[:2], "only the effect's transition should differ"
    # Difference alone is not enough: a description of the activity that quoted
    # the result digest would also differ between these two runs. The golden
    # state is asserted as well, so the value that reached the machine has to be
    # the recorded one and not merely a function of it.
    assert golden_digests[-1] == record["expected_terminal_snapshot_digest"]

def test_a_metadata_description_of_the_result_is_not_the_result() -> None:
    """The old stub, recorded verbatim, does not reproduce the golden state.

    Stated as a comparison rather than as a claim about the implementation: if
    a description of the activity were what the machine received, these two runs
    would agree, and the golden fixture would still be reached.
    """

    record, _, _ = effect_fixture()
    stub = RVC.encode_recorded_result(
        {
            "opcode": "LLM_EVAL",
            "status": "replayed",
            "activity_identity": record["activity_identity"],
            "result_sha256": hashlib.sha256(GOLDEN_EFFECT_RESULT).hexdigest(),
        }
    )
    _, _, golden_digests = effect_run(GOLDEN_EFFECT_RESULT)
    _, _, stub_digests = effect_run(stub)
    assert stub_digests[2] != golden_digests[2]
    assert golden_digests[-1] == record["expected_terminal_snapshot_digest"]

def test_a_recorded_result_whose_bytes_were_never_stored_stops_the_replay() -> None:
    """A record naming a blob the store does not hold is not a usable record.

    This is the case the metadata stub hid: with no bytes to load, the old path
    still answered the machine, because what it answered with never came from a
    store at all.
    """

    _, program, records = effect_fixture()
    payload = dict(records[0]["payload"])
    orphan = b"bytes that are never published to any store"
    activity = ACT.record_activity(
        kind=ACT.ActivityKind(payload["kind"]),
        inputs=ACT.ActivityInputs.from_dict(payload["inputs"]),
        position=ACT.ActivityPosition.from_dict(payload["position"]),
        result=orphan,
        result_ref=ACTIVITY_RESULT_REF(orphan),
        context=RECORD_CONTEXT,
        entitlement=_fixture_activity_entitlement(
            kind=ACT.ActivityKind(payload["kind"]),
            inputs=ACT.ActivityInputs.from_dict(payload["inputs"]),
            position=ACT.ActivityPosition.from_dict(payload["position"]),
            result=orphan,
            result_ref=ACTIVITY_RESULT_REF(orphan),
        ),
    )
    channel = channel_for(activity, budget=8)
    adapter = vm_adapter(program)
    adapter.attach_channel(channel)
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        for _ in range(10):
            adapter.step()
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.ACTIVITY_NOT_RECORDED

def test_a_rewritten_blob_is_refused_rather_than_injected() -> None:
    """The store re-derives the digest, so bytes swapped underneath it do not pass."""

    substituted = RVC.encode_recorded_result("bytes written under someone else's name")
    activity, channel, _ = effect_run(GOLDEN_EFFECT_RESULT)
    store = channel._results
    blob = store._blob_path(activity.result_sha256)
    original = blob.read_bytes()
    blob.write_bytes(substituted)
    try:
        with pytest.raises(ACT.ActivityViolation) as excinfo:
            channel.open_result(activity)
    finally:
        blob.write_bytes(original)
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.RESULT_HASH_MISMATCH
    assert channel.open_result(activity) == GOLDEN_EFFECT_RESULT

def test_result_bytes_that_are_not_canonical_under_the_codec_are_refused() -> None:
    """A reference is hash-bound only if reader and writer agree what the bytes are."""

    with pytest.raises(R.ReplayViolation) as excinfo:
        RVC.decode_recorded_result(b"\xff not json at all")
    assert excinfo.value.failure_code is R.ReplayFailureCode.RESULT_NOT_DECODABLE

def test_the_request_is_durable_before_the_first_transition(monkeypatch) -> None:
    """Observed from inside the run, because the ordering is the whole claim.

    Counting the store after the run would pass for a path that recorded the
    request last, so the count is taken at the moment the governed executor is
    about to drive its first behaviour — the machines are built, nothing has
    stepped, and the request must already be findable.

    Observed by wrapping the owner's transition driver rather than by scripting a
    machine. The transition driver writes nothing at all, so a run through it has
    no request to be durable and could never have shown this; and the reference
    capture reaches the driver through its own import, so what this wrapper sees
    is the governed phase and only that.
    """

    prepared = pure_prepared()
    store = prepared.bundle.replay_store
    seen: dict = {}
    real_driver = R._drive_one_behavior

    def observing_driver(**kwargs):
        seen.setdefault("requests", len(store.recorded_request_refs()))
        seen.setdefault("results", len(store.recorded_result_refs()))
        return real_driver(**kwargs)

    before = len(store.recorded_request_refs())
    results_before = len(store.recorded_result_refs())
    monkeypatch.setattr(R, "_drive_one_behavior", observing_driver)
    result = prepared.run()
    assert result.status is R.ReplayStatus.REPLAY_IDENTICAL
    assert seen["requests"] == before + 1, "the request was not durable before the run started"
    assert seen["results"] == results_before, "a result was recorded before the run produced one"
    assert len(store.recorded_result_refs()) == results_before + 1

def test_the_result_is_durable_whatever_it_says() -> None:
    """All four statuses, not only the good ones — NR-13 forbids the selection."""

    # A failing run is now produced by a real constraint rather than by a port
    # narrating a transcript: the reference capture is taken under budgets that
    # let the behaviour finish, and the run itself is then given less gas than it
    # needs. What is durable either way is the attempt.
    for gas_budget, status in (
        (1_000, R.ReplayStatus.REPLAY_IDENTICAL),
        (3, R.ReplayStatus.REPLAY_FAILED),
    ):
        prepared, _ = scripted_prepared(["ADD", "SUB"], gas_budget=gas_budget)
        store = prepared.bundle.replay_store
        result = prepared.run()
        assert result.status is status
        restored = store.require_result(R.replay_result_ref(result))
        assert restored.status is status
        assert restored.to_dict() == result.to_dict()

def test_the_durable_result_names_a_request_the_same_store_holds() -> None:
    """The pairing is what makes the history a history rather than two lists."""

    prepared, _ = scripted_prepared(["ADD"])
    store = prepared.bundle.replay_store
    result = prepared.run()
    record = store.request_record(result.request_ref)
    assert record["payload"]["schema_version"] == SchemaVersion.BEHAVIOR_REPLAY_REQUEST_V1.value
    assert record["envelope"]["run_id"] == RECORD_CONTEXT.run_id.to_dict()

def test_a_result_cannot_be_recorded_for_a_request_the_store_never_saw() -> None:
    """A run that appeared out of nowhere with an outcome attached is refused.

    On a coordinator of its own, deliberately. A mutation that raises leaves its
    interval open on purpose — the store is unsettled and every reader must keep
    refusing until someone looks — so a refused append against the world's fence
    would close that world for good. The refusal under test is the store's, and
    it does not care whose coordinator it happened under.

    What surfaces is therefore the coordinator's ``MUTATION_ABORTED``, with the
    store's own reason as its cause. Both are asserted: the outer says the store
    is now unsettled and readers must refuse, the inner says why the append was
    refused, and collapsing either into the other would lose a fact.
    """

    from tests.gold_store_fence import quiet_fence
    from synapse.experiments.gold.admission_journal import (
        JournalAdapterFailureCode,
        JournalAdapterViolation,
    )
    from synapse.experiments.gold.persistence import store_transaction
    from synapse.experiments.gold.replay_store import FileReplayStore, ReplayStoreViolation

    # A sealed result of a real governed run. The store's rule is about a result
    # whose *request* it never saw, so the object has to be a result — the
    # transition driver's raw output would be refused for not being one, and the
    # case would pass while testing nothing about orphaned results.
    prepared = pure_prepared()
    result = prepared.run()
    fence = quiet_fence()
    root = WORLD.stores_root(prepared.core, prepared.extra) / "orphan-results"
    root.mkdir(parents=True, exist_ok=True)
    empty = FileReplayStore(root, mutation_fence=fence)
    with pytest.raises(JournalAdapterViolation) as excinfo:
        with store_transaction(empty.mutation_fence) as ticket:
            empty.append_result(result, ticket=ticket)
    assert excinfo.value.failure_code is JournalAdapterFailureCode.MUTATION_ABORTED
    cause = excinfo.value.__cause__
    assert type(cause) is ReplayStoreViolation
    assert cause.failure_code is R_STORE.ReplayStoreFailureCode.REQUEST_NOT_RECORDED
    assert empty.recorded_result_refs() == ()

def test_a_restarted_store_still_holds_what_the_run_recorded() -> None:
    """Durability means a second process reads it, not that one object remembers."""

    # A governed run, because durability is a property of the record a governed
    # run seals. The transition driver produces a raw fact and stores nothing, so
    # asking a reopened store for one would be asking for something no process
    # ever wrote.
    prepared = pure_prepared()
    result = prepared.run()
    reopened = replica_of(prepared.bundle.replay_store, prepared, "restart")
    restored = reopened.require_result(R.replay_result_ref(result))
    assert restored.to_dict() == result.to_dict()
    assert R.replay_result_ref(restored).sha256 == R.replay_result_ref(result).sha256

def test_a_torn_replay_journal_is_refused_rather_than_read() -> None:
    """A partial write at the tail is a torn history, not a shorter one."""

    from synapse.experiments.gold.replay_store import ReplayStoreViolation

    prepared = pure_prepared()
    prepared.run()
    torn = replica_of(
        prepared.bundle.replay_store, prepared, "torn", mutate=lambda raw: raw[:-9]
    )
    with pytest.raises(ReplayStoreViolation) as excinfo:
        torn.recorded_result_refs()
    assert excinfo.value.failure_code is R_STORE.ReplayStoreFailureCode.HISTORY_TORN

def test_a_tampered_replay_record_is_refused_rather_than_believed() -> None:
    """Rewriting a recorded byte breaks the frame, and the store says so."""

    from synapse.experiments.gold.replay_store import ReplayStoreViolation

    prepared, _ = scripted_prepared(["ADD"])
    run_scripted(prepared, opcodes=["ADD"])

    def flip(raw: bytes) -> bytes:
        index = len(raw) // 2
        return raw[:index] + bytes([raw[index] ^ 0x01]) + raw[index + 1 :]

    tampered = replica_of(prepared.bundle.replay_store, prepared, "tampered", mutate=flip)
    with pytest.raises(ReplayStoreViolation) as excinfo:
        tampered.recorded_result_refs()
    assert excinfo.value.failure_code in {
        R_STORE.ReplayStoreFailureCode.HISTORY_CORRUPT,
        R_STORE.ReplayStoreFailureCode.HISTORY_TORN,
        R_STORE.ReplayStoreFailureCode.HISTORY_FORKED,
    }
