"""P0.6.4 AS2 fixture/invariant matrix validator.

This test module validates the static AS2 fixture corpus introduced by P0.6.4.
It is intentionally data-only:

- no AS2 adapter import;
- no ``to_agent_snapshot()`` call;
- no AS2 exception classes;
- no legacy runtime mutation;
- no provider/network/runtime lookup.

The purpose is to make the approved AS2 RFC executable as fixture contracts
before adapter implementation is authorized.
"""

import json
import re
from pathlib import Path


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "as2"

SCHEMA_VERSION = "alpha3g.as2_fixture.v1"
PROFILE = "stable-canonical.v1"

EXPECTED_FIXTURES = {
    "positive_minimal_valid_projection_inputs",
    "negative_missing_identity_context",
    "negative_incomplete_identity_context",
    "negative_unknown_model_ref",
    "negative_missing_memory_ref_source",
    "negative_memory_space_mismatch",
    "negative_missing_capability_grant_source",
    "negative_legacy_envelope_conflict",
    "negative_inline_memory_rejected",
    "negative_subagent_out_of_scope",
    "negative_ambient_authority_forbidden",
}

APPROVED_ERROR_NAMES = {
    "AdapterIdentityContextMissingError",
    "AdapterIdentityContextIncompleteError",
    "ModelRefUnknownError",
    "MemoryRefSourceMissingError",
    "AdapterMemorySpaceMismatchError",
    "CapabilityGrantSourceMissingError",
    "CapabilityGrantInvalidRefError",
    "AdapterEnvelopeConflictError",
    "AdapterAmbientAuthorityError",
    "AdapterInlineMemoryRejectedError",
    "AdapterSubagentOutOfScopeError",
}

# P0.6.4 fixtures are intentionally mock-only. Real provider namespaces are a
# future registry/deployment concern and would introduce semantic drift.
ALLOWED_PROVIDER_NAMESPACES = {"mock"}

SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RFC_REFERENCE_RE = re.compile(r"^RFC-[A-Z0-9-]+\.md §[0-9]+(\.[0-9]+)*$")


def _fixture_paths():
    return sorted(FIXTURE_DIR.glob("*.json"), key=lambda p: p.name)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _all_fixtures():
    return [(path, _load(path)) for path in _fixture_paths()]


def _walk_values(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)


def _model_refs(fixture):
    for value in _walk_values(fixture):
        if value.get("type") == "model_ref":
            yield value


def test_fixture_directory_contains_exactly_p064_baseline_corpus():
    observed = {path.stem for path in _fixture_paths()}
    assert observed == EXPECTED_FIXTURES


def test_fixtures_are_written_in_deterministic_sorted_json_form():
    for path, data in _all_fixtures():
        expected = json.dumps(data, indent=2, sort_keys=True) + "\n"
        assert path.read_text(encoding="utf-8") == expected, path.name


def test_every_fixture_has_required_top_level_fields_and_case_id_matches_filename():
    required = {
        "aspect",
        "case_id",
        "expected_result",
        "inputs",
        "polarity",
        "profile",
        "rationale",
        "rfc_reference",
        "schema_version",
    }
    for path, data in _all_fixtures():
        assert required.issubset(data), path.name
        assert data["case_id"] == path.stem
        assert data["schema_version"] == SCHEMA_VERSION
        assert data["profile"] == PROFILE
        assert data["polarity"] in {"positive", "negative"}
        assert data["expected_result"] in {"valid", "error"}
        assert data["rfc_reference"].startswith("RFC-AGENT-SNAPSHOT-ADAPTER.md §")
        assert data["rationale"].strip()


def test_negative_fixtures_use_approved_non_generic_error_names():
    for path, data in _all_fixtures():
        if data["polarity"] == "negative":
            assert data["expected_result"] == "error"
            assert data.get("expected_error") in APPROVED_ERROR_NAMES, path.name
            assert data.get("expected_error") != "AdapterError"
            context = data.get("expected_error_context")
            assert isinstance(context, dict), path.name
            assert RFC_REFERENCE_RE.match(context.get("rfc_reference", "")), path.name
            assert isinstance(context.get("violated_field"), str) and context["violated_field"], path.name
        else:
            assert data["expected_result"] == "valid"
            assert "expected_error" not in data


def test_fixture_error_names_cover_p064_required_negative_paths():
    observed = {
        data["expected_error"]
        for _, data in _all_fixtures()
        if data["polarity"] == "negative"
    }
    required_for_p064 = {
        "AdapterIdentityContextMissingError",
        "AdapterIdentityContextIncompleteError",
        "ModelRefUnknownError",
        "MemoryRefSourceMissingError",
        "AdapterMemorySpaceMismatchError",
        "CapabilityGrantSourceMissingError",
        "AdapterEnvelopeConflictError",
        "AdapterAmbientAuthorityError",
        "AdapterInlineMemoryRejectedError",
        "AdapterSubagentOutOfScopeError",
    }
    assert observed == required_for_p064


