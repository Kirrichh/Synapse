from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from synapse.experiments.gold.contracts import (
    IDENTITY_PREIMAGE_PREFIX,
    IDENTITY_PROTOCOL_VERSION,
    RECORD_ID_TEXT_SEPARATOR,
    ActorIdentity,
    AttemptId,
    AuthorityDecisionId,
    AuthorityIdentity,
    AuthorityRole,
    ClaimedRecordId,
    CommonEnvelope,
    ContractFailureCode,
    ContractViolation,
    DelegationStep,
    ExecutionId,
    IdentityDomain,
    IndependenceProof,
    LineageEdgeKind,
    LineageParentRef,
    ProposalId,
    ReasonCode,
    RecordId,
    RepositoryRevision,
    RepositoryRevisionKind,
    RunId,
    SchemaVersion,
    authority_decision_id_from_dict,
    common_envelope_from_dict,
    compute_authority_decision_id,
    compute_execution_id,
    compute_payload_sha256,
    compute_proposal_id,
    compute_record_id,
    create_common_envelope,
    create_independence_proof,
    execution_id_from_dict,
    independence_proof_from_dict,
    record_id_from_text,
    validate_common_envelope,
    validate_independence_proof,
    validate_record_id,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "gold" / "contract_vectors_v1.json"
PAYLOAD = bytes.fromhex("7b2270726f706f73616c223a22616c706861227d")
REVISION = "3e7a8a5f2f3b24184b592bb887b1626ecfb3e8d8"
CREATED_AT = datetime(2026, 7, 15, 10, 20, 30, 123456, tzinfo=timezone.utc)


def fixture_vectors() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def assert_failure(code: ContractFailureCode, operation) -> ContractViolation:
    with pytest.raises(ContractViolation) as caught:
        operation()
    assert caught.value.failure_code is code
    return caught.value


def make_envelope(
    *,
    payload: bytes = PAYLOAD,
    identity_domain: IdentityDomain = IdentityDomain.COMMON_RECORD,
    created_at: datetime = CREATED_AT,
    lineage: tuple[LineageParentRef, ...] = (),
    repository_revision: RepositoryRevision | None = None,
) -> CommonEnvelope:
    return create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=identity_domain,
        canonical_payload_bytes=payload,
        run_id=RunId("run-stage4-001"),
        attempt_id=AttemptId("attempt-stage4-001"),
        created_at_utc=created_at,
        producer_component="gold.contracts",
        repository_revision=(
            RepositoryRevision.git_commit(REVISION)
            if repository_revision is None
            else repository_revision
        ),
        policy_version="synapse.stage4.gold-policy/v1",
        environment_profile_id="synapse.stage4.test-environment/v1",
        lineage_parent_ids=lineage,
    )


def make_proof(**overrides: object) -> IndependenceProof:
    values: dict[str, object] = {
        "schema_version": SchemaVersion.INDEPENDENCE_PROOF_V1,
        "subject_proposal_id": compute_proposal_id(canonical_bytes=PAYLOAD),
        "authority_identity": AuthorityIdentity("authority-a"),
        "authority_role": AuthorityRole.PLAN_REVIEWER,
        "reason_code": ReasonCode.PLAN_REVIEW_INDEPENDENT,
        "producer_actor_ids": (ActorIdentity("producer-a"),),
        "source_actor_ids": (ActorIdentity("source-a"),),
        "proposer_identity": ActorIdentity("proposer-a"),
        "executor_identity": None,
        "subject_derived_actor_ids": (),
        "delegation_chain": (),
    }
    values.update(overrides)
    return create_independence_proof(**values)  # type: ignore[arg-type]


def replace_fixture_path(value: dict[str, object], path: str, replacement: object) -> None:
    parts = path.split(".")
    target: dict[str, object] = value
    for part in parts[:-1]:
        nested = target[part]
        assert type(nested) is dict
        target = nested
    target[parts[-1]] = replacement


