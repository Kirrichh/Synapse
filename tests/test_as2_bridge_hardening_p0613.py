"""P0.6.13 Host Pre-Stage bridge hardening tests.

These tests probe the bridge boundary added in P0.6.12. They intentionally do
not call projection, do not construct AgentSnapshot, and do not import legacy
runtime classes.
"""

import copy
import ast
import json
from pathlib import Path
from types import MappingProxyType

import pytest

import synapse.agent_snapshot_bridge as bridge

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "as2_bridge"
BASELINE_FIXTURE = FIXTURE_DIR / "positive_bridge_minimal_host_prestage.json"


def _load_baseline_payload() -> dict:
    fixture = json.loads(BASELINE_FIXTURE.read_text(encoding="utf-8"))
    return copy.deepcopy(fixture["host_prestage_outputs"])


def _enable_bridge(monkeypatch):
    monkeypatch.setattr(bridge, "AS2_HOST_PRESTAGE_BRIDGE_ENABLED", True)


def _remove_required_payload_source(payload: dict, key: str) -> dict:
    mutated = copy.deepcopy(payload)
    mutated.pop(key, None)
    return mutated


def _null_required_payload_source(payload: dict, key: str) -> dict:
    mutated = copy.deepcopy(payload)
    mutated[key] = None
    return mutated


def _empty_required_payload_source(payload: dict, key: str) -> dict:
    mutated = copy.deepcopy(payload)
    mutated[key] = {}
    return mutated


def _wrong_type_required_payload_source(payload: dict, key: str) -> dict:
    mutated = copy.deepcopy(payload)
    mutated[key] = "not-a-mapping"
    return mutated


@pytest.mark.parametrize(
    "field_name, expected_error",
    [
        ("adapter_identity_context", bridge.HostPreStageMissingIdentitySourceError),
        ("adapter_definition_source", bridge.HostPreStageMissingDefinitionSourceError),
        ("static_model_registry", bridge.HostPreStageMissingModelRegistryError),
        ("memory_ref_source", bridge.HostPreStageMissingMemoryRefSourceError),
        ("capability_grant_source", bridge.HostPreStageMissingCapabilityGrantsError),
    ],
)
def test_required_sources_missing_raise_specific_missing_errors(monkeypatch, field_name, expected_error):
    _enable_bridge(monkeypatch)
    with pytest.raises(expected_error):
        bridge.prepare_as2_inputs_from_host_prestage(
            _remove_required_payload_source(_load_baseline_payload(), field_name)
        )


@pytest.mark.parametrize(
    "field_name, expected_error",
    [
        ("adapter_identity_context", bridge.HostPreStageMissingIdentitySourceError),
        ("adapter_definition_source", bridge.HostPreStageMissingDefinitionSourceError),
        ("static_model_registry", bridge.HostPreStageMissingModelRegistryError),
        ("memory_ref_source", bridge.HostPreStageMissingMemoryRefSourceError),
        ("capability_grant_source", bridge.HostPreStageMissingCapabilityGrantsError),
    ],
)
def test_required_sources_null_raise_specific_missing_errors(monkeypatch, field_name, expected_error):
    _enable_bridge(monkeypatch)
    with pytest.raises(expected_error):
        bridge.prepare_as2_inputs_from_host_prestage(
            _null_required_payload_source(_load_baseline_payload(), field_name)
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "adapter_identity_context",
        "adapter_definition_source",
        "static_model_registry",
        "memory_ref_source",
        "capability_grant_source",
    ],
)
def test_required_sources_empty_dict_raise_invalid_as2_inputs(monkeypatch, field_name):
    _enable_bridge(monkeypatch)
    with pytest.raises(bridge.HostPreStageInvalidAS2InputsError):
        bridge.prepare_as2_inputs_from_host_prestage(
            _empty_required_payload_source(_load_baseline_payload(), field_name)
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "adapter_identity_context",
        "adapter_definition_source",
        "static_model_registry",
        "memory_ref_source",
        "capability_grant_source",
    ],
)
def test_required_sources_wrong_type_raise_invalid_as2_inputs(monkeypatch, field_name):
    _enable_bridge(monkeypatch)
    with pytest.raises(bridge.HostPreStageInvalidAS2InputsError):
        bridge.prepare_as2_inputs_from_host_prestage(
            _wrong_type_required_payload_source(_load_baseline_payload(), field_name)
        )


