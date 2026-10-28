"""Alternate CLI replay and verified initial-run measurements without source bytecode."""
import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import unicodedata

from run_series import digest_tree, require_no_bytecode, verify_measured_sources

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--artifact', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base-sha', required=True)
    parser.add_argument('--candidate-sha', required=True)
    parser.add_argument('--pairs', type=int, default=5)
    args = parser.parse_args()
    if args.pairs < 1:
        parser.error('pairs must be positive')
    roots = {'base': args.base.resolve(), 'candidate': args.candidate.resolve()}
    require_no_bytecode(roots)
    identities, harness_hash, sources_unchanged = verify_measured_sources(roots, args.base_sha, args.candidate_sha)
    artifact = args.artifact.resolve()
    artifact_digest = digest_tree(artifact)
    inventory_script = ('import importlib.metadata,json,platform,unicodedata; '
        'from synapse.experiments.gold import canonicalization as c; '
        'print(json.dumps({"python":platform.python_version(),"unicode":unicodedata.unidata_version,'
        '"distributions":sorted((d.metadata["Name"],d.version) for d in importlib.metadata.distributions()),'
        '"backend": c.native_runtime_identity() if hasattr(c,"native_runtime_identity") else {"implementation":"python"}}))')
    inventories = {}
    for name, source in roots.items():
        env = {**os.environ, 'PYTHONPATH': str(source), 'PYTHONDONTWRITEBYTECODE': '1'}
        inventories[name] = json.loads(subprocess.run([str(args.python), '-B', '-c', inventory_script],
                            cwd=source, env=env, capture_output=True, text=True, check=True).stdout)
        if inventories[name]['backend']['implementation'] != 'python':
            raise RuntimeError('Python backend required in both trees')
    if inventories['base'] != inventories['candidate']:
        raise RuntimeError('interpreter, distributions or backend differ')
    results = {'base_sha': args.base_sha, 'candidate_sha': args.candidate_sha, 'pairs': args.pairs,
               'inventory': inventories, 'platform': platform.platform(), 'machine': platform.machine(),
               'bytecode': 'both source archives contain no .pyc; CLI Python uses -B',
               'filesystem_cache': 'uncontrolled; not called cold',
               'cpu': 'wait4 user plus system for the CLI and children it waited for',
               'rss': 'wait4 maximum of an individual process, not aggregate process-tree peak',
               'artifact_sha256': artifact_digest, 'source_identities': identities,
               'harness_sha256': harness_hash, 'runs': []}
    expected_replay = None
    expected_behavior = None
    for pair in range(args.pairs):
        for name in (('base', 'candidate') if pair % 2 == 0 else ('candidate', 'base')):
            source = roots[name]
            with tempfile.TemporaryDirectory(prefix='pr112-bytecode-pair-') as temp:
                temporary = Path(temp)
                trace = temporary / 'trace.jsonl'
                environment = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(HERE), str(source))),
                               'PYTHONDONTWRITEBYTECODE': '1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
                               'PR112_BENCH_TRACE': str(trace), 'PR112_BENCH_INITIAL_ONLY': '1',
                               'GIT_AUTHOR_DATE': '2020-01-01T00:00:00+00:00',
                               'GIT_COMMITTER_DATE': '2020-01-01T00:00:00+00:00'}
                command = [str(args.python), '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                           '-p', 'benchmark_plugin', '--basetemp', str(temporary / 'pytest'), str(HERE / 'cli_case.py')]
                test = subprocess.run(command, cwd=source, env=environment, capture_output=True, text=True, timeout=3600)
                if test.returncode:
                    raise RuntimeError(f'{name} pair {pair+1}: {test.stdout[-3000:]} {test.stderr[-1000:]}')
                events = [json.loads(line) for line in trace.read_text().splitlines()]
                behavior = [row for row in events if row['kind'] == 'behavior']
                if len(behavior) != 2:
                    raise RuntimeError('initial scenario did not capture source and initial outcomes')
                if expected_behavior is None:
                    expected_behavior = behavior
                elif behavior != expected_behavior:
                    raise RuntimeError(f'{name} pair {pair+1}: behavior differs')
                metric = temporary / 'replay.json'
                replay = subprocess.run([str(args.python), str(HERE / 'timed_exec.py'), str(metric), str(args.python),
                                         '-B', '-m', 'synapse', 'replay', '--mock', str(artifact)],
                                        cwd=source, env=environment, capture_output=True, text=True, timeout=300, check=True)
                value = json.loads(replay.stdout)
                value.pop('artifact_dir', None)
                if value.get('drift') != 0 or digest_tree(artifact) != artifact_digest:
                    raise RuntimeError('replay failed or altered the artifact')
                if expected_replay is None:
                    expected_replay = value
                elif value != expected_replay:
                    raise RuntimeError(f'{name} pair {pair+1}: replay differs')
                sources_unchanged()
                replay_row = json.loads(metric.read_text())
                replay_row.update(kind='cli', scenario='replay-mock')
                events.append(replay_row)
                cli = [row for row in events if row['kind'] == 'cli']
                expected = {'source-publication', 'source-recall', 'run-initial', 'approve-initial', 'replay-mock'}
                if len(cli) != len(expected) or {row['scenario'] for row in cli} != expected:
                    raise RuntimeError('missing or duplicate CLI measurements')
                for row in cli:
                    row['cpu_seconds'] = row['cpu_user_seconds'] + row['cpu_system_seconds']
                results['runs'].append({'pair': pair+1, 'member': name, 'cli': cli,
                                        'provider_server_seconds': [row['seconds'] for row in events if row['kind'] == 'provider_server']})
                results['behavior'] = expected_behavior
                results['replay_result'] = expected_replay
                args.output.write_text(json.dumps(results, indent=2) + '\n')
                print('pair', pair+1, name, 'completed', flush=True)
    comparisons = {}
    for scenario in sorted(expected):
        groups = {}
        for name in roots:
            samples = [next(row for row in run['cli'] if row['scenario'] == scenario)
                       for run in results['runs'] if run['member'] == name]
            groups[name] = {key: {'median': statistics.median(row[key] for row in samples),
                                  'min': min(row[key] for row in samples), 'max': max(row[key] for row in samples)}
                            for key in ('wall_seconds', 'cpu_seconds', 'max_rss_kib')}
        before = groups['base']['wall_seconds']['median']
        after = groups['candidate']['wall_seconds']['median']
        comparisons[scenario] = {**groups, 'wall_delta_seconds': after-before, 'wall_delta_percent': 100*(after/before-1)}
    results['comparisons'] = comparisons
    args.output.write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    main()
