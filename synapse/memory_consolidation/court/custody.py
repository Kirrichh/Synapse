"""Custody of raw experience in store D (memory spec part 3 §2.2, §8.2, §8.4).

At substage 7a the owner takes each case's raw trace — its recorded events and
their positions in the session history — and the session's replay data — the
program, its bindings, the opening the session started from and the executor
that ran it — into its own content-addressed store D. From then on the owner's
experience does not depend on a run artifact kept elsewhere.

A raw trace may leave custody only when the replay data and the recorded
results reproduce it exactly (И9); restoring writes the reproduced trace back
to the same address, so a restored case is the same case, never a new vote.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from ..quanta import trace_ref
from ..session import opening_of

RAW_TRACE_V1 = "synapse.memory.raw-trace/v1"
REPLAY_DATA_V1 = "synapse.memory.replay-data/v1"


def replay_data(session: Mapping[str, Any], executor: str) -> dict[str, Any]:
    return {"kind": REPLAY_DATA_V1, "run_id": session["run_id"], "source_hash": session["source_hash"],
            "source_code": session["source_code"], "initial_bindings": session["initial_bindings"],
            "opening": opening_of(session["history"]), "executor": executor}


def raw_trace(run_id: str, op_scope: str, positions: list[int], events: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {"kind": RAW_TRACE_V1, "run_id": run_id, "op_scope": op_scope, "positions": list(positions),
            "events": [dict(event) for event in events]}


def take_custody(evidence, sessions: Iterable[Mapping[str, Any]], cases: Iterable[Mapping[str, Any]],
                 executor: str) -> dict[str, dict[str, Any]]:
    """Store the window's raw traces and replay data; the references each quantum keeps."""
    data = {session["run_id"]: evidence.put(replay_data(session, executor))[0] for session in sessions}
    custody = {}
    for case in cases:
        raw, _ = evidence.put(raw_trace(case["run_id"], case["op_scope"], case["trace_positions"], case["trace"]))
        custody[case["quantum"]["id"]] = {"raw_ref": raw, "data_ref": data[case["run_id"]],
                                          "positions": list(case["trace_positions"]), "executor": executor}
    return custody


def replay_input(evidence, data_ref: str) -> dict[str, Any] | None:
    """The replay data of one session, if it is still in custody."""
    content = evidence.get(data_ref)
    return content if content is not None and content.get("kind") == REPLAY_DATA_V1 else None


def reproduces(history: list[Mapping[str, Any]], positions: list[int], canonical_trace: str) -> bool:
    """Whether a reproduced history carries the case's exact events at its recorded positions."""
    if not positions or max(positions) >= len(history):
        return False
    return trace_ref(history[position] for position in positions) == canonical_trace


def materialize(evidence, restoring: Mapping[str, Any], history: list[Mapping[str, Any]]) -> str:
    """Write a reproduced raw trace back to its address; the address proves it is the same trace.

    ``restoring`` names the case's ``run_id``, ``op_scope``, ``positions`` and ``raw_ref``.
    """
    ref, _ = evidence.put(raw_trace(restoring["run_id"], restoring["op_scope"], restoring["positions"],
                                    [history[position] for position in restoring["positions"]]))
    if ref != restoring["raw_ref"]:
        raise ValueError("a reproduced raw trace is not the trace the case names")
    return ref


Reproduce = Callable[[Mapping[str, Any]], dict[str, Any]]


def reproduce_session(reproduce: Reproduce, evidence, data_ref: str) -> dict[str, Any]:
    """The session history re-executed from its replay data and recorded results only."""
    data = replay_input(evidence, data_ref)
    if data is None:
        return {"status": "replay_unavailable", "reason": "replay data is not in custody", "history": []}
    return reproduce(data)
