"""The frozen candidate set: what a committed snapshot lets retrieval consider.

An adapter of `contracts.py`, not a §12 owner. It holds no responsibility of its
own — `FrozenCandidateSet` is a shared vocabulary record like every other sealed
contract, and it lives here only because `contracts.py` is past the size at which
this repository's owner requires additions to arrive through an adapter rather
than be grown in place. `authority_config.py` is the same relation to the same
module, and the direction is the one an adapter must have: this module imports
`contracts` and `contracts` imports nothing from it.

**Why the record exists at all.** `enumerate_retrieval_candidates` must be able
to *demand* the snapshot's constraint before it enumerates, and the snapshot
owner (`knowledge.py`) sits later in the §38 order than the retrieval owner. A
shared vocabulary record is how the two ends of that arrow meet without the
earlier one importing the later.

**Why it carries names rather than objects.** The set holds the §22 subject key
of every frozen candidate plus the identities of the boundary and manifest they
came from — strings and one timestamp, nothing else. That keeps snapshot
semantics out of this module: nothing here can open a snapshot or decide that one
is usable. Verifying the snapshot is `gate_findings`' job, and this record is how
the result of that verification crosses the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .contracts import (
    ContractFailureCode,
    SchemaVersion,
    UTC_TIMESTAMP_FORMAT,
    _require_sha256,
    _validate_utc_timestamp,
    _violation,
)

#: Names taken from the owner's private surface. Reimplementing the digest and
#: timestamp validators here is how two modules end up disagreeing about what a
#: valid record is, so the seam is shared rather than duplicated — and the cost,
#: a non-public dependency, is paid by writing the dependency down where a
#: reviewer and a tripwire can both see it.
ADAPTER_PRIVATE_SEAM = ("_require_sha256", "_validate_utc_timestamp", "_violation")

_FROZEN_CANDIDATE_SET_SEAL = object()


@dataclass(frozen=True, init=False)
class FrozenCandidateSet:
    """The objects a committed snapshot allows a run to consider, by name."""

    schema_version: str
    boundary_id_sha256: str
    snapshot_id_sha256: str
    subject_ref_keys: tuple[str, ...]
    frozen_at_utc: datetime
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> FrozenCandidateSet:
        raise TypeError(
            "FrozenCandidateSet is produced only by the adapter that verifies a "
            "committed knowledge snapshot"
        )

    def __contains__(self, subject_ref_key: object) -> bool:
        validate_frozen_candidate_set(self)
        return subject_ref_key in self.subject_ref_keys

    def to_dict(self) -> dict[str, object]:
        validate_frozen_candidate_set(self)
        return {
            "schema_version": self.schema_version,
            "boundary_id_sha256": self.boundary_id_sha256,
            "snapshot_id_sha256": self.snapshot_id_sha256,
            "subject_ref_keys": list(self.subject_ref_keys),
            "frozen_at_utc": self.frozen_at_utc.strftime(UTC_TIMESTAMP_FORMAT),
        }


def validate_frozen_candidate_set(value: FrozenCandidateSet) -> FrozenCandidateSet:
    if (
        type(value) is not FrozenCandidateSet
        or getattr(value, "_trusted_seal", None) is not _FROZEN_CANDIDATE_SET_SEAL
    ):
        raise _violation(
            ContractFailureCode.TRUSTED_OBJECT_FORGED,
            "frozen candidate set is not snapshot minted",
        )
    if value.schema_version != SchemaVersion.FROZEN_CANDIDATE_SET_V1.value:
        raise _violation(
            ContractFailureCode.UNKNOWN_SCHEMA_VERSION,
            "frozen candidate set schema is unknown",
        )
    _require_sha256(value.boundary_id_sha256, "frozen.boundary_id_sha256")
    _require_sha256(value.snapshot_id_sha256, "frozen.snapshot_id_sha256")
    keys = value.subject_ref_keys
    if type(keys) is not tuple or any(type(item) is not str or not item for item in keys):
        raise _violation(
            ContractFailureCode.TYPE_MISMATCH,
            "frozen candidate keys must be an exact tuple of non-empty strings",
        )
    # Sorted and duplicate-free, because the set is part of what the enumeration
    # is judged against: two snapshots that froze the same objects must present
    # the same set, and a repeated key would let one object be counted twice.
    if list(keys) != sorted(set(keys)):
        raise _violation(
            ContractFailureCode.TYPE_MISMATCH,
            "frozen candidate keys must be sorted and duplicate-free",
        )
    _validate_utc_timestamp(value.frozen_at_utc)
    return value


def _mint_frozen_candidate_set(
    *,
    boundary_id_sha256: str,
    snapshot_id_sha256: str,
    subject_ref_keys: tuple[str, ...],
    frozen_at_utc: datetime,
) -> FrozenCandidateSet:
    """Mint the capability. Private for the reason the write capability is.

    A frozen set whose factory is public is not a constraint: any caller could
    assemble one naming whatever it wanted to retrieve, and the snapshot would
    stand beside the enumeration rather than in front of it. The name is private
    and a tripwire holds it to one importer.
    """

    result = object.__new__(FrozenCandidateSet)
    object.__setattr__(result, "schema_version", SchemaVersion.FROZEN_CANDIDATE_SET_V1.value)
    object.__setattr__(result, "boundary_id_sha256", boundary_id_sha256)
    object.__setattr__(result, "snapshot_id_sha256", snapshot_id_sha256)
    object.__setattr__(result, "subject_ref_keys", subject_ref_keys)
    object.__setattr__(result, "frozen_at_utc", frozen_at_utc)
    object.__setattr__(result, "_trusted_seal", _FROZEN_CANDIDATE_SET_SEAL)
    return validate_frozen_candidate_set(result)


__all__ = (
    "ADAPTER_PRIVATE_SEAM",
    "FrozenCandidateSet",
    "validate_frozen_candidate_set",
)
