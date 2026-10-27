"""Bound the decoded graph as well as the canonical input bytes."""
import gc
import tracemalloc

import pytest
from synapse.experiments.gold import canonicalization as c


def test_structural_budget_evicts_and_rejects_large_graphs():
    cache = c._ImmutableDecodedArtifactCache(max_entries=20, max_bytes=100_000,
        max_item_bytes=100_000, max_retained_bytes=2500)
    for i in range(20):
        cache.put(str(i).encode(), [str(i)] * 40)
        assert cache.info().retained_bytes <= 2500
    assert cache.info().evictions > 0
    assert cache.info().currsize < 20
    cache.clear()
    cache.put(b'large', list(range(1000)))
    assert cache.get(b'large') is c._CACHE_MISS
    assert cache.info().retained_bytes == 0


def test_repeated_fill_evict_and_copy_isolation():
    cache = c._ImmutableDecodedArtifactCache(max_entries=4, max_bytes=10000,
        max_item_bytes=10000, max_retained_bytes=3000)
    for iteration in range(100):
        raw = str(iteration).encode()
        cache.put(raw, {'items': [iteration, {'value': 'x'}]})
        frozen = cache.get(raw)
        value = c._thaw_decoded(frozen)
        value['items'][1]['value'] = 'altered'
        assert c._thaw_decoded(cache.get(raw))['items'][1]['value'] == 'x'
        assert cache.info().retained_bytes <= 3000
    cache.clear()
    assert cache.info().retained_bytes == 0


def test_repeated_eviction_does_not_accumulate_traced_memory():
    if tracemalloc.is_tracing():
        pytest.skip('requires an isolated tracemalloc session')
    cache = c._ImmutableDecodedArtifactCache(max_entries=3, max_bytes=10000,
        max_item_bytes=10000, max_retained_bytes=5000)
    tracemalloc.start()
    try:
        for cycle in range(10):
            for i in range(100):
                cache.put(str(i).encode(), {'items': [str(i)] * 10})
            cache.clear()
            gc.collect()
            if cycle == 1:
                baseline = tracemalloc.get_traced_memory()[0]
        assert tracemalloc.get_traced_memory()[0] - baseline < 32 * 1024
    finally:
        tracemalloc.stop()
