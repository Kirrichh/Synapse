"""All artifact verdicts come from mutations of a completed physical run."""

from acceptance.stage4.stage15._run_case import completed_run
from acceptance.stage4.stage15._retained_sources import inventory, changed_source, replaced_record
from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.stage15.artifact_reconciliation import reconcile_artifacts
import json


def test_physical_artifact_status_matrix_never_repairs_sources(tmp_path, monkeypatch):
    with completed_run(tmp_path, monkeypatch) as (case, finished, requests):
        root = case.run_root / 'run-records'
        assessment = json.loads(next((root / 'observability-manifest').glob('*.json')).read_bytes())
        manifest_ref = HashBoundRef.from_dict(assessment['run_manifest_ref'])
        graph = next((root / 'attempt-lineage').glob('*.json'))
        def check(status):
            before = inventory(tmp_path)
            report = reconcile_artifacts(run_root=case.run_root, manifest_ref=manifest_ref, publication_root=case.run_root / 'publication')
            assert report.status == status, report.to_dict()
            assert inventory(tmp_path) == before
        check('COMPLETE')
        with changed_source(graph, None):
            check('MISSING_BLOB')
        with changed_source(graph, graph.read_bytes() + b' '):
            check('HASH_MISMATCH')
        def orphan(value):
            value['edges'][0]['source'] = 'f' * 64
        with replaced_record(graph, orphan):
            check('ORPHAN_REF')
        def missing_relation(value):
            value['edges'] = [e for e in value['edges'] if e['kind'] != 'MEASURED_BY']
        with replaced_record(graph, missing_relation):
            check('LINEAGE_MISMATCH')
        check('COMPLETE')
        assert len(requests) == 1
