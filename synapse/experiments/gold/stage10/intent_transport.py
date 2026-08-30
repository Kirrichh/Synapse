"""Strict canonical transport for Stage 10 intent proposals."""

from __future__ import annotations

from ..canonicalization import HashBoundRef
from ..contracts import ActorIdentity
from .context_codec import decode_canonical, encode_canonical
from .intent import (
    AcceptanceCriterion,
    AcceptanceKind,
    EffectConstraint,
    EffectDisposition,
    EffectKind,
    IntentCandidate,
    IntentFailureCode,
    IntentViolation,
    propose_intent,
)
from .repository_scope import RepositoryScope


def encode_intent_candidate(value: IntentCandidate) -> bytes:
    return encode_canonical(value.to_dict())


def decode_intent_candidate(value: object) -> IntentCandidate:
    decoded = decode_canonical(value)
    if type(decoded) is not dict or set(decoded) != {"proposal_id", "payload"}:
        raise IntentViolation(IntentFailureCode.UNKNOWN_FIELD, "intent transport has an unknown shape")
    payload = decoded["payload"]
    required = {
        "schema_version",
        "proposer",
        "source_actors",
        "task_statement",
        "repository_revision_sha256",
        "knowledge_snapshot_ref",
        "allowed_scope",
        "required_capabilities",
        "effects",
        "acceptance",
        "uncertainties",
    }
    if type(payload) is not dict or set(payload) != required:
        raise IntentViolation(IntentFailureCode.UNKNOWN_FIELD, "intent payload has an unknown shape")
    sources = payload["source_actors"]
    capabilities = payload["required_capabilities"]
    effects = payload["effects"]
    acceptance = payload["acceptance"]
    uncertainties = payload["uncertainties"]
    if any(type(item) is not list for item in (sources, capabilities, effects, acceptance, uncertainties)):
        raise IntentViolation(IntentFailureCode.TYPE_MISMATCH, "intent collections must be transport lists")
    parsed_effects: list[EffectConstraint] = []
    for item in effects:
        if type(item) is not dict or set(item) != {
            "constraint_id",
            "disposition",
            "kind",
            "subject_path",
            "verification_ref",
        }:
            raise IntentViolation(IntentFailureCode.UNKNOWN_FIELD, "effect transport has an unknown shape")
        try:
            parsed_effects.append(
                EffectConstraint(
                    constraint_id=item["constraint_id"],
                    disposition=EffectDisposition(item["disposition"]),
                    kind=EffectKind(item["kind"]),
                    subject_path=item["subject_path"],
                    verification_ref=HashBoundRef.from_dict(item["verification_ref"]),
                )
            )
        except (TypeError, ValueError) as exc:
            raise IntentViolation(IntentFailureCode.TYPE_MISMATCH, "effect transport is invalid") from exc
    parsed_acceptance: list[AcceptanceCriterion] = []
    for item in acceptance:
        if type(item) is not dict or set(item) != {"criterion_id", "kind", "condition_ref", "argv"}:
            raise IntentViolation(IntentFailureCode.UNKNOWN_FIELD, "acceptance transport has an unknown shape")
        if type(item["argv"]) is not list:
            raise IntentViolation(IntentFailureCode.TYPE_MISMATCH, "acceptance argv must be a list")
        try:
            parsed_acceptance.append(
                AcceptanceCriterion(
                    criterion_id=item["criterion_id"],
                    kind=AcceptanceKind(item["kind"]),
                    condition_ref=HashBoundRef.from_dict(item["condition_ref"]),
                    argv=tuple(item["argv"]),
                )
            )
        except (TypeError, ValueError) as exc:
            raise IntentViolation(IntentFailureCode.TYPE_MISMATCH, "acceptance transport is invalid") from exc
    try:
        result = propose_intent(
            proposer=ActorIdentity.from_dict(payload["proposer"]),
            source_actors=tuple(ActorIdentity.from_dict(item) for item in sources),
            task_statement=payload["task_statement"],
            repository_revision_sha256=payload["repository_revision_sha256"],
            knowledge_snapshot_ref=HashBoundRef.from_dict(payload["knowledge_snapshot_ref"]),
            allowed_scope=RepositoryScope.from_dict(payload["allowed_scope"]),
            required_capabilities=tuple(capabilities),
            effects=tuple(parsed_effects),
            acceptance=tuple(parsed_acceptance),
            uncertainties=tuple(uncertainties),
        )
    except IntentViolation:
        raise
    except (TypeError, ValueError) as exc:
        raise IntentViolation(IntentFailureCode.TYPE_MISMATCH, "intent transport is invalid") from exc
    if result.proposal_id.to_dict() != decoded["proposal_id"]:
        raise IntentViolation(IntentFailureCode.IDENTITY_MISMATCH, "transport intent id differs")
    if encode_intent_candidate(result) != value:
        raise IntentViolation(IntentFailureCode.IDENTITY_MISMATCH, "intent bytes do not round-trip")
    return result
