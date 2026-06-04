import json
import math

import pytest

from synapse.canonical_values import (
    PROFILE_ID,
    SAFE_INTEGER_MAX,
    CanonicalSerializationError,
    canonical_bytes,
    canonical_hash,
    canonical_json_bytes,
    canonicalize,
    is_canonical_serializable,
)


def test_profile_and_hash_stability_for_dict_ordering():
    left = {"b": 2, "a": [1, {"z": True, "y": None}]}
    right = {"a": [1, {"y": None, "z": True}], "b": 2}

    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert canonical_hash(left) == canonical_hash(right)
    payload = canonical_bytes(left)
    assert payload.profile == PROFILE_ID
    assert payload.sha256() == canonical_hash(left)


def test_unicode_values_and_keys_are_nfc_normalized():
    decomposed = "e\u0301"
    composed = "é"

    assert canonicalize(decomposed) == composed
    assert canonical_json_bytes({decomposed: decomposed}) == canonical_json_bytes({composed: composed})


def test_lone_surrogate_rejected():
    with pytest.raises(CanonicalSerializationError):
        canonical_json_bytes("bad\ud800")


def test_safe_integer_boundary_and_large_int_wrapper():
    assert canonicalize(SAFE_INTEGER_MAX) == SAFE_INTEGER_MAX
    assert canonicalize(SAFE_INTEGER_MAX + 1) == {
        "__type__": "int",
        "value": str(SAFE_INTEGER_MAX + 1),
    }
    assert canonicalize(-(SAFE_INTEGER_MAX + 1)) == {
        "__type__": "int",
        "value": str(-(SAFE_INTEGER_MAX + 1)),
    }


def test_float_edge_cases():
    assert canonicalize(-0.0) == 0.0
    assert canonical_json_bytes(-0.0) == canonical_json_bytes(0.0)

    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(CanonicalSerializationError):
            canonical_json_bytes(value)


def test_non_string_dict_keys_rejected_to_prevent_json_coercion():
    with pytest.raises(CanonicalSerializationError):
        canonical_json_bytes({1: "one"})


def test_dict_key_collision_after_nfc_normalization_rejected():
    with pytest.raises(CanonicalSerializationError):
        canonical_json_bytes({"é": 1, "e\u0301": 2})


def test_bytes_encode_as_base64url_nopad_wrapper():
    assert canonicalize(b"Hello") == {
        "__type__": "bytes",
        "encoding": "base64url-nopad",
        "data": "SGVsbG8",
    }
    assert b"=" not in canonical_json_bytes(b"Hello")


def test_sets_use_typed_wrapper_and_canonical_sorting():
    first = {"b", "a", "é"}
    second = {"é", "b", "a"}
    assert canonical_json_bytes(first) == canonical_json_bytes(second)

    payload = json.loads(canonical_json_bytes(first).decode("utf-8"))
    assert payload["__type__"] == "set"
    assert payload["items"] == ["a", "b", "é"]


def test_nested_set_sorting_uses_canonical_bytes_not_python_repr():
    first = frozenset({(2, 1), (1, 2)})
    second = frozenset({(1, 2), (2, 1)})
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert json.loads(canonical_json_bytes(first).decode("utf-8")) == {
        "__type__": "set",
        "items": [[1, 2], [2, 1]],
    }


def test_cycles_rejected_with_canonical_error():
    value = []
    value.append(value)
    with pytest.raises(CanonicalSerializationError, match="cycle detected"):
        canonical_json_bytes(value)


def test_functions_callables_and_host_objects_rejected():
    class HostObject:
        pass

    for value in (lambda: None, print, HostObject()):
        with pytest.raises(CanonicalSerializationError):
            canonical_json_bytes(value)


def test_is_canonical_serializable_helper_is_fail_closed():
    assert is_canonical_serializable({"ok": [1, 2, b"x"]}) is True
    assert is_canonical_serializable({"bad": object()}) is False

from synapse.canonical_values import MAX_NESTING_DEPTH, PROFILE_VERSION


def test_profile_version_exported_for_future_registries():
    assert PROFILE_ID == "stable-canonical.v1"
    assert PROFILE_VERSION == "v1"


def test_canonical_hash_has_known_cross_platform_fixture():
    value = {
        "text": "e\u0301",
        "bytes": b"\xff\x00",
        "large": 2**70,
        "set": frozenset({"b", "a"}),
        "float": -0.0,
    }

    assert canonical_json_bytes(value) == (
        b'{"bytes":{"__type__":"bytes","data":"_wA","encoding":"base64url-nopad"},'
        b'"float":0.0,'
        b'"large":{"__type__":"int","value":"1180591620717411303424"},'
        b'"set":{"__type__":"set","items":["a","b"]},'
        b'"text":"\xc3\xa9"}'
    )
    assert canonical_hash(value) == "sha256:27fb082229b759babbbb95ede415939ed7db4c25862605587e643af23793797d"


def test_unicode_edge_cases_preserve_valid_scalars_and_normalize_combining_marks():
    family = "👩\u200d💻"
    assert canonicalize(family) == family
    assert canonicalize("A\u030a") == "Å"


def test_deep_nesting_within_limit_is_stable_and_over_limit_fails_closed():
    value = "leaf"
    for _ in range(32):
        value = [value]
    assert canonical_json_bytes(value).startswith(b"[[[[")

    too_deep = "leaf"
    for _ in range(MAX_NESTING_DEPTH + 2):
        too_deep = [too_deep]
    with pytest.raises(CanonicalSerializationError, match="nesting exceeds limit"):
        canonical_json_bytes(too_deep)


def test_typed_wrappers_round_trip_through_json_without_padding_or_float_artifacts():
    payload = json.loads(canonical_json_bytes({"bytes": b"\x00\xff", "big": 2**80, "set": {3, 1, 2}}))

    assert payload["bytes"] == {"__type__": "bytes", "encoding": "base64url-nopad", "data": "AP8"}
    assert "=" not in payload["bytes"]["data"]
    assert payload["big"] == {"__type__": "int", "value": str(2**80)}
    assert payload["set"] == {"__type__": "set", "items": [1, 2, 3]}


def test_set_with_mixed_canonical_types_sorts_by_canonical_bytes():
    value = {"10", "2", 3, b"a"}
    payload = json.loads(canonical_json_bytes(value).decode("utf-8"))

    assert payload["__type__"] == "set"
    assert payload["items"] == ["10", "2", 3, {"__type__": "bytes", "data": "YQ", "encoding": "base64url-nopad"}]


def test_error_messages_include_forensic_path_for_nested_rejection():
    with pytest.raises(CanonicalSerializationError, match=r"\$\.outer\[0\]\.bad"):
        canonical_json_bytes({"outer": [{"bad": object()}]})


def test_mapping_cycle_and_set_cycle_fail_with_project_error():
    mapping = {}
    mapping["self"] = mapping
    with pytest.raises(CanonicalSerializationError, match="cycle detected"):
        canonical_json_bytes(mapping)

    value = []
    container = {"items": value}
    value.append(container)
    with pytest.raises(CanonicalSerializationError, match="cycle detected"):
        canonical_json_bytes(container)
