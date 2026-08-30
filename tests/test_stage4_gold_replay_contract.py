"""Stage 4 replay contract, profile, adapter, and exact transcript acceptance shard."""

from __future__ import annotations

from tests.stage4_gold_replay_support import *  # noqa: F403


def test_artifact_decoder_rejects_hash_bound_but_noncanonical_program_bytes() -> None:
    """Hash identity alone cannot make a noncanonical transport executable."""

    program = llm_artifact_program("noncanonical transport")
    raw = json.dumps(program.to_dict(), sort_keys=True, indent=2).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    reference = HashBoundRef(
        kind=RefKind.PROGRAM_ARTIFACT,
        ref_id=digest,
        schema_id=SchemaVersion.REPLAY_ARTIFACT_PROGRAM_V1.value,
        sha256=digest,
        byte_length=len(raw),
        media_type="application/json",
    )
    payload = copy.deepcopy(
        json.loads(VECTORS.read_text(encoding="utf-8"))["vectors"][0]["core"]
    )
    payload["canonical_program"] = {
        "form": "ARTIFACT_REF_V1",
        "artifact_ref": reference.to_dict(),
    }
    payload["artifact_refs"] = [reference.to_dict()]
    payload["capability_requirements"] = list(R.capabilities_required_by(program))
    core = BehaviorCore.from_dict(payload)
    unit = create_behavior_unit(
        behavior_kind=core.behavior_kind,
        canonical_program=core.canonical_program,
        input_contract=core.input_contract,
        output_contract=core.output_contract,
        capability_requirements=core.capability_requirements,
        replay_contract=core.replay_contract,
        verification_contract=core.verification_contract,
        binding_refs=core.binding_refs,
        source_evidence_refs=core.source_evidence_refs,
        artifact_refs=core.artifact_refs,
    )

    class ExactBytesResolver:
        def open_artifact(self, requested: HashBoundRef) -> bytes:
            assert requested == reference
            return raw

    with pytest.raises(R.ReplayViolation) as excinfo:
        R.resolve_artifact_program(unit, resolver=ExactBytesResolver())

    assert excinfo.value.failure_code is R.ReplayFailureCode.TYPE_MISMATCH

def test_the_status_vocabulary_is_exactly_the_four_normative_members() -> None:
    assert [item.value for item in R.ReplayStatus] == [
        "REPLAY_IDENTICAL", "REPLAY_INCOMPATIBLE", "REPLAY_FAILED", "INFRA_ERROR"
    ]

def test_every_failure_reason_maps_to_a_status_and_none_maps_to_identical() -> None:
    """Fail-closed: no inadmissible state can arrive as a success."""

    for reason in R.ReplayFailureReason:
        assert R.status_for_reason(reason) is not R.ReplayStatus.REPLAY_IDENTICAL

def test_infra_error_is_distinct_from_a_genuine_failure() -> None:
    assert R.status_for_reason(R.ReplayFailureReason.MACHINE_FAULT) is R.ReplayStatus.INFRA_ERROR
    assert R.status_for_reason(R.ReplayFailureReason.GAS_EXHAUSTED) is R.ReplayStatus.REPLAY_FAILED

def test_an_unknown_opcode_has_no_class_and_no_kind() -> None:
    for call in (R.classify_replay_opcode, R.activity_kind_for_opcode):
        with pytest.raises(R.ReplayViolation) as excinfo:
            call("NOT_AN_OPCODE")
        assert excinfo.value.failure_code is R.ReplayFailureCode.OPCODE_NOT_CLASSIFIED

def test_the_profile_digest_changes_when_the_profile_changes(monkeypatch) -> None:
    """A request records which frozen profile it ran under."""

    before = R.capability_profile_digest()
    monkeypatch.setattr(
        R, "REPLAY_ADMISSIBLE_OPCODES", R.REPLAY_ADMISSIBLE_OPCODES | {"NEW_OPCODE"}
    )
    assert R.capability_profile_digest() != before

