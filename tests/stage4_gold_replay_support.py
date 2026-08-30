"""Shared fixtures for the independent Stage 4 Patch 9 replay shards.

The stage's required checks cover exact transition
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

This module contains no tests. The replay scenarios and every mandatory mutant
killer live in separate test files so CI can schedule the heavy groups in
parallel.
"""

from __future__ import annotations

import copy

from datetime import datetime, timezone

import hashlib

import json

from pathlib import Path

from types import SimpleNamespace

import pytest

from dataclasses import dataclass

from synapse.bytecode import BytecodeProgram, Instruction

from synapse.cvm import GAS_COSTS, VMState

from synapse.experiments.gold import activities as ACT

from synapse.experiments.gold import admission as A

from synapse.experiments.gold import replay as R

from synapse.experiments.gold import replay_composition as RC

from synapse.experiments.gold import replay_store as R_STORE

from synapse.experiments.gold import replay_structural_history as RSH

from synapse.experiments.gold import replay_vm_codec as RVC

from synapse.experiments.gold import replay_vm_adapter as RVM

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

R_RESULT = RVC.encode_recorded_result("answer")

GOLDEN_EFFECT_RESULT = RVC.encode_recorded_result("the recorded model answer")

MACHINE_CONTEXT = R.replay_machine_execution_context(
    run_id=RunId("point-of-use-run"),
    attempt_id=AttemptId("point-of-use-attempt"),
    repository_revision=RepositoryRevision.git_commit("a" * 40),
    environment_profile_id="production-point-of-use",
    policy_version=POLICY,
)

MACHINE_FACTORY = RVM.CognitiveVMReplayMachineFactory()

def vm_adapter(program: BytecodeProgram, *, gas: int = GAS, state=None):
    return RVM.CognitiveVMReplayAdapter(
        program, gas_budget=gas, execution_context=MACHINE_CONTEXT, _state=state
    )

def restore_vm_adapter(snapshot, *, gas: int = GAS):
    raw = snapshot if type(snapshot) is bytes else json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return MACHINE_FACTORY.restore(
        raw, gas_budget=gas, execution_context=MACHINE_CONTEXT
    )

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

_POLICY_BUNDLES: dict = {}

_DECISIONS: dict = {}

EVALUATOR_IDENTITY = AuthorityIdentity("stage9-activity-policy-evaluator")

ACTORS = {
    "producer_actor": ActorIdentity("stage9-activity-producer"),
    "recorder_actor": ActorIdentity("stage9-activity-recorder"),
    "worker_actor": ActorIdentity("stage9-worker"),
    "model_actor": ActorIdentity("stage9-model"),
    "replay_executor_actor": EXECUTOR,
    "machine_adapter_actor": ActorIdentity("stage9-machine-adapter"),
    "consumer_actor": ActorIdentity("stage9-consumer"),
}

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
    actor_set = AP.create_activity_policy_actor_set(authority_handle=handle, **ACTORS)
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

RECORD_CONTEXT = ACT.ActivityRecordContext(
    run_id=RunId("point-of-use-run"),
    attempt_id=AttemptId("point-of-use-attempt"),
    repository_revision=RepositoryRevision.git_commit("a" * 40),
    environment_profile_id="production-point-of-use",
    producer_component="stage9-activity-recorder",
)

def _fixture_activity_entitlement(*, kind, inputs, position, result, result_ref):
    """Build one subject-bound fixture entitlement for raw driver probes only.

    Governed capture/run paths never use this helper: ``Prepared`` records their
    observed activities through ``RC.record_observed_activity``.  Raw channel and
    codec probes still need an immutable record without performing a production
    ingress, and each fixture receives its own occurrence-bound entitlement.
    """

    from synapse.experiments.gold import activity_policy as AP
    from synapse.experiments.gold import activity_provenance as APR

    evaluator = policy_bundle().evaluator
    production = APR.record_activity_production_provenance(
        evaluator.provenance_authority,
        kind=kind,
        inputs=inputs,
        position=position,
        result=result,
        result_ref=result_ref,
        context=RECORD_CONTEXT,
    )
    return AP.issue_activity_recorder_entitlement(
        evaluator,
        production=production,
    )

