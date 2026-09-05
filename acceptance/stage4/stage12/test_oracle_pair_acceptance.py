"""One heavy scenario: a resolving oracle names someone else's commit."""

from dataclasses import replace
import copy
import hashlib
import pytest

from acceptance.stage4.stage11._builders import ScriptedOracle, run_world
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy
from synapse.experiments.gold.stage12.outcome import inspect_outcome
from synapse.experiments.gold.stage10.context_codec import encode_canonical


def test_resolved_oracle_for_another_commit_is_invalid_contract(tmp_path, monkeypatch):
    original = ScriptedOracle.verify

    def verify(self, worktree_path, task):
        result = original(self, worktree_path, task)
        return replace(result, diagnostics={**result.diagnostics, "verified_commit": "0" * 40})

    monkeypatch.setattr(ScriptedOracle, "verify", verify)
    world = run_world(tmp_path, max_attempts=1, fallback_policy=FallbackPolicy.FORBIDDEN,
                      oracle_outcomes=[(True, False)])
    result = world.execute()
    attempt = load_run_state(world.composition.record_store).attempts[0].result
    assert attempt.c1_status == "GOLD_APPLIED_WITH_EVIDENCE"
    assert attempt.oracle_resolved is True
    assert result.structured_outcome["payload"]["status"] == "INVALID_CONTRACT"
    assert attempt.verified_finding_sha256 is None
    assert world.worker_process.calls == world.oracle.calls == 1
    changed = copy.deepcopy(result.structured_outcome)
    changed["payload"]["status"] = "FULL"
    raw = encode_canonical(changed["payload"])
    digest = hashlib.sha256(raw).hexdigest()
    changed["outcome_ref"].update(ref_id=digest, sha256=digest, byte_length=len(raw))
    with pytest.raises(ValueError, match="terminal attempt"):
        inspect_outcome(changed)
