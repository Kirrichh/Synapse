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
import hashlib

from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
)
from .contracts import (
    AttemptId,
    CommonEnvelope,
    ContractFailureCode,
    SchemaVersion,
    IdentityDomain,
    RepositoryRevision,
    RunId,
    UTC_TIMESTAMP_FORMAT,
    _require_sha256,
    _validate_utc_timestamp,
    _violation,
    compute_envelope_binding_sha256,
    create_common_envelope,
    envelope_bound_record_bytes,
    validate_envelope_bound_record,
)

#: Names taken from the owner's private surface. Reimplementing the digest and
#: timestamp validators here is how two modules end up disagreeing about what a
#: valid record is, so the seam is shared rather than duplicated — and the cost,
#: a non-public dependency, is paid by writing the dependency down where a
#: reviewer and a tripwire can both see it.
ADAPTER_PRIVATE_SEAM = ("_require_sha256", "_validate_utc_timestamp", "_violation")

_FROZEN_CANDIDATE_SET_SEAL = object()


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


@dataclass(frozen=True, init=False)
class FrozenCandidateSet:
    """The objects a committed snapshot allows a run to consider, by name."""

    schema_version: str
    envelope: CommonEnvelope | None
    envelope_binding_sha256: str | None
    boundary_ref: HashBoundRef | None
    snapshot_ref: HashBoundRef | None
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
        payload = _frozen_payload(self)
        if self.schema_version == SchemaVersion.FROZEN_CANDIDATE_SET_V1.value:
            return payload
        assert self.envelope is not None and self.envelope_binding_sha256 is not None
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": payload,
        }

    def canonical_bytes(self) -> bytes:
        validate_frozen_candidate_set(self)
        if self.schema_version == SchemaVersion.FROZEN_CANDIDATE_SET_V1.value:
            return _canonical(_frozen_payload(self))
        assert self.envelope is not None and self.envelope_binding_sha256 is not None
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=_frozen_payload(self),
        )


def _frozen_payload(value: FrozenCandidateSet) -> dict[str, object]:
    payload = {
            "schema_version": value.schema_version,
            "boundary_id_sha256": value.boundary_id_sha256,
            "snapshot_id_sha256": value.snapshot_id_sha256,
            "subject_ref_keys": list(value.subject_ref_keys),
            "frozen_at_utc": value.frozen_at_utc.strftime(UTC_TIMESTAMP_FORMAT),
        }
    if value.schema_version == SchemaVersion.FROZEN_CANDIDATE_SET_V2.value:
        assert value.boundary_ref is not None and value.snapshot_ref is not None
        payload["boundary_ref"] = value.boundary_ref.to_dict()
        payload["snapshot_ref"] = value.snapshot_ref.to_dict()
    return payload


def validate_frozen_candidate_set(value: FrozenCandidateSet) -> FrozenCandidateSet:
    if (
        type(value) is not FrozenCandidateSet
        or getattr(value, "_trusted_seal", None) is not _FROZEN_CANDIDATE_SET_SEAL
    ):
        raise _violation(
            ContractFailureCode.TRUSTED_OBJECT_FORGED,
            "frozen candidate set is not snapshot minted",
        )
    if value.schema_version not in {
        SchemaVersion.FROZEN_CANDIDATE_SET_V1.value,
        SchemaVersion.FROZEN_CANDIDATE_SET_V2.value,
    }:
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
    if value.schema_version == SchemaVersion.FROZEN_CANDIDATE_SET_V2.value:
        if type(value.boundary_ref) is not HashBoundRef or value.boundary_ref.kind is not RefKind.ATOMIC_BOUNDARY:
            raise _violation(ContractFailureCode.TYPE_MISMATCH, "frozen boundary ref is invalid")
        if type(value.snapshot_ref) is not HashBoundRef or value.snapshot_ref.kind is not RefKind.KNOWLEDGE_SNAPSHOT:
            raise _violation(ContractFailureCode.TYPE_MISMATCH, "frozen snapshot ref is invalid")
        if value.boundary_ref.ref_id != value.boundary_id_sha256 or value.snapshot_ref.ref_id != value.snapshot_id_sha256:
            raise _violation(ContractFailureCode.RECORD_ID_MISMATCH, "frozen aliases differ from exact refs")
        if value.envelope is None or value.envelope_binding_sha256 is None:
            raise _violation(ContractFailureCode.RECORD_ID_MISMATCH, "frozen envelope is absent")
        validate_envelope_bound_record(
            envelope=value.envelope,
            envelope_binding_sha256=value.envelope_binding_sha256,
            canonical_domain_payload_bytes=_canonical(_frozen_payload(value)),
            expected_identity_domain=IdentityDomain.FROZEN_CANDIDATE_SET_V2,
        )
    return value


