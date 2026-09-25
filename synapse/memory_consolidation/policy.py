"""The court's declared, versioned policy (memory spec part 2 §8).

Every value is a starting parameter fixed before a run and recorded in every
report; another value is another policy version, never a reinterpretation of an
earlier report. Formulas here are pure and deterministic.

One deliberate departure from the tested model follows the refinement: an
``unclear`` or uncertain local outcome is not scored 0.5. It is neither success
nor proven failure, so it produces no signal and waits as pending evidence.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from .records import digest

POLICY_V1 = "synapse.memory.court-policy/v1"
DECISION_RULES = ("threshold", "sprt")

PARAMETERS_V1: dict[str, Any] = {
    # signal
    "w_outcome": 0.6, "w_segment": 0.4,
    "outcome_score": {"success": 1.0, "failure": 0.0, "op_failure": 0.0},
    "segment_score": {"confirmed": 1.0, "partial": 0.5, "failed": 0.0},
    # trust
    "lr": {"born": 0.50, "active": 0.15, "probation": 0.30},
    "min_evidence": 3, "pending_max_windows": 6, "resurrection_trust": 0.5,
    # automaton
    "t1_trust": 0.70, "t1_fires": 5, "t1_tasks": 2,
    "t3_signal": 0.65, "t3_fires": 5,
    "t4_fires": 5, "t4_signal": 0.70,
    "m_idle": 6, "cold_matches": 2, "k_extinct": 3, "n_t9": 10, "n_pivot": 10,
    "sprt": {"p0": 0.8, "p1": 0.5, "alpha": 0.01, "beta": 0.01, "useful_signal": 0.8},
    # conflicts
    "conflict_gap": 0.30, "conflict_warning_gap": 0.05, "counterfactual_min_pairs": 3,
    # birth
    "birth_episodes": 3, "birth_tasks": 2, "birth_sources": 2, "action_same": 0.90, "action_different": 0.70,
    "learned_priority_class": "medium", "boundary_min_events": 3,
    # similarity
    "arbitration_similarity": 0.80, "semantic_veto_below": 0.75,
    "verify_confirm_similarity": 0.90, "verify_partial_similarity": 0.75,
    # environment
    "environmental_run": 3,
    # replay (1b): recorded events a verified re-execution may consume
    "replay_event_budget": 100_000,
    # weight W = C x R x k_dir (spec part 3 §9)
    "complexity": {"a": 0.1, "b": 0.5, "c": 0.5, "d": 0.3},
    "resources": {"v_tok": 0.5, "v_time": 0.3, "v_gas": 0.2},
    "norms": {"tokens": 10_000, "seconds": 30, "gas": 1_000_000},
    "k_dir": 1.0, "energy_per_R": 1.0, "unmeasured_energy_cost": 1.0, "time_coverage": 0.8,
    "recent_fires": 64, "candidate_max_windows": 30,
    # retention by significance, counted in consolidation windows
    "w_high": 5.0, "n_medium_windows": 30, "n_low_windows": 7, "grace_windows": 30, "k_rollup": 12,
}


class PolicyViolation(ValueError):
    """A court policy override is outside the declared parameter registry."""


def resolve_parameters(overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    """The declared parameters with operator overrides of the same shape."""
    resolved = {key: (dict(value) if isinstance(value, dict) else value) for key, value in PARAMETERS_V1.items()}
    for key, value in (overrides or {}).items():
        if key not in PARAMETERS_V1:
            raise PolicyViolation(f"unknown court parameter {key!r}")
        base = PARAMETERS_V1[key]
        if isinstance(base, dict):
            if type(value) is not dict or set(value) - set(base):
                raise PolicyViolation(f"court parameter {key!r} overrides only declared entries")
            for name, item in value.items():
                if type(item) not in {int, float} or isinstance(item, bool):
                    raise PolicyViolation(f"court parameter {key}.{name} is numeric")
            resolved[key] = {**base, **value}
        elif isinstance(base, str):
            if type(value) is not str:
                raise PolicyViolation(f"court parameter {key!r} is a name")
            resolved[key] = value
        else:
            if type(value) not in {int, float}:
                raise PolicyViolation(f"court parameter {key!r} is numeric")
            if type(base) is int:
                if not float(value).is_integer() or value < 0:
                    raise PolicyViolation(f"court parameter {key!r} is a count")
                value = int(value)
            resolved[key] = value
    return resolved


def policy_identity(parameters: Mapping[str, Any], decision_rule: str) -> dict[str, Any]:
    if decision_rule not in DECISION_RULES:
        raise PolicyViolation("decision rule is threshold or sprt")
    return {"policy": POLICY_V1, "decision_rule": decision_rule, "parameters": dict(parameters),
            "parameters_ref": digest({"rule": decision_rule, "parameters": dict(parameters)})}


def signal(parameters: Mapping[str, Any], outcome: str, segment_verdict: str) -> float | None:
    """``w_outcome · outcome + w_segment · segment``; ``None`` when either is undecided."""
    outcome_score = parameters["outcome_score"].get(outcome)
    segment_score = parameters["segment_score"].get(segment_verdict)
    if outcome_score is None or segment_score is None:
        return None
    return parameters["w_outcome"] * outcome_score + parameters["w_segment"] * segment_score


def trust_update(parameters: Mapping[str, Any], trust: float, observed: float, state: str) -> float:
    """``trust + lr(state) · (signal − trust)``; dormant and extinct habits are not updated."""
    rate = parameters["lr"].get(state)
    if rate is None:
        return trust
    return trust + rate * (observed - trust)


def sprt_step(parameters: Mapping[str, Any], observed: float) -> float:
    """Log-likelihood step of one fire for H1 'the habit is bad' against H0 'good'."""
    sprt = parameters["sprt"]
    useful = observed >= sprt["useful_signal"]
    if useful:
        return math.log(sprt["p1"] / sprt["p0"])
    return math.log((1 - sprt["p1"]) / (1 - sprt["p0"]))


def sprt_decision(parameters: Mapping[str, Any], log_likelihood: float) -> str | None:
    sprt = parameters["sprt"]
    upper = math.log((1 - sprt["beta"]) / sprt["alpha"])
    lower = math.log(sprt["beta"] / (1 - sprt["alpha"]))
    if log_likelihood >= upper:
        return "H1"
    if log_likelihood <= lower:
        return "H0"
    return None
