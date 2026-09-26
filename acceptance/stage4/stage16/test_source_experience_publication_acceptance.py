"""Recall includes both verification polarities through real publication."""

import json
import sys

from acceptance.stage4.stage16._source_inputs import prepare, learn, recall


def test_expected_negative_exit_is_verified_and_does_not_hide_unsatisfied_claims(tmp_path):
    _, state, path = prepare(tmp_path, execute=True)
    value = json.loads(path.read_text())
    claim = value["claim"]
    claim.update(operation_id="negative-observation", kind="VERIFICATION_RECIPE", recipe={
        "command": [sys.executable, "-B", "-c", 'print("double observed refusal"); raise SystemExit(3)'],
        "expectation": {"expected_exit_codes": [3], "expected_nonzero_exit": True,
            "combined_output_contains": ["observed refusal"], "combined_output_not_contains": [], "timeout_seconds": 10}})
    path.write_text(json.dumps(value))
    code, published = learn(state, path)
    assert code == 0 and published["status"] == "PUBLISHED", published
    claim["operation_id"] = "unsatisfied-observation"
    claim["recipe"]["expectation"]["expected_exit_codes"] = [0]
    claim["recipe"]["expectation"]["expected_nonzero_exit"] = False
    path.write_text(json.dumps(value))
    assert learn(state, path)[1]["status"] == "REJECTED"
    code, found = recall(state, tmp_path, claim)
    assert code == 0 and found["eligible_count"] == 2, found
    records = {item["experience"]["operation_id"]: item["experience"] for item in found["selected"]}
    assert records["negative-observation"]["verification"] == "VERIFIED"
    assert records["negative-observation"]["execution"] == "EXITED_NONZERO"
    assert records["unsatisfied-observation"]["verification"] == "UNVERIFIED"
    assert records["unsatisfied-observation"]["execution"] == "EXITED_NONZERO"
    assert all(item["applicability"] == "UNASSESSED" for item in records.values())
    code, limited = recall(state, tmp_path, claim, limit=1)
    assert code == 0 and len(limited["selected"]) == 1 and limited["omitted_by_limit"] == 1
    claim["operation_id"] = "reused-negative-observation"
    claim["recipe"]["expectation"]["expected_exit_codes"] = [3]
    claim["recipe"]["expectation"]["expected_nonzero_exit"] = True
    path.write_text(json.dumps(value))
    assert learn(state, path)[1]["status"] == "ALREADY_KNOWN"
    code, recalled = recall(state, tmp_path, claim)
    assert code == 0, recalled
    reused = next(item["experience"] for item in recalled["selected"]
                  if item["experience"]["operation_id"] == claim["operation_id"])
    assert reused["status"] == "ALREADY_KNOWN" and reused["origin"] == "PUBLISHED_PROOF"
    assert reused["observation_operation_id"] == "negative-observation"
    assert reused["observations"] == []  # No second command was observed.