def governed_activity(
    *,
    kind=None,
    inputs=None,
    position=None,
    result: bytes = b"",
    policy_disposition=ACT.ActivityDisposition.RECORDED_CONSUMABLE,
) -> ACT.RecordedActivity:
    """One record fixture for channel/codec probes and replay-contract identity.

    The production path replaces it with the exact record returned by
    ``RC.record_observed_activity`` before capture or replay.  The two records
    share their occurrence identity; only the production record is durable.
    """

    from synapse.experiments.gold.activity_store import activity_result_ref

    actual_kind = kind or ACT.ActivityKind.LLM_CALL
    result_ref = activity_result_ref(result)
    record = ACT.record_activity(
        kind=actual_kind,
        inputs=inputs,
        position=position,
        result=result,
        result_ref=result_ref,
        context=RECORD_CONTEXT,
        entitlement=_fixture_activity_entitlement(
            kind=actual_kind,
            inputs=inputs,
            position=position,
            result=result,
            result_ref=result_ref,
        ),
    )
    _RESULT_BYTES[record.result_sha256] = result
    _POLICY_DISPOSITIONS[record.activity_identity] = policy_disposition
    return record

from tests.gold_point_of_use_world import ARTIFACTS  # noqa: E402

def llm_artifact_program(prompt: str) -> BytecodeProgram:
    """A real program that performs one recorded LLM call and halts.

    Three instructions and no inline IR anywhere. ``LLM_EVAL`` takes its operands
    from the instruction itself, so what the machine asks the channel for is
    decided by this program and by nothing at the call site; the result the
    channel serves is pushed, popped and the machine halts.
    """

    from synapse.bytecode import Instruction

    return BytecodeProgram(
        instructions=[
            Instruction("LLM_EVAL", a=prompt, b=None),
            Instruction("POP"),
            Instruction("HALT"),
        ],
        constants=[],
    )

