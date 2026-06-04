import pytest

from synapse.canonical_path import (
    CanonicalPathError,
    canonical_key_decode,
    canonical_key_encode,
    make_env_path,
    make_memory_path,
    parse_canonical_path,
)
from synapse.state_overlay import (
    CanonicalSerializationError,
    OverlayPathError,
    StateOverlay,
    StateOverlayError,
    WriteSet,
    WriteSetEntry,
)


def test_canonical_key_encode_ascii_safe():
    assert canonical_key_encode("session_token-1.2") == "session_token-1.2"
    assert make_memory_path("session_token") == "/memory/session_token"


def test_canonical_key_encode_unicode_nfc():
    # e + combining acute normalizes to the same key/path as U+00E9.
    assert canonical_key_encode("e\u0301") == "%C3%A9"
    assert canonical_key_decode("%C3%A9") == "é"


def test_canonical_key_encode_percent_uppercase_and_ambiguous_literals():
    assert canonical_key_encode("a/b") == "a%2Fb"
    assert canonical_key_encode("a~b") == "a%7Eb"
    assert canonical_key_encode("%") == "%25"
    with pytest.raises(CanonicalPathError):
        canonical_key_decode("a%2fb")
    with pytest.raises(CanonicalPathError):
        canonical_key_decode("a/b")


def test_memory_empty_key_path_valid_and_missing_trailing_slash_invalid():
    parsed = parse_canonical_path("/memory/")
    assert parsed.namespace == "memory"
    assert parsed.key == ""
    with pytest.raises(CanonicalPathError):
        parse_canonical_path("/memory")


def test_parse_rejects_bare_unknown_extra_and_noncanonical_paths():
    invalid = [
        "env/x",
        "/",
        "/agent/id",
        "/env/",
        "/env/not-valid-name",
        "/memory/a/b",
        "/memory/a%2fb",
        "/memory/a~1b",
    ]
    for path in invalid:
        with pytest.raises(CanonicalPathError):
            parse_canonical_path(path)


def test_env_path_identifier_validation():
    assert make_env_path("user_1") == "/env/user_1"
    with pytest.raises(CanonicalPathError):
        make_env_path("1user")


def test_state_overlay_copy_on_write_get_mutation_does_not_touch_base():
    base = {"env": {"profile": {"name": "Ada"}}, "memory": {}}
    overlay = StateOverlay(base)

    profile = overlay.get("/env/profile")
    profile["name"] = "Grace"

    assert base["env"]["profile"]["name"] == "Ada"
    assert overlay.dirty_paths == ("/env/profile",)
    write_set = overlay.commit()
    assert isinstance(write_set, WriteSet)
    assert isinstance(write_set[0], WriteSetEntry)
    serialized = write_set.to_list()
    assert serialized == [
        {
            "path": "/env/profile",
            "granularity": "top_level",
            "op": "replace",
            "old_value_hash": serialized[0]["old_value_hash"],
            "new_value": {"name": "Grace"},
            "new_value_hash": serialized[0]["new_value_hash"],
        }
    ]


def test_state_overlay_noop_write_not_dirty_and_commit_empty():
    base = {"env": {"x": 1}, "memory": {}}
    overlay = StateOverlay(base)
    overlay.set("/env/x", 1)
    assert overlay.dirty_paths == ()
    assert overlay.commit().to_list() == []


def test_state_overlay_set_delete_and_sorted_write_set():
    base = {"env": {"x": 1, "z": 3}, "memory": {"a/b": "old"}}
    overlay = StateOverlay(base)
    overlay.set("/memory/a%2Fb", "new")
    overlay.set("/env/y", {"nested": [1, 2]})
    overlay.delete("/env/z")

    assert overlay.dirty_paths == ("/env/y", "/env/z", "/memory/a%2Fb")
    write_set = overlay.commit()
    assert isinstance(write_set, WriteSet)
    assert [entry.path for entry in write_set] == ["/env/y", "/env/z", "/memory/a%2Fb"]
    assert write_set[0].op == "replace"
    assert write_set[1].op == "delete"
    assert "new_value" not in write_set[1].to_dict()
    assert write_set[2].new_value == "new"


def test_state_overlay_discard_releases_overlay_without_base_mutation():
    base = {"env": {"x": [1]}, "memory": {}}
    overlay = StateOverlay(base)
    value = overlay.get("/env/x")
    value.append(2)
    overlay.discard()
    assert base["env"]["x"] == [1]
    with pytest.raises(StateOverlayError):
        overlay.get("/env/x")


def test_state_overlay_rejects_invalid_paths_and_function_values():
    overlay = StateOverlay({"env": {}, "memory": {}})
    with pytest.raises(OverlayPathError):
        overlay.set("x", 1)
    with pytest.raises(CanonicalSerializationError):
        overlay.set("/env/f", lambda: None)


def test_state_overlay_canonical_hash_changes_with_overlay_state():
    overlay = StateOverlay({"env": {"x": 1}, "memory": {}})
    before = overlay.canonical_hash()
    overlay.set("/env/x", 2)
    after = overlay.canonical_hash()
    assert before != after


def test_state_overlay_delete_then_reset_same_path():
    overlay = StateOverlay({"env": {"x": 1}, "memory": {}})
    overlay.delete("/env/x")
    overlay.set("/env/x", 2)

    write_set = overlay.commit()
    assert len(write_set) == 1
    assert write_set[0].path == "/env/x"
    assert write_set[0].op == "replace"
    assert write_set[0].new_value == 2


