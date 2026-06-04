import inspect
import sys

import pytest

from synapse.canonical_service import (
    ALPHA3G_LOCAL_JSON_PROFILE,
    STABLE_CANONICAL_PROFILE,
    CanonicalProfileError,
    DriftCategory,
    ProfileHashComparison,
    alpha3g_local_json_hash,
    compare_profile_hashes,
    stable_canonical_bytes,
    stable_canonical_hash,
)
from synapse.canonical_values import CanonicalSerializationError, canonical_hash, canonical_json_bytes


def test_service_delegates_to_stable_canonical_core():
    value = {"text": "e\u0301", "bytes": b"x", "set": {2, 1}}
    assert stable_canonical_hash(value) == canonical_hash(value)
    assert stable_canonical_bytes(value) == canonical_json_bytes(value)


def test_service_rejects_unsupported_profile_fail_closed():
    with pytest.raises(CanonicalProfileError, match="unsupported canonical profile"):
        stable_canonical_hash({"x": 1}, profile="alpha3g.local-json.v1")
    with pytest.raises(CanonicalProfileError, match="unsupported canonical profile"):
        alpha3g_local_json_hash({"x": 1}, profile="stable-canonical.v1")


def test_service_fail_closed_on_unsupported_values():
    class HostObject:
        pass

    for value in (lambda: None, print, HostObject()):
        with pytest.raises(CanonicalSerializationError):
            stable_canonical_hash(value)


def test_compare_profile_hashes_reports_no_drift_for_legacy_safe_values():
    comparison = compare_profile_hashes({"a": [1, True, None], "b": "text"})
    assert isinstance(comparison, ProfileHashComparison)
    assert comparison.local_profile == ALPHA3G_LOCAL_JSON_PROFILE
    assert comparison.stable_profile == STABLE_CANONICAL_PROFILE
    assert comparison.drift_detected is False
    assert comparison.drift_category == DriftCategory.NONE.value
    assert comparison.local_hash == comparison.stable_hash


def test_compare_profile_hashes_detects_float_normalization_drift():
    comparison = compare_profile_hashes({"value": -0.0})
    assert comparison.drift_detected is True
    assert comparison.drift_category == DriftCategory.FLOAT_NORMALIZATION.value
    assert comparison.local_hash != comparison.stable_hash


def test_compare_profile_hashes_detects_large_int_wrapper_drift():
    comparison = compare_profile_hashes({"value": 2**70})
    assert comparison.drift_detected is True
    assert comparison.drift_category == DriftCategory.LARGE_INT_WRAPPER.value
    assert comparison.local_hash != comparison.stable_hash


def test_compare_profile_hashes_detects_set_ordering_or_local_type_rejection():
    comparison = compare_profile_hashes({"value": {"b", "a"}})
    assert comparison.drift_detected is True
    assert comparison.drift_category == DriftCategory.SET_ORDERING.value
    assert comparison.local_hash is None
    assert comparison.stable_hash is not None
    assert "CanonicalSerializationError" in comparison.local_error


def test_compare_profile_hashes_detects_bytes_wrapper_drift():
    comparison = compare_profile_hashes({"payload": b"abc"})
    assert comparison.drift_detected is True
    assert comparison.drift_category == DriftCategory.BYTES_WRAPPER.value
    assert comparison.local_hash is None
    assert comparison.stable_hash is not None


def test_compare_profile_hashes_detects_key_normalization_drift():
    comparison = compare_profile_hashes({"e\u0301": "value"})
    assert comparison.drift_detected is True
    assert comparison.drift_category == DriftCategory.KEY_NORMALIZATION.value
    assert comparison.local_hash != comparison.stable_hash


def test_compare_profile_hashes_detects_value_normalization_drift():
    comparison = compare_profile_hashes({"text": "A\u030a"})
    assert comparison.drift_detected is True
    assert comparison.drift_category == DriftCategory.VALUE_NORMALIZATION.value
    assert comparison.local_hash != comparison.stable_hash


def test_compare_profile_hashes_for_representative_integrate_payload_shape():
    value = {
        "env": {"user": "Кирилл", "count": 3},
        "memory": {"ключ": {"value": "данные", "tags": ["alpha", "beta"]}},
    }
    comparison = compare_profile_hashes(value)
    assert comparison.drift_detected is False
    assert comparison.drift_category == DriftCategory.NONE.value


def test_compare_profile_hashes_reports_both_rejected_for_host_object():
    comparison = compare_profile_hashes({"bad": object()})
    assert comparison.drift_detected is True
    assert comparison.drift_category == DriftCategory.BOTH_REJECTED.value
    assert comparison.local_hash is None
    assert comparison.stable_hash is None
    assert "CanonicalSerializationError" in comparison.local_error
    assert "CanonicalSerializationError" in comparison.stable_error


def test_service_module_does_not_import_runtime_consumers():
    import synapse.canonical_service as canonical_service

    source = inspect.getsource(canonical_service)
    forbidden_imports = (
        "state_overlay",
        "interpreter",
        "golden_replay",
        "actor_runtime",
        "cvm",
    )
    for forbidden in forbidden_imports:
        assert f"import {forbidden}" not in source
        assert f"from synapse.{forbidden}" not in source

    # The service itself must not load these modules as a side effect in a clean
    # process; in the full suite they may already exist, so this assertion is
    # limited to direct service globals.
    for forbidden in forbidden_imports:
        assert forbidden not in canonical_service.__dict__
