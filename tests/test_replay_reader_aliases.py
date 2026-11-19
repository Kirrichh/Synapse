from contextlib import contextmanager
from pathlib import Path

import pytest

from synapse import golden_replay as replay
from synapse import recording_publication as publication
from synapse.debugger_core import GoldenArtifactTraceAdapter


READERS = [
    (replay.record_source, replay.replay_mock_artifact),
    (replay.record_integrate_artifact, replay.replay_integrate_artifact),
    (replay.record_source, GoldenArtifactTraceAdapter),
]


@pytest.mark.parametrize("writer,reader", READERS, ids=["replay", "integrate", "debugger"])
@pytest.mark.parametrize("spelling", ["direct", "link"])
def test_stable_recording_uses_publisher_lock(tmp_path, monkeypatch, writer, reader, spelling):
    acquired = []
    file_lock = publication.FileLock

    @contextmanager
    def observe_lock(path):
        with file_lock(path):
            acquired.append(Path(path).resolve())
            yield

    monkeypatch.setattr(publication, "FileLock", observe_lock)
    dest = tmp_path / "record"
    manifest = writer('print("verified")', dest)
    assert len(acquired) == 1
    publisher_lock = acquired.pop()

    alias = tmp_path / "latest"
    alias.symlink_to(dest.name, target_is_directory=True)
    opened = reader(dest if spelling == "direct" else alias)
    assert opened.program_hash == manifest["metadata"]["program_hash"]
    assert acquired == [publisher_lock], "reader must acquire the publisher's lock"


@pytest.mark.parametrize("spelling", ["direct", "link"])
def test_first_replay_restores_saved_generation(tmp_path, spelling):
    import hashlib
    import json
    import subprocess
    import sys

    dest = tmp_path / "record"
    manifest = replay.record_source("print(uuid())", dest)
    alias = tmp_path / "latest"
    alias.symlink_to(dest.name, target_is_directory=True)

    def digests(folder):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}

    expected_bytes = digests(dest)
    # A saved generation is the recovery input. There is no concurrent writer.
    saved = tmp_path / ".synapse-record-record" / "previous"
    dest.rename(saved)
    first_open = subprocess.run(
        [sys.executable, "-m", "synapse", "replay", "--mock", str(dest if spelling == "direct" else alias)],
        capture_output=True, text=True, timeout=30,
    )
    assert first_open.returncode == 0, first_open.stderr
    result = json.loads(first_open.stdout)
    assert result["drift"] == 0
    assert result["final_history_hash"] == manifest["final"]["final_history_hash"]
    assert digests(dest) == expected_bytes
    assert alias.is_symlink() and not saved.exists()


@pytest.mark.parametrize("writer,reader", READERS, ids=["replay", "integrate", "debugger"])
def test_publication_and_alias_reader_share_one_generation(tmp_path, monkeypatch, writer, reader):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, local

    dest = tmp_path / "record"
    extra = "print(uuid())" if writer is replay.record_source else ""
    old = writer('print("first generation")\n' + extra, dest)
    alias = tmp_path / "latest"
    alias.symlink_to(dest.name, target_is_directory=True)

    writer_holds_lock = Event()
    reader_arrived = Event()
    local_state = local()
    real_lock = publication.FileLock
    real_read = publication.read_recording
    writer_locks = []

    @contextmanager
    def observed_lock(path):
        if local_state.role == "reader":
            reader_arrived.set()
        with real_lock(path):
            local_state.active_lock = Path(path).resolve()
            try:
                if local_state.role == "writer":
                    writer_locks.append(local_state.active_lock)
                    writer_holds_lock.set()
                    assert reader_arrived.wait(10), "reader did not reach the publication boundary"
                yield
            finally:
                local_state.active_lock = None

    def checked_read(output, load):
        def checked_load(root):
            reader_arrived.set()
            assert getattr(local_state, "active_lock", None) == writer_locks[0], (
                "reader must hold the publisher's lock before loading any files"
            )
            return load(root)
        return real_read(output, checked_load)

    monkeypatch.setattr(publication, "FileLock", observed_lock)
    monkeypatch.setattr(publication, "read_recording", checked_read)
    monkeypatch.setattr(replay, "read_recording", checked_read)

    def publish():
        local_state.role = "writer"
        return writer('print("second generation")\n' + extra, dest)

    def read():
        local_state.role = "reader"
        return reader(alias)

    with ThreadPoolExecutor(max_workers=2) as pool:
        publication_result = pool.submit(publish)
        assert writer_holds_lock.wait(10)
        reading = pool.submit(read)
        new = publication_result.result(timeout=20)
        opened = reading.result(timeout=20)

    assert old["metadata"]["program_hash"] != new["metadata"]["program_hash"]
    assert opened.program_hash == new["metadata"]["program_hash"]
    assert opened.final_history_hash == new["final"]["final_history_hash"]
    if hasattr(opened, "drift"):
        assert opened.drift == 0
        assert opened.state_sanity == new["final"]["state_sanity"]


