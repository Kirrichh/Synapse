"""Source operations use the same atomic publication/undo recovery boundary."""

import pytest

from acceptance.stage4.stage16._source_inputs import prepare, learn
from synapse.experiments.gold.source_ingestion import execute_source_ingestion
from synapse.experiments.gold.stage13.publication_store import PublicationStore
from synapse.experiments.gold.knowledge_environment import open_gold_project


@pytest.mark.parametrize('phase', ['PREPARED', 'INDEXED', 'COMMITTED'])
def test_source_publication_interruption_is_whole_or_invisible_after_restart(tmp_path, monkeypatch, phase):
    _, state, input_path = prepare(tmp_path)
    original = PublicationStore._phase
    def interrupt(self, tx, observed, ticket):
        original(self, tx, observed, ticket)
        if observed == phase:
            raise SystemExit('acceptance process interruption')
    monkeypatch.setattr(PublicationStore, '_phase', interrupt)
    with pytest.raises(SystemExit):
        execute_source_ingestion(state_root=state, input_path=input_path)
    # Canonical CLI in a new interpreter performs existing project recovery.
    code, result = learn(state, input_path)
    assert code == (0 if phase == 'COMMITTED' else 2), result
    assert result['status'] == ('PUBLISHED' if phase == 'COMMITTED' else 'INTERRUPTED')
    project = open_gold_project(state)
    assert len(project.library.search_index()) == (1 if phase == 'COMMITTED' else 0)
    assert project.fence.current_epoch() % 2 == 0
    again, reopened = learn(state, input_path)
    assert again == code and reopened == result
