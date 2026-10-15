"""Restore logical memory events from their versioned physical representations."""
from __future__ import annotations

import hashlib
import os

from .memory_blocks import MemoryBlockStore
from .memory_event_budget import EVENT_BUDGET_ENV, resolve_event_budget
from .memory_event_encoding import (MAX_BLOCK_BYTES, MAX_LEGACY_EVENT_BYTES,
                                    decode_manifest, decode_tree_manifest, encode_event)
from .persistence import (committed_transaction_exists, read_committed_snapshot_transaction,
                          require_directory, require_open_mutation_ticket, require_regular_file)

MAX_EVENT_MEMBER_BYTES = MAX_BLOCK_BYTES


class MemoryEventStorage:
    def __init__(self, root, *, maximum_event_bytes=None):
        self.events = root / "events"
        self.event_budget = resolve_event_budget(maximum_event_bytes, configured=os.environ.get(EVENT_BUDGET_ENV))
        self.blocks = MemoryBlockStore(root / "blocks", maximum_event_bytes=self.event_budget.maximum_bytes)

    def prepare(self, raw, *, ticket):
        """Publish shared payload bytes before the caller stages the terminal event transaction."""
        require_open_mutation_ticket(ticket)
        self.event_budget.require(len(raw))
        if len(raw) <= MAX_EVENT_MEMBER_BYTES:
            return {"event.json": raw}
        manifest, blocks = encode_event(raw)
        self.blocks.publish(blocks, ticket=ticket)
        return {"event-tree.json": manifest}

    def _restore(self, members):
        if set(members) == {"event-manifest.json"}:
            manifest = decode_manifest(members["event-manifest.json"])
            self.event_budget.require(manifest["logical_byte_length"])
            raw = self.blocks.read(manifest["chunks"])
            retained = frozenset(entry["sha256"] for entry in manifest["chunks"])
        elif set(members) == {"event-tree.json"}:
            manifest = decode_tree_manifest(members["event-tree.json"])
            self.event_budget.require(manifest["logical_byte_length"])
            raw, retained = self.blocks.read_tree(manifest["root"])
        else:
            raise ValueError("memory event has undeclared members")
        return raw, manifest, retained

    def _read(self, transaction):
        marker, members = read_committed_snapshot_transaction(self.events, transaction_id=transaction,
            maximum_member_bytes=MAX_LEGACY_EVENT_BYTES, maximum_members=1)
        if set(members) == {"event.json"}:
            if not 0 < len(members["event.json"]) <= MAX_LEGACY_EVENT_BYTES:
                raise ValueError("legacy memory event exceeds its retained byte budget")
            self.event_budget.require(len(members["event.json"]))
            return marker, members["event.json"], frozenset()
        raw, manifest, retained = self._restore(members)
        digest = hashlib.sha256(raw).hexdigest()
        if (len(raw) != manifest["logical_byte_length"] or digest != manifest["logical_sha256"]
                or marker["marker_sha256"] != digest):
            raise ValueError("memory event blocks differ from their logical event identity")
        return marker, raw, retained

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