def test_all_public_value_objects_construct_and_preserve_exact_types() -> None:
    claimed = ClaimedRecordId("worker-claimed-id")
    run_id = RunId("run-001")
    attempt_id = AttemptId("attempt-001")
    actor = ActorIdentity("actor-001")
    authority = AuthorityIdentity("authority-001")
    revision = RepositoryRevision.git_commit(REVISION)
    proposal = compute_proposal_id(canonical_bytes=PAYLOAD)
    proof = make_proof()
    decision = compute_authority_decision_id(
        canonical_bytes=b"decision",
        independence_proof=proof,
    )
    execution = compute_execution_id(
        canonical_bytes=b"execution",
        authority_decision_id=decision,
    )

    assert ClaimedRecordId.from_dict(claimed.to_dict()) == claimed
    assert RunId.from_dict(run_id.to_dict()) == run_id
    assert AttemptId.from_dict(attempt_id.to_dict()) == attempt_id
    assert ActorIdentity.from_dict(actor.to_dict()) == actor
    assert AuthorityIdentity.from_dict(authority.to_dict()) == authority
    assert RepositoryRevision.from_dict(revision.to_dict()) == revision
    assert type(proposal) is ProposalId
    assert type(decision) is AuthorityDecisionId
    assert type(execution) is ExecutionId


def test_valid_common_envelope_round_trip_preserves_typed_semantics() -> None:
    envelope = make_envelope()
    transport = envelope.to_dict()
    parsed = CommonEnvelope.from_dict(transport, canonical_payload_bytes=PAYLOAD)

    assert parsed == envelope
    assert type(parsed.schema_version) is SchemaVersion
    assert type(parsed.record_id) is RecordId
    assert type(parsed.run_id) is RunId
    assert type(parsed.attempt_id) is AttemptId
    assert type(parsed.repository_revision) is RepositoryRevision
    assert type(parsed.lineage_parent_ids) is tuple
    assert parsed.to_dict() == transport
    assert "payload" not in transport


@pytest.mark.parametrize("field", ["schema_version", "record_id", "payload_sha256"])
def test_envelope_missing_required_field_rejected(field: str) -> None:
    transport = make_envelope().to_dict()
    del transport[field]
    assert_failure(
        ContractFailureCode.MISSING_REQUIRED_FIELD,
        lambda: common_envelope_from_dict(transport, canonical_payload_bytes=PAYLOAD),
    )


def test_envelope_unknown_field_rejected() -> None:
    transport = make_envelope().to_dict()
    transport["future_default"] = False
    assert_failure(
        ContractFailureCode.UNKNOWN_FIELD,
        lambda: common_envelope_from_dict(transport, canonical_payload_bytes=PAYLOAD),
    )


def test_malformed_versions_ids_components_hashes_and_timestamps_rejected() -> None:
    assert_failure(ContractFailureCode.MALFORMED_IDENTITY, lambda: RunId(""))
    assert_failure(ContractFailureCode.MALFORMED_IDENTITY, lambda: AttemptId("has space"))
    assert_failure(
        ContractFailureCode.MALFORMED_VERSION,
        lambda: create_common_envelope(
            schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
            identity_domain=IdentityDomain.COMMON_RECORD,
            canonical_payload_bytes=PAYLOAD,
            run_id=RunId("run-1"),
            attempt_id=AttemptId("attempt-1"),
            created_at_utc=CREATED_AT,
            producer_component="gold.contracts",
            repository_revision=RepositoryRevision.git_commit(REVISION),
            policy_version="unversioned",
            environment_profile_id="synapse.stage4.environment/v1",
            lineage_parent_ids=(),
        ),
    )
    transport = make_envelope().to_dict()
    transport["producer_component"] = "Gold Contracts"
    assert_failure(
        ContractFailureCode.MALFORMED_IDENTITY,
        lambda: CommonEnvelope.from_dict(transport, canonical_payload_bytes=PAYLOAD),
    )
    transport = make_envelope().to_dict()
    transport["payload_sha256"] = "ABC"
    assert_failure(
        ContractFailureCode.MALFORMED_SHA256,
        lambda: CommonEnvelope.from_dict(transport, canonical_payload_bytes=PAYLOAD),
    )
    transport = make_envelope().to_dict()
    transport["created_at_utc"] = "2026-07-15T10:20:30Z"
    assert_failure(
        ContractFailureCode.MALFORMED_TIMESTAMP,
        lambda: CommonEnvelope.from_dict(transport, canonical_payload_bytes=PAYLOAD),
    )


