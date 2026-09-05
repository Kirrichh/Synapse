"""Run-lifecycle records and their domain-separated identities.

This module owns record shape, validation and canonical identity only.
Orchestration, persistence, stop policy and external boundaries remain in their
respective owners.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from synapse.experiments.gold.canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
)
from synapse.experiments.gold.contracts import AttemptId, RunId
from synapse.experiments.gold.runner.vocabulary import (
    TERMINAL_DECISIONS,
    AttemptOutcome,
    FallbackPolicy,
    GoldRunFailureCode,
    GoldRunViolation,
    MechanismActivationStatus,
    RunFinalStatus,
    TelemetryCompleteness,
    TerminalDecisionKind,
    final_status_for_decision,
)


GOLD_RUN_MANIFEST_SCHEMA_V3 = "synapse.stage4.gold.run-manifest/v3"
GOLD_ATTEMPT_CONTEXT_SCHEMA_V2 = "synapse.stage4.gold.attempt-context/v2"
GOLD_ATTEMPT_CONTEXT_SCHEMA_V3 = "synapse.stage4.gold.attempt-context/v3"
GOLD_ATTEMPT_CONTEXT_SCHEMA_V4 = "synapse.stage4.gold.attempt-context/v4"
GOLD_ATTEMPT_RESULT_SCHEMA_V4 = "synapse.stage4.gold.attempt-result/v4"
GOLD_RUN_DECISION_SCHEMA_V2 = "synapse.stage4.gold.run-decision/v2"
GOLD_RUN_DECISION_SCHEMA_V3 = "synapse.stage4.gold.run-decision/v3"
GOLD_ATTEMPT_PREPARATION_FAILURE_SCHEMA_V1 = (
    "synapse.stage4.gold.attempt-preparation-failure/v1"
)
GOLD_RUN_RESULT_SCHEMA_V3 = "synapse.stage4.gold.run-result/v3"

_ZERO_DIGEST = "0" * 64
_C1_GOLD_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_GIT_REVISION_RE = re.compile(r"^[0-9a-f]{40,64}$")
_WORKER_CONTEXT_ID_RE = re.compile(r"^ctx_[0-9a-f]{64}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


def canonical_run_bytes(payload: dict[str, object]) -> bytes:
    return canonicalize_stage4_payload(
        payload,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _bounded(value: object, field_name: str, *, maximum: int) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise _fail(GoldRunFailureCode.BOUNDED_VALUE, f"{field_name} must be a bounded non-empty string")
    return value


def _digest(value: object, field_name: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, f"{field_name} must be a lowercase sha256 digest")
    return value


def _gold_run_id(value: object) -> str:
    if type(value) is not str or _C1_GOLD_RUN_ID_RE.fullmatch(value) is None:
        raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "gold_run_id must match the C1 boundary class")
    return value


def _positive_budget(value: object, field_name: str) -> int:
    if type(value) is not int or not 1 <= value <= 2**53:
        raise _fail(GoldRunFailureCode.CONFIG_INVALID, f"{field_name} must be a positive bounded integer")
    return value


def _artifact_ref(value: object, field_name: str) -> HashBoundRef:
    if type(value) is not HashBoundRef or value.kind is not RefKind.ARTIFACT:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{field_name} must be an exact artifact ref")
    return value


def _artifact_refs(values: object, field_name: str) -> tuple[HashBoundRef, ...]:
    if type(values) is not tuple:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{field_name} must be a tuple")
    refs = tuple(_artifact_ref(item, field_name) for item in values)
    keys = tuple(item.ref_id for item in refs)
    if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys) or len({item.sha256 for item in refs}) != len(refs):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, f"{field_name} must be ordered and unique")
    return refs


@dataclass(frozen=True)
class GoldRunBudgets:
    maximum_wall_clock_seconds: int
    maximum_worker_tokens: int
    replay_gas_budget: int
    replay_cognitive_budget: int

    def __post_init__(self) -> None:
        for name in (
            "maximum_wall_clock_seconds",
            "maximum_worker_tokens",
            "replay_gas_budget",
            "replay_cognitive_budget",
        ):
            _positive_budget(getattr(self, name), name)

    def to_dict(self) -> dict[str, int]:
        return {
            "maximum_wall_clock_seconds": self.maximum_wall_clock_seconds,
            "maximum_worker_tokens": self.maximum_worker_tokens,
            "replay_gas_budget": self.replay_gas_budget,
            "replay_cognitive_budget": self.replay_cognitive_budget,
        }


@dataclass(frozen=True)
class GoldReplicatePolicy:
    group_id: str
    replicate_count: int
    replicate_index: int

    def __post_init__(self) -> None:
        _bounded(self.group_id, "replicate group id", maximum=128)
        if type(self.replicate_count) is not int or not 1 <= self.replicate_count <= 128:
            raise _fail(GoldRunFailureCode.CONFIG_INVALID, "replicate_count must be within 1..128")
        if type(self.replicate_index) is not int or not 1 <= self.replicate_index <= self.replicate_count:
            raise _fail(GoldRunFailureCode.CONFIG_INVALID, "replicate_index must identify one configured replicate")

    def to_dict(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "replicate_count": self.replicate_count,
            "replicate_index": self.replicate_index,
        }


@dataclass(frozen=True)
class GoldRunVersions:
    specification_version: str
    specification_sha256: str
    implementation_revision: str
    policy_version: str
    policy_sha256: str

    def __post_init__(self) -> None:
        _bounded(self.specification_version, "specification_version", maximum=64)
        _digest(self.specification_sha256, "specification_sha256")
        if type(self.implementation_revision) is not str or _GIT_REVISION_RE.fullmatch(self.implementation_revision) is None:
            raise _fail(GoldRunFailureCode.CONFIG_INVALID, "implementation_revision must be a lowercase git hash")
        _bounded(self.policy_version, "policy_version", maximum=128)
        _digest(self.policy_sha256, "policy_sha256")

    def to_dict(self) -> dict[str, str]:
        return {
            "specification_version": self.specification_version,
            "specification_sha256": self.specification_sha256,
            "implementation_revision": self.implementation_revision,
            "policy_version": self.policy_version,
            "policy_sha256": self.policy_sha256,
        }


@dataclass(frozen=True)
class GoldRunConfig:
    task_id: str
    instance_id: str
    base_revision: str
    provider: str
    model: str
    oracle_name: str
    environment_kind: str
    budgets: GoldRunBudgets
    max_attempts: int
    replicate_policy: GoldReplicatePolicy
    fallback_policy: FallbackPolicy

    def __post_init__(self) -> None:
        if type(self.fallback_policy) is not FallbackPolicy:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "fallback_policy must be exact")
        if type(self.budgets) is not GoldRunBudgets or type(self.replicate_policy) is not GoldReplicatePolicy:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "config nested records must be exact")
        GoldRunBudgets(**self.budgets.__dict__)
        GoldReplicatePolicy(**self.replicate_policy.__dict__)
        for name in ("task_id", "instance_id", "provider", "model", "oracle_name"):
            _bounded(getattr(self, name), name, maximum=128)
        if type(self.environment_kind) is not str or not self.environment_kind or len(self.environment_kind) > 32:
            raise _fail(GoldRunFailureCode.CONFIG_INVALID, "environment_kind must be a bounded string")
        if type(self.base_revision) is not str or _GIT_REVISION_RE.fullmatch(self.base_revision) is None:
            raise _fail(GoldRunFailureCode.CONFIG_INVALID, "base_revision must be a lowercase git hash")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 16:
            raise _fail(GoldRunFailureCode.CONFIG_INVALID, "max_attempts must be within 1..16")

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "instance_id": self.instance_id,
            "base_revision": self.base_revision,
            "provider": self.provider,
            "model": self.model,
            "oracle_name": self.oracle_name,
            "environment_kind": self.environment_kind,
            "budgets": self.budgets.to_dict(),
            "max_attempts": self.max_attempts,
            "replicate_policy": self.replicate_policy.to_dict(),
            "fallback_policy": self.fallback_policy.value,
        }


@dataclass(frozen=True)
class GoldRunManifest:
    run_id: RunId
    gold_run_id: str
    config: GoldRunConfig
    versions: GoldRunVersions
    manifest_sha256: str
    inputs_sha256: str

    def __post_init__(self) -> None:
        if type(self.run_id) is not RunId or type(self.config) is not GoldRunConfig or type(self.versions) is not GoldRunVersions:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "manifest fields must be exact")
        _digest(self.inputs_sha256, "inputs_sha256")
        _gold_run_id(self.gold_run_id)
        GoldRunConfig(**self.config.__dict__)
        GoldRunVersions(**self.versions.__dict__)
        _digest(self.manifest_sha256, "manifest_sha256")

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": GOLD_RUN_MANIFEST_SCHEMA_V3,
            "run_id": self.run_id.to_dict(),
            "gold_run_id": self.gold_run_id,
            "config": self.config.to_dict(),
            "versions": self.versions.to_dict(),
            "inputs_sha256": self.inputs_sha256,
        }

    def stored_dict(self) -> dict[str, object]:
        return {"record_sha256": self.manifest_sha256, "payload": self.payload()}

    def canonical_bytes(self) -> bytes:
        return canonical_run_bytes(self.payload())

    @classmethod
    def create(cls, *, run_id: RunId, gold_run_id: str, config: GoldRunConfig, versions: GoldRunVersions, inputs_sha256: str) -> "GoldRunManifest":
        provisional = cls(run_id, gold_run_id, config, versions, _ZERO_DIGEST, inputs_sha256)
        return cls(run_id, gold_run_id, config, versions, hashlib.sha256(provisional.canonical_bytes()).hexdigest(), inputs_sha256)

    def validate_identity(self) -> None:
        if self.manifest_sha256 != hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest():
            raise _fail(GoldRunFailureCode.IDENTITY_MISMATCH, "manifest identity does not match frozen payload")


@dataclass(frozen=True)
class AttemptPhaseRefs:
    """Upstream identities; stable plan semantics become mandatory at context publication."""

    knowledge_snapshot_ref: HashBoundRef
    retrieval_ref: HashBoundRef
    replay_ref: HashBoundRef
    intent_ref: HashBoundRef
    plan_ref: HashBoundRef
    worker_context_id: str | None = None
    worker_context_audit_sha256: str | None = None
    knowledge_basis_sha256: str | None = None
    plan_semantic_sha256: str | None = None

    def __post_init__(self) -> None:
        if type(self.knowledge_snapshot_ref) is not HashBoundRef or self.knowledge_snapshot_ref.kind is not RefKind.KNOWLEDGE_SNAPSHOT:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "knowledge_snapshot_ref must be a knowledge-snapshot ref")
        for name in ("retrieval_ref", "replay_ref", "intent_ref", "plan_ref"):
            _artifact_ref(getattr(self, name), name)
        if self.plan_semantic_sha256 is not None:
            _digest(self.plan_semantic_sha256, "plan_semantic_sha256")
        if self.knowledge_basis_sha256 is not None:
            _digest(self.knowledge_basis_sha256, "knowledge_basis_sha256")
        if (self.worker_context_id is None) != (self.worker_context_audit_sha256 is None):
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "worker context identity and audit digest must be present or absent together")
        if self.worker_context_id is not None:
            if type(self.worker_context_id) is not str or _WORKER_CONTEXT_ID_RE.fullmatch(self.worker_context_id) is None:
                raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "worker_context_id must be exact")
            _digest(self.worker_context_audit_sha256, "worker_context_audit_sha256")

    def to_dict(self) -> dict[str, object]:
        return {
            "knowledge_snapshot_ref": self.knowledge_snapshot_ref.to_dict(),
            "retrieval_ref": self.retrieval_ref.to_dict(),
            "replay_ref": self.replay_ref.to_dict(),
            "intent_ref": self.intent_ref.to_dict(),
            "plan_ref": self.plan_ref.to_dict(),
            "plan_semantic_sha256": self.plan_semantic_sha256,
            "worker_context_id": self.worker_context_id,
            "worker_context_audit_sha256": self.worker_context_audit_sha256,
            "knowledge_basis_sha256": self.knowledge_basis_sha256,
        }


@dataclass(frozen=True)
class GoldAttemptContext:
    run_id: RunId
    gold_run_id: str
    attempt_index: int
    attempt_id: AttemptId
    phase_refs: AttemptPhaseRefs
    context_sha256: str

    def __post_init__(self) -> None:
        if type(self.run_id) is not RunId or type(self.attempt_id) is not AttemptId or type(self.phase_refs) is not AttemptPhaseRefs:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "attempt context fields must be exact")
        _gold_run_id(self.gold_run_id)
        if type(self.attempt_index) is not int or self.attempt_index < 1 or self.attempt_id.value != str(self.attempt_index):
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt identity is malformed")
        AttemptPhaseRefs(**self.phase_refs.__dict__)
        if self.phase_refs.plan_semantic_sha256 is None:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "durable attempt context requires stable plan semantics")
        _digest(self.phase_refs.plan_semantic_sha256, "plan_semantic_sha256")
        _digest(self.context_sha256, "context_sha256")

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": GOLD_ATTEMPT_CONTEXT_SCHEMA_V4,
            "run_id": self.run_id.to_dict(),
            "gold_run_id": self.gold_run_id,
            "attempt_index": self.attempt_index,
            "attempt_id": self.attempt_id.to_dict(),
            "phase_refs": self.phase_refs.to_dict(),
        }

    def stored_dict(self) -> dict[str, object]:
        return {"record_sha256": self.context_sha256, "payload": self.payload()}

    def canonical_bytes(self) -> bytes:
        return canonical_run_bytes(self.payload())

    @classmethod
    def create(cls, *, manifest: GoldRunManifest, attempt_index: int, phase_refs: AttemptPhaseRefs) -> "GoldAttemptContext":
        if type(attempt_index) is not int or not 1 <= attempt_index <= manifest.config.max_attempts:
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt_index is outside the configured run budget")
        provisional = cls(manifest.run_id, manifest.gold_run_id, attempt_index, AttemptId(str(attempt_index)), phase_refs, _ZERO_DIGEST)
        return cls(
            manifest.run_id,
            manifest.gold_run_id,
            attempt_index,
            AttemptId(str(attempt_index)),
            phase_refs,
            hashlib.sha256(provisional.canonical_bytes()).hexdigest(),
        )

    def validate_identity(self) -> None:
        if self.context_sha256 != hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest():
            raise _fail(GoldRunFailureCode.IDENTITY_MISMATCH, "attempt context identity does not match payload")


@dataclass(frozen=True)
class GoldAttemptResult:
    run_id: RunId
    gold_run_id: str
    attempt_index: int
    attempt_id: AttemptId
    outcome: AttemptOutcome
    c1_status: str | None
    oracle_invoked: bool
    oracle_resolved: bool | None
    worker_result_ref: HashBoundRef | None
    c1_result_ref: HashBoundRef | None
    oracle_result_ref: HashBoundRef | None
    publication_refs: tuple[HashBoundRef, ...]
    context_sha256: str
    result_sha256: str
    structured_outcome: dict[str, object]
    verified_finding_sha256: str | None = None
    verified_patch_sha256: str | None = None

    def __post_init__(self) -> None:
        if type(self.run_id) is not RunId or type(self.attempt_id) is not AttemptId or type(self.outcome) is not AttemptOutcome:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "attempt result fields must be exact")
        if type(self.structured_outcome) is not dict:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "attempt result requires a structured outcome")
        _gold_run_id(self.gold_run_id)
        if type(self.attempt_index) is not int or self.attempt_index < 1 or self.attempt_id.value != str(self.attempt_index):
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt identity is malformed")
        if self.c1_status is not None and (type(self.c1_status) is not str or not self.c1_status or len(self.c1_status) > 64):
            raise _fail(GoldRunFailureCode.BOUNDED_VALUE, "c1_status must be bounded or absent")
        if type(self.oracle_invoked) is not bool or (self.oracle_resolved is not None and type(self.oracle_resolved) is not bool):
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "oracle result fields are malformed")
        for name in ("worker_result_ref", "c1_result_ref", "oracle_result_ref"):
            value = getattr(self, name)
            if value is not None:
                _artifact_ref(value, name)
        _artifact_refs(self.publication_refs, "publication_refs")
        if self.verified_finding_sha256 is not None:
            _digest(self.verified_finding_sha256, "verified finding digest")
            _digest(self.verified_patch_sha256, "verified patch digest")
            if not self.oracle_invoked or self.oracle_resolved is None or self.c1_result_ref is None:
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "verified finding lacks oracle authority")
        elif self.verified_patch_sha256 is not None:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "verified patch lacks a checked finding")
        reached_no_c1 = (
            AttemptOutcome.CONTROLLER_INTERRUPTED,
            AttemptOutcome.DELIVERY_REFUSED,
            AttemptOutcome.DELIVERY_UNAVAILABLE,
        )
        if self.outcome in reached_no_c1:
            if (
                self.c1_status is not None
                or self.oracle_invoked is not False
                or self.oracle_resolved is not None
                or self.c1_result_ref is not None
                or self.oracle_result_ref is not None
                or self.publication_refs
            ):
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "pre-C1 result carries unreachable authority")
            if self.outcome in (AttemptOutcome.DELIVERY_REFUSED, AttemptOutcome.DELIVERY_UNAVAILABLE) and self.worker_result_ref is not None:
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "pre-C1 delivery failure cannot carry worker result")
        elif self.c1_status is None or self.worker_result_ref is None or self.c1_result_ref is None:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "C1-classified attempt requires worker and C1 authority")
        if not self.oracle_invoked and (self.oracle_resolved is not None or self.oracle_result_ref is not None):
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "oracle result exists without invocation")
        if self.oracle_invoked and self.oracle_result_ref is None and self.outcome is not AttemptOutcome.C1_RESULT_INVALID:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "invoked oracle requires its authority ref")
        if self.outcome is AttemptOutcome.RESOLVED and (self.oracle_invoked is not True or self.oracle_resolved is not True):
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "resolved attempt requires resolving oracle")
        _digest(self.context_sha256, "context_sha256")
        _digest(self.result_sha256, "result_sha256")

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": GOLD_ATTEMPT_RESULT_SCHEMA_V4,
            "run_id": self.run_id.to_dict(),
            "gold_run_id": self.gold_run_id,
            "attempt_index": self.attempt_index,
            "attempt_id": self.attempt_id.to_dict(),
            "outcome": self.outcome.value,
            "c1_status": self.c1_status,
            "oracle_invoked": self.oracle_invoked,
            "oracle_resolved": self.oracle_resolved,
            "worker_result_ref": None if self.worker_result_ref is None else self.worker_result_ref.to_dict(),
            "c1_result_ref": None if self.c1_result_ref is None else self.c1_result_ref.to_dict(),
            "oracle_result_ref": None if self.oracle_result_ref is None else self.oracle_result_ref.to_dict(),
            "publication_refs": [item.to_dict() for item in self.publication_refs],
            "verified_finding_sha256": self.verified_finding_sha256,
            "verified_patch_sha256": self.verified_patch_sha256,
            "structured_outcome": self.structured_outcome,
            "context_sha256": self.context_sha256,
        }

    def stored_dict(self) -> dict[str, object]:
        return {"record_sha256": self.result_sha256, "payload": self.payload()}

    def canonical_bytes(self) -> bytes:
        return canonical_run_bytes(self.payload())

    @classmethod
    def create(cls, **fields: object) -> "GoldAttemptResult":
        provisional = cls(result_sha256=_ZERO_DIGEST, **fields)
        return cls(result_sha256=hashlib.sha256(provisional.canonical_bytes()).hexdigest(), **fields)

    def validate_identity(self) -> None:
        if self.result_sha256 != hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest():
            raise _fail(GoldRunFailureCode.IDENTITY_MISMATCH, "attempt result identity does not match payload")


@dataclass(frozen=True)
class NextAttemptDecision:
    """Run-level decision bound to completed-attempt continuation evidence."""

    run_id: RunId
    gold_run_id: str
    attempt_index: int
    attempt_result_sha256: str
    decision: TerminalDecisionKind
    reason: str
    fallback_arm_id: str | None
    continuation_evidence_sha256: str
    decision_sha256: str

    def __post_init__(self) -> None:
        if type(self.run_id) is not RunId or type(self.decision) is not TerminalDecisionKind:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "decision fields must be exact")
        _gold_run_id(self.gold_run_id)
        if type(self.attempt_index) is not int or self.attempt_index < 1:
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt_index must be positive")
        _digest(self.attempt_result_sha256, "attempt_result_sha256")
        _digest(self.continuation_evidence_sha256, "continuation_evidence_sha256")
        _bounded(self.reason, "reason", maximum=128)
        if self.fallback_arm_id is not None:
            _bounded(self.fallback_arm_id, "fallback_arm_id", maximum=128)
            if self.decision is not TerminalDecisionKind.FALLBACK_BASELINE_EXPLICIT:
                raise _fail(GoldRunFailureCode.PHASE_INVALID, "fallback arm id requires explicit fallback")
        if self.decision is TerminalDecisionKind.FALLBACK_BASELINE_EXPLICIT and self.fallback_arm_id is None:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "explicit fallback requires arm identity")
        _digest(self.decision_sha256, "decision_sha256")

    @property
    def terminal(self) -> bool:
        return self.decision in TERMINAL_DECISIONS

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": GOLD_RUN_DECISION_SCHEMA_V3,
            "run_id": self.run_id.to_dict(),
            "gold_run_id": self.gold_run_id,
            "attempt_index": self.attempt_index,
            "attempt_result_sha256": self.attempt_result_sha256,
            "decision": self.decision.value,
            "reason": self.reason,
            "fallback_arm_id": self.fallback_arm_id,
            "continuation_evidence_sha256": self.continuation_evidence_sha256,
        }

    def stored_dict(self) -> dict[str, object]:
        return {"record_sha256": self.decision_sha256, "payload": self.payload()}

    def canonical_bytes(self) -> bytes:
        return canonical_run_bytes(self.payload())

    @classmethod
    def create(cls, **fields: object) -> "NextAttemptDecision":
        provisional = cls(decision_sha256=_ZERO_DIGEST, **fields)
        return cls(decision_sha256=hashlib.sha256(provisional.canonical_bytes()).hexdigest(), **fields)

    def validate_identity(self) -> None:
        if self.decision_sha256 != hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest():
            raise _fail(GoldRunFailureCode.IDENTITY_MISMATCH, "decision identity does not match payload")


@dataclass(frozen=True)
class AttemptPreparationFailure:
    """A target attempt that was authorised but could not establish its inputs.

    The preceding ``CONTINUE`` remains immutable history. This record binds the
    later preparation failure to that decision and carries the distinct
    run-level terminal authority without fabricating an attempt context/result.
    """

    run_id: RunId
    gold_run_id: str
    manifest_sha256: str
    target_attempt_index: int
    source_attempt_index: int | None
    source_attempt_result_sha256: str | None
    source_decision_sha256: str | None
    continuation_evidence_sha256: str | None
    terminal_decision: TerminalDecisionKind
    reason: str
    detail_code: str
    fallback_arm_id: str | None
    failure_sha256: str

    def __post_init__(self) -> None:
        if type(self.run_id) is not RunId or type(self.terminal_decision) is not TerminalDecisionKind:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "preparation failure fields must be exact")
        _gold_run_id(self.gold_run_id)
        _digest(self.manifest_sha256, "manifest_sha256")
        if type(self.target_attempt_index) is not int or self.target_attempt_index < 1:
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "target attempt index must be positive")
        source_digests = (
            self.source_attempt_result_sha256,
            self.source_decision_sha256,
            self.continuation_evidence_sha256,
        )
        if self.source_attempt_index is None:
            if any(item is not None for item in source_digests) or self.target_attempt_index != 1:
                raise _fail(
                    GoldRunFailureCode.AUTHORITY_MISMATCH,
                    "initial preparation failure cannot name predecessor authority",
                )
        else:
            if (
                type(self.source_attempt_index) is not int
                or self.source_attempt_index < 1
                or self.target_attempt_index != self.source_attempt_index + 1
                or any(item is None for item in source_digests)
            ):
                raise _fail(
                    GoldRunFailureCode.AUTHORITY_MISMATCH,
                    "continued preparation failure lacks exact predecessor authority",
                )
            _digest(self.source_attempt_result_sha256, "source_attempt_result_sha256")
            _digest(self.source_decision_sha256, "source_decision_sha256")
            _digest(self.continuation_evidence_sha256, "continuation_evidence_sha256")
        if self.terminal_decision not in (
            TerminalDecisionKind.STOP_UNRECOVERABLE,
            TerminalDecisionKind.STOP_LIMIT,
            TerminalDecisionKind.FALLBACK_BASELINE_EXPLICIT,
        ):
            raise _fail(
                GoldRunFailureCode.PHASE_INVALID,
                "preparation failure requires unavailable or explicit fallback decision",
            )
        _bounded(self.reason, "reason", maximum=128)
        _bounded(self.detail_code, "detail_code", maximum=128)
        if self.terminal_decision is TerminalDecisionKind.FALLBACK_BASELINE_EXPLICIT:
            if self.fallback_arm_id is None:
                raise _fail(GoldRunFailureCode.PHASE_INVALID, "explicit fallback requires arm identity")
            _bounded(self.fallback_arm_id, "fallback_arm_id", maximum=128)
        elif self.fallback_arm_id is not None:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "unavailable Gold result cannot name fallback arm")
        _digest(self.failure_sha256, "failure_sha256")

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": GOLD_ATTEMPT_PREPARATION_FAILURE_SCHEMA_V1,
            "run_id": self.run_id.to_dict(),
            "gold_run_id": self.gold_run_id,
            "manifest_sha256": self.manifest_sha256,
            "target_attempt_index": self.target_attempt_index,
            "source_attempt_index": self.source_attempt_index,
            "source_attempt_result_sha256": self.source_attempt_result_sha256,
            "source_decision_sha256": self.source_decision_sha256,
            "continuation_evidence_sha256": self.continuation_evidence_sha256,
            "terminal_decision": self.terminal_decision.value,
            "reason": self.reason,
            "detail_code": self.detail_code,
            "fallback_arm_id": self.fallback_arm_id,
        }

    def stored_dict(self) -> dict[str, object]:
        return {"record_sha256": self.failure_sha256, "payload": self.payload()}

    def canonical_bytes(self) -> bytes:
        return canonical_run_bytes(self.payload())

    @classmethod
    def create(cls, **fields: object) -> "AttemptPreparationFailure":
        provisional = cls(failure_sha256=_ZERO_DIGEST, **fields)
        return cls(failure_sha256=hashlib.sha256(provisional.canonical_bytes()).hexdigest(), **fields)

    def validate_identity(self) -> None:
        if self.failure_sha256 != hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest():
            raise _fail(
                GoldRunFailureCode.IDENTITY_MISMATCH,
                "preparation failure identity does not match payload",
            )


@dataclass(frozen=True)
class AttemptSummary:
    attempt_index: int
    attempt_id: str
    outcome: AttemptOutcome
    c1_status: str | None
    result_sha256: str

    def __post_init__(self) -> None:
        if type(self.attempt_index) is not int or self.attempt_index < 1:
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt index must be positive")
        if type(self.attempt_id) is not str or self.attempt_id != str(self.attempt_index):
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt id must match index")
        if type(self.outcome) is not AttemptOutcome:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "summary outcome must be exact")
        if self.c1_status is not None and (type(self.c1_status) is not str or not self.c1_status or len(self.c1_status) > 64):
            raise _fail(GoldRunFailureCode.BOUNDED_VALUE, "summary c1_status must be bounded")
        _digest(self.result_sha256, "result_sha256")

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt_index": self.attempt_index,
            "attempt_id": self.attempt_id,
            "outcome": self.outcome.value,
            "c1_status": self.c1_status,
            "result_sha256": self.result_sha256,
        }


@dataclass(frozen=True)
class GoldRunResult:
    run_id: RunId
    gold_run_id: str
    manifest_sha256: str
    final_status: RunFinalStatus
    terminal_decision: TerminalDecisionKind
    terminal_decision_sha256: str
    attempts: tuple[AttemptSummary, ...]
    resolved_attempt_index: int | None
    fallback_arm_id: str | None
    telemetry_completeness: TelemetryCompleteness
    telemetry_refs: tuple[HashBoundRef, ...]
    mechanism_activation: MechanismActivationStatus
    mechanism_activation_refs: tuple[HashBoundRef, ...]
    result_sha256: str
    structured_outcome: dict[str, object]

    def __post_init__(self) -> None:
        if type(self.run_id) is not RunId or type(self.final_status) is not RunFinalStatus:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run result fields must be exact")
        if type(self.structured_outcome) is not dict:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run result requires a structured outcome")
        _gold_run_id(self.gold_run_id)
        if type(self.terminal_decision) is not TerminalDecisionKind or self.terminal_decision not in TERMINAL_DECISIONS:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "run result requires terminal decision")
        if self.final_status is not final_status_for_decision(self.terminal_decision):
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "run status differs from terminal decision")
        _digest(self.terminal_decision_sha256, "terminal_decision_sha256")
        if type(self.attempts) is not tuple:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "attempts must be a tuple")
        summaries = tuple(AttemptSummary(**item.__dict__) for item in self.attempts)
        indexes = [item.attempt_index for item in summaries]
        if indexes != list(range(1, len(indexes) + 1)):
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "run attempts must be a gapless prefix")
        if not indexes and self.final_status not in (
            RunFinalStatus.GOLD_UNAVAILABLE,
            RunFinalStatus.BASELINE_FALLBACK_EXPLICIT,
        ):
            raise _fail(
                GoldRunFailureCode.PHASE_INVALID,
                "only pre-attempt unavailability may finish without attempt records",
            )
        _digest(self.manifest_sha256, "manifest_sha256")
        resolved = [item for item in summaries if item.outcome is AttemptOutcome.RESOLVED]
        if self.terminal_decision is TerminalDecisionKind.STOP_SUCCESS:
            if len(resolved) != 1 or resolved[0].attempt_index != indexes[-1] or self.resolved_attempt_index != indexes[-1]:
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "STOP_SUCCESS requires terminal attempt resolved")
        elif resolved or self.resolved_attempt_index is not None:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "non-success result cannot carry resolved attempt")
        if self.final_status is RunFinalStatus.BASELINE_FALLBACK_EXPLICIT:
            if self.terminal_decision is not TerminalDecisionKind.FALLBACK_BASELINE_EXPLICIT or self.fallback_arm_id is None:
                raise _fail(GoldRunFailureCode.PHASE_INVALID, "explicit fallback requires fallback decision and arm")
        elif self.fallback_arm_id is not None:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "fallback arm exists without explicit fallback")
        if type(self.telemetry_completeness) is not TelemetryCompleteness:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "telemetry completeness must be exact")
        telemetry_refs = _artifact_refs(self.telemetry_refs, "telemetry_refs")
        if self.telemetry_completeness is TelemetryCompleteness.UNAVAILABLE:
            if telemetry_refs:
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "unavailable telemetry cannot carry refs")
        elif not telemetry_refs:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "evaluated telemetry requires refs")
        if type(self.mechanism_activation) is not MechanismActivationStatus:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "mechanism activation must be exact")
        activation_refs = _artifact_refs(self.mechanism_activation_refs, "mechanism_activation_refs")
        if self.mechanism_activation is MechanismActivationStatus.NOT_EVALUATED:
            if activation_refs:
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "unevaluated activation cannot carry refs")
        elif not activation_refs:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "activation conclusion requires refs")
        _digest(self.result_sha256, "result_sha256")

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": GOLD_RUN_RESULT_SCHEMA_V3,
            "run_id": self.run_id.to_dict(),
            "gold_run_id": self.gold_run_id,
            "manifest_sha256": self.manifest_sha256,
            "final_status": self.final_status.value,
            "structured_outcome": self.structured_outcome,
            "terminal_decision": self.terminal_decision.value,
            "terminal_decision_sha256": self.terminal_decision_sha256,
            "attempts": [item.to_dict() for item in self.attempts],
            "resolved_attempt_index": self.resolved_attempt_index,
            "fallback_arm_id": self.fallback_arm_id,
            "telemetry_completeness": self.telemetry_completeness.value,
            "telemetry_refs": [item.to_dict() for item in self.telemetry_refs],
            "mechanism_activation": self.mechanism_activation.value,
            "mechanism_activation_refs": [item.to_dict() for item in self.mechanism_activation_refs],
        }

    def stored_dict(self) -> dict[str, object]:
        return {"record_sha256": self.result_sha256, "payload": self.payload()}

    def canonical_bytes(self) -> bytes:
        return canonical_run_bytes(self.payload())

    @classmethod
    def create(cls, **fields: object) -> "GoldRunResult":
        provisional = cls(result_sha256=_ZERO_DIGEST, **fields)
        return cls(result_sha256=hashlib.sha256(provisional.canonical_bytes()).hexdigest(), **fields)

    def validate_identity(self) -> None:
        if self.result_sha256 != hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest():
            raise _fail(GoldRunFailureCode.IDENTITY_MISMATCH, "run result identity does not match payload")
