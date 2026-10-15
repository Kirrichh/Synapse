"""Compare engineering outcomes without erasing evidence identities."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def exact(left, right, fields):
    for field in fields:
        assert left[field] == right[field], (field, left[field], right[field])


def compare_retained(before, after):
    old, new = before["window4"]["reads"], after["window4"]["reads"]
    exact(old, new, ("window", "knowledge_versions", "knowledge", "logical_inventory_with_receipts",
                    "logical_events_without_receipts", "event_kinds"))
    exact(old["state"], new["state"], ("canonical_sha256", "canonical_bytes"))
    exact(old["configuration"], new["configuration"], ("canonical_sha256", "canonical_bytes"))
    exact(before["audit"], after["audit"], ("full_search_events_sha256", "knowledge_unchanged_sha256",
          "independently_checked_candidates", "queries", "transport_calls", "replay_transport_calls"))
    exact(before["window5"]["reads"], after["window5"]["reads"], ("knowledge", "event_kinds"))
    return {"retained_complete_state_exact": True, "retained_complete_inventory_with_receipts_exact": True,
            "all_64_complete_search_results_exact": True, "continued_knowledge_exact": True,
            "continued_complete_state_equal": before["window5"]["reads"]["state"]["canonical_sha256"]
                == after["window5"]["reads"]["state"]["canonical_sha256"],
            "continued_state_scope": "New independent runs retain different physical receipts and artifact "
                                     "paths. No complete-state equivalence is inferred from knowledge equality."}


def compare_fresh(before, after, baseline_root, patched_root):
    exact(before["evacuation"], after["evacuation"], ("shards", "applied_moves", "copied_units",
          "transport_calls", "planned", "disturbance", "stale_plan_refusals", "knowledge_versions",
          "court_windows", "corrected_node5_free_space", "recalled_candidates", "replay_transport_calls"))
    for filename in ("final-cluster.json", "move-ledger.json"):
        assert json.loads((baseline_root / "evacuation" / filename).read_text()) == json.loads(
            (patched_root / "evacuation" / filename).read_text()), filename
    return {"final_cluster_exact": True, "complete_move_ledger_exact": True,
            "domain_oracles_passed_both": True,
            "scope": "Independent projects retain different identities, paths and receipts. "
                     "This comparison proves exact engineering outcomes, not equal raw journal bytes."}


def compare(baseline_path, patched_path):
    before, after = (json.loads(path.read_text()) for path in (baseline_path, patched_path))
    mode = before["environment"]["mode"]
    assert mode == after["environment"]["mode"]
    if mode == "retained":
        result = compare_retained(before, after)
    elif mode == "fresh":
        result = compare_fresh(before, after, baseline_path.parent, patched_path.parent)
    else:
        raise ValueError("Compare fresh or retained complete workload measurements")
    if "window8" in before and "window8" in after:
        exact(before["window8"]["reads"], after["window8"]["reads"], ("window", "knowledge_versions", "event_kinds"))
        if mode == "retained":
            exact(before["window8"]["reads"], after["window8"]["reads"], ("knowledge",))
        result["materialized_snapshot_present_both"] = (
            before["window8"]["reads"]["event_kinds"]["MEMORY_STATE"]
            == after["window8"]["reads"]["event_kinds"]["MEMORY_STATE"] == 1)
        assert result["materialized_snapshot_present_both"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("patched", type=Path)
    parser.add_argument("output", type=Path)
    options = parser.parse_args()
    result = compare(options.baseline, options.patched)
    options.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
