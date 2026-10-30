"""Compare base and candidate Stage 4 decoding in alternating fresh processes.

Run with --base and --candidate pointing to source trees, using the same Python
and installed dependencies. The workload is local and does not invoke a provider.
The optional native backend is disabled for a common Python-only comparison.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import time
import unicodedata


def worker(mode):
    from synapse.experiments.gold import canonicalization as canonical
    if hasattr(canonical, '_native'):
        canonical._native = None
    data = {'items': [{'id': i, 'name': 'test-%04d' % i, 'values': [i, i + 1, True, None]}
                      for i in range(180)]}
    raw = json.dumps(data, separators=(',', ':'), sort_keys=True).encode()
    items = ([raw] * 180 if mode == 'warm' else
             [json.dumps({**data, 'nonce': i}, separators=(',', ':'), sort_keys=True).encode()
              for i in range(180)])
    started, cpu = time.perf_counter(), time.process_time()
    for item in items:
        canonical.decode_stage4_canonical_bytes(item, profile_id=canonical.STAGE4_CANONICAL_PROFILE_V1,
                                                codec_id=canonical.STABLE_CANONICAL_CODEC_ID)
    info = canonical._decoded_artifact_cache.info()._asdict() if hasattr(canonical, '_decoded_artifact_cache') else None
    return {'wall_seconds': time.perf_counter() - started, 'cpu_seconds': time.process_time() - cpu,
            'max_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, 'cache': info}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--repeats', type=int, default=6)
    parser.add_argument('--worker', choices=('cold', 'warm'))
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker)))
        return
    if args.base is None or args.candidate is None or args.repeats < 1:
        parser.error('--base, --candidate and positive --repeats are required')
    roots = {'base': args.base.resolve(), 'candidate': args.candidate.resolve()}
    samples = {}
    for mode in ('cold', 'warm'):
        for index in range(args.repeats):
            order = ('base', 'candidate') if index % 2 == 0 else ('candidate', 'base')
            for name in order:
                env = {**os.environ, 'PYTHONPATH': str(roots[name])}
                started = time.perf_counter()
                before_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
                run = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--worker', mode],
                                     cwd=roots[name], env=env, check=True, capture_output=True, text=True)
                after_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
                sample = json.loads(run.stdout)
                sample['process_wall_seconds'] = time.perf_counter() - started
                sample['process_cpu_seconds'] = (after_cpu.ru_utime + after_cpu.ru_stime
                                                 - before_cpu.ru_utime - before_cpu.ru_stime)
                samples.setdefault(mode + '_' + name, []).append(sample)
    result = {key: {'runs': rows,
                    'median_wall_seconds': statistics.median(row['wall_seconds'] for row in rows),
                    'median_cpu_seconds': statistics.median(row['cpu_seconds'] for row in rows)}
              for key, rows in samples.items()}
    print(json.dumps({'repeats': args.repeats, 'python_version': platform.python_version(),
                      'unicode_version': unicodedata.unidata_version, 'backend': 'python',
                      'provider_network': 'not invoked', 'results': result}, indent=2))


if __name__ == '__main__':
    main()
