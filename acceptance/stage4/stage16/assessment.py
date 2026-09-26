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

from .protocol import digest, read_source, code_identity, environment_identity
from .run_evidence import inspect_baseline, inspect_gold


def _metric(view, key):
    if view is None or view["telemetry"] is None or view["telemetry"]["status"] != "COMPLETE":
        return None
    if key == "provider_tokens":
        return view["telemetry"]["source_totals"]["physical_provider_reported_tokens"]
    if key == "provider_money":
        return view["telemetry"]["source_totals"].get("provider_reported_money")
    raise ValueError("unknown diagnostic measurement")


def describe_samples(values):
    """Complete observations only; no zero imputation or sample selection."""
    if not values or any(value is None for value in values):
        return {"status": "INCOMPLETE", "n": len(values), "mean": None, "sample_stddev": None,
            "minimum": None, "maximum": None}
    samples = [Decimal(str(value)) for value in values]
    if any(not sample.is_finite() for sample in samples):
        raise ValueError("non-finite paired observation")
    return {"status": "DESCRIPTIVE_ONLY", "n": len(samples),
        "mean": str(statistics.mean(samples)), "sample_stddev": None if len(samples) < 2 else str(statistics.stdev(samples)),
        "minimum": str(min(samples)), "maximum": str(max(samples))}


def dispatch_duration(slot):
    """A lost dispatch receipt leaves an unknown interval after recovery.

    Summing surviving receipts gives an observed subtotal, not the full time.
    This is independent of the product's recovered outcome/provider accounting.
    """
    observed, pending, missing = 0, False, False
    for event in slot["events"]:
        if event["kind"] in {"STARTED", "RESUMING"}:
            missing |= pending
            pending = True
        else:
            raw = event["payload"].get("external_action_duration_ns")
            if type(raw) is not str or not raw.isdecimal():
                missing = True
            else:
                observed += int(raw)
            pending = False
    complete = bool(slot["events"]) and not (missing or pending)
    return {"status": "COMPLETE" if complete else "INCOMPLETE",
        "total_ns": str(observed) if complete else None, "observed_subtotal_ns": str(observed)}


def summarize_runs(runs):
    """Per-task/arm results retain failures and unknown replicas in their denominator."""
    groups = {}
    for run in runs:
        groups.setdefault((run["task_id"], run["arm"]), []).append(run)
    result = []
    for (task, arm), selected in sorted(groups.items()):
        outcomes = [None if run["outcome"] is None else run["outcome"]["task_resolved"] for run in selected]
        aligned = all(run["replicate_parameters_match"] for run in selected)
        result.append({"task_id": task, "arm": arm, "requested_runs": len(selected),
            "finished_runs": sum(run["execution_state"] == "FINISHED" for run in selected),
            "verified_resolved": sum(value is True for value in outcomes),
            "unresolved": sum(value is False for value in outcomes),
            "outcome_unknown": sum(value is None for value in outcomes),
            "replicate_parameters_match": aligned,
            "samples": {name: describe_samples([run[name] if aligned else None for run in selected])
                for name in ("provider_tokens", "provider_money", "elapsed_seconds")}})
    return result


