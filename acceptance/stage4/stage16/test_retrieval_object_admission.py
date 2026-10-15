"""Retrieval admits the compatible subset without losing rejected evidence."""

import json

import pytest

from synapse.experiments.gold import retrieval as R
from synapse.experiments.gold.admission import require_committed_decision
from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.compatibility import ConflictKind
from synapse.experiments.gold.lifecycle import LifecycleState
from tests.gold_frozen_candidates import frozen_for_retriever
from tests.test_stage4_gold_compatibility import _make_harness, _ref
from tests.test_stage4_gold_retrieval import (
    _admission_for,
    _configured_retriever,
    _durable_enumeration,
)


def _enumerated_case(root, *, extra=2):
    harness = _make_harness(root, lifecycle_state=LifecycleState.STALE, extra_resolved=extra)
    retriever, _, query = _configured_retriever(
        harness, scorer=lambda *_: 900_000, selected_set_limit=max(1, extra),
    )
    enumeration, journal, persistence = _durable_enumeration(
        retriever=retriever, context=harness.context, query=query,
        frozen=frozen_for_retriever(retriever),
    )
    expected = tuple(sorted(
        (R.candidate_subject_ref(item[7]) for item in harness.extra_candidates),
        key=lambda ref: (ref.kind.value, ref.ref_id, ref.sha256),
    ))
    return harness, retriever, query, enumeration, journal, persistence, expected


def _load(case, admission):
    harness, retriever, query, enumeration, _, persistence, _ = case
    return R.select_and_load_durably(
        retriever=retriever, context=harness.context, query=query,
        enumeration=enumeration, admission=admission, persistence=persistence,
    )


def test_mixed_candidates_load_only_compatible_with_durable_rejected_audit(tmp_path, monkeypatch):
    case = _enumerated_case(tmp_path)
    harness, _, _, enumeration, journal, persistence, expected = case
    reads = []
    original = harness.library.get_verified_behavior

    def observe_read(content_key, manifest_id):
        reads.append(content_key)
        return original(content_key, manifest_id)

    monkeypatch.setattr(harness.library, "get_verified_behavior", observe_read)
    admission = _admission_for(enumeration, harness.context, refs=expected, journal=journal)
    result = _load(case, admission)
    assert set(reads) == {item[0].content_key for item in harness.extra_candidates}
    assert harness.unit.content_key not in reads
    audits = result.decision.considered_candidates
    rejected = [item for item in audits if item.disposition is R.CandidateDisposition.REJECTED]
    assert len(audits) == 3 and len(rejected) == 1
    assert rejected[0].descriptor_id == harness.descriptor.descriptor_id
    assert rejected[0].compatibility_decision_id == harness.decision.decision_id
    assert len(result.load_decisions) == 2
    causal = persistence.admission_causal_history
    raw = causal.resolve_ref(R.retrieval_decision_ref(result.decision))
    assert json.loads(raw)["considered_candidates"] == [item.to_dict() for item in audits]
    assert causal.resolve_ref(R.retrieval_causal_record_ref(result.causal_record))
    require_committed_decision(admission.decision_receipt, decision=admission.decision, journal=journal)


@pytest.mark.parametrize("alteration", ["missing", "extra"])
def test_sealed_verdict_for_different_subset_cannot_load_any_candidate(tmp_path, monkeypatch, alteration):
    case = _enumerated_case(tmp_path)
    harness, _, _, enumeration, journal, _, expected = case
    subjects = expected[:1] if alteration == "missing" else enumeration.subject_refs
    admission = _admission_for(enumeration, harness.context, refs=subjects, journal=journal)
    reads = []
    monkeypatch.setattr(harness.library, "get_verified_behavior", lambda *args: reads.append(args))
    with pytest.raises(R.RetrievalViolation) as rejected:
        _load(case, admission)
    assert rejected.value.failure_code is R.RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE
    assert reads == []


def test_all_rejected_candidates_keep_committed_refusal_and_load_nothing(tmp_path, monkeypatch):
    case = _enumerated_case(tmp_path, extra=0)
    harness, _, _, enumeration, journal, persistence, _ = case
    admission = _admission_for(
        enumeration, harness.context, refs=enumeration.subject_refs, admit=False, journal=journal,
    )
    reads = []
    monkeypatch.setattr(harness.library, "get_verified_behavior", lambda *args: reads.append(args))
    result = _load(case, admission)
    assert admission.decision is not None and not admission.decision.admitted
    assert admission.decision.subject_refs == enumeration.subject_refs
    require_committed_decision(admission.decision_receipt, decision=admission.decision, journal=journal)
    assert result.decision.selected_candidate_ids == () and result.load_decisions == ()
    assert len(result.decision.considered_candidates) == 1 and reads == []
    assert persistence.admission_causal_history.contains_ref(R.retrieval_causal_record_ref(result.causal_record))


def test_authoritative_unresolved_conflict_blocks_run_but_retains_component_audit(tmp_path):
    harness = _make_harness(tmp_path, extra_resolved=1)
    other = harness.extra_candidates[0][7]
    pair = tuple(sorted((harness.descriptor.descriptor_id.value, other.descriptor_id.value)))
    harness.conflict_matrix[pair] = (
        ConflictKind.CONTRADICTORY_EVIDENCE, (_ref('authoritative-conflict', RefKind.SOURCE_EVIDENCE),),
    )
    retriever, _, query = _configured_retriever(harness, scorer=lambda *_: 900_000)
    enumeration, journal, persistence = _durable_enumeration(
        retriever=retriever, context=harness.context, query=query,
        frozen=frozen_for_retriever(retriever),
    )
    with pytest.raises(R.RetrievalViolation) as rejected:
        enumeration.require_resolved_conflicts()
    assert rejected.value.failure_code is R.RetrievalFailureCode.CONFLICT_UNRESOLVED
    admission = _admission_for(enumeration, harness.context, journal=journal)
    result = R.select_and_load_durably(
        retriever=retriever, context=harness.context, query=query,
        enumeration=enumeration, admission=admission, persistence=persistence,
    )
    assert result.decision.outcome is R.RetrievalOutcome.CONFLICT_BLOCKED
    assert len(result.decision.considered_candidates) == 2 and result.load_decisions == ()