def llm_artifact_behavior(prompt: str = "explain the artifact"):
    """A published behaviour whose program is a durable artifact that calls out.

    This is the shape §23 was always about and that Stage 9 could not express.
    Inline IR in this repository performs no external effect, so every case about
    a recorded activity had to be staged on a scripted machine — which proves
    that the harness can call a channel, not that a governed replay does.

    Here the program is published into the artifact store first, the behaviour
    names it by hash-bound reference, its declared capabilities are exactly the
    ones its opcodes require, and its replay contract is the transcript the exact
    ``CognitiveVMReplayAdapter`` produces over it. Returned with the recorded
    activity that run will consume, whose inputs and position are the ones the
    machine will actually present — computed from the program, not asserted.

    The result bytes are not published here. Which store they belong in is a
    property of the run, so the prepared run puts them where it will look.
    """

    program = llm_artifact_program(prompt)
    reference = ARTIFACTS.publish(
        json.dumps(program.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    )

    payload = json.loads(VECTORS.read_text(encoding="utf-8"))["vectors"][0]["core"]
    payload = copy.deepcopy(payload)
    payload["canonical_program"] = {
        "form": "ARTIFACT_REF_V1",
        "artifact_ref": reference.to_dict(),
    }
    payload["artifact_refs"] = [reference.to_dict()]
    payload["capability_requirements"] = list(R.capabilities_required_by(program))
    core = BehaviorCore.from_dict(payload)

    # The activity the run will look for: the machine asks by opcode and by the
    # instruction's own operands, at the instruction pointer it is standing on.
    activity = governed_activity(
        kind=ACT.ActivityKind.LLM_CALL,
        inputs=ACT.activity_inputs(
            opcode=b"LLM_EVAL",
            operand_a=RVC.encode_recorded_result(prompt),
            operand_b=RVC.encode_recorded_result(None),
        ),
        position=ACT.ActivityPosition(
            program_hash=program.program_hash,
            instruction_pointer=0,
            frame_depth=0,
            sequence=1,
        ),
        result=R_RESULT,
    )

    # Two passes, as for any real behaviour: the transcript is what the exact
    # adapter produces, and it cannot be written down in advance.
    resolved = program
    machine = vm_adapter(resolved)
    machine.attach_channel(_ScriptedActivityChannel(activity))
    seen = []
    while not machine.is_halted() and machine.next_opcode() is not None:
        machine.step()
        seen.append(machine.transition_hash())

    unit = create_behavior_unit(
        behavior_kind=core.behavior_kind,
        canonical_program=core.canonical_program,
        input_contract=core.input_contract,
        output_contract=core.output_contract,
        capability_requirements=core.capability_requirements,
        replay_contract=contract_for(tuple(seen), (activity.activity_identity,)),
        verification_contract=core.verification_contract,
        binding_refs=core.binding_refs,
        source_evidence_refs=core.source_evidence_refs,
        artifact_refs=core.artifact_refs,
    )
    return unit, activity

class _ScriptedActivityChannel:
    """The fixture channel the *probe* pass runs against, and only that pass.

    Building the contract needs the transcript the real adapter produces, and
    producing it needs the recorded result the run will later be served. This
    stands in for that one pass. It is a fixture, it is never attached to a
    governed run — the seal on the real channel is what stops that — and the
    bytes it serves are the same bytes the durable store will hold.
    """

    def __init__(self, activity) -> None:
        self._activity = activity
        self._seal = R._CHANNEL_SEAL

    def resolve(self, *, kind, inputs, position):
        assert kind is self._activity.kind, "the probe was asked for another activity kind"
        return self._activity

    def open_result(self, record) -> bytes:
        return _RESULT_BYTES[record.result_sha256]

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
        self._durable_activities = None

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

    @property
    def artifact_reader(self):
        return WORLD.artifact_reader(self.core, self.extra)

    def _record_observed_activities(self, binding):
        if self._durable_activities is None:
            observed = []
            for fixture in self.activities:
                raw = _RESULT_BYTES[fixture.result_sha256]
                durable = RC.record_observed_activity(
                    binding=binding,
                    kind=fixture.kind,
                    inputs=fixture.inputs,
                    position=fixture.position,
                    result=raw,
                )
                assert durable.activity_identity == fixture.activity_identity
                observed.append(durable)
            self._durable_activities = tuple(observed)
        return self._durable_activities

    def _governed(self) -> dict:
        bundle = self.bundle
        final_admission = WORLD.admission_request(self.core, self.extra)
        # The exact-type check on the replay history, asked here because here is
        # the composition root. ``replay.py`` owns the history contract and may
        # not import the adapter that implements it, so the exactness is asserted
        # by the party that imports both — which, for a governed run assembled in
        # this suite, is this method.
        R_STORE.require_production_replay_store(bundle.replay_store)
        replay_binding = RC.create_production_replay_binding(
            authority=final_admission.binding,
            initial_admission=self.admission,
            final_admission=final_admission,
            activity_policy_evaluator=bundle.evaluator,
            activity_store=bundle.activity_store,
            activity_policy_store=bundle.activity_policy_store,
            replay_store=bundle.replay_store,
            artifact_reader=self.artifact_reader,
        )
        activities = self._record_observed_activities(replay_binding)
        self._last_binding = replay_binding
        return {
            "binding": replay_binding,
            "activity_refs": tuple(ACT.activity_ref(item) for item in activities),
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

    def manifest_ref(self, store, *, initial=None, resumed_from=None, authority=None):
        """Take the reference capture for this run, and publish its manifest.

        Two governed operations, in the order production has: a reference
        execution observes what the admitted programs actually do, and a manifest
        authority issues the expected outcome from that durable observation. The
        acceptance layer plays the operator preparing an attempt — it does not
        state any expected value, because there is no longer an argument for one.

        ``resumed_from`` makes this a continuation: the starting states are the
        predecessor's own durable terminal snapshots, named by the exact
        references its observations recorded, so the reference run restores them
        rather than recomputing them.

        Both writes happen *before* the attempt's final admission is taken.
        Appending to a Stage 9 store opens a mutation interval on the shared
        coordinator, so anything written afterwards would advance the epoch that
        admission settled at and the run would correctly refuse itself.
        """

        del initial  # the reference run builds its own machines from the admitted programs
        capture_admission = WORLD.admission_request(self.core, self.extra)
        capture_final = WORLD.admission_request(self.core, self.extra)
        # One executor for both phases. §9.4 names seven actors and a reference
        # run adds no eighth: the same replay executor takes the reference run and
        # the run it is later measured against, and the two are told apart by
        # phase and by record identity. What must be separate is the authority
        # that seals the capture, which is not an executor at all.
        capture_binding = RC.create_production_replay_binding(
            authority=(capture_final.binding if authority is None else authority),
            initial_admission=capture_admission,
            final_admission=capture_final,
            activity_policy_evaluator=self.bundle.evaluator,
            activity_store=self.bundle.activity_store,
            activity_policy_store=self.bundle.activity_policy_store,
            replay_store=store,
            artifact_reader=self.artifact_reader,
        )
        activities = self._record_observed_activities(capture_binding)
        capture_authority = RC.create_reference_capture_authority(binding=capture_binding)
        arguments = self._run_arguments()
        capture_ref = RC.capture_reference_replay(
            admission=capture_admission,
            binding=capture_binding,
            subjects=self.subjects,
            compiler=self.compiler,
            activity_refs=tuple(ACT.activity_ref(item) for item in activities),
            capture_authority=capture_authority,
            # The reference run is taken under budgets that let it finish. A case
            # that starves the run it is judging starves the run, not the
            # observation the run is judged against.
            gas_budget=max(int(arguments["gas_budget"]), GAS),
            cognitive_budget=max(int(arguments["cognitive_budget"]), 8),
            step_limit=max(int(arguments["step_limit"]), 1_000),
            resumed_from_result_ref=(
                None if resumed_from is None else R.replay_result_ref(resumed_from)
            ),
        )
        return RC.publish_replay_manifest(
            # The same binding the capture was taken through: its executor is the
            # one that will consume the manifest, which is exactly what the
            # authority independence check is about. A second binding here would
            # be two more admissions for no additional evidence.
            binding=capture_binding,
            capture_authority=capture_authority,
            capture_ref=capture_ref,
        )

    def _run_arguments(self) -> dict:
        """The run parameters the public entry points still take.

        The expected values and the executor are no longer among them: they come
        from the manifest and from the binding. Dropping them quietly is how a
        case can go on *stating* an expectation that nothing reads, and pass for
        a reason it never tested — so a case that still supplies one is refused
        here rather than silently ignored. The record-level cases that need such
        a value build a request with ``request()``, which does take them.
        """

        arguments = dict(self.arguments)
        stale = [
            name
            for name in (
                "expected_transcript_root",
                "expected_terminal_snapshot_digests",
                "executor_actor",
            )
            if name in arguments
        ]
        if stale:
            raise AssertionError(
                "the governed path takes no "
                + ", ".join(stale)
                + ": the expected values come from the manifest and the executor "
                "from the binding"
            )
        return arguments

    def run(self, *, governed=None):
        """The production path. It builds its own machines from the manifest.

        ``governed`` is for the cases that deliberately prepare a damaged world —
        a record withheld, a blob removed — and therefore have to build the
        binding themselves before the run reaches it. Everything else lets the
        run assemble its own.
        """

        reference = self.manifest_ref(self.bundle.replay_store)
        governed = self._governed() if governed is None else dict(governed)
        return RC.run_governed_replay(
            admission=self.admission,
            subjects=self.subjects,
            compiler=self.compiler,
            manifest_ref=reference,
            **governed,
            **self._run_arguments(),
        )

    def resume(self, *, resumed_from):
        """The production continuation path. It takes no machine at all.

        There is nothing left for a caller to supply: the starting state is the
        predecessor's own durable terminal snapshot, named by the exact reference
        its observations recorded and restored from those bytes. A case that
        wants to resume from some other state has to make that state a durable
        record first, which is the point — a continuation attaches to what
        happened, not to an object assembled at the call site.
        """

        reference = self.manifest_ref(
            self.bundle.replay_store, resumed_from=resumed_from
        )
        governed = self._governed()
        return RC.resume_governed_replay(
            admission=self.admission,
            subjects=self.subjects,
            compiler=self.compiler,
            manifest_ref=reference,
            resumed_from_result_ref=R.replay_result_ref(resumed_from),
            binding=governed["binding"],
            **self._run_arguments(),
        )

    def request(self):
        """The request this run would produce, for cases whose subject is the record.

        Reaches the private constructor on purpose. A validator, an identity or a
        consistently forged record is a property of the *record*, and there is no
        public way to obtain one without also running it — which is the point of
        the repair. The acceptance layer may look at the record; nothing in
        production may.
        """

        # In the order production has: the manifest is durable before the request
        # that names it exists, because a request carries the reference and a
        # reference to a record nobody wrote is not a reference.
        reference = self.manifest_ref(self.bundle.replay_store)
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
            execution_manifest_ref=reference,
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

def pure_adapter(gas: int = GAS) -> RVM.CognitiveVMReplayAdapter:
    _, binding = pure_behavior()
    return vm_adapter(binding.program, gas=gas)

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

    unit, _binding = pure_behavior()
    # No expected values. They used to be read off the golden fixture and handed
    # to the run, which made the acceptance layer the party stating what the run
    # should produce. They now come from a manifest the reference execution
    # issued, and ``_run_arguments`` refuses to be given one — a case that needs
    # a stated expectation asks the rule directly instead.
    return prepare_for(unit, **overrides)

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

    def gas_remaining(self) -> int:
        return self._gas

    def is_halted(self) -> bool:
        return self._index >= len(self._opcodes)

    def next_opcode(self):
        if self._index >= len(self._opcodes):
            return None
        return self._opcodes[self._index]

    def next_step_gas_cost(self) -> int:
        opcode = self.next_opcode()
        return 0 if opcode is None else GAS_COSTS.get(opcode, 1)

    def snapshot_digest(self) -> str:
        return hashlib.sha256(f"{self._seed}:{self._program}:{self._index}".encode()).hexdigest()

    def snapshot_bytes(self) -> bytes:
        """A scripted machine's state, as the bytes a manifest would record.

        Synthetic, like the rest of this port. It is never restored from — the
        seam is handed the port itself — but a manifest names a durable starting
        state for every machine, and a port that could not state one would be a
        port these cases could not write a manifest for.
        """

        return json.dumps(
            {"scripted": True, "program": self._program, "index": self._index},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")

    def structural_history_bytes(self) -> bytes:
        return RSH.encode_replay_structural_history(
            (),
            profile_id=R.REPLAY_CAPABILITY_PROFILE_V1_E1,
            profile_digest=R.capability_profile_digest(),
        )

    def structural_history_complete(self) -> bool:
        return True

    def step(self) -> None:
        gas_cost = self.next_step_gas_cost()
        opcode = self._opcodes[self._index]
        if self._on_step is not None:
            self._on_step(self, opcode)
        self._index += 1
        if self._hash_script is not None:
            self._hash = self._hash_script[self._index - 1]
        else:
            self._hash = "sha256:" + hashlib.sha256(f"{opcode}:{self._index}".encode()).hexdigest()
        self._gas = self._gas_after(self._gas) if self._gas_after else self._gas - gas_cost

def scripted_transitions(opcodes: list[str]) -> tuple[str, ...]:
    port = ScriptedPort(program="sha256:scripted", opcodes=opcodes)
    seen = []
    while not port.is_halted():
        port.step()
        seen.append(port.transition_hash())
    return tuple(seen)

def real_behavior(literal: int, *, activity_ids: tuple[str, ...] = ()):
    """A published behaviour whose replay contract is what its program does.

    Built in two passes because the contract lives inside the core while the
    program does not depend on it: a probe unit is compiled to get the program,
    the exact adapter is driven over it, and the transcript that run produced
    becomes the contract of the unit actually published.

    This replaces the older habit of writing a contract out of a scripted port's
    transcript. Such a contract described a machine nobody would run, so a
    reference execution on the real adapter could never reproduce it — and
    telling two behaviours apart by their invented transcripts is not telling
    them apart at all. Distinct ``literal`` values give genuinely distinct
    programs, which is the difference these cases are about.
    """

    probe = unit_with(contract_for(("0" * 64,)), literal=literal)
    program = compile_behavior_unit(probe).program
    machine = vm_adapter(program)
    seen = []
    while not machine.is_halted() and machine.next_opcode() is not None:
        machine.step()
        seen.append(machine.transition_hash())
    return unit_with(contract_for(tuple(seen), activity_ids), literal=literal)

def scripted_prepared(opcodes: list[str], *, activity_ids: tuple[str, ...] = (), **overrides):
    """A run over a real behaviour, identified by what its own program does.

    ``opcodes`` no longer decides the behaviour's contract — it decides which
    behaviour this is. A distinct opcode list yields a distinct ``literal``,
    hence a genuinely distinct program, and the contract is the transcript that
    program actually produces. Writing the contract out of a scripted port's
    transcript, as this helper used to, described a machine nobody would run:
    a reference execution on the real adapter could never reproduce it, and two
    behaviours "differing" only in invented transcripts did not differ at all.

    The scripted port is still how a case reaches the anomalies a correct
    machine never produces — see ``run_scripted``, which drives the transition
    driver with one. What changed is that the *behaviour* is real, so the
    governed path can prepare it.
    """

    # A stable digest, not ``hash()``. Python salts ``hash()`` per process, so the
    # literal — and therefore the program, its content key and every identity
    # derived from them — differed between runs and could collide across opcode
    # lists. A fixture that cannot name the same behaviour twice is not a fixture.
    digest = hashlib.sha256("\x00".join(opcodes).encode("utf-8")).hexdigest()
    literal = 1000 + int(digest[:8], 16) % 8000
    unit = real_behavior(literal, activity_ids=activity_ids)
    transitions = unit.core.replay_contract.expected_transition_ids
    arguments = dict(gas_budget=1_000)
    arguments.update(overrides)
    return prepare_for(unit, **arguments), transitions

def run_scripted(prepared: Prepared, **port_kwargs):
    """Drive a scripted transcript through the transition driver itself.

    A ``ScriptedPort`` exists to produce transcripts a correct machine never
    produces — a fault, gas that increases, an unclassified opcode, a program
    other than the bound one. Those are properties of the *driver*: of how it
    classifies, charges and refuses what a machine does, one transition at a
    time. They were reached through an executor seam that ran the whole governed
    path around them, which cost every such case an admission, a reference
    capture and a manifest for evidence none of them were about.

    They are asked of the driver directly now. That is not a weaker test of the
    same thing — it is the same test without the apparatus, and it became
    possible when the driver was extracted so that a reference capture and a
    governed replay could not have two execution semantics. Everything the
    governed path adds around it — the receipt, the durable request, the
    manifest comparison — is covered by the cases that are about those things.

    Returns the driver's own output, so a case reads ``failure_reason``,
    ``transition_hash_chain`` and the rest directly rather than through a sealed
    result. Where a case is about the §23 status, it maps the reason with
    ``status_for_reason`` — the mapping has its own cases.
    """

    governed = prepared._governed()
    binding = governed["binding"]
    prep = R._prepare_replay(
        admission=prepared.admission,
        binding=binding,
        subjects=prepared.subjects,
        compiler=prepared.compiler,
        activity_refs=governed["activity_refs"],
    )
    # The bound program and ABI by default, because most cases are not about
    # those; a case that *is* — a substituted program, a foreign ABI — overrides
    # them, which is exactly the machine misbehaviour it exists to produce.
    port_kwargs.setdefault("program", prepared.program_hash)
    port_kwargs.setdefault("host_abi", prepared.host_abi)
    port = ScriptedPort(**port_kwargs)
    channel = R.RecordedActivityChannel(
        prep.ledger,
        prepared.arguments["cognitive_budget"],
        binding.activity_store,
        _seal=R._CHANNEL_SEAL,
    )
    try:
        incompatible = R._check_execution_contract(prep.bindings[0], port)
        if incompatible is not None:
            return _DriverRefusal(incompatible, port=port)
        port.attach_channel(channel)
        return R._drive_one_behavior(
            binding=prep.bindings[0],
            machine=port,
            channel=channel,
            gas_budget=prepared.arguments["gas_budget"],
            step_limit=prepared.arguments["step_limit"],
        )
    finally:
        channel.close()

@dataclass(frozen=True)
class _DriverRefusal:
    """A refusal made before the driver was reached, shaped like its output.

    The execution contract is checked before a machine is allowed to see the
    channel, so a case about a substituted program never reaches a transition.
    Reporting that as the driver's own stopping reason keeps the two kinds of
    refusal readable side by side without pretending a run happened.
    """

    failure_reason: R.ReplayFailureReason
    transition_hash_chain: tuple = ()
    consumed_activity_identities: tuple = ()
    steps_executed: int = 0
    transcript_matched: bool = False
    first_unexpected_index: int | None = None
    #: The port the refusal was made about, so a case can state what the refusal
    #: prevented rather than only that it happened.
    port: object = None

def assert_contract_rejected(run) -> None:
    """The behavior's own contract refused this transcript.

    Asserted on the driver's raw output, which is where the comparison happens.
    It used to unwrap a sealed result's single observation, from the days when
    these cases reached the driver through the governed path; that path no longer
    accepts a scripted machine, and the fact under test was never the result's —
    the result's reason is also reachable from the pinned-root comparison, so a
    result-level assertion alone would pass even if the contract comparison did
    nothing at all.
    """

    assert not run.transcript_matched, "the contract accepted this transcript"
    assert run.failure_reason is R.ReplayFailureReason.TRANSITION_MISMATCH
    assert R.status_for_reason(run.failure_reason) is R.ReplayStatus.REPLAY_FAILED

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
    """Reach the channel from a scripted machine, the way a real one would.

    What this establishes is only that the effect is *routed*: the scripted port
    asks the channel for a recorded activity instead of calling out. It is not an
    injection test and does not pretend to be one. A scripted machine has no
    stack to put a served value on, so the case would be asserting that the
    harness can hold bytes — which proves nothing about a replay.

    The injection claim is made where it can actually be made:
    ``test_governed_replay_resolves_durable_record_and_injects_exact_stored_bytes``
    runs an admitted artifact program on the exact CognitiveVM, and the recorded
    bytes reach the machine's own stack or the run does not complete.

    An earlier revision took a ``resolved`` list to collect what came back, and
    no case ever passed one — a mechanism for a claim nobody made.
    """

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
        result=GOLDEN_EFFECT_RESULT,
    )
    _RESULT_BYTES[record.result_sha256] = GOLDEN_EFFECT_RESULT
    return record

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

def effect_run(result: bytes, *, budget: int = 8):
    """The golden effect program, driven to its halt over one recorded result."""

    _, program, records = effect_fixture()
    payload = dict(records[0]["payload"])
    activity = governed_activity(
        kind=ACT.ActivityKind(payload["kind"]),
        inputs=ACT.ActivityInputs.from_dict(payload["inputs"]),
        position=ACT.ActivityPosition.from_dict(payload["position"]),
        result=result,
    )
    channel = channel_for(activity, budget=budget)
    adapter = vm_adapter(program)
    adapter.attach_channel(channel)
    digests = []
    while not adapter.is_halted() and adapter.next_opcode() is not None:
        adapter.step()
        digests.append(adapter.snapshot_digest())
    return activity, channel, tuple(digests)

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

def dispatching_adapter(instructions: list[dict], stack: list[object] | None = None):
    """An adapter over a hand-built program, with dispatch operands seeded.

    A behavior cannot put a Python callable into its own stack. The acceptance
    layer therefore arranges the operand at the exact seam the guard reads; it
    does not first execute ``LOAD_NAME``, whose transition hash would itself
    inspect a hostile value before the dispatch guard owns it.
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
    state = VMState(gas_remaining=GAS)
    adapter = vm_adapter(program, state=state)
    state.stack.extend(stack or [])
    return adapter, state

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

def _governed_driver_raising(monkeypatch, code: "R.ReplayFailureCode") -> None:
    """Make the *governed* transition driver raise, and only the governed one.

    The reference capture reaches ``_drive_one_behavior`` through its own import,
    so replacing the owner's binding leaves the preparation phase on the real
    driver: the manifest is issued from an execution that actually happened, and
    what breaks is the attempt being measured against it. That ordering is the
    whole point — the request has to be durable before anything raises, or the
    case would be about a run that never started.
    """

    def raising(**_kwargs):
        raise R._fail(code, "the machine reported gas that increased")

    monkeypatch.setattr(R, "_drive_one_behavior", raising)

__all__ = tuple(name for name in globals() if not name.startswith('__'))
