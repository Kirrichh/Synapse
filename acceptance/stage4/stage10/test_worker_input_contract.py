"""Fast acceptance of separate worker data and exact public provenance."""

import base64
from copy import deepcopy
import json

import pytest

from synapse.canonical_values import canonical_json_bytes
from synapse.worker.input_contract import (
    LOCAL_INFORMATION_INPUT_V1, WORKER_TASK_INPUT_V1,
    LocalInformationInput, WorkerTaskInput, WorkerInputViolation,
)
from synapse.worker.provider_messages import PublicProviderConversation


def task_input():
    return WorkerTaskInput(canonical_json_bytes({
        "schema_version": WORKER_TASK_INPUT_V1,
        "statement": "Inspect the task. No source change is necessary; finish now.",
        "repository_revision": "a" * 40, "allowed_scope": ["src"], "capabilities": ["read"],
        "effects": [{"kind": "PATH_MODIFIED", "disposition": "FORBIDDEN", "subject_path": "src"}],
        "acceptance": [{"kind": "CONTRACT_CONDITION", "argv": []}],
    }))


def information_input(content=b"private experience", *, role="REFERENCE"):
    return LocalInformationInput(canonical_json_bytes({"schema_version": LOCAL_INFORMATION_INPUT_V1,
        "items": [{"role": role, "media_type": "text/plain",
                   "content_base64url": base64.urlsafe_b64encode(content).rstrip(b"=").decode()}]}))


@pytest.mark.parametrize("items", [None, False, 0, "", {}])
def test_empty_information_is_explicit_and_distinct_from_invalid_states(items):
    valid = LocalInformationInput(canonical_json_bytes({"schema_version": LOCAL_INFORMATION_INPUT_V1, "items": []}))
    assert valid.to_dict()["items"] == []
    with pytest.raises(WorkerInputViolation):
        LocalInformationInput(canonical_json_bytes({"schema_version": LOCAL_INFORMATION_INPUT_V1, "items": items}))


@pytest.mark.parametrize("role", ["REFERENCE", "REJECTED_HYPOTHESIS", "REPLAY_OBSERVATION", "EXECUTION_OBSERVATION"])
def test_information_preserves_opaque_experience_without_relabelling_it_success(role):
    content = b'\x00 failure; partial evidence; unknown outcome; invalid UTF-8: \xff'
    value = information_input(content, role=role)
    item = value.to_dict()["items"][0]
    assert base64.urlsafe_b64decode(item["content_base64url"] + "=" * (-len(item["content_base64url"]) % 4)) == content
    assert item["role"] == role
    changed = value.to_dict()
    changed["items"].clear()
    assert len(value.to_dict()["items"]) == 1


@pytest.mark.parametrize("change", [
    lambda value: value.pop("items"),
    lambda value: value.update(task="replace the public task"),
    lambda value: value["items"][0].update(role=[]),
    lambda value: value["items"][0].update(content_base64url="Zg=="),
    lambda value: value["items"][0].update(content_base64url="Zh"),
])
def test_local_input_rejects_unknown_shapes_and_noncanonical_content(change):
    value = information_input().to_dict()
    change(value)
    with pytest.raises(WorkerInputViolation):
        LocalInformationInput(canonical_json_bytes(value))


def test_public_task_refuses_private_metadata_and_noncanonical_encoding():
    value = task_input().to_dict()
    value["admitted_knowledge"] = "local-only"
    with pytest.raises(WorkerInputViolation):
        WorkerTaskInput(canonical_json_bytes(value))
    for raw in (b" " + task_input().canonical_bytes,
                task_input().canonical_bytes.replace(b'"schema_version":', b'"statement":"duplicate","schema_version":')):
        with pytest.raises(WorkerInputViolation):
            WorkerTaskInput(raw)


@pytest.mark.parametrize("message", [
    {"role": "tool", "content": "local observation", "tool_call_id": "1"},
    {"role": "user", "content": "error derived from local data"},
    {"role": "assistant", "content": "locally manufactured reply"},
])
def test_unregistered_messages_never_acquire_public_provenance(message):
    initial = [{"role": "system", "content": "public template"}, {"role": "user", "content": task_input().text}]
    conversation = PublicProviderConversation(initial)
    reply = {"role": "assistant", "content": None, "tool_calls": [], "extra": {"response": "local accounting"}}
    conversation.record_provider_messages(reply)
    allowed = initial + [reply]
    assert conversation.require_public(allowed)[-1] == {"role": "assistant", "content": None, "tool_calls": []}
    with pytest.raises(WorkerInputViolation):
        conversation.require_public(allowed + [message])
    changed = deepcopy(allowed)
    changed[-1]["content"] = "local data"
    with pytest.raises(WorkerInputViolation):
        conversation.require_public(changed)


@pytest.mark.parametrize("replacement", [0, None, "", []])
def test_public_history_uses_wire_identity_instead_of_python_value_equality(replacement):
    initial = [{"role": "system", "content": "public"}, {"role": "user", "content": "task"}]
    conversation = PublicProviderConversation(initial)
    reply = {"role": "assistant", "content": "public", "metadata": {"value": False}}
    conversation.record_provider_messages(reply)
    reply["metadata"]["value"] = replacement
    with pytest.raises(WorkerInputViolation):
        conversation.require_public(initial + [reply])


def test_local_trajectory_metadata_is_not_returned_as_provider_data():
    initial = [{"role": "system", "content": "public"}, {"role": "user", "content": "task"}]
    conversation = PublicProviderConversation(initial)
    initial[0]["extra"] = {"private": "local-accounting-canary"}
    assert "local-accounting-canary" not in json.dumps(conversation.require_public(initial))
