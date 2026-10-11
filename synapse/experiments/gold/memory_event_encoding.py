"""Encode memory events as exact sequences of bounded content-addressed bytes."""
from __future__ import annotations

import hashlib
import re

from .source_verification import canonical
from .stage10.context_codec import decode_canonical

MEMORY_BLOCK_MANIFEST_V1 = "synapse.stage4.gold.memory-event-blocks/v1"
MAX_EVENT_BYTES = 16 * 1024 * 1024
MIN_BLOCK_BYTES = 16 * 1024
MAX_BLOCK_BYTES = 64 * 1024
MAX_EVENT_BLOCKS = MAX_EVENT_BYTES // MIN_BLOCK_BYTES
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def block_digest(value: object) -> str:
    """Require one opaque block identity before using it as a leaf name."""
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError("memory block identity must be a lowercase SHA-256 digest")
    return value


def encode_event(raw: bytes) -> tuple[bytes, dict[str, bytes]]:
    """Factor bytes without interpreting JSON delimiters as semantic boundaries."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_EVENT_BYTES:
        raise ValueError("memory event exceeds its retained byte budget")
    entries, blocks, offset = [], {}, 0
    while offset < len(raw):
        limit = min(offset + MAX_BLOCK_BYTES, len(raw))
        found = raw.find(b"},", offset + MIN_BLOCK_BYTES, limit)
        end = found + 2 if found != -1 else limit
        chunk = raw[offset:end]
        digest = hashlib.sha256(chunk).hexdigest()
        entries.append({"sha256": digest, "byte_length": len(chunk)})
        blocks[digest] = chunk
        offset = end
    manifest = {"schema_version": MEMORY_BLOCK_MANIFEST_V1,
                "logical_sha256": hashlib.sha256(raw).hexdigest(),
                "logical_byte_length": len(raw), "chunks": entries}
    return canonical(manifest), blocks


def decode_manifest(raw: bytes) -> dict:
    """Validate the complete physical reconstruction contract before reading blocks."""
    value = decode_canonical(raw)
    if (type(value) is not dict or set(value) != {
            "schema_version", "logical_sha256", "logical_byte_length", "chunks"}
            or value["schema_version"] != MEMORY_BLOCK_MANIFEST_V1):
        raise ValueError("memory event block manifest has an unknown contract")
    block_digest(value["logical_sha256"])
    length, entries = value["logical_byte_length"], value["chunks"]
    if (type(length) is not int or not 0 < length <= MAX_EVENT_BYTES
            or type(entries) is not list or not 0 < len(entries) <= MAX_EVENT_BLOCKS):
        raise ValueError("memory event block manifest exceeds its reconstruction budget")
    total = 0
    for entry in entries:
        if type(entry) is not dict or set(entry) != {"sha256", "byte_length"}:
            raise ValueError("memory event block descriptor has an unknown contract")
        block_digest(entry["sha256"])
        size = entry["byte_length"]
        if type(size) is not int or not 0 < size <= MAX_BLOCK_BYTES:
            raise ValueError("memory event block exceeds its retained byte budget")
        total += size
    if total != length:
        raise ValueError("memory event block lengths differ from the logical event")
    return value
