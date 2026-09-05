"""Strict canonical transport for Stage 10 intent proposals."""

from __future__ import annotations

from ..canonicalization import HashBoundRef
from ..contracts import ActorIdentity
from .context_codec import decode_canonical, encode_canonical
from .intent import (
    AcceptanceCriterion,
    EffectConstraint,
    ExecutionFeedback,
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
        "execution_feedback",
        "task_contract_ref",
        "target_bindings",
        "behavior_refs",
    }
    if type(payload) is not dict or set(payload) != required:
        raise IntentViolation(IntentFailureCode.UNKNOWN_FIELD, "intent payload has an unknown shape")
    sources = payload["source_actors"]
    capabilities = payload["required_capabilities"]
    effects = payload["effects"]
    acceptance = payload["acceptance"]
    uncertainties = payload["uncertainties"]
    feedback = payload["execution_feedback"]
    if any(type(item) is not list for item in (sources, capabilities, effects, acceptance, uncertainties, feedback)):
        raise IntentViolation(IntentFailureCode.TYPE_MISMATCH, "intent collections must be transport lists")
    try:
        parsed_effects = tuple(EffectConstraint.from_dict(item) for item in effects)
        parsed_acceptance = tuple(AcceptanceCriterion.from_dict(item) for item in acceptance)
        if type(payload["target_bindings"]) is not list or type(payload["behavior_refs"]) is not list:
            raise ValueError("intent subjects must be lists")
    except (TypeError, ValueError) as exc:
        raise IntentViolation(IntentFailureCode.TYPE_MISMATCH, "intent constraints are invalid") from exc
    try:
        result = propose_intent(
            proposer=ActorIdentity.from_dict(payload["proposer"]),
            source_actors=tuple(ActorIdentity.from_dict(item) for item in sources),
            task_statement=payload["task_statement"],
            task_contract_ref=HashBoundRef.from_dict(payload["task_contract_ref"]),
            target_bindings=tuple(HashBoundRef.from_dict(item) for item in payload["target_bindings"]),
            behavior_refs=tuple(HashBoundRef.from_dict(item) for item in payload["behavior_refs"]),
            repository_revision_sha256=payload["repository_revision_sha256"],
            knowledge_snapshot_ref=HashBoundRef.from_dict(payload["knowledge_snapshot_ref"]),
            allowed_scope=RepositoryScope.from_dict(payload["allowed_scope"]),
            required_capabilities=tuple(capabilities),
            effects=tuple(parsed_effects),
            acceptance=tuple(parsed_acceptance),
            uncertainties=tuple(uncertainties),
            execution_feedback=tuple(ExecutionFeedback.from_dict(item) for item in feedback),
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
