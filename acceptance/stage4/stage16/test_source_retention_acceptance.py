"""A saved graph cannot conceal the loss or substitution of real source bytes."""

import json
import pytest

from acceptance.stage4.stage16._source_inputs import prepare, learn
from synapse.experiments.gold.stage13.publication_store import PublicationResult
from synapse.experiments.gold.persistence import PersistenceViolation, read_committed_snapshot_transaction
from synapse.experiments.gold.source_verification import SOURCE_FILE_V1, inspect_source_verification
from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.stage10.context_codec import decode_canonical


def test_source_publication_requires_retained_bytes_and_reconstructs_symbol_identity(tmp_path):
    _, state, input_path = prepare(tmp_path)
    code, result = learn(state, input_path)
    assert code == 0, result
    tx = result['publication']['transaction_id']
    _, prepared = read_committed_snapshot_transaction(state / 'publications' / 'prepared', transaction_id=tx)
    request = decode_canonical(prepared['request.json'])
    facts = request['verification']['payload']
    retained = {HashBoundRef.from_dict(ref): prepared[ref['sha256']] for ref in request['evidence_refs']}
    altered = json.loads(json.dumps(facts))
    altered['bindings'][0]['source_span_hash'] = '0' * 64
    with pytest.raises(ValueError):
        inspect_source_verification(altered, evidence=retained)
    source = next(ref for ref in request['evidence_refs'] if ref['schema_id'] == SOURCE_FILE_V1)
    path = state / 'publications' / 'prepared' / tx / source['sha256']
    raw = path.read_bytes()
    for replacement in (None, b'substituted source'):
        if replacement is None:
            path.unlink()
        else:
            path.write_bytes(replacement)
        with pytest.raises((ValueError, OSError, PersistenceViolation)):
            PublicationResult(state / 'publications', tx).payload()
        path.write_bytes(raw)
    assert PublicationResult(state / 'publications', tx).payload() == result['publication']
