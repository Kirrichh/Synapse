"""Stage 4 Patch 9 — §23 BehaviorReplay and governed external activities.

The stage's five required checks are each a section below: exact transition
replay with program/activity manifest matching; forbidden host capability and
gas exhaustion producing typed failures; snapshot/resume reproducing the same
terminal state; a recorded activity result injected without a repeated external
call; and replay success neither establishing FULL nor standing in for oracle
verification.

Two golden fixtures back the machine-level claims. ``pure_add_v1`` pins the
transcript a compiled Patch 6 behavior unit actually produces; ``llm_effect_v1``
pins a program whose ``LLM_EVAL`` is served from a recorded activity record.
Both carry their VM snapshot, so the resume path is checked against a real
machine state rather than a constructed one.

Where a scripted port appears it is not a shortcut. ``ReplayMachinePort`` is the
contract Patch 9 defines, and the cases that matter most — a machine that
faults, one reporting an unclassified opcode, one whose gas goes up, one running
a program other than the bound one — are cases a correct machine never produces.

Every mandatory Stage 9 mutant has a named killing test at the end.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from synapse.bytecode import BytecodeProgram
from synapse.cvm import GAS_COSTS
from synapse.experiments.gold import activities as ACT
from synapse.experiments.gold import admission as A
from synapse.experiments.gold import replay as R
from synapse.experiments.gold.behavior import (
    BehaviorCore,
    ReplayContract,
    ReplayResultClass,
    compile_behavior_unit,
    create_behavior_unit,
)
from synapse.experiments.gold.canonicalization import (
    HashBoundRef,
    RefKind,
    content_key_digest,
)
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    SchemaVersion,
)
from tests import gold_point_of_use_world as WORLD

NOW = datetime(2026, 7, 31, 9, 0, 0, tzinfo=timezone.utc)
POLICY = "policy-v1"
EXECUTOR = ActorIdentity(value="replay-executor")
GAS = 10_000

FIXTURES = Path(__file__).parent / "fixtures" / "gold"
VECTORS = FIXTURES / "behavior_vectors_v1.json"
GOLDEN = FIXTURES / "golden_replays"


def golden(name: str) -> dict:
    return json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))


def golden_file(name: str) -> dict:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


def ref(kind: RefKind, name: str, payload: bytes = b"p") -> HashBoundRef:
    return HashBoundRef(
        kind=kind,
        ref_id=name,
        schema_id="synapse.stage4.gold.thing/v1",
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


#: Refs belonging to no admission, for cases that need "some other run".
OTHER_CONTEXT_REF = ref(RefKind.ARTIFACT, "consumer-ctx-2")
OTHER_BOUNDARY_REF = ref(RefKind.ATOMIC_BOUNDARY, "boundary-2")


def ledger(*activities: ACT.RecordedActivity, core=None) -> ACT.ActivityLedger:
    """A sealed ledger for cases whose subject is the channel, not the request.

    A request seals its own ledger against its own admission, so this exists only
    for the cases that hand a ledger straight to ``RecordedActivityChannel``.
    """

    return ACT.seal_activity_ledger(
        activities=tuple(activities), admitted=WORLD.admitted_knowledge(core)
    )


# ---------------------------------------------------------------------------
# Behavior units
# ---------------------------------------------------------------------------


def _core(*, literal: int | None = None) -> BehaviorCore:
    payload = json.loads(VECTORS.read_text(encoding="utf-8"))["vectors"][0]["core"]
    payload = copy.deepcopy(payload)
    if literal is not None:
        # A different constant compiles to different bytecode, which is what a
        # test about program identity needs — a second contract over the same
        # program would still share its hash.
        statements = payload["canonical_program"]["ir"]["program"]["statements"]
        statements[0]["value"]["value"] = literal
    return BehaviorCore.from_dict(payload)


def unit_with(replay_contract: ReplayContract, *, literal: int | None = None):
    core = _core(literal=literal)
    return create_behavior_unit(
        behavior_kind=core.behavior_kind,
        canonical_program=core.canonical_program,
        input_contract=core.input_contract,
        output_contract=core.output_contract,
        capability_requirements=core.capability_requirements,
        replay_contract=replay_contract,
        verification_contract=core.verification_contract,
        binding_refs=core.binding_refs,
        source_evidence_refs=core.source_evidence_refs,
        artifact_refs=core.artifact_refs,
    )


def published_core(unit) -> dict:
    """The core payload a library must publish for this unit to be admissible.

    §22 decides about a *published* subject, so the behavior a case replays has
    to be the behavior its world published — not a look-alike. Taking the payload
    off the unit and handing it to the world is what makes the admitted subject
    ref and the compiled program the same object rather than two things that
    happen to agree.
    """

    return unit.core.to_dict()


def admitted_subject(unit):
    """The library subject ref the world's four gates admitted for this unit."""

    core = published_core(unit)
    reference = WORLD.subject_ref(core)
    assert reference.ref_id == unit.content_key.digest_sha256, (
        "the world published a different behavior than the one under replay"
    )
    return reference


def world_of(*units):
    """The core set one world must publish for these behaviors to be admissible.

    Returned as ``(primary, extra)`` because that is the shape the world builder
    takes: a §22 subject is a published behavior, so a replay over two behaviors
    needs both of them published, attested, lifecycled and admitted **under one
    committed boundary** — not one world each. Two worlds give two boundaries,
    and a request cannot span them.
    """

    return published_core(units[0]), tuple(published_core(item) for item in units[1:])


def admitted_subject_in(unit, primary, extra):
    """The subject ref this world's gates admitted for this exact behavior."""

    reference = WORLD.subject_ref_for(unit, primary, extra)
    assert reference.ref_id == unit.content_key.digest_sha256, (
        "the world published a different behavior than the one under replay"
    )
    return reference


def multi_request(units, *, order=None, **arguments):
    """A request over several admitted behaviors, in the caller's chosen order.

    ``order`` is the *execution* sequence, and it is deliberately allowed to
    differ from the canonical subject order §22 decides about. Defaulting it to
    the reverse of the canonical order is what makes these cases bite: a build
    that hands the execution sequence to the gate comparison is refused as
    `UNORDERED_SUBJECT`, which is how that conflation was found.
    """

    primary, extra = world_of(*units)
    admission = WORLD.admission_request(primary, extra)
    by_digest = {item.content_key.digest_sha256: item for item in units}
    canonical = A.canonical_subject_refs(
        tuple(admitted_subject_in(item, primary, extra) for item in units)
    )
    sequence = order if order is not None else tuple(reversed(canonical))
    subjects = tuple(
        R.replay_subject(subject_ref=reference, unit=by_digest[reference.ref_id])
        for reference in sequence
    )
    arguments.setdefault("activities", ())
    arguments.setdefault("gas_budget", GAS)
    arguments.setdefault("cognitive_budget", 8)
    arguments.setdefault("step_limit", 1_000)
    arguments.setdefault("executor_actor", EXECUTOR)
    request = R.create_replay_request(
        admission=admission,
        subjects=subjects,
        compiler=compile_behavior_unit,
        **arguments,
    )
    return request, admission.binding, tuple(item.unit for item in subjects)


def _machines_for(request, ordered_units, pure_unit):
    """One machine per behavior, in the request's execution order."""

    machines = []
    for index, unit in enumerate(ordered_units):
        if unit is pure_unit:
            machines.append(pure_adapter())
        else:
            machines.append(
                ScriptedPort(
                    program=request.program_hashes[index],
                    host_abi=request.bindings[index].host_abi_version,
                    opcodes=["ADD"],
                )
            )
    return tuple(machines)


def resuming_request(previous, unit, **arguments):
    """Запрос, который продолжает в точности этот результат.

    Линия объявляется в запросе, а не в вызове ``resume_replay``, поэтому
    продолжение строится здесь, а не собирается из пары аргументов на месте.
    """

    arguments.setdefault("activities", ())
    arguments.setdefault("gas_budget", GAS)
    arguments.setdefault("cognitive_budget", 8)
    arguments.setdefault("step_limit", 1_000)
    arguments.setdefault("executor_actor", EXECUTOR)
    return request_for(
        unit, resumed_from_result_ref=R.replay_result_ref(previous), **arguments
    )


def request_for(unit, *, compiler=compile_behavior_unit, **arguments):
    """Build a replay request through the production path, admitting first.

    The production binding comes back with the request, because a request is not
    an authority to replay: ``execute_replay`` re-checks the admission against
    the live authority state and needs the binding to do it. Returning the two
    together is what stops the acceptance acquiring a habit the production path
    forbids — carrying a request to a machine and running it.
    """

    core = published_core(unit)
    admission = WORLD.admission_request(core)
    request = R.create_replay_request(
        admission=admission,
        subjects=(R.replay_subject(subject_ref=admitted_subject(unit), unit=unit),),
        compiler=compiler,
        **arguments,
    )
    return request, admission.binding


def contract_for(
    transitions: tuple[str, ...], activities: tuple[str, ...] = ()
) -> ReplayContract:
    return ReplayContract(
        profile_id="synapse.stage4.test.replay-contract/v1",
        expected_transition_ids=transitions,
        expected_observation_ids=("result-observed",),
        expected_activity_ids=activities,
        allowed_result_classes=(ReplayResultClass.MATCH,),
    )


def pure_behavior():
    record = golden("pure_add_v1")
    unit = unit_with(contract_for(tuple(record["expected_transition_ids"])))
    return unit, compile_behavior_unit(unit)


def pure_adapter(gas: int = GAS) -> R.CognitiveVMReplayAdapter:
    _, binding = pure_behavior()
    return R.CognitiveVMReplayAdapter(binding.program, gas_budget=gas)


