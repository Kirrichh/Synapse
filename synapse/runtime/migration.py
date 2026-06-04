"""Migration safety validators for VM state restoration.

This module intentionally keeps migration policy out of VMState.from_dict():
VMState remains a JSON-safe parser/loader, while this layer validates whether
loaded runtime values are safe to resume against a target bytecode program.
"""
from __future__ import annotations

from typing import Any, Generator, Set

from synapse.cvm import FunctionObject, VMCodeMigrationRequiresMapError, VMState


def iter_function_objects(
    value: Any,
    _seen: Set[int] | None = None,
) -> Generator[FunctionObject, None, None]:
    """Yield every FunctionObject reachable from *value*, including closures.

    The traversal is cycle-safe. Cycles are not expected from the compiler today,
    but snapshots can be hand-authored, restored from older versions, or later
    augmented by host/runtime code. A migration validator must not be able to
    recurse forever on such input.
    """
    if _seen is None:
        _seen = set()

    if isinstance(value, FunctionObject):
        obj_id = id(value)
        if obj_id in _seen:
            return
        _seen.add(obj_id)
        yield value
        yield from iter_function_objects(value.closure, _seen)
        return

    if isinstance(value, dict):
        for child in value.values():
            yield from iter_function_objects(child, _seen)
        return

    if isinstance(value, (list, tuple)):
        for child in value:
            yield from iter_function_objects(child, _seen)
        return


def validate_vm_state_program_hashes(state: VMState, target_program_hash: str) -> None:
    """Fail closed if any live FunctionObject targets different bytecode.

    This validator is intended to run only in a migration context, after the
    snapshot program_hash is known to differ from the target program_hash and the
    call_stack is empty. Active call stacks are rejected before this point.

    Locations checked reflect the current Alpha.3-A VM model:
    - state.locals: active frame/user-visible bindings
    - state.stack: operand stack may contain a function object
    - state.name_save_stack: saved shadowed values may be functions
    - call_stack.locals_snapshot: caller locals preserved by active frames
    """
    locations = {
        "state.locals": state.locals,
        "state.stack": state.stack,
        "state.name_save_stack": [entry[2] for entry in getattr(state, "name_save_stack", [])],
        "state.call_stack.locals_snapshot": [frame.locals_snapshot for frame in state.call_stack],
    }

    for location_name, root in locations.items():
        for fn in iter_function_objects(root):
            if fn.program_hash is None:
                raise VMCodeMigrationRequiresMapError(
                    f"FunctionObject '{fn.name}' in {location_name} has no program_hash. "
                    f"Cannot verify body_ip={fn.body_ip} safety after code migration. "
                    "Requires explicit migration_map."
                )
            if fn.program_hash != target_program_hash:
                raise VMCodeMigrationRequiresMapError(
                    f"Stale FunctionObject '{fn.name}' in {location_name}: "
                    f"stored_hash={fn.program_hash[:12]}..., "
                    f"current_hash={target_program_hash[:12]}... "
                    "body_ip may point to wrong instructions after migration. "
                    "Requires explicit migration_map."
                )
