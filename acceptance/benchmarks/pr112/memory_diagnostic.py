import gc
import json
import resource
import tracemalloc
from synapse.experiments.gold import canonicalization as c

def data(i):
    return {'id': i, 'items': [{'index': j, 'text': 'value-%d-%d' % (i, j)} for j in range(100)]}
def raw(v):
    return c.canonicalize_stage4_payload(v, profile_id=c.STAGE4_CANONICAL_PROFILE_V1, codec_id=c.STABLE_CANONICAL_CODEC_ID)
value = data(0)
probe = c._ImmutableDecodedArtifactCache(max_entries=1000, max_bytes=10_000_000, max_item_bytes=1_000_000, max_retained_bytes=10_000_000)
probe.put(raw(value), value)
budget = probe.info().retained_bytes * 3
probe.clear()
cache = c._ImmutableDecodedArtifactCache(max_entries=1000, max_bytes=10_000_000, max_item_bytes=1_000_000, max_retained_bytes=budget)
tracemalloc.start()
for i in range(100):
    value = data(i)
    key = raw(value)
    cache.put(key, value)
info = cache.info()._asdict()
assert info['evictions'] > 0 and info['retained_bytes'] <= budget
assert info['byte_length'] < cache.max_bytes and info['currsize'] < cache.max_entries
frozen = cache.get(key)
gc.collect()
before = tracemalloc.get_traced_memory()[0]
tracemalloc.reset_peak()
copy = c._thaw_decoded(frozen)
after, peak = tracemalloc.get_traced_memory()
copy['items'][0]['text'] = 'modified'
assert c._thaw_decoded(cache.get(key))['items'][0]['text'] != 'modified'
del copy, frozen, value, key
cache.clear()
gc.collect()
cycle = []
for n in range(10):
    for i in range(30):
        value = data(i)
        cache.put(raw(value), value)
    del value
    cache.clear()
    gc.collect()
    cycle.append(tracemalloc.get_traced_memory()[0])
print(json.dumps({'structural_budget': budget, 'fill': info, 'thaw_live_bytes': after-before, 'thaw_peak_increment_bytes': peak-before,
                  'clear_traced_bytes_cycles': cycle, 'maxrss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}, indent=2))
