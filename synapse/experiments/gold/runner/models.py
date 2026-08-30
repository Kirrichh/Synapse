"""The six §26 run-lifecycle records and their domain-separated identities.

§26 names exactly six records — config, manifest, attempt context, attempt
result, next-attempt decision and run result — and this module owns their shape,
their validation and the hash that identifies each one. It owns nothing else:
orchestration is ``controller.py``, durable bytes are ``records.py``, the stop
rule is ``stop_policy.py`` and every closed enum is ``vocabulary.py``.

Two properties carry the §26 argument and both are enforced here rather than by
the caller. Identity is computed from the canonical payload, so a record whose
fields were edited after construction fails ``validate_identity`` instead of
travelling as a different record under the same digest. And an attempt result
records only the controller's own classification plus the C1 status *label*:
evidence, oracle output and C1 payloads stay inside the C1 boundary, because a
controller that copies them becomes a second place where they can disagree.
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
    RunFinalStatus,
    TerminalDecisionKind,
)


GOLD_RUN_MANIFEST_SCHEMA_V1 = "synapse.stage4.gold.run-manifest/v1"
GOLD_ATTEMPT_CONTEXT_SCHEMA_V1 = "synapse.stage4.gold.attempt-context/v1"
GOLD_ATTEMPT_RESULT_SCHEMA_V1 = "synapse.stage4.gold.attempt-result/v1"
GOLD_RUN_DECISION_SCHEMA_V1 = "synapse.stage4.gold.run-decision/v1"
GOLD_RUN_RESULT_SCHEMA_V1 = "synapse.stage4.gold.run-result/v1"

_ZERO_DIGEST = "0" * 64

#: The C1 boundary accepts gold_run_id values matching exactly this class.
#: Kept as a local copy because the record model must stay free of any swebench
#: import; ``c1_boundary.py`` re-checks it against the C1 validator.
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

@dataclass(frozen=True)
class GoldRunConfig:
    """Frozen per-run inputs; identical for every attempt of one run."""

    task_id: str
    instance_id: str
    base_revision: str
    provider: str
    model: str
    oracle_name: str
    environment_kind: str
    max_attempts: int
    fallback_policy: FallbackPolicy

    def __post_init__(self) -> None:
        if type(self.fallback_policy) is not FallbackPolicy:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "fallback_policy must be exact")
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
            "max_attempts": self.max_attempts,
            "fallback_policy": self.fallback_policy.value,
        }


@dataclass(frozen=True)
class GoldRunManifest:
    """Run identity over frozen specification; persisted before any attempt."""

    run_id: RunId
    gold_run_id: str
    config: GoldRunConfig
    manifest_sha256: str

    def __post_init__(self) -> None:
        if type(self.run_id) is not RunId:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run_id must be exact")
        if type(self.gold_run_id) is not str or _C1_GOLD_RUN_ID_RE.fullmatch(self.gold_run_id) is None:
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "gold_run_id must match the C1 boundary class")
        if type(self.config) is not GoldRunConfig:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "config must be exact")
        GoldRunConfig(**self.config.__dict__)
        _digest(self.manifest_sha256, "manifest_sha256")

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": GOLD_RUN_MANIFEST_SCHEMA_V1,
            "run_id": self.run_id.to_dict(),
            "gold_run_id": self.gold_run_id,
            "config": self.config.to_dict(),
        }

    def stored_dict(self) -> dict[str, object]:
        return {"record_sha256": self.manifest_sha256, "payload": self.payload()}

    def canonical_bytes(self) -> bytes:
        return canonical_run_bytes(self.payload())

    @classmethod
    def create(cls, *, run_id: RunId, gold_run_id: str, config: GoldRunConfig) -> "GoldRunManifest":
        provisional = cls(run_id=run_id, gold_run_id=gold_run_id, config=config, manifest_sha256=_ZERO_DIGEST)
        digest = hashlib.sha256(provisional.canonical_bytes()).hexdigest()
        return cls(run_id=run_id, gold_run_id=gold_run_id, config=config, manifest_sha256=digest)

    def validate_identity(self) -> None:
        expected = hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest()
        if self.manifest_sha256 != expected:
            raise _fail(GoldRunFailureCode.IDENTITY_MISMATCH, "manifest identity does not match frozen payload")


@dataclass(frozen=True)
class AttemptPhaseRefs:
    """Upstream Stage 4 phase identities bound into one attempt context."""

    knowledge_snapshot_ref: HashBoundRef
    retrieval_ref: HashBoundRef
    replay_ref: HashBoundRef
    intent_ref: HashBoundRef
    plan_ref: HashBoundRef
    worker_context_id: str
    worker_context_audit_sha256: str

    def __post_init__(self) -> None:
        if type(self.knowledge_snapshot_ref) is not HashBoundRef or self.knowledge_snapshot_ref.kind is not RefKind.KNOWLEDGE_SNAPSHOT:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "knowledge_snapshot_ref must be a knowledge-snapshot ref")
        for name in ("retrieval_ref", "replay_ref", "intent_ref", "plan_ref"):
            value = getattr(self, name)
            if type(value) is not HashBoundRef or value.kind is not RefKind.ARTIFACT:
                raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be an artifact ref")
        if type(self.worker_context_id) is not str or _WORKER_CONTEXT_ID_RE.fullmatch(self.worker_context_id) is None:
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "worker_context_id must be an exact typed context id")
        _digest(self.worker_context_audit_sha256, "worker_context_audit_sha256")

    def to_dict(self) -> dict[str, object]:
        return {
            "knowledge_snapshot_ref": self.knowledge_snapshot_ref.to_dict(),
            "retrieval_ref": self.retrieval_ref.to_dict(),
            "replay_ref": self.replay_ref.to_dict(),
            "intent_ref": self.intent_ref.to_dict(),
            "plan_ref": self.plan_ref.to_dict(),
            "worker_context_id": self.worker_context_id,
            "worker_context_audit_sha256": self.worker_context_audit_sha256,
        }


@dataclass(frozen=True)
class GoldAttemptContext:
    """Immutable per-attempt context; a new identity for every attempt."""

    run_id: RunId
    gold_run_id: str
    attempt_index: int
    attempt_id: AttemptId
    phase_refs: AttemptPhaseRefs
    context_sha256: str

    def __post_init__(self) -> None:
        if type(self.run_id) is not RunId or type(self.attempt_id) is not AttemptId:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "attempt identities must be exact")
        if type(self.attempt_index) is not int or self.attempt_index < 1:
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt_index must be a positive integer")
        if self.attempt_id.value != str(self.attempt_index):
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt_id must equal the decimal attempt index")
        if type(self.phase_refs) is not AttemptPhaseRefs:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "phase_refs must be exact")
        AttemptPhaseRefs(**self.phase_refs.__dict__)
        _digest(self.context_sha256, "context_sha256")

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": GOLD_ATTEMPT_CONTEXT_SCHEMA_V1,
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
    def create(
        cls,
        *,
        manifest: GoldRunManifest,
        attempt_index: int,
        phase_refs: AttemptPhaseRefs,
    ) -> "GoldAttemptContext":
        if type(attempt_index) is not int or not 1 <= attempt_index <= manifest.config.max_attempts:
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt_index is outside the configured run budget")
        provisional = cls(
            run_id=manifest.run_id,
            gold_run_id=manifest.gold_run_id,
            attempt_index=attempt_index,
            attempt_id=AttemptId(str(attempt_index)),
            phase_refs=phase_refs,
            context_sha256=_ZERO_DIGEST,
        )
        digest = hashlib.sha256(provisional.canonical_bytes()).hexdigest()
        return cls(
            run_id=manifest.run_id,
            gold_run_id=manifest.gold_run_id,
            attempt_index=attempt_index,
            attempt_id=AttemptId(str(attempt_index)),
            phase_refs=phase_refs,
            context_sha256=digest,
        )

    def validate_identity(self) -> None:
        expected = hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest()
        if self.context_sha256 != expected:
            raise _fail(GoldRunFailureCode.IDENTITY_MISMATCH, "attempt context identity does not match payload")


@dataclass(frozen=True)
class GoldAttemptResult:
    """Controller record for one finished (or interrupted) attempt.

    The controller records only its own classification and the C1 status
    label. C1 payloads, evidence and oracle outputs stay inside the C1
    boundary; the controller never copies or restates them.
    """

    run_id: RunId
    gold_run_id: str
    attempt_index: int
    attempt_id: AttemptId
    outcome: AttemptOutcome
    c1_status: str | None
    oracle_invoked: bool
    oracle_resolved: bool | None
    context_sha256: str
    result_sha256: str

    def __post_init__(self) -> None:
        if type(self.run_id) is not RunId or type(self.attempt_id) is not AttemptId:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "attempt identities must be exact")
        if type(self.outcome) is not AttemptOutcome:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "outcome must be exact")
        if type(self.attempt_index) is not int or self.attempt_index < 1:
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt_index must be a positive integer")
        if self.attempt_id.value != str(self.attempt_index):
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt_id must equal the decimal attempt index")
        if self.c1_status is not None and (type(self.c1_status) is not str or not self.c1_status or len(self.c1_status) > 64):
            raise _fail(GoldRunFailureCode.BOUNDED_VALUE, "c1_status must be a bounded string or None")
        if type(self.oracle_invoked) is not bool:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "oracle_invoked must be exact bool")
        if self.oracle_resolved is not None and type(self.oracle_resolved) is not bool:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "oracle_resolved must be exact bool or None")
        reached_no_c1 = (AttemptOutcome.CONTROLLER_INTERRUPTED, AttemptOutcome.DELIVERY_REFUSED)
        if self.outcome in reached_no_c1 and self.c1_status is not None:
            raise _fail(
                GoldRunFailureCode.PHASE_INVALID,
                "an attempt that never reached C1 carries no C1 status",
            )
        _digest(self.context_sha256, "context_sha256")
        _digest(self.result_sha256, "result_sha256")

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": GOLD_ATTEMPT_RESULT_SCHEMA_V1,
            "run_id": self.run_id.to_dict(),
            "gold_run_id": self.gold_run_id,
            "attempt_index": self.attempt_index,
            "attempt_id": self.attempt_id.to_dict(),
            "outcome": self.outcome.value,
            "c1_status": self.c1_status,
            "oracle_invoked": self.oracle_invoked,
            "oracle_resolved": self.oracle_resolved,
            "context_sha256": self.context_sha256,
        }

    def stored_dict(self) -> dict[str, object]:
        return {"record_sha256": self.result_sha256, "payload": self.payload()}

    def canonical_bytes(self) -> bytes:
        return canonical_run_bytes(self.payload())

    @classmethod
    def create(cls, **fields: object) -> "GoldAttemptResult":
        provisional = cls(result_sha256=_ZERO_DIGEST, **fields)  # type: ignore[arg-type]
        digest = hashlib.sha256(provisional.canonical_bytes()).hexdigest()
        return cls(result_sha256=digest, **fields)  # type: ignore[arg-type]

    def validate_identity(self) -> None:
        expected = hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest()
        if self.result_sha256 != expected:
            raise _fail(GoldRunFailureCode.IDENTITY_MISMATCH, "attempt result identity does not match payload")


@dataclass(frozen=True)
class NextAttemptDecision:
    """Reasoned continue/stop/fallback decision recorded after an attempt."""

    run_id: RunId
    attempt_index: int
    decision: TerminalDecisionKind
    reason: str
    fallback_arm_id: str | None
    decision_sha256: str

    def __post_init__(self) -> None:
        if type(self.run_id) is not RunId or type(self.decision) is not TerminalDecisionKind:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "decision fields must be exact")
        if type(self.attempt_index) is not int or self.attempt_index < 0:
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt_index must be zero or a positive integer")
        _bounded(self.reason, "reason", maximum=128)
        if self.fallback_arm_id is not None:
            _bounded(self.fallback_arm_id, "fallback_arm_id", maximum=128)
            if self.decision is not TerminalDecisionKind.FALLBACK_BASELINE_EXPLICIT:
                raise _fail(GoldRunFailureCode.PHASE_INVALID, "fallback arm id requires an explicit fallback decision")
        if self.decision is TerminalDecisionKind.FALLBACK_BASELINE_EXPLICIT and self.fallback_arm_id is None:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "explicit fallback requires a new arm identity")
        _digest(self.decision_sha256, "decision_sha256")

    @property
    def terminal(self) -> bool:
        return self.decision in TERMINAL_DECISIONS

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": GOLD_RUN_DECISION_SCHEMA_V1,
            "run_id": self.run_id.to_dict(),
            "attempt_index": self.attempt_index,
            "decision": self.decision.value,
            "reason": self.reason,
            "fallback_arm_id": self.fallback_arm_id,
        }

    def stored_dict(self) -> dict[str, object]:
        return {"record_sha256": self.decision_sha256, "payload": self.payload()}

    def canonical_bytes(self) -> bytes:
        return canonical_run_bytes(self.payload())

    @classmethod
    def create(cls, **fields: object) -> "NextAttemptDecision":
        provisional = cls(decision_sha256=_ZERO_DIGEST, **fields)  # type: ignore[arg-type]
        digest = hashlib.sha256(provisional.canonical_bytes()).hexdigest()
        return cls(decision_sha256=digest, **fields)  # type: ignore[arg-type]

    def validate_identity(self) -> None:
        expected = hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest()
        if self.decision_sha256 != expected:
            raise _fail(GoldRunFailureCode.IDENTITY_MISMATCH, "decision identity does not match payload")


@dataclass(frozen=True)
class AttemptSummary:
    """Immutable attempt entry inside GoldRunResult; the full set, no picks."""

    attempt_index: int
    attempt_id: str
    outcome: AttemptOutcome
    c1_status: str | None
    result_sha256: str

    def __post_init__(self) -> None:
        if type(self.attempt_index) is not int or self.attempt_index < 1:
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt_index must be a positive integer")
        if type(self.attempt_id) is not str or self.attempt_id != str(self.attempt_index):
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt_id must equal the decimal attempt index")
        if type(self.outcome) is not AttemptOutcome:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "summary outcome must be exact")
        if self.c1_status is not None and (type(self.c1_status) is not str or not self.c1_status or len(self.c1_status) > 64):
            raise _fail(GoldRunFailureCode.BOUNDED_VALUE, "c1_status must be a bounded string or None")
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
    """Full attempt set plus final authority status; built only from records."""

    run_id: RunId
    gold_run_id: str
    manifest_sha256: str
    final_status: RunFinalStatus
    terminal_decision: TerminalDecisionKind
    attempts: tuple[AttemptSummary, ...]
    resolved_attempt_index: int | None
    fallback_arm_id: str | None
    result_sha256: str

    def __post_init__(self) -> None:
        if type(self.run_id) is not RunId or type(self.final_status) is not RunFinalStatus:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run result fields must be exact")
        if type(self.terminal_decision) is not TerminalDecisionKind or self.terminal_decision not in TERMINAL_DECISIONS:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "run result requires a terminal decision")
        if type(self.attempts) is not tuple:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "attempts must be a tuple")
        summaries = tuple(AttemptSummary(**item.__dict__) for item in self.attempts)
        indexes = [item.attempt_index for item in summaries]
        if indexes != sorted(set(indexes)):
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "run attempts must be ordered and unique")
        _digest(self.manifest_sha256, "manifest_sha256")
        if self.resolved_attempt_index is not None:
            if type(self.resolved_attempt_index) is not int:
                raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "resolved_attempt_index must be exact")
            resolved = [item for item in summaries if item.outcome is AttemptOutcome.RESOLVED]
            if len(resolved) != 1 or resolved[0].attempt_index != self.resolved_attempt_index:
                raise _fail(GoldRunFailureCode.PHASE_INVALID, "run result names exactly one resolved attempt")
        if self.final_status is RunFinalStatus.GOLD_RESOLVED and self.resolved_attempt_index is None:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "GOLD_RESOLVED requires the resolved attempt")
        if self.final_status is RunFinalStatus.BASELINE_FALLBACK_EXPLICIT:
            if self.terminal_decision is not TerminalDecisionKind.FALLBACK_BASELINE_EXPLICIT or self.fallback_arm_id is None:
                raise _fail(GoldRunFailureCode.PHASE_INVALID, "explicit fallback requires the fallback decision and arm id")
        elif self.fallback_arm_id is not None:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "fallback arm id is present without an explicit fallback")
        _digest(self.result_sha256, "result_sha256")

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": GOLD_RUN_RESULT_SCHEMA_V1,
            "run_id": self.run_id.to_dict(),
            "gold_run_id": self.gold_run_id,
            "manifest_sha256": self.manifest_sha256,
            "final_status": self.final_status.value,
            "terminal_decision": self.terminal_decision.value,
            "attempts": [item.to_dict() for item in self.attempts],
            "resolved_attempt_index": self.resolved_attempt_index,
            "fallback_arm_id": self.fallback_arm_id,
        }

    def stored_dict(self) -> dict[str, object]:
        return {"record_sha256": self.result_sha256, "payload": self.payload()}

    def canonical_bytes(self) -> bytes:
        return canonical_run_bytes(self.payload())

    @classmethod
    def create(cls, **fields: object) -> "GoldRunResult":
        provisional = cls(result_sha256=_ZERO_DIGEST, **fields)  # type: ignore[arg-type]
        digest = hashlib.sha256(provisional.canonical_bytes()).hexdigest()
        return cls(result_sha256=digest, **fields)  # type: ignore[arg-type]

    def validate_identity(self) -> None:
        expected = hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest()
        if self.result_sha256 != expected:
            raise _fail(GoldRunFailureCode.IDENTITY_MISMATCH, "run result identity does not match payload")

