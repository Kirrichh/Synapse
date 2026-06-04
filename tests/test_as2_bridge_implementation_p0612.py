"""P0.6.12 Host Pre-Stage bridge skeleton implementation tests.

These tests exercise the first bridge-code boundary using the P0.6.11 bridge
fixture corpus. They intentionally do not construct AgentRuntime, Environment,
or AgentSnapshot, and they do not call project_validated_as2_inputs(...).
"""

import ast
import copy
import json
from pathlib import Path

import pytest

import synapse.agent_snapshot_bridge as bridge
from synapse.agent_snapshot_adapter import validate_as2_inputs

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "as2_bridge"
POSITIVE_BASELINE = FIXTURE_DIR / "positive_bridge_minimal_host_prestage.json"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_paths():
    return sorted(FIXTURE_DIR.glob("*.json"), key=lambda path: path.name)


def _positive_fixture_paths():
    return [path for path in _fixture_paths() if _load(path)["polarity"] == "positive"]


def _negative_fixture_paths():
    return [path for path in _fixture_paths() if _load(path)["polarity"] == "negative"]


def _baseline_positive_payload() -> dict:
    return copy.deepcopy(_load(POSITIVE_BASELINE)["host_prestage_outputs"])


def _payload_from_bridge_fixture(fixture: dict) -> dict:
    """Translate a P0.6.11 fixture into the public Host Pre-Stage payload.

    The public bridge entrypoint must not depend on fixture schema. Tests use
    this helper to construct synthetic host payloads for negative data cases.
    """

    if fixture["polarity"] == "positive":
        return copy.deepcopy(fixture["host_prestage_outputs"])

    expected_error = fixture["expected_error"]
    if expected_error == "HostPreStageIOError":
        return {"host_stage_failure": copy.deepcopy(fixture["host_stage_failure"])}
    if expected_error in {
        "HostForbiddenRuntimeReadError",
        "HostInlineMemoryNotExternalizedError",
        "HostEnvironmentEnvelopeForbiddenError",
        "HostRuntimeEnvelopeSourceForbiddenError",
    }:
        return {"forbidden_runtime_reads": list(fixture["forbidden_reads"])}

    payload = _baseline_positive_payload()
    violated_field = fixture["expected_error_context"]["violated_field"]
    field_map = {
        "host_prestage_outputs.adapter_identity_context": "adapter_identity_context",
        "host_prestage_outputs.adapter_definition_source": "adapter_definition_source",
        "host_prestage_outputs.static_model_registry": "static_model_registry",
        "host_prestage_outputs.capability_grant_source": "capability_grant_source",
    }
    if violated_field not in field_map:
        raise AssertionError(f"unsupported negative fixture for bridge payload helper: {fixture['case_id']}")
    payload.pop(field_map[violated_field], None)
    return payload


def _enable_bridge(monkeypatch):
    monkeypatch.setattr(bridge, "AS2_HOST_PRESTAGE_BRIDGE_ENABLED", True)


def _error_class(name: str):
    try:
        return bridge.BRIDGE_ERROR_NAME_TO_CLASS[name]
    except KeyError as exc:
        raise AssertionError(f"bridge error class is missing for {name}") from exc


def _assert_context_subset(actual, expected: dict) -> None:
    assert actual is not None
    for key, value in expected.items():
        assert getattr(actual, key) == value, key


def test_bridge_flag_is_disabled_by_default():
    assert bridge.AS2_HOST_PRESTAGE_BRIDGE_ENABLED is False
    with pytest.raises(bridge.AS2HostPreStageBridgeDisabledError):
        bridge.prepare_as2_inputs_from_host_prestage(_baseline_positive_payload())


@pytest.mark.parametrize("fixture_path", _positive_fixture_paths(), ids=lambda path: path.stem)
def test_positive_bridge_fixtures_prepare_validated_as2_inputs(monkeypatch, fixture_path: Path):
    _enable_bridge(monkeypatch)
    fixture = _load(fixture_path)
    payload = _payload_from_bridge_fixture(fixture)

    prepared = bridge.prepare_as2_inputs_from_host_prestage(payload)

    assert isinstance(prepared, bridge.PreparedAS2Inputs)
    assert prepared.model_selection_source == bridge.ModelSelectionSource(model="mock-agent-model")
    assert prepared.to_validate_kwargs() == fixture["expected_as2_inputs"]
    assert validate_as2_inputs(**prepared.to_validate_kwargs()) is None


@pytest.mark.parametrize("fixture_path", _negative_fixture_paths(), ids=lambda path: path.stem)
def test_negative_bridge_fixtures_raise_expected_bridge_errors(monkeypatch, fixture_path: Path):
    _enable_bridge(monkeypatch)
    fixture = _load(fixture_path)
    payload = _payload_from_bridge_fixture(fixture)
    expected_error = _error_class(fixture["expected_error"])

    with pytest.raises(expected_error) as exc_info:
        bridge.prepare_as2_inputs_from_host_prestage(payload)

    _assert_context_subset(exc_info.value.context, fixture["expected_error_context"])


def test_bridge_output_does_not_construct_snapshot(monkeypatch):
    _enable_bridge(monkeypatch)
    prepared = bridge.prepare_as2_inputs_from_host_prestage(_baseline_positive_payload())
    assert isinstance(prepared, bridge.PreparedAS2Inputs)
    assert not hasattr(prepared, "agent_snapshot")
    assert not hasattr(prepared, "snapshot")


def test_bridge_module_does_not_import_legacy_runtime_or_environment():
    tree = ast.parse(Path(bridge.__file__).read_text(encoding="utf-8"))
    forbidden_modules = {
        "synapse.agent_runtime",
        "synapse.environment",
        "synapse.interpreter",
        "synapse.actor_runtime",
    }
    forbidden_names = {"AgentRuntime", "Environment"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden_modules
        elif isinstance(node, ast.ImportFrom):
            assert node.module not in forbidden_modules
        elif isinstance(node, ast.Name):
            assert node.id not in forbidden_names


def test_bridge_module_does_not_call_projection_or_construct_agent_snapshot():
    tree = ast.parse(Path(bridge.__file__).read_text(encoding="utf-8"))
    forbidden_calls = {"project_validated_as2_inputs", "to_agent_snapshot", "AgentSnapshot"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls
            elif isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_calls
        elif isinstance(node, ast.ImportFrom):
            imported = {alias.name for alias in node.names}
            assert "project_validated_as2_inputs" not in imported
            assert "AgentSnapshot" not in imported


def test_bridge_module_emits_canonical_model_selection_source():
    source = Path(bridge.__file__).read_text(encoding="utf-8")
    removed_key = "legacy_" + "agent_runtime_to_dict"
    assert "model_selection_source" in source
    assert removed_key not in source
    assert "_MODEL_SELECTOR_DEBT_NOTE" not in source
    prepared = bridge.PreparedAS2Inputs(
        adapter_identity_context={},
        adapter_definition_source={},
        static_model_registry={},
        memory_ref_source={},
        capability_grant_source={},
        model_selection_source=bridge.ModelSelectionSource(model="mock-agent-model"),
    )
    kwargs = prepared.to_validate_kwargs()
    assert kwargs["model_selection_source"] == {"model": "mock-agent-model"}
    assert removed_key not in kwargs
