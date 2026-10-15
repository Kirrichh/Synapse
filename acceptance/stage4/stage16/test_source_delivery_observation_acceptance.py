"""Shared source verification observations retain their complete operation scope."""

import sys

import pytest

from acceptance.stage4.stage16._source_delivery_case import SOURCES, capture, publish_case, source_records
from synapse.experiments.gold.source_verification import canonical
from synapse.experiments.gold.stage10.context_codec import decode_base64url


@pytest.fixture(scope="module")
def verified_migration(tmp_path_factory):
    recipe = {
        "command": [sys.executable, "-B", "-c",
            "from routing.ring import route_owner; from billing.ledger import BILLING_RULE; "
            "assert all(route_owner(p, e, range(13)) == (p * 17 + e) % 24 "
            "for p in range(1536) for e in range(8)); "
            "print('CHECK_PASSED shared operation', BILLING_RULE)"],
        "expectation": {"expected_exit_codes": [0], "expected_nonzero_exit": False,
            "combined_output_contains": ["CHECK_PASSED", "IRRELEVANT_SETTLEMENT_CURRENCY_SENTINEL"],
            "combined_output_not_contains": [], "timeout_seconds": 10},
    }
    return publish_case(tmp_path_factory.mktemp("source-observation-scope"), recipe=recipe)


def test_partial_selection_omits_indivisible_recipe_and_output(verified_migration):
    snapshot, _ = capture(verified_migration, "scoped-command", ("routing/ring.py",))
    original = canonical(snapshot)
    record, = source_records(snapshot)
    assert record["declared_recipe"] is None and record["command_result"] is None
    assert record["operation_observation_scope"] == "OMITTED_UNSELECTED_SOURCE_PATHS"
    assert record["verification"] == "VERIFIED" and record["execution"] == "EXITED_ZERO"
    assert record["applicability"] == "UNASSESSED"
    assert b"IRRELEVANT_SETTLEMENT_CURRENCY_SENTINEL" not in canonical(record)
    held = snapshot["recall"]["selected"][0]["experience"]
    assert "IRRELEVANT_SETTLEMENT_CURRENCY_SENTINEL" in held["command_result"]["stdout"]
    assert held["claim"]["recipe"]["expectation"]["combined_output_contains"][1].startswith("IRRELEVANT_")
    assert canonical(snapshot) == original


def test_complete_selection_retains_exact_command_observation(verified_migration):
    snapshot, _ = capture(verified_migration, "whole-command", tuple(sorted(SOURCES)))
    record, = source_records(snapshot)
    held = snapshot["recall"]["selected"][0]["experience"]
    assert record["declared_recipe"] == held["claim"]["recipe"]
    assert record["command_result"] == {key: value for key, value in held["command_result"].items()
        if key in {"command", "argv", "returncode", "stdout", "stderr", "status", "diagnostics", "duration_ms"}}
    assert "operation_observation_scope" not in record
    assert record["execution"] == "EXITED_ZERO" and record["command_result"]["returncode"] == 0
    delivered = {item["path"]: decode_base64url(item["content_base64url"]) for item in record["sources"]}
    assert delivered == {path: raw.encode() for path, raw in SOURCES.items()}
