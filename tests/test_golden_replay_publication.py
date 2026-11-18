"""Acceptance for publication of Replay recordings and observed cascade identity."""

import concurrent.futures
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from synapse import golden_replay as golden

ONE = 'print("first")\n'
TWO = 'print("replacement")\n'


def hashes(path):
    return {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in path.iterdir() if f.is_file()}


def checkout_environment():
    root = str(Path(__file__).resolve().parents[1])
    inherited = os.environ.get("PYTHONPATH")
    return {**os.environ, "PYTHONPATH": os.pathsep.join([root, inherited]) if inherited else root}


def cli(*args, cwd):
    return subprocess.run(
        [sys.executable, "-B", "-m", "synapse", *map(str, args)],
        cwd=cwd,
        env=checkout_environment(),
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_failed_new_program_keeps_previous_bytes_and_replays(tmp_path):
    dest = tmp_path / "record"
    source = tmp_path / "case.syn"
    source.write_text(ONE)
    assert cli("run", source, "--record", "--output", dest, cwd=tmp_path).returncode == 0
    before = hashes(dest)
    assert cli("replay", "--mock", dest, cwd=tmp_path).returncode == 0
    source.write_text('assert false, "sample failure"\n')
    assert cli("run", source, "--record", "--output", dest, cwd=tmp_path).returncode != 0
    assert hashes(dest) == before
    assert cli("replay", "--mock", dest, cwd=tmp_path).returncode == 0
    source.write_text(TWO)
    assert cli("run", source, "--record", "--output", dest, cwd=tmp_path).returncode == 0
    assert (dest / "source.syn").read_text() == TWO
    assert golden.replay_mock_artifact(dest).drift == 0


def test_partial_serialization_cannot_replace_a_good_record(tmp_path, monkeypatch):
    out = tmp_path / "record"
    golden.record_source(ONE, out)
    previous = hashes(out)
    real_write = golden._write_json

    def write(path, value):
        if path.name == "history.json":
            raise OSError("simulated storage error")
        return real_write(path, value)

    monkeypatch.setattr(golden, "_write_json", write)
    with pytest.raises(OSError):
        golden.record_source(TWO, out)
    assert hashes(out) == previous and golden.replay_mock_artifact(out).drift == 0


def test_process_loss_between_directory_renames_recovers_previous_record(tmp_path):
    out = tmp_path / "record"
    golden.record_source(ONE, out)
    before = hashes(out)
    child = """
import os, sys
from pathlib import Path
from synapse.golden_replay import record_source
from synapse import recording_publication
out = Path(sys.argv[1])
rename = os.replace
def interrupted(src, dst):
    if Path(dst) == out and Path(src).name == "recording":
        os._exit(87)
    return rename(src, dst)
recording_publication.os.replace = interrupted
record_source('print("incomplete")', out)
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", child, str(out)], cwd=tmp_path, env=checkout_environment(), timeout=20
    )
    assert result.returncode == 87
    assert cli("replay", "--mock", out, cwd=tmp_path).returncode == 0
    assert golden.replay_mock_artifact(out).drift == 0
    assert hashes(out) == before
    assert all(b"incomplete" not in path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())


def test_concurrent_recorders_and_readers_see_complete_generations(tmp_path):
    out = tmp_path / "record"
    golden.record_source(ONE, out)

    def act(n):
        if n % 3:
            result = cli("replay", "--mock", out, cwd=tmp_path)
            assert result.returncode == 0, result.stderr
        else:
            golden.record_source(f"let generation = {n}\nprint(generation)\n", out)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(act, range(18)))
    assert golden.replay_mock_artifact(out).drift == 0


def test_integrate_failure_keeps_previous_record(tmp_path):
    out = tmp_path / "record"
    golden.record_integrate_artifact(ONE, out)
    previous = hashes(out)
    with pytest.raises(Exception, match="unplanned abort"):
        golden.record_integrate_artifact('assert false, "unplanned abort"', out)
    assert hashes(out) == previous
    assert golden.replay_integrate_artifact(out).drift == 0


def test_reading_a_legacy_record_retries_if_its_first_replacement_starts(tmp_path, monkeypatch):
    import shutil

    writer = tmp_path / "writer"
    golden.record_source(ONE, writer)
    legacy = tmp_path / "legacy"
    shutil.copytree(writer, legacy)
    assert golden.replay_mock_artifact(legacy).drift == 0
    assert not (tmp_path / ".synapse-record-legacy").exists()
    read = golden._read_artifact_json
    replacements = []

    def interleave(root, relative):
        value = read(root, relative)
        if root == legacy and relative == "manifest.json" and not replacements:
            replacements.append(golden.record_source(TWO, legacy))
        return value

    monkeypatch.setattr(golden, "_read_artifact_json", interleave)
    recovered = golden.replay_mock_artifact(legacy)
    assert len(replacements) == 1 and recovered.drift == 0
    assert recovered.program_hash == golden.program_hash_for_source(TWO)


@pytest.mark.parametrize("known_recording", [False, True])
def test_replacement_preserves_foreign_files(tmp_path, known_recording):
    output = tmp_path / "record"
    if known_recording:
        golden.record_source(ONE, output)
        (output / "notes.txt").write_text("operator notes")
    else:
        output.mkdir()
        (output / "manifest.json").write_text('{"name":"another application"}')
    before = hashes(output)
    with pytest.raises(ValueError, match="refusing to replace"):
        golden.record_source(TWO, output)
    assert hashes(output) == before
