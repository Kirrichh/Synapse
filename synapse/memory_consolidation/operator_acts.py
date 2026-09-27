"""The governing operator's acts on retained experience (memory spec part 3 §8.7; refinement §9).

Two acts, each recorded as a retention pass in the project journal before any
body in store D changes, under the owner session:

* ``forget`` is a legally significant removal, stronger than retention periods:
  the raw trace and the recorded results of the case — and of every other case
  that carries the same recorded results — are removed, and a tombstone naming
  the identity, reason and authority takes their place. Chains that passed
  through them end at that tombstone, never at a dangling reference.
* ``restore`` returns a compacted case to processing: its session is reproduced
  from the replay data and the recorded results, and only an exact
  reproduction is written back to the address the case names. The case keeps
  its identity, so it gains no new vote for the same evidence.

The next consolidation applies both, like any other retention fact.
"""
from __future__ import annotations

from typing import Any

from . import records
from .court.custody import materialize, reproduce_session, reproduces
from .court.retention import complete

AUTHORITY = "GOVERNING_HUMAN"


def forget(owner, ports, guard, qid: str, *, reason: str, operator: str) -> list[dict[str, Any]]:
    state = owner.state(guard=guard)
    entry = state["quanta"].get(qid)
    if entry is None or entry["retention_state"] == "forgotten":
        raise ValueError("forget names a retained case of this memory owner")
    if not reason.strip() or not operator.strip():
        raise ValueError("forget states its reason and the operator who holds the authority")
    carried = set(entry["evidence_refs"])
    # Every case carrying the forgotten recorded results contains what is forgotten.
    affected = sorted(other for other, item in state["quanta"].items()
                      if item["retention_state"] != "forgotten" and (other == qid or carried & set(item["evidence_refs"])))
    tombstone = "tmb_" + records.digest({"qids": affected, "reason": reason, "authority": AUTHORITY,
                                         "operator": operator, "window": state["window"]})
    acts = []
    for member in affected:
        item = state["quanta"][member]
        refs = sorted({*item["evidence_refs"], *([item["raw_ref"]] if item.get("raw_ref") else [])})
        acts.append({"act": "forgotten", "qid": member, "tombstone": tombstone, "reason": reason,
                     "authority": AUTHORITY, "operator": operator, "refs": refs})
    _record(owner, ports, guard, state, acts)
    complete(ports, acts)
    return acts


def restore(owner, ports, guard, qid: str, parameters) -> dict[str, Any]:
    state = owner.state(guard=guard)
    entry = state["quanta"].get(qid)
    if entry is None or entry["retention_state"] != "replay_only":
        raise ValueError("only a case whose raw trace retention removed can be restored")
    replay = entry["replay_ref"]
    result = reproduce_session(ports.reproduce, ports.gateway.evidence, replay["data_ref"])
    if result["status"] != "reproduced" or not reproduces(result["history"], replay["positions"],
                                                          entry["canonical"]["trace_ref"]):
        raise ValueError(f"the case is not reproducible now: {result['status']}")
    act = {"act": "restored", "qid": qid, "raw_ref": entry["raw_ref"], "run_id": replay["run_id"],
           "op_scope": replay["op_scope"], "positions": replay["positions"], "data_ref": replay["data_ref"],
           "retention": {"action": "delete_raw_trace_after_replay",
                         "due": state["window"] + parameters["n_medium_windows"]}}
    _record(owner, ports, guard, state, [act])
    materialize(ports.gateway.evidence, act, result["history"])
    return act


def _record(owner, ports, guard, state, acts) -> None:
    """Complete the last pass, then record these acts before any body changes."""
    passes = owner.retention_passes(guard=guard)
    if passes:
        complete(ports, passes[-1]["acts"])
    owner.put_retention(guard, len(passes), state["window"], acts)