def test_positive_fixture_contains_expected_derivation_record_shape():
    data = _load(FIXTURE_DIR / "positive_minimal_valid_projection_inputs.json")
    record = data["expected_derivation_record"]
    assert record["schema_version"] == "alpha3g.adapter_derivation.v1"
    assert record["profile"] == PROFILE
    assert set(record["input_hashes"]) == {
        "identity_context_hash",
        "adapter_definition_source_hash",
        "model_registry_snapshot_hash",
        "memory_ref_source_hash",
        "capability_grant_source_hash",
    }
    for digest in record["input_hashes"].values():
        assert SHA_RE.match(digest)
    assert record["memory_space_policy"]["policy_version"] == "alpha3g.memory_space_policy.v1"
    assert SHA_RE.match(record["memory_space_policy"]["expected_memory_space_id"])


def test_static_model_registry_fixtures_are_mock_only_and_content_addressed():
    for path, data in _all_fixtures():
        for ref in _model_refs(data):
            assert ref["provider_namespace"] in ALLOWED_PROVIDER_NAMESPACES, path.name
            assert ref["schema_version"] == "alpha3g.model_ref.v1"
            assert ref["profile"] == PROFILE
            assert SHA_RE.match(ref["capability_profile_hash"])
        registry = data["inputs"].get("static_model_registry")
        if registry is not None:
            assert registry["type"] == "static_model_registry"
            assert registry["schema_version"] == "alpha3g.static_model_registry.v1"
            assert SHA_RE.match(registry["registry_snapshot_hash"])


def test_positive_empty_memory_is_explicit_empty_list_not_omission():
    data = _load(FIXTURE_DIR / "positive_minimal_valid_projection_inputs.json")
    source = data["inputs"]["memory_ref_source"]
    assert "refs" in source
    assert source["refs"] == []
    assert source["expected_memory_space_id"]
    assert source["memory_space_policy_version"] == "alpha3g.memory_space_policy.v1"


def test_missing_memory_source_fixture_omits_source_to_distinguish_from_empty_list():
    data = _load(FIXTURE_DIR / "negative_missing_memory_ref_source.json")
    assert "memory_ref_source" not in data["inputs"]
    assert data["expected_error"] == "MemoryRefSourceMissingError"


def test_memory_space_mismatch_fixture_structurally_contains_foreign_space():
    data = _load(FIXTURE_DIR / "negative_memory_space_mismatch.json")
    source = data["inputs"]["memory_ref_source"]
    expected = source["expected_memory_space_id"]
    refs = source["refs"]
    assert refs
    assert any(ref["memory_space_id"] != expected for ref in refs)
    assert data["expected_error"] == "AdapterMemorySpaceMismatchError"


def test_inline_memory_fixture_represents_forbidden_raw_memory_payload():
    data = _load(FIXTURE_DIR / "negative_inline_memory_rejected.json")
    assert "inline_memory_payload" in data["inputs"]
    assert "memory_ref_source" not in data["inputs"]
    assert data["expected_error"] == "AdapterInlineMemoryRejectedError"


def test_ambient_authority_fixture_is_metadata_only_and_lists_forbidden_calls():
    data = _load(FIXTURE_DIR / "negative_ambient_authority_forbidden.json")
    metadata = data["harness_metadata"]
    assert metadata["requires_sandbox_mock"] is True
    assert set(metadata["forbidden_calls"]) == {"time.time", "os.environ.get", "uuid.uuid4"}
    assert data["expected_error"] == "AdapterAmbientAuthorityError"


def test_legacy_envelope_conflict_fixture_contains_legacy_marker_only_as_negative_candidate():
    data = _load(FIXTURE_DIR / "negative_legacy_envelope_conflict.json")
    candidate = data["candidate_output_envelope"]
    assert candidate["__type__"] == "agent"
    assert "data" in candidate
    assert data["expected_error"] == "AdapterEnvelopeConflictError"


def test_subagent_fixture_contains_out_of_scope_runtime_graph_marker():
    data = _load(FIXTURE_DIR / "negative_subagent_out_of_scope.json")
    assert data["inputs"]["subagent_runtime_graph"]["nodes"][0]["kind"] == "SubAgentDef"
    assert data["expected_error"] == "AdapterSubagentOutOfScopeError"


def test_no_fixture_contains_json_null_values():
    for path, data in _all_fixtures():
        def assert_no_none(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    assert item is not None, f"{path.name}: null value at key {key!r}"
                    assert_no_none(item)
            elif isinstance(value, list):
                for item in value:
                    assert item is not None, f"{path.name}: null list item"
                    assert_no_none(item)
        assert_no_none(data)
