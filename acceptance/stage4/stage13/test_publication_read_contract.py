"""Traversal lifecycle contracts only; the physical evidence reader is replaced.

These tests establish no C1 or publication authority. Separate integration
acceptance checks the actual immutable files and retained predecessor proofs.
"""
from collections import Counter
from contextvars import copy_context
import json
from threading import Thread

import pytest

from synapse.experiments.gold.stage13 import publication_store as storage
from synapse.experiments.gold.stage13.publication import PublicationViolation, reference
from synapse.experiments.gold.stage10.context_codec import encode_canonical
from synapse.experiments.gold.stage14 import reconstruction
from synapse.experiments.gold.stage14.graph import GraphBuilder, LineageNodeClass
from synapse.experiments.gold.stage14.read_traversal import publication_read_scope


def install_reader(monkeypatch, root, visit):
    calls = Counter()

    def physical(self, *, retain_retired_source):
        calls[self.transaction_id, retain_retired_source] += 1
        payload = visit(self.transaction_id, retain_retired_source)
        return payload, json.dumps(payload, sort_keys=True).encode()

    monkeypatch.setattr(storage.PublicationResult, '_read_physical_payload', physical)
    return lambda name: storage.PublicationResult(root, name), calls


def test_shared_predecessor_is_read_once_per_traversal_and_reopened_next_time(tmp_path, monkeypatch):
    observed = [1]

    def visit(name, retired):
        if name == 'root':
            return {'direct': result('shared').payload(), 'indirect': result('branch').payload()}
        if name == 'branch':
            return result('shared').payload()
        return {'value': observed[0]}

    result, calls = install_reader(monkeypatch, tmp_path, visit)
    assert result('root').payload() == {'direct': {'value': 1}, 'indirect': {'value': 1}}
    assert calls['shared', False] == 1
    observed[0] = 2
    assert result('root').payload() == {'direct': {'value': 2}, 'indirect': {'value': 2}}
    assert calls['shared', False] == 2


def test_decorated_read_shares_predecessors_across_roots_only_until_return(tmp_path, monkeypatch):
    """Measure traversal reuse with a replaced physical reader, not durability."""
    observed = [1]

    def visit(name, retired):
        if name in {'left', 'right'}:
            return {'root': name, 'shared': result('shared').payload()}
        return {'value': observed[0]}

    result, calls = install_reader(monkeypatch, tmp_path, visit)

    @publication_read_scope()
    def read_roots():
        return result('left').payload(), result('right').payload()

    assert read_roots() == (
        {'root': 'left', 'shared': {'value': 1}},
        {'root': 'right', 'shared': {'value': 1}},
    )
    assert calls == Counter({('left', False): 1, ('right', False): 1, ('shared', False): 1})
    observed[0] = 2
    assert read_roots() == (
        {'root': 'left', 'shared': {'value': 2}},
        {'root': 'right', 'shared': {'value': 2}},
    )
    assert calls == Counter({('left', False): 2, ('right', False): 2, ('shared', False): 2})


def test_exception_after_a_root_read_discards_the_decorated_scope(tmp_path, monkeypatch):
    """A reader substitute exposes cleanup after the outer operation fails."""
    observed, fail = [1], [True]

    def visit(name, retired):
        if name in {'left', 'right'}:
            return result('shared').payload()
        return {'value': observed[0]}

    result, calls = install_reader(monkeypatch, tmp_path, visit)

    @publication_read_scope()
    def read_roots():
        left = result('left').payload()
        if fail[0]:
            raise RuntimeError('outer reconstruction interrupted')
        return left, result('right').payload()

    with pytest.raises(RuntimeError, match='outer reconstruction interrupted'):
        read_roots()
    assert calls == Counter({('left', False): 1, ('shared', False): 1})
    observed[0], fail[0] = 2, False
    assert read_roots() == ({'value': 2}, {'value': 2})
    assert calls == Counter({('left', False): 2, ('right', False): 1, ('shared', False): 2})


def test_nested_consumers_cannot_change_one_anothers_payload(tmp_path, monkeypatch):
    def visit(name, retired):
        if name == 'root':
            first = result('shared').payload()
            first['items'].append('not retained')
            return result('shared').payload()
        return {'items': ['original']}

    result, calls = install_reader(monkeypatch, tmp_path, visit)
    assert result('root').payload() == {'items': ['original']}
    assert calls['shared', False] == 1


def test_retired_read_cannot_satisfy_an_active_profile_read(tmp_path, monkeypatch):
    def visit(name, retired):
        if name == 'root':
            return {'historical': result('shared').retained_payload(), 'active': result('shared').payload()}
        if not retired:
            raise PublicationViolation('profile retired')
        return {'historical': True}

    result, calls = install_reader(monkeypatch, tmp_path, visit)
    with pytest.raises(PublicationViolation, match='profile retired'):
        result('root').payload()
    assert calls['shared', True] == calls['shared', False] == 1


