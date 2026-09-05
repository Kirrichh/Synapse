"""One real C1 execution supplies the report, bindings and accepted plan."""

import copy
import hashlib

import pytest

from acceptance.stage4.stage11._builders import run_world
from synapse.experiments.gold.stage12.outcome import inspect_outcome
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy
from synapse.experiments.gold.stage10.context_codec import encode_canonical


@pytest.fixture(scope="module")
def completed(tmp_path_factory):
    world = run_world(tmp_path_factory.mktemp("full-outcome"), max_attempts=1,
                      fallback_policy=FallbackPolicy.FORBIDDEN, oracle_outcomes=[(True, False)])
    result = world.execute()
    return world, result, load_run_state(world.composition.record_store).attempts[0]


def test_full_binds_the_report_plan_context_and_terminal_run(completed):
    world, run, attempt = completed
    outcome = inspect_outcome(attempt.result.structured_outcome)
    proof = outcome["verification"]["payload"]
    assert outcome["status"] == "FULL"
    assert proof["context_sha256"] == attempt.context.context_sha256
    assert proof["c1"]["c1_result_ref"] == attempt.result.c1_result_ref.to_dict()
    assert proof["c1"]["oracle_result_ref"] == attempt.result.oracle_result_ref.to_dict()
    assert proof["plan"]["authority_route"] == "POLICY_ACCEPTED"
    assert proof["resolved_bindings"]
    assert proof["obligations"] == [{
        "operation_id": "operation-main", "condition_ref": proof["c1"]["command_policy_ref"],
        "evidence_ref": proof["c1"]["report_ref"], "discharged": True,
    }]
    assert run.structured_outcome["payload"]["status"] == "FULL"
    assert run.structured_outcome["payload"]["attempt_outcomes"][0]["result_sha256"] == attempt.result.result_sha256
    assert world.worker_process.calls == world.oracle.calls == 1


@pytest.mark.parametrize("changes", [
    {"status": "VERIFIED_REUSABLE_PARTIAL"},
    {"created_behaviors": [{"admission_id": "approved"}]},
    {"publication_result": "COMMITTED"},
    {"telemetry_completeness": "COMPLETE"},
])
def test_rehashing_a_label_does_not_create_its_evidence(completed, changes):
    _, _, attempt = completed
    changed = copy.deepcopy(attempt.result.structured_outcome)
    changed["payload"].update(changes)
    raw = encode_canonical(changed["payload"])
    digest = hashlib.sha256(raw).hexdigest()
    changed["outcome_ref"].update(ref_id=digest, sha256=digest, byte_length=len(raw))
    with pytest.raises(ValueError):
        inspect_outcome(changed)


def test_result_reads_revalidate_without_repeating_execution(completed):
    world, result, _ = completed
    assert world.controller.load_result().stored_dict() == result.stored_dict()
    assert world.worker_process.calls == world.oracle.calls == 1


@pytest.mark.parametrize("status", ["UNRESOLVED", "FAIL", "INFRA_ERROR", "INVALID_CONTRACT", "NO_CANDIDATE", "VERIFIED_REUSABLE_PARTIAL"])
def test_run_rehash_cannot_relabel_a_verified_attempt(completed, status):
    _, run, _ = completed
    changed = copy.deepcopy(run.structured_outcome)
    changed["payload"]["status"] = status
    raw = encode_canonical(changed["payload"])
    digest = hashlib.sha256(raw).hexdigest()
    changed["outcome_ref"].update(ref_id=digest, sha256=digest, byte_length=len(raw))
    with pytest.raises(ValueError, match="terminal attempt"):
        inspect_outcome(changed)


@pytest.mark.parametrize("field,value", [
    ("evidence_ref", None), ("oracle_result_ref", None), ("oracle_resolved", False),
    ("commands_complete", False), ("task_ref", None),
])
def test_rehashed_full_without_a_required_c1_predicate_is_rejected(completed, field, value):
    _, _, attempt = completed
    changed = copy.deepcopy(attempt.result.structured_outcome)
    verification = changed["payload"]["verification"]
    verification["payload"]["c1"][field] = value
    for record, ref_key in ((verification, "verification_ref"), (changed, "outcome_ref")):
        raw = encode_canonical(record["payload"])
        digest = hashlib.sha256(raw).hexdigest()
        record[ref_key].update(ref_id=digest, sha256=digest, byte_length=len(raw))
    with pytest.raises(ValueError):
        inspect_outcome(changed)