def test_naive_and_non_utc_timestamps_rejected() -> None:
    naive = CREATED_AT.replace(tzinfo=None)
    offset = CREATED_AT.astimezone(timezone(timedelta(hours=3)))
    assert_failure(ContractFailureCode.MALFORMED_TIMESTAMP, lambda: make_envelope(created_at=naive))
    assert_failure(ContractFailureCode.MALFORMED_TIMESTAMP, lambda: make_envelope(created_at=offset))


def test_identity_same_and_different_bytes_and_domains() -> None:
    first = compute_record_id(domain=IdentityDomain.COMMON_RECORD, canonical_bytes=PAYLOAD)
    repeated = compute_record_id(domain=IdentityDomain.COMMON_RECORD, canonical_bytes=PAYLOAD)
    different_domain = compute_record_id(domain=IdentityDomain.PROPOSAL, canonical_bytes=PAYLOAD)
    different_bytes = compute_record_id(
        domain=IdentityDomain.COMMON_RECORD,
        canonical_bytes=PAYLOAD + b"!",
    )
    assert first == repeated
    assert first != different_domain
    assert first != different_bytes
    assert record_id_from_text(first.value, canonical_bytes=PAYLOAD) == first


def test_fixture_vectors_have_exact_hashes_and_domain_separated_ids() -> None:
    vectors = fixture_vectors()
    profile = vectors["contract_profile"]
    assert type(profile) is dict
    assert profile["identity_protocol_version"] == IDENTITY_PROTOCOL_VERSION
    assert profile["identity_preimage_prefix_hex"] == IDENTITY_PREIMAGE_PREFIX.hex()
    assert profile["record_id_text_separator"] == RECORD_ID_TEXT_SEPARATOR
    assert profile["canonical_domain_object_encoding"] is None

    identities = vectors["identity_vectors"]
    assert type(identities) is list
    for vector in identities:
        assert type(vector) is dict
        canonical_bytes = bytes.fromhex(vector["canonical_bytes_hex"])
        domain = IdentityDomain(vector["domain"])
        assert compute_payload_sha256(canonical_bytes=canonical_bytes) == vector["payload_sha256"]
        assert compute_record_id(domain=domain, canonical_bytes=canonical_bytes).value == vector["record_id"]


def test_fixture_valid_envelope_and_invalid_cases_have_typed_failures() -> None:
    vectors = fixture_vectors()
    valid = vectors["valid_envelope"]
    assert type(valid) is dict
    payload = bytes.fromhex(valid["canonical_payload_bytes_hex"])
    transport = valid["value"]
    parsed = CommonEnvelope.from_dict(transport, canonical_payload_bytes=payload)
    assert parsed.to_dict() == transport

    invalid_cases = vectors["invalid_envelopes"]
    assert type(invalid_cases) is list
    for case in invalid_cases:
        assert type(case) is dict
        mutated = deepcopy(transport)
        assert type(mutated) is dict
        replace_fixture_path(mutated, case["path"], case["replacement"])
        assert_failure(
            ContractFailureCode(case["expected_failure_code"]),
            lambda mutated=mutated: CommonEnvelope.from_dict(
                mutated,
                canonical_payload_bytes=payload,
            ),
        )


def test_fixture_authority_vectors_have_typed_acceptance_and_rejection() -> None:
    authority = fixture_vectors()["authority"]
    assert type(authority) is dict
    proposal_bytes = bytes.fromhex(authority["proposal_canonical_bytes_hex"])
    valid = authority["valid"]
    proof = IndependenceProof.from_dict(valid, proposal_canonical_bytes=proposal_bytes)
    assert proof.to_dict() == valid

    invalid_cases = authority["invalid"]
    assert type(invalid_cases) is list
    for case in invalid_cases:
        assert type(case) is dict
        mutated = deepcopy(valid)
        assert type(mutated) is dict
        replace_fixture_path(mutated, case["path"], case["replacement"])
        assert_failure(
            ContractFailureCode(case["expected_failure_code"]),
            lambda mutated=mutated: IndependenceProof.from_dict(
                mutated,
                proposal_canonical_bytes=proposal_bytes,
            ),
        )


