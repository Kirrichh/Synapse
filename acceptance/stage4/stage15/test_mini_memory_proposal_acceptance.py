"""Installed SDK comparison: identical local evidence, model invoked only in slow mode."""
import hashlib
import json

from acceptance.stage4.stage10.test_local_edit_contract import information, proposal, feedback, encoded
from acceptance.stage4.stage15.test_mini_local_edit_acceptance import repository, invoke, SOURCE, apply_and_check
from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from synapse.canonical_values import canonical_json_bytes
from synapse.worker.local_edits import LOCAL_EDIT_COMMAND, LOCAL_EDIT_PROFILE_V4, LOCAL_EDIT_PROFILE_V5, propose_local_edits


def test_installed_mini_uses_exact_memory_without_an_invented_provider_call(tmp_path):
    repo, public = repository(tmp_path)
    revision = public.to_dict()["repository_revision"]
    raw_information = information(source=SOURCE, revision=revision)
    patch = propose_local_edits(task=public, information=raw_information,
                               proposal=proposal(("a - b", "a + b")))["diff_text"]
    digest = hashlib.sha256(patch.encode()).hexdigest()
    private = information(source=SOURCE, revision=revision, extra=[feedback(patch, True),
        {"role": "REFERENCE", "media_type": "text/plain", "content_base64url": encoded(patch.encode())},
        {"role": "REFERENCE", "media_type": "application/json", "content_base64url": encoded(canonical_json_bytes({
            "repository_revision": revision, "procedural_memory": {"profile": "exact-verified-patch-memory/v1",
            "patches": [digest], "generalization": "NOT_ESTABLISHED"}}))}])
    command = LOCAL_EDIT_COMMAND + canonical_json_bytes(proposal()).decode()
    patches = []
    for name, profile, expected_calls in (("automatic", LOCAL_EDIT_PROFILE_V5, 0), ("slow", LOCAL_EDIT_PROFILE_V4, 1)):
        with provider_endpoint(model="gemini-3.1-flash-lite", path="/v1beta/openai/chat/completions",
                               command=command) as (endpoint, requests):
            result, trajectory = invoke(tmp_path / name, repo, public, private, endpoint,
                                       profile=profile, expected_model_calls=expected_calls)
        assert len(requests) == expected_calls
        assert result.diff_text == patch and result.status.value == "PROPOSED_PATCH"
        local = trajectory["info"]["local_edit_result"]
        if not expected_calls:
            assert local["planning_route"] == "EXACT_MEMORY"
            assert trajectory["info"]["model_stats"]["api_calls"] == 0
        assert "PRIVATE-SOURCE-COMMENT" not in json.dumps(requests)
        patches.append(result.diff_text)
    assert patches[0] == patches[1]
    assert apply_and_check(repo, patches[0]).returncode == 0
