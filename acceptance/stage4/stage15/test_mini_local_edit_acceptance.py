"""Real Mini/SDK local proposals, independent effects, and provider noninterference.

Only the provider response is controlled. These tests do not assert that the
Gold planner or CVM generated the proposals, or that publication has succeeded.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


from synapse.canonical_values import canonical_json_bytes
from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.stage10.worker_transport import WorkerInvocation, WORKER_INVOCATION_SCHEMA_V2
from synapse.experiments.gold.stage15.capture_store import CaptureStore, inspect_capture, read_source
from synapse.experiments.gold.stage15.reconciliation import reconcile_telemetry
from synapse.experiments.gold.stage15.telemetry import reference
from synapse.experiments.gold.stage15.worker_accounting import WorkerAccounting
from synapse.worker.local_edits import LOCAL_EDIT_COMMAND, LOCAL_EDIT_PROFILE_V1, propose_local_edits
from synapse.worker.mini_adapter import MiniAdapterConfig, MiniWorkerTransport
from synapse.worker.provider_transport import MiniProviderConfiguration

from acceptance.stage4.stage10.test_local_edit_contract import task, information, proposal, feedback
from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint


SOURCE = "# PRIVATE-SOURCE-COMMENT\ndef add(a, b):\n    return a - b\n"


def repository(root):
    repo = root / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "calc.py").write_text(SOURCE)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "src/calc.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Acceptance", "-c", "user.email=acceptance@example.invalid",
                    "commit", "-qm", "initial"], check=True,
        env={**os.environ, "GIT_AUTHOR_DATE": "2001-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2001-01-01T00:00:00Z"})
    revision = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    return repo, task(revision=revision)


def invoke(root, repo, public, private, endpoint):
    root.mkdir(parents=True, exist_ok=True)
    mini = Path(sys.executable).parent / ("mini.exe" if sys.platform == "win32" else "mini")
    assert mini.is_file(), "the acceptance job must install the pinned Mini SDK"
    store = CaptureStore(root / "capture", run_id="run-1", manifest_ref=reference({"run_id": "run-1"}, "test.run/v1"))
    invocation = WorkerInvocation("inv_" + "1" * 64, "attempt-1", "ctx_" + "2" * 64, public.text,
        hashlib.sha256(public.canonical_bytes).hexdigest(), len(public.canonical_bytes), "3" * 64,
        ("src",), ("repository.edit",), schema_version=WORKER_INVOCATION_SCHEMA_V2,
        information_text=private.text, information_sha256=private.sha256, information_byte_length=len(private.canonical_bytes))
    worker = MiniWorkerTransport(config=MiniAdapterConfig(command=(str(mini),), model="openai/gemini-3.1-flash-lite",
        timeout_seconds=45, max_steps=3, cost_limit=1.0, input_profile=LOCAL_EDIT_PROFILE_V1),
        accounting=WorkerAccounting(store=store, configuration=MiniProviderConfiguration(
            "gemini-3.1-flash-lite", "acceptance-only", endpoint, 10)))
    result = worker.run(repo, invocation)
    frames = inspect_capture(store.cut())
    closure, = [frame["payload"] for frame in frames if frame["kind"] == "INVOCATION_CLOSED"]
    trajectory = json.loads(read_source(store.root, HashBoundRef.from_dict(closure["trajectory_ref"])))
    report = reconcile_telemetry(store.cut()).to_dict()
    assert report["status"] == "COMPLETE", report
    assert report["source_totals"]["physical_provider_reported_tokens"] == 18
    assert len([frame for frame in frames if frame["kind"] == "LOGICAL_OPEN"]) == 1
    # Mini proposed bytes, and never executed even a repository-local command.
    assert (repo / "src" / "calc.py").read_text() == SOURCE
    assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], check=True,
                          capture_output=True, text=True).stdout == ""
    return result, trajectory


def apply_and_check(repo, patch):
    subprocess.run(["git", "-C", str(repo), "apply", "--check", "-"], input=patch, text=True, check=True)
    subprocess.run(["git", "-C", str(repo), "apply", "-"], input=patch, text=True, check=True)
    return subprocess.run([sys.executable, "-B", "-c", "from src.calc import add; assert add(4, 3) == 7"],
                          cwd=repo, capture_output=True, text=True)


def test_actual_mini_uses_private_source_for_a_patch_without_changing_the_public_request(tmp_path):
    outgoing, results = [], []
    command = LOCAL_EDIT_COMMAND + canonical_json_bytes(proposal(("a - b", "a + b"))).decode()
    for index, available in enumerate((True, False)):
        root = tmp_path / str(index)
        repo, public = repository(root)
        private = information(source=SOURCE if available else None, revision=public.to_dict()["repository_revision"])
        with provider_endpoint(model="gemini-3.1-flash-lite", path="/v1beta/openai/chat/completions",
                               command=command) as (endpoint, requests):
            result, trajectory = invoke(root, repo, public, private, endpoint)
        assert len(requests) == 1
        outgoing.append(requests)
        results.append(result)
        assert trajectory["info"]["input_delivery"]["local_interpretation"] == "LOCAL_TEXT_EDIT_PROPOSALS"
        assert trajectory["info"]["local_edit_result"] == result.diagnostics["local_edit_result"]
        for forbidden in ("PRIVATE-SOURCE-COMMENT", private.sha256, "source_bindings", "local_edit_result"):
            assert forbidden not in json.dumps(requests)
        if available:
            assert result.status.value == "PROPOSED_PATCH", result
            assert "PRIVATE-SOURCE-COMMENT" in result.diff_text
            assert apply_and_check(repo, result.diff_text).returncode == 0
        else:
            assert result.status.value == "NO_PATCH", result
            assert result.diagnostics["local_edit_result"]["candidates"][0]["reason"] == "SOURCE_UNAVAILABLE"
    assert outgoing[0] == outgoing[1]
    assert results[0].diff_text != results[1].diff_text


def test_real_failed_patch_changes_local_selection_before_another_effect(tmp_path):
    repo, public = repository(tmp_path)
    revision = public.to_dict()["repository_revision"]
    variants = proposal(("a - b", "a * b"), ("a - b", "a + b"))
    first = propose_local_edits(task=public, information=information(source=SOURCE, revision=revision), proposal=variants)
    failed = apply_and_check(repo, first["diff_text"])
    assert failed.returncode != 0
    assert "AssertionError" in failed.stderr
    subprocess.run(["git", "-C", str(repo), "restore", "src/calc.py"], check=True)
    private = information(source=SOURCE, revision=revision, extra=[feedback(first["diff_text"], False)])
    command = LOCAL_EDIT_COMMAND + canonical_json_bytes(variants).decode()
    with provider_endpoint(model="gemini-3.1-flash-lite", path="/v1beta/openai/chat/completions",
                           command=command) as (endpoint, requests):
        result, trajectory = invoke(tmp_path, repo, public, private, endpoint)
    assert result.status.value == "PROPOSED_PATCH", result
    assessment = trajectory["info"]["local_edit_result"]
    assert assessment["selected_index"] == 1
    assert assessment["candidates"][0]["reason"] == "EXACT_VERIFIED_PATCH_REJECTED"
    assert apply_and_check(repo, result.diff_text).returncode == 0
    assert private.sha256 not in json.dumps(requests)
    assert len(requests) == 1


# Protocol refusal cases live in test_local_edit_contract.py and
# test_public_request_policy_contract.py as pure checks. This real-Mini
# shard sends ordinary typed edit proposals only.

def test_mini_preserves_literal_replacement_bytes_through_transport_and_git_apply(tmp_path):
    repo, public = repository(tmp_path)
    private = information(source=SOURCE, revision=public.to_dict()["repository_revision"])
    replacement = "a + b  # e\u0301"
    variants = proposal(("a - b", replacement))
    command = LOCAL_EDIT_COMMAND + json.dumps(variants, ensure_ascii=False)
    with provider_endpoint(model="gemini-3.1-flash-lite", path="/v1beta/openai/chat/completions",
                           command=command) as (endpoint, requests):
        result, trajectory = invoke(tmp_path, repo, public, private, endpoint)
    assert len(requests) == 1
    assert result.status.value == "PROPOSED_PATCH", result
    assert trajectory["info"]["local_edit_result"]["proposal"] == variants
    assert result.diagnostics["local_edit_result"]["proposal"] == variants
    assert apply_and_check(repo, result.diff_text).returncode == 0
    assert (repo / "src/calc.py").read_bytes() == SOURCE.replace("a - b", replacement).encode("utf-8")