def test_repository_revision_is_typed_git_or_typed_not_applicable() -> None:
    git_revision = RepositoryRevision.git_commit(REVISION)
    absent = RepositoryRevision.not_applicable()
    assert git_revision.to_dict() == {"kind": "GIT_COMMIT", "git_sha": REVISION}
    assert absent.to_dict() == {"kind": "NOT_APPLICABLE", "git_sha": None}
    assert RepositoryRevision.from_dict(absent.to_dict()).kind is RepositoryRevisionKind.NOT_APPLICABLE

    invalid_values = (
        "3e7a8a5",
        REVISION.upper(),
        "N/A",
        "unknown",
        "",
    )
    for value in invalid_values:
        assert_failure(
            ContractFailureCode.MALFORMED_REPOSITORY_REVISION,
            lambda value=value: RepositoryRevision.git_commit(value),
        )
    for sentinel in ("N/A", "unknown", "unavailable", "not-applicable", "redacted", "", False, 0):
        assert_failure(
            ContractFailureCode.TYPE_MISMATCH,
            lambda sentinel=sentinel: RepositoryRevision.from_dict(sentinel),
        )


def test_lineage_is_ordered_immutable_recomputed_and_duplicate_free() -> None:
    parent_a_bytes = b"parent-a"
    parent_b_bytes = b"parent-b"
    parent_a = LineageParentRef(
        compute_record_id(domain=IdentityDomain.COMMON_RECORD, canonical_bytes=parent_a_bytes),
        LineageEdgeKind.DERIVED_FROM,
    )
    parent_b = LineageParentRef(
        compute_record_id(domain=IdentityDomain.PROPOSAL, canonical_bytes=parent_b_bytes),
        LineageEdgeKind.REFERENCES,
    )
    envelope = make_envelope(lineage=(parent_a, parent_b))
    transport = envelope.to_dict()
    parsed = CommonEnvelope.from_dict(
        transport,
        canonical_payload_bytes=PAYLOAD,
        lineage_parent_canonical_bytes=(parent_a_bytes, parent_b_bytes),
    )
    assert parsed.lineage_parent_ids == (parent_a, parent_b)
    assert type(parsed.lineage_parent_ids) is tuple
    assert [item["edge_kind"] for item in transport["lineage_parent_ids"]] == [
        "DERIVED_FROM",
        "REFERENCES",
    ]
    assert_failure(
        ContractFailureCode.DUPLICATE_LINEAGE_PARENT,
        lambda: make_envelope(lineage=(parent_a, replace(parent_a, edge_kind=LineageEdgeKind.SUPERSEDES))),
    )


def test_empty_exact_canonical_bytes_are_an_explicit_valid_input() -> None:
    envelope = make_envelope(payload=b"", identity_domain=IdentityDomain.EXECUTION)
    validate_common_envelope(envelope, canonical_payload_bytes=b"")
    assert envelope.payload_sha256 == hashlib.sha256(b"").hexdigest()


def test_independence_proof_round_trip_and_role_reason_matrix() -> None:
    proof = make_proof()
    parsed = independence_proof_from_dict(proof.to_dict(), proposal_canonical_bytes=PAYLOAD)
    assert parsed == proof
    assert type(parsed.producer_actor_ids) is tuple
    assert type(parsed.source_actor_ids) is tuple
    assert type(parsed.subject_derived_actor_ids) is tuple
    assert type(parsed.delegation_chain) is tuple
    assert_failure(
        ContractFailureCode.UNKNOWN_REASON_CODE,
        lambda: make_proof(reason_code=ReasonCode.PUBLICATION_REVIEW_INDEPENDENT),
    )


def test_duplicate_actor_ids_and_missing_actor_coverage_rejected() -> None:
    duplicate = (ActorIdentity("producer-a"), ActorIdentity("producer-a"))
    assert_failure(
        ContractFailureCode.DUPLICATE_ACTOR,
        lambda: make_proof(producer_actor_ids=duplicate),
    )
    assert_failure(
        ContractFailureCode.UNPROVEN_INDEPENDENCE,
        lambda: make_proof(source_actor_ids=()),
    )


