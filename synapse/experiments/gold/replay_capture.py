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

* ask the composition-provided machine factory to build the machines a fresh
  reference run starts from, out of admitted programs;
* ask that same factory to restore the machines a continuation starts from, out
  of durable snapshot bytes;
* drive the admitted set once and report one ``_TransitionRun`` per behaviour.

Nothing here opens a transaction, writes a record, evaluates a policy or refuses
a publication. The owner seals what these facts mean, and the production
composition root is the only party that holds both sides.

What a reference capture establishes is reproducibility, and only that. It is not
an oracle, it does not establish `FULL`, and it says nothing about whether the
behaviour is correct.
"""

from __future__ import annotations

from .replay import (
    ReplayFailureCode,
    ReplayMachineExecutionContext,
    ReplayMachineFactoryPort,
    ReplayMachinePort,
    ReplayProgramBinding,
    _check_execution_contract,
    _drive_one_behavior,
    _fail,
    _snapshot_bytes_of,
    refused_transition_run,
)


def build_reference_machines(
    programs: tuple[object, ...],
    *,
    machine_factory: ReplayMachineFactoryPort,
    execution_context: ReplayMachineExecutionContext,
    gas_budget: int,
) -> tuple[ReplayMachinePort, ...]:
    """The machines a fresh reference run starts from, built from admitted code.

    The concrete factory is selected by the composition root, never by this
    sibling adapter or by the caller asking for a capture. The programs are the
    ones the admission covered — compiled or resolved once inside the barrier
    and carried here — because asking a compiler again would run whatever it
    returned the second time, which nothing admitted.
    """

    return tuple(
        machine_factory.build(
            program,
            gas_budget=gas_budget,
            execution_context=execution_context,
            expected_structural_history=None,
        )
        for program in programs
    )


def reference_machine_snapshots(
    machines: tuple[ReplayMachinePort, ...],
) -> tuple[bytes, ...]:
    """The exact starting bytes of these machines, for the caller to make durable."""

    return tuple(_snapshot_bytes_of(machine) for machine in machines)


def restore_reference_machines(
    raw_snapshots: tuple[bytes, ...],
    *,
    machine_factory: ReplayMachineFactoryPort,
    execution_context: ReplayMachineExecutionContext,
    gas_budget: int,
) -> tuple[ReplayMachinePort, ...]:
    """The machines a continuation starts from, restored from durable bytes.

    Bytes, not references. Resolving a reference is a question for the store the
    composition root holds, and an adapter that resolved one would be choosing
    which store a continuation attaches to.
    """

    return tuple(
        machine_factory.restore(
            raw,
            gas_budget=gas_budget,
            execution_context=execution_context,
            expected_structural_history=None,
        )
        for raw in raw_snapshots
    )


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

    A machine whose program is not the bound one is reported the same way — as a
    raw fact with the reason, produced by ``refused_transition_run``. Raising
    there is what made ``PROGRAM_HASH_MISMATCH`` unreachable in a governed run:
    a continuation whose admitted programs do not sit where its predecessor left
    them could never obtain a manifest, so the governed attempt that would have
    recorded the mismatch as the typed outcome §23 names never happened. The
    adapter reports; the owner decides.
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
            # Only now, once the execution contract holds, may a machine see the
            # channel — a machine running a program other than the bound one
            # must never reach a recorded effect.
            runs.append(refused_transition_run(incompatible, machine=machine))
            break
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
