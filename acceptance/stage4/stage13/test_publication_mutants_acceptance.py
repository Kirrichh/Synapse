"""Controlled product mutations must change the real acceptance verdict."""

import inspect
import textwrap

import pytest

from acceptance.stage4.stage13._case import negative_attempt, publication_case
from synapse.experiments.gold import library_admission as LA
from synapse.experiments.gold.admission_journal import JournalAdapterViolation
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.library import BehaviorLibrary
from synapse.experiments.gold.stage13 import publication_store as PS
from synapse.experiments.gold.stage13.publication import PublicationViolation


@pytest.fixture(scope="module")
def attempt(tmp_path_factory):
    return negative_attempt(tmp_path_factory.mktemp("mutation-attempt"))


def _complete_publication(case, monkeypatch):
    original = case.publisher._phase

    def observed(tx, phase, ticket):
        original(tx, phase, ticket)
        if phase in {"LIBRARY", "INDEXED", "VERIFIED"}:
            assert case.project.library.search_index() == ()

    monkeypatch.setattr(case.publisher, "_phase", observed)
    result = case.publisher.publish(case.request)
    assert result is not None
    value = result.payload()
    assert len(case.project.library.search_index()) == 1
    before = case.project.fence.current_epoch()
    assert case.publisher.publish(case.request).payload() == value
    assert open_gold_project(case.project.declaration.state_root).fence.current_epoch() == before
    return result


def test_unmodified_publication_satisfies_the_mutation_oracle(tmp_path, monkeypatch, attempt):
    _complete_publication(publication_case(tmp_path / "project", attempt), monkeypatch)


@pytest.mark.parametrize("mutation", ["visibility", "independent_sources", "complete_members", "idempotency"])
def test_product_guard_mutant_is_killed(tmp_path, monkeypatch, attempt, mutation):
    case = publication_case(tmp_path / "project", attempt)
    if mutation == "visibility":
        monkeypatch.setattr(BehaviorLibrary, "_visible_index_entries", lambda self, entries: entries)
    elif mutation == "independent_sources":
        original = LA.create_production_write_authority_binding
        source = textwrap.dedent(inspect.getsource(original))
        before, after = "for actor in source_actors:", "for actor in ():"
        assert source.count(before) == 1
        namespace = dict(original.__globals__)
        exec(compile(source.replace(before, after), original.__code__.co_filename, "exec"), namespace)
        monkeypatch.setattr(LA, original.__name__, namespace[original.__name__])
    elif mutation == "complete_members":
        original = PS.PublicationStore._committed_members
        monkeypatch.setattr(PS.PublicationStore, "_committed_members", lambda self, *args:
            [member for member in original(self, *args) if member["path"] != "lifecycle/lifecycle-v1.journal"])
    else:
        # Mutate the publication owner's body with its own globals. The
        # observation decorator is retained when this source is recompiled.
        original = inspect.unwrap(PS.PublicationStore.publish)
        source = textwrap.dedent(inspect.getsource(original))
        before = 'if committed_transaction_exists(self.root / "committed", transaction_id=tx):'
        assert source.count(before) == 1
        namespace = dict(original.__globals__)
        exec(compile(source.replace(before, "if False:"), original.__code__.co_filename, "exec"), namespace)
        monkeypatch.setattr(PS.PublicationStore, "publish", namespace[original.__name__])
    with pytest.raises((AssertionError, JournalAdapterViolation, PublicationViolation)):
        _complete_publication(case, monkeypatch)
