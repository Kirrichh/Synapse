"""Stage 4 OD-10 — the raw reference execution. An adapter, and nothing else.

An adapter of ``replay.py``. What it does is run admitted programs on the exact
machine and report what happened; what it decides is nothing.

That distinction is the whole of this revision. The previous file was an adapter
by declaration and an owner by behaviour: it held the authority position that may
seal a capture, the rules deciding whether a capture may become a manifest, the
assembly of the capture record itself, and the orchestration of the durable
writes around all of it — reaching into two dozen of the owner's symbols,
including a dozen private ones, to do so. Every one of those is a statement about
what a replay record *is*, which is the owner's single responsibility, and an
adapter that makes them is a second owner wearing an adapter's name.

Three operations remain, and each returns a raw fact:

* build the machines a fresh reference run starts from, out of admitted programs;
* restore the machines a continuation starts from, out of durable snapshot bytes;
* drive the admitted set once and report one ``_TransitionRun`` per behaviour.

Nothing here opens a transaction, writes a record, evaluates a policy or refuses
a publication. The owner seals what these facts mean, and the production
composition root is the only party that holds both sides.

What a reference capture establishes is reproducibility, and only that. It is not
an oracle, it does not establish `FULL`, and it says nothing about whether the
behaviour is correct.
"""

from __future__ import annotations

import json

from .replay import (
    CognitiveVMReplayAdapter,
    ReplayFailureCode,
    ReplayMachinePort,
    ReplayProgramBinding,
    _check_execution_contract,
    _drive_one_behavior,
    _fail,
    _snapshot_bytes_of,
)


def build_reference_machines(
    programs: tuple[object, ...], *, gas_budget: int
) -> tuple[CognitiveVMReplayAdapter, ...]:
    """The machines a fresh reference run starts from, built from admitted code.

    The exact ``CognitiveVMReplayAdapter``, never a scripted port: a port answers
    every question about itself, and a capture taken from one would record a
    transcript nothing executed. The programs are the ones the admission covered
    — compiled or resolved once inside the barrier and carried here — because
    asking a compiler again would run whatever it returned the second time, which
    nothing admitted.
    """

    return tuple(
        CognitiveVMReplayAdapter(program, gas_budget=gas_budget) for program in programs
    )


def reference_machine_snapshots(
    machines: tuple[CognitiveVMReplayAdapter, ...],
) -> tuple[bytes, ...]:
    """The exact starting bytes of these machines, for the caller to make durable."""

    return tuple(_snapshot_bytes_of(machine) for machine in machines)


def restore_reference_machines(
    raw_snapshots: tuple[bytes, ...], *, gas_budget: int
) -> tuple[CognitiveVMReplayAdapter, ...]:
    """The machines a continuation starts from, restored from durable bytes.

    Bytes, not references. Resolving a reference is a question for the store the
    composition root holds, and an adapter that resolved one would be choosing
    which store a continuation attaches to.
    """

    machines: list[CognitiveVMReplayAdapter] = []
    for raw in raw_snapshots:
        try:
            snapshot = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH, "a durable snapshot is not a machine snapshot"
            ) from exc
        machines.append(CognitiveVMReplayAdapter.from_snapshot(snapshot, gas_budget=gas_budget))
    return tuple(machines)


def drive_reference_execution(
    *,
    bindings: tuple[ReplayProgramBinding, ...],
    machines: tuple[ReplayMachinePort, ...],
    channel: object,
    gas_budget: int,
    step_limit: int,
) -> tuple[object, ...]:
    """Drive the admitted set once and report what each behaviour did.

    The same ``_drive_one_behavior`` a governed replay uses, over the same
    durable activity history, so no external call happens and no second
    execution semantics exists. Whatever that run reaches is what comes back.

    Stops at the first behaviour that did not complete. A run that faulted or
    exhausted a budget leaves the machines after it untouched, and reporting a
    transcript for a behaviour that never started would be reporting a fact
    nobody observed. The owner decides what a short report means.
    """

    if len(machines) != len(bindings):
        raise _fail(
            ReplayFailureCode.MACHINE_COUNT_MISMATCH,
            "a reference execution needs one machine per admitted behavior",
        )
    runs: list[object] = []
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
        runs.append(run)
        if run.failure_reason is not None:
            break
    return tuple(runs)


__all__ = [
    "build_reference_machines",
    "drive_reference_execution",
    "reference_machine_snapshots",
    "restore_reference_machines",
]
