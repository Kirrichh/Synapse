"""The closed vocabulary of a Gold run: outcomes, decisions and failures.

A run has three enumerations that no other module may extend on the fly: how
one attempt ended, what the controller decided afterwards, and what authority
status the whole run carries. They live here rather than beside the records
because they change for a different reason: a new outcome is a change to what
the controller can conclude, while a new field on ``GoldAttemptResult`` is a
change to what a record carries. Keeping the two apart is what stops a widened
vocabulary from arriving as a side effect of a record edit.

``final_status_for_decision`` is the one mapping from a terminal decision to a
run status. It is a closed table and not a heuristic: a decision that is not
terminal has no status, and asking for one is a phase error rather than a
default.
"""

from __future__ import annotations

from enum import Enum


class GoldRunFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    MALFORMED_IDENTITY = "MALFORMED_IDENTITY"
    BOUNDED_VALUE = "BOUNDED_VALUE"
    CONFIG_INVALID = "CONFIG_INVALID"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    AUTHORITY_MISMATCH = "AUTHORITY_MISMATCH"
    RECORD_CONFLICT = "RECORD_CONFLICT"
    RECORD_MISSING = "RECORD_MISSING"
    PHASE_INVALID = "PHASE_INVALID"
    C1_BOUNDARY_MISMATCH = "C1_BOUNDARY_MISMATCH"
    CONSUMPTION_REFUSED = "CONSUMPTION_REFUSED"
    DELIVERY_MISMATCH = "DELIVERY_MISMATCH"


class GoldRunViolation(ValueError):
    """Typed, fail-closed violation of the run-lifecycle contract."""

    def __init__(self, failure_code: GoldRunFailureCode, detail: str) -> None:
        if type(failure_code) is not GoldRunFailureCode:
            raise TypeError("failure_code must be an exact GoldRunFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a bounded non-empty string")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)

class AttemptOutcome(str, Enum):
    """Controller-owned classification of one attempt; never C1 authority."""

    RESOLVED = "ATTEMPT_RESOLVED"
    UNRESOLVED = "ATTEMPT_UNRESOLVED"
    NO_CANDIDATE = "ATTEMPT_NO_CANDIDATE"
    INFRA_ERROR = "ATTEMPT_INFRA_ERROR"
    C1_RESULT_INVALID = "ATTEMPT_C1_RESULT_INVALID"
    DELIVERY_REFUSED = "ATTEMPT_DELIVERY_REFUSED"
    DELIVERY_UNAVAILABLE = "ATTEMPT_DELIVERY_UNAVAILABLE"
    CONTROLLER_INTERRUPTED = "ATTEMPT_CONTROLLER_INTERRUPTED"


class TerminalDecisionKind(str, Enum):
    CONTINUE = "CONTINUE"
    STOP_SUCCESS = "STOP_SUCCESS"
    STOP_LIMIT = "STOP_LIMIT"
    STOP_UNRECOVERABLE = "STOP_UNRECOVERABLE"
    STOP_NO_NEW_KNOWLEDGE = "STOP_NO_NEW_KNOWLEDGE"
    FALLBACK_BASELINE_EXPLICIT = "FALLBACK_BASELINE_EXPLICIT"


class RunFinalStatus(str, Enum):
    GOLD_RESOLVED = "GOLD_RESOLVED"
    GOLD_STOPPED_LIMIT = "GOLD_STOPPED_LIMIT"
    GOLD_STOPPED_NO_NEW_KNOWLEDGE = "GOLD_STOPPED_NO_NEW_KNOWLEDGE"
    GOLD_UNAVAILABLE = "GOLD_UNAVAILABLE"
    BASELINE_FALLBACK_EXPLICIT = "BASELINE_FALLBACK_EXPLICIT"


class TelemetryCompleteness(str, Enum):
    """Whether telemetry is authoritative enough for downstream measurements."""

    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    UNAVAILABLE = "UNAVAILABLE"


class MechanismActivationStatus(str, Enum):
    """Whether the reuse mechanism has independent activation evidence."""

    MECHANISM_ACTIVATED = "MECHANISM_ACTIVATED"
    MECHANISM_NOT_ACTIVATED = "MECHANISM_NOT_ACTIVATED"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_EVALUATED = "NOT_EVALUATED"


class FallbackPolicy(str, Enum):
    FORBIDDEN = "FORBIDDEN"
    EXPLICIT_BASELINE_ARM = "EXPLICIT_BASELINE_ARM"


TERMINAL_DECISIONS = frozenset(
    {
        TerminalDecisionKind.STOP_SUCCESS,
        TerminalDecisionKind.STOP_LIMIT,
        TerminalDecisionKind.STOP_UNRECOVERABLE,
        TerminalDecisionKind.STOP_NO_NEW_KNOWLEDGE,
        TerminalDecisionKind.FALLBACK_BASELINE_EXPLICIT,
    }
)


_FINAL_STATUS_BY_DECISION: dict[TerminalDecisionKind, RunFinalStatus] = {
    TerminalDecisionKind.STOP_SUCCESS: RunFinalStatus.GOLD_RESOLVED,
    TerminalDecisionKind.STOP_LIMIT: RunFinalStatus.GOLD_STOPPED_LIMIT,
    TerminalDecisionKind.STOP_NO_NEW_KNOWLEDGE: RunFinalStatus.GOLD_STOPPED_NO_NEW_KNOWLEDGE,
    TerminalDecisionKind.STOP_UNRECOVERABLE: RunFinalStatus.GOLD_UNAVAILABLE,
    TerminalDecisionKind.FALLBACK_BASELINE_EXPLICIT: RunFinalStatus.BASELINE_FALLBACK_EXPLICIT,
}


def final_status_for_decision(decision: TerminalDecisionKind) -> RunFinalStatus:
    """Closed mapping from terminal decision to final authority status."""

    if type(decision) is not TerminalDecisionKind or decision not in _FINAL_STATUS_BY_DECISION:
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "decision is not terminal")
    return _FINAL_STATUS_BY_DECISION[decision]
