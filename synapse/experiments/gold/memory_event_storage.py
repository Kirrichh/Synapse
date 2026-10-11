"""Restore logical memory events from their versioned physical representations."""
from __future__ import annotations

import hashlib

from .memory_blocks import MemoryBlockStore
from .memory_event_encoding import MAX_BLOCK_BYTES, decode_manifest, encode_event
from .persistence import (committed_transaction_exists, read_committed_snapshot_transaction,
                          require_directory, require_open_mutation_ticket, require_regular_file)

SHARED_EVENT_KINDS = frozenset({"SNAPSHOT_BOUNDARY", "MEMORY_STATE", "CONSOLIDATION_REPORTED",
                              "REQUESTED", "FRAME_COMPLETED"})


class MemoryEventStorage:
    def __init__(self, root):
        self.events = root / "events"
        self.blocks = MemoryBlockStore(root / "blocks")

    def prepare(self, raw, *, kind, ticket):
        """Publish shared payload bytes before the caller stages the terminal event transaction."""
        require_open_mutation_ticket(ticket)
        if kind not in SHARED_EVENT_KINDS or len(raw) <= MAX_BLOCK_BYTES:
            return {"event.json": raw}
        manifest, blocks = encode_event(raw)
        self.blocks.publish(blocks, ticket=ticket)
        return {"event-manifest.json": manifest}

    def _read(self, transaction):
        marker, members = read_committed_snapshot_transaction(self.events, transaction_id=transaction)
        if set(members) == {"event.json"}:
            return marker, members["event.json"], frozenset()
        if set(members) != {"event-manifest.json"}:
            raise ValueError("memory event has undeclared members")
        manifest = decode_manifest(members["event-manifest.json"])
        raw = self.blocks.read(manifest["chunks"])
        digest = hashlib.sha256(raw).hexdigest()
        if (len(raw) != manifest["logical_byte_length"] or digest != manifest["logical_sha256"]
                or marker["marker_sha256"] != digest):
            raise ValueError("memory event blocks differ from their logical event identity")
        return marker, raw, frozenset(entry["sha256"] for entry in manifest["chunks"])

    def read(self, transaction):
        marker, raw, _ = self._read(transaction)
        return marker, raw

    def referenced_blocks(self, transaction):
        """Return physical reachability only after checking the complete retained representation."""
        return self._read(transaction)[2]

    def collect_garbage(self, *, ticket, validate_inventory):
        """Trace every committed event before removing any unreachable physical byte block."""
        require_open_mutation_ticket(ticket)
        require_directory(self.events)
        validate_inventory()
        retained = set()
        for path in sorted(self.events.iterdir()):
            require_directory(path)
            marker = path / "commit-marker.json"
            if marker.exists() or marker.is_symlink():
                require_regular_file(marker)
            if committed_transaction_exists(self.events, transaction_id=path.name):
                retained.update(self.referenced_blocks(path.name))
        return self.blocks.collect(retained, ticket=ticket)
