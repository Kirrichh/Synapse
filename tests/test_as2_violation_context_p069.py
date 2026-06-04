"""P0.6.9 AS2ViolationContext / forensic error attribution tests.

This test module verifies the failure-path enrichment introduced in P0.6.9.
It does not alter projection semantics, does not inspect AdapterDerivationRecord,
and does not import legacy runtime components.
"""

import json
import re
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from synapse import agent_snapshot_adapter as as2


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "as2"
RFC_REFERENCE_RE = re.compile(r"^RFC-[A-Z0-9-]+\.md §[0-9]+(\.[0-9]+)*$")


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _negative_fixtures():
    return sorted(FIXTURE_DIR.glob("negative_*.json"), key=lambda p: p.name)


def _dispatch_fixture(data):
    inputs = data["inputs"]
    return as2.validate_as2_inputs(
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


def _assert_context_contains(actual, expected):
    assert isinstance(actual, as2.AS2ViolationContext)
    for field_name, expected_value in expected.items():
        assert getattr(actual, field_name) == expected_value


@pytest.mark.parametrize("fixture_path", _negative_fixtures(), ids=lambda p: p.stem)
def test_negative_fixtures_expect_context_and_raise_matching_context(fixture_path):
    data = _load(fixture_path)
    expected_context = data["expected_error_context"]
    assert RFC_REFERENCE_RE.match(expected_context["rfc_reference"])
    expected_cls = as2.ERROR_NAME_TO_CLASS[data["expected_error"]]
    with pytest.raises(expected_cls) as exc_info:
        _dispatch_fixture(data)
    assert type(exc_info.value) is expected_cls
    _assert_context_contains(exc_info.value.context, expected_context)


def test_as2_violation_context_is_frozen_value_object():
    context = as2.AS2ViolationContext(
        rfc_reference="RFC-AGENT-SNAPSHOT-ADAPTER.md §5.1",
        violated_field="adapter_identity_context",
        fixture_case_id="negative_missing_identity_context",
    )
    with pytest.raises(FrozenInstanceError):
        context.violated_field = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "rfc_reference",
    [
        "RFC-AGENT-SNAPSHOT-ADAPTER.md §5",
        "RFC-AGENT-SNAPSHOT-ADAPTER.md §5.1",
        "RFC-AGENT-SNAPSHOT-ADAPTER.md §7.3.2",
    ],
)
def test_as2_violation_context_accepts_canonical_rfc_reference_format(rfc_reference):
    context = as2.AS2ViolationContext(rfc_reference=rfc_reference)
    assert context.rfc_reference == rfc_reference


@pytest.mark.parametrize(
    "rfc_reference",
    [
        "AS2-01",
        "adapter.py:120",
        "identity context",
        "RFC adapter section 5",
        "RFC-AGENT-SNAPSHOT-ADAPTER#5",
        "RFC-AGENT-SNAPSHOT-ADAPTER.md §5.1, §7.2",
    ],
)
def test_as2_violation_context_rejects_non_canonical_rfc_reference_format(rfc_reference):
    with pytest.raises(ValueError):
        as2.AS2ViolationContext(rfc_reference=rfc_reference)


def test_all_leaf_errors_accept_context_without_renaming_or_restructuring():
    context = as2.AS2ViolationContext(
        rfc_reference="RFC-AGENT-SNAPSHOT-ADAPTER.md §12",
        violated_field="fixture",
    )
    for error_name, error_cls in as2.ERROR_NAME_TO_CLASS.items():
        err = error_cls(f"{error_name} test", context)
        assert isinstance(err, as2.AS2AdapterError)
        assert type(err) is error_cls
        assert err.context is context


def test_violation_context_is_not_mixed_into_success_derivation_record():
    data = _load(FIXTURE_DIR / "positive_minimal_valid_projection_inputs.json")
    inputs = data["inputs"]
    snapshot, derivation = as2.project_validated_as2_inputs(
        adapter_identity_context=inputs["adapter_identity_context"],
        adapter_definition_source=inputs["adapter_definition_source"],
        static_model_registry=inputs["static_model_registry"],
        memory_ref_source=inputs["memory_ref_source"],
        capability_grant_source=inputs["capability_grant_source"],
        model_selection_source=inputs["model_selection_source"],
    )
    assert snapshot.snapshot_hash()
    assert not hasattr(derivation, "context")
    assert "AS2ViolationContext" not in repr(derivation)
