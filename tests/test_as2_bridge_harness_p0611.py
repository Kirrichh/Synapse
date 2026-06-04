"""P0.6.11 AS2 bridge fixture corpus / Host Pre-Stage harness.

This harness validates bridge-boundary fixtures only. It intentionally does not
implement or call a bridge. It does not import AgentRuntime, Environment, or any
legacy runtime surface. Positive fixtures prove that Host Pre-Stage outputs can
produce AS2-compatible explicit inputs by validating those inputs with the
standalone AS2 validation boundary. Negative fixtures are data-only contracts for
future bridge implementation.

Scope discipline:
  - fixture/schema validation only;
  - positive expected_as2_inputs -> validate_as2_inputs(...);
  - no project_validated_as2_inputs(...) calls;
  - no bridge code;
  - no AgentRuntime / Environment imports;
  - no feature flag implementation;
  - bridge-specific errors remain string identifiers.
"""

import json
import ast
import re
from pathlib import Path

import pytest

from synapse.agent_snapshot_adapter import validate_as2_inputs


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "as2_bridge"
SCHEMA_VERSION = "alpha3g.as2_bridge_fixture.v1"

EXPECTED_POSITIVE = {
    "positive_bridge_minimal_host_prestage",
    "positive_bridge_empty_memory_refs",
    "positive_bridge_no_live_tools_with_empty_grants",
    "positive_bridge_minimal_host_prestage_with_audit_context",
}

EXPECTED_NEGATIVE = {
    "negative_bridge_uses_agentruntime_todict",
    "negative_bridge_reads_live_tools",
    "negative_bridge_inline_memory_not_externalized",
    "negative_bridge_missing_identity_source",
    "negative_bridge_missing_definition_source",
    "negative_bridge_missing_model_registry",
    "negative_bridge_missing_capability_grants",
    "negative_bridge_host_io_failure_during_memory_externalization",
    "negative_bridge_environment_json_safe_used",
    "negative_bridge_actor_mailbox_used_as_snapshot_input",
    "negative_bridge_uses_agentruntime_name_as_identity",
    "negative_bridge_uses_agentruntime_model_as_model_ref",
}

EXPECTED_FIXTURES = EXPECTED_POSITIVE | EXPECTED_NEGATIVE

FORBIDDEN_READS = {
    "AgentRuntime.to_dict()",
    "AgentRuntime.name",
    "AgentRuntime.model",
    "AgentRuntime.memory",
    "AgentRuntime.tools",
    "AgentRuntime.tools.keys()",
    "AgentRuntime.llm",
    "AgentRuntime.env",
    "Environment._json_safe()",
    "interpreter hidden state",
    "actor runtime mailbox",
    "scheduler timers",
    "sockets / process handles",
}

PROTOCOL_STEPS = {f"Step {index}" for index in range(0, 11)}

BRIDGE_ERROR_NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]+Error$")
BRIDGE_RFC_REF_RE = re.compile(r"^AS2-LEGACY-BRIDGE-DESIGN\.md §[0-9]+(\.[0-9]+)*$")


def _fixture_paths():
    return sorted(FIXTURE_DIR.glob("*.json"), key=lambda path: path.name)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _all_fixtures():
    return [(path, _load(path)) for path in _fixture_paths()]


def _positive_fixtures():
    return [(path, data) for path, data in _all_fixtures() if data["polarity"] == "positive"]


def _negative_fixtures():
    return [(path, data) for path, data in _all_fixtures() if data["polarity"] == "negative"]


def _validate_with_standalone_as2(expected_as2_inputs):
    """Validate bridge-produced explicit inputs against the existing AS2 boundary.

    This helper intentionally calls only validate_as2_inputs(...). It must not
    call any projection/construction function: bridge fixtures are an input
    compatibility oracle, not a snapshot construction test.
    """

    return validate_as2_inputs(
        adapter_identity_context=expected_as2_inputs.get("adapter_identity_context"),
        adapter_definition_source=expected_as2_inputs.get("adapter_definition_source"),
        static_model_registry=expected_as2_inputs.get("static_model_registry"),
        memory_ref_source=expected_as2_inputs.get("memory_ref_source"),
        capability_grant_source=expected_as2_inputs.get("capability_grant_source"),
        model_selection_source=expected_as2_inputs.get("model_selection_source"),
    )


def test_bridge_fixture_directory_contains_approved_p0611_corpus():
    observed = {path.stem for path in _fixture_paths()}
    assert observed == EXPECTED_FIXTURES


def test_bridge_fixtures_are_deterministic_sorted_json():
    for path, data in _all_fixtures():
        expected = json.dumps(data, indent=2, sort_keys=True) + "\n"
        assert path.read_text(encoding="utf-8") == expected, path.name