def pure_request(**overrides):
    """One fresh point-of-use attempt, and the request it admits.

    Deliberately **not** cached. An earlier revision memoised requests by their
    arguments, reasoning that a point-of-use attempt admits exactly once and two
    cases asking for the same request should share the object. That was right
    about the cost and wrong about the subject: a shared request is a request
    that outlives its admission, and reusing one across cases taught the
    acceptance to treat a request as portable authority — the exact property §22
    forbids and the production path now refuses. Each case admits for itself.
    """

    record = golden("pure_add_v1")
    unit, binding = pure_behavior()
    arguments = dict(
        activities=(),
        gas_budget=GAS,
        cognitive_budget=8,
        step_limit=1_000,
        executor_actor=EXECUTOR,
        expected_transcript_root=record["expected_transcript_root"],
    )
    arguments.update(overrides)
    request, authority = request_for(unit, **arguments)
    return request, binding, authority


# ---------------------------------------------------------------------------
# A machine port that can misbehave on demand
# ---------------------------------------------------------------------------


class ScriptedPort:
    """A ``ReplayMachinePort`` driven by a script of opcodes."""

    def __init__(
        self,
        *,
        program: str,
        opcodes: list[str],
        host_abi: str = "2.2",
        on_step=None,
        gas: int = 1_000,
        gas_after=None,
        snapshot_seed: str = "s",
        hash_script: list[str] | None = None,
    ) -> None:
        self._program = program
        self._opcodes = list(opcodes)
        self._host_abi = host_abi
        self._on_step = on_step
        self._gas = gas
        self._gas_after = gas_after
        self._seed = snapshot_seed
        # A machine that repeats a transition hash. A real chain cannot, since
        # each hash folds in its predecessor — which is exactly why a test for
        # the count check needs a port that can.
        self._hash_script = list(hash_script) if hash_script else None
        self._index = 0
        self._hash = "sha256:" + "0" * 64
        self.channel = None

    def attach_channel(self, channel) -> None:
        self.channel = channel

    def program_hash(self) -> str:
        return self._program

    def host_abi_version(self) -> str:
        return self._host_abi

    def transition_hash(self) -> str:
        return self._hash

    def instruction_pointer(self) -> int:
        return self._index

    def frame_depth(self) -> int:
        return 0

    def gas_remaining(self) -> int:
        return self._gas

    def is_halted(self) -> bool:
        return self._index >= len(self._opcodes)

    def next_opcode(self):
        if self._index >= len(self._opcodes):
            return None
        return self._opcodes[self._index]

    def snapshot_digest(self) -> str:
        return hashlib.sha256(f"{self._seed}:{self._program}:{self._index}".encode()).hexdigest()

    def step(self) -> None:
        opcode = self._opcodes[self._index]
        if self._on_step is not None:
            self._on_step(self, opcode)
        self._index += 1
        if self._hash_script is not None:
            self._hash = self._hash_script[self._index - 1]
        else:
            self._hash = "sha256:" + hashlib.sha256(f"{opcode}:{self._index}".encode()).hexdigest()
        self._gas = self._gas_after(self._gas) if self._gas_after else self._gas - 1


def scripted_transitions(opcodes: list[str]) -> tuple[str, ...]:
    port = ScriptedPort(program="sha256:scripted", opcodes=opcodes)
    seen = []
    while not port.is_halted():
        port.step()
        seen.append(port.transition_hash())
    return tuple(seen)


def scripted_request(opcodes: list[str], *, activity_ids: tuple[str, ...] = (), **overrides):
    """A request bound to a real unit whose contract matches a scripted transcript."""

    transitions = scripted_transitions(opcodes)
    unit = unit_with(contract_for(transitions, activity_ids))
    arguments = dict(
        activities=(),
        gas_budget=1_000,
        cognitive_budget=8,
        step_limit=1_000,
        executor_actor=EXECUTOR,
        expected_transcript_root=R.transcript_root(
            transitions=transitions, activities=activity_ids
        ),
    )
    arguments.update(overrides)
    request, authority = request_for(unit, **arguments)
    return request, transitions, authority


def run_scripted(request, authority, **port_kwargs) -> R.BehaviorReplayResult:
    port = ScriptedPort(
        program=request.program_hashes[0],
        host_abi=request.bindings[0].host_abi_version,
        **port_kwargs,
    )
    return R.execute_replay(request, machines=(port,), authority=authority)


# ---------------------------------------------------------------------------
# The §23 status vocabulary and the capability profile
# ---------------------------------------------------------------------------


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


def test_the_profile_classifies_every_opcode_the_machine_can_charge_for() -> None:
    classified = R.REPLAY_ADMISSIBLE_OPCODES | R.RECORDED_ONLY_OPCODES
    unclassified = set(GAS_COSTS) - classified
    assert not unclassified, f"opcodes with no determinism class: {sorted(unclassified)}"


def test_the_profile_names_no_opcode_the_machine_does_not_have() -> None:
    classified = R.REPLAY_ADMISSIBLE_OPCODES | R.RECORDED_ONLY_OPCODES
    assert not classified - set(GAS_COSTS)


def test_the_two_halves_are_disjoint() -> None:
    assert not (R.REPLAY_ADMISSIBLE_OPCODES & R.RECORDED_ONLY_OPCODES)


def test_every_effect_bearing_opcode_has_an_activity_kind() -> None:
    missing = sorted(R.RECORDED_ONLY_OPCODES - set(R.ACTIVITY_KIND_BY_OPCODE))
    assert not missing, f"effect-bearing opcodes with no activity kind: {missing}"
    assert not set(R.ACTIVITY_KIND_BY_OPCODE) - R.RECORDED_ONLY_OPCODES


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
    request, binding, authority = pure_request()
    monkeypatch.setattr(
        R, "REPLAY_ADMISSIBLE_OPCODES", R.REPLAY_ADMISSIBLE_OPCODES | {"NEW_OPCODE"}
    )
    result = R.execute_replay(request, machines=(pure_adapter(),), authority=authority)
    assert result.status is R.ReplayStatus.REPLAY_INCOMPATIBLE
    assert result.failure_reason is R.ReplayFailureReason.CAPABILITY_PROFILE_MISMATCH
    assert result.steps_executed == 0


# ---------------------------------------------------------------------------
# The adapter — NR-03's single narrow point into the protected core
# ---------------------------------------------------------------------------


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
    channel = R.RecordedActivityChannel(ledger(), 4, _seal=R._CHANNEL_SEAL)
    adapter.attach_channel(channel)
    with pytest.raises(R.ReplayViolation):
        adapter.attach_channel(channel)


# ---------------------------------------------------------------------------
# Exact transition replay and program/activity manifest matching
# ---------------------------------------------------------------------------


def test_the_golden_replay_is_identical_to_its_manifest() -> None:
    record = golden("pure_add_v1")
    request, binding, authority = pure_request()
    result = R.execute_replay(request, machines=(pure_adapter(),), authority=authority)

    assert result.status is R.ReplayStatus.REPLAY_IDENTICAL
    assert result.failure_reason is None
    assert result.steps_executed == record["expected_steps"]
    assert list(result.transition_hash_chain) == record["expected_transition_ids"]
    assert result.observed_transcript_root == record["expected_transcript_root"]
    assert result.knowledge_snapshot_id == request.knowledge_snapshot_id
    assert result.consumed_activity_identities == ()
    assert result.terminal_snapshot_digests == (record["expected_terminal_snapshot_digest"],)
    R.validate_replay_result(result)


def test_the_request_carries_the_whole_schema_23_names() -> None:
    record = golden("pure_add_v1")
    request, binding, authority = pure_request()
    # §21 names the selected knowledge state and the transaction that publishes
    # it separately, and the request carries both: the snapshot identity is the
    # manifest the committed boundary points at, never a string the caller chose
    # and never a second copy of the boundary id.
    assert request.knowledge_snapshot_id == request.snapshot_manifest_ref.ref_id
    assert request.knowledge_snapshot_id != request.boundary_ref.ref_id
    assert request.behavior_content_keys == (record["behavior_content_key"],)
    assert request.program_hashes == (record["program_hash"],)
    assert request.bindings[0].host_abi_version == record["host_abi_version"]
    assert request.capability_profile == R.REPLAY_CAPABILITY_PROFILE_V1
    assert request.capability_profile_digest == record["capability_profile_digest"]
    assert request.gas_budget == GAS and request.cognitive_budget == 8
    assert request.recorded_activity_refs == ()
    assert request.schema_version is SchemaVersion.BEHAVIOR_REPLAY_REQUEST_V1
    R.validate_replay_request(request)


def test_the_replay_is_identical_run_to_run() -> None:
    roots = []
    for _ in range(3):
        request, _, authority = pure_request()
        result = R.execute_replay(request, machines=(pure_adapter(),), authority=authority)
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
    request, _, authority = pure_request()
    result = R.execute_replay(request, machines=(pure_adapter(),), authority=authority)
    (observation,) = result.observations
    assert observation.behavior_content_key == record["behavior_content_key"]
    assert observation.transcript_matched
    assert observation.failure_reason is None
    assert observation.initial_snapshot_digest == record["initial_snapshot_digest"]
    assert observation.terminal_snapshot_digest == record["expected_terminal_snapshot_digest"]
    R.validate_replay_observation(observation)