def test_mutant_01_claim_is_not_trusted_record_id() -> None:
    claim = ClaimedRecordId(compute_record_id(
        domain=IdentityDomain.COMMON_RECORD,
        canonical_bytes=PAYLOAD,
    ).value)
    assert type(claim) is ClaimedRecordId
    assert_failure(
        ContractFailureCode.TYPE_MISMATCH,
        lambda: validate_record_id(claim, canonical_bytes=PAYLOAD),  # type: ignore[arg-type]
    )
    assert_failure(
        ContractFailureCode.TYPE_MISMATCH,
        lambda: LineageParentRef(claim, LineageEdgeKind.REFERENCES),  # type: ignore[arg-type]
    )


def test_mutant_02_domain_separator_is_in_hash_preimage() -> None:
    expected = "7af3a3aeedf70a8a3e937f45864fef590c5f5b50cba60a8c0c5b7750c0bfcc53"
    record_id = compute_record_id(domain=IdentityDomain.COMMON_RECORD, canonical_bytes=PAYLOAD)
    explicit_preimage = (
        b"synapse.stage4.record-id/v1\x00"
        + IdentityDomain.COMMON_RECORD.value.encode("utf-8")
        + b"\x00"
        + PAYLOAD
    )
    assert record_id.digest_sha256 == expected
    assert record_id.digest_sha256 == hashlib.sha256(explicit_preimage).hexdigest()
    assert record_id.digest_sha256 != hashlib.sha256(PAYLOAD).hexdigest()


def test_mutant_03_identity_uses_non_ascii_exact_bytes_without_implicit_json() -> None:
    raw = bytes.fromhex("d09fd180d0b8d0b2d0b5d1822c2053796e61707365")
    expected = "5c3ab697add2b0307db08d5563ace5dca976fc71eeceed19cc9d42692c7cbdd1"
    assert compute_record_id(domain=IdentityDomain.PROPOSAL, canonical_bytes=raw).digest_sha256 == expected
    for not_bytes in ("Привет, Synapse", {"text": "Привет"}, bytearray(raw), memoryview(raw)):
        assert_failure(
            ContractFailureCode.TYPE_MISMATCH,
            lambda not_bytes=not_bytes: compute_record_id(
                domain=IdentityDomain.PROPOSAL,
                canonical_bytes=not_bytes,  # type: ignore[arg-type]
            ),
        )


def test_mutant_04_payload_substitution_with_preserved_hash_and_id_rejected() -> None:
    envelope = make_envelope()
    original_transport = envelope.to_dict()
    assert original_transport["payload_sha256"] == envelope.payload_sha256
    assert_failure(
        ContractFailureCode.PAYLOAD_HASH_MISMATCH,
        lambda: validate_common_envelope(envelope, canonical_payload_bytes=b"substituted"),
    )


def test_mutant_05_unknown_schema_is_not_a_warning_or_default() -> None:
    transport = make_envelope().to_dict()
    transport["schema_version"] = "synapse.stage4.gold.common-envelope/v999"
    assert_failure(
        ContractFailureCode.UNKNOWN_SCHEMA_VERSION,
        lambda: CommonEnvelope.from_dict(transport, canonical_payload_bytes=PAYLOAD),
    )
    assert_failure(
        ContractFailureCode.UNKNOWN_SCHEMA_VERSION,
        lambda: create_common_envelope(
            schema_version="synapse.stage4.gold.common-envelope/v999",  # type: ignore[arg-type]
            identity_domain=IdentityDomain.COMMON_RECORD,
            canonical_payload_bytes=PAYLOAD,
            run_id=RunId("run-1"),
            attempt_id=AttemptId("attempt-1"),
            created_at_utc=CREATED_AT,
            producer_component="gold.contracts",
            repository_revision=RepositoryRevision.not_applicable(),
            policy_version="synapse.stage4.policy/v1",
            environment_profile_id="synapse.stage4.environment/v1",
            lineage_parent_ids=(),
        ),
    )


def test_mutant_06_unknown_authority_role_and_reason_are_not_strings() -> None:
    transport = make_proof().to_dict()
    transport["authority_role"] = "GENERIC_APPROVER"
    assert_failure(
        ContractFailureCode.UNKNOWN_AUTHORITY_ROLE,
        lambda: IndependenceProof.from_dict(transport, proposal_canonical_bytes=PAYLOAD),
    )
    transport = make_proof().to_dict()
    transport["reason_code"] = "looks independent to me"
    assert_failure(
        ContractFailureCode.UNKNOWN_REASON_CODE,
        lambda: IndependenceProof.from_dict(transport, proposal_canonical_bytes=PAYLOAD),
    )


