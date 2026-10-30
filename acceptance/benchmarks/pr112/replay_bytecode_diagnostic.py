"""Compare the same replay code with and without precompiled Python bytecode."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import tempfile

from run_series import digest_tree, require_no_bytecode

HERE = Path(__file__).resolve().parent


def source_digest(root):
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_file() and path.suffix != '.pyc':
            digest.update(str(path.relative_to(root)).encode() + b'\0' + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-only', type=Path, required=True)
    parser.add_argument('--precompiled', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--artifact', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--pairs', type=int, default=5)
    args = parser.parse_args()
    roots = {'source_only': args.source_only.resolve(), 'precompiled': args.precompiled.resolve()}
    require_no_bytecode({'source_only': roots['source_only']})
    if not any(roots['precompiled'].rglob('*.pyc')):
        parser.error('precompiled tree has no .pyc files')
    if source_digest(roots['source_only']) != source_digest(roots['precompiled']):
        parser.error('source content differs between diagnostic trees')
    artifact = args.artifact.resolve()
    digest = digest_tree(artifact)
    rows = []
    expected = None
    for pair in range(args.pairs):
        for name in (('source_only', 'precompiled') if pair % 2 == 0 else ('precompiled', 'source_only')):
            with tempfile.TemporaryDirectory(prefix='pr112-bytecode-diagnostic-') as tmp:
                metric = Path(tmp) / 'metric.json'
                env = {**os.environ, 'PYTHONPATH': str(roots[name]), 'PYTHONDONTWRITEBYTECODE': '1'}
                command = [str(args.python), str(HERE / 'timed_exec.py'), str(metric), str(args.python),
                           '-B', '-m', 'synapse', 'replay', '--mock', str(artifact)]
                done = subprocess.run(command, cwd=roots[name], env=env, capture_output=True, text=True, check=True)
                output = json.loads(done.stdout)
                output.pop('artifact_dir', None)
                if expected is None:
                    expected = output
                if expected != output or output['drift'] != 0 or digest_tree(artifact) != digest:
                    raise RuntimeError('replay outcome or artifact differs')
                row = json.loads(metric.read_text())
                row.update(pair=pair + 1, member=name)
                rows.append(row)
    summary = {}
    for name in roots:
        samples = [row for row in rows if row['member'] == name]
        summary[name] = {metric: {'median': statistics.median(row[metric] for row in samples),
                                   'min': min(row[metric] for row in samples),
                                   'max': max(row[metric] for row in samples)}
                         for metric in ('wall_seconds', 'cpu_user_seconds', 'cpu_system_seconds', 'max_rss_kib')}
    args.output.write_text(json.dumps({'commit': args.commit, 'python': platform.python_version(),
        'pairs': args.pairs, 'method': 'Same source and inputs, no .pyc versus compileall; Python -B; filesystem cache uncontrolled.',
        'source_digest': source_digest(roots['source_only']), 'artifact_digest': digest,
        'rows': rows, 'summary': summary}, indent=2) + '\n')


if __name__ == '__main__':
    main()
