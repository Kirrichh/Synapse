"""Negative facts cross the same per-dimension compatibility boundary as all knowledge."""

from dataclasses import replace
import inspect
import textwrap

import pytest

from synapse.experiments.gold import compatibility as C
from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.contracts import RepositoryRevision
from synapse.experiments.gold.compatibility_store import compatibility_record_ref
from synapse.experiments.gold.stage12 import reusable
from synapse.experiments.gold.stage13.publication import reference
from synapse.experiments.gold.stage13.rejected_patch_profile import REJECTED_PATCH_DOMAIN_V2, build_rejected_patch_guard
from tests.test_stage4_gold_compatibility import _make_harness, REVISION


CASES = (
    ("POLICY", "context_policy_version", "changed-policy/v1", "INCOMPATIBLE_POLICY"),
    ("ENVIRONMENT_AND_TOOLCHAIN", "context_environment_version", "changed-environment/v1", "INCOMPATIBLE_ENVIRONMENT"),
    ("ENVIRONMENT_AND_TOOLCHAIN", "context_tool_version", "changed-tool/v1", "INCOMPATIBLE_TOOLCHAIN"),
    ("ORACLE", "context_oracle_ref_name", "changed-oracle-configuration", "INCOMPATIBLE_ORACLE"),
    ("REPOSITORY_REVISION", "context_repository_revision", RepositoryRevision.git_commit("3" * 40), "INCOMPATIBLE_REVISION"),
)


def historical_fact(root, **consumer):
    domain = replace(reference({"base_revision": REVISION.git_sha}, REJECTED_PATCH_DOMAIN_V2), kind=RefKind.CONTRACT_CONDITION)
    unit = build_rejected_patch_guard(domain_ref=domain,
        report=replace(reference({"report": "retained-negative-proof"}), kind=RefKind.SOURCE_EVIDENCE),
        oracle=reference({"oracle": "retained-negative-result"}))
    return _make_harness(root, behavior_core=unit.to_dict()["core"], producer_base_revision=REVISION, **consumer)


@pytest.fixture(scope="module", params=CASES, ids=[case[1] for case in CASES])
def incompatible(request, tmp_path_factory):
    dimension, field, value, verdict = request.param
    case = historical_fact(tmp_path_factory.mktemp(field), **{field: value})
    return case, dimension, verdict


def decision(case):
    return C.evaluate_compatibility(evaluator=case.evaluator, context=case.context,
                                   descriptor=case.descriptor, index_entry=case.entry)


def require_rejection(case, dimension, verdict):
    result = decision(case)
    assert result.decision_kind.value == verdict
    checked = next(item for item in result.evidence.dimensions if item.dimension.value == C.CompatibilityDimension[dimension].value)
    assert checked.result.value == "FAIL"


def test_same_context_negative_fact_passes_every_required_dimension(tmp_path):
    case = historical_fact(tmp_path)
    assert decision(case).decision_kind is C.CompatibilityDecisionKind.COMPATIBLE


def test_historical_fact_rejects_changed_consumer_dimension(incompatible):
    require_rejection(*incompatible)


def test_each_dimension_has_a_controlled_mutation_oracle(incompatible, monkeypatch):
    case, dimension, verdict = incompatible
    require_rejection(case, dimension, verdict)
    original = C._dimension_facts
    source = textwrap.dedent(inspect.getsource(original))
    if dimension == "ENVIRONMENT_AND_TOOLCHAIN":
        before = "environment_present and tools_present and environment_matches and tools_match"
        after = "environment_present and tools_present"
    else:
        before = "passed = producer.sha256 == consumer.sha256"
        after = f"passed = dimension is CompatibilityDimension.{dimension} or producer.sha256 == consumer.sha256"
    assert source.count(before) == 1
    namespace = dict(original.__globals__)
    exec(compile(source.replace(before, after), original.__code__.co_filename, "exec"), namespace)
    monkeypatch.setattr(C, "_dimension_facts", namespace["_dimension_facts"])
    with pytest.raises(AssertionError):
        require_rejection(case, dimension, verdict)


def test_future_use_task_binding_has_an_external_mutation_oracle(tmp_path, monkeypatch):
    case = historical_fact(tmp_path)
    value = {"ref": compatibility_record_ref(case.context).to_dict(), "record": case.context.to_dict()}
    assert reusable.inspect_reusable_use_context(value, base_revision=REVISION.git_sha,
        task_contract_ref=case.context.task_contract_ref.to_dict()) == value["record"]
    different_task = replace(reference({"task": "another-governing-contract"}), kind=RefKind.CONTRACT_CONDITION).to_dict()

    def require_rejection():
        with pytest.raises(ValueError):
            reusable.inspect_reusable_use_context(value, base_revision=REVISION.git_sha, task_contract_ref=different_task)

    require_rejection()
    original = reusable.inspect_reusable_use_context
    source = textwrap.dedent(inspect.getsource(original))
    condition = 'or record["task_contract_ref"] != task_contract_ref'
    assert source.count(condition) == 1
    namespace = dict(original.__globals__)
    exec(compile(source.replace(condition, ""), original.__code__.co_filename, "exec"), namespace)
    monkeypatch.setattr(reusable, "inspect_reusable_use_context", namespace["inspect_reusable_use_context"])
    with pytest.raises(pytest.fail.Exception):
        require_rejection()
