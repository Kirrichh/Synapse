"""§32: signed observations and missing samples retain their actual meaning."""

from acceptance.stage4.stage16.assessment import describe_samples, dispatch_duration, summarize_runs


def test_negative_net_difference_and_variation_are_reported_without_clipping():
    observed = describe_samples([-4, -2, 0])
    assert observed["mean"] == "-2"
    assert observed["sample_stddev"] == "2"
    assert observed["minimum"] == "-4"
    assert observed["maximum"] == "0"


def test_a_missing_pair_cannot_be_dropped_or_imputed_as_zero():
    observed = describe_samples([10, None, -3])
    assert observed["status"] == "INCOMPLETE"
    assert observed["n"] == 3
    assert observed["mean"] is None
    assert describe_samples([10])["sample_stddev"] is None


def test_lost_dispatch_receipt_is_an_unknown_interval_after_successful_resume():
    slot = {"events": [
        {"kind": "STARTED", "payload": {}},
        {"kind": "RESUMING", "payload": {}},
        {"kind": "FINISHED", "payload": {"external_action_duration_ns": "12"}},
    ]}
    assert dispatch_duration(slot) == {"status": "INCOMPLETE", "total_ns": None, "observed_subtotal_ns": "12"}
    slot["events"].insert(1, {"kind": "APPROVAL_REQUIRED", "payload": {"external_action_duration_ns": "5"}})
    assert dispatch_duration(slot) == {"status": "COMPLETE", "total_ns": "17", "observed_subtotal_ns": "17"}


def test_per_task_summary_keeps_failed_and_unstarted_replicas_in_the_denominator():
    common = {"task_id": "task", "arm": "GOLD", "provider_money": None, "replicate_parameters_match": True}
    rows = [{**common, "execution_state": "FINISHED", "outcome": {"task_resolved": True},
                "provider_tokens": 18, "elapsed_seconds": "2"},
            {**common, "execution_state": "FINISHED", "outcome": {"task_resolved": False},
                "provider_tokens": 36, "elapsed_seconds": "4"},
            {**common, "execution_state": "PLANNED", "outcome": None,
                "provider_tokens": None, "elapsed_seconds": None}]
    result, = summarize_runs(rows)
    assert (result["requested_runs"], result["verified_resolved"], result["unresolved"], result["outcome_unknown"]) == (3, 1, 1, 1)
    assert result["samples"]["provider_tokens"]["mean"] is None
    assert result["samples"]["provider_money"]["mean"] is None
    complete, = summarize_runs(rows[:2])
    assert complete["samples"]["provider_tokens"]["mean"] == "27"
    assert complete["samples"]["elapsed_seconds"]["mean"] == "3"
    rows[1]["replicate_parameters_match"] = False
    mismatch, = summarize_runs(rows[:2])
    assert mismatch["samples"]["provider_tokens"]["mean"] is None