def test_mutant_07_proposal_decision_execution_ids_are_not_interchangeable() -> None:
    proposal = compute_proposal_id(canonical_bytes=PAYLOAD)
    decision = compute_authority_decision_id(
        canonical_bytes=PAYLOAD,
        independence_proof=make_proof(),
    )
    execution = compute_execution_id(
        canonical_bytes=PAYLOAD,
        authority_decision_id=decision,
    )
    assert type(proposal) is ProposalId
    assert type(decision) is AuthorityDecisionId
    assert type(execution) is ExecutionId
    assert proposal.record_id.domain is IdentityDomain.PROPOSAL
    assert decision.record_id.domain is IdentityDomain.AUTHORITY_DECISION
    assert execution.record_id.domain is IdentityDomain.EXECUTION
    assert len({proposal.record_id.value, decision.record_id.value, execution.record_id.value}) == 3
    assert_failure(
        ContractFailureCode.TYPE_MISMATCH,
        lambda: compute_execution_id(
            canonical_bytes=b"execution",
            authority_decision_id=proposal,  # type: ignore[arg-type]
        ),
    )


@pytest.mark.parametrize("collision", ["producer", "source", "proposer", "executor"])
def test_mutant_08_authority_cannot_match_any_participating_actor(collision: str) -> None:
    actor = ActorIdentity("same-actor")
    overrides: dict[str, object] = {"authority_identity": AuthorityIdentity("same-actor")}
    if collision == "producer":
        overrides["producer_actor_ids"] = (actor,)
    elif collision == "source":
        overrides["source_actor_ids"] = (actor,)
    elif collision == "proposer":
        overrides["proposer_identity"] = actor
    else:
        overrides["executor_identity"] = actor
    assert_failure(ContractFailureCode.AUTHORITY_AMBIGUITY, lambda: make_proof(**overrides))


def test_mutant_09_authority_cannot_be_derived_from_subject() -> None:
    assert_failure(
        ContractFailureCode.AUTHORITY_DERIVED_FROM_SUBJECT,
        lambda: make_proof(subject_derived_actor_ids=(ActorIdentity("authority-a"),)),
    )


def test_mutant_10_proof_is_typed_and_revalidated_by_decision_consumer() -> None:
    for invalid in (None, True, False, "independent"):
        assert_failure(
            ContractFailureCode.UNPROVEN_INDEPENDENCE,
            lambda invalid=invalid: compute_authority_decision_id(
                canonical_bytes=b"decision",
                independence_proof=invalid,  # type: ignore[arg-type]
            ),
        )
    proof = make_proof()
    object.__setattr__(proof, "authority_identity", AuthorityIdentity("producer-a"))
    assert_failure(
        ContractFailureCode.AUTHORITY_AMBIGUITY,
        lambda: compute_authority_decision_id(
            canonical_bytes=b"decision",
            independence_proof=proof,
        ),
    )


@pytest.mark.parametrize("kind", ["cycle", "delegated_back"])
def test_mutant_11_circular_and_delegated_back_authority_rejected(kind: str) -> None:
    if kind == "cycle":
        chain = (
            DelegationStep(ActorIdentity("actor-a"), ActorIdentity("actor-b")),
            DelegationStep(ActorIdentity("actor-b"), ActorIdentity("actor-a")),
        )
        code = ContractFailureCode.DELEGATION_CYCLE
    else:
        chain = (
            DelegationStep(ActorIdentity("authority-a"), ActorIdentity("middle-a")),
            DelegationStep(ActorIdentity("middle-a"), ActorIdentity("proposer-a")),
        )
        code = ContractFailureCode.DELEGATED_BACK_AUTHORITY
    assert_failure(code, lambda: make_proof(delegation_chain=chain))