def assert_contract_rejected(result: R.BehaviorReplayResult) -> None:
    """The behavior's own contract refused this transcript.

    Asserted at the observation, not only at the result. The result's reason is
    also reachable from the pinned-root comparison, so a result-level assertion
    alone would pass even if the contract comparison did nothing at all.
    """

    (observation,) = result.observations
    assert not observation.transcript_matched, "the contract accepted this transcript"
    assert observation.failure_reason is R.ReplayFailureReason.TRANSITION_MISMATCH
    assert result.status is R.ReplayStatus.REPLAY_FAILED
    assert result.failure_reason is R.ReplayFailureReason.TRANSITION_MISMATCH


def test_a_missing_transition_is_a_mismatch_not_a_silence() -> None:
    request, transitions, authority = scripted_request(["ADD", "SUB", "MUL"])
    result = run_scripted(request, authority, opcodes=["ADD", "SUB"])
    assert_contract_rejected(result)
    assert result.steps_executed == 2 < len(transitions)


def test_an_extra_transition_is_a_mismatch() -> None:
    request, _, authority = scripted_request(["ADD", "SUB"])
    assert_contract_rejected(run_scripted(request, authority, opcodes=["ADD", "SUB", "MUL"]))


def test_a_duplicate_transition_cannot_hide_an_omission() -> None:
    """Equal set, different count. Only the count check sees this one.

    The observed transcript visits A twice and never reaches the third expected
    transition, so its *set* is a subset that happens to equal the expected set
    once deduplicated. A set comparison alone reports a match.
    """

    request, transitions, authority = scripted_request(["ADD", "SUB"])
    first, second = transitions
    result = run_scripted(
        request, authority, opcodes=["ADD", "SUB", "MUL"], hash_script=[first, first, second]
    )
    assert frozenset(result.transition_hash_chain) == frozenset(transitions)
    assert len(result.transition_hash_chain) != len(transitions)
    assert_contract_rejected(result)


def test_a_substituted_transition_is_a_mismatch_and_is_located() -> None:
    request, _, authority = scripted_request(["ADD", "SUB", "MUL"])
    result = run_scripted(request, authority, opcodes=["ADD", "DIV", "MUL"])
    assert_contract_rejected(result)
    assert result.observations[0].first_unexpected_index == 1


def test_a_different_program_hash_is_incompatible_and_runs_nothing() -> None:
    request, _, authority = scripted_request(["ADD"])
    port = ScriptedPort(program="sha256:some-other-program", opcodes=["ADD"])
    result = R.execute_replay(request, machines=(port,), authority=authority)
    assert result.status is R.ReplayStatus.REPLAY_INCOMPATIBLE
    assert result.failure_reason is R.ReplayFailureReason.PROGRAM_HASH_MISMATCH
    assert result.steps_executed == 0
    assert port.channel is None


def test_a_different_host_abi_is_incompatible() -> None:
    request, _, authority = scripted_request(["ADD"])
    port = ScriptedPort(program=request.program_hashes[0], opcodes=["ADD"], host_abi="9.9")
    result = R.execute_replay(request, machines=(port,), authority=authority)
    assert result.status is R.ReplayStatus.REPLAY_INCOMPATIBLE
    assert result.failure_reason is R.ReplayFailureReason.HOST_ABI_MISMATCH


def test_one_machine_is_required_per_admitted_behavior() -> None:
    request, _, authority = pure_request()
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.execute_replay(request, machines=(), authority=authority)
    assert excinfo.value.failure_code is R.ReplayFailureCode.MACHINE_COUNT_MISMATCH


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
    unit_b = unit_with(contract_for(scripted_transitions(["ADD", "SUB"])))
    assert unit_a.content_key.value != unit_b.content_key.value

    request, authority, ordered_units = multi_request((unit_a, unit_b))
    assert request.behavior_content_keys == tuple(
        item.content_key.value for item in ordered_units
    )
    # The admitted set is canonical; the run is not, and that is the point.
    assert tuple(item.ref_id for item in request.knowledge_subject_refs) != tuple(
        content_key_digest(item) for item in request.behavior_content_keys
    ), "the execution order was not made to differ from the canonical order"

    machines = []
    for index, unit in enumerate(ordered_units):
        if unit is unit_a:
            machines.append(pure_adapter())
        else:
            machines.append(
                ScriptedPort(
                    program=request.program_hashes[index],
                    host_abi=request.bindings[index].host_abi_version,
                    opcodes=["ADD", "SUB"],
                )
            )
    result = R.execute_replay(request, machines=tuple(machines), authority=authority)
    assert [item.behavior_content_key for item in result.observations] == [
        item.content_key.value for item in ordered_units
    ], "observations did not follow the execution order the request declared"


def test_a_behavior_cannot_appear_twice_in_one_replay() -> None:
    unit, _binding = pure_behavior()
    subject = R.replay_subject(subject_ref=admitted_subject(unit), unit=unit)
    with pytest.raises(Exception) as excinfo:
        R.create_replay_request(
            admission=WORLD.admission_request(published_core(unit)),
            subjects=(subject, subject),
            compiler=compile_behavior_unit,
            activities=(),
            gas_budget=GAS,
            cognitive_budget=8,
            step_limit=1_000,
            executor_actor=EXECUTOR,
        )
    # The admitted set names the subject once, so a repeated subject is refused
    # before compilation as a subject mismatch rather than after it as a
    # duplicate behavior — an earlier refusal for the same reason.
    assert getattr(excinfo.value, "failure_code", None) is not None


# ---------------------------------------------------------------------------
# Forbidden host capability and gas exhaustion give typed failures
# ---------------------------------------------------------------------------


def recorded_llm_call(
    *,
    prompt: bytes = b"explain",
    sequence: int = 1,
    result: bytes = b"answer",
    disposition=ACT.ActivityDisposition.RECORDED_CONSUMABLE,
    program: str = "sha256:scripted",
):
    return ACT.record_activity(
        kind=ACT.ActivityKind.LLM_CALL,
        inputs=ACT.activity_inputs(prompt=prompt),
        position=ACT.ActivityPosition(
            program_hash=program, instruction_pointer=0, frame_depth=0, sequence=sequence
        ),
        policy_version=POLICY,
        disposition=disposition,
        result=result,
        recorded_at_utc=NOW,
    )


def consuming_step(sequence: int = 1, prompt: bytes = b"explain"):
    def step(port, opcode):
        if opcode == "LLM_EVAL":
            port.channel.resolve(
                kind=ACT.ActivityKind.LLM_CALL,
                inputs=ACT.activity_inputs(prompt=prompt),
                position=ACT.ActivityPosition(
                    program_hash="sha256:scripted", instruction_pointer=0,
                    frame_depth=0, sequence=sequence,
                ),
            )

    return step


def test_a_forbidden_host_capability_is_a_typed_failure() -> None:
    activity = recorded_llm_call(disposition=ACT.ActivityDisposition.FORBIDDEN_IN_REPLAY)
    request, _, authority = scripted_request(["ADD", "LLM_EVAL"], activities=(activity,))
    result = run_scripted(request, authority, opcodes=["ADD", "LLM_EVAL"], on_step=consuming_step())
    assert result.status is R.ReplayStatus.REPLAY_FAILED
    assert result.failure_reason is R.ReplayFailureReason.FORBIDDEN_HOST_CALL


def test_an_activity_requiring_fresh_authority_is_a_typed_failure() -> None:
    activity = recorded_llm_call(disposition=ACT.ActivityDisposition.REQUIRES_FRESH_AUTHORITY)
    request, _, authority = scripted_request(["ADD", "LLM_EVAL"], activities=(activity,))
    result = run_scripted(request, authority, opcodes=["ADD", "LLM_EVAL"], on_step=consuming_step())
    assert result.failure_reason is R.ReplayFailureReason.FORBIDDEN_HOST_CALL


def test_a_forbidden_host_call_fails_before_its_side_effect() -> None:
    """The refusal happens inside resolution, so nothing external is reached."""

    activity = recorded_llm_call(disposition=ACT.ActivityDisposition.FORBIDDEN_IN_REPLAY)
    sealed = ledger(activity)
    channel = R.RecordedActivityChannel(sealed, 4, _seal=R._CHANNEL_SEAL)
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        channel.resolve(
            kind=ACT.ActivityKind.LLM_CALL,
            inputs=ACT.activity_inputs(prompt=b"explain"),
            position=ACT.ActivityPosition(
                program_hash="sha256:scripted", instruction_pointer=0, frame_depth=0, sequence=1
            ),
        )
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.FORBIDDEN_IN_REPLAY
    assert channel.consumed_identities() == ()


def test_gas_exhaustion_is_a_typed_failure() -> None:
    request, _, authority = scripted_request(["ADD", "SUB", "MUL"], gas_budget=2)
    result = run_scripted(request, authority, opcodes=["ADD", "SUB", "MUL"], gas=100)
    assert result.status is R.ReplayStatus.REPLAY_FAILED
    assert result.failure_reason is R.ReplayFailureReason.GAS_EXHAUSTED
    assert result.steps_executed == 2


def test_an_exhausted_cognitive_budget_is_a_typed_failure() -> None:
    """A separate bound from gas: it limits reliance on external results."""

    first = recorded_llm_call(prompt=b"one", sequence=1)
    second = recorded_llm_call(prompt=b"two", sequence=2)

    def step(port, opcode):
        if opcode == "LLM_EVAL":
            index = port.instruction_pointer()
            port.channel.resolve(
                kind=ACT.ActivityKind.LLM_CALL,
                inputs=ACT.activity_inputs(prompt=b"one" if index == 0 else b"two"),
                position=ACT.ActivityPosition(
                    program_hash="sha256:scripted", instruction_pointer=0,
                    frame_depth=0, sequence=1 if index == 0 else 2,
                ),
            )

    request, _, authority = scripted_request(
        ["LLM_EVAL", "LLM_EVAL"], activities=(first, second,), cognitive_budget=1
    )
    result = run_scripted(request, authority, opcodes=["LLM_EVAL", "LLM_EVAL"], on_step=step)
    assert result.failure_reason is R.ReplayFailureReason.COGNITIVE_BUDGET_EXHAUSTED


