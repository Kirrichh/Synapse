"""Controlled mutations of the real structural owner, with restoration."""
import inspect
import textwrap

import pytest

from acceptance.stage4.stage14.test_graph_contract import (
    input_graph, test_every_mandatory_input_relation_is_required as check_required,
    test_orphan_edge_fails_before_traversal as check_endpoint, test_restart_and_permutation_preserve_graph_identity as check_ordering,
)
from synapse.experiments.gold.stage14 import graph as G


def _mutate(monkeypatch, owner, name, before, after):
    original = getattr(owner, name)
    source = textwrap.dedent(inspect.getsource(original))
    assert source.count(before) == 1
    namespace = dict(original.__globals__)
    exec(compile(source.replace(before, after), original.__code__.co_filename, "exec"), namespace)
    monkeypatch.setattr(owner, name, namespace[name])


def test_structural_mutations_change_acceptance_verdict(monkeypatch):
    # Each oracle passes on unmodified production first. Patches are limited to
    # this process and restored before the next control is checked.
    controls = (
        ("endpoint", check_endpoint),
        ("mandatory", lambda: check_required(input_graph().edges[0])),
        ("ordering", check_ordering),
    )
    for name, oracle in controls:
        oracle()
        with monkeypatch.context() as patch:
            if name == "endpoint":
                _mutate(patch, G.LineageGraph, "validate",
                        "if e.source not in nodes or e.target not in nodes:", "if False:")
            elif name == "mandatory":
                _mutate(patch, G.LineageGraph, "validate", "for a, k, b in _LINKS:", "for a, k, b in ():")
            else:
                _mutate(patch, G.LineageGraph, "payload",
                        'sorted((e.to_dict() for e in self.edges), key=lambda e: (e["source"], e["kind"], e["target"]))',
                        '[e.to_dict() for e in self.edges]')
            killed = False
            try:
                oracle()
            except BaseException as failure:
                if isinstance(failure, (KeyboardInterrupt, SystemExit)):
                    raise
                killed = True
            assert killed, name
        oracle()


def test_unproven_dependency_mutation_is_detected(tmp_path, monkeypatch):
    from synapse.experiments.gold.admission_journal import FileSnapshotFence
    from synapse.experiments.gold.persistence import store_transaction
    from synapse.experiments.gold.runner.records import RunRecordStore, RecordKind
    from synapse.experiments.gold.stage14 import reconstruction as R
    physical = input_graph()
    b = G.GraphBuilder("inputs/v1", "run-a", "1")
    b.merge("original", physical)
    b.roles.update(dict(physical.roles))
    # A well-formed additional claim is still not a physically proven edge.
    b.record("observation", G.LineageNodeClass.BINDING, {"observed_at": "2026-09-06T12:00:00Z"},
             "synapse.acceptance.observation/v1")
    b.link("observation", G.LineageEdgeKind.DERIVED_FROM, "replay_request")
    claimed = b.finish()
    fence = FileSnapshotFence(tmp_path / "coordinator")
    store = RunRecordStore(tmp_path, mutation_fence=fence)
    with store_transaction(fence) as ticket:
        store.put(kind=RecordKind.ATTEMPT_LINEAGE, key="1", canonical_payload=claimed.to_dict(), ticket=ticket)

    def oracle():
        refused = False
        try:
            R.require_stored_graph(store, RecordKind.ATTEMPT_LINEAGE, "1", physical)
        except G.LineageViolation as error:
            assert error.failure_code is G.LineageFailureCode.PHYSICAL_MISMATCH
            refused = True
        assert refused

    oracle()
    with monkeypatch.context() as patch:
        _mutate(patch, R, "require_stored_graph", "if graph.to_dict() != expected.to_dict():", "if False:")
        with pytest.raises(AssertionError):
            oracle()
    oracle()
