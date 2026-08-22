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
from types import SimpleNamespace

import pytest

from synapse.bytecode import BytecodeProgram
from synapse.cvm import GAS_COSTS
from synapse.experiments.gold import activities as ACT
from synapse.experiments.gold import admission as A
from synapse.experiments.gold import replay as R
from synapse.experiments.gold import replay_store as R_STORE
from synapse.experiments.gold.activity_store import activity_result_ref as ACTIVITY_RESULT_REF
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
    AttemptId,
    AuthorityIdentity,
    RepositoryRevision,
    RunId,
    SchemaVersion,
)
from tests import gold_point_of_use_world as WORLD

NOW = datetime(2026, 7, 31, 9, 0, 0, tzinfo=timezone.utc)
POLICY = "policy-v1"
EXECUTOR = ActorIdentity(value="replay-executor")
GAS = 10_000
#: The bytes a recorded LLM result actually is, under the activity result codec.
R_RESULT = R.encode_recorded_result("answer")
#: The bytes the golden effect fixture records.
GOLDEN_EFFECT_RESULT = R.encode_recorded_result("the recorded model answer")

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


def channel_for(*activities: ACT.RecordedActivity, core=None, budget: int = 4):
    """A channel over a sealed ledger and the store its results really live in.

    Built through the private seal because the cases below are about the channel
    itself; a channel is otherwise opened only by a governed run, which is the
    property the tripwire checks and this helper does not weaken — it reaches the
    private constructor from the acceptance layer, where reaching it is allowed.
    """

    bundle = policy_bundle(core, ())
    from synapse.experiments.gold.persistence import store_transaction

    if activities:
        with store_transaction(bundle.fence) as ticket:
            for item in activities:
                if item.result_sha256 in _RESULT_BYTES:
                    bundle.activity_store.put_result(
                        _RESULT_BYTES[item.result_sha256], ticket=ticket
                    )
    return R.RecordedActivityChannel(
        ledger(*activities, core=core), budget, bundle.activity_store, _seal=R._CHANNEL_SEAL
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


#: The OD-10 authority and the two Stage 9 stores, one bundle per world.
#:
#: Built here rather than in the world helper because they are Stage 9 objects:
#: the world owns §21 and §22, and an activity policy evaluator that lived there
#: would be the world answering a question §22 has no vocabulary for.
_POLICY_BUNDLES: dict = {}
_DECISIONS: dict = {}

EVALUATOR_IDENTITY = AuthorityIdentity("stage9-activity-policy-evaluator")

#: Result bytes by digest, so a prepared run can publish what its records name.
_RESULT_BYTES: dict = {}
_POLICY_DISPOSITIONS: dict[str, ACT.ActivityDisposition] = {}


def policy_bundle(core=None, extra=(), *, dispositions=None):
    from synapse.experiments.gold import activity_policy as AP
    from synapse.experiments.gold.activity_policy_store import FileActivityPolicyStore
    from synapse.experiments.gold.activity_store import FileActivityStore
    from synapse.experiments.gold.replay_store import FileReplayStore

    declared = {
        kind: item
        for kind, item in (dispositions or {}).items()
        if item is not ACT.ActivityDisposition.RECORDED_CONSUMABLE
    }
    # Only the *departures* from the default policy identify a bundle. A run
    # whose activities are all ordinarily consumable must land in the same
    # evaluator and the same stores as one with no activities at all, or a
    # continuation would look for its predecessor in a store that never saw it.
    key = (
        WORLD._core_key(core, extra),
        tuple(sorted((kind.value, item.value) for kind, item in declared.items())),
    )
    if key in _POLICY_BUNDLES:
        return _POLICY_BUNDLES[key]

    handle = WORLD.authority_handle(core, extra)
    mapping = {
        kind: declared.get(kind, ACT.ActivityDisposition.RECORDED_CONSUMABLE)
        for kind in ACT.ActivityKind
    }
    declaration = AP.create_activity_policy_declaration(
        authority_handle=handle,
        evaluator_identity=EVALUATOR_IDENTITY,
        evaluator_component_id="stage9-activity-policy",
        evaluator_component_version="synapse.stage4.activity-policy/v1",
        policy_version=POLICY,
        dispositions=mapping,
        trusted_clock=lambda: NOW,
    )
    actor_set = AP.create_activity_policy_actor_set(
        authority_handle=handle,
        producer_actor=ActorIdentity("stage9-activity-producer"),
        recorder_actor=ActorIdentity("stage9-activity-recorder"),
        worker_actor=ActorIdentity("stage9-worker"),
        model_actor=ActorIdentity("stage9-model"),
        replay_executor_actor=EXECUTOR,
        machine_adapter_actor=ActorIdentity("stage9-machine-adapter"),
        consumer_actor=ActorIdentity("stage9-consumer"),
    )
    proof = AP.create_activity_policy_independence_proof(
        declaration=declaration, actor_set=actor_set
    )
    clock_tick = {"value": 0}

    def policy_clock():
        clock_tick["value"] += 1
        return NOW.replace(microsecond=clock_tick["value"])

    evaluator = AP.configure_activity_policy_evaluator(
        declaration=declaration,
        actor_set=actor_set,
        independence_proof=proof,
        lifecycle_store=WORLD.lifecycle_store(core, extra),
        taint_store=WORLD.taint_store(core, extra),
        trusted_clock=policy_clock,
    )
    root = WORLD.stores_root(core, extra) / ("policy-" + str(len(_POLICY_BUNDLES)))
    root.mkdir(parents=True, exist_ok=True)
    fence = WORLD.coordinator_fence(core, extra)
    bundle = SimpleNamespace(
        declaration=declaration,
        actor_set=actor_set,
        proof=proof,
        evaluator=evaluator,
        activity_store=FileActivityStore(root / "activities", mutation_fence=fence),
        activity_policy_store=FileActivityPolicyStore(root / "policy-decisions", mutation_fence=fence),
        replay_store=FileReplayStore(root / "replays", mutation_fence=fence),
        fence=fence,
        core=core,
        extra=extra,
    )
    _POLICY_BUNDLES[key] = bundle
    return bundle


#: The execution identity every world in this suite runs under. Constant on
#: purpose: the production case builds one run and one attempt regardless of
#: which behavior it publishes, so a record can be minted before its world is
#: known — which is what breaks the otherwise circular dependency between an
#: activity, the replay contract naming it, the behavior carrying that contract
#: and the world publishing that behavior.
RECORD_CONTEXT = ACT.ActivityRecordContext(
    run_id=RunId("point-of-use-run"),
    attempt_id=AttemptId("point-of-use-attempt"),
    repository_revision=RepositoryRevision.git_commit("a" * 40),
    environment_profile_id="production-point-of-use",
    producer_component="stage9-activity-recorder",
)


def governed_activity(
    *,
    kind=None,
    inputs=None,
    position=None,
    result: bytes = b"",
    policy_version: str = POLICY,
    policy_disposition=ACT.ActivityDisposition.RECORDED_CONSUMABLE,
) -> ACT.RecordedActivity:
    """One recorded activity, complete with the reference its bytes live behind.

    The bytes are not stored here. Which store they belong in is a property of
    the *run*, not of the record, so the prepared run puts them where it will
    look for them — and a case that wants to prove a missing blob simply does not
    ask it to.
    """

    from synapse.experiments.gold.activity_store import activity_result_ref

    record = ACT.record_activity(
        kind=kind or ACT.ActivityKind.LLM_CALL,
        inputs=inputs,
        position=position,
        policy_version=policy_version,
        result=result,
        result_ref=activity_result_ref(result),
        context=RECORD_CONTEXT,
        recorded_at_utc=NOW,
    )
    _RESULT_BYTES[record.result_sha256] = result
    _POLICY_DISPOSITIONS[record.activity_identity] = policy_disposition
    return record


def persist_activities(bundle, activities, *, store_results=True, store_records=True):
    """Publish exact blobs and records as fixture setup, under the shared fence."""

    from synapse.experiments.gold.persistence import store_transaction

    existing = {item.activity_identity for item in bundle.activity_store.recorded_activities()}
    if not activities:
        return
    with store_transaction(bundle.fence) as ticket:
        for item in activities:
            raw = _RESULT_BYTES.get(item.result_sha256)
            if store_results and raw is not None:
                bundle.activity_store.put_result(raw, ticket=ticket)
            if store_records and item.activity_identity not in existing:
                bundle.activity_store.append_record(item, ticket=ticket)
                existing.add(item.activity_identity)


class Prepared:
    """The inputs one governed run needs, before the barrier is crossed.

    A prepared run is **not** authority to replay. It carries one point-of-use
    attempt, and an attempt admits exactly once, so ``run``, ``resume`` and
    ``request`` are alternatives rather than a sequence: whichever is called
    crosses the barrier and consumes it. There is deliberately no way to spell
    "build the request, then run it later" — that interval is the defect the
    production path was repaired to remove, and an acceptance layer that could
    still express it would go on rehearsing it.
    """

    def __init__(self, admission, subjects, compiler, arguments, units, core=None, extra=()):
        self.admission = admission
        self.subjects = subjects
        self.compiler = compiler
        self.activities = arguments.pop("activities", ())
        self.arguments = arguments
        self.units = units
        self.core = core
        self.extra = extra
        self._bundle = None

    @property
    def bundle(self):
        if self._bundle is None:
            dispositions = {
                item.kind: _POLICY_DISPOSITIONS.get(
                    item.activity_identity, ACT.ActivityDisposition.RECORDED_CONSUMABLE
                )
                for item in self.activities
            }
            self._bundle = policy_bundle(self.core, self.extra, dispositions=dispositions)
        return self._bundle

    def _governed(
        self, *, store_results: bool = True, store_records: bool = True
    ) -> dict:
        bundle = self.bundle
        persist_activities(
            bundle,
            self.activities,
            store_results=store_results,
            store_records=store_records,
        )
        final_admission = WORLD.admission_request(self.core, self.extra)
        replay_binding = R.create_production_replay_binding(
            authority=final_admission.binding,
            initial_admission=self.admission,
            final_admission=final_admission,
            activity_policy_evaluator=bundle.evaluator,
            activity_store=bundle.activity_store,
            activity_policy_store=bundle.activity_policy_store,
            replay_store=bundle.replay_store,
            executor_actor=EXECUTOR,
        )
        self._last_binding = replay_binding
        return {
            "binding": replay_binding,
            "activity_refs": tuple(ACT.activity_ref(item) for item in self.activities),
        }

    @property
    def authority(self):
        return self.admission.binding

    @property
    def program_hash(self) -> str:
        return compile_behavior_unit(self.units[0]).actual_program_hash

    @property
    def host_abi(self) -> str:
        return compile_behavior_unit(self.units[0]).host_abi_version

    def run(self, machines):
        return R.run_governed_replay(
            admission=self.admission,
            subjects=self.subjects,
            compiler=self.compiler,
            machines=tuple(machines),
            **self._governed(),
            **self.arguments,
        )

    def resume(self, machines, *, resumed_from):
        governed = self._governed()
        return R.resume_governed_replay(
            admission=self.admission,
            subjects=self.subjects,
            compiler=self.compiler,
            machines=tuple(machines),
            resumed_from_result_ref=R.replay_result_ref(resumed_from),
            binding=governed["binding"],
            **self.arguments,
        )

    def request(self):
        """The request this run would produce, for cases whose subject is the record.

        Reaches the private constructor on purpose. A validator, an identity or a
        consistently forged record is a property of the *record*, and there is no
        public way to obtain one without also running it — which is the point of
        the repair. The acceptance layer may look at the record; nothing in
        production may.
        """

        governed = self._governed()
        prepared = R._prepare_replay(
            admission=self.admission,
            binding=governed["binding"],
            subjects=self.subjects,
            compiler=self.compiler,
            activity_refs=governed["activity_refs"],
        )
        decisions = R._evaluate_governed_activities(
            prepared, binding=governed["binding"]
        )
        from synapse.experiments.gold.activity_policy import activity_policy_decision_ref

        return R._create_replay_request(
            prepared=prepared,
            decision_refs=tuple(activity_policy_decision_ref(item) for item in decisions),
            executor_actor=EXECUTOR,
            **self.arguments,
        )


def _defaults(arguments: dict) -> dict:
    arguments.setdefault("activities", ())
    arguments.setdefault("gas_budget", GAS)
    arguments.setdefault("cognitive_budget", 8)
    arguments.setdefault("step_limit", 1_000)
    return arguments


def prepare_for(unit, *, compiler=compile_behavior_unit, **arguments) -> Prepared:
    """One fresh point-of-use attempt over one published behavior."""

    core = published_core(unit)
    admission = WORLD.admission_request(core)
    subjects = (R.replay_subject(subject_ref=admitted_subject(unit), unit=unit),)
    return Prepared(admission, subjects, compiler, _defaults(arguments), (unit,), core, ())


def prepare_many(units, *, order=None, **arguments) -> Prepared:
    """A run over several admitted behaviors, in the caller's chosen order.

    ``order`` is the *execution* sequence, and it is deliberately allowed to
    differ from the canonical subject order §22 decides about. Defaulting it to
    the reverse of the canonical order is what makes these cases bite: a build
    that hands the execution sequence to the gate comparison is refused as
    ``UNORDERED_SUBJECT``, which is how that conflation was found.
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
    ordered = tuple(item.unit for item in subjects)
    return Prepared(
        admission, subjects, compile_behavior_unit, _defaults(arguments), ordered, primary, extra
    )


def _machines_for(prepared, pure_unit):
    """One machine per behavior, in the run's execution order.

    Each machine holds a pool covering the run's budget. A machine that runs dry
    first stops the replay with ``GAS_EXHAUSTED``, which is correct but is not
    what these cases are about.
    """

    budget = prepared.arguments["gas_budget"]
    machines = []
    for unit in prepared.units:
        compiled = compile_behavior_unit(unit)
        if unit is pure_unit:
            machines.append(pure_adapter(budget))
        else:
            machines.append(
                ScriptedPort(
                    program=compiled.actual_program_hash,
                    host_abi=compiled.host_abi_version,
                    opcodes=["ADD"],
                    gas=budget,
                )
            )
    return tuple(machines)


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


def pure_prepared(**overrides) -> Prepared:
    """One fresh point-of-use attempt over the golden pure behavior.

    Deliberately **not** cached. An earlier revision memoised requests by their
    arguments, reasoning that a point-of-use attempt admits exactly once and two
    cases asking for the same request should share the object. That was right
    about the cost and wrong about the subject: a shared request is a request
    that outlives its admission, and reusing one across cases taught the
    acceptance to treat a request as portable authority — the exact property §22
    forbids and the production path now refuses. Each case admits for itself.
    """

    record = golden("pure_add_v1")
    unit, _binding = pure_behavior()
    arguments = dict(expected_transcript_root=record["expected_transcript_root"])
    arguments.update(overrides)
    return prepare_for(unit, **arguments)


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


def scripted_prepared(opcodes: list[str], *, activity_ids: tuple[str, ...] = (), **overrides):
    """A run bound to a real unit whose contract matches a scripted transcript."""

    transitions = scripted_transitions(opcodes)
    unit = unit_with(contract_for(transitions, activity_ids))
    arguments = dict(
        gas_budget=1_000,
        expected_transcript_root=R.transcript_root(
            transitions=transitions, activities=activity_ids
        ),
    )
    arguments.update(overrides)
    return prepare_for(unit, **arguments), transitions


def run_scripted(prepared: Prepared, **port_kwargs) -> R.BehaviorReplayResult:
    port = ScriptedPort(
        program=prepared.program_hash, host_abi=prepared.host_abi, **port_kwargs
    )
    return prepared.run((port,))


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
    classified = (
        R.REPLAY_ADMISSIBLE_OPCODES | R.RECORDED_ONLY_OPCODES | R.DISPATCH_GUARDED_OPCODES
    )
    unclassified = set(GAS_COSTS) - classified
    assert not unclassified, f"opcodes with no determinism class: {sorted(unclassified)}"


def test_the_profile_names_no_opcode_the_machine_does_not_have() -> None:
    classified = (
        R.REPLAY_ADMISSIBLE_OPCODES | R.RECORDED_ONLY_OPCODES | R.DISPATCH_GUARDED_OPCODES
    )
    assert not classified - set(GAS_COSTS)


def test_the_three_classes_are_disjoint() -> None:
    """Three classes now, and an opcode in two of them would have no class at all."""

    assert not (R.REPLAY_ADMISSIBLE_OPCODES & R.RECORDED_ONLY_OPCODES)
    assert not (R.REPLAY_ADMISSIBLE_OPCODES & R.DISPATCH_GUARDED_OPCODES)
    assert not (R.RECORDED_ONLY_OPCODES & R.DISPATCH_GUARDED_OPCODES)


def test_arbitrary_python_dispatch_is_not_unconditionally_deterministic() -> None:
    """``CALL`` and ``CALL_METHOD`` execute Python inline, so neither is Category A.

    The machine runs ``fn(*args)`` for an ordinary callable without passing
    through host routing, which means the recorded-activity channel never sees
    it. Leaving them in the admissible set said a replay could run uninstrumented
    code and still be called deterministic.
    """

    for opcode in ("CALL", "CALL_METHOD"):
        assert opcode not in R.REPLAY_ADMISSIBLE_OPCODES
        assert R.classify_replay_opcode(opcode) == "dispatch_guarded"


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
    """A record pinned to one frozen profile is not evidence about another.

    Stated at the record level, because the governed path computes the digest
    inside the same call that runs — a request and a run can no longer disagree
    about the profile unless the request came from somewhere else, which is
    exactly the case a restored record represents.
    """

    prepared = pure_prepared()
    request = prepared.request()
    monkeypatch.setattr(
        R, "REPLAY_ADMISSIBLE_OPCODES", R.REPLAY_ADMISSIBLE_OPCODES | {"NEW_OPCODE"}
    )
    result = R._execute_replay_body(
        request,
        machines=(pure_adapter(),),
        activity_store=prepared.bundle.activity_store,
    )
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
    channel = channel_for(budget=4)
    adapter.attach_channel(channel)
    with pytest.raises(R.ReplayViolation):
        adapter.attach_channel(channel)


# ---------------------------------------------------------------------------
# Exact transition replay and program/activity manifest matching
# ---------------------------------------------------------------------------


def test_the_golden_replay_is_identical_to_its_manifest() -> None:
    record = golden("pure_add_v1")
    prepared = pure_prepared()
    result = prepared.run((pure_adapter(),))

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
    assert request.capability_profile == R.REPLAY_CAPABILITY_PROFILE_V1
    assert request.capability_profile_digest == record["capability_profile_digest"]
    assert request.gas_budget == GAS and request.cognitive_budget == 8
    assert request.recorded_activity_refs == ()
    assert request.schema_version is SchemaVersion.BEHAVIOR_REPLAY_REQUEST_V1
    R.validate_replay_request(request)


def test_the_replay_is_identical_run_to_run() -> None:
    roots = []
    for _ in range(3):
        prepared = pure_prepared()
        result = prepared.run((pure_adapter(),))
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
    result = prepared.run((pure_adapter(),))
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
    first, second = transitions
    result = run_scripted(
        prepared, opcodes=["ADD", "SUB", "MUL"], hash_script=[first, first, second]
    )
    assert frozenset(result.transition_hash_chain) == frozenset(transitions)
    assert len(result.transition_hash_chain) != len(transitions)
    assert_contract_rejected(result)


def test_a_substituted_transition_is_a_mismatch_and_is_located() -> None:
    prepared, _ = scripted_prepared(["ADD", "SUB", "MUL"])
    result = run_scripted(prepared, opcodes=["ADD", "DIV", "MUL"])
    assert_contract_rejected(result)
    assert result.observations[0].first_unexpected_index == 1


def test_a_different_program_hash_is_incompatible_and_runs_nothing() -> None:
    prepared, _ = scripted_prepared(["ADD"])
    port = ScriptedPort(program="sha256:some-other-program", opcodes=["ADD"])
    result = prepared.run((port,))
    assert result.status is R.ReplayStatus.REPLAY_INCOMPATIBLE
    assert result.failure_reason is R.ReplayFailureReason.PROGRAM_HASH_MISMATCH
    assert result.steps_executed == 0
    assert port.channel is None


def test_a_different_host_abi_is_incompatible() -> None:
    prepared, _ = scripted_prepared(["ADD"])
    port = ScriptedPort(program=prepared.program_hash, opcodes=["ADD"], host_abi="9.9")
    result = prepared.run((port,))
    assert result.status is R.ReplayStatus.REPLAY_INCOMPATIBLE
    assert result.failure_reason is R.ReplayFailureReason.HOST_ABI_MISMATCH


def test_one_machine_is_required_per_admitted_behavior() -> None:
    prepared = pure_prepared()
    with pytest.raises(R.ReplayViolation) as excinfo:
        prepared.run(())
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

    prepared = prepare_many((unit_a, unit_b))
    ordered_units = prepared.units
    execution = tuple(item.subject_ref for item in prepared.subjects)
    # The admitted set is canonical; the run is not, and that is the point.
    assert execution != A.canonical_subject_refs(execution), (
        "the execution order was not made to differ from the canonical order"
    )

    # Each machine holds a pool covering the run's budget: a machine that ran dry
    # would stop the replay with GAS_EXHAUSTED, and this case is about execution
    # order rather than about gas.
    budget = prepared.arguments["gas_budget"]
    machines = []
    for unit in ordered_units:
        compiled = compile_behavior_unit(unit)
        if unit is unit_a:
            machines.append(pure_adapter(budget))
        else:
            machines.append(
                ScriptedPort(
                    program=compiled.actual_program_hash,
                    host_abi=compiled.host_abi_version,
                    opcodes=["ADD", "SUB"],
                    gas=budget,
                )
            )
    result = prepared.run(tuple(machines))
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


# ---------------------------------------------------------------------------
# Forbidden host capability and gas exhaustion give typed failures
# ---------------------------------------------------------------------------


def recorded_llm_call(
    *,
    prompt: bytes = b"explain",
    sequence: int = 1,
    result: bytes = R_RESULT,
    policy_disposition=ACT.ActivityDisposition.RECORDED_CONSUMABLE,
    program: str = "sha256:scripted",
) -> ACT.RecordedActivity:
    """One recorded LLM call whose exact bytes a prepared run will publish.

    ``policy_disposition`` names the test authority declaration, not a field
    on the record: a record no longer carries a disposition of anyone's choosing,
    and a prepared run builds its evaluator's declaration to match whatever its
    activities were recorded under.
    """

    record = governed_activity(
        kind=ACT.ActivityKind.LLM_CALL,
        inputs=ACT.activity_inputs(prompt=prompt),
        position=ACT.ActivityPosition(
            program_hash=program, instruction_pointer=0, frame_depth=0, sequence=sequence
        ),
        result=result,
        policy_disposition=policy_disposition,
    )
    _RESULT_BYTES[record.result_sha256] = result
    return record


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
        run_scripted(prepared, opcodes=["ADD", "LLM_EVAL"], on_step=consuming_step())
    assert excinfo.value.failure_code is R.ReplayFailureCode.ACTIVITY_NOT_GOVERNED


def test_a_forbidden_host_call_fails_before_its_side_effect() -> None:
    """The policy refusal occurs before channel attachment and machine movement."""

    activity = recorded_llm_call(
        policy_disposition=ACT.ActivityDisposition.FORBIDDEN_IN_REPLAY
    )
    prepared, _ = scripted_prepared(["LLM_EVAL"], activities=(activity,))
    machine = ScriptedPort(
        program=prepared.program_hash,
        host_abi=prepared.host_abi,
        opcodes=["LLM_EVAL"],
        on_step=consuming_step(),
    )
    with pytest.raises(R.ReplayViolation) as excinfo:
        prepared.run((machine,))
    assert excinfo.value.failure_code is R.ReplayFailureCode.ACTIVITY_NOT_GOVERNED
    assert machine.channel is None
    assert machine._index == 0


def test_gas_exhaustion_is_a_typed_failure() -> None:
    prepared, _ = scripted_prepared(["ADD", "SUB", "MUL"], gas_budget=2)
    result = run_scripted(prepared, opcodes=["ADD", "SUB", "MUL"], gas=100)
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


def test_a_faulting_machine_is_infra_error_not_a_behavior_failure() -> None:
    def explode(port, opcode):
        if opcode == "SUB":
            raise ZeroDivisionError("machine fault")

    prepared, _ = scripted_prepared(["ADD", "SUB"])
    result = run_scripted(prepared, opcodes=["ADD", "SUB"], on_step=explode)
    assert result.status is R.ReplayStatus.INFRA_ERROR
    assert result.failure_reason is R.ReplayFailureReason.MACHINE_FAULT


def test_gas_that_increases_is_refused_outright() -> None:
    prepared, _ = scripted_prepared(["ADD", "SUB"])
    with pytest.raises(R.ReplayViolation) as excinfo:
        run_scripted(prepared, opcodes=["ADD", "SUB"], gas_after=lambda gas: gas + 5)
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

    record = governed_activity(
        kind=ACT.ActivityKind(payload["kind"]),
        inputs=ACT.ActivityInputs.from_dict(payload["inputs"]),
        position=ACT.ActivityPosition.from_dict(payload["position"]),
        policy_version=payload["policy_version"],
        result=GOLDEN_EFFECT_RESULT,
    )
    _RESULT_BYTES[record.result_sha256] = GOLDEN_EFFECT_RESULT
    return record


def test_the_golden_activity_record_round_trips() -> None:
    record, _, records = effect_fixture()
    rebuilt = rebuild_recorded_activity(records[0]["payload"])
    assert rebuilt.activity_identity == record["activity_identity"]
    assert rebuilt.lookup_key == record["activity_lookup_key"]
    assert rebuilt.to_dict() == records[0]


def test_a_recorded_result_is_injected_without_a_fresh_external_call() -> None:
    """The adapter serves LLM_EVAL from record; no live producer is reachable."""

    record, program, records = effect_fixture()
    activity = rebuild_recorded_activity(records[0]["payload"])
    channel = channel_for(activity, budget=8)
    adapter = R.CognitiveVMReplayAdapter(program, gas_budget=GAS)
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
                ACT.compute_activity_lookup_key(
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
    prepared, _ = scripted_prepared(
        ["ADD", "LLM_EVAL"],
        activity_ids=(other.activity_identity,),
        activities=(activity, other,),
    )
    result = run_scripted(prepared, opcodes=["ADD", "LLM_EVAL"], on_step=consuming_step())
    assert result.failure_reason is R.ReplayFailureReason.TRANSITION_MISMATCH


def test_the_channel_closes_when_the_replay_ends() -> None:
    prepared, _ = scripted_prepared(["ADD"])
    port = ScriptedPort(program=prepared.program_hash, opcodes=["ADD"])
    prepared.run((port,))
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

    prepared, _ = scripted_prepared(["ADD"])
    port = ScriptedPort(program=prepared.program_hash, opcodes=["ADD"], on_step=explode)
    prepared.run((port,))
    assert not port.channel.is_open


def test_a_channel_cannot_be_built_outside_a_replay() -> None:
    with pytest.raises(TypeError):
        R.RecordedActivityChannel(ledger(), 4, None)


def test_the_request_pins_the_activity_history_it_will_consume() -> None:
    activity = recorded_llm_call()
    prepared, _ = scripted_prepared(["ADD", "LLM_EVAL"], activities=(activity,))
    request = prepared.request()
    assert request.recorded_activity_refs == (ACT.activity_ref(activity),)
    assert request.activity_identities == (activity.activity_identity,)


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


def test_the_effect_snapshot_is_the_state_the_injected_result_produced() -> None:
    """The effect fixture's snapshot, and the run that is supposed to reach it.

    ``llm_effect_v1`` is the behaviour whose ``LLM_EVAL`` is served from record,
    so its terminal state is the one place where "the exact recorded bytes were
    injected" becomes a durable artifact rather than an assertion about a run.
    Both directions are checked: the stored snapshot restores to the digest the
    manifest records, and a live run over the recorded result arrives at the same
    digest. Either alone would let the two drift — a fixture nobody reaches, or a
    run measured only against itself.
    """

    record, _, _ = effect_fixture()
    restored = R.CognitiveVMReplayAdapter.from_snapshot(
        golden_file("llm_effect_v1.vm_snapshot.json"), gas_budget=GAS
    )
    assert restored.program_hash() == record["program_hash"]
    assert restored.snapshot_digest() == record["expected_terminal_snapshot_digest"]
    assert restored.transition_hash() == record["expected_transition_ids"][-1]

    _, _, digests = effect_run(GOLDEN_EFFECT_RESULT)
    assert digests[-1] == restored.snapshot_digest()


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
    prepared = pure_prepared(
        expected_terminal_snapshot_digests=(record["expected_terminal_snapshot_digest"],)
    )
    first = prepared.run((pure_adapter(),))
    assert first.status is R.ReplayStatus.REPLAY_IDENTICAL

    resumed_machine = R.CognitiveVMReplayAdapter.from_snapshot(
        golden_file("pure_add_v1.vm_snapshot.json"), gas_budget=GAS
    )
    again = prepare_for(
        unit,
        expected_terminal_snapshot_digests=(record["expected_terminal_snapshot_digest"],),
    ).resume((resumed_machine,), resumed_from=first)
    assert again.terminal_snapshot_digests == first.terminal_snapshot_digests
    assert again.steps_executed == 0
    assert again.status is not R.ReplayStatus.REPLAY_INCOMPATIBLE, (
        "resume verification rejected a state it should have accepted"
    )


def test_resume_refuses_a_machine_in_another_state() -> None:
    record = golden("pure_add_v1")
    unit, _ = pure_behavior()
    prepared = pure_prepared()
    first = prepared.run((pure_adapter(),))
    fresh = pure_adapter()
    assert fresh.snapshot_digest() != record["expected_terminal_snapshot_digest"]
    result = prepare_for(unit).resume((fresh,), resumed_from=first)
    assert result.status is R.ReplayStatus.REPLAY_INCOMPATIBLE
    assert result.failure_reason is R.ReplayFailureReason.SNAPSHOT_INCOMPATIBLE


def test_resume_refuses_a_continuation_across_a_knowledge_snapshot() -> None:
    """A continuation cannot reach across worlds, and it is stopped twice.

    First by the store, which is where a continuation now finds the result it
    continues: a run committed under another boundary was recorded in that
    world's journal, and asking this one for it gets a typed refusal rather than
    an answer. That is earlier and stronger than the lineage comparison, and it
    is the answer the production path gives.

    The lineage comparison is still asserted, at the record level, because a
    restored continuation is not built by that path and can name anything.
    """

    from synapse.experiments.gold.replay_store import ReplayStoreViolation

    prepared = pure_prepared()
    first = prepared.run((pure_adapter(),))

    other_unit = unit_with(contract_for(scripted_transitions(["ADD"])), literal=99)
    elsewhere = prepare_for(other_unit)
    compiled = compile_behavior_unit(other_unit)
    machines = (
        ScriptedPort(
            program=compiled.actual_program_hash,
            host_abi=compiled.host_abi_version,
            opcodes=["ADD"],
        ),
    )
    with pytest.raises(ReplayStoreViolation) as store_error:
        elsewhere.resume(machines, resumed_from=first)
    assert store_error.value.failure_code.value == "RECORD_UNKNOWN"

    # And the record-level check: a continuation naming a result from another
    # committed boundary is refused for crossing it, before any machine is asked.
    crossing = prepare_for(
        other_unit, resumed_from_result_ref=R.replay_result_ref(first)
    )
    with pytest.raises(R.ReplayViolation) as excinfo:
        R._resume_replay_body(
            crossing.request(),
            machines=machines,
            resumed_from=first,
            activity_store=crossing.bundle.activity_store,
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.RESUME_LINEAGE_MISMATCH


def test_resume_refuses_a_continuation_of_another_result() -> None:
    """Пара «запрос + результат» больше не выбирается на месте вызова."""

    unit, _ = pure_behavior()
    first = pure_prepared().run((pure_adapter(),))
    second = pure_prepared(cognitive_budget=7).run((pure_adapter(),))
    assert R.replay_result_ref(first) != R.replay_result_ref(second)

    # The public continuation path derives the lineage from the result it is
    # given, so the pair cannot be chosen at the call site any more. The check
    # is still asserted at the record level, because a restored continuation is
    # not built by that path and could name anything.
    continuation = prepare_for(
        unit, resumed_from_result_ref=R.replay_result_ref(first)
    )
    with pytest.raises(R.ReplayViolation) as excinfo:
        R._resume_replay_body(
            continuation.request(),
            machines=(pure_adapter(),),
            resumed_from=second,
            activity_store=continuation.bundle.activity_store,
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
    prepared = prepare_many((unit_a, unit_b), order=forward)
    first = prepared.run(_machines_for(prepared, unit_a))
    assert len(first.observations) == 2, "both behaviors must have run to their end"

    continuation = prepare_many((unit_a, unit_b), order=tuple(reversed(forward)))
    assert tuple(item.subject_ref for item in continuation.subjects) != tuple(
        item.subject_ref for item in prepared.subjects
    )
    result = continuation.resume(
        _machines_for(continuation, unit_a), resumed_from=first
    )
    assert result.status is R.ReplayStatus.REPLAY_INCOMPATIBLE
    assert result.failure_reason is R.ReplayFailureReason.PROGRAM_HASH_MISMATCH


def test_resume_uses_the_predecessors_exact_durable_activity_history() -> None:
    activity = recorded_llm_call()
    unit, _ = pure_behavior()
    prepared = pure_prepared()
    first = prepared.run((pure_adapter(),))
    terminal = R.CognitiveVMReplayAdapter.from_snapshot(
        golden_file("pure_add_v1.vm_snapshot.json"), gas_budget=GAS
    )
    result = prepare_for(unit, activities=(activity,)).resume(
        (terminal,), resumed_from=first
    )
    assert result.recorded_activity_refs == first.recorded_activity_refs == ()
    assert result.status is not R.ReplayStatus.REPLAY_INCOMPATIBLE


def test_a_tampered_terminal_state_is_detected() -> None:
    prepared = pure_prepared(expected_terminal_snapshot_digests=("f" * 64,))
    result = prepared.run((pure_adapter(),))
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
    prepared = pure_prepared()
    result = prepared.run((pure_adapter(),))
    fields = set(result.to_dict())
    assert not fields & {
        "outcome", "verdict", "completeness", "correctness", "task_success", "full"
    }
    assert result.status is R.ReplayStatus.REPLAY_IDENTICAL


def test_an_observation_makes_no_claim_about_task_success() -> None:
    """§23: replay observations do not gain instruction or task-success authority."""

    prepared = pure_prepared()
    result = prepared.run((pure_adapter(),))
    stored = result.observations[0].to_dict()
    payload = stored["payload"]
    assert not set(payload) & {
        "correct", "passed", "verdict", "task_success", "oracle", "authority"
    }
    assert set(payload) >= {"transition_hash_chain", "transcript_matched", "failure_reason"}


def test_identity_requires_a_root_pinned_before_the_run() -> None:
    """A sorted-set contract cannot see a permutation; a pinned root can."""

    prepared = pure_prepared(expected_transcript_root=None)
    result = prepared.run((pure_adapter(),))
    assert result.status is R.ReplayStatus.REPLAY_FAILED
    assert result.failure_reason is R.ReplayFailureReason.TRANSITION_MISMATCH
    assert result.observations[0].transcript_matched


def test_a_result_cannot_be_built_by_its_constructor() -> None:
    for factory in (R.BehaviorReplayRequest, R.BehaviorReplayResult, R.ReplayObservation):
        with pytest.raises(TypeError):
            factory()  # type: ignore[call-arg]


def test_rewriting_a_result_field_invalidates_it() -> None:
    prepared = pure_prepared()
    result = prepared.run((pure_adapter(),))
    object.__setattr__(result, "status", R.ReplayStatus.REPLAY_FAILED)
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.validate_replay_result(result)
    assert excinfo.value.failure_code is R.ReplayFailureCode.STATUS_REASON_INCONSISTENT


def test_a_forged_identical_status_over_a_failed_run_is_refused() -> None:
    prepared, _ = scripted_prepared(["ADD", "SUB"])
    failed = run_scripted(prepared, opcodes=["ADD", "MUL"])
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

    prepared = pure_prepared(expected_transcript_root=None)
    result = prepared.run((pure_adapter(),))
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

    prepared, _ = scripted_prepared(["ADD"])
    port = ScriptedPort(program="sha256:some-other-program", opcodes=["ADD"])
    result = prepared.run((port,))
    assert result.status is R.ReplayStatus.REPLAY_INCOMPATIBLE
    object.__setattr__(result, "status", R.ReplayStatus.REPLAY_FAILED)
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.validate_replay_result(result)
    assert excinfo.value.failure_code is R.ReplayFailureCode.STATUS_REASON_INCONSISTENT


def test_rewriting_the_transcript_invalidates_the_root() -> None:
    prepared = pure_prepared()
    result = prepared.run((pure_adapter(),))
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
    prepared = prepare_for(unit)
    prepared.subjects = (
        R.ReplaySubject(subject_ref=admitted_subject(unit), unit=stranger),
    )
    with pytest.raises(R.ReplayViolation) as excinfo:
        prepared.request()
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

    prepared = prepare_for(unit)
    prepared.subjects = (R.replay_subject(subject_ref=stranger_ref, unit=stranger),)
    with pytest.raises(Exception) as excinfo:
        prepared.request()
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

    prepared = pure_prepared()
    request = prepared.request()
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

    prepared = pure_prepared()
    request = prepared.request()
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


def test_live_environment_drift_after_preparation_is_refused_before_replay() -> None:
    """§22: the Consumption Gate reads the world at the moment of use.

    Everything durable is left exactly where it was — lifecycle, provenance,
    taint, the admission journal and the committed boundary all keep their
    anchors — and only the *live* platform observation changes, to another
    environment profile version. Nothing a head comparison can see has moved.

    Before the repair this ran to ``REPLAY_IDENTICAL``. The gate had been
    crossed when the request was built, and execution re-checked only the
    coordinator epoch, the authority heads and the boundary; the compatibility
    that had just stopped holding was never re-evaluated, because the evaluation
    that reads environment, tool and policy observation had already happened.

    Four things are asserted together, because three of them pass without the
    fourth: the durable heads did not move, the observation provider really was
    consulted again, the refusal is typed and fail-closed, and the machine was
    never attached to a channel and never took a step.
    """

    unit, _binding = pure_behavior()
    core = published_core(unit)
    prepared = prepare_for(unit)
    port = ScriptedPort(program=prepared.program_hash, opcodes=["ADD"])

    before = WORLD.durable_head_anchors(core)
    provider = WORLD.platform_observation_provider(core)
    with WORLD.drifted_environment(core, environment_version="synapse.stage4.environment/v999"):
        assert WORLD.durable_head_anchors(core) == before, (
            "changing the live observation must not move a durable authority head"
        )
        calls_before = provider.calls
        with pytest.raises(Exception) as excinfo:
            prepared.run((port,))
        assert provider.calls > calls_before, (
            "the point-of-use evaluation did not read the live observation again"
        )

    assert getattr(excinfo.value, "failure_code", None) is not None, (
        "environment drift must be refused with a typed failure, not a bare error"
    )
    assert port.channel is None, "a refused replay attached the activity channel"
    assert port._index == 0, "a refused replay took a machine step"


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
    prepared = pure_prepared()
    request = prepared.request()

    # The world moves: a second attempt is admitted over the same subject.
    WORLD.admit(WORLD.admission_request(published_core(unit)))

    # The public path cannot express this any more — it admits and runs as one
    # act — so the case is stated where a restored request would arrive: at the
    # executor, which re-checks the admission it was handed against the world as
    # it is now.
    with pytest.raises(R.ReplayViolation) as excinfo:
        R._require_current_admission(
            request, authority=prepared._last_binding.authority
        )
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

    prepared = pure_prepared()
    request = prepared.request()

    class NotABinding:
        def open_current_snapshot(self):  # pragma: no cover - must never be reached
            raise AssertionError("a forged binding was asked for the current snapshot")

    with pytest.raises(AdmissionViolation) as excinfo:
        R._require_current_admission(request, authority=NotABinding())
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

    prepared = pure_prepared()
    request = prepared.request()
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

    prepared = pure_prepared()
    request = prepared.request()
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

    prepared = pure_prepared()
    request = prepared.request()
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

    prepared = pure_prepared()
    request = prepared.request()
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

    first = pure_prepared().run((pure_adapter(),))
    plain = pure_prepared()
    request = plain.request()
    assert request.resumed_from_result_ref is None
    with pytest.raises(R.ReplayViolation) as excinfo:
        R._resume_replay_body(
            request,
            machines=(pure_adapter(),),
            resumed_from=first,
            activity_store=plain.bundle.activity_store,
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.RESUME_LINEAGE_MISMATCH


def test_the_request_reads_its_authority_off_the_admission_not_the_caller() -> None:
    """There is nothing left for a caller to assert about its own entitlement."""

    import inspect

    parameters = set(inspect.signature(R._create_replay_request).parameters)
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
    prepared = pure_prepared()
    request = prepared.request()
    assert request.knowledge_snapshot_id == request.snapshot_manifest_ref.ref_id
    assert request.policy_version == POLICY


def test_the_barrier_is_crossed_before_anything_is_compiled() -> None:
    """The order is a sequence this function performs, not a flag a caller sets."""

    unit, _binding = pure_behavior()
    order: list[tuple[str, int]] = []
    core = published_core(unit)
    provider = WORLD.platform_observation_provider(core)
    calls_before = provider.calls

    def watching_compiler(value):
        order.append(("compile", provider.calls))
        return compile_behavior_unit(value)

    prepare_for(unit, compiler=watching_compiler).request()
    assert order and order[0][0] == "compile"
    assert order[0][1] > calls_before, "compilation started before the first fresh barrier"


def test_a_ledger_is_sealed_by_the_request_against_its_own_admission() -> None:
    """A ledger cannot be sealed elsewhere and carried in.

    One point-of-use attempt admits exactly once, so a request and a
    separately-sealed ledger could never share an admission. The request seals
    its own, and there is no parameter through which another one could arrive.
    """

    import inspect

    assert "ledger" not in inspect.signature(R._create_replay_request).parameters
    prepared = pure_prepared()
    request = prepared.request()
    knowledge_id = request.ledger.admitted_knowledge_id
    assert knowledge_id == request.admitted_knowledge_id
    assert request.ledger.knowledge_subject_refs == request.knowledge_subject_refs


def test_a_ledger_from_another_policy_version_never_reaches_a_request() -> None:
    other = governed_activity(
        kind=ACT.ActivityKind.LLM_CALL,
        inputs=ACT.activity_inputs(prompt=b"explain the bug"),
        position=ACT.ActivityPosition(
            program_hash="sha256:program-a", instruction_pointer=7, frame_depth=0, sequence=0
        ),
        policy_version="policy-v2",
        result=R_RESULT,
    )
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        pure_prepared(activities=(other,)).request()
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
        prepare_for(other_unit, compiler=lambda _value: binding).request()
    assert excinfo.value.failure_code is CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH


def test_a_request_does_not_accept_its_own_replay_contract() -> None:
    import inspect

    assert "replay_contract" not in inspect.signature(R.run_governed_replay).parameters
    assert "replay_contract" not in inspect.signature(R._create_replay_request).parameters


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

    channel = channel_for(budget=8)
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        channel.resolve(
            kind=ACT.ActivityKind.LLM_CALL,
            inputs=ACT.activity_inputs(prompt=b"nobody recorded this"),
            position=ACT.ActivityPosition(
                program_hash="sha256:scripted", instruction_pointer=0, frame_depth=0, sequence=1
            ),
        )
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.ACTIVITY_NOT_RECORDED

    prepared, _ = scripted_prepared(["ADD", "LLM_EVAL"])
    result = run_scripted(prepared, opcodes=["ADD", "LLM_EVAL"], on_step=consuming_step())
    assert result.status is R.ReplayStatus.REPLAY_FAILED
    assert result.failure_reason is R.ReplayFailureReason.MISSING_ACTIVITY_RECORD
    assert result.steps_executed == 1


def test_mutant_a_different_program_hash_is_accepted_is_killed() -> None:
    """Mutant: the execution-contract check before the first transition is dropped."""

    prepared, _ = scripted_prepared(["ADD"])
    port = ScriptedPort(program="sha256:some-other-program", opcodes=["ADD"])
    result = prepared.run((port,))
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

    short_prepared, _ = scripted_prepared(["ADD", "SUB", "MUL"])
    assert_contract_rejected(run_scripted(short_prepared, opcodes=["ADD", "SUB"]))

    swapped_prepared, _ = scripted_prepared(["ADD", "SUB", "MUL"])
    assert_contract_rejected(
        run_scripted(swapped_prepared, opcodes=["ADD", "DIV", "MUL"])
    )

    duplicate_prepared, transitions = scripted_prepared(["ADD", "SUB"])
    first, second = transitions
    assert_contract_rejected(
        run_scripted(
            duplicate_prepared, opcodes=["ADD", "SUB", "MUL"],
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

    prepared = pure_prepared()
    result = prepared.run((pure_adapter(),))
    assert not set(result.to_dict()) & {"outcome", "verdict", "completeness", "full"}
    assert {item.value for item in R.ReplayStatus} == {
        "REPLAY_IDENTICAL", "REPLAY_INCOMPATIBLE", "REPLAY_FAILED", "INFRA_ERROR"
    }

    # Forged onto a run that failed, and onto a clean run whose root was never
    # pinned. Two barriers, and each has to hold on its own.
    scripted, _transitions = scripted_prepared(["ADD", "SUB"])
    unpinned = pure_prepared(expected_transcript_root=None)
    for forged in (
        run_scripted(scripted, opcodes=["ADD", "MUL"]),
        unpinned.run((pure_adapter(),)),
    ):
        object.__setattr__(forged, "status", R.ReplayStatus.REPLAY_IDENTICAL)
        object.__setattr__(forged, "failure_reason", None)
        with pytest.raises(R.ReplayViolation) as excinfo:
            R.validate_replay_result(forged)
        assert excinfo.value.failure_code is R.ReplayFailureCode.STATUS_REASON_INCONSISTENT


def test_mutant_the_consumption_gate_is_skipped_before_replay_is_killed() -> None:
    """The compiler is bracketed by two distinct fresh §22 evaluations."""

    import inspect

    source = inspect.getsource(R._prepare_replay)
    first = source.index("_admit_now(initial_request)")
    compiled = source.index("replay_program_binding")
    final = source.index("_admit_now(binding.final_admission)")
    assert first < compiled < final


# ---------------------------------------------------------------------------
# §23 — the recorded result that is injected is the recorded result
# ---------------------------------------------------------------------------
#
# An earlier revision answered the machine with a dictionary assembled during
# the run: the opcode, a status string, the activity identity and the result
# digest. Every field of it was accurate and none of it was the result. The
# machine pushed the description onto its stack and carried on, and nothing
# downstream could tell, because the recorded bytes were never stored anywhere.
# These cases are about the difference between describing a result and having it.


def effect_run(result: bytes, *, budget: int = 8):
    """The golden effect program, driven to its halt over one recorded result."""

    _, program, records = effect_fixture()
    payload = dict(records[0]["payload"])
    activity = governed_activity(
        kind=ACT.ActivityKind(payload["kind"]),
        inputs=ACT.ActivityInputs.from_dict(payload["inputs"]),
        position=ACT.ActivityPosition.from_dict(payload["position"]),
        policy_version=payload["policy_version"],
        result=result,
    )
    channel = channel_for(activity, budget=budget)
    adapter = R.CognitiveVMReplayAdapter(program, gas_budget=GAS)
    adapter.attach_channel(channel)
    digests = []
    while not adapter.is_halted() and adapter.next_opcode() is not None:
        adapter.step()
        digests.append(adapter.snapshot_digest())
    return activity, channel, tuple(digests)


def test_the_channel_produces_the_exact_bytes_the_record_names() -> None:
    """The value, not a description of it, and byte-identical to what was stored."""

    activity, channel, _ = effect_run(GOLDEN_EFFECT_RESULT)
    raw = channel.open_result(activity)
    assert raw == GOLDEN_EFFECT_RESULT
    assert hashlib.sha256(raw).hexdigest() == activity.result_sha256
    assert R.decode_recorded_result(raw) == "the recorded model answer"


def test_the_recorded_bytes_are_what_the_machine_carries_forward() -> None:
    """Change only the recorded result, and the machine's state changes with it.

    The effect's value is pushed and then popped, so the observable difference
    is the state *at* the transition that consumed it. A run that injected a
    description keyed on identity, or a constant stub, would reach the same
    state under both records — which is exactly the defect this replaces.
    """

    record, _, _ = effect_fixture()
    _, _, golden_digests = effect_run(GOLDEN_EFFECT_RESULT)
    _, _, other_digests = effect_run(R.encode_recorded_result("a different answer"))
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
    stub = R.encode_recorded_result(
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
        policy_version=payload["policy_version"],
        result=orphan,
        result_ref=ACTIVITY_RESULT_REF(orphan),
        context=RECORD_CONTEXT,
        recorded_at_utc=NOW,
    )
    channel = channel_for(activity, budget=8)
    adapter = R.CognitiveVMReplayAdapter(program, gas_budget=GAS)
    adapter.attach_channel(channel)
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        for _ in range(10):
            adapter.step()
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.ACTIVITY_NOT_RECORDED


def test_a_rewritten_blob_is_refused_rather_than_injected() -> None:
    """The store re-derives the digest, so bytes swapped underneath it do not pass."""

    substituted = R.encode_recorded_result("bytes written under someone else's name")
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
        R.decode_recorded_result(b"\xff not json at all")
    assert excinfo.value.failure_code is R.ReplayFailureCode.RESULT_NOT_DECODABLE


# ---------------------------------------------------------------------------
# §23 — the request and the result are durable, and in that order
# ---------------------------------------------------------------------------


def test_the_request_is_durable_before_the_first_transition() -> None:
    """Observed from inside the run, because the ordering is the whole claim.

    Counting the store after the run would pass for a path that recorded the
    request last. The count is taken at the first transition instead, while the
    machine has executed nothing.
    """

    prepared, _ = scripted_prepared(["ADD", "SUB"])
    store = prepared.bundle.replay_store
    before = len(store.recorded_request_refs())
    results_before = len(store.recorded_result_refs())
    seen: dict = {}
    def observe(port, opcode):
        seen.setdefault("requests", len(store.recorded_request_refs()))
        seen.setdefault("results", len(store.recorded_result_refs()))

    result = run_scripted(prepared, opcodes=["ADD", "SUB"], on_step=observe)
    assert result.status is R.ReplayStatus.REPLAY_IDENTICAL
    assert seen["requests"] == before + 1, "the request was not durable before the run started"
    assert seen["results"] == results_before, "a result was recorded before the run produced one"
    assert len(store.recorded_result_refs()) == results_before + 1


def test_the_result_is_durable_whatever_it_says() -> None:
    """All four statuses, not only the good ones — NR-13 forbids the selection."""

    for opcodes, status in (
        (["ADD", "SUB"], R.ReplayStatus.REPLAY_IDENTICAL),
        (["ADD", "DIV"], R.ReplayStatus.REPLAY_FAILED),
    ):
        prepared, _ = scripted_prepared(["ADD", "SUB"])
        store = prepared.bundle.replay_store
        result = run_scripted(prepared, opcodes=opcodes)
        assert result.status is status
        restored = store.require_result(R.replay_result_ref(result))
        assert restored.status is status
        assert restored.to_dict() == result.to_dict()


def test_the_durable_result_names_a_request_the_same_store_holds() -> None:
    """The pairing is what makes the history a history rather than two lists."""

    prepared, _ = scripted_prepared(["ADD"])
    store = prepared.bundle.replay_store
    result = run_scripted(prepared, opcodes=["ADD"])
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

    prepared, _ = scripted_prepared(["ADD"])
    result = run_scripted(prepared, opcodes=["ADD"])
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


def replica_of(store, prepared, name: str, *, mutate=None):
    """A second store over a copy of a real journal, optionally damaged.

    A copy rather than the store itself: damage inflicted on the run's own
    journal would travel to every other case sharing that world, and what is
    under test is how a store reads a journal, not which file it is.
    """

    from synapse.experiments.gold.replay_store import FileReplayStore

    root = WORLD.stores_root(prepared.core, prepared.extra) / name
    root.mkdir(parents=True, exist_ok=True)
    replica = FileReplayStore(root, mutation_fence=prepared.bundle.fence)
    payload = store.journal_path.read_bytes()
    replica.journal_path.write_bytes(payload if mutate is None else mutate(payload))
    return replica


def test_a_restarted_store_still_holds_what_the_run_recorded() -> None:
    """Durability means a second process reads it, not that one object remembers."""

    prepared, _ = scripted_prepared(["ADD"])
    result = run_scripted(prepared, opcodes=["ADD"])
    reopened = replica_of(prepared.bundle.replay_store, prepared, "restart")
    restored = reopened.require_result(R.replay_result_ref(result))
    assert restored.to_dict() == result.to_dict()
    assert R.replay_result_ref(restored).sha256 == R.replay_result_ref(result).sha256


def test_a_torn_replay_journal_is_refused_rather_than_read() -> None:
    """A partial write at the tail is a torn history, not a shorter one."""

    from synapse.experiments.gold.replay_store import ReplayStoreViolation

    prepared, _ = scripted_prepared(["ADD"])
    run_scripted(prepared, opcodes=["ADD"])
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


# ---------------------------------------------------------------------------
# PR #99 authority/durability repair acceptance
# ---------------------------------------------------------------------------


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
    machine = ScriptedPort(program=prepared.program_hash, opcodes=["ADD"])
    requests_before = len(prepared.bundle.replay_store.recorded_request_refs())
    calls_before = provider.calls
    try:
        with pytest.raises(Exception) as excinfo:
            prepared.run((machine,))
    finally:
        provider.observation = original
    assert getattr(excinfo.value, "failure_code", None) is not None
    assert provider.calls >= calls_before + 2
    assert len(prepared.bundle.replay_store.recorded_request_refs()) == requests_before
    assert machine.channel is None
    assert machine._index == 0


def test_real_executor_cannot_hide_behind_a_false_policy_actor_set() -> None:
    """Actual executor/evaluator equality is refused even if actor-set text differs."""

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
    with pytest.raises(AP.ActivityPolicyViolation) as excinfo:
        R.create_production_replay_binding(
            authority=final.binding,
            initial_admission=prepared.admission,
            final_admission=final,
            activity_policy_evaluator=evaluator,
            activity_store=prepared.bundle.activity_store,
            activity_policy_store=prepared.bundle.activity_policy_store,
            replay_store=prepared.bundle.replay_store,
            executor_actor=EXECUTOR,
        )
    assert excinfo.value.failure_code is AP.ActivityPolicyFailureCode.EVALUATOR_NOT_INDEPENDENT


def test_result_blob_without_durable_activity_record_is_refused_before_compilation() -> None:
    from synapse.experiments.gold.activity_store import (
        ActivityStoreFailureCode,
        ActivityStoreViolation,
    )

    unit, _ = pure_behavior()
    activity = recorded_llm_call(prompt=b"blob-without-record")
    prepared = prepare_for(unit, activities=(activity,))
    governed = prepared._governed(store_records=False)
    machine = ScriptedPort(program=prepared.program_hash, opcodes=["ADD"])
    requests_before = len(prepared.bundle.replay_store.recorded_request_refs())
    with pytest.raises(ActivityStoreViolation) as excinfo:
        R.run_governed_replay(
            admission=prepared.admission,
            subjects=prepared.subjects,
            compiler=prepared.compiler,
            machines=(machine,),
            **governed,
            **prepared.arguments,
        )
    assert excinfo.value.failure_code is ActivityStoreFailureCode.RECORD_UNKNOWN
    assert len(prepared.bundle.replay_store.recorded_request_refs()) == requests_before
    assert machine.channel is None and machine._index == 0


@pytest.mark.parametrize("damage", ["missing", "substituted"])
def test_durable_activity_record_with_unavailable_result_blob_is_refused(damage: str) -> None:
    from synapse.experiments.gold.activity_store import (
        ActivityStoreFailureCode,
        ActivityStoreViolation,
    )

    unit = unit_with(contract_for(scripted_transitions(["ADD"])), literal=7301)
    activity = recorded_llm_call(prompt=("record-" + damage).encode())
    prepared = prepare_for(unit, activities=(activity,))
    governed = prepared._governed()
    blob = prepared.bundle.activity_store._blob_path(activity.result_sha256)
    original = blob.read_bytes()
    if damage == "missing":
        blob.unlink()
    else:
        blob.write_bytes(b"x" * len(original))
    machine = ScriptedPort(program=prepared.program_hash, opcodes=["ADD"])
    try:
        with pytest.raises(ActivityStoreViolation) as excinfo:
            R.run_governed_replay(
                admission=prepared.admission,
                subjects=prepared.subjects,
                compiler=prepared.compiler,
                machines=(machine,),
                **governed,
                **prepared.arguments,
            )
    finally:
        blob.write_bytes(original)
    expected = (
        ActivityStoreFailureCode.RESULT_UNAVAILABLE
        if damage == "missing"
        else ActivityStoreFailureCode.RESULT_CORRUPTED
    )
    assert excinfo.value.failure_code is expected
    assert machine.channel is None and machine._index == 0


def test_activity_policy_decision_missing_after_restart_is_refused() -> None:
    from synapse.experiments.gold.activity_policy_store import (
        ActivityPolicyStoreFailureCode,
        ActivityPolicyStoreViolation,
        FileActivityPolicyStore,
    )
    from synapse.experiments.gold.activity_store import FileActivityStore
    from synapse.experiments.gold.replay_store import FileReplayStore

    activity = recorded_llm_call(prompt=b"missing-policy-after-restart")
    prepared, _ = scripted_prepared(
        ["LLM_EVAL"],
        activity_ids=(activity.activity_identity,),
        activities=(activity,),
    )
    result = run_scripted(
        prepared, opcodes=["LLM_EVAL"], on_step=consuming_step(prompt=b"missing-policy-after-restart")
    )
    continuation = prepare_for(prepared.units[0])
    final = WORLD.admission_request(continuation.core, continuation.extra)
    activity_store = FileActivityStore(
        prepared.bundle.activity_store.journal_path.parent,
        mutation_fence=prepared.bundle.fence,
    )
    replay_store = FileReplayStore(
        prepared.bundle.replay_store.journal_path.parent,
        mutation_fence=prepared.bundle.fence,
    )
    empty_policy = FileActivityPolicyStore(
        WORLD.stores_root(prepared.core, prepared.extra) / "empty-policy-after-restart",
        mutation_fence=prepared.bundle.fence,
    )
    binding = R.create_production_replay_binding(
        authority=final.binding,
        initial_admission=continuation.admission,
        final_admission=final,
        activity_policy_evaluator=prepared.bundle.evaluator,
        activity_store=activity_store,
        activity_policy_store=empty_policy,
        replay_store=replay_store,
        executor_actor=EXECUTOR,
    )
    machine = ScriptedPort(
        program=continuation.program_hash,
        host_abi=continuation.host_abi,
        opcodes=["LLM_EVAL"],
    )
    with pytest.raises(ActivityPolicyStoreViolation) as excinfo:
        R.resume_governed_replay(
            admission=continuation.admission,
            binding=binding,
            subjects=continuation.subjects,
            compiler=continuation.compiler,
            machines=(machine,),
            resumed_from_result_ref=R.replay_result_ref(result),
            **continuation.arguments,
        )
    assert excinfo.value.failure_code is ActivityPolicyStoreFailureCode.RECORD_UNKNOWN
    assert machine.channel is None and machine._index == 0


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
        R.create_production_replay_binding(
            authority=final.binding,
            initial_admission=prepared.admission,
            final_admission=final,
            activity_policy_evaluator=prepared.bundle.evaluator,
            activity_store=stores[0],
            activity_policy_store=stores[1],
            replay_store=stores[2],
            executor_actor=EXECUTOR,
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.ADMISSION_NOT_CURRENT


@pytest.mark.parametrize("foreign_context", ["attempt", "boundary"])
def test_durable_policy_decision_for_another_execution_context_is_refused(
    foreign_context: str,
) -> None:
    from synapse.experiments.gold import activity_policy as AP

    prompt = b"durable-policy-context"
    activity = recorded_llm_call(prompt=prompt)
    prepared, _ = scripted_prepared(
        ["LLM_EVAL"], activity_ids=(activity.activity_identity,), activities=(activity,)
    )
    result = run_scripted(
        prepared, opcodes=["LLM_EVAL"], on_step=consuming_step(prompt=prompt)
    )
    stored_request = prepared.bundle.replay_store.request_record(result.request_ref)
    reference = HashBoundRef.from_dict(
        stored_request["payload"]["activity_policy_decision_refs"][0]
    )
    decision = prepared.bundle.activity_policy_store.require_decision(
        reference, evaluator=prepared.bundle.evaluator
    )
    context = {
        "consumer_context_ref": decision.consumer_context_ref,
        "boundary_ref": decision.boundary_ref,
        "run_id": decision.run_id,
        "attempt_id": decision.attempt_id,
        "environment_profile_id": decision.environment_profile_id,
        "capability_profile_digest": decision.capability_profile_digest,
    }
    if foreign_context == "attempt":
        context["attempt_id"] = AttemptId("foreign-policy-attempt")
    else:
        context["boundary_ref"] = OTHER_BOUNDARY_REF
    with pytest.raises(AP.ActivityPolicyViolation) as excinfo:
        AP.require_consumable_activity_decision(
            decision,
            evaluator=prepared.bundle.evaluator,
            activity=activity,
            **context,
        )
    assert excinfo.value.failure_code is AP.ActivityPolicyFailureCode.DECISION_CONTEXT_MISMATCH


def test_governed_replay_resolves_durable_record_and_injects_exact_stored_bytes() -> None:
    from synapse.experiments.gold.activity_policy import activity_policy_decision_ref

    prompt = b"durable-exact-injection"
    activity = recorded_llm_call(prompt=prompt)
    prepared, _ = scripted_prepared(
        ["LLM_EVAL"], activity_ids=(activity.activity_identity,), activities=(activity,)
    )
    result = run_scripted(
        prepared, opcodes=["LLM_EVAL"], on_step=consuming_step(prompt=prompt)
    )
    assert result.status is R.ReplayStatus.REPLAY_IDENTICAL
    restored = prepared.bundle.activity_store.require_record(ACT.activity_ref(activity))
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
    from synapse.experiments.gold.activity_policy_store import FileActivityPolicyStore
    from synapse.experiments.gold.activity_store import FileActivityStore
    from synapse.experiments.gold.replay_store import FileReplayStore

    prompt = b"restart-exact-histories"
    activity = recorded_llm_call(prompt=prompt)
    prepared, _ = scripted_prepared(
        ["LLM_EVAL"], activity_ids=(activity.activity_identity,), activities=(activity,)
    )
    first = run_scripted(
        prepared, opcodes=["LLM_EVAL"], on_step=consuming_step(prompt=prompt)
    )
    continuation = prepare_for(prepared.units[0])
    final = WORLD.admission_request(continuation.core, continuation.extra)
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
    binding = R.create_production_replay_binding(
        authority=final.binding,
        initial_admission=continuation.admission,
        final_admission=final,
        activity_policy_evaluator=prepared.bundle.evaluator,
        activity_store=activity_store,
        activity_policy_store=policy_store,
        replay_store=replay_store,
        executor_actor=EXECUTOR,
    )
    terminal = ScriptedPort(
        program=continuation.program_hash,
        host_abi=continuation.host_abi,
        opcodes=["LLM_EVAL"],
    )
    terminal._index = 1
    again = R.resume_governed_replay(
        admission=continuation.admission,
        binding=binding,
        subjects=continuation.subjects,
        compiler=continuation.compiler,
        machines=(terminal,),
        resumed_from_result_ref=R.replay_result_ref(first),
        **continuation.arguments,
    )
    assert again.status is not R.ReplayStatus.REPLAY_INCOMPATIBLE
    assert again.recorded_activity_refs == first.recorded_activity_refs
    assert activity_store.require_record(again.recorded_activity_refs[0]).activity_identity == (
        activity.activity_identity
    )
    latest = replay_store.request_record(again.request_ref)
    for raw_ref in latest["payload"]["activity_policy_decision_refs"]:
        policy_store.require_decision(
            HashBoundRef.from_dict(raw_ref), evaluator=prepared.bundle.evaluator
        )


# ---------------------------------------------------------------------------
# §7.3 — the two opcodes whose determinism is not a property of the opcode
# ---------------------------------------------------------------------------
#
# ``CALL`` and ``CALL_METHOD`` execute an ordinary Python callable inline. The
# machine does it itself, without routing through the host, so the
# recorded-activity channel never sees the call: a replay could run
# uninstrumented code in the middle of an operation whose whole claim is that
# nothing unrecorded happens. The profile classifies them as dispatch-guarded
# and the adapter decides per dispatch, which is the only place the answer
# exists. These cases drive real dispatches rather than reading the profile.


def dispatching_adapter(instructions: list[dict], locals_: dict | None = None):
    """An adapter over a hand-built program, with the machine's locals seeded.

    The locals are reached directly. A behavior cannot put a Python callable
    into its own scope — that is the point — so the state the guard exists for is
    not reachable through any behavior, and the acceptance layer arranges it at
    the seam the guard actually reads.
    """

    program = BytecodeProgram.from_dict(
        {
            "type": "bytecode_program",
            "version": "2.2",
            "host_abi_version": "2.2",
            "program_hash": hashlib.sha256(json.dumps(instructions, sort_keys=True).encode()).hexdigest(),
            "constants": [],
            "guard_cleanup_table": [],
            "instructions": instructions,
        }
    )
    adapter = R.CognitiveVMReplayAdapter(program, gas_budget=GAS)
    adapter._vm.state.locals.update(locals_ or {})
    return adapter


def test_an_ordinary_python_callable_is_refused_before_it_is_called() -> None:
    """The refusal is pre-dispatch: the callee is still on the stack afterwards."""

    calls: list[int] = []
    adapter = dispatching_adapter(
        [
            {"op": "LOAD_NAME", "a": "helper", "b": None, "c": None},
            {"op": "CALL", "a": 0, "b": None, "c": None},
            {"op": "HALT", "a": None, "b": None, "c": None},
        ],
        {"helper": lambda: calls.append(1)},
    )
    adapter.step()
    with pytest.raises(R.ReplayViolation) as excinfo:
        adapter.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.UNGOVERNED_DISPATCH
    assert calls == [], "the callable ran before it was refused"
    assert callable(adapter._vm.state.stack[-1]), "the refusal disturbed the operand stack"


def test_an_ordinary_python_method_is_refused_before_it_is_called() -> None:
    """``CALL_METHOD`` reaches arbitrary Python by another route, and is closed too."""

    adapter = dispatching_adapter(
        [
            {"op": "LOAD_NAME", "a": "subject", "b": None, "c": None},
            {"op": "CALL_METHOD", "a": "upper", "b": 0, "c": None},
            {"op": "HALT", "a": None, "b": None, "c": None},
        ],
        {"subject": "a recorded string"},
    )
    adapter.step()
    with pytest.raises(R.ReplayViolation) as excinfo:
        adapter.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.UNGOVERNED_DISPATCH
    assert adapter._vm.state.stack[-1] == "a recorded string"


def test_a_compiled_synapse_function_still_dispatches() -> None:
    """The guard refuses arbitrary Python, not the machine's own control flow.

    A ``FunctionObject`` is an internal transition: the body it enters is the
    same governed program, and every effect inside it reaches the same channel.
    Refusing it would make the guard a ban on function calls.
    """

    from synapse.cvm import FunctionObject

    instructions = [
        {"op": "LOAD_NAME", "a": "behavior", "b": None, "c": None},
        {"op": "CALL", "a": 0, "b": None, "c": None},
        {"op": "HALT", "a": None, "b": None, "c": None},
    ]
    adapter = dispatching_adapter(
        instructions,
        {"behavior": FunctionObject(name="inner", params=[], body_ip=2, closure={})},
    )
    adapter.step()
    adapter.step()
    assert adapter._vm.state.ip == 2, "the machine did not enter the function body"


def test_a_dispatch_the_machine_would_route_to_its_host_is_left_to_the_channel() -> None:
    """A non-callable callee is a host route, and the host route is governed.

    Refusing it here would move a governed effect into an ungoverned refusal, so
    the guard lets it through and the channel answers — which with no channel
    attached is ``CHANNEL_CLOSED``, not ``UNGOVERNED_DISPATCH``.
    """

    adapter = dispatching_adapter(
        [
            {"op": "LOAD_NAME", "a": "not_a_callable", "b": None, "c": None},
            {"op": "CALL", "a": 0, "b": None, "c": None},
            {"op": "HALT", "a": None, "b": None, "c": None},
        ],
        {"not_a_callable": "a value"},
    )
    adapter.step()
    with pytest.raises(R.ReplayViolation) as excinfo:
        adapter.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.CHANNEL_CLOSED


# ---------------------------------------------------------------------------
# The checks do not run the code they are checking
# ---------------------------------------------------------------------------
#
# A guard that asks a value a question is running that value's code. The machine
# reprs whatever it does not recognise — in ``encode_vm_value`` for a snapshot
# and in ``_hash_transition`` for every step — and an ordinary attribute lookup
# invokes ``__getattribute__``, properties and descriptors. All three sit inside
# operations whose whole claim is that nothing unrecorded happens, and NR-03
# forbids repairing the first two from this layer. So the closed value
# vocabulary is what keeps such a value from ever reaching them.


class RecordingSubject:
    """A machine value that writes down every time the runtime asks it anything.

    Not a mock of a check: it is the shape of value the guards exist for. Each
    hook appends to ``touches``, so a case can assert the *absence* of execution
    rather than infer it from a refusal that may have come one step too late.
    """

    def __init__(self) -> None:
        object.__setattr__(self, "touches", [])

    def __getattribute__(self, name: str):
        if name != "touches":
            object.__getattribute__(self, "touches").append(("getattr", name))
        return object.__getattribute__(self, name)

    def __repr__(self) -> str:
        object.__getattribute__(self, "touches").append(("repr", None))
        return "<recording>"

    def upper(self) -> str:
        object.__getattribute__(self, "touches").append(("called", "upper"))
        return "x"


def test_refusing_a_method_dispatch_does_not_consult_the_subject() -> None:
    """The reproduction that opened this: the guard used to ask before refusing.

    ``getattr(subject, name, None)`` is a lookup, and a lookup is the subject's
    own code. The refusal arrived, but after ``__getattribute__`` had already
    run — so the guard against executing ungoverned code executed ungoverned
    code in order to decide.
    """

    subject = RecordingSubject()
    adapter = dispatching_adapter(
        [
            {"op": "LOAD_NAME", "a": "subject", "b": None, "c": None},
            {"op": "CALL_METHOD", "a": "upper", "b": 0, "c": None},
            {"op": "HALT", "a": None, "b": None, "c": None},
        ],
        {"subject": subject},
    )
    adapter.step()
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

    adapter = dispatching_adapter(
        [
            {"op": "LOAD_NAME", "a": "subject", "b": None, "c": None},
            {"op": "CALL_METHOD", "a": "upper", "b": 0, "c": None},
            {"op": "HALT", "a": None, "b": None, "c": None},
        ],
        {"subject": "a recorded string"},
    )
    adapter.step()
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
    adapter = dispatching_adapter([{"op": "HALT", "a": None, "b": None, "c": None}])
    adapter._vm.state.stack.append(subject)
    del subject.touches[:]
    with pytest.raises(R.ReplayViolation) as excinfo:
        adapter.snapshot_digest()
    assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE
    assert subject.touches == [], "the digest ran the value's own repr"


def test_a_transition_does_not_hash_a_value_that_would_hash_itself() -> None:
    """The same hazard once per step: ``_hash_transition`` reprs the stack top."""

    subject = RecordingSubject()
    adapter = dispatching_adapter([{"op": "POP", "a": None, "b": None, "c": None}])
    adapter._vm.state.stack.append(subject)
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

    from synapse.cvm import VMState

    _, program, _ = effect_fixture()
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.CognitiveVMReplayAdapter(
            program, gas_budget=GAS, state=VMState(stack=[RecordingSubject()])
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE

    with pytest.raises(R.ReplayViolation) as excinfo:
        R.CognitiveVMReplayAdapter(
            program, gas_budget=GAS, state=VMState(locals={"x": RecordingSubject()})
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE


def test_the_value_vocabulary_is_exact_and_not_merely_structural() -> None:
    """A subclass is the way around an ``isinstance`` check, so the check is exact.

    The machine's encoder tests with ``isinstance``, so a ``dict`` subclass whose
    ``items`` is user code, or a ``str`` subclass whose ``__str__`` is, passes it
    and then runs during serialization. Exact types are the only form of this
    check that cannot be subclassed around.
    """

    class SneakyDict(dict):
        def items(self):  # pragma: no cover - must never be reached
            raise AssertionError("the encoder consulted a subclass hook")

    class SneakyStr(str):
        pass

    for value in (SneakyDict(a=1), SneakyStr("x"), b"raw bytes", {1: "int key"}, object()):
        with pytest.raises(R.ReplayViolation) as excinfo:
            R.require_canonical_vm_value(value)
        assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE

    from synapse.cvm import FunctionObject

    for value in (None, True, 7, 1.5, "text", [1, "a"], (1,), {"k": [1, {"n": None}]},
                  FunctionObject(name="f", params=[], body_ip=0, closure={"c": 1})):
        R.require_canonical_vm_value(value)


def test_a_value_graph_too_deep_or_too_wide_is_refused_not_walked() -> None:
    """Fail-closed at the limit: what this cannot afford to check, it refuses.

    The encoder would recurse exactly as far, so a value this validator declines
    to walk is a value the machine could not have serialized either.
    """

    deep: object = "leaf"
    for _ in range(R._MAX_VM_VALUE_DEPTH + 2):
        deep = [deep]
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.require_canonical_vm_value(deep)
    assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE

    wide = list(range(R._MAX_VM_VALUE_NODES + 2))
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.require_canonical_vm_value(wide)
    assert excinfo.value.failure_code is R.ReplayFailureCode.RESOURCE_LIMIT_EXCEEDED


def test_a_hostile_value_hidden_inside_a_canonical_container_is_still_refused() -> None:
    """``repr`` of a list is the ``repr`` of its elements, so the check goes deep."""

    subject = RecordingSubject()
    del subject.touches[:]
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.require_canonical_vm_value({"outer": [{"inner": subject}]})
    assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE
    assert subject.touches == []


def test_an_attempt_that_raises_after_its_request_is_still_recorded() -> None:
    """NR-13: every attempt is preserved, and a raise is still an attempt.

    Once the request is durable this run happened. A raise between the request
    append and the result append leaves a history in which a run started and,
    to any later reader, never finished — which is exactly the shape a run that
    was allowed to start unrecorded would leave, arrived at from the other end.

    ``GAS_NOT_MONOTONE`` is the case that shows it, because it is deliberately
    raised rather than turned into a result: gas that increases is not the
    modelled cost function, so it is not an execution outcome to be reported.
    The exception still travels — the caller asked for a run and did not get one
    — and the record exists either way.
    """

    prepared, _ = scripted_prepared(["ADD", "SUB"])
    store = prepared.bundle.replay_store
    requests_before = len(store.recorded_request_refs())
    results_before = len(store.recorded_result_refs())

    with pytest.raises(R.ReplayViolation) as excinfo:
        run_scripted(prepared, opcodes=["ADD", "SUB"], gas_after=lambda gas: gas + 1)
    assert excinfo.value.failure_code is R.ReplayFailureCode.GAS_NOT_MONOTONE

    assert len(store.recorded_request_refs()) == requests_before + 1
    assert len(store.recorded_result_refs()) == results_before + 1, (
        "the attempt left an orphan request with no outcome"
    )
    recorded = store.require_result(store.recorded_result_refs()[-1])
    assert recorded.status is R.ReplayStatus.INFRA_ERROR
    assert recorded.failure_reason is R.ReplayFailureReason.MACHINE_FAULT
    assert recorded.request_ref.to_dict() == store.recorded_request_refs()[-1].to_dict(), (
        "the recorded outcome does not name the request this attempt started from"
    )


def test_a_recorded_infra_error_is_not_a_replay_verdict() -> None:
    """§26 keeps INFRA_ERROR apart from a failure, and the record keeps it apart too.

    A reader of the history must be able to tell "the executor broke" from "the
    behaviour diverged". Both are recorded; they are not the same status and the
    infrastructure one carries no observations to mistake for evidence.
    """

    prepared, _ = scripted_prepared(["ADD", "SUB"])
    store = prepared.bundle.replay_store
    with pytest.raises(R.ReplayViolation):
        run_scripted(prepared, opcodes=["ADD", "SUB"], gas_after=lambda gas: gas + 1)
    recorded = store.require_result(store.recorded_result_refs()[-1])
    assert recorded.status is not R.ReplayStatus.REPLAY_FAILED
    assert recorded.status is not R.ReplayStatus.REPLAY_IDENTICAL
    assert recorded.observations == ()
    assert recorded.transition_hash_chain == ()
    R.validate_replay_result(recorded)


# ---------------------------------------------------------------------------
# The budget is a promise, and the codec is a rule
# ---------------------------------------------------------------------------


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
    """Refusing everything expensive is not the fix; the arithmetic has to be right."""

    from synapse.cvm import GAS_COSTS

    activity = recorded_llm_call()
    cost = GAS_COSTS["LLM_EVAL"]
    prepared, transitions = scripted_prepared(
        ["LLM_EVAL"],
        activities=(activity,),
        # The contract has to expect the activity this run consumes, or the
        # transcript root disagrees for a reason that has nothing to do with gas.
        activity_ids=(activity.activity_identity,),
        gas_budget=cost,
    )
    result = run_scripted(
        prepared, opcodes=["LLM_EVAL"], on_step=consuming_step(),
        gas_after=lambda gas: gas - cost,
    )
    assert result.status is R.ReplayStatus.REPLAY_IDENTICAL
    assert result.steps_executed == 1
    assert result.transition_hash_chain == transitions


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

    prepared, _ = scripted_prepared(["ADD", "SUB", "MUL"], gas_budget=1_000)
    result = run_scripted(prepared, opcodes=["ADD", "SUB", "MUL"], gas=2)
    assert result.status is R.ReplayStatus.REPLAY_FAILED
    assert result.failure_reason is R.ReplayFailureReason.GAS_EXHAUSTED
    assert result.status is not R.ReplayStatus.INFRA_ERROR
    assert result.steps_executed < 3, "the machine executed past the gas it held"


def test_the_result_codec_is_enforced_and_not_merely_declared() -> None:
    """JSON has many spellings of one value; an identity must have one.

    Every byte string below parses, and each has a different digest and therefore
    a different activity identity. Accepting them would let two identities name
    one injected value — the collision activity identity exists to prevent, run
    backwards. So the bytes must be the ones this codec would have produced.
    """

    assert R.ACTIVITY_RESULT_CODEC_V1 == ACT.ACTIVITY_RESULT_CODEC_V1, (
        "the codec is declared twice and the two spellings can fork"
    )
    for raw in (b" 1 ", b'{"b":1,"a":2}', b"[1,  2]", b'{ "a": 1 }'):
        with pytest.raises(R.ReplayViolation) as excinfo:
            R.decode_recorded_result(raw)
        assert excinfo.value.failure_code is R.ReplayFailureCode.RESULT_NOT_DECODABLE

    for value in (1, "text", None, True, [1, 2], {"a": 1, "b": 2}):
        raw = R.encode_recorded_result(value)
        assert R.decode_recorded_result(raw) == value
        assert R.encode_recorded_result(R.decode_recorded_result(raw)) == raw


def test_a_non_canonical_recorded_result_stops_the_replay() -> None:
    """And it stops it at consumption, where the bytes become a machine value."""

    _, program, records = effect_fixture()
    payload = dict(records[0]["payload"])
    sloppy = b'{ "b": 2, "a": 1 }'
    activity = governed_activity(
        kind=ACT.ActivityKind(payload["kind"]),
        inputs=ACT.ActivityInputs.from_dict(payload["inputs"]),
        position=ACT.ActivityPosition.from_dict(payload["position"]),
        policy_version=payload["policy_version"],
        result=sloppy,
    )
    channel = channel_for(activity, budget=8)
    adapter = R.CognitiveVMReplayAdapter(program, gas_budget=GAS)
    adapter.attach_channel(channel)
    with pytest.raises(R.ReplayViolation) as excinfo:
        for _ in range(10):
            adapter.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.RESULT_NOT_DECODABLE