def frozen_candidate_set_ref(value: FrozenCandidateSet) -> HashBoundRef:
    """Return the exact full-record reference carried by retrieval causal evidence."""

    validate_frozen_candidate_set(value)
    if value.schema_version != SchemaVersion.FROZEN_CANDIDATE_SET_V2.value:
        raise _violation(
            ContractFailureCode.UNKNOWN_SCHEMA_VERSION,
            "current retrieval authority requires a v2 frozen candidate set",
        )
    assert value.envelope is not None
    canonical = value.canonical_bytes()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=value.envelope.record_id.digest_sha256,
        schema_id=value.schema_version,
        sha256=hashlib.sha256(canonical).hexdigest(),
        byte_length=len(canonical),
        media_type="application/json",
    )


def _mint_frozen_candidate_set(
    *,
    boundary_ref: HashBoundRef,
    snapshot_ref: HashBoundRef,
    subject_ref_keys: tuple[str, ...],
    frozen_at_utc: datetime,
    run_id: RunId,
    attempt_id: AttemptId,
    repository_revision: str,
    policy_version: str,
    environment_profile_id: str,
) -> FrozenCandidateSet:
    """Mint the capability. Private for the reason the write capability is.

    A frozen set whose factory is public is not a constraint: any caller could
    assemble one naming whatever it wanted to retrieve, and the snapshot would
    stand beside the enumeration rather than in front of it. The name is private
    and a tripwire holds it to one importer.
    """

    result = object.__new__(FrozenCandidateSet)
    object.__setattr__(result, "schema_version", SchemaVersion.FROZEN_CANDIDATE_SET_V2.value)
    object.__setattr__(result, "boundary_ref", boundary_ref)
    object.__setattr__(result, "snapshot_ref", snapshot_ref)
    object.__setattr__(result, "boundary_id_sha256", boundary_ref.ref_id)
    object.__setattr__(result, "snapshot_id_sha256", snapshot_ref.ref_id)
    object.__setattr__(result, "subject_ref_keys", subject_ref_keys)
    object.__setattr__(result, "frozen_at_utc", frozen_at_utc)
    object.__setattr__(result, "_trusted_seal", _FROZEN_CANDIDATE_SET_SEAL)
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V2,
        identity_domain=IdentityDomain.FROZEN_CANDIDATE_SET_V2,
        canonical_payload_bytes=_canonical(_frozen_payload(result)),
        run_id=run_id,
        attempt_id=attempt_id,
        created_at_utc=frozen_at_utc,
        producer_component="frozen-candidate-adapter",
        repository_revision=RepositoryRevision.git_commit(repository_revision),
        policy_version=policy_version,
        environment_profile_id=environment_profile_id,
        lineage_parent_ids=(),
    )
    object.__setattr__(result, "envelope", envelope)
    object.__setattr__(result, "envelope_binding_sha256", compute_envelope_binding_sha256(envelope))
    return validate_frozen_candidate_set(result)


__all__ = (
    "ADAPTER_PRIVATE_SEAM",
    "FrozenCandidateSet",
    "frozen_candidate_set_ref",
    "validate_frozen_candidate_set",
)