def test_a_request_made_under_another_profile_is_incompatible(monkeypatch) -> None:
    """A record pinned to one frozen profile is not evidence about another.

    Stated at the record level, because the governed path computes the digest
    inside the same call that runs — a request and a run can no longer disagree
    about the profile unless the request came from somewhere else, which is
    exactly the case a restored record represents. The rule is asked for by
    name, not through the body: reaching it through ``_execute_replay_body``
    would need an execution receipt, and a receipt is issued only for a request
    the store already holds, which a restored record is precisely not.
    """

    prepared = pure_prepared()
    request = prepared.request()
    assert R._incompatible_profile_result(request) is None, "the profile is current"
    monkeypatch.setattr(
        R, "REPLAY_ADMISSIBLE_OPCODES", R.REPLAY_ADMISSIBLE_OPCODES | {"NEW_OPCODE"}
    )
    result = R._incompatible_profile_result(request)
    assert result is not None
    assert result.status is R.ReplayStatus.REPLAY_INCOMPATIBLE
    assert result.failure_reason is R.ReplayFailureReason.CAPABILITY_PROFILE_MISMATCH
    assert result.steps_executed == 0

def test_the_adapter_satisfies_the_port() -> None:
    adapter = pure_adapter()
    assert R.require_machine_port(adapter) is adapter
    assert isinstance(adapter, R.ReplayMachinePort)

@pytest.mark.parametrize("dropped", R._MACHINE_PORT_OPERATIONS)
def test_a_machine_missing_any_operation_is_refused(dropped: str) -> None:
    class Partial(ScriptedPort):
        pass

    setattr(Partial, dropped, None)
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.require_machine_port(Partial(program="sha256:p", opcodes=["ADD"]))
    assert excinfo.value.failure_code is R.ReplayFailureCode.MACHINE_PORT_INCOMPLETE

def test_the_adapter_reports_the_loaded_program_and_the_next_opcode() -> None:
    record = golden("pure_add_v1")
    adapter = pure_adapter()
    assert adapter.program_hash() == record["program_hash"]
    assert adapter.host_abi_version() == record["host_abi_version"]
    assert adapter.next_opcode() == record["opcodes"][0]
    adapter.step()
    assert adapter.next_opcode() == record["opcodes"][1]

def test_the_adapter_refuses_a_second_channel() -> None:
    adapter = pure_adapter()
    channel = channel_for(budget=4)
    adapter.attach_channel(channel)
    with pytest.raises(R.ReplayViolation):
        adapter.attach_channel(channel)

def test_the_golden_replay_is_identical_to_its_manifest() -> None:
    record = golden("pure_add_v1")
    prepared = pure_prepared()
    result = prepared.run()

    assert result.status is R.ReplayStatus.REPLAY_IDENTICAL
    assert result.failure_reason is None
    assert result.steps_executed == record["expected_steps"]
    assert list(result.transition_hash_chain) == record["expected_transition_ids"]
    assert result.observed_transcript_root == record["expected_transcript_root"]
    assert result.knowledge_snapshot_id != ""
    assert result.consumed_activity_identities == ()
    assert result.terminal_snapshot_digests == (record["expected_terminal_snapshot_digest"],)
    R.validate_replay_result(result)

def test_the_request_carries_the_whole_schema_23_names() -> None:
    record = golden("pure_add_v1")
    prepared = pure_prepared()
    request = prepared.request()
    # §21 names the selected knowledge state and the transaction that publishes
    # it separately, and the request carries both: the snapshot identity is the
    # manifest the committed boundary points at, never a string the caller chose
    # and never a second copy of the boundary id.
    assert request.knowledge_snapshot_id == request.snapshot_manifest_ref.ref_id
    assert request.knowledge_snapshot_id != request.boundary_ref.ref_id
    assert request.behavior_content_keys == (record["behavior_content_key"],)
    assert request.program_hashes == (record["program_hash"],)
    assert request.bindings[0].host_abi_version == record["host_abi_version"]
    assert request.capability_profile == R.REPLAY_CAPABILITY_PROFILE_V1_E1
    assert request.capability_profile_digest == record["capability_profile_digest"]
    assert request.gas_budget == GAS and request.cognitive_budget == 8
    assert request.recorded_activity_refs == ()
    assert request.schema_version is SchemaVersion.BEHAVIOR_REPLAY_REQUEST_V1
    R.validate_replay_request(request)

