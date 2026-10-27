"""Regenerate Stage4 vectors using only the public API of the pinned base tree."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

BASE_COMMIT = '1e226e52a3400d4fb0336420569d8bab43c8f783'
SOURCE_HASHES = {
    'synapse/experiments/gold/canonicalization.py': 'a8123771e00e1f2e6c83f34420aab1d4bcd68a81b9facc40f2ff2008abb55a7b',
    'synapse/canonical_values.py': '067927ff6457820aa0b658a50c797c280703b6b43e285e9ac2e31f2ba4cb783a',
}
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from acceptance.stage4.canonical_baseline_cases import DECODE_SAMPLES, fingerprint, samples


def observed(call, value):
    try:
        result = call(value)
        return {'kind': 'ok', 'value': result.hex() if isinstance(result, bytes) else result}
    except Exception as exc:
        code = getattr(exc, 'failure_code', None)
        return {'kind': 'error', 'class': type(exc).__module__ + '.' + type(exc).__qualname__,
                'failure_code': getattr(code, 'value', None), 'detail': getattr(exc, 'detail', None),
                'str': str(exc), 'args': list(exc.args)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-tree', type=Path, required=True,
                        help='Unmodified archive of commit ' + BASE_COMMIT)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        parser.error('the recorded baseline requires CPython 3.12')
    base = args.base_tree.resolve()
    entries = subprocess.check_output(['git', 'ls-tree', '-rz', '--full-tree', BASE_COMMIT, '--', 'synapse'], cwd=ROOT)
    for entry in entries.split(b'\0'):
        if not entry:
            continue
        metadata, name = entry.split(b'\t', 1)
        mode, kind, object_id = metadata.split()
        if kind != b'blob' or mode == b'120000':
            parser.error('unexpected baseline tree entry')
        path = name.decode('utf-8')
        content = (base / path).read_bytes()
        actual = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest().encode()
        if actual != object_id:
            parser.error('baseline tree differs from pinned commit: ' + path)
    if any(base.rglob('*.pyc')):
        parser.error('baseline archive contains bytecode not present in the pinned tree')
    for path, expected in SOURCE_HASHES.items():
        if hashlib.sha256((base / path).read_bytes()).hexdigest() != expected:
            parser.error('baseline source hash differs: ' + path)
    sys.path.insert(0, str(base))
    from synapse.experiments.gold import canonicalization as api
    if not Path(api.__file__).resolve().is_relative_to(base):
        parser.error('baseline API did not load from the supplied tree')
    options = {'profile_id': api.STAGE4_CANONICAL_PROFILE_V1, 'codec_id': api.STABLE_CANONICAL_CODEC_ID}
    values = samples()
    result = {'baseline_commit': BASE_COMMIT,
              'encode': [observed(lambda value: api.canonicalize_stage4_payload(value, **options), value)
                         for value in values],
              'decode': [observed(lambda value: api.decode_stage4_canonical_bytes(value, **options), value)
                         for value in DECODE_SAMPLES],
              'provenance': {'interpreter': 'CPython 3.12', 'stage4_source_sha256': SOURCE_HASHES['synapse/experiments/gold/canonicalization.py'],
                             'stable_source_sha256': SOURCE_HASHES['synapse/canonical_values.py'],
                             'method': 'Recorded from the unmodified public API at the named commit; no candidate helpers are used.'},
              'encode_inputs': [fingerprint(value) for value in values],
              'decode_inputs': [fingerprint(value) for value in DECODE_SAMPLES]}
    if args.output.exists():
        previous = json.loads(args.output.read_text())
        if any(previous.get(key) != result[key] for key in ('baseline_commit', 'encode', 'decode', 'provenance')):
            parser.error('existing public baseline outcomes differ; refusing to replace them')
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=True) + '\n')


if __name__ == '__main__':
    main()
