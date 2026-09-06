"""The transaction rejects additional, otherwise valid participant writes."""

import inspect
import textwrap

import pytest

from acceptance.stage4.stage13._case import negative_attempt, publication_case
from synapse.experiments.gold import taint as T
from synapse.experiments.gold.admission_journal import JournalAdapterViolation
from synapse.experiments.gold.contracts import ActorIdentity
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.stage13 import publication_store as PS


@pytest.fixture(scope="module")
def attempt(tmp_path_factory):
    return negative_attempt(tmp_path_factory.mktemp("write-set-mutation"))


@pytest.mark.parametrize("mutated", [False, True])
def test_exact_write_set_guard_has_an_external_mutation_oracle(tmp_path, monkeypatch, attempt, mutated):
    case = publication_case(tmp_path / "project", attempt)
    original_phase = case.publisher._phase

    def extra_record(tx, name, ticket):
        original_phase(tx, name, ticket)
        if name == "AUTHORIZED":
            extra = T.classify_source_taint(authority_handle=case.project.authority_handle,
                subject_ref=case.request.taint.subject_ref, taint_classes=(T.TaintClass.TRUSTED_PLATFORM_DERIVED,),
                producer_actor_ids=(ActorIdentity("extra-producer"),), source_actor_ids=(ActorIdentity("extra-source"),),
                admission_actor_ids=(ActorIdentity("extra-admission"),), consumer_actor_ids=())
            case.project.taint_store.append_profile(authority_handle=case.project.authority_handle,
                                                   profile=extra, mutation_ticket=ticket)

    monkeypatch.setattr(case.publisher, "_phase", extra_record)
    if mutated:
        original = PS._verify_write_set
        source = textwrap.dedent(inspect.getsource(original))
        check = 'if len(taint) != 1 or taint[0]["kind"] != "SOURCE_PROFILE" or taint[0]["payload"] != request["taint"]:'
        assert source.count(check) == 1
        namespace = dict(original.__globals__)
        exec(compile(source.replace(check, "if False:"), original.__code__.co_filename, "exec"), namespace)
        monkeypatch.setattr(PS, original.__name__, namespace[original.__name__])

    def acceptance_oracle():
        with pytest.raises(JournalAdapterViolation) as caught:
            case.publisher.publish(case.request)
        assert isinstance(caught.value.__cause__, PS.PublicationViolation)
        assert open_gold_project(case.project.declaration.state_root).library.search_index() == ()

    if mutated:
        with pytest.raises(pytest.fail.Exception):
            acceptance_oracle()
    else:
        acceptance_oracle()
