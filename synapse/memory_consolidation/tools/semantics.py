"""Semantics of recorded answers under their tool contracts.

Nothing here reads HTTP status classes as proof of an effect: without a
contract, an answer is read by the general payload rule and an unreadable one
stays unverifiable. A lost answer leaves the effect unknown; a repeat is
admissible only when the earlier effect is known absent or partial-and-
repeatable, or the operation is idempotent by contract.
"""
from __future__ import annotations

from typing import Any

from .contracts import ToolContract

#: Services whose records are the agent's own product; they never corroborate.
AGENT_OWN_SERVICES = frozenset({"palace", "memory", "store", "scratchpad", "ledger", "journal", "log",
                                "notes", "notebook", "self", "agent", "cassette", "internal"})


def payload_result(payload: Any) -> tuple[str, str | None]:
    """General reading of a recorded answer, without knowledge of the task.

    A boolean ``ok`` is a machine-checkable result; a non-empty ``err`` without
    it is a refusal; anything else is unverifiable — the gateway never guesses.
    """
    if isinstance(payload, dict):
        ok = payload.get("ok")
        if isinstance(ok, bool):
            if ok:
                return "ok", None
            err = payload.get("err")
            return "op_error", err if isinstance(err, str) and err else None
        err = payload.get("err")
        if isinstance(err, str) and err:
            return "op_error", err
    return "unverifiable", None


def interpret(contract: ToolContract, transport: str, payload: Any) -> dict[str, Any]:
    """Result and effect class of one attempt under its tool contract.

    A lost answer leaves the effect unknown. A refusal leaves no effect unless
    the service's documentation says otherwise for that code. An answer without
    a machine-checkable result leaves the effect unknown.
    """
    if transport != "ok":
        return {"op_result": "unknown", "op_err": None, "effect": "unknown"}
    op_result, op_err = payload_result(payload)
    if op_result == "ok":
        effect = "applied"
    elif op_result == "op_error":
        effect = contract.effect_on_err.get(op_err or "", "none")
    else:
        effect = "unknown"
    return {"op_result": op_result, "op_err": op_err, "effect": effect}


def repeat_admissible(effect: str, contract: ToolContract) -> tuple[bool, str]:
    """May an unresolved operation be attempted again, decided before any effect."""
    if effect == "none":
        return True, "the service refused without effect"
    if effect == "partial":
        if contract.repeatable_on_partial:
            return True, "a partial effect is repeatable under the operation's contract"
        return False, "a partial effect is not repeatable under the operation's contract"
    if effect == "applied":
        return False, "the operation already took effect"
    if contract.idempotent:
        return True, "the effect is unknown and the operation is idempotent by contract"
    return False, "the effect is unknown and the operation is not idempotent; resolve it by a state check"


def compensation_confirmed(contract: ToolContract, payload: Any) -> bool:
    if not isinstance(payload, dict) or not contract.compensation_signs:
        return False
    return all(payload.get(key) == value for key, value in contract.compensation_signs.items())


def resolve_uncertainty(contract: ToolContract, payload: Any) -> str | None:
    """Effect a recorded state check establishes for the operation it checks."""
    if not isinstance(payload, dict):
        return None
    for key, effect in sorted(contract.resolve_state.items()):
        if key in payload and isinstance(payload[key], bool):
            return effect if payload[key] else ("none" if effect == "applied" else "applied")
    return None


def environmental_refusal(contract: ToolContract, transport: str, op_err: str | None) -> bool:
    """An answer the service documents as its own unavailability, or no answer at all."""
    return transport == "lost" or (op_err is not None and op_err in contract.environmental_errors)


def corroborating(role: str, source: str) -> bool:
    """Whether a recorded answer can count as an outside witness."""
    return role == "action" and source.partition(":")[0] not in AGENT_OWN_SERVICES