def test_cycle_is_rejected_and_failed_traversal_is_discarded(tmp_path, monkeypatch):
    cyclic = [True]

    def visit(name, retired):
        if cyclic[0]:
            return result('other' if name == 'root' else 'root').payload()
        return {'recovered': True}

    result, calls = install_reader(monkeypatch, tmp_path, visit)
    with pytest.raises(PublicationViolation, match='cycle'):
        result('root').payload()
    cyclic[0] = False
    assert result('root').payload() == {'recovered': True}
    assert calls['root', False] == 2


@pytest.mark.parametrize('limit', ['_PUBLICATION_READ_ENTRIES', '_PUBLICATION_READ_BYTES'])
def test_budget_exhaustion_keeps_physical_checks_instead_of_assuming_success(tmp_path, monkeypatch, limit):
    monkeypatch.setattr(storage, limit, 0)

    def visit(name, retired):
        if name == 'root':
            result('shared').payload()
            return result('shared').payload()
        return {'read': True}

    result, calls = install_reader(monkeypatch, tmp_path, visit)
    assert result('root').payload() == {'read': True}
    assert calls['shared', False] == 2


def test_copied_context_cannot_reuse_a_finished_reads_results(tmp_path, monkeypatch):
    contexts = []

    def visit(name, retired):
        if name == 'root':
            value = result('shared').payload()
            contexts.append(copy_context())
            return value
        return {'read': True}

    result, calls = install_reader(monkeypatch, tmp_path, visit)
    result('root').payload()
    contexts[0].run(result('shared').payload)
    assert calls['shared', False] == 2


def test_another_thread_cannot_reuse_an_open_reads_results(tmp_path, monkeypatch):
    errors = []

    def visit(name, retired):
        if name == 'root':
            result('shared').payload()
            context = copy_context()

            def read():
                try:
                    context.run(result('shared').payload)
                except BaseException as exc:
                    errors.append(exc)

            worker = Thread(target=read)
            worker.start()
            worker.join(timeout=10)
            assert not worker.is_alive() and not errors
        return {'read': True}

    result, calls = install_reader(monkeypatch, tmp_path, visit)
    result('root').payload()
    assert calls['shared', False] == 2


def test_publication_fragment_returns_the_verified_identity_without_reopening(tmp_path, monkeypatch):
    """Reader substitutes prove reference transport, not publication authority."""
    payload = {'schema_version': storage.PUBLICATION_RESULT_V3, 'value': 'retained'}
    expected_ref = reference(payload, storage.PUBLICATION_RESULT_V3)
    builder = GraphBuilder('run/v1', 'contract-run', 'run')
    for role, kind in (('run', LineageNodeClass.RUN), ('decision', LineageNodeClass.RUN_DECISION),
                       ('run_result', LineageNodeClass.RUN_RESULT)):
        builder.record(role, kind, {'role': role}, 'contract-record/v1')
    builder.link_roles()
    expected_graph = builder.finish().to_dict()
    calls = []

    def read_payload(self):
        calls.append(('payload', self.root, self.transaction_id))
        return payload

    def read_members(directory, *, transaction_id):
        calls.append(('lineage', directory, transaction_id))
        return None, {'lineage.json': encode_canonical(expected_graph)}

    monkeypatch.setattr(storage.PublicationResult, 'payload', read_payload)
    monkeypatch.setattr(reconstruction, 'read_committed_snapshot_transaction', read_members)
    published_ref, graph = reconstruction._publication_fragment(tmp_path, 'retained-result')
    assert published_ref == expected_ref
    assert graph.to_dict() == expected_graph
    assert calls == [('payload', tmp_path, 'retained-result'),
                     ('lineage', tmp_path / 'committed', 'retained-result')]


@pytest.mark.parametrize('failed_reader', ['payload', 'lineage'])
def test_publication_fragment_preserves_reader_failures(tmp_path, monkeypatch, failed_reader):
    """Injected failures establish no physical evidence or publication authority."""
    error = PublicationViolation('retained reader failed')
    calls = []

    def read_payload(self):
        calls.append('payload')
        if failed_reader == 'payload':
            raise error
        return {'schema_version': storage.PUBLICATION_RESULT_V3, 'value': 'retained'}

    def read_members(directory, *, transaction_id):
        calls.append('lineage')
        raise error

    monkeypatch.setattr(storage.PublicationResult, 'payload', read_payload)
    monkeypatch.setattr(reconstruction, 'read_committed_snapshot_transaction', read_members)
    with pytest.raises(PublicationViolation) as caught:
        reconstruction._publication_fragment(tmp_path, 'retained-result')
    assert caught.value is error
    assert calls == (['payload'] if failed_reader == 'payload' else ['payload', 'lineage'])
