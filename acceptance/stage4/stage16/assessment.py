"""External paired diagnostics over physically reopened evidence (§§32–33).

Differences are Baseline minus Gold. Actual measurements, legacy C2 diagnoses
and causal activation remain separate. This acceptance report does not grant
economic or product authority and does not estimate per-token counterfactuals.
"""

from synapse.experiments.gold.persistence import ExclusiveStoreLock

from decimal import Decimal
import statistics

from synapse.experiments.swebench.paired_measurement import (
    build_paired_measurement_record, ExecutionOrder, StatePolicy,
)

from .protocol import digest, read_source
from .run_evidence import inspect_baseline, inspect_gold


def _metric(view, key):
    if view is None:
        return None
    if key == "provider_tokens":
        if view["telemetry"]["status"] != "COMPLETE":
            return None
        return view["telemetry"]["source_totals"]["physical_provider_reported_tokens"]
    if key == "provider_money":
        return view["telemetry"]["source_totals"].get("provider_reported_money")
    raise ValueError("unknown diagnostic measurement")


def summarize_differences(values):
    """Complete paired samples only; no zero imputation or task cherry-picking."""
    if not values or any(value is None for value in values):
        return {"status": "INCOMPLETE", "n": len(values), "mean": None, "sample_stddev": None,
            "minimum": None, "maximum": None}
    samples = [Decimal(str(value)) for value in values]
    if any(not sample.is_finite() for sample in samples):
        raise ValueError("non-finite paired observation")
    return {"status": "DESCRIPTIVE_ONLY", "n": len(samples),
        "mean": str(statistics.mean(samples)), "sample_stddev": None if len(samples) < 2 else str(statistics.stdev(samples)),
        "minimum": str(min(samples)), "maximum": str(max(samples))}