def test_a_step_limit_is_a_typed_failure() -> None:
    request, _, authority = scripted_request(["ADD", "SUB", "MUL"], step_limit=2)
    result = run_scripted(request, authority, opcodes=["ADD", "SUB", "MUL"])
    assert result.failure_reason is R.ReplayFailureReason.STEP_LIMIT_REACHED


def test_an_unknown_host_call_stops_the_run_before_executing_it() -> None:
    request, _, authority = scripted_request(["ADD", "SUB"])
    result = run_scripted(request, authority, opcodes=["ADD", "NOT_AN_OPCODE"])
    assert result.failure_reason is R.ReplayFailureReason.UNKNOWN_HOST_CALL
    assert result.steps_executed == 1


def test_a_faulting_machine_is_infra_error_not_a_behavior_failure() -> None:
    def explode(port, opcode):
        if opcode == "SUB":
            raise ZeroDivisionError("machine fault")

    request, _, authority = scripted_request(["ADD", "SUB"])
    result = run_scripted(request, authority, opcodes=["ADD", "SUB"], on_step=explode)
    assert result.status is R.ReplayStatus.INFRA_ERROR
    assert result.failure_reason is R.ReplayFailureReason.MACHINE_FAULT


def test_gas_that_increases_is_refused_outright() -> None:
    request, _, authority = scripted_request(["ADD", "SUB"])
    with pytest.raises(R.ReplayViolation) as excinfo:
        run_scripted(request, authority, opcodes=["ADD", "SUB"], gas_after=lambda gas: gas + 5)
    assert excinfo.value.failure_code is R.ReplayFailureCode.GAS_NOT_MONOTONE


# ---------------------------------------------------------------------------
# A recorded activity result is injected without a repeated external call
# ---------------------------------------------------------------------------


def effect_fixture():
    record = golden("llm_effect_v1")
    program = BytecodeProgram.from_dict(golden_file("llm_effect_v1.program.json"))
    records = golden_file("llm_effect_v1.activity_records.json")["records"]
    return record, program, records


def rebuild_recorded_activity(payload: dict) -> ACT.RecordedActivity:
    """Rebuild the golden activity record from its own fields."""

    return ACT.record_activity(
        kind=ACT.ActivityKind(payload["kind"]),
        inputs=ACT.ActivityInputs.from_dict(payload["inputs"]),
        position=ACT.ActivityPosition.from_dict(payload["position"]),
        policy_version=payload["policy_version"],
        disposition=ACT.ActivityDisposition(payload["disposition"]),
        result=b"the recorded model answer",
        recorded_at_utc=datetime.strptime(
            payload["recorded_at_utc"], ACT.UTC_TIMESTAMP_FORMAT
        ).replace(tzinfo=timezone.utc),
    )


def test_the_golden_activity_record_round_trips() -> None:
    record, _, records = effect_fixture()
    rebuilt = rebuild_recorded_activity(records[0])
    assert rebuilt.activity_identity == record["activity_identity"]
    assert rebuilt.idempotency_key == record["activity_idempotency_key"]
    assert rebuilt.to_dict() == records[0]


def test_a_recorded_result_is_injected_without_a_fresh_external_call() -> None:
    """The adapter serves LLM_EVAL from record; no live producer is reachable."""

    record, program, records = effect_fixture()
    activity = rebuild_recorded_activity(records[0])
    sealed = ledger(activity)
    channel = R.RecordedActivityChannel(sealed, 8, _seal=R._CHANNEL_SEAL)
    adapter = R.CognitiveVMReplayAdapter(program, gas_budget=GAS)
    adapter.attach_channel(channel)

    seen = []
    while not adapter.is_halted() and adapter.next_opcode() is not None:
        adapter.step()
        seen.append(adapter.transition_hash())

    assert seen == record["expected_transition_ids"]
    assert channel.consumed_identities() == (record["activity_identity"],)
    assert channel.consumed_keys() == (record["activity_idempotency_key"],)
    assert adapter.snapshot_digest() == record["expected_terminal_snapshot_digest"]


def test_an_unrecorded_activity_stops_the_replay_instead_of_happening_again() -> None:
    _, program, _ = effect_fixture()
    channel = R.RecordedActivityChannel(ledger(), 8, _seal=R._CHANNEL_SEAL)
    adapter = R.CognitiveVMReplayAdapter(program, gas_budget=GAS)
    adapter.attach_channel(channel)
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        for _ in range(10):
            adapter.step()
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.ACTIVITY_NOT_RECORDED


def test_the_adapter_refuses_an_effect_with_no_channel() -> None:
    """No channel means no recorded result, and the machine's stub is not one."""

    _, program, _ = effect_fixture()
    adapter = R.CognitiveVMReplayAdapter(program, gas_budget=GAS)
    with pytest.raises(R.ReplayViolation) as excinfo:
        for _ in range(10):
            adapter.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.CHANNEL_CLOSED


def test_the_adapter_separates_activities_that_differ_in_either_operand() -> None:
    seen: list[str] = []

    class Recorder:
        def resolve(self, *, kind, inputs, position):
            seen.append(
                ACT.compute_activity_identity(
                    kind=kind, inputs=inputs, policy_version=POLICY, position=position
                )
            )
            raise ACT.ActivityViolation(ACT.ActivityFailureCode.ACTIVITY_NOT_RECORDED, "probe")

    _, program, _ = effect_fixture()
    adapter = R.CognitiveVMReplayAdapter(program, gas_budget=GAS)
    adapter._channel = Recorder()
    for a, b in (("x", 1), ("y", 1), ("x", 2)):
        with pytest.raises(ACT.ActivityViolation):
            adapter._host("LLM_EVAL", a, b)
    assert len(set(seen)) == 3


def test_the_adapter_records_the_position_of_the_executing_instruction() -> None:
    """Recorded after dispatch, the position would be the next instruction's."""

    positions: list[int] = []

    class Recorder:
        def resolve(self, *, kind, inputs, position):
            positions.append(position.instruction_pointer)
            raise ACT.ActivityViolation(ACT.ActivityFailureCode.ACTIVITY_NOT_RECORDED, "probe")

    _, program, _ = effect_fixture()
    adapter = R.CognitiveVMReplayAdapter(program, gas_budget=GAS)
    adapter._channel = Recorder()
    while True:
        try:
            adapter.step()
        except ACT.ActivityViolation:
            break
    assert positions == [2], "the effect is the third instruction of the fixture program"


def test_a_replay_consuming_the_wrong_activity_set_fails() -> None:
    """The transitions matched, the effects did not. That is not identity."""

    activity = recorded_llm_call()
    other = recorded_llm_call(prompt=b"a different prompt", sequence=2)
    request, _, authority = scripted_request(
        ["ADD", "LLM_EVAL"],
        activity_ids=(other.activity_identity,),
        activities=(activity, other,),
    )
    result = run_scripted(request, authority, opcodes=["ADD", "LLM_EVAL"], on_step=consuming_step())
    assert result.failure_reason is R.ReplayFailureReason.TRANSITION_MISMATCH


