"""Alpha3g P0.6.15 AS2 Runtime Wiring Harness / Host Provider Mocks.

The P0.6.15 harness is test-only. It exercises Host provider responsibility
contracts through mocked payloads and stops at PreparedAS2Inputs. It performs no
runtime wiring and does not cross into the snapshot projection layer.
"""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

import synapse.agent_snapshot_bridge as bridge
from tests.support import as2_runtime_wiring_harness as harness


AS2_BRIDGE_FIXTURE = Path(__file__).parent / "fixtures" / "as2_bridge" / "positive_bridge_minimal_host_prestage.json"
P0615_CONTRACT_FIXTURE = Path(__file__).parent / "fixtures" / "as2_runtime_wiring" / "p0615_runtime_wiring_contract.json"


PROVIDER_PROTOCOL_PAIRS = (
    ("identity", harness.MockHostIdentityProvider, harness.IdentityProviderProtocol),
    ("definition", harness.MockHostDefinitionProvider, harness.DefinitionProviderProtocol),
    ("model_registry", harness.MockStaticModelRegistryProvider, harness.ModelRegistryProviderProtocol),
    ("memory", harness.MockMemoryExternalizationProvider, harness.MemoryExternalizationProtocol),
    ("capability", harness.MockCapabilityGrantManifestProvider, harness.CapabilityGrantManifestProtocol),
    ("model_selection", harness.MockModelSelectionProvider, harness.ModelSelectionProtocol),
)


def _bridge_fixture_payload() -> dict[str, Any]:
    fixture = json.loads(AS2_BRIDGE_FIXTURE.read_text(encoding="utf-8"))
    payload = copy.deepcopy(fixture["host_prestage_outputs"])
    payload.pop("notes", None)
    return payload


def _providers_from_payload(payload: Mapping[str, Any]) -> dict[str, object]:
    return {
        "identity": harness.MockHostIdentityProvider(payload["adapter_identity_context"]),
        "definition": harness.MockHostDefinitionProvider(payload["adapter_definition_source"]),
        "model_registry": harness.MockStaticModelRegistryProvider(payload["static_model_registry"]),
        "memory": harness.MockMemoryExternalizationProvider(payload["memory_ref_source"]),
        "capability": harness.MockCapabilityGrantManifestProvider(payload["capability_grant_source"]),
        "model_selection": harness.MockModelSelectionProvider(payload["model_selection_source"]),
    }


def _builder_from_payload(payload: Mapping[str, Any]) -> harness.MockHostPrestagePayloadBuilder:
    providers = _providers_from_payload(payload)
    return harness.MockHostPrestagePayloadBuilder(
        identity_provider=providers["identity"],
        definition_provider=providers["definition"],
        model_registry_provider=providers["model_registry"],
        memory_provider=providers["memory"],
        capability_provider=providers["capability"],
        model_selection_provider=providers["model_selection"],
    )


def _enable_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "AS2_HOST_PRESTAGE_BRIDGE_ENABLED", True)


