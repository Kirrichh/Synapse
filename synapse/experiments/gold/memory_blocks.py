"""Keep immutable shared byte blocks in the memory owner's physical custody."""
from __future__ import annotations

import hashlib
import re

from .memory_event_encoding import MAX_BLOCK_BYTES, block_digest
from .persistence import (ensure_durable_directory, new_operation_id, publish_immutable,
                          read_regular_bytes, remove_immutable, require_directory,
                          require_open_mutation_ticket, require_regular_file, write_staged_bytes)

_BLOCK_LEAF = re.compile(r"([0-9a-f]{64})\.bin\Z")
_STAGED_LEAF = re.compile(r"\.([0-9a-f]{64})\.bin\.stage-[0-9a-f]{32}\Z")


class MemoryBlockStore:
    def __init__(self, root):
        self.root = root

    def _read(self, digest, length):
        require_directory(self.root)
        raw = read_regular_bytes(self.root / (block_digest(digest) + ".bin"),
                                 maximum_bytes=MAX_BLOCK_BYTES)
        if len(raw) != length or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("memory block differs from its retained content identity")
        return raw

    def read(self, entries):
        """Recheck every referenced file on each reconstruction, including repeated blocks."""
        return b"".join(self._read(entry["sha256"], entry["byte_length"]) for entry in entries)

    def publish(self, blocks, *, ticket):
        """Make every block durable before its event manifest may be published."""
        require_open_mutation_ticket(ticket)
        ensure_durable_directory(self.root, ticket=ticket)
        for digest, raw in blocks.items():
            block_digest(digest)
            if (type(raw) is not bytes or not 0 < len(raw) <= MAX_BLOCK_BYTES
                    or hashlib.sha256(raw).hexdigest() != digest):
                raise ValueError("memory block bytes differ from their publication identity")
            path = self.root / (digest + ".bin")
            if path.exists() or path.is_symlink():
                self._read(digest, len(raw))
                continue
            staged = write_staged_bytes(self.root, final_name=path.name,
                                        operation_id=new_operation_id(), value=raw,
                                        maximum_bytes=MAX_BLOCK_BYTES, ticket=ticket)
            publish_immutable(staged, path, ticket=ticket)

    def _garbage(self, retained):
        if not self.root.exists() and not self.root.is_symlink():
            return []
        require_directory(self.root)
        garbage = []
        for path in sorted(self.root.iterdir()):
            block, staged = _BLOCK_LEAF.fullmatch(path.name), _STAGED_LEAF.fullmatch(path.name)
            if block is None and staged is None:
                raise ValueError("memory block directory contains an undeclared entry")
            require_regular_file(path)
            if staged is not None or block.group(1) not in retained:
                raw = read_regular_bytes(path, maximum_bytes=MAX_BLOCK_BYTES)
                garbage.append((path, len(raw), staged is not None))
        return garbage

    def collect(self, retained, *, ticket):
        """Remove only unreachable bytes after validating the entire physical namespace."""
        require_open_mutation_ticket(ticket)
        for digest in retained:
            block_digest(digest)
        garbage = self._garbage(retained)
        result = {"removed_blocks": 0, "removed_staged_files": 0, "removed_bytes": 0,
                  "retained_blocks": len(retained)}
        for path, length, staged in garbage:
            remove_immutable(path, ticket=ticket)
            result["removed_staged_files" if staged else "removed_blocks"] += 1
            result["removed_bytes"] += length
        return result
