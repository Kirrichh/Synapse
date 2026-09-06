"""A self-consistent projection cannot delete real calls from its inventory."""

import json

from acceptance.stage4.stage15._run_case import completed_run
from acceptance.stage4.stage15._retained_sources import inventory
from synapse.experiments.gold.stage14.graph import LineageGraph
from synapse.experiments.gold.stage15.events import GoldEvent
from synapse.experiments.gold.stage15.run_observability import build_observation_graph, inspect_observability
from synapse.experiments.gold.stage15.telemetry import canonical, reference


def test_forged_complete_inventory_cannot_omit_a_physical_call(tmp_path, monkeypatch):
    with completed_run(tmp_path, monkeypatch) as (case, finished, requests):
        root = case.run_root / 'run-records'
        body = json.loads(next((root / 'observability-manifest').glob('*.json')).read_bytes())
        assert body['call_record_refs']
        body['call_record_refs'] = []
        forged_ref = reference(body, body['schema_version'])
        observations = [json.loads(next((root / 'observation').glob(ref['sha256'] + '.*.json')).read_bytes())
                        for ref in body['observation_refs']]
        graph = build_observation_graph(run_graph=LineageGraph.from_dict(
            json.loads(next((root / 'run-lineage').glob('*.json')).read_bytes())), observations=observations,
            calls=[], events=tuple(GoldEvent.from_dict(e) for e in body['event_inventory']), assessment_ref=forged_ref).to_dict()
        graph_ref = reference(graph, graph['schema_version'])
        # Both transport hashes and the claimed projection DAG are internally
        # valid. Only independent physical reconstruction exposes this omission.
        (root / 'observability-manifest' / f'{forged_ref.sha256}.{forged_ref.sha256}.json').write_bytes(canonical(body))
        (root / 'observability-lineage' / f'{forged_ref.sha256}.{graph_ref.sha256}.json').write_bytes(canonical(graph))
        before = inventory(tmp_path)
        inspected = inspect_observability(run_root=case.run_root, assessment_key=forged_ref.sha256)
        codes = {finding['code'] for finding in inspected['discrepancies']}
        assert 'assessment_inventory_differs_from_physical_sources' in codes
        assert 'observability_lineage_missing_or_changed' not in codes
        assert inventory(tmp_path) == before
        assert len(requests) == 1