def test_the_replay_is_identical_run_to_run() -> None:
    roots = []
    for _ in range(3):
        prepared = pure_prepared()
        result = prepared.run()
        roots.append((result.observed_transcript_root, result.terminal_snapshot_digests))
    assert len(set(roots)) == 1

def test_the_golden_fixture_still_describes_the_compiled_program() -> None:
    """A drifted fixture would silently stop testing anything."""

    record = golden("pure_add_v1")
    _, binding = pure_behavior()
    assert binding.actual_program_hash == record["program_hash"]
    assert [item.op for item in binding.program.instructions] == record["opcodes"]
    assert R.capability_profile_digest() == record["capability_profile_digest"]

def test_an_observation_is_produced_for_each_behavior() -> None:
    record = golden("pure_add_v1")
    prepared = pure_prepared()
    result = prepared.run()
    (observation,) = result.observations
    assert observation.behavior_content_key == record["behavior_content_key"]
    assert observation.transcript_matched
    assert observation.failure_reason is None
    assert observation.initial_snapshot_digest == record["initial_snapshot_digest"]
    assert observation.terminal_snapshot_digest == record["expected_terminal_snapshot_digest"]
    R.validate_replay_observation(observation)

def test_a_missing_transition_is_a_mismatch_not_a_silence() -> None:
    prepared, transitions = scripted_prepared(["ADD", "SUB", "MUL"])
    result = run_scripted(prepared, opcodes=["ADD", "SUB"])
    assert_contract_rejected(result)
    assert result.steps_executed == 2 < len(transitions)

def test_an_extra_transition_is_a_mismatch() -> None:
    prepared, _ = scripted_prepared(["ADD", "SUB"])
    assert_contract_rejected(run_scripted(prepared, opcodes=["ADD", "SUB", "MUL"]))

def test_a_duplicate_transition_cannot_hide_an_omission() -> None:
    """Equal set, different count. Only the count check sees this one.

    The observed transcript visits A twice and never reaches the third expected
    transition, so its *set* is a subset that happens to equal the expected set
    once deduplicated. A set comparison alone reports a match.
    """

    prepared, transitions = scripted_prepared(["ADD", "SUB"])
    # Built from the contract's own length rather than by unpacking two names.
    # The contract is the transcript a real program produces, and that is however
    # many transitions the program takes — six, for the behaviour in the shared
    # vector. Naming two was a leftover from when the contract was invented from
    # a scripted port's opcode list, and it now raises before the case runs.
    script = [transitions[0], *transitions]
    result = run_scripted(
        prepared, opcodes=["ADD"] * len(script), hash_script=script
    )
    assert frozenset(result.transition_hash_chain) == frozenset(transitions)
    assert len(result.transition_hash_chain) != len(transitions)
    assert_contract_rejected(result)

def test_a_substituted_transition_is_a_mismatch_and_is_located() -> None:
    """And *located*: the index reported is where the transcripts first differ.

    Built by substituting one transition into the behaviour's own expected
    transcript, so everything before the substitution matches. An earlier
    revision drove an unrelated opcode list and asserted index 1, which held
    only while the expected transcript was itself invented from an opcode list —
    against a real program every transition differs and the first index is 0,
    which says nothing about locating anything.
    """

    prepared, transitions = scripted_prepared(["ADD", "SUB", "MUL"])
    assert len(transitions) > 2, "locating a substitution needs a transcript to locate it in"
    script = list(transitions)
    script[1] = "sha256:" + "f" * 64
    result = run_scripted(prepared, opcodes=["ADD"] * len(script), hash_script=script)
    assert_contract_rejected(result)
    assert result.first_unexpected_index == 1

