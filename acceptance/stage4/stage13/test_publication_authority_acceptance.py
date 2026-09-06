"""Exact publication authority and verification of every retained participant."""

import copy
from dataclasses import fields, replace

import pytest

from acceptance.stage4.stage13._case import negative_attempt, publication_case
from synapse.experiments.gold.admission_journal import JournalAdapterViolation
from synapse.experiments.gold.contracts import ActorIdentity
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.persistence import PersistenceViolation
from synapse.experiments.gold.stage10.context_codec import encode_canonical
from synapse.experiments.gold.stage13.publication import EVALUATOR, PublicationViolation


@pytest.fixture(scope="module")
def attempt(tmp_path_factory):
    return negative_attempt(tmp_path_factory.mktemp("authority-attempt"))


@pytest.mark.parametrize("field", ["grant", "subject_ref", "required_transition", "authority_identity"])
def test_evaluator_decision_remains_exact_until_execution(tmp_path, monkeypatch, attempt, field):
    case = publication_case(tmp_path / "project", attempt)
    authority = type(case.publisher.authority)
    original = authority.evaluate

    def changed(self, request, **kwargs):
        decision = original(self, request, **kwargs)
        payload = copy.deepcopy(decision.payload())
        payload[field] = None
        clone = object.__new__(type(decision))
        for item in fields(decision):
            object.__setattr__(clone, item.name, getattr(decision, item.name))
        object.__setattr__(clone, "_raw", encode_canonical(payload))
        return clone

    monkeypatch.setattr(authority, "evaluate", changed)
    with pytest.raises(JournalAdapterViolation) as caught:
        case.publisher.publish(case.request)
    assert isinstance(caught.value.__cause__, PublicationViolation)
    assert open_gold_project(case.project.declaration.state_root).library.search_index() == ()


def test_authority_is_independent_of_every_configured_source(tmp_path, attempt):
    case = publication_case(tmp_path / "project", attempt)
    with pytest.raises(PublicationViolation):
        replace(case.publisher.authority, source_actors=(ActorIdentity(EVALUATOR.value),)).validate()


def test_every_committed_participant_is_required_on_read_and_recovery(tmp_path, attempt):
    case = publication_case(tmp_path / "project", attempt)
    result = case.publisher.publish(case.request)
    value = result.payload()
    for member in value["participants"]:
        path = case.project.declaration.state_root / member["path"]
        original = path.read_bytes()
        try:
            path.write_bytes(original[:-1])
            with pytest.raises((PublicationViolation, PersistenceViolation)):
                result.payload()
            with pytest.raises((PublicationViolation, PersistenceViolation)):
                open_gold_project(case.project.declaration.state_root)
        finally:
            path.write_bytes(original)
    assert result.payload() == value


def test_actual_c1_evidence_is_retained_inside_the_prepared_transaction(tmp_path, attempt):
    case = publication_case(tmp_path / "project", attempt)
    result = case.publisher.publish(case.request)
    result.payload()
    for ref, raw in attempt.c1.retained_artifacts():
        path = case.publisher.root / "prepared" / result.transaction_id / ref.sha256
        assert path.read_bytes() == raw
        try:
            path.write_bytes(raw + b"x")
            with pytest.raises(PersistenceViolation):
                result.payload()
        finally:
            path.write_bytes(raw)
