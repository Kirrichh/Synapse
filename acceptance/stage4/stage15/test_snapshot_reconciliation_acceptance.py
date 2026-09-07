"""Historical snapshot sources fail closed without rebuilding library state."""

import json
from pathlib import Path

from acceptance.stage4.stage15._run_case import completed_run
from acceptance.stage4.stage15._retained_sources import inventory, changed_source, replaced_record
from synapse.experiments.gold.stage15.snapshot_reconciliation import reconcile_snapshot
from synapse.experiments.gold.stage15.telemetry import reference


def test_physical_snapshot_status_matrix_is_read_only(tmp_path, monkeypatch):
    with completed_run(tmp_path, monkeypatch) as (case, finished, requests):
        catalog_path = next((case.run_root / 'run-records' / 'lineage-sources').glob('*.json'))
        catalog = json.loads(catalog_path.read_bytes())
        catalog_ref = reference(catalog, catalog['schema_version'])
        attempt_index = int(catalog_path.name.split('.')[0])
        def check(status, expected_ref=catalog_ref):
            before = inventory(tmp_path)
            report = reconcile_snapshot(run_root=case.run_root, attempt_index=attempt_index, catalog_ref=expected_ref)
            assert report.status == status, report.to_dict()
            assert inventory(tmp_path) == before
        check('COMPLETE')
        with changed_source(catalog_path, None):
            check('INCOMPLETE')
        def mixed(value):
            value['snapshot_ref'] = value['boundary_ref']
        with replaced_record(catalog_path, mixed) as value:
            check('MIX_AND_MATCH', reference(value, value['schema_version']))
        history = Path(catalog['snapshot_sources']['lifecycle']['location']['path'])
        raw = history.read_bytes()
        assert raw
        with changed_source(history, b''):
            check('ROLLBACK')
        with changed_source(history, bytes([raw[0] ^ 1]) + raw[1:]):
            check('CORRUPTED')
        check('COMPLETE')
        assert len(requests) == 1