def test_bridge_fixtures_have_required_schema_and_case_identity():
    required = {
        "schema_version",
        "case_id",
        "description",
        "polarity",
        "bridge_stage",
        "legacy_runtime_shape",
        "host_prestage_outputs",
        "expected_as2_inputs",
        "forbidden_reads",
        "protocol_steps",
        "expected_error",
        "expected_error_context",
        "rationale",
    }
    for path, data in _all_fixtures():
        assert required.issubset(data), path.name
        assert data["schema_version"] == SCHEMA_VERSION
        assert data["case_id"] == path.stem
        assert data["polarity"] in {"positive", "negative"}
        assert data["bridge_stage"] == "host_prestage"
        assert isinstance(data["description"], str) and data["description"].strip()
        assert isinstance(data["rationale"], str) and data["rationale"].strip()
        assert isinstance(data["forbidden_reads"], list), path.name
        assert isinstance(data["protocol_steps"], list), path.name


def test_legacy_runtime_shape_is_documentary_mocked_json_only():
    for path, data in _all_fixtures():
        shape = data["legacy_runtime_shape"]
        assert shape["source_kind"] == "mocked_legacy_runtime_shape", path.name
        assert isinstance(shape.get("fields_present"), dict), path.name
        notes = "\n".join(shape.get("notes", []))
        assert "documentary shape only" in notes, path.name
        assert "must not be treated as canonical input" in notes, path.name
        assert "must not be passed to AS2 validation" in notes, path.name


def test_positive_bridge_fixtures_validate_expected_as2_inputs_against_standalone_boundary():
    for path, data in _positive_fixtures():
        assert data["expected_error"] is None, path.name
        assert data["expected_error_context"] is None, path.name
        assert isinstance(data["host_prestage_outputs"], dict), path.name
        expected_as2_inputs = data["expected_as2_inputs"]
        assert isinstance(expected_as2_inputs, dict), path.name
        assert _validate_with_standalone_as2(expected_as2_inputs) is None


def test_positive_bridge_fixtures_use_canonical_model_selection_source():
    """P0.6.20 bridge fixtures emit only canonical model selection input."""

    removed_key = "legacy_" + "agent_runtime_to_dict"
    for path, data in _positive_fixtures():
        selector = data["expected_as2_inputs"].get("model_selection_source")
        assert selector == {"model": "mock-agent-model"}, path.name
        assert removed_key not in data["expected_as2_inputs"], path.name


def test_negative_bridge_fixtures_use_string_error_identifiers_and_context():
    for path, data in _negative_fixtures():
        assert data["host_prestage_outputs"] is None, path.name
        assert data["expected_as2_inputs"] is None, path.name
        assert BRIDGE_ERROR_NAME_RE.match(data["expected_error"]), path.name
        assert isinstance(data["expected_error"], str), path.name
        context = data["expected_error_context"]
        assert isinstance(context, dict), path.name
        assert BRIDGE_RFC_REF_RE.match(context.get("rfc_reference", "")), path.name
        assert isinstance(context.get("violated_field"), str) and context["violated_field"], path.name


def test_forbidden_reads_registry_has_complete_negative_fixture_coverage():
    covered = set()
    for _, data in _negative_fixtures():
        covered.update(data["forbidden_reads"])
    assert FORBIDDEN_READS <= covered


def test_host_prestage_protocol_steps_have_complete_fixture_coverage():
    covered = set()
    for _, data in _all_fixtures():
        covered.update(data["protocol_steps"])
    assert PROTOCOL_STEPS <= covered


def test_negative_forbidden_read_entries_are_known_registry_items():
    for path, data in _negative_fixtures():
        for forbidden_read in data["forbidden_reads"]:
            assert forbidden_read in FORBIDDEN_READS, f"{path.name}: {forbidden_read}"


def test_host_io_failure_is_modeled_as_deterministic_host_stage_failure():
    data = _load(FIXTURE_DIR / "negative_bridge_host_io_failure_during_memory_externalization.json")
    failure = data["host_stage_failure"]
    assert failure["failure_type"] == "HostPreStageIOError"
    assert failure["failure_phase"] == "memory_externalization"
    assert failure["deterministic_trigger"] == "memory_externalization_exceeds_quota"
    assert failure["as2_inputs_available"] is False
    assert data["expected_as2_inputs"] is None


def test_bridge_harness_does_not_import_legacy_runtime_modules():
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    forbidden_modules = {"synapse.agent_runtime", "synapse.environment"}
    forbidden_names = {"AgentRuntime", "Environment"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden_modules
        elif isinstance(node, ast.ImportFrom):
            assert node.module not in forbidden_modules
        elif isinstance(node, ast.Name):
            assert node.id not in forbidden_names


def test_bridge_harness_keeps_bridge_errors_as_strings_only():
    for _, data in _negative_fixtures():
        assert isinstance(data["expected_error"], str)
        assert data["expected_error"].endswith("Error")