def test_a_different_program_hash_is_incompatible_and_runs_nothing() -> None:
    prepared, _ = scripted_prepared(["ADD"])
    result = run_scripted(prepared, program="sha256:some-other-program", opcodes=["ADD"])
    assert R.status_for_reason(result.failure_reason) is R.ReplayStatus.REPLAY_INCOMPATIBLE
    assert result.failure_reason is R.ReplayFailureReason.PROGRAM_HASH_MISMATCH
    assert result.steps_executed == 0, "a refused program executed anyway"

def test_a_different_host_abi_is_incompatible() -> None:
    prepared, _ = scripted_prepared(["ADD"])
    result = run_scripted(prepared, program=prepared.program_hash, opcodes=["ADD"], host_abi="9.9")
    assert R.status_for_reason(result.failure_reason) is R.ReplayStatus.REPLAY_INCOMPATIBLE
    assert result.failure_reason is R.ReplayFailureReason.HOST_ABI_MISMATCH

def test_a_manifest_must_describe_every_admitted_behavior() -> None:
    """One machine per behaviour, stated where the machines now come from.

    This used to be a check on the ``machines`` argument. There is no such
    argument any more — the executor builds its machines from the manifest — so
    the same rule lives where the count is now decided: a manifest whose columns
    do not describe every behaviour describes a different run.

    There is no constructor to hand ragged columns to, either: a manifest is
    issued from a capture and the store refuses one that does not project it.
    So the ragged manifest is forged from an honest one, which is the only way
    such a record can now come into existence, and validation still refuses it.
    """

    prepared = pure_prepared()
    binding = prepared._governed()["binding"]
    manifest = binding.replay_store.require_manifest(
        prepared.manifest_ref(binding.replay_store)
    )
    R.validate_replay_manifest(manifest)
    object.__setattr__(manifest, "program_hashes", manifest.program_hashes + ("sha256:one",))
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.validate_replay_manifest(manifest)
    assert excinfo.value.failure_code is R.ReplayFailureCode.TYPE_MISMATCH

def test_an_ordered_behavior_set_replays_in_order() -> None:
    """§23 admits an *ordered* behavior set, and the order is the run's own.

    Two behaviors, published into one world, admitted under one committed
    boundary, and executed in the reverse of the canonical subject order. Both
    halves matter. Without the second subject there is no set to order; without
    the deliberate disagreement between execution sequence and canonical order
    the case would pass against a build that conflates them — which is the build
    this repository had, and which answered `UNORDERED_SUBJECT`.
    """

    unit_a, _binding_a = pure_behavior()
    unit_b = real_behavior(literal=31)
    assert unit_a.content_key.value != unit_b.content_key.value

    prepared = prepare_many((unit_a, unit_b))
    ordered_units = prepared.units
    execution = tuple(item.subject_ref for item in prepared.subjects)
    # The admitted set is canonical; the run is not, and that is the point.
    assert execution != A.canonical_subject_refs(execution), (
        "the execution order was not made to differ from the canonical order"
    )

    result = prepared.run()
    assert [item.behavior_content_key for item in result.observations] == [
        item.content_key.value for item in ordered_units
    ], "observations did not follow the execution order the run declared"

def test_a_behavior_cannot_appear_twice_in_one_replay() -> None:
    unit, _binding = pure_behavior()
    subject = R.replay_subject(subject_ref=admitted_subject(unit), unit=unit)
    prepared = prepare_for(unit)
    prepared.subjects = (subject, subject)
    with pytest.raises(Exception) as excinfo:
        prepared.request()
    # The admitted set names the subject once, so a repeated subject is refused
    # before compilation as a subject mismatch rather than after it as a
    # duplicate behavior — an earlier refusal for the same reason.
    assert getattr(excinfo.value, "failure_code", None) is not None
