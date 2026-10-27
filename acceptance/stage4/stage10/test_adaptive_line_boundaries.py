"""Adaptive patch anchors must match complete source lines."""
import pytest

from synapse.worker.input_contract import WorkerInputViolation
from synapse.worker.local_edits import _adaptive_retained_patch_edits


PATCH = b'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n'


def adapt(source):
    return _adaptive_retained_patch_edits(PATCH, {'a.py': {source.encode()}})


def test_embedded_substring_is_not_an_anchor():
    with pytest.raises(WorkerInputViolation):
        adapt('max = 1\n')


def test_one_line_anchor_and_one_embedded_substring():
    result = adapt('max = 1\nx = 1\n')
    assert result['edits'][0]['new'] == 'max = 1\nx = 2\n'


def test_two_line_anchors_are_ambiguous():
    with pytest.raises(WorkerInputViolation):
        adapt('x = 1\nx = 1\n')


def test_overlapping_multiline_anchors_are_ambiguous():
    patch = (b'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n'
             b'@@ -1,2 +1,2 @@\n x = 1\n-x = 1\n+x = 2\n')
    with pytest.raises(WorkerInputViolation):
        _adaptive_retained_patch_edits(patch, {'a.py': {b'x = 1\nx = 1\nx = 1\n'}})


def test_start_of_file_is_an_anchor():
    assert adapt('x = 1\n')['edits'][0]['new'] == 'x = 2\n'


@pytest.mark.parametrize('source', ['x = 1', 'x = 1\r\n'])
def test_unsupported_source_line_endings_are_rejected(source):
    with pytest.raises(WorkerInputViolation):
        adapt(source)


@pytest.mark.parametrize('hunk', [b'@@ -0,0 +1 @@\n+x = 2\n', b'@@ invalid @@\n-x = 1\n+x = 2\n'])
def test_insertion_only_and_unsupported_hunks_are_rejected(hunk):
    patch = b'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n' + hunk
    with pytest.raises(WorkerInputViolation):
        _adaptive_retained_patch_edits(patch, {'a.py': {b'x = 1\n'}})
