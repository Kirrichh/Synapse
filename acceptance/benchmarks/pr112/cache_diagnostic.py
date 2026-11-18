import json
import statistics
import time
from synapse.experiments.gold import canonicalization as c

class NoCache:
    def get(self, key):
        return c._CACHE_MISS
    def put(self, key, value):
        pass

params = {'profile_id': c.STAGE4_CANONICAL_PROFILE_V1, 'codec_id': c.STABLE_CANONICAL_CODEC_ID}
def payload(i):
    return {'index': i, 'entries': [{'index': j, 'text': 'value-%d-%d' % (i, j)} for j in range(20)]}
raws = [c.canonicalize_stage4_payload(payload(i), **params) for i in range(400)]
result = {}
for kind, inputs in (('repeated', [raws[0]] * 400), ('unique', raws)):
    rows = {'enabled': [], 'disabled': []}
    for pair in range(5):
        for mode in (('enabled', 'disabled') if pair % 2 == 0 else ('disabled', 'enabled')):
            cache = c._ImmutableDecodedArtifactCache() if mode == 'enabled' else NoCache()
            c._decoded_artifact_cache = cache
            if kind == 'repeated' and mode == 'enabled':
                c.decode_stage4_canonical_bytes(inputs[0], **params)
            start = time.perf_counter()
            for encoded in inputs:
                result_value = c.decode_stage4_canonical_bytes(encoded, **params)
            elapsed = time.perf_counter() - start
            assert result_value == payload(0 if kind == 'repeated' else 399)
            row = {'wall_seconds': elapsed}
            if mode == 'enabled':
                row['cache'] = cache.info()._asdict()
            rows[mode].append(row)
    result[kind] = rows
print(json.dumps(result, indent=2))