def assess(experiment):
    with ExclusiveStoreLock(experiment.root / "experiment.lock"):
        allocations = experiment.allocations()
        pairs, seen_runs = [], set()
        for declared in experiment.protocol.payload()["pairs"]:
            selected = [slot for slot in allocations if slot["pair_id"] == declared["pair_id"]]
            views, errors, measurements, durations = {}, [], {}, {}
            for slot in selected:
                arm = slot["arm"]
                if slot["state"] != "FINISHED":
                    errors.append({"arm": arm, "code": "ARM_INCOMPLETE", "state": slot["state"]})
                    views[arm] = None
                    continue
                try:
                    definition = read_source(slot["input_ref"])
                    view = (inspect_baseline(slot, definition, slot["receipt"]) if arm == "BASELINE"
                        else inspect_gold(slot, definition, slot["receipt"]))
                    occurrence = arm, view["run_id"], view["result_identity"]
                    if occurrence in seen_runs:
                        raise ValueError("a physical run was reused as another replicate")
                    seen_runs.add(occurrence)
                    views[arm] = view
                    starts = [event["payload"]["environment"] for event in slot["events"] if event["kind"] in {"STARTED", "RESUMING"}]
                    if any(environment != experiment.protocol.payload()["environment"] for environment in starts + [view["axes"]["environment"]]):
                        raise ValueError("observed execution environment differs from preregistration")
                    if view["discrepancies"]:
                        errors.append({"arm": arm, "code": "PHYSICAL_RECONCILIATION_FAILED", "findings": view["discrepancies"]})
                    durations[arm] = sum(int(event["payload"].get("external_action_duration_ns", "0")) for event in slot["events"])
                    measurements[arm] = {"provider_tokens": _metric(view, "provider_tokens"),
                        "provider_calls": view["provider_calls"],
                        "provider_money": _metric(view, "provider_money"), "external_action_duration_ns": str(durations[arm]),
                        "infrastructure": view["resources"], "telemetry": view["telemetry"],
                        "attempts": view["attempts"], "result_identity": view["result_identity"]}
                except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
                    errors.append({"arm": arm, "code": "SOURCE_UNAVAILABLE_OR_CHANGED", "detail": str(exc)[:400]})
                    views[arm] = None
            baseline, gold = views.get("BASELINE"), views.get("GOLD")
            mismatches, c2 = [], None
            if baseline is not None and gold is not None:
                for key in baseline["axes"]:
                    if key != "execution_policy" and baseline["axes"][key] != gold["axes"].get(key):
                        mismatches.append(key)
                c2 = build_paired_measurement_record(pair_id=declared["pair_id"], baseline=baseline["member"], gold=gold["member"],
                    execution_order=ExecutionOrder.BASELINE_THEN_GOLD if selected[0]["arm"] == "BASELINE" else ExecutionOrder.GOLD_THEN_BASELINE,
                    cache_state_policy=StatePolicy.UNKNOWN, profile_state_policy=StatePolicy.CLEAN).to_dict()
            if mismatches:
                errors.append({"code": "ARM_FINGERPRINT_MISMATCH", "axes": mismatches})
            complete = not errors and all(view is not None and view["telemetry"]["status"] == "COMPLETE"
                and view["resources"]["status"] == "COMPLETE" for view in (baseline, gold))
            values = [_metric(view, "provider_tokens") for view in (baseline, gold)]
            delta = values[0] - values[1] if complete and all(value is not None for value in values) else None
            active = gold is not None and bool(gold["mechanisms"])
            policy_difference = baseline is not None and gold is not None and baseline["axes"]["execution_policy"] != gold["axes"]["execution_policy"]
            pairs.append({"pair_id": declared["pair_id"], "task_id": declared["task_id"], "replicate_id": declared["replicate_id"],
                "status": "DIAGNOSTIC_COMPLETE" if complete else "INCOMPLETE",
                "activation_eligible": complete and active,
                "execution_order": [slot["arm"] for slot in selected], "errors": errors,
                "observations": measurements, "provider_token_difference": delta,
                "external_action_duration_difference_ns": str(durations["BASELINE"] - durations["GOLD"]) if complete else None,
                "mechanism": {"status": "ACTIVATED" if active else "MECHANISM_NOT_ACTIVATED",
                    "proofs": [] if gold is None else gold["mechanisms"]}, "c2": c2,
                "causal_isolation": "EXECUTION_POLICIES_DIFFER" if policy_difference else "NOT_ESTABLISHED",
                "economic_claim": "NOT_AUTHORIZED_BY_ACCEPTANCE", "reuse_coefficient": "REUSE_COEFFICIENT_NOT_IDENTIFIABLE"})
        count = sum(pair["activation_eligible"] for pair in pairs)
        threshold = experiment.protocol.payload()["minimum_activated_pairs"]
        value = {"schema_version": "synapse.acceptance.stage16.assessment/v1", "protocol_id": experiment.protocol.identity,
            "code": experiment.protocol.payload()["code"], "specification": experiment.protocol.payload()["specification"],
            "history_identity": experiment.protocol.identity if not experiment.history() else digest(experiment.history()[-1]),
            "pairs": pairs, "provider_token_differences": summarize_differences([pair["provider_token_difference"] for pair in pairs]),
            "activation": {"observed_pairs": count, "required_pairs": threshold,
                "status": "ACTIVATED" if count >= threshold else "MECHANISM_NOT_ACTIVATED / INCONCLUSIVE"},
            "scope": {"measurements": "actual-full-run-diagnostics", "provider_cache": "separate-from-semantic-reuse",
                "external_duration": "sum-of-observed-invocations;operator-idle-time-excluded",
                "infrastructure": "retain-the-source-reports-with-their-measurement-exclusions",
                "money": "provider-reported-only;missing-is-unknown", "counterfactual_credit": "not-estimated",
                "statistical_unit": "paired-run;task-grouping-retained;no-population-inference",
                "stage4_completion": "requires-stage17-and-human-review"}}
        return {**value, "assessment_id": digest(value)}
