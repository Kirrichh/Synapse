"""Protect baseline inputs and import provenance from ambiguous or injected data."""
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

from acceptance.stage4.canonical_baseline_cases import fingerprint, samples

ROOT = Path(__file__).resolve().parents[2]
BASE = '1e226e52a3400d4fb0336420569d8bab43c8f783'
GENERATOR = ROOT / 'acceptance/stage4/generate_canonical_baseline_vectors.py'


@pytest.fixture(scope='module')
def clean_archive(tmp_path_factory):
    root = tmp_path_factory.mktemp('baseline')
    archive = subprocess.check_output(['git', 'archive', BASE], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(root, filter='data')
    return root


def generate(base, output):
    return subprocess.run([sys.executable, '-B', str(GENERATOR), '--base-tree', str(base), '--output', str(output)],
                          cwd=ROOT, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
                          capture_output=True, text=True, timeout=60)


def test_clean_baseline_generates_same_results(clean_archive, tmp_path):
    import json
    output = tmp_path / 'vectors.json'
    result = generate(clean_archive, output)
    assert result.returncode == 0, result.stderr
    current = json.loads((ROOT / 'tests/data/stage4_baseline_1e226e52.json').read_text())
    assert json.loads(output.read_text()) == current


@pytest.mark.parametrize('intrusion', ['package', 'extension', 'bytecode', 'changed_source'])
def test_modified_import_tree_is_refused_before_output(clean_archive, tmp_path, intrusion):
    base = tmp_path / 'base'
    shutil.copytree(clean_archive, base)
    gold = base / 'synapse/experiments/gold'
    if intrusion == 'package':
        shadow = gold / 'canonicalization'
        shadow.mkdir()
        shutil.copy2(gold / 'canonicalization.py', gold / 'canonicalization_base_copy.py')
        (shadow / '__init__.py').write_text(
            'from ..canonicalization_base_copy import *\n'
            '_original = canonicalize_stage4_payload\n'
            'def canonicalize_stage4_payload(value, **options):\n'
            '    return b"wrong" if value is None else _original(value, **options)\n'
        )
    elif intrusion == 'extension':
        (gold / 'canonicalization.so').write_bytes(b'fake extension')
    elif intrusion == 'bytecode':
        (gold / 'canonicalization.pyc').write_bytes(b'fake bytecode')
    else:
        with (gold / 'canonicalization.py').open('a') as stream:
            stream.write('\n# altered\n')
    output = tmp_path / 'vectors.json'
    result = generate(base, output)
    assert result.returncode != 0
    assert not output.exists()


class IntegerSubclass(int):
    pass


class SlottedValue:
    __slots__ = ('value',)

    def __init__(self, value):
        self.value = value


@pytest.mark.parametrize('value', [(1,), (2,), {1}, {2}, IntegerSubclass(1), IntegerSubclass(2),
                                   SlottedValue(1), SlottedValue(2)])
def test_unsupported_values_cannot_share_an_input_fingerprint(value):
    with pytest.raises(TypeError):
        fingerprint(value)


def test_corpus_keeps_the_unsupported_object_and_its_input_fingerprint():
    value = samples()[-1]
    assert type(value).__name__ == 'x' * 300
    assert isinstance(fingerprint(value), str)


def test_fingerprints_distinguish_aliases_unicode_and_signed_zero():
    shared = []
    assert fingerprint([shared, shared]) != fingerprint([[], []])
    assert fingerprint('é') != fingerprint('e\u0301')
    assert fingerprint(-0.0) != fingerprint(0.0)


def test_unrelated_object_cannot_impersonate_the_retained_corpus_type():
    from acceptance.stage4 import canonical_baseline_cases as cases
    impostor = type('x' * 300, (), {'__module__': cases.__name__})()
    with pytest.raises(TypeError):
        fingerprint(impostor)