def test_first_cli_alias_open_after_real_process_loss(tmp_path, monkeypatch):
    from tests import test_golden_replay_publication as stock

    original_cli = stock.cli

    def first_open(*args, cwd):
        assert args[:2] == ("replay", "--mock")
        real = Path(args[2])
        assert not real.exists()
        alias = tmp_path / "latest"
        alias.symlink_to(real.name, target_is_directory=True)
        return original_cli(*args[:2], alias, cwd=cwd)

    monkeypatch.setattr(stock, "cli", first_open)
    stock.test_process_loss_between_directory_renames_recovers_previous_record(tmp_path)


def test_lock_recovery_and_loader_use_same_destination(tmp_path, monkeypatch):
    dest = tmp_path / "record"
    replay.record_source('print("stable")', dest)
    first = tmp_path / "first"
    first.symlink_to(dest.name, target_is_directory=True)
    aliases = tmp_path / "aliases"
    aliases.mkdir()
    alias = aliases / "latest"
    alias.symlink_to(Path("..") / first.name, target_is_directory=True)
    recover = publication._recover
    seen = []

    def track_recovery(root, control):
        seen.append((root, control))
        return recover(root, control)

    monkeypatch.setattr(publication, "_recover", track_recovery)
    root = publication.read_recording(alias, lambda recording: recording)
    assert seen == [(dest, dest.parent / (".synapse-record-" + dest.name))], "recover at the writer destination"
    assert root == dest, "load from the same destination selected for recovery"


def test_alias_to_legacy_recording_remains_read_only(tmp_path):
    import shutil

    original = tmp_path / "writer"
    replay.record_source('print("archived")', original)
    legacy = tmp_path / "archive"
    shutil.copytree(original, legacy)
    alias = tmp_path / "latest"
    alias.symlink_to(legacy.name, target_is_directory=True)
    before = set(tmp_path.iterdir())
    opened = replay.replay_mock_artifact(alias)
    assert opened.drift == 0
    assert set(tmp_path.iterdir()) == before, "reading an archive must not create coordination state"


def test_reader_that_acquires_first_finishes_with_old_generation(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, local

    dest = tmp_path / "record"
    old = replay.record_source("print(uuid())", dest)
    alias = tmp_path / "latest"
    alias.symlink_to(dest.name, target_is_directory=True)
    reader_loading = Event()
    writer_arrived = Event()
    local_state = local()
    lock = publication.FileLock
    read = publication.read_recording

    @contextmanager
    def track_lock(path):
        if local_state.is_writer:
            writer_arrived.set()
        with lock(path):
            local_state.active = Path(path)
            try:
                yield
            finally:
                local_state.active = None

    def require_lock(output, load):
        def load_generation(root):
            assert getattr(local_state, "active", None) == tmp_path / ".synapse-record-record" / "publish.lock", (
                "reader must hold the publisher's lock before loading any files"
            )
            reader_loading.set()
            assert writer_arrived.wait(10)
            return load(root)
        return read(output, load_generation)

    def publish():
        local_state.is_writer = True
        return replay.record_source('print("new")', dest)

    monkeypatch.setattr(publication, "FileLock", track_lock)
    monkeypatch.setattr(replay, "read_recording", require_lock)
    local_state.is_writer = False
    with ThreadPoolExecutor(max_workers=1) as pool:
        def start_writer():
            if reader_loading.wait(10):
                return publish()
        pending = pool.submit(start_writer)
        try:
            opened = replay.replay_mock_artifact(alias)
        finally:
            reader_loading.set()
        new = pending.result(timeout=20)
    assert opened.drift == 0
    assert opened.program_hash == old["metadata"]["program_hash"]
    assert opened.final_history_hash == old["final"]["final_history_hash"]
    assert opened.state_sanity == old["final"]["state_sanity"]
    assert new["metadata"]["program_hash"] != old["metadata"]["program_hash"]


@pytest.mark.parametrize("writer,reader", READERS, ids=["replay", "integrate", "debugger"])
def test_first_reader_after_interruption_preserves_original(tmp_path, writer, reader):
    import hashlib

    dest = tmp_path / "record"
    manifest = writer('print("verified backup")', dest)
    expected = {p.name: hashlib.sha256(p.read_bytes()).digest() for p in dest.iterdir()}
    alias = tmp_path / "latest"
    alias.symlink_to(dest.name, target_is_directory=True)
    dest.rename(tmp_path / ".synapse-record-record" / "previous")
    opened = reader(alias)
    assert opened.program_hash == manifest["metadata"]["program_hash"]
    assert opened.final_history_hash == manifest["final"]["final_history_hash"]
    assert alias.is_symlink()
    assert expected == {p.name: hashlib.sha256(p.read_bytes()).digest() for p in dest.iterdir()}