def test_mutant_12_identity_bound_collections_are_immutable_and_detached() -> None:
    transport = make_proof().to_dict()
    proof = IndependenceProof.from_dict(transport, proposal_canonical_bytes=PAYLOAD)
    transport["producer_actor_ids"].append({"value": "late-mutation"})
    transport["delegation_chain"].append(
        {"delegator": {"value": "late-a"}, "delegate": {"value": "late-b"}}
    )
    assert tuple(item.value for item in proof.producer_actor_ids) == ("producer-a",)
    assert proof.delegation_chain == ()
    detached = proof.to_dict()
    detached["source_actor_ids"].append({"value": "detached-mutation"})
    assert tuple(item.value for item in proof.source_actor_ids) == ("source-a",)
    assert_failure(
        ContractFailureCode.TYPE_MISMATCH,
        lambda: make_proof(producer_actor_ids=[ActorIdentity("producer-a")]),
    )


def test_mutant_13_direct_constructor_and_dataclasses_replace_cannot_create_trusted_objects() -> None:
    record_id = compute_record_id(domain=IdentityDomain.COMMON_RECORD, canonical_bytes=PAYLOAD)
    envelope = make_envelope()
    proof = make_proof()
    with pytest.raises(TypeError):
        RecordId(domain=IdentityDomain.COMMON_RECORD, digest_sha256="0" * 64)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        CommonEnvelope(  # type: ignore[call-arg]
            schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
            record_id=record_id,
        )
    with pytest.raises(TypeError):
        replace(record_id, digest_sha256="0" * 64)
    with pytest.raises(TypeError):
        replace(envelope, payload_sha256="0" * 64)
    with pytest.raises(TypeError):
        replace(proof, authority_identity=AuthorityIdentity("producer-a"))


def test_mutant_14_low_level_forged_trusted_objects_fail_serialization_and_use() -> None:
    forged_record = object.__new__(RecordId)
    object.__setattr__(forged_record, "domain", IdentityDomain.COMMON_RECORD)
    object.__setattr__(forged_record, "digest_sha256", hashlib.sha256(PAYLOAD).hexdigest())
    assert_failure(ContractFailureCode.TRUSTED_OBJECT_FORGED, forged_record.to_dict)
    assert_failure(
        ContractFailureCode.TRUSTED_OBJECT_FORGED,
        lambda: validate_record_id(forged_record, canonical_bytes=PAYLOAD),
    )

    valid = make_envelope()
    forged_envelope = object.__new__(CommonEnvelope)
    for name in (
        "schema_version",
        "record_id",
        "run_id",
        "attempt_id",
        "created_at_utc",
        "producer_component",
        "repository_revision",
        "policy_version",
        "environment_profile_id",
        "lineage_parent_ids",
        "payload_sha256",
    ):
        object.__setattr__(forged_envelope, name, getattr(valid, name))
    assert_failure(ContractFailureCode.TRUSTED_OBJECT_FORGED, forged_envelope.to_dict)
    assert_failure(
        ContractFailureCode.TRUSTED_OBJECT_FORGED,
        lambda: validate_common_envelope(forged_envelope, canonical_payload_bytes=PAYLOAD),
    )


def test_strict_typed_id_transport_factories_recompute_and_revalidate_dependencies() -> None:
    proof = make_proof()
    decision_bytes = b"authority-decision"
    decision = compute_authority_decision_id(
        canonical_bytes=decision_bytes,
        independence_proof=proof,
    )
    parsed_decision = authority_decision_id_from_dict(
        decision.to_dict(),
        canonical_bytes=decision_bytes,
        independence_proof=proof,
    )
    execution_bytes = b"execution"
    execution = compute_execution_id(
        canonical_bytes=execution_bytes,
        authority_decision_id=parsed_decision,
    )
    parsed_execution = execution_id_from_dict(
        execution.to_dict(),
        canonical_bytes=execution_bytes,
        authority_decision_id=parsed_decision,
    )
    assert parsed_decision == decision
    assert parsed_execution == execution


def test_contract_violation_detail_is_typed_and_does_not_echo_payload() -> None:
    secret_payload = b"secret-material-that-must-not-appear"
    record_id = compute_record_id(domain=IdentityDomain.COMMON_RECORD, canonical_bytes=b"other")
    error = assert_failure(
        ContractFailureCode.RECORD_ID_MISMATCH,
        lambda: validate_record_id(record_id, canonical_bytes=secret_payload),
    )
    assert "secret-material" not in str(error)
    assert error.failure_code is ContractFailureCode.RECORD_ID_MISMATCH