def test_the_channel_closes_when_the_replay_ends() -> None:
    request, _, authority = scripted_request(["ADD"])
    port = ScriptedPort(program=request.program_hashes[0], opcodes=["ADD"])
    R.execute_replay(request, machines=(port,), authority=authority)
    with pytest.raises(R.ReplayViolation) as excinfo:
        port.channel.resolve(
            kind=ACT.ActivityKind.LLM_CALL,
            inputs=ACT.activity_inputs(prompt=b"explain"),
            position=ACT.ActivityPosition(
                program_hash="sha256:scripted", instruction_pointer=0, frame_depth=0, sequence=1
            ),
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.CHANNEL_CLOSED


def test_the_channel_closes_even_when_the_machine_faults() -> None:
    def explode(port, opcode):
        raise RuntimeError("boom")

    request, _, authority = scripted_request(["ADD"])
    port = ScriptedPort(program=request.program_hashes[0], opcodes=["ADD"], on_step=explode)
    R.execute_replay(request, machines=(port,), authority=authority)
    assert not port.channel.is_open


def test_a_channel_cannot_be_built_outside_a_replay() -> None:
    with pytest.raises(TypeError):
        R.RecordedActivityChannel(ledger(), 4)


def test_the_request_pins_the_activity_history_it_will_consume() -> None:
    activity = recorded_llm_call()
    request, _, authority = scripted_request(["ADD", "LLM_EVAL"], activities=(activity,))
    assert request.recorded_activity_refs == (ACT.activity_ref(activity),)
    assert request.activity_idempotency_keys == (activity.idempotency_key,)


# ---------------------------------------------------------------------------
# Snapshot and resume reproduce the same terminal state
# ---------------------------------------------------------------------------


def test_the_golden_vm_snapshot_restores_to_the_recorded_terminal_state() -> None:
    record = golden("pure_add_v1")
    snapshot = golden_file("pure_add_v1.vm_snapshot.json")
    resumed = R.CognitiveVMReplayAdapter.from_snapshot(snapshot, gas_budget=GAS)
    assert resumed.program_hash() == record["program_hash"]
    assert resumed.snapshot_digest() == record["expected_terminal_snapshot_digest"]
    assert resumed.transition_hash() == record["expected_transition_ids"][-1]


def test_a_resumed_replay_reaches_the_same_terminal_state() -> None:
    """Resume accepts the state its predecessor left, and leaves it unchanged.

    The resumed run executes nothing — the snapshot it attaches to is already
    terminal — so it does not, and should not, reproduce the earlier run's
    transcript. What it must reproduce is the terminal state, and that is what
    is asserted. A resumed run whose transcript is empty against a contract
    describing a full run is correctly a failure, not an unearned identity.
    """

    record = golden("pure_add_v1")
    unit, _ = pure_behavior()
    request, _, authority = pure_request(
        expected_terminal_snapshot_digests=(record["expected_terminal_snapshot_digest"],)
    )
    first = R.execute_replay(request, machines=(pure_adapter(),), authority=authority)
    assert first.status is R.ReplayStatus.REPLAY_IDENTICAL

    resumed_machine = R.CognitiveVMReplayAdapter.from_snapshot(
        golden_file("pure_add_v1.vm_snapshot.json"), gas_budget=GAS
    )
    continuation, continuation_authority = resuming_request(
        first,
        unit,
        expected_terminal_snapshot_digests=(record["expected_terminal_snapshot_digest"],),
    )
    again = R.resume_replay(
        continuation, machines=(resumed_machine,), resumed_from=first,
        authority=continuation_authority,
    )
    assert again.terminal_snapshot_digests == first.terminal_snapshot_digests
    assert again.steps_executed == 0
    assert again.status is not R.ReplayStatus.REPLAY_INCOMPATIBLE, (
        "resume verification rejected a state it should have accepted"
    )


def test_resume_refuses_a_machine_in_another_state() -> None:
    record = golden("pure_add_v1")
    unit, _ = pure_behavior()
    request, _, authority = pure_request()
    first = R.execute_replay(request, machines=(pure_adapter(),), authority=authority)
    fresh = pure_adapter()
    assert fresh.snapshot_digest() != record["expected_terminal_snapshot_digest"]
    continuation, continuation_authority = resuming_request(first, unit)
    result = R.resume_replay(
        continuation, machines=(fresh,), resumed_from=first,
        authority=continuation_authority,
    )
    assert result.status is R.ReplayStatus.REPLAY_INCOMPATIBLE
    assert result.failure_reason is R.ReplayFailureReason.SNAPSHOT_INCOMPATIBLE


def test_resume_refuses_a_continuation_across_a_knowledge_snapshot() -> None:
    """Линия проверяется раньше всего остального, и снимок знания — её часть.

    Продолжение, собранное над другой зафиксированной границей, отвергается до
    того, как хоть одна машина будет опрошена: это не исход исполнения, а
    запрос, который никогда не был продолжением этого результата.
    """

    request, _, authority = pure_request()
    first = R.execute_replay(request, machines=(pure_adapter(),), authority=authority)

    other_unit = unit_with(contract_for(scripted_transitions(["ADD"])), literal=99)
    other, other_authority = request_for(
        other_unit,
        activities=(),
        gas_budget=GAS,
        cognitive_budget=8,
        step_limit=1_000,
        executor_actor=EXECUTOR,
        resumed_from_result_ref=R.replay_result_ref(first),
    )
    assert other.knowledge_snapshot_id != first.knowledge_snapshot_id
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.resume_replay(
            other,
            machines=(
                ScriptedPort(
                    program=other.program_hashes[0],
                    host_abi=other.bindings[0].host_abi_version,
                    opcodes=["ADD"],
                ),
            ),
            resumed_from=first,
            authority=other_authority,
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.RESUME_LINEAGE_MISMATCH


def test_resume_refuses_a_continuation_of_another_result() -> None:
    """Пара «запрос + результат» больше не выбирается на месте вызова."""

    unit, _ = pure_behavior()
    request, _, authority = pure_request()
    first = R.execute_replay(request, machines=(pure_adapter(),), authority=authority)
    other_request, _, other_authority = pure_request(cognitive_budget=7)
    second = R.execute_replay(
        other_request, machines=(pure_adapter(),), authority=other_authority
    )
    assert R.replay_result_ref(first) != R.replay_result_ref(second)
    continuation, continuation_authority = resuming_request(first, unit)
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.resume_replay(
            continuation, machines=(pure_adapter(),), resumed_from=second,
            authority=continuation_authority,
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.RESUME_LINEAGE_MISMATCH


def test_resume_refuses_another_program() -> None:
    """§23: the program a continuation runs must be the one it resumes from.

    Two behaviors with genuinely different bytecode, published into one world
    and admitted together, so both requests name the same committed boundary and
    the lineage check — which is checked first, and correctly — has nothing to
    object to. The continuation then runs the same admitted set in the other
    execution order, so its program hashes are the resumed-from hashes reversed:
    the same programs, not in the places that result left them.
    """

    unit_a, _binding_a = pure_behavior()
    unit_b = unit_with(contract_for(scripted_transitions(["ADD"])), literal=99)
    assert compile_behavior_unit(unit_a).actual_program_hash != (
        compile_behavior_unit(unit_b).actual_program_hash
    ), "the two behaviors must compile to different programs for this case to exist"

    primary, extra = world_of(unit_a, unit_b)
    forward = A.canonical_subject_refs(
        tuple(admitted_subject_in(item, primary, extra) for item in (unit_a, unit_b))
    )
    request, authority, ordered = multi_request((unit_a, unit_b), order=forward)
    first = R.execute_replay(
        request, machines=_machines_for(request, ordered, unit_a), authority=authority
    )
    assert len(first.observations) == 2, "both behaviors must have run to their end"

    continuation, continuation_authority, reordered = multi_request(
        (unit_a, unit_b),
        order=tuple(reversed(forward)),
        resumed_from_result_ref=R.replay_result_ref(first),
    )
    assert continuation.program_hashes != request.program_hashes
    result = R.resume_replay(
        continuation,
        machines=_machines_for(continuation, reordered, unit_a),
        resumed_from=first,
        authority=continuation_authority,
    )
    assert result.status is R.ReplayStatus.REPLAY_INCOMPATIBLE
    assert result.failure_reason is R.ReplayFailureReason.PROGRAM_HASH_MISMATCH


def test_resume_refuses_another_activity_history() -> None:
    activity = recorded_llm_call()
    unit, _ = pure_behavior()
    request, _, authority = pure_request()
    first = R.execute_replay(request, machines=(pure_adapter(),), authority=authority)
    with_history, history_authority = resuming_request(first, unit, activities=(activity,))
    result = R.resume_replay(
        with_history, machines=(pure_adapter(),), resumed_from=first,
        authority=history_authority,
    )
    assert result.status is R.ReplayStatus.REPLAY_FAILED
    assert result.failure_reason is R.ReplayFailureReason.ACTIVITY_HISTORY_MISMATCH


def test_a_tampered_terminal_state_is_detected() -> None:
    request, _, authority = pure_request(expected_terminal_snapshot_digests=("f" * 64,))
    result = R.execute_replay(request, machines=(pure_adapter(),), authority=authority)
    assert result.status is R.ReplayStatus.REPLAY_FAILED
    assert result.failure_reason is R.ReplayFailureReason.SNAPSHOT_TAMPERED


def test_a_snapshot_that_is_not_a_machine_snapshot_is_refused() -> None:
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.CognitiveVMReplayAdapter.from_snapshot({"not": "a snapshot"}, gas_budget=GAS)
    assert excinfo.value.failure_code is R.ReplayFailureCode.TYPE_MISMATCH


# ---------------------------------------------------------------------------
# Replay success does not establish FULL and does not replace oracle verification
# ---------------------------------------------------------------------------


#: §26's outcome vocabulary. Replay may not produce any of these, and this
#: stage's NR-14 says why: replay success is not oracle correctness and does not
#: establish FULL. Prose may discuss them; executable code may not name them.
_OUTCOME_VOCABULARY = frozenset(
    {
        "FULL", "VERIFIED_REUSABLE_PARTIAL", "UNRESOLVED", "NO_CANDIDATE",
        "FAIL", "INVALID_CONTRACT", "StructuredOutcome",
        "completeness", "correctness", "task_success", "oracle_verdict",
    }
)


def _executable_names(module) -> set[str]:
    """Every identifier and non-docstring literal in a module's executable code.

    Docstrings are excluded deliberately. A module is allowed — required, even —
    to explain in prose which concepts it must not produce; what it may not do
    is name one in code.
    """

    import ast

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                names.add(node.value)
    return names


def test_replay_cannot_express_full_at_all() -> None:
    """NR-14 for this stage: replay success does not establish FULL.

    The check is structural rather than behavioural, because a *guarded* FULL is
    still a FULL this module could produce, and a guard is one edit away from
    being removed. §26 owns the outcome vocabulary; nothing executable here may
    name a member of it.
    """

    named = _executable_names(R) & _OUTCOME_VOCABULARY
    assert not named, f"replay.py names the outcome concept(s) {sorted(named)}"
    assert not [item for item in dir(R) if "FULL" in item.upper()]
    assert not [item for item in dir(R) if "COMPLETENESS" in item.upper()]


def test_a_result_carries_no_verdict_field() -> None:
    request, _, authority = pure_request()
    result = R.execute_replay(request, machines=(pure_adapter(),), authority=authority)
    fields = set(result.to_dict())
    assert not fields & {
        "outcome", "verdict", "completeness", "correctness", "task_success", "full"
    }
    assert result.status is R.ReplayStatus.REPLAY_IDENTICAL


def test_an_observation_makes_no_claim_about_task_success() -> None:
    """§23: replay observations do not gain instruction or task-success authority."""

    request, _, authority = pure_request()
    result = R.execute_replay(request, machines=(pure_adapter(),), authority=authority)
    payload = result.observations[0].to_dict()
    assert not set(payload) & {
        "correct", "passed", "verdict", "task_success", "oracle", "authority"
    }
    assert set(payload) >= {"transition_hash_chain", "transcript_matched", "failure_reason"}


def test_identity_requires_a_root_pinned_before_the_run() -> None:
    """A sorted-set contract cannot see a permutation; a pinned root can."""

    request, _, authority = pure_request(expected_transcript_root=None)
    result = R.execute_replay(request, machines=(pure_adapter(),), authority=authority)
    assert result.status is R.ReplayStatus.REPLAY_FAILED
    assert result.failure_reason is R.ReplayFailureReason.TRANSITION_MISMATCH
    assert result.observations[0].transcript_matched


def test_a_result_cannot_be_built_by_its_constructor() -> None:
    for factory in (R.BehaviorReplayRequest, R.BehaviorReplayResult, R.ReplayObservation):
        with pytest.raises(TypeError):
            factory()  # type: ignore[call-arg]


def test_rewriting_a_result_field_invalidates_it() -> None:
    request, _, authority = pure_request()
    result = R.execute_replay(request, machines=(pure_adapter(),), authority=authority)
    object.__setattr__(result, "status", R.ReplayStatus.REPLAY_FAILED)
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.validate_replay_result(result)
    assert excinfo.value.failure_code is R.ReplayFailureCode.STATUS_REASON_INCONSISTENT


def test_a_forged_identical_status_over_a_failed_run_is_refused() -> None:
    request, _, authority = scripted_request(["ADD", "SUB"])
    failed = run_scripted(request, authority, opcodes=["ADD", "MUL"])
    object.__setattr__(failed, "status", R.ReplayStatus.REPLAY_IDENTICAL)
    object.__setattr__(failed, "failure_reason", None)
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.validate_replay_result(failed)
    assert excinfo.value.failure_code is R.ReplayFailureCode.STATUS_REASON_INCONSISTENT


def test_a_forged_identical_status_over_an_unpinned_run_is_refused() -> None:
    """Every observation matched, but no root was pinned. Still not identity.

    This is the case the observation check cannot catch — the run was clean —
    so the pinned-root requirement has to be enforced in validation on its own.
    """

    request, _, authority = pure_request(expected_transcript_root=None)
    result = R.execute_replay(request, machines=(pure_adapter(),), authority=authority)
    assert all(item.transcript_matched for item in result.observations)
    object.__setattr__(result, "status", R.ReplayStatus.REPLAY_IDENTICAL)
    object.__setattr__(result, "failure_reason", None)
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.validate_replay_result(result)
    assert excinfo.value.failure_code is R.ReplayFailureCode.STATUS_REASON_INCONSISTENT


def test_a_status_that_its_reason_does_not_produce_is_refused() -> None:
    """The mapping is enforced, not merely consulted.

    A program-hash mismatch is an incompatibility; recording it as a failure
    would misreport whether the behavior or its execution contract was at fault.
    """

    request, _, authority = scripted_request(["ADD"])
    port = ScriptedPort(program="sha256:some-other-program", opcodes=["ADD"])
    result = R.execute_replay(request, machines=(port,), authority=authority)
    assert result.status is R.ReplayStatus.REPLAY_INCOMPATIBLE
    object.__setattr__(result, "status", R.ReplayStatus.REPLAY_FAILED)
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.validate_replay_result(result)
    assert excinfo.value.failure_code is R.ReplayFailureCode.STATUS_REASON_INCONSISTENT


def test_rewriting_the_transcript_invalidates_the_root() -> None:
    request, _, authority = pure_request()
    result = R.execute_replay(request, machines=(pure_adapter(),), authority=authority)
    object.__setattr__(result, "transition_hash_chain", result.transition_hash_chain[:-1])
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.validate_replay_result(result)
    assert excinfo.value.failure_code is R.ReplayFailureCode.IDENTITY_MISMATCH


# ---------------------------------------------------------------------------
# Admission before compilation
# ---------------------------------------------------------------------------


def test_a_subject_the_admission_does_not_name_never_reaches_a_request() -> None:
    """Compiling B while A was admitted is refused before anything is compiled."""

    unit, _binding = pure_behavior()
    stranger = unit_with(contract_for(("a-transition-nobody-admitted",)))
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.create_replay_request(
            admission=WORLD.admission_request(published_core(unit)),
            subjects=(
                R.ReplaySubject(subject_ref=admitted_subject(unit), unit=stranger),
            ),
            compiler=compile_behavior_unit,
            activities=(),
            gas_budget=GAS,
            cognitive_budget=8,
            step_limit=1_000,
            executor_actor=EXECUTOR,
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.IDENTITY_MISMATCH


def test_a_well_named_subject_the_admission_never_covered_is_refused() -> None:
    """Ссылка, честно называющая своё поведение, но не из этого допущения.

    Предыдущий случай подставляет чужое поведение под допущенную ссылку, и его
    ловит сверка «ссылка называет этот юнит». Здесь ссылка называет свой юнит
    честно — она просто принадлежит другому допущению, и отказ обязан прийти
    от сверки допущенного набора, под другим кодом и от другого владельца.
    Разводить эти две проверки нужно явно: они совпадают, пока ссылка и
    допущение приходят из одного мира, а совпадающие проверки нельзя показать
    работающими по отдельности.
    """

    unit, _binding = pure_behavior()
    stranger = unit_with(contract_for(("a-transition-of-another-subject",)))
    stranger_ref = admitted_subject(stranger)
    assert stranger_ref.ref_id == stranger.content_key.digest_sha256
    assert stranger_ref != admitted_subject(unit)

    with pytest.raises(Exception) as excinfo:
        R.create_replay_request(
            admission=WORLD.admission_request(published_core(unit)),
            subjects=(R.replay_subject(subject_ref=stranger_ref, unit=stranger),),
            compiler=compile_behavior_unit,
            activities=(),
            gas_budget=GAS,
            cognitive_budget=8,
            step_limit=1_000,
            executor_actor=EXECUTOR,
        )
    from synapse.experiments.gold import admission as A

    assert isinstance(excinfo.value, A.AdmissionViolation)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.SUBJECT_MISMATCH


def test_a_reference_of_another_kind_cannot_stand_in_for_a_library_subject() -> None:
    """Мутант A8: снята проверка схемы ссылки субъекта.

    Сверка ``ref_id`` ловит чужое поведение, но не чужой *вид* объекта: запись
    активности, артефакт или граница могут нести тот же идентификатор и совсем
    другое значение. Схема — отдельное утверждение, и убрать её было незаметно,
    пока ни один случай не приносил ссылку правильного идентификатора и
    неправильного рода.
    """

    unit, _binding = pure_behavior()
    genuine = admitted_subject(unit)
    impostor = HashBoundRef(
        kind=genuine.kind,
        ref_id=genuine.ref_id,
        schema_id=SchemaVersion.RECORDED_ACTIVITY_V1.value,
        sha256=genuine.sha256,
        byte_length=genuine.byte_length,
        media_type=genuine.media_type,
    )
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.replay_subject(subject_ref=impostor, unit=unit)
    assert excinfo.value.failure_code is R.ReplayFailureCode.TYPE_MISMATCH


def test_a_consistently_forged_record_still_fails_the_snapshot_agreement() -> None:
    """Мутант A10: снята сверка снимка с зафиксированной границей.

    Первая попытка этой приёмки просто переписывала поле и требовала отказа —
    и проходила даже без проверки, потому что переписанный payload расходится
    с ``replay_id`` и отвергается по идентичности под тем же кодом. Тест ничего
    не доказывал.

    Здесь подделка *согласованная*: идентичность пересчитана под переписанный
    payload, как её пересчитает любой путь восстановления записи из внешнего
    представления. Такую запись сверка идентичности пропускает, и остаётся ровно
    одно, что её отвергает, — требование, чтобы снимок был той самой границей,
    против которой запись допущена.
    """

    from synapse.experiments.gold.contracts import IdentityDomain, compute_record_id

    request, _, authority = pure_request()
    R.validate_replay_request(request)
    original_snapshot = request.knowledge_snapshot_id
    original_id = request.replay_id
    object.__setattr__(request, "knowledge_snapshot_id", "snapshot-someone-preferred")
    object.__setattr__(
        request,
        "replay_id",
        compute_record_id(
            domain=IdentityDomain.BEHAVIOR_REPLAY_REQUEST,
            canonical_bytes=R._canonical(R._request_payload(request)),
        ),
    )
    try:
        with pytest.raises(R.ReplayViolation) as excinfo:
            R.validate_replay_request(request)
        assert excinfo.value.failure_code is R.ReplayFailureCode.IDENTITY_MISMATCH
        assert "manifest" in str(excinfo.value)
    finally:
        object.__setattr__(request, "knowledge_snapshot_id", original_snapshot)
        object.__setattr__(request, "replay_id", original_id)


def test_a_rewritten_knowledge_set_detaches_the_ledger() -> None:
    """Мутант A11: журнал перестал сверяться с допущенным набором знания.

    Внутри фабрики журнал запечатывается тем же допущением, поэтому расхождение
    возможно только у переписанной записи — и именно её валидатор обязан
    отвергнуть, иначе журнал одного прогона молча описывает другой.
    """

    request, _, authority = pure_request()
    original = request.knowledge_subject_refs
    object.__setattr__(
        request, "knowledge_subject_refs", (ref(RefKind.ARTIFACT, "another-subject"),)
    )
    try:
        with pytest.raises(ACT.ActivityViolation) as excinfo:
            R.validate_replay_request(request)
        assert excinfo.value.failure_code is ACT.ActivityFailureCode.LEDGER_NOT_BOUND
    finally:
        object.__setattr__(request, "knowledge_subject_refs", original)


def _reseal(request):
    """Recompute ``replay_id`` over the rewritten payload, as restoration would.

    A forgery that leaves the identity behind is refused by the identity check
    and proves nothing about the rule under test. Every case below therefore
    forges *consistently*: the record is exactly what a path restoring it from an
    external representation would produce.
    """

    from synapse.experiments.gold.contracts import IdentityDomain, compute_record_id

    object.__setattr__(
        request,
        "replay_id",
        compute_record_id(
            domain=IdentityDomain.BEHAVIOR_REPLAY_REQUEST,
            canonical_bytes=R._canonical(R._request_payload(request)),
        ),
    )
    return request


def test_mutant_a_stale_admission_still_replays_is_killed() -> None:
    """Mutant B1: ``execute_replay`` stops re-checking the admission.

    This is the audit finding of round A stated as a test. The request is built
    under an admission that holds; the world then moves on, exactly as it does
    whenever another point-of-use attempt is admitted; and the same request is
    handed to the same machines. Before the fix this reached
    ``REPLAY_IDENTICAL`` — a run whose authority the system had already
    classified as stale. §22 puts the consumption decision immediately before
    replay, so a request that outlived its admission must not execute at all.
    """

    unit, _binding = pure_behavior()
    request, _, authority = pure_request()
    R.validate_replay_request(request)

    # The world moves: a second attempt is admitted over the same subject.
    WORLD.admit(WORLD.admission_request(published_core(unit)))

    with pytest.raises(R.ReplayViolation) as excinfo:
        R.execute_replay(request, machines=(pure_adapter(),), authority=authority)
    assert excinfo.value.failure_code is R.ReplayFailureCode.ADMISSION_NOT_CURRENT


def test_a_forged_authority_is_not_reported_as_a_stale_admission() -> None:
    """NR-10: a forged binding and a stale admission are different facts.

    The first revision of the point-of-use re-check caught every exception and
    reported all of them as ``ADMISSION_NOT_CURRENT``. That reads as caution and
    is the opposite: an object that was never a production binding, a store that
    cannot be reached and an admission the world has moved past would all arrive
    at the caller wearing the mildest of the three labels, and §2 names that
    status relabelling outright.
    """

    from synapse.experiments.gold.admission import AdmissionFailureCode, AdmissionViolation

    request, _, _authority = pure_request()

    class NotABinding:
        def open_current_snapshot(self):  # pragma: no cover - must never be reached
            raise AssertionError("a forged binding was asked for the current snapshot")

    with pytest.raises(AdmissionViolation) as excinfo:
        R.execute_replay(
            request, machines=(pure_adapter(),), authority=NotABinding()
        )
    assert excinfo.value.failure_code is AdmissionFailureCode.TRUSTED_OBJECT_FORGED


def test_mutant_a_binding_for_an_unadmitted_behavior_is_accepted_is_killed() -> None:
    """Mutant B2: the validator stops tying compiled programs to admitted refs.

    The factory ties each subject reference to the unit it names, but a factory
    check protects only objects that went through the factory. A restored or
    mutated request can carry the admitted references of one behavior beside the
    compiled binding of another, and until this comparison existed nothing
    downstream would notice: the machine would run a program no gate ever saw,
    under an admission that names something else.
    """

    request, _, authority = pure_request()
    R.validate_replay_request(request)
    stranger = unit_with(contract_for(scripted_transitions(["ADD"])), literal=77)
    assert stranger.content_key.digest_sha256 not in {
        item.ref_id for item in request.knowledge_subject_refs
    }
    original = request.bindings
    object.__setattr__(
        request,
        "bindings",
        (R.replay_program_binding(unit=stranger, binding=compile_behavior_unit(stranger)),),
    )
    _reseal(request)
    try:
        with pytest.raises(R.ReplayViolation) as excinfo:
            R.validate_replay_request(request)
        assert excinfo.value.failure_code is R.ReplayFailureCode.SUBJECT_NOT_ADMITTED
    finally:
        object.__setattr__(request, "bindings", original)
        _reseal(request)


def test_mutant_a_forged_admission_identity_is_accepted_is_killed() -> None:
    """Mutant B3: the admission identities go back to being unresolved fields.

    ``admitted_knowledge_id`` and ``consumption_decision_id`` were checked only
    for being record identities, so a consistently forged request could name any
    admission and any decision it liked. They are now resolved against the
    admission object the request carries, which nothing outside
    ``admit_for_use_now`` can mint.
    """

    from synapse.experiments.gold.contracts import IdentityDomain, compute_record_id

    request, _, authority = pure_request()
    R.validate_replay_request(request)
    impostor = compute_record_id(
        domain=IdentityDomain.BEHAVIOR_REPLAY_REQUEST, canonical_bytes=b"another-admission"
    )
    for field_name in ("admitted_knowledge_id", "consumption_decision_id"):
        original = getattr(request, field_name)
        object.__setattr__(request, field_name, impostor)
        _reseal(request)
        try:
            with pytest.raises(R.ReplayViolation) as excinfo:
                R.validate_replay_request(request)
            assert excinfo.value.failure_code is R.ReplayFailureCode.IDENTITY_MISMATCH
        finally:
            object.__setattr__(request, field_name, original)
            _reseal(request)


def test_mutant_a_ledger_from_another_admission_is_accepted_is_killed() -> None:
    """Mutant B5: the ledger stops being tied to *this* request's admission.

    Two ledgers sealed in the same world agree on everything ``require_bound_to``
    can see — consumer context, boundary, admitted subject set, policy version —
    because those describe the world, not the moment. What separates them is
    which revalidation admitted them, and a request that accepted either one
    would let an activity set sealed under an earlier admission travel into a
    run admitted by a later one.
    """

    request, _, authority = pure_request()
    R.validate_replay_request(request)
    unit, _binding = pure_behavior()
    elsewhere = ACT.seal_activity_ledger(
        activities=(), admitted=WORLD.admitted_knowledge(published_core(unit))
    )
    assert elsewhere.activity_refs() == request.recorded_activity_refs
    assert (
        elsewhere.admitted_knowledge_id.digest_sha256
        != request.ledger.admitted_knowledge_id.digest_sha256
    ), "the two ledgers must rest on different admissions for this case to exist"

    original = request.ledger
    object.__setattr__(request, "ledger", elsewhere)
    _reseal(request)
    try:
        with pytest.raises(R.ReplayViolation) as excinfo:
            R.validate_replay_request(request)
        assert excinfo.value.failure_code is R.ReplayFailureCode.LEDGER_NOT_BOUND
    finally:
        object.__setattr__(request, "ledger", original)
        _reseal(request)


def test_mutant_the_snapshot_is_the_boundary_again_is_killed() -> None:
    """Mutant B4: ``knowledge_snapshot_id`` goes back to being the boundary id.

    §21 gives the selected knowledge state and the transaction that publishes it
    separate identities. A request that names the boundary twice has not said
    which snapshot it read, and the field that was supposed to say so becomes
    decoration.
    """

    request, _, authority = pure_request()
    R.validate_replay_request(request)
    assert request.knowledge_snapshot_id != request.boundary_ref.ref_id
    original_id = request.knowledge_snapshot_id
    original_ref = request.snapshot_manifest_ref
    object.__setattr__(request, "knowledge_snapshot_id", request.boundary_ref.ref_id)
    object.__setattr__(request, "snapshot_manifest_ref", request.boundary_ref)
    _reseal(request)
    try:
        with pytest.raises(R.ReplayViolation) as excinfo:
            R.validate_replay_request(request)
        assert excinfo.value.failure_code is R.ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH
    finally:
        object.__setattr__(request, "knowledge_snapshot_id", original_id)
        object.__setattr__(request, "snapshot_manifest_ref", original_ref)
        _reseal(request)


def test_a_request_that_declares_no_predecessor_cannot_be_resumed() -> None:
    """Мутант A12: снята проверка «продолжение обязано назвать предшественника».

    Каждый случай выше подавал продолжающий запрос, поэтому ветка отсутствующей
    линии не исполнялась ни разу. Обычный запрос — не продолжение, и попытка
    возобновить его отвергается типизированно, а не падает по дороге.
    """

    request, _, authority = pure_request()
    assert request.resumed_from_result_ref is None
    first = R.execute_replay(request, machines=(pure_adapter(),), authority=authority)
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.resume_replay(request, machines=(pure_adapter(),), resumed_from=first, authority=authority)
    assert excinfo.value.failure_code is R.ReplayFailureCode.RESUME_LINEAGE_MISMATCH


def test_the_request_reads_its_authority_off_the_admission_not_the_caller() -> None:
    """There is nothing left for a caller to assert about its own entitlement."""

    import inspect

    parameters = set(inspect.signature(R.create_replay_request).parameters)
    for name in (
        "knowledge_snapshot_id",
        "consumption_decision",
        "knowledge_subject_refs",
        "consumer_context_ref",
        "boundary_ref",
        "policy_version",
    ):
        assert name not in parameters, (
            f"{name} is a caller assertion about authority; it belongs to the admission"
        )
    request, _, authority = pure_request()
    assert request.knowledge_snapshot_id == request.snapshot_manifest_ref.ref_id
    assert request.policy_version == POLICY


def test_the_barrier_is_crossed_before_anything_is_compiled() -> None:
    """The order is a sequence this function performs, not a flag a caller sets."""

    unit, _binding = pure_behavior()
    order: list[str] = []

    def watching_compiler(value):
        order.append("compile")
        return compile_behavior_unit(value)

    class WatchingAdmission:
        """A request whose parts are read when ``admit_for_use_now`` is entered."""

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            if name in {"handle", "binding", "chain", "evidence", "entitlements", "requested"}:
                if "admit" not in order:
                    order.append("admit")
            return getattr(self._inner, name)

    inner = WORLD.admission_request(published_core(unit))
    watching = WatchingAdmission(inner)
    # The sealed-request check refuses a wrapper, which is itself the point: the
    # barrier's inputs cannot be substituted. So the ordering is observed through
    # the compiler alone, against the real request.
    with pytest.raises(Exception):
        R.create_replay_request(
            admission=watching,
            subjects=(R.replay_subject(subject_ref=admitted_subject(unit), unit=unit),),
            compiler=watching_compiler,
            activities=(),
            gas_budget=GAS,
            cognitive_budget=8,
            step_limit=1_000,
            executor_actor=EXECUTOR,
        )
    assert order == [], "compilation started before the barrier was crossed"

    R.create_replay_request(
        admission=inner,
        subjects=(R.replay_subject(subject_ref=admitted_subject(unit), unit=unit),),
        compiler=watching_compiler,
        activities=(),
        gas_budget=GAS,
        cognitive_budget=8,
        step_limit=1_000,
        executor_actor=EXECUTOR,
    )
    assert order == ["compile"]


def test_a_ledger_is_sealed_by_the_request_against_its_own_admission() -> None:
    """A ledger cannot be sealed elsewhere and carried in.

    One point-of-use attempt admits exactly once, so a request and a
    separately-sealed ledger could never share an admission. The request seals
    its own, and there is no parameter through which another one could arrive.
    """

    import inspect

    assert "ledger" not in inspect.signature(R.create_replay_request).parameters
    request, _, authority = pure_request()
    knowledge_id = request.ledger.admitted_knowledge_id
    assert knowledge_id == request.admitted_knowledge_id
    assert request.ledger.knowledge_subject_refs == request.knowledge_subject_refs


def test_a_ledger_from_another_policy_version_never_reaches_a_request() -> None:
    other = ACT.record_activity(
        kind=ACT.ActivityKind.LLM_CALL,
        inputs=ACT.activity_inputs(prompt=b"explain the bug"),
        position=ACT.ActivityPosition(
            program_hash="sha256:program-a", instruction_pointer=7, frame_depth=0, sequence=0
        ),
        policy_version="policy-v2",
        disposition=ACT.ActivityDisposition.RECORDED_CONSUMABLE,
        result=b"answer",
        recorded_at_utc=NOW,
    )
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        pure_request(activities=(other,))
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.POLICY_VERSION_MISMATCH


def test_a_binding_from_another_unit_is_refused() -> None:
    from synapse.experiments.gold.canonicalization import (
        CanonicalizationFailureCode,
        CanonicalizationViolation,
    )

    _unit, binding = pure_behavior()
    other_unit = unit_with(contract_for(("some-other-transition",)))
    with pytest.raises(CanonicalizationViolation) as excinfo:
        # A compiler that hands back the binding of a different unit. Injecting
        # the compiler buys nothing, because its output is revalidated against
        # the unit it was asked about.
        request_for(
            other_unit,
            compiler=lambda _value: binding,
            activities=(),
            gas_budget=GAS,
            cognitive_budget=8,
            step_limit=1_000,
            executor_actor=EXECUTOR,
        )
    assert excinfo.value.failure_code is CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH


def test_a_request_does_not_accept_its_own_replay_contract() -> None:
    import inspect

    assert "replay_contract" not in inspect.signature(R.create_replay_request).parameters


# ---------------------------------------------------------------------------
# Mandatory mutation killers
# ---------------------------------------------------------------------------


def test_mutant_replay_reinvokes_an_external_activity_is_killed() -> None:
    """Mutant: a ledger miss falls through to a live call.

    Three barriers are checked: the adapter has no producer to reach when no
    channel is attached, the channel raises on a miss, and the driver turns
    that into a stopped run with a typed reason rather than a step.
    """

    _, program, _ = effect_fixture()
    adapter = R.CognitiveVMReplayAdapter(program, gas_budget=GAS)
    with pytest.raises(R.ReplayViolation):
        for _ in range(10):
            adapter.step()

    channel = R.RecordedActivityChannel(ledger(), 8, _seal=R._CHANNEL_SEAL)
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        channel.resolve(
            kind=ACT.ActivityKind.LLM_CALL,
            inputs=ACT.activity_inputs(prompt=b"nobody recorded this"),
            position=ACT.ActivityPosition(
                program_hash="sha256:scripted", instruction_pointer=0, frame_depth=0, sequence=1
            ),
        )
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.ACTIVITY_NOT_RECORDED

    request, _, authority = scripted_request(["ADD", "LLM_EVAL"])
    result = run_scripted(request, authority, opcodes=["ADD", "LLM_EVAL"], on_step=consuming_step())
    assert result.status is R.ReplayStatus.REPLAY_FAILED
    assert result.failure_reason is R.ReplayFailureReason.MISSING_ACTIVITY_RECORD
    assert result.steps_executed == 1


def test_mutant_a_different_program_hash_is_accepted_is_killed() -> None:
    """Mutant: the execution-contract check before the first transition is dropped."""

    request, _, authority = scripted_request(["ADD"])
    port = ScriptedPort(program="sha256:some-other-program", opcodes=["ADD"])
    result = R.execute_replay(request, machines=(port,), authority=authority)
    assert result.status is R.ReplayStatus.REPLAY_INCOMPATIBLE
    assert result.failure_reason is R.ReplayFailureReason.PROGRAM_HASH_MISMATCH
    assert result.steps_executed == 0
    assert port.channel is None, "the channel opened before the program was verified"


def test_mutant_a_missing_transition_is_ignored_is_killed() -> None:
    """Mutant: the comparison checks the set but not the count, or vice versa.

    Three defects are exercised, because the two halves of the comparison fail
    independently. A substituted transition keeps the count, so a count-only
    check passes it. A duplicate transition standing in for a missing one keeps
    the deduplicated set, so a set-only check passes that. And a mismatch that
    is detected but not turned into a stopping reason is a third way to ignore
    the same thing.

    All three are asserted at the observation. The result's reason is also
    reachable from the pinned-root comparison, so a result-level assertion would
    survive a contract comparison that had been removed entirely.
    """

    short_request, _, short_authority = scripted_request(["ADD", "SUB", "MUL"])
    assert_contract_rejected(
        run_scripted(short_request, short_authority, opcodes=["ADD", "SUB"])
    )

    swapped_request, _, swapped_authority = scripted_request(["ADD", "SUB", "MUL"])
    assert_contract_rejected(
        run_scripted(swapped_request, swapped_authority, opcodes=["ADD", "DIV", "MUL"])
    )

    duplicate_request, transitions, duplicate_authority = scripted_request(["ADD", "SUB"])
    first, second = transitions
    assert_contract_rejected(
        run_scripted(
            duplicate_request, duplicate_authority, opcodes=["ADD", "SUB", "MUL"],
            hash_script=[first, first, second],
        )
    )


def test_mutant_the_result_sets_full_by_itself_is_killed() -> None:
    """Mutant: the replay result asserts an outcome verdict of its own.

    Four checks. The module names no member of the §26 vocabulary; the result
    exposes no verdict field; the status vocabulary contains only the four §23
    members; and a status forged onto a failed run does not survive validation.
    """

    assert not _executable_names(R) & _OUTCOME_VOCABULARY

    request, _, authority = pure_request()
    result = R.execute_replay(request, machines=(pure_adapter(),), authority=authority)
    assert not set(result.to_dict()) & {"outcome", "verdict", "completeness", "full"}
    assert {item.value for item in R.ReplayStatus} == {
        "REPLAY_IDENTICAL", "REPLAY_INCOMPATIBLE", "REPLAY_FAILED", "INFRA_ERROR"
    }

    # Forged onto a run that failed, and onto a clean run whose root was never
    # pinned. Two barriers, and each has to hold on its own.
    scripted, _transitions, scripted_authority = scripted_request(["ADD", "SUB"])
    unpinned, _binding, unpinned_authority = pure_request(expected_transcript_root=None)
    for forged in (
        run_scripted(scripted, scripted_authority, opcodes=["ADD", "MUL"]),
        R.execute_replay(
            unpinned, machines=(pure_adapter(),), authority=unpinned_authority
        ),
    ):
        object.__setattr__(forged, "status", R.ReplayStatus.REPLAY_IDENTICAL)
        object.__setattr__(forged, "failure_reason", None)
        with pytest.raises(R.ReplayViolation) as excinfo:
            R.validate_replay_result(forged)
        assert excinfo.value.failure_code is R.ReplayFailureCode.STATUS_REASON_INCONSISTENT


def test_mutant_the_consumption_gate_is_skipped_before_replay_is_killed() -> None:
    """Mutant: ``create_replay_request`` stops calling the §22 barrier first."""

    import ast
    import inspect

    tree = ast.parse(inspect.getsource(R.create_replay_request))
    called = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "admit_for_use_now" in called
    assert called.index("admit_for_use_now") < called.index("seal_activity_ledger")
    assert called.index("admit_for_use_now") < called.index(
        "replay_program_binding"
    ), "the barrier must be crossed before any program binding is resolved"
