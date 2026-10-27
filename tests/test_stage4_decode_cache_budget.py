"""Bound the decoded graph as well as the canonical input bytes."""
import gc
import tracemalloc

import pytest
from synapse.experiments.gold import canonicalization as c


def test_structural_budget_evicts_old_entries():
    first, second = ["a"] * 40, ["b"] * 40
    probe = c._ImmutableDecodedArtifactCache(max_retained_bytes=100_000)
    probe.put(b'a', first)
    first_size = probe.info().retained_bytes
    probe.clear()
    probe.put(b'b', second)
    second_size = probe.info().retained_bytes
    budget = max(first_size, second_size)
    cache = c._ImmutableDecodedArtifactCache(max_entries=20, max_bytes=100_000,
        max_item_bytes=100_000, max_retained_bytes=budget)
    cache.put(b'a', first)
    assert c._thaw_decoded(cache.get(b'a')) == first
    cache.put(b'b', second)
    assert cache.get(b'a') is c._CACHE_MISS
    assert c._thaw_decoded(cache.get(b'b')) == second
    assert cache.info().evictions == 1
    assert cache.info().retained_bytes <= budget
    assert cache.info().byte_length < cache.max_bytes and cache.info().currsize < cache.max_entries


def test_structural_budget_rejects_an_oversized_graph():
    probe = c._ImmutableDecodedArtifactCache(max_retained_bytes=100_000)
    value = list(range(1000))
    probe.put(b'large', value)
    size = probe.info().retained_bytes
    assert size > 0
    cache = c._ImmutableDecodedArtifactCache(max_entries=20, max_bytes=100_000,
        max_item_bytes=100_000, max_retained_bytes=size - 1)
    cache.put(b'large', value)
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
