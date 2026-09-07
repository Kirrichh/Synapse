"""External disk mutation harness; preserves sources after each adversarial case."""

from contextlib import contextmanager
import hashlib
import json

from synapse.experiments.gold.stage15.telemetry import canonical


def inventory(root):
    return {str(p.relative_to(root)): (p.lstat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


@contextmanager
def changed_source(path, replacement):
    original = path.read_bytes()
    if replacement is None:
        path.unlink()
    else:
        path.write_bytes(replacement)
    try:
        yield
    finally:
        path.write_bytes(original)


@contextmanager
def replaced_record(path, transform):
    original = path.read_bytes()
    value = json.loads(original)
    transform(value)
    raw = canonical(value)
    key = path.name.rsplit('.', 2)[0]
    replacement = path.with_name(key + '.' + hashlib.sha256(raw).hexdigest() + '.json')
    path.unlink()
    replacement.write_bytes(raw)
    try:
        yield value
    finally:
        replacement.unlink()
        path.write_bytes(original)
