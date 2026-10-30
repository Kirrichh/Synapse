"""Verify an extracted Git tree without executing code from it."""
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess


def verify_source_tree(root, repository, commit):
    root = Path(root).resolve()
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('source commit must be a full immutable SHA-1')
    try:
        entries = subprocess.check_output(['git', 'ls-tree', '-rz', '--full-tree', commit], cwd=repository)
        tree_id = subprocess.check_output(['git', 'rev-parse', '--verify', commit + '^{tree}'], cwd=repository, text=True).strip()
    except subprocess.CalledProcessError as exc:
        raise ValueError('commit tree is unavailable; fetch the pinned commit first') from exc
    expected = {}
    for entry in entries.split(b'\0'):
        if not entry:
            continue
        metadata, name = entry.split(b'\t', 1)
        mode, kind, object_id = metadata.split()
        if kind != b'blob' or mode not in (b'100644', b'100755'):
            raise ValueError('source tree has an unsupported Git object')
        expected[name.decode('utf-8')] = (mode, object_id)
    expected_dirs = {str(parent) for path in expected for parent in Path(path).parents if str(parent) != '.'}
    seen_files, seen_dirs = set(), set()
    file_hashes = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in dirs:
            path = parent / name
            relative = path.relative_to(root).as_posix()
            if not stat.S_ISDIR(path.lstat().st_mode) or relative not in expected_dirs:
                raise ValueError('source identity differs from commit: ' + relative)
            seen_dirs.add(relative)
        for name in files:
            path = parent / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or relative not in expected:
                raise ValueError('source identity differs from commit: ' + relative)
            mode, object_id = expected[relative]
            if bool(info.st_mode & 0o111) != (mode == b'100755'):
                raise ValueError('source identity differs from commit: ' + relative)
            content = path.read_bytes()
            actual = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest().encode()
            if actual != object_id:
                raise ValueError('source identity differs from commit: ' + relative)
            file_hashes[relative] = hashlib.sha256(content).digest()
            seen_files.add(relative)
    if seen_files != expected.keys() or seen_dirs != expected_dirs:
        raise ValueError('source identity differs from commit: missing paths')
    digest = hashlib.sha256()
    for name in sorted(file_hashes):
        digest.update(name.encode('utf-8') + b'\0' + expected[name][0] + b'\0' + file_hashes[name])
    return {'commit': commit, 'git_tree': tree_id, 'sha256': digest.hexdigest()}
