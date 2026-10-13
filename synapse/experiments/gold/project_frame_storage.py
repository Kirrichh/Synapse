"""Encode an active frame as exact references to its own retained values."""
from copy import deepcopy
import hashlib
import re

from .project_model import MAX_MODEL_ELEMENTS, MEMORY_KINDS
from .source_verification import canonical

COMPACT_FRAME_V1 = "synapse.stage4.gold.compact-active-memory-frame/v1"
_FRAME_SCHEMAS = {f"synapse.stage4.gold.active-memory-frame/v{version}" for version in (1, 2, 3, 4)}
_STATES = {"target_state", "current_state", "development_delta"}
_METADATA = {"element_id", "owner_id", "path"}
_ENVELOPE = {"schema_version", "expanded_sha256", "expanded_bytes", "frame", "elements", "values"}


def _retain(value, values, slots):
    raw = canonical(value)
    if raw not in slots:
        slots[raw] = len(values)
        values.append(deepcopy(value))
    return slots[raw]


def compact_frame(frame, *, max_bytes):
    """Retain repeated state values once without merging their logical occurrences."""
    if (type(frame) is not dict or type(frame.get("elements")) is not list
            or len(frame["elements"]) > MAX_MODEL_ELEMENTS):
        raise ValueError("active memory frame exceeds its element contract")
    raw = canonical(frame)
    if len(raw) > max_bytes:
        raise ValueError("active memory frame exceeds its expanded byte budget")
    if frame.get("schema_version") not in _FRAME_SCHEMAS:
        raise ValueError("active memory frame has an unknown schema")
    values, slots, elements = [], {}, []
    for item in frame["elements"]:
        if set(item) != _METADATA | _STATES | {"memory"} or set(item["memory"]) != set(MEMORY_KINDS):
            raise ValueError("active memory frame has an unknown element shape")
        elements.append({"metadata": {key: deepcopy(item[key]) for key in sorted(_METADATA)},
            "states": {key: _retain(item[key], values, slots) for key in sorted(_STATES)},
            "memory": {key: _retain(item["memory"][key], values, slots) for key in MEMORY_KINDS}})
    return {"schema_version": COMPACT_FRAME_V1, "expanded_sha256": hashlib.sha256(raw).hexdigest(),
        "expanded_bytes": len(raw), "frame": {key: deepcopy(value) for key, value in frame.items() if key != "elements"},
        "elements": elements, "values": values}


def _validate_envelope(value, max_bytes):
    if (type(value) is not dict or set(value) != _ENVELOPE or value["schema_version"] != COMPACT_FRAME_V1
            or type(value["frame"]) is not dict or "elements" in value["frame"]
            or value["frame"].get("schema_version") not in _FRAME_SCHEMAS
            or type(value["elements"]) is not list or len(value["elements"]) > MAX_MODEL_ELEMENTS
            or type(value["values"]) is not list or any(type(item) is not dict for item in value["values"])
            or type(value["expanded_bytes"]) is not int or not 0 <= value["expanded_bytes"] <= max_bytes
            or type(value["expanded_sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", value["expanded_sha256"]) is None):
        raise ValueError("compact memory frame differs from its local envelope")


def _element_size(item, sizes, used):
    if (type(item) is not dict or set(item) != {"metadata", "states", "memory"}
            or type(item["metadata"]) is not dict or set(item["metadata"]) != _METADATA
            or type(item["states"]) is not dict or set(item["states"]) != _STATES
            or type(item["memory"]) is not dict or set(item["memory"]) != set(MEMORY_KINDS)):
        raise ValueError("compact memory frame has an unknown element shape")
    references = [*item["states"].values(), *item["memory"].values()]
    if any(type(slot) is not int or not 0 <= slot < len(sizes) for slot in references):
        raise ValueError("compact memory frame reference leaves its bound frame")
    used.update(references)
    skeleton = {**item["metadata"], **{key: None for key in _STATES},
                "memory": {key: None for key in MEMORY_KINDS}}
    return len(canonical(skeleton)) + sum(sizes[slot] - 4 for slot in references)


def _expanded_size(value):
    sizes = [len(canonical(item)) for item in value["values"]]
    used, total = set(), len(canonical({**value["frame"], "elements": []}))
    for item in value["elements"]:
        total += _element_size(item, sizes, used)
    total += max(0, len(value["elements"]) - 1)
    if used != set(range(len(sizes))):
        raise ValueError("compact memory frame retains values outside its element bindings")
    return total


def restore_frame(value, *, max_bytes):
    """Resolve this frame's local slots after its retained-evidence check."""
    _validate_envelope(value, max_bytes)
    size = _expanded_size(value)
    if size != value["expanded_bytes"] or size > max_bytes:
        raise ValueError("compact memory frame exceeds its expanded byte budget")
    frame, values = deepcopy(value["frame"]), value["values"]
    frame["elements"] = [{**deepcopy(item["metadata"]),
        **{key: deepcopy(values[slot]) for key, slot in item["states"].items()},
        "memory": {key: deepcopy(values[slot]) for key, slot in item["memory"].items()}}
        for item in value["elements"]]
    if hashlib.sha256(canonical(frame)).hexdigest() != value["expanded_sha256"]:
        raise ValueError("compact memory frame differs from its expanded identity")
    return frame
