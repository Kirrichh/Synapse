"""The canonical run composition preserves per-object admission authority."""

import json
from dataclasses import replace

import pytest

from acceptance.stage4.stage11._retrieval_inputs import acceptance_retrieval_bindings
from synapse.experiments.gold.lifecycle import LifecycleState
from synapse.experiments.gold.compatibility import ConflictKind, create_conflict_evidence_proposal
from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.contracts import ActorIdentity, IdentityDomain, compute_record_id
from synapse.experiments.gold.persistence import scan_journal
from synapse.experiments.gold.retrieval import (
    RetrievalFailureCode, RetrievalViolation, candidate_subject_ref, retrieval_causal_record_ref,
)
from tests.test_stage4_gold_compatibility import _ref
from tests.test_stage4_gold_consumption_evidence import production_point_of_use_case


def test_run_retrieval_excludes_stale_object_through_same_four_gate_subjects(tmp_path):
    case = production_point_of_use_case(
        tmp_path, primary_lifecycle_state=LifecycleState.STALE, extra_resolved=1,
        retrieval_bindings=acceptance_retrieval_bindings(), retrieval_root=tmp_path / 'retrieval',
    )
    compatible = case.world.extra_candidates[0]
    expected = (candidate_subject_ref(compatible[7]),)
    assert case.chain.admitted
    assert all(decision.subject_refs == expected for decision in case.chain.decisions())
    retrieved = case.durable_retrieval_result
    assert retrieved is not None and retrieved.admission.decision.subject_refs == expected
    loads = retrieved.result.load_decisions
    assert len(loads) == 1 and loads[0].loaded_content_key == compatible[0].content_key.value
    assert case.subject not in expected
    # Stage 3 records the rejected object before the snapshot narrows the run.
    frames = scan_journal(case.compatibility_history.path, create_if_missing=False).frames
    records = [json.loads(json.loads(frame.payload)['canonical_record']) for frame in frames]
    stale = [record for record in records if record.get('decision_kind') == 'STALE']
    assert len(stale) == 1
    assert stale[0]['evidence']['descriptor_id'] == case.world.descriptor.descriptor_id.to_dict()
    assert any(dimension['reason'] == 'LIFECYCLE_STALE'
               for dimension in stale[0]['evidence']['dimensions'])
    causal_ref = retrieval_causal_record_ref(retrieved.result.causal_record)
    assert case.binding.admission_causal_history.contains_ref(causal_ref)
    # Reading retained evidence after the run does not recompute gate coverage.
    raw = case.binding.admission_causal_history.resolve_ref(causal_ref)
    assert json.loads(raw)['payload']['retrieval_gate_decision_ref']['ref_id'] == (
        retrieved.admission.decision.gate_decision_id.digest_sha256
    )


def _conflict_proposal(descriptors, *, known):
    unknown = compute_record_id(
        domain=IdentityDomain.COMPATIBILITY_SUBJECT_DESCRIPTOR,
        canonical_bytes=b'{"descriptor":"unavailable-conflict-witness"}',
    )
    return (create_conflict_evidence_proposal(
        conflict_kind=ConflictKind.CONTRADICTORY_EVIDENCE,
        proposer_actor=ActorIdentity('external-conflict-proposer'),
        left_descriptor_id=descriptors[0].descriptor_id,
        right_descriptor_id=descriptors[1].descriptor_id if known else unknown,
        scope=('src/a.py',), binding_targets=('pkg.symbol',),
        evidence_refs=(_ref('reported-conflict', RefKind.SOURCE_EVIDENCE),),
    ),)


def _new_conflict_bindings(*, known):
    observation = {'reported': False}

    def report(_context, _decisions, descriptors):
        observation['reported'] = True
        return _conflict_proposal(descriptors, known=known)

    def assess(*_):
        if not observation['reported']:
            return None, ()
        return ConflictKind.CONTRADICTORY_EVIDENCE, (_ref('observed-conflict', RefKind.SOURCE_EVIDENCE),)

    return replace(acceptance_retrieval_bindings(), conflict_proposal_resolver=report), assess


@pytest.mark.parametrize('known, expected', [
    (False, RetrievalFailureCode.CONFLICT_SCAN_INCOMPLETE),
    (True, RetrievalFailureCode.CONFLICT_UNRESOLVED),
])
def test_retrieval_only_blocked_conflict_scan_refuses_canonical_environment(tmp_path, known, expected):
    bindings, assessor = _new_conflict_bindings(known=known)
    with pytest.raises(RetrievalViolation) as rejected:
        production_point_of_use_case(
            tmp_path, retrieval_bindings=bindings, retrieval_root=tmp_path / 'retrieval',
            extra_resolved=1, conflict_assessor=assessor,
        )
    assert rejected.value.failure_code is expected
    retrieval_root = next((tmp_path / 'retrieval').iterdir())
    history = retrieval_root / 'compatibility' / 'compatibility.journal'
    records = [json.loads(json.loads(frame.payload)['canonical_record'])
               for frame in scan_journal(history, create_if_missing=False).frames]
    kind = 'UNRESOLVED_CONFLICT' if known else 'SCAN_INCOMPLETE'
    scans = [record for record in records if record.get('decision_kind') == kind]
    assert len(scans) == 1
    gate_history = retrieval_root / 'retrieval-gate' / 'decisions.journal'
    if gate_history.exists():
        assert scan_journal(gate_history, create_if_missing=False).frames == ()
