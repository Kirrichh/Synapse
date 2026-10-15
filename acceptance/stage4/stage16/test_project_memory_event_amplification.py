"""Adversarial tree expansion fails before reconstruction without truncating valid history."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from acceptance.stage4.stage16 import _memory_event_amplification as A
from acceptance.stage4.stage16 import _shared_memory_events as E
from synapse.experiments.gold import project_memory_store as M
from synapse.experiments.gold.memory_blocks import MemoryBlockStore
from synapse.experiments.gold.memory_event_encoding import MEMORY_EVENT_INDEX_V2, decode_tree_node

REPOSITORY = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(sys.platform == "win32", reason="resource ceiling uses POSIX RLIMIT_AS")
@pytest.mark.parametrize("entry", ["owner_session", "storage_read", "referenced_blocks"])
def test_four_gibibyte_tree_is_refused_under_process_memory_limit_without_mutation(tmp_path, entry):
    descriptor, blocks = A.tree(b"x" * 65536)
    store, transaction = A.committed_tree(tmp_path, descriptor, blocks)
    assert descriptor["logical_byte_length"] == 4 * 1024**3
    assert sum(map(len, blocks.values())) < 140000
    before = E.physical_files(store.root)
    script = ("from pathlib import Path; import sys; "
              "from acceptance.stage4.stage16._memory_event_amplification import bounded_read; "
              "bounded_read(Path(sys.argv[1]), sys.argv[2], sys.argv[3])")
    result = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path), transaction, entry],
                            cwd=REPOSITORY, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    observation = json.loads(result.stdout)
    assert observation["exception"] == "MemoryEventBudgetExceeded", observation
    assert "4294967296" in observation["detail"]
    assert E.physical_files(store.root) == before


def test_small_repeated_leaf_tree_restores_every_occurrence_at_exact_capacity(tmp_path):
    descriptor, blocks = A.tree(b"x", fanout=32, height=2)
    store, _ = A.committed_tree(tmp_path, descriptor, blocks)
    reader = MemoryBlockStore(store.root / "blocks", maximum_event_bytes=1024)
    raw, retained = reader.read_tree(descriptor)
    assert raw == b"x" * 1024
    assert retained == frozenset(blocks)


def test_understated_nested_length_cannot_hide_its_larger_children(tmp_path):
    child, blocks = A.tree(b"x" * 4096, fanout=4, height=1)
    actual_leaf = decode_tree_node(blocks[child["sha256"]], child)[0]
    understated = {**child, "logical_byte_length": child["logical_byte_length"] - 1}
    raw = M.canonical({"schema_version": MEMORY_EVENT_INDEX_V2, "children": [understated] * 2})
    descriptor = A.retain(raw, understated["logical_byte_length"] * 2, 2, blocks)
    store, _ = A.committed_tree(tmp_path, descriptor, blocks)
    (store.root / "blocks" / (actual_leaf["sha256"] + ".bin")).unlink()
    reader = MemoryBlockStore(store.root / "blocks", maximum_event_bytes=descriptor["logical_byte_length"])
    with pytest.raises(ValueError, match="subtree bounds"):
        reader.read_tree(descriptor)


def test_valid_event_beyond_legacy_sixteen_mebibytes_reopens_without_truncation(tmp_path):
    value = E.event(body={"retained_state": "x" * (17 * 1024**2)})
    store = M.ProjectMemoryStore(tmp_path)
    with store.session() as guard:
        receipt = E.put(store, guard, value)
    reopened = M.ProjectMemoryStore(tmp_path, read_only=True)
    assert reopened.read(receipt) == value
    assert len(M.canonical(value)) > 16 * 1024**2
