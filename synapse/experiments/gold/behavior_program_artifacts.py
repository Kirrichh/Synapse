"""Program-artifact producer adapter attached to the Behavior owner.

``behavior.py`` owns the unit, its canonical core, and compiler binding.
It deliberately performs no artifact resolution.  This adapter accepts a
program whose exact bytes have already been resolved by the Library-owned CAS
and seals the one canonical producer binding against the complete unit.

The dependency is one-way: this module imports the Behavior owner; the owner
does not import this adapter.  A replay composition root may combine this
adapter with the exact Library reader, but neither side grows a second binding
model or a replay-specific artifact store.
"""

from __future__ import annotations

from synapse.bytecode import BytecodeProgram

from .behavior import (
    ArtifactProgram,
    BehaviorFailureCode,
    BehaviorViolation,
    SynapseBehaviorUnit,
    _unit_context_sha256,
    validate_behavior_unit,
)
from .canonicalization import CompilerBinding, bind_resolved_artifact_program

#: The one private owner primitive this adapter consumes.  Naming the seam
#: makes an expansion visible in review instead of letting private dependencies
#: accumulate silently.
ADAPTER_PRIVATE_SEAM = {
    "synapse.experiments.gold.behavior": frozenset({"_unit_context_sha256"}),
}

__all__ = ["bind_artifact_behavior_unit"]


def bind_artifact_behavior_unit(
    unit: SynapseBehaviorUnit, *, program: BytecodeProgram
) -> CompilerBinding:
    """Bind a resolved artifact program to its complete canonical behavior.

    ``program`` has already crossed the Library exact-reader boundary.  This
    operation performs no I/O: it recursively revalidates the unit, rejects the
    inline form, and delegates to the existing canonical binding primitive.
    """

    validate_behavior_unit(unit)
    if type(unit.core.canonical_program) is not ArtifactProgram:
        raise BehaviorViolation(
            BehaviorFailureCode.PROGRAM_MISMATCH,
            "this behaviour's program is inline IR and is compiled, not resolved",
        )
    return bind_resolved_artifact_program(
        core=unit.canonical_core,
        program=program,
        unit_context_sha256=_unit_context_sha256(unit),
    )