def assess(experiment):
    with ExclusiveStoreLock(experiment.root / "experiment.lock"):
        allocations = experiment.allocations()
        pairs, runs, seen_runs, replica_axes = [], [], set(), {}
        for declared in experiment.protocol.payload()["pairs"]:
            selected = [slot for slot in allocations if slot["pair_id"] == declared["pair_id"]]
            views, errors, measurements, durations, pair_runs = {}, [], {}, {}, {}
            for slot in selected:
                arm = slot["arm"]
                duration = dispatch_duration(slot)
                row = {"slot_id": slot["slot_id"], "pair_id": slot["pair_id"], "task_id": slot["task_id"],
                    "replicate_id": slot["replicate_id"], "arm": arm, "execution_state": slot["state"],
                    "run_id": None, "outcome": None, "attempt_count": None, "provider_tokens": None,
                    "provider_money": None, "elapsed_seconds": None if duration["total_ns"] is None else str(Decimal(duration["total_ns"]) / 10**9),
                    "duration": duration, "measurements_status": "INCOMPLETE", "replicate_parameters_match": True,
                    "infrastructure": None, "errors": []}
                runs.append(row)
                pair_runs[arm] = row
                if slot["state"] != "FINISHED":
                    errors.append({"arm": arm, "code": "ARM_INCOMPLETE", "state": slot["state"]})
                    row["errors"].append(errors[-1])
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
                    previous = replica_axes.setdefault((slot["task_id"], arm), view["axes"])
                    changed = [key for key in previous if previous[key] != view["axes"].get(key)]
                    if changed:
                        errors.append({"arm": arm, "code": "REPLICATE_FINGERPRINT_MISMATCH", "axes": changed})
                        row["replicate_parameters_match"] = False
                    starts = [event["payload"]["environment"] for event in slot["events"] if event["kind"] in {"STARTED", "RESUMING"}]
                    if any(environment != experiment.protocol.payload()["environment"] for environment in starts + [view["axes"]["environment"]]):
                        raise ValueError("observed execution environment differs from preregistration")
                    if view["discrepancies"]:
                        errors.append({"arm": arm, "code": "PHYSICAL_RECONCILIATION_FAILED", "findings": view["discrepancies"]})
                    durations[arm] = None if duration["total_ns"] is None else int(duration["total_ns"])
                    measurements[arm] = {"provider_tokens": _metric(view, "provider_tokens"),
                        "provider_calls": view["provider_calls"],
                        "provider_money": _metric(view, "provider_money"), "external_action_duration_ns": duration["total_ns"],
                        "duration": duration, "outcome": view["outcome"],
                        "infrastructure": view["resources"], "telemetry": view["telemetry"],
                        "attempts": view["attempts"], "result_identity": view["result_identity"]}
                    measured = duration["status"] == "COMPLETE" and not view["discrepancies"] and all(report is not None and report["status"] == "COMPLETE"
                        for report in (view["telemetry"], view["resources"]))
                    row.update(run_id=view["run_id"], outcome=view["outcome"], attempt_count=len(view["attempts"]),
                        provider_tokens=_metric(view, "provider_tokens"), provider_money=_metric(view, "provider_money"),
                        measurements_status="COMPLETE" if measured else "INCOMPLETE", infrastructure=view["resources"],
                        errors=[error for error in errors if error.get("arm") == arm])
                except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
                    errors.append({"arm": arm, "code": "SOURCE_UNAVAILABLE_OR_CHANGED", "detail": str(exc)[:400]})
                    row["errors"].append(errors[-1])
                    views[arm] = None
            baseline, gold = views.get("BASELINE"), views.get("GOLD")
            mismatches, alignment, c2 = [], {}, None
            if baseline is not None and gold is not None:
                for key in baseline["axes"]:
                    alignment[key] = {"baseline": baseline["axes"][key], "gold": gold["axes"].get(key),
                        "status": "MATCH" if baseline["axes"][key] == gold["axes"].get(key) else "MISMATCH"}
                    if key != "execution_policy" and alignment[key]["status"] == "MISMATCH":
                        mismatches.append(key)
                c2 = build_paired_measurement_record(pair_id=declared["pair_id"], baseline=baseline["member"], gold=gold["member"],
                    execution_order=ExecutionOrder.BASELINE_THEN_GOLD if selected[0]["arm"] == "BASELINE" else ExecutionOrder.GOLD_THEN_BASELINE,
                    cache_state_policy=StatePolicy.UNKNOWN, profile_state_policy=StatePolicy.CLEAN).to_dict()
            if mismatches:
                errors.append({"code": "ARM_FINGERPRINT_MISMATCH", "axes": mismatches})
            complete = not errors and all(row["measurements_status"] == "COMPLETE" for row in pair_runs.values())
            values = [_metric(view, "provider_tokens") for view in (baseline, gold)]
            delta = values[0] - values[1] if complete and all(value is not None for value in values) else None
            active = gold is not None and bool(gold["mechanisms"])
            policy_difference = baseline is not None and gold is not None and baseline["axes"]["execution_policy"] != gold["axes"]["execution_policy"]
            pairs.append({"pair_id": declared["pair_id"], "task_id": declared["task_id"], "replicate_id": declared["replicate_id"],
                "status": "DIAGNOSTIC_COMPLETE" if complete else "INCOMPLETE",
                "activation_eligible": complete and active,
                "execution_order": [slot["arm"] for slot in selected], "errors": errors,
                "observations": measurements, "provider_token_difference": delta,
                "external_action_duration_difference_ns": str(durations["BASELINE"] - durations["GOLD"])
                    if complete else None,
                "mechanism": {"status": "ACTIVATED" if active else "MECHANISM_NOT_ACTIVATED",
                    "proofs": [] if gold is None else gold["mechanisms"]}, "c2": c2,
                "causal_isolation": "EXECUTION_POLICIES_DIFFER" if policy_difference else "NOT_ESTABLISHED",
                "parameter_alignment": alignment,
                "execution_policies_comparable": bool(alignment) and all(axis["status"] == "MATCH" for axis in alignment.values()),
                "economic_claim": "NOT_AUTHORIZED_BY_ACCEPTANCE", "reuse_coefficient": "REUSE_COEFFICIENT_NOT_IDENTIFIABLE"})
        count = sum(pair["activation_eligible"] for pair in pairs)
        threshold = experiment.protocol.payload()["minimum_activated_pairs"]
        value = {"schema_version": "synapse.acceptance.stage16.assessment/v2", "protocol_id": experiment.protocol.identity,
            "evaluator_code": code_identity(experiment.repository), "evaluator_environment": environment_identity(),
            "code": experiment.protocol.payload()["code"], "specification": experiment.protocol.payload()["specification"],
            "history_identity": experiment.protocol.identity if not experiment.history() else digest(experiment.history()[-1]),
            "runs": runs, "task_results": summarize_runs(runs),
            "pairs": pairs, "provider_token_differences": describe_samples([pair["provider_token_difference"] for pair in pairs]),
            "external_action_duration_differences_ns": describe_samples([pair["external_action_duration_difference_ns"] for pair in pairs]),
            "activation": {"observed_pairs": count, "required_pairs": threshold,
                "status": "ACTIVATED" if count >= threshold else "MECHANISM_NOT_ACTIVATED / INCONCLUSIVE"},
            "scope": {"measurements": "actual-full-run-diagnostics", "provider_cache": "separate-from-semantic-reuse",
                "external_duration": "sum-of-observed-invocations;operator-idle-time-excluded",
                "infrastructure": "retain-the-source-reports-with-their-measurement-exclusions",
                "money": "provider-reported-only;missing-is-unknown", "counterfactual_credit": "not-estimated",
                "statistical_unit": "paired-run;task-grouping-retained;no-population-inference",
                "stage4_completion": "requires-stage17-and-human-review"}}
        return {**value, "assessment_id": digest(value)}
