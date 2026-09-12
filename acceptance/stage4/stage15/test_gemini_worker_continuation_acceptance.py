"""Gemini tool state must survive a real Mini/SDK continuation unchanged."""

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint, run_actual_mini


def test_gemini_tool_signature_survives_the_next_physical_request(tmp_path):
    signature = "c2lnbmF0dXJlLWZyb20tdGhlLXByb3ZpZlcg=="
    with provider_endpoint(model="gemini-3.1-flash-lite", path="/v1beta/openai/chat/completions",
            commands=("pwd", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"),
            thought_signature=signature) as (endpoint, requests):
        _, result = run_actual_mini(tmp_path, endpoint, model="gemini-3.1-flash-lite")
    assert result.status.value == "NO_PATCH", result
    assert len(requests) == 2
    assistant, = [message for message in requests[1]["messages"] if message["role"] == "assistant"]
    assert assistant["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == signature
