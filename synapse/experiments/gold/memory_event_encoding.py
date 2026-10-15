"""Encode exact memory events as bounded content-addressed trees."""
from __future__ import annotations

import hashlib
import re

from .source_verification import canonical
from .stage10.context_codec import decode_canonical

MEMORY_BLOCK_MANIFEST_V1 = "synapse.stage4.gold.memory-event-blocks/v1"
MEMORY_EVENT_TREE_V2 = "synapse.stage4.gold.memory-event-tree/v2"
MEMORY_EVENT_INDEX_V2 = "synapse.stage4.gold.memory-event-index/v2"
MAX_LEGACY_EVENT_BYTES = 16 * 1024 * 1024
MIN_BLOCK_BYTES = 16 * 1024
MAX_BLOCK_BYTES = 64 * 1024
MAX_INDEX_CHILDREN = 256
MAX_LEGACY_EVENT_BLOCKS = MAX_LEGACY_EVENT_BYTES // MIN_BLOCK_BYTES
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def block_digest(value: object) -> str:
    """Require one opaque block identity before using it as a leaf name."""
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError("memory block identity must be a lowercase SHA-256 digest")
    return value


def _retain_block(raw, *, logical_length, height, blocks):
    if not 0 < len(raw) <= MAX_BLOCK_BYTES:
        raise ValueError("memory tree block exceeds its physical byte budget")
    digest = hashlib.sha256(raw).hexdigest()
    blocks[digest] = raw
    return {"sha256": digest, "byte_length": len(raw),
            "logical_byte_length": logical_length, "height": height}


def _leaf_blocks(raw, blocks):
    entries, offset = [], 0
    while offset < len(raw):
        limit = min(offset + MAX_BLOCK_BYTES, len(raw))
        found = raw.find(b"},", offset + MIN_BLOCK_BYTES, limit)
        end = found + 2 if found != -1 else limit
        chunk = raw[offset:end]
        entries.append(_retain_block(chunk, logical_length=len(chunk), height=0, blocks=blocks))
        offset = end
    return entries


def _parent_blocks(entries, blocks):
    # Balanced groups prevent a final single-child node from creating a unary
    # chain; every level strictly reduces the number of retained descriptors.
    groups = (len(entries) + MAX_INDEX_CHILDREN - 1) // MAX_INDEX_CHILDREN
    width, extra = divmod(len(entries), groups)
    parents, offset = [], 0
    for index in range(groups):
        end = offset + width + int(index < extra)
        children = entries[offset:end]
        raw = canonical({"schema_version": MEMORY_EVENT_INDEX_V2, "children": children})
        parents.append(_retain_block(raw, logical_length=sum(item["logical_byte_length"] for item in children),
                                     height=children[0]["height"] + 1, blocks=blocks))
        offset = end
    return parents


def encode_event(raw: bytes) -> tuple[bytes, dict[str, bytes]]:
    """Return the root plus physical blocks ordered before their parents."""
    if type(raw) is not bytes or not raw:
        raise ValueError("memory event requires nonempty canonical bytes")
    blocks = {}
    entries = _leaf_blocks(raw, blocks)
    while len(entries) > 1:
        entries = _parent_blocks(entries, blocks)
    manifest = {"schema_version": MEMORY_EVENT_TREE_V2,
                "logical_sha256": hashlib.sha256(raw).hexdigest(),
                "logical_byte_length": len(raw), "root": entries[0]}
    return canonical(manifest), blocks


def _tree_descriptor(value):
    if type(value) is not dict or set(value) != {"sha256", "byte_length", "logical_byte_length", "height"}:
        raise ValueError("memory tree descriptor has an unknown contract")
    block_digest(value["sha256"])
    size, length, height = value["byte_length"], value["logical_byte_length"], value["height"]
    if (type(size) is not int or not 0 < size <= MAX_BLOCK_BYTES
            or type(length) is not int or length <= 0 or type(height) is not int
            or not 0 <= height <= (length - 1).bit_length()
            or (height == 0 and size != length)):
        raise ValueError("memory tree descriptor has inconsistent bounds")
    return value


def decode_tree_manifest(raw: bytes) -> dict:
    """Validate a bounded root without setting a lifetime logical-size budget."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BLOCK_BYTES:
        raise ValueError("memory tree root exceeds its physical byte budget")
    value = decode_canonical(raw)
    if (type(value) is not dict or set(value) != {
            "schema_version", "logical_sha256", "logical_byte_length", "root"}
            or value["schema_version"] != MEMORY_EVENT_TREE_V2):
        raise ValueError("memory tree root has an unknown contract")
    block_digest(value["logical_sha256"])
    root = _tree_descriptor(value["root"])
    if (type(value["logical_byte_length"]) is not int
            or value["logical_byte_length"] != root["logical_byte_length"]):
        raise ValueError("memory tree root differs from its logical event length")
    return value


def decode_tree_node(raw: bytes, descriptor: dict) -> list[dict]:
    """Validate the bounded children of one physical index node."""
    _tree_descriptor(descriptor)
    if (descriptor["height"] == 0 or type(raw) is not bytes
            or len(raw) != descriptor["byte_length"] or len(raw) > MAX_BLOCK_BYTES
            or hashlib.sha256(raw).hexdigest() != descriptor["sha256"]):
        raise ValueError("memory tree index differs from its physical descriptor")
    node = decode_canonical(raw)
    if (type(node) is not dict or set(node) != {"schema_version", "children"}
            or node["schema_version"] != MEMORY_EVENT_INDEX_V2
            or type(node["children"]) is not list or not 2 <= len(node["children"]) <= MAX_INDEX_CHILDREN):
        raise ValueError("memory tree index has an unknown contract")
    children = [_tree_descriptor(item) for item in node["children"]]
    if (any(item["height"] != descriptor["height"] - 1 for item in children)
            or sum(item["logical_byte_length"] for item in children) != descriptor["logical_byte_length"]):
        raise ValueError("memory tree index differs from its subtree bounds")
    return children


def decode_manifest(raw: bytes) -> dict:
    """Read the retained v1 flat format under its original physical contract."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_LEGACY_EVENT_BYTES:
        raise ValueError("legacy memory manifest exceeds its physical byte budget")
    value = decode_canonical(raw)
    if (type(value) is not dict or set(value) != {
            "schema_version", "logical_sha256", "logical_byte_length", "chunks"}
            or value["schema_version"] != MEMORY_BLOCK_MANIFEST_V1):
        raise ValueError("memory event block manifest has an unknown contract")
    block_digest(value["logical_sha256"])
    length, entries = value["logical_byte_length"], value["chunks"]
    if (type(length) is not int or not 0 < length <= MAX_LEGACY_EVENT_BYTES
            or type(entries) is not list or not 0 < len(entries) <= MAX_LEGACY_EVENT_BLOCKS):
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
