"""§32: signed observations and missing samples retain their actual meaning."""

from acceptance.stage4.stage16.assessment import summarize_differences


def test_negative_net_difference_and_variation_are_reported_without_clipping():
    observed = summarize_differences([-4, -2, 0])
    assert observed["mean"] == "-2"
    assert observed["sample_stddev"] == "2"
    assert observed["minimum"] == "-4"
    assert observed["maximum"] == "0"


def test_a_missing_pair_cannot_be_dropped_or_imputed_as_zero():
    observed = summarize_differences([10, None, -3])
    assert observed["status"] == "INCOMPLETE"
    assert observed["n"] == 3
    assert observed["mean"] is None
    assert summarize_differences([10])["sample_stddev"] is None