def test_unknown_top_level_payload_field_rejected(monkeypatch):
    _enable_bridge(monkeypatch)
    payload = _load_baseline_payload()
    payload["runtime_envelope_leak"] = {"mailbox": []}

    with pytest.raises(bridge.HostPreStageUnexpectedFieldError) as exc_info:
        bridge.prepare_as2_inputs_from_host_prestage(payload)

    assert exc_info.value.context is not None
    assert exc_info.value.context.violated_field == "host_prestage_outputs"
    assert "runtime_envelope_leak" in (exc_info.value.context.actual_value or "")


def test_unknown_nested_identity_field_rejected(monkeypatch):
    _enable_bridge(monkeypatch)
    payload = _load_baseline_payload()
    payload["adapter_identity_context"]["identity_seed"]["runtime_pid"] = "pid:123"

    with pytest.raises(bridge.HostPreStageUnexpectedFieldError) as exc_info:
        bridge.prepare_as2_inputs_from_host_prestage(payload)

    assert exc_info.value.context is not None
    assert exc_info.value.context.violated_field == "adapter_identity_context.identity_seed"
    assert "runtime_pid" in (exc_info.value.context.actual_value or "")


def test_unknown_nested_memory_ref_field_rejected(monkeypatch):
    _enable_bridge(monkeypatch)
    payload = _load_baseline_payload()
    payload["memory_ref_source"]["refs"][0]["inline_value"] = "dirty-memory"

    with pytest.raises(bridge.HostPreStageUnexpectedFieldError) as exc_info:
        bridge.prepare_as2_inputs_from_host_prestage(payload)

    assert exc_info.value.context is not None
    assert exc_info.value.context.violated_field == "memory_ref_source.refs[0]"
    assert "inline_value" in (exc_info.value.context.actual_value or "")


def test_unknown_nested_capability_grant_field_rejected(monkeypatch):
    _enable_bridge(monkeypatch)
    payload = _load_baseline_payload()
    payload["capability_grant_source"]["grants"][0]["live_callable"] = "function-object"

    with pytest.raises(bridge.HostPreStageUnexpectedFieldError) as exc_info:
        bridge.prepare_as2_inputs_from_host_prestage(payload)

    assert exc_info.value.context is not None
    assert exc_info.value.context.violated_field == "capability_grant_source.grants[0]"
    assert "live_callable" in (exc_info.value.context.actual_value or "")


def test_prepared_as2_inputs_are_isolated_from_external_payload_mutation(monkeypatch):
    _enable_bridge(monkeypatch)
    payload = _load_baseline_payload()
    prepared = bridge.prepare_as2_inputs_from_host_prestage(payload)
    before = prepared.to_validate_kwargs()

    payload["adapter_identity_context"]["identity_seed"]["alias"] = "tampered-alias"
    payload["memory_ref_source"]["refs"][0]["memory_key"] = "tampered-key"
    payload["capability_grant_source"]["grants"][0]["tool_namespace"] = "tampered.tool"
    payload["model_selection_source"]["model"] = "tampered-model"

    assert prepared.to_validate_kwargs() == before


def test_to_validate_kwargs_returns_fresh_mutable_copies(monkeypatch):
    _enable_bridge(monkeypatch)
    prepared = bridge.prepare_as2_inputs_from_host_prestage(_load_baseline_payload())
    first = prepared.to_validate_kwargs()
    first["adapter_identity_context"]["identity_seed"]["alias"] = "mutated-return-value"
    first["memory_ref_source"]["refs"][0]["memory_key"] = "mutated-return-value"

    second = prepared.to_validate_kwargs()
    assert second["adapter_identity_context"]["identity_seed"]["alias"] == "fixture-agent"
    assert second["memory_ref_source"]["refs"][0]["memory_key"] == "legacy.short_term.0"


def test_prepared_dto_internal_mappings_are_read_only(monkeypatch):
    _enable_bridge(monkeypatch)
    prepared = bridge.prepare_as2_inputs_from_host_prestage(_load_baseline_payload())

    assert isinstance(prepared.adapter_identity_context, MappingProxyType)
    with pytest.raises(TypeError):
        prepared.adapter_identity_context["tampered"] = True
    with pytest.raises(AttributeError):
        prepared.memory_ref_source["refs"].append({})


def test_local_feature_flag_still_disabled_by_default():
    assert bridge.AS2_HOST_PRESTAGE_BRIDGE_ENABLED is False


def test_bridge_module_still_does_not_use_projection():
    tree = ast.parse(Path(bridge.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id not in {"project_validated_as2_inputs", "AgentSnapshot"}
            elif isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"project_validated_as2_inputs", "AgentSnapshot"}
        elif isinstance(node, ast.ImportFrom):
            imported = {alias.name for alias in node.names}
            assert "project_validated_as2_inputs" not in imported
            assert "AgentSnapshot" not in imported
