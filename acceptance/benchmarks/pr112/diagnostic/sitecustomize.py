"""Optional diagnostic run: local provider time to headers and cache access counts."""
import atexit
import json
import os
import sys
import time
import urllib.parse
import urllib.request

output = os.environ.get('PR112_DIAGNOSTIC_TRACE')
if output:
    def emit(value):
        raw = (json.dumps(value) + '\n').encode()
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, raw)
        finally:
            os.close(descriptor)

    original_open = urllib.request.OpenerDirector.open

    def measured_open(self, request, *args, **kwargs):
        url = getattr(request, 'full_url', request)
        is_provider = isinstance(url, str) and urllib.parse.urlsplit(url).path == '/v1/chat/completions'
        started = time.perf_counter()
        try:
            return original_open(self, request, *args, **kwargs)
        finally:
            if is_provider:
                emit({'kind': 'provider_client_headers', 'seconds': time.perf_counter() - started})

    urllib.request.OpenerDirector.open = measured_open
    try:
        from synapse.experiments.gold import canonicalization
        cache = getattr(canonicalization, '_decoded_artifact_cache', None)
    except ImportError:
        cache = None
    if cache is not None:
        seen = set()
        original_get = cache.get

        def measured_get(raw):
            seen.add(raw)
            return original_get(raw)

        cache.get = measured_get

        def finish():
            info = cache.info()._asdict()
            if info['hits'] + info['misses']:
                emit({'kind': 'decode_cache', 'unique_raw': len(seen), **info})

        atexit.register(finish)
