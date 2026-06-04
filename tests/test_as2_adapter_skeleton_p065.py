"""P0.6.5 AS2 flagged adapter skeleton tests.

These tests validate the isolated AS2 skeleton boundary introduced in
``synapse.agent_snapshot_adapter``. They intentionally do not exercise
projection logic and require that ``to_agent_snapshot`` does not exist yet.
"""

import ast
import json
from pathlib import Path

import pytest

from synapse import agent_snapshot_adapter as as2


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "as2"

EXPECTED_ERROR_NAMES = {
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

INPUT_ERROR_NAMES = {
    "AdapterIdentityContextMissingError",
    "AdapterIdentityContextIncompleteError",
    "MemoryRefSourceMissingError",
    "CapabilityGrantSourceMissingError",
}
MAPPING_ERROR_NAMES = {"ModelRefUnknownError", "CapabilityGrantInvalidRefError"}
INTEGRITY_ERROR_NAMES = {
    "AdapterMemorySpaceMismatchError",
    "AdapterEnvelopeConflictError",
    "AdapterAmbientAuthorityError",
    "AdapterInlineMemoryRejectedError",
    "AdapterSubagentOutOfScopeError",
}

FORBIDDEN_IMPORT_MODULES = {
    "synapse.builtins",
    "synapse.interpreter",
    "synapse.runtime.actor_runtime",
    "synapse.runtime",
    "synapse.cvm",
    "synapse.golden_replay",
    "synapse.state_overlay",
}


def _load_fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _all_fixtures():
    return [(path, _load_fixture(path)) for path in sorted(FIXTURE_DIR.glob("*.json"))]


def _negative_fixtures():
    return [(path, data) for path, data in _all_fixtures() if data["polarity"] == "negative"]


def _call_validate_for_fixture(data: dict) -> None:
    inputs = data["inputs"]
    as2.validate_as2_inputs(
        adapter_identity_context=inputs.get("adapter_identity_context"),
        static_model_registry=inputs.get("static_model_registry"),
        memory_ref_source=inputs.get("memory_ref_source"),
        capability_grant_source=inputs.get("capability_grant_source"),
        model_selection_source=inputs.get("model_selection_source"),
        candidate_output_envelope=data.get("candidate_output_envelope"),
        inline_memory_payload=inputs.get("inline_memory_payload"),
        subagent_runtime_graph=inputs.get("subagent_runtime_graph"),
        harness_metadata=data.get("harness_metadata"),
    )


def test_as2_skeleton_module_is_importable_and_quarantined():
    assert as2.AS2_ADAPTER_SKELETON_ENABLED is False
    assert not hasattr(as2, "to_agent_snapshot")


def test_error_taxonomy_contains_all_fixture_error_names():
    assert set(as2.ERROR_NAME_TO_CLASS) == EXPECTED_ERROR_NAMES | {"CapabilityGrantInvalidRefError"}
    for name in EXPECTED_ERROR_NAMES:
        cls = as2.ERROR_NAME_TO_CLASS[name]
        assert issubclass(cls, as2.AS2AdapterError)
        assert cls is not as2.AS2AdapterError


def test_error_hierarchy_matches_p065_boundary_groups():
    for name in INPUT_ERROR_NAMES:
        assert issubclass(as2.ERROR_NAME_TO_CLASS[name], as2.AS2AdapterInputError)
    for name in MAPPING_ERROR_NAMES:
        assert issubclass(as2.ERROR_NAME_TO_CLASS[name], as2.AS2AdapterMappingError)
    for name in INTEGRITY_ERROR_NAMES:
        assert issubclass(as2.ERROR_NAME_TO_CLASS[name], as2.AS2AdapterIntegrityError)


def test_expected_fixture_errors_are_leaf_classes_not_generic_base_errors():
    forbidden = {
        "AS2AdapterError",
        "AS2AdapterInputError",
        "AS2AdapterMappingError",
        "AS2AdapterIntegrityError",
        "AdapterError",
    }
    for path, data in _all_fixtures():
        if data["polarity"] == "negative":
            expected = data["expected_error"]
            assert expected not in forbidden, path.name
            assert expected in as2.ERROR_NAME_TO_CLASS, path.name


def test_validation_boundary_accepts_positive_fixture_without_projection():
    data = _load_fixture(FIXTURE_DIR / "positive_minimal_valid_projection_inputs.json")
    assert data["expected_result"] == "valid"
    assert _call_validate_for_fixture(data) is None


@pytest.mark.parametrize("path,data", _negative_fixtures())
def test_validation_boundary_raises_fixture_expected_error_for_negative_cases(path, data):
    expected_cls = as2.ERROR_NAME_TO_CLASS[data["expected_error"]]
    with pytest.raises(expected_cls):
        _call_validate_for_fixture(data)


def test_validation_functions_exist_but_projection_api_does_not():
    for name in (
        "validate_as2_inputs",
        "validate_identity_context",
        "validate_model_registry",
        "validate_memory_ref_source",
        "validate_capability_grant_source",
    ):
        assert callable(getattr(as2, name))
    assert not hasattr(as2, "to_agent_snapshot")


def test_value_skeleton_types_are_present():
    for name in (
        "AdapterIdentitySeed",
        "AdapterAuditContext",
        "AdapterIdentityContext",
        "ModelRef",
        "StaticModelRegistry",
        "MemoryRefSource",
        "CapabilityGrantSource",
        "AdapterDerivationRecordSkeleton",
    ):
        assert hasattr(as2, name)


def test_adapter_module_does_not_import_legacy_runtime_paths():
    source = Path(as2.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module)
    assert FORBIDDEN_IMPORT_MODULES.isdisjoint(imported)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attr_names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    function_defs = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "AgentRuntime" not in names | attr_names
    assert "Environment" not in names | attr_names
    assert "to_agent_snapshot" not in function_defs


def test_adapter_module_does_not_use_ambient_authority_modules():
    source = Path(as2.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module)
    assert {"time", "uuid", "os", "socket", "requests", "urllib"}.isdisjoint(imported)


def test_memory_source_validator_accepts_explicit_empty_list_but_not_missing():
    positive = _load_fixture(FIXTURE_DIR / "positive_minimal_valid_projection_inputs.json")
    as2.validate_memory_ref_source(positive["inputs"]["memory_ref_source"])
    with pytest.raises(as2.MemoryRefSourceMissingError):
        as2.validate_memory_ref_source(None)


def test_memory_source_validator_rejects_mixed_space_fixture():
    fixture = _load_fixture(FIXTURE_DIR / "negative_memory_space_mismatch.json")
    with pytest.raises(as2.AdapterMemorySpaceMismatchError):
        as2.validate_memory_ref_source(fixture["inputs"]["memory_ref_source"])
