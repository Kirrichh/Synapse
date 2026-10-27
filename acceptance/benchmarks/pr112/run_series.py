"""Alternate isolated real Synapse CLI scenarios; output raw, path-free evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import unicodedata

HERE = Path(__file__).resolve().parent


def digest_tree(root):
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode() + b'\0' + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def summary(rows):
    metrics = ('wall_seconds', 'cpu_seconds', 'max_rss_kib')
    return {key: {'median': statistics.median(item[key] for item in rows),
                  'min': min(item[key] for item in rows), 'max': max(item[key] for item in rows)} for key in metrics}


def require_no_bytecode(roots):
    for source in roots.values():
        if any(source.rglob('*.pyc')):
            raise RuntimeError('source tree contains bytecode: ' + str(source))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--native-addon', type=Path, required=True)
    parser.add_argument('--replay-artifact', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pairs', type=int, default=5)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--base-sha', required=True)
    parser.add_argument('--candidate-sha', required=True)
    args = parser.parse_args()
    if args.pairs < 1:
        parser.error('pairs must be positive')
    roots = {'base': args.base.resolve(), 'candidate': args.candidate.resolve()}
    require_no_bytecode(roots)
    def inventory(executable, source, addon=None):
        script = 'import json,platform,unicodedata,importlib.metadata; from synapse.experiments.gold import canonicalization as c; print(json.dumps({\"python\":platform.python_version(),\"unicode\":unicodedata.unidata_version,\"distributions\":sorted((d.metadata[\"Name\"],d.version) for d in importlib.metadata.distributions()),\"native\":c.native_runtime_identity() if hasattr(c,\"native_runtime_identity\") else {\"implementation\":\"python\"}}))'
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(source)] + ([str(addon)] if addon else [])), 'PYTHONDONTWRITEBYTECODE': '1'}
        result = subprocess.run([str(executable), '-B', '-c', script], cwd=source, env=env, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    inventories = {'base_python': inventory(args.python, roots['base']), 'candidate_python': inventory(args.python, roots['candidate']),
                   'candidate_native': inventory(args.python, roots['candidate'], args.native_addon.resolve())}
    if inventories['base_python']['native']['implementation'] != 'python' or inventories['candidate_python']['native']['implementation'] != 'python':
        raise RuntimeError('Python comparison unexpectedly enabled a native backend')
    if inventories['candidate_native']['native']['implementation'] != 'rust-pyo3':
        raise RuntimeError('native comparison did not load the extension')
    artifact = args.replay_artifact.resolve()
    artifact_hash = digest_tree(artifact)
    results = {'schema': 'synapse.pr112.cli-benchmark/v1', 'pairs': args.pairs,
               'base_sha': args.base_sha, 'candidate_sha': args.candidate_sha, 'inventories': inventories,
               'environment': {'platform': platform.system() + ' ' + platform.release(),
                    'machine': platform.machine(), 'driver_python': platform.python_version(),
                    'driver_unicode': unicodedata.unidata_version,
                    'network': 'deterministic local HTTP provider; no external provider',
                    'filesystem_cache': 'uncontrolled; not described as cold',
                    'source_bytecode': 'no .pyc files in either source tree; Python -B prevents writes',
                    'processes': 'each CLI call is a new process; first and repeated resume are separate calls',
                    'cpu': 'wait4 user plus system time for the CLI and children it waited for',
                    'rss': 'wait4 maximum RSS of an individual process in the waited tree, not simultaneous aggregate',
                    'provider_latency': 'server-side accepted request handling; not client round-trip'},
               'runs': [], 'comparisons': {}}
    expected_behavior = None
    expected_replay = None
    if args.resume and args.output.exists():
        previous = json.loads(args.output.read_text())
        for key in ('base_sha', 'candidate_sha', 'pairs', 'inventories', 'environment'):
            if previous[key] != results[key]:
                raise RuntimeError('resume metadata differs: ' + key)
        results = previous
        expected_behavior = results.get('behavior')
        expected_replay = results.get('replay_result')
    combinations = [('python', ('base', 'candidate')), ('native', ('candidate_python', 'candidate_native'))]
    for series, members in combinations:
        for pair in range(args.pairs):
            order = members if pair % 2 == 0 else tuple(reversed(members))
            for member in order:
                if any(r['series'] == series and r['pair'] == pair + 1 and r['member'] == member for r in results['runs']):
                    continue
                source = roots['base'] if member == 'base' else roots['candidate']
                executable = args.python
                pythonpath = os.pathsep.join([str(HERE), str(source)] + ([str(args.native_addon.resolve())] if member == 'candidate_native' else []))
                with tempfile.TemporaryDirectory(prefix='synapse-benchmark-') as temporary:
                    root = Path(temporary)
                    trace = root / 'trace.jsonl'
                    env = {**os.environ, 'PYTHONPATH': pythonpath, 'PYTHONDONTWRITEBYTECODE': '1',
                           'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PR112_BENCH_TRACE': str(trace),
                           'GIT_AUTHOR_DATE': '2020-01-01T00:00:00+00:00',
                           'GIT_COMMITTER_DATE': '2020-01-01T00:00:00+00:00'}
                    test = subprocess.run([str(executable), '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'benchmark_plugin',
                        '--basetemp', str(root / 'pytest'), str(HERE / 'cli_case.py')],
                        cwd=source, env=env, capture_output=True, text=True, timeout=7200)
                    if test.returncode:
                        raise RuntimeError(f'{series} {member} pair {pair + 1}: acceptance scenario failed: {test.stdout[-3000:]} {test.stderr[-1000:]}')
                    events = [json.loads(line) for line in trace.read_text().splitlines()]
                    behavior = [item for item in events if item['kind'] == 'behavior']
                    if len(behavior) != 3:
                        raise RuntimeError('acceptance scenario did not record all outcomes')
                    if expected_behavior is None:
                        expected_behavior = behavior
                    elif behavior != expected_behavior:
                        raise RuntimeError(f'{series} {member} pair {pair + 1}: observable behavior differs')
                    replay_metric = root / 'replay.json'
                    command = [str(executable), '-B', '-m', 'synapse', 'replay', '--mock', str(artifact)]
                    replay = subprocess.run([str(executable), str(HERE / 'timed_exec.py'), str(replay_metric), *command],
                                            cwd=source, env=env, capture_output=True, text=True, timeout=300)
                    if replay.returncode:
                        raise RuntimeError(f'{series} {member}: replay failed')
                    replay_value = json.loads(replay.stdout)
                    replay_value.pop('artifact_dir', None)
                    if replay_value.get('drift') != 0 or digest_tree(artifact) != artifact_hash:
                        raise RuntimeError('replay changed its artifact or failed determinism')
                    if expected_replay is None:
                        expected_replay = replay_value
                    elif replay_value != expected_replay:
                        raise RuntimeError(f'{series} {member}: replay behavior differs')
                    metric = json.loads(replay_metric.read_text())
                    metric.update(kind='cli', scenario='replay-mock')
                    events.append(metric)
                    cli = [x for x in events if x['kind'] == 'cli']
                    expected = {'source-publication', 'source-recall', 'run-initial', 'approve-initial',
                                'run-reuse', 'approve-reuse', 'resume-first', 'resume-repeat', 'replay-mock'}
                    if {x['scenario'] for x in cli} != expected or len(cli) != len(expected):
                        raise RuntimeError(f'{series} {member}: missing or repeated measured commands')
                    for event in cli:
                        event['cpu_seconds'] = event['cpu_user_seconds'] + event['cpu_system_seconds']
                    results['runs'].append({'series': series, 'pair': pair + 1, 'member': member,
                                            'cli': cli, 'behavior': behavior,
                                            'provider_server_seconds': [x['seconds'] for x in events if x['kind'] == 'provider_server']})
                    results['behavior'] = expected_behavior
                    results['replay_result'] = expected_replay
                    args.output.write_text(json.dumps(results, indent=2) + '\n')
                    print(series, 'pair', pair + 1, member, 'completed', flush=True)
    for series, members in combinations:
        comparisons = {}
        for scenario in sorted(expected):
            left = [next(x for x in r['cli'] if x['scenario'] == scenario) for r in results['runs'] if r['series'] == series and r['member'] == members[0]]
            right = [next(x for x in r['cli'] if x['scenario'] == scenario) for r in results['runs'] if r['series'] == series and r['member'] == members[1]]
            left_summary, right_summary = summary(left), summary(right)
            baseline = left_summary['wall_seconds']['median']; candidate = right_summary['wall_seconds']['median']
            comparisons[scenario] = {'base': left_summary, 'candidate': right_summary,
                'wall_difference_seconds': candidate-baseline, 'wall_difference_percent': 100*(candidate/baseline-1)}
        results['comparisons'][series] = comparisons
    results['behavior'] = expected_behavior
    results['replay_result'] = expected_replay
    args.output.write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    main()
