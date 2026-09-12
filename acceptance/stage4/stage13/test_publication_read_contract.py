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
from synapse.experiments.gold.stage13.publication import PublicationViolation


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
