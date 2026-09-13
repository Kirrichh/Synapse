"""Pure provenance/capability contracts; no command or HTTP request is executed."""

import copy
from importlib.metadata import distribution
import json

import pytest

from synapse.worker.input_contract import SPLIT_INPUT_PROFILE_V1, WorkerInputViolation
from synapse.worker.mini_protocol import public_input_messages, require_terminal_declaration, SUBMIT_COMMAND
from synapse.worker.provider_policy import MiniPublicRequestPolicy
from minisweagent.models.utils.actions_toolcall import BASH_TOOL


def policy():
    return MiniPublicRequestPolicy(
        task="Public task", input_profile=SPLIT_INPUT_PROFILE_V1, model="gpt-4o-mini",
        configuration_path=distribution("mini-swe-agent").locate_file("minisweagent/config/mini.yaml"),
    )


def request():
    return {"model": "gpt-4o-mini", "tools": [copy.deepcopy(BASH_TOOL)],
            "messages": public_input_messages("Public task", SPLIT_INPUT_PROFILE_V1)}


def test_public_request_and_exact_terminal_capability_are_admitted():
    policy().require_request(request())
    require_terminal_declaration({"command": SUBMIT_COMMAND, "tool_call_id": "public-call"})


@pytest.mark.parametrize("field,value", [
    ("metadata", {"label": "unbound-value"}), ("model", "different-model"),
    ("stream", 0), ("tools", []),
])
def test_unbound_request_fields_have_no_public_authority(field, value):
    candidate = request()
    candidate[field] = value
    with pytest.raises(WorkerInputViolation):
        policy().require_request(candidate)


def test_unregistered_message_is_refused_by_the_pure_validator():
    candidate = request()
    candidate["messages"].append({"role": "user", "content": "unregistered observation"})
    with pytest.raises(WorkerInputViolation):
        policy().require_request(candidate)


@pytest.mark.parametrize("action", [{}, {"command": "undeclared-operation"}, {"command": SUBMIT_COMMAND, "cwd": "src"}])
def test_action_contract_has_no_ambient_execution_capability(action):
    with pytest.raises(WorkerInputViolation):
        require_terminal_declaration(action)


def test_actual_provider_response_is_the_only_source_of_a_continuation():
    guard = policy()
    candidate = request()
    guard.require_request(candidate)
    response = {"id": "public-reply", "model": "gpt-4o-mini", "choices": [{
        "index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": "Finished.",
            "tool_calls": [{"id": "public-call", "type": "function", "function": {
                "name": "bash", "arguments": json.dumps({"command": SUBMIT_COMMAND})}}]}}]}
    guard.observe_response(json.dumps(response).encode())
    candidate["messages"].append(response["choices"][0]["message"])
    guard.require_request(candidate)


def test_local_edit_public_root_matches_the_installed_template_semantics():
    from jinja2 import Template
    from synapse.worker.local_edits import LOCAL_EDIT_PROFILE_V4
    public = public_input_messages("A public task.", LOCAL_EDIT_PROFILE_V4)
    assert Template(public[0]["content"]).render() == public[0]["content"]
    policy = MiniPublicRequestPolicy(task="A public task.", input_profile=LOCAL_EDIT_PROFILE_V4,
        model="gpt-4o-mini", configuration_path=distribution("mini-swe-agent").locate_file("minisweagent/config/mini.yaml"))
    policy.require_request({"model": "gpt-4o-mini", "tools": [BASH_TOOL], "messages": public})


@pytest.mark.parametrize("exclude_none", [False, True])
def test_public_reply_retains_both_sdk_null_serializations(exclude_none):
    from litellm import ModelResponse

    guard = policy()
    candidate = request()
    response = {"id": "public-reply", "model": "gpt-4o-mini", "choices": [{
        "index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "public-call", "type": "function", "function": {
                "name": "bash", "arguments": json.dumps({"command": SUBMIT_COMMAND})}}]}}]}
    guard.observe_response(json.dumps(response).encode())
    message = ModelResponse(**response).choices[0].message.model_dump(exclude_none=exclude_none)
    candidate["messages"].append(message)
    guard.require_request(candidate)