def test_p0615_contract_fixture_is_deterministic_sorted_json():
    data = json.loads(P0615_CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    expected = json.dumps(data, indent=2, sort_keys=True) + "\n"
    assert P0615_CONTRACT_FIXTURE.read_text(encoding="utf-8") == expected


def test_p0615_contract_fixture_matches_harness_key_classification():
    data = json.loads(P0615_CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    classification = data["payload_key_classification"]

    assert set(classification["production_success_keys"]) == harness.PRODUCTION_PAYLOAD_KEYS
    assert set(classification["test_failure_modelling_keys"]) == harness.TEST_FAILURE_MODELING_KEYS
    assert set(classification["compatibility_aliases"]) == harness.COMPATIBILITY_ALIAS_KEYS
    assert set(classification["diagnostic_notes"]) == harness.DIAGNOSTIC_NOTE_KEYS


def test_mock_providers_implement_test_scope_protocol_ports():
    providers = _providers_from_payload(_bridge_fixture_payload())

    for provider_name, _provider_cls, protocol in PROVIDER_PROTOCOL_PAIRS:
        assert isinstance(providers[provider_name], protocol)


def test_prestage_payload_builder_emits_only_production_success_keys():
    payload = _builder_from_payload(_bridge_fixture_payload()).assemble_payload()

    assert set(payload) == harness.PRODUCTION_PAYLOAD_KEYS
    assert "model_selection_source" in payload
    removed_alias = "model_" + "selector"
    assert removed_alias not in payload
    assert not (set(payload) & harness.TEST_FAILURE_MODELING_KEYS)
    assert not (set(payload) & harness.COMPATIBILITY_ALIAS_KEYS)


def test_harness_happy_path_calls_bridge_and_returns_prepared_inputs(monkeypatch):
    _enable_bridge(monkeypatch)
    payload = _builder_from_payload(_bridge_fixture_payload()).assemble_payload()

    prepared = bridge.prepare_as2_inputs_from_host_prestage(payload)

    assert isinstance(prepared, bridge.PreparedAS2Inputs)
    assert prepared.model_selection_source.model == "mock-agent-model"
    assert prepared.to_validate_kwargs()["model_selection_source"]["model"] == "mock-agent-model"


def test_prepare_from_same_mocked_payload_is_deterministic(monkeypatch):
    _enable_bridge(monkeypatch)
    payload = _builder_from_payload(_bridge_fixture_payload()).assemble_payload()

    first = bridge.prepare_as2_inputs_from_host_prestage(payload)
    second = bridge.prepare_as2_inputs_from_host_prestage(copy.deepcopy(payload))

    assert first == second
    assert first.to_validate_kwargs() == second.to_validate_kwargs()


@pytest.mark.parametrize(
    "missing_key, expected_error",
    [
        ("adapter_identity_context", bridge.HostPreStageMissingIdentitySourceError),
        ("adapter_definition_source", bridge.HostPreStageMissingDefinitionSourceError),
        ("static_model_registry", bridge.HostPreStageMissingModelRegistryError),
        ("memory_ref_source", bridge.HostPreStageMissingMemoryRefSourceError),
        ("capability_grant_source", bridge.HostPreStageMissingCapabilityGrantsError),
    ],
)
def test_negative_contract_missing_production_inputs_fail_closed(monkeypatch, missing_key, expected_error):
    _enable_bridge(monkeypatch)
    payload = _builder_from_payload(_bridge_fixture_payload()).assemble_payload()
    payload.pop(missing_key)

    with pytest.raises(expected_error):
        bridge.prepare_as2_inputs_from_host_prestage(payload)


@pytest.mark.parametrize(
    "bad_key, expected_error",
    [
        ("host_stage_failure", bridge.AS2BridgeInputError),
        ("forbidden_runtime_reads", bridge.HostForbiddenRuntimeReadError),
        ("inline_memory_payload", bridge.HostInlineMemoryNotExternalizedError),
        ("runtime_envelope", bridge.HostPreStageUnexpectedFieldError),
    ],
)
def test_negative_contract_non_production_payload_keys_are_not_happy_path_inputs(
    monkeypatch,
    bad_key,
    expected_error,
):
    _enable_bridge(monkeypatch)
    payload = _builder_from_payload(_bridge_fixture_payload()).assemble_payload()
    payload.update(
        {
            "host_stage_failure": {"failure_type": "UnsupportedTestFailure"},
            "forbidden_runtime_reads": ["AgentRuntime.to_dict()"],
            "inline_memory_payload": {"short_term": ["raw legacy memory"]},
            "runtime_envelope": {"mailbox": []},
        }
    )
    for key in list(payload):
        if key in {"host_stage_failure", "forbidden_runtime_reads", "inline_memory_payload", "runtime_envelope"} and key != bad_key:
            payload.pop(key)

    with pytest.raises(expected_error):
        bridge.prepare_as2_inputs_from_host_prestage(payload)


def test_single_agent_bad_payload_quarantines_only_that_agent(monkeypatch):
    _enable_bridge(monkeypatch)
    valid_payload = _bridge_fixture_payload()
    invalid_payload = copy.deepcopy(valid_payload)
    invalid_payload["adapter_identity_context"] = {}

    result = harness.run_p0615_harness_batch(
        {
            "agent.valid": _builder_from_payload(valid_payload),
            "agent.bad": _builder_from_payload(invalid_payload),
        },
        prepare=bridge.prepare_as2_inputs_from_host_prestage,
    )

    assert result.global_outcome == harness.HarnessOutcome.PREPARED
    assert result.agent_results["agent.valid"].outcome == harness.HarnessOutcome.PREPARED
    assert result.agent_results["agent.bad"].outcome == harness.HarnessOutcome.QUARANTINE_AGENT
    assert result.agent_results["agent.bad"].error_type == "HostPreStageInvalidAS2InputsError"


def test_systemic_provider_failure_disables_wiring_globally_without_prepare_call():
    payload = _bridge_fixture_payload()
    providers = _providers_from_payload(payload)
    builder = harness.MockHostPrestagePayloadBuilder(
        identity_provider=providers["identity"],
        definition_provider=providers["definition"],
        model_registry_provider=providers["model_registry"],
        memory_provider=harness.MockMemoryExternalizationProvider(fail_systemically=True),
        capability_provider=providers["capability"],
        model_selection_provider=providers["model_selection"],
    )

    def _prepare_must_not_be_called(_payload: Mapping[str, Any]) -> bridge.PreparedAS2Inputs:
        raise AssertionError("systemic provider failure must stop before bridge preparation")

    result = harness.run_p0615_harness_batch(
        {"agent.any": builder},
        prepare=_prepare_must_not_be_called,
    )

    assert result.global_outcome == harness.HarnessOutcome.DISABLE_WIRING_GLOBALLY
    assert result.agent_results == {}
    assert result.systemic_error_type == "SystemicProviderUnavailableError"


def test_support_harness_has_no_forbidden_runtime_imports_or_projection_calls():
    source_path = Path(harness.__file__)
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_modules = {
        "synapse.agent_runtime",
        "synapse.environment",
        "synapse.interpreter",
        "synapse.actor_runtime",
    }
    forbidden_calls = {
        "project_validated_as2_inputs",
        "to_agent_snapshot",
        "AgentSnapshot",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden_modules
        elif isinstance(node, ast.ImportFrom):
            assert node.module not in forbidden_modules
            imported = {alias.name for alias in node.names}
            assert not (imported & forbidden_calls)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls
            elif isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_calls

    assert "project_validated_as2_inputs" not in source
    assert "AgentSnapshot" not in source
