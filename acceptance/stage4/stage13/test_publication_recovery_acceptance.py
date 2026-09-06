"""Phase interruptions and fresh-process recovery of real participant stores."""

import json
import os
import subprocess
import sys

import pytest

from acceptance.stage4.stage13._case import negative_attempt, publication_case
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.persistence import PersistenceViolation
from synapse.experiments.gold.provenance import behavior_attestation_to_ref
from synapse.experiments.gold.stage13 import publication_store as PS


@pytest.fixture(scope="module")
def attempt(tmp_path_factory):
    return negative_attempt(tmp_path_factory.mktemp("verified-attempt"))


def _reopen(root):
    command = """
import json, sys
from pathlib import Path
from synapse.experiments.gold.knowledge_environment import open_gold_project
p = open_gold_project(Path(sys.argv[1]))
print(json.dumps({'entries': len(p.library.search_index()), 'epoch': p.fence.current_epoch()}))
"""
    result = subprocess.run([sys.executable, "-c", command, str(root)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("phase", ["PREPARED", "ATTESTATION", "ATTESTED", "AUTHORIZED", "LIBRARY", "INDEXED", "VERIFIED", "COMMITTED"])
def test_interrupted_phase_is_whole_or_invisible_in_a_new_process(tmp_path, monkeypatch, attempt, phase):
    case = publication_case(tmp_path / "project", attempt)
    original = case.publisher._phase

    def interrupt(tx, observed, ticket):
        original(tx, observed, ticket)
        if observed in {"LIBRARY", "INDEXED", "VERIFIED", "COMMITTED"}:
            assert case.project.library.search_index() == ()
            with pytest.raises(PersistenceViolation):
                case.project.lifecycle_store.current_state(subject_ref=behavior_attestation_to_ref(case.request.attestation),
                                                            context=case.request.context)
        if observed == phase:
            raise SystemExit("acceptance interruption")

    monkeypatch.setattr(case.publisher, "_phase", interrupt)
    with pytest.raises(SystemExit):
        case.publisher.publish(case.request)
    root = case.project.declaration.state_root
    recovered = _reopen(root)
    assert recovered["entries"] == (1 if phase == "COMMITTED" else 0)
    assert recovered["epoch"] % 2 == 0
    assert _reopen(root) == recovered


def test_preparation_interruption_has_durable_undo_before_first_member(tmp_path, monkeypatch, attempt):
    case = publication_case(tmp_path / "project", attempt)
    original = PS.stage_snapshot_transaction

    def interrupt(root, **kwargs):
        if root == case.publisher.root / "prepared":
            raise SystemExit("acceptance preparation interruption")
        return original(root, **kwargs)

    monkeypatch.setattr(PS, "stage_snapshot_transaction", interrupt)
    with pytest.raises(SystemExit):
        case.publisher.publish(case.request)
    assert _reopen(case.project.declaration.state_root)["entries"] == 0


def test_recovery_can_itself_be_interrupted_and_repeated(tmp_path, monkeypatch, attempt):
    case = publication_case(tmp_path / "project", attempt)
    original = case.publisher._phase

    def interrupt(tx, phase, ticket):
        original(tx, phase, ticket)
        if phase == "INDEXED":
            raise SystemExit("acceptance interruption")

    monkeypatch.setattr(case.publisher, "_phase", interrupt)
    with pytest.raises(SystemExit):
        case.publisher.publish(case.request)
    original_restore = PS.restore_uncommitted_file
    count = 0

    def interrupted_restore(*args, **kwargs):
        nonlocal count
        original_restore(*args, **kwargs)
        count += 1
        if count == 2:
            raise SystemExit("acceptance recovery interruption")

    with monkeypatch.context() as patch:
        patch.setattr(PS, "restore_uncommitted_file", interrupted_restore)
        with pytest.raises(SystemExit):
            open_gold_project(case.project.declaration.state_root)
    assert _reopen(case.project.declaration.state_root)["entries"] == 0


def test_recovery_repeats_after_quarantine_is_durable(tmp_path, monkeypatch, attempt):
    case = publication_case(tmp_path / "project", attempt)
    phase = case.publisher._phase

    def interrupt(tx, name, ticket):
        phase(tx, name, ticket)
        if name == "INDEXED":
            raise SystemExit("acceptance interruption")

    monkeypatch.setattr(case.publisher, "_phase", interrupt)
    with pytest.raises(SystemExit):
        case.publisher.publish(case.request)
    write = PS._write

    def interrupt_quarantine(path, raw, ticket):
        write(path, raw, ticket)
        if path.parent == case.publisher.root / "quarantine":
            raise SystemExit("acceptance recovery interruption")

    with monkeypatch.context() as patch:
        patch.setattr(PS, "_write", interrupt_quarantine)
        with pytest.raises(SystemExit):
            open_gold_project(case.project.declaration.state_root)
    first = _reopen(case.project.declaration.state_root)
    assert first["entries"] == 0 and first["epoch"] % 2 == 0
    assert _reopen(case.project.declaration.state_root) == first


def test_committed_recovery_repairs_a_torn_audit_tail(tmp_path, monkeypatch, attempt):
    case = publication_case(tmp_path / "project", attempt)
    phase = case.publisher._phase

    def interrupt(tx, name, ticket):
        phase(tx, name, ticket)
        if name == "COMMITTED":
            last = PS.scan_journal(case.publisher.journal).frames[-1]
            with case.publisher.journal.open("r+b") as stream:
                stream.truncate(last.start_offset + 4)
                stream.flush()
                os.fsync(stream.fileno())
            raise SystemExit("acceptance audit interruption")

    monkeypatch.setattr(case.publisher, "_phase", interrupt)
    with pytest.raises(SystemExit):
        case.publisher.publish(case.request)
    root = case.project.declaration.state_root
    first = _reopen(root)
    assert first["entries"] == 1 and first["epoch"] % 2 == 0
    scan = PS.scan_journal(case.publisher.journal)
    assert not scan.torn_tail
    assert PS.decode_canonical(scan.frames[-1].payload)["phase"] == "COMMITTED"
    assert _reopen(root) == first