def test_state_overlay_set_then_delete_new_path_elides_noop():
    overlay = StateOverlay({"env": {}, "memory": {}})
    overlay.set("/env/temp", {"v": 1})
    overlay.delete("/env/temp")

    assert overlay.dirty_paths == ()
    assert overlay.commit().to_list() == []


def test_state_overlay_commit_after_discard_raises():
    overlay = StateOverlay({"env": {"x": 1}, "memory": {}})
    overlay.set("/env/x", 2)
    overlay.discard()

    with pytest.raises(StateOverlayError):
        overlay.commit()


def test_state_overlay_unsupported_set_values_rejected():
    overlay = StateOverlay({"env": {}, "memory": {}})
    with pytest.raises(CanonicalSerializationError):
        overlay.set("/env/s", {1, 2, 3})
    with pytest.raises(CanonicalSerializationError):
        overlay.set("/env/bad_float", float("nan"))


def test_state_overlay_dict_non_string_key_rejected():
    overlay = StateOverlay({"env": {}, "memory": {}})
    with pytest.raises(CanonicalSerializationError):
        overlay.set("/env/bad", {1: "one"})


def test_state_overlay_canonical_hash_stability_for_equivalent_ordering():
    left = StateOverlay({"env": {"obj": {"b": 2, "a": 1}}, "memory": {}})
    right = StateOverlay({"env": {"obj": {"a": 1, "b": 2}}, "memory": {}})

    assert left.canonical_hash() == right.canonical_hash()


def test_malformed_percent_escape_rejected():
    for path in ["/memory/%", "/memory/%2", "/memory/%2G", "/memory/%c3%A9"]:
        with pytest.raises(CanonicalPathError):
            parse_canonical_path(path)


def test_state_overlay_profile_defaults_to_legacy_local_json():
    from synapse.canonical_service import ALPHA3G_LOCAL_JSON_PROFILE, alpha3g_local_json_hash
    from synapse.state_overlay import canonical_value_hash

    overlay = StateOverlay({"env": {"x": {"a": 1}}, "memory": {}})
    assert overlay.profile == ALPHA3G_LOCAL_JSON_PROFILE
    assert canonical_value_hash({"a": 1}) == alpha3g_local_json_hash({"a": 1})


def test_state_overlay_stable_profile_hash_matches_service():
    from synapse.canonical_service import STABLE_CANONICAL_PROFILE, stable_canonical_hash

    overlay = StateOverlay({"env": {"x": 1}, "memory": {}}, profile=STABLE_CANONICAL_PROFILE)
    assert overlay.profile == STABLE_CANONICAL_PROFILE
    assert overlay.canonical_hash() == stable_canonical_hash({"env": {"x": 1}, "memory": {}})


def test_state_overlay_stable_profile_accepts_stable_wrappers_without_changing_default():
    from synapse.canonical_service import STABLE_CANONICAL_PROFILE

    legacy_overlay = StateOverlay({"env": {}, "memory": {}})
    with pytest.raises(CanonicalSerializationError):
        legacy_overlay.set("/env/raw_bytes", b"\x01\x02")

    stable_overlay = StateOverlay({"env": {}, "memory": {}}, profile=STABLE_CANONICAL_PROFILE)
    stable_overlay.set("/env/raw_bytes", b"\x01\x02")
    write_set = stable_overlay.commit()

    assert write_set.to_list() == [
        {
            "path": "/env/raw_bytes",
            "granularity": "top_level",
            "op": "replace",
            "old_value_hash": None,
            "new_value": {"__type__": "bytes", "encoding": "base64url-nopad", "data": "AQI"},
            "new_value_hash": write_set[0].new_value_hash,
            "value_profile": STABLE_CANONICAL_PROFILE,
        }
    ]


def test_state_overlay_stable_profile_normalized_string_noop_vs_legacy_dirty():
    from synapse.canonical_service import STABLE_CANONICAL_PROFILE

    legacy = StateOverlay({"env": {"name": "é"}, "memory": {}})
    legacy.set("/env/name", "e\u0301")
    assert legacy.dirty_paths == ("/env/name",)

    stable = StateOverlay({"env": {"name": "é"}, "memory": {}}, profile=STABLE_CANONICAL_PROFILE)
    stable.set("/env/name", "e\u0301")
    assert stable.dirty_paths == ()
    assert stable.commit().to_list() == []


def test_state_overlay_stable_profile_set_ordering_is_deterministic():
    from synapse.canonical_service import STABLE_CANONICAL_PROFILE

    left = StateOverlay({"env": {}, "memory": {}}, profile=STABLE_CANONICAL_PROFILE)
    right = StateOverlay({"env": {}, "memory": {}}, profile=STABLE_CANONICAL_PROFILE)
    left.set("/env/items", {"b", "a", "c"})
    right.set("/env/items", {"c", "b", "a"})

    left_ws = left.commit()
    right_ws = right.commit()
    assert left_ws[0].new_value == right_ws[0].new_value
    assert left_ws[0].new_value_hash == right_ws[0].new_value_hash


def test_state_overlay_rejects_unknown_profile():
    with pytest.raises(CanonicalSerializationError):
        StateOverlay({"env": {}, "memory": {}}, profile="unknown-profile.v1")
