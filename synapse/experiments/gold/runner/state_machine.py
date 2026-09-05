"""Exact durable state reconstruction for one Stage 11 Gold run.

The store is the state machine. This owner decodes only current schemas, binds
every basis, context, result, continuation evidence and decision to one run and
attempt, and recomputes continuation evidence from the completed durable
history before a decision is trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.contracts import AttemptId, RunId
from synapse.experiments.gold.stage12.outcome import project_run_outcome

from .attempt_knowledge import (
    AttemptKnowledgeBasis,
    KnowledgeContinuationEvidence,
    basis_from_payload,
    continuation_evidence_from_payload,
    decide_completed_attempt_continuation,
    prior_attempt_evidence_from_result,
)
from .attempt_knowledge_store import basis_record_key
from .budgets import preparation_budget_failure
from .vocabulary import EXHAUSTED_BUDGET_CODES, UNKNOWN_BUDGET_CODES
from .models import (
    GOLD_ATTEMPT_CONTEXT_SCHEMA_V4,
    GOLD_ATTEMPT_PREPARATION_FAILURE_SCHEMA_V1,
    GOLD_ATTEMPT_RESULT_SCHEMA_V4,
    GOLD_RUN_DECISION_SCHEMA_V3,
    GOLD_RUN_MANIFEST_SCHEMA_V3,
    GOLD_RUN_RESULT_SCHEMA_V3,
    AttemptPhaseRefs,
    AttemptPreparationFailure,
    AttemptSummary,
    GoldAttemptContext,
    GoldAttemptResult,
    GoldReplicatePolicy,
    GoldRunBudgets,
    GoldRunConfig,
    GoldRunManifest,
    GoldRunResult,
    GoldRunVersions,
    NextAttemptDecision,
)
from .records import RecordKind, RunRecordStore
from .run_progress import AttemptPreparationStarted, audit_preparation_starts
from .state_machine_validation import validate_state_consistency
from .vocabulary import (
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


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


def _exact_dict(value: object, fields: tuple[str, ...], label: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != set(fields):
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, f"{label} has an unknown shape")
    return value


def _exact_list(value: object, label: str) -> list[object]:
    if type(value) is not list:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, f"{label} must be a list")
    return value


def _enum(value: object, cls: type[Enum], label: str) -> Enum:
    if type(value) is not str:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, f"{label} must be a string")
    try:
        return cls(value)
    except ValueError as exc:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, f"{label} is unknown") from exc


def _run_id(value: object, label: str) -> RunId:
    try:
        return RunId.from_dict(value)
    except (TypeError, ValueError) as exc:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, f"{label} is malformed") from exc


def _attempt_id(value: object, label: str) -> AttemptId:
    try:
        return AttemptId.from_dict(value)
    except (TypeError, ValueError) as exc:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, f"{label} is malformed") from exc


def _ref(value: object, label: str) -> HashBoundRef:
    try:
        return HashBoundRef.from_dict(value)
    except (TypeError, ValueError) as exc:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, f"{label} is malformed") from exc


def _optional_ref(value: object, label: str) -> HashBoundRef | None:
    return None if value is None else _ref(value, label)


def _refs(value: object, label: str) -> tuple[HashBoundRef, ...]:
    return tuple(_ref(item, label) for item in _exact_list(value, label))


def _stored_envelope(payload: object) -> tuple[str, dict[str, object]]:
    envelope = _exact_dict(payload, ("record_sha256", "payload"), "stored record")
    digest = envelope["record_sha256"]
    inner = envelope["payload"]
    if type(digest) is not str or type(inner) is not dict:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "stored record envelope is malformed")
    return digest, inner


def manifest_from_stored_payload(stored: dict[str, object]) -> GoldRunManifest:
    digest, raw = _stored_envelope(stored)
    payload = _exact_dict(raw, ("schema_version", "run_id", "gold_run_id", "config", "versions", "inputs_sha256"), "run manifest")
    if payload["schema_version"] != GOLD_RUN_MANIFEST_SCHEMA_V3:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "manifest schema is unknown")
    config_raw = _exact_dict(
        payload["config"],
        (
            "task_id", "instance_id", "base_revision", "provider", "model",
            "oracle_name", "environment_kind", "budgets", "max_attempts",
            "replicate_policy", "fallback_policy",
        ),
        "run config",
    )
    budgets_raw = _exact_dict(
        config_raw["budgets"],
        ("maximum_wall_clock_seconds", "maximum_worker_tokens", "replay_gas_budget", "replay_cognitive_budget"),
        "run budgets",
    )
    replicate_raw = _exact_dict(
        config_raw["replicate_policy"],
        ("group_id", "replicate_count", "replicate_index"),
        "replicate policy",
    )
    versions_raw = _exact_dict(
        payload["versions"],
        ("specification_version", "specification_sha256", "implementation_revision", "policy_version", "policy_sha256"),
        "run versions",
    )
    manifest = GoldRunManifest(
        run_id=_run_id(payload["run_id"], "manifest run id"),
        gold_run_id=payload["gold_run_id"],
        config=GoldRunConfig(
            task_id=config_raw["task_id"],
            instance_id=config_raw["instance_id"],
            base_revision=config_raw["base_revision"],
            provider=config_raw["provider"],
            model=config_raw["model"],
            oracle_name=config_raw["oracle_name"],
            environment_kind=config_raw["environment_kind"],
            budgets=GoldRunBudgets(**budgets_raw),
            max_attempts=config_raw["max_attempts"],
            replicate_policy=GoldReplicatePolicy(**replicate_raw),
            fallback_policy=_enum(config_raw["fallback_policy"], FallbackPolicy, "fallback policy"),
        ),
        versions=GoldRunVersions(**versions_raw),
        manifest_sha256=digest,
        inputs_sha256=payload["inputs_sha256"],
    )
    manifest.validate_identity()
    return manifest


def _attempt_context_from_payload(stored: dict[str, object]) -> GoldAttemptContext:
    digest, raw = _stored_envelope(stored)
    payload = _exact_dict(
        raw,
        ("schema_version", "run_id", "gold_run_id", "attempt_index", "attempt_id", "phase_refs"),
        "attempt context",
    )
    if payload["schema_version"] != GOLD_ATTEMPT_CONTEXT_SCHEMA_V4:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "attempt context schema is unknown")
    refs_raw = _exact_dict(
        payload["phase_refs"],
        (
            "knowledge_snapshot_ref", "retrieval_ref", "replay_ref", "intent_ref",
            "plan_ref", "plan_semantic_sha256", "worker_context_id",
            "worker_context_audit_sha256", "knowledge_basis_sha256",
        ),
        "attempt phase refs",
    )
    context = GoldAttemptContext(
        run_id=_run_id(payload["run_id"], "attempt run id"),
        gold_run_id=payload["gold_run_id"],
        attempt_index=payload["attempt_index"],
        attempt_id=_attempt_id(payload["attempt_id"], "attempt id"),
        phase_refs=AttemptPhaseRefs(
            knowledge_snapshot_ref=_ref(refs_raw["knowledge_snapshot_ref"], "knowledge snapshot ref"),
            retrieval_ref=_ref(refs_raw["retrieval_ref"], "retrieval ref"),
            replay_ref=_ref(refs_raw["replay_ref"], "replay ref"),
            intent_ref=_ref(refs_raw["intent_ref"], "intent ref"),
            plan_ref=_ref(refs_raw["plan_ref"], "plan ref"),
            plan_semantic_sha256=refs_raw["plan_semantic_sha256"],
            worker_context_id=refs_raw["worker_context_id"],
            worker_context_audit_sha256=refs_raw["worker_context_audit_sha256"],
            knowledge_basis_sha256=refs_raw["knowledge_basis_sha256"],
        ),
        context_sha256=digest,
    )
    context.validate_identity()
    return context


def _attempt_result_from_payload(stored: dict[str, object]) -> GoldAttemptResult:
    digest, raw = _stored_envelope(stored)
    payload = _exact_dict(
        raw,
        (
            "schema_version", "run_id", "gold_run_id", "attempt_index", "attempt_id",
            "outcome", "c1_status", "oracle_invoked", "oracle_resolved",
            "worker_result_ref", "c1_result_ref", "oracle_result_ref",
            "publication_refs", "context_sha256", "verified_finding_sha256", "verified_patch_sha256",
            "structured_outcome",
        ),
        "attempt result",
    )
    if payload["schema_version"] != GOLD_ATTEMPT_RESULT_SCHEMA_V4:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "attempt result schema is unknown")
    result = GoldAttemptResult(
        run_id=_run_id(payload["run_id"], "result run id"),
        gold_run_id=payload["gold_run_id"],
        attempt_index=payload["attempt_index"],
        attempt_id=_attempt_id(payload["attempt_id"], "result attempt id"),
        outcome=_enum(payload["outcome"], AttemptOutcome, "attempt outcome"),
        c1_status=payload["c1_status"],
        oracle_invoked=payload["oracle_invoked"],
        oracle_resolved=payload["oracle_resolved"],
        worker_result_ref=_optional_ref(payload["worker_result_ref"], "worker result ref"),
        c1_result_ref=_optional_ref(payload["c1_result_ref"], "C1 result ref"),
        oracle_result_ref=_optional_ref(payload["oracle_result_ref"], "oracle result ref"),
        publication_refs=_refs(payload["publication_refs"], "publication refs"),
        verified_finding_sha256=payload["verified_finding_sha256"],
        verified_patch_sha256=payload["verified_patch_sha256"],
        structured_outcome=payload["structured_outcome"],
        context_sha256=payload["context_sha256"],
        result_sha256=digest,
    )
    result.validate_identity()
    return result


def _decision_from_payload(stored: dict[str, object]) -> NextAttemptDecision:
    digest, raw = _stored_envelope(stored)
    payload = _exact_dict(
        raw,
        (
            "schema_version", "run_id", "gold_run_id", "attempt_index",
            "attempt_result_sha256", "decision", "reason", "fallback_arm_id",
            "continuation_evidence_sha256",
        ),
        "run decision",
    )
    if payload["schema_version"] != GOLD_RUN_DECISION_SCHEMA_V3:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "decision schema is unknown")
    decision = NextAttemptDecision(
        run_id=_run_id(payload["run_id"], "decision run id"),
        gold_run_id=payload["gold_run_id"],
        attempt_index=payload["attempt_index"],
        attempt_result_sha256=payload["attempt_result_sha256"],
        decision=_enum(payload["decision"], TerminalDecisionKind, "decision kind"),
        reason=payload["reason"],
        fallback_arm_id=payload["fallback_arm_id"],
        continuation_evidence_sha256=payload["continuation_evidence_sha256"],
        decision_sha256=digest,
    )
    decision.validate_identity()
    return decision


def _preparation_failure_from_payload(
    stored: dict[str, object],
) -> AttemptPreparationFailure:
    digest, raw = _stored_envelope(stored)
    payload = _exact_dict(
        raw,
        (
            "schema_version", "run_id", "gold_run_id", "manifest_sha256",
            "target_attempt_index", "source_attempt_index",
            "source_attempt_result_sha256", "source_decision_sha256",
            "continuation_evidence_sha256", "terminal_decision", "reason",
            "detail_code", "fallback_arm_id",
        ),
        "attempt preparation failure",
    )
    if payload["schema_version"] != GOLD_ATTEMPT_PREPARATION_FAILURE_SCHEMA_V1:
        raise _fail(
            GoldRunFailureCode.RECORD_CONFLICT,
            "attempt preparation failure schema is unknown",
        )
    failure = AttemptPreparationFailure(
        run_id=_run_id(payload["run_id"], "preparation failure run id"),
        gold_run_id=payload["gold_run_id"],
        manifest_sha256=payload["manifest_sha256"],
        target_attempt_index=payload["target_attempt_index"],
        source_attempt_index=payload["source_attempt_index"],
        source_attempt_result_sha256=payload["source_attempt_result_sha256"],
        source_decision_sha256=payload["source_decision_sha256"],
        continuation_evidence_sha256=payload["continuation_evidence_sha256"],
        terminal_decision=_enum(
            payload["terminal_decision"],
            TerminalDecisionKind,
            "preparation failure terminal decision",
        ),
        reason=payload["reason"],
        detail_code=payload["detail_code"],
        fallback_arm_id=payload["fallback_arm_id"],
        failure_sha256=digest,
    )
    failure.validate_identity()
    return failure


def _attempt_summary(value: object) -> AttemptSummary:
    payload = _exact_dict(value, ("attempt_index", "attempt_id", "outcome", "c1_status", "result_sha256"), "attempt summary")
    return AttemptSummary(
        attempt_index=payload["attempt_index"],
        attempt_id=payload["attempt_id"],
        outcome=_enum(payload["outcome"], AttemptOutcome, "summary outcome"),
        c1_status=payload["c1_status"],
        result_sha256=payload["result_sha256"],
    )


def _run_result_from_payload(stored: dict[str, object]) -> GoldRunResult:
    digest, raw = _stored_envelope(stored)
    payload = _exact_dict(
        raw,
        (
            "schema_version", "run_id", "gold_run_id", "manifest_sha256",
            "final_status", "terminal_decision", "terminal_decision_sha256",
            "attempts", "resolved_attempt_index", "fallback_arm_id",
            "telemetry_completeness", "telemetry_refs", "mechanism_activation",
            "mechanism_activation_refs",
            "structured_outcome",
        ),
        "run result",
    )
    if payload["schema_version"] != GOLD_RUN_RESULT_SCHEMA_V3:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run result schema is unknown")
    result = GoldRunResult(
        run_id=_run_id(payload["run_id"], "final run id"),
        gold_run_id=payload["gold_run_id"],
        manifest_sha256=payload["manifest_sha256"],
        final_status=_enum(payload["final_status"], RunFinalStatus, "final status"),
        structured_outcome=payload["structured_outcome"],
        terminal_decision=_enum(payload["terminal_decision"], TerminalDecisionKind, "terminal decision"),
        terminal_decision_sha256=payload["terminal_decision_sha256"],
        attempts=tuple(_attempt_summary(item) for item in _exact_list(payload["attempts"], "attempts")),
        resolved_attempt_index=payload["resolved_attempt_index"],
        fallback_arm_id=payload["fallback_arm_id"],
        telemetry_completeness=_enum(payload["telemetry_completeness"], TelemetryCompleteness, "telemetry completeness"),
        telemetry_refs=_refs(payload["telemetry_refs"], "telemetry refs"),
        mechanism_activation=_enum(payload["mechanism_activation"], MechanismActivationStatus, "mechanism activation"),
        mechanism_activation_refs=_refs(payload["mechanism_activation_refs"], "mechanism activation refs"),
        result_sha256=digest,
    )
    result.validate_identity()
    return result


def restore_manifest(store: RunRecordStore) -> GoldRunManifest:
    keys = store.iter_keys(kind=RecordKind.MANIFEST)
    if not keys:
        raise _fail(GoldRunFailureCode.RECORD_MISSING, "run manifest is not persisted")
    if keys != ("manifest",):
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "manifest namespace is not exact")
    record = store.get(kind=RecordKind.MANIFEST, key="manifest")
    if record is None:
        raise _fail(GoldRunFailureCode.RECORD_MISSING, "run manifest disappeared during scan")
    return manifest_from_stored_payload(record.payload)


@dataclass(frozen=True)
class AttemptState:
    attempt_index: int
    context: GoldAttemptContext
    result: GoldAttemptResult | None

    @property
    def finished(self) -> bool:
        return self.result is not None


@dataclass(frozen=True)
class RunState:
    manifest: GoldRunManifest
    attempts: tuple[AttemptState, ...]
    decisions: tuple[NextAttemptDecision, ...]
    continuation_evidence: tuple[KnowledgeContinuationEvidence, ...]
    preparation_failure: AttemptPreparationFailure | None
    final_result: GoldRunResult | None

    @property
    def completed_indexes(self) -> tuple[int, ...]:
        return tuple(item.attempt_index for item in self.attempts if item.finished)

    @property
    def interrupted_indexes(self) -> tuple[int, ...]:
        return tuple(item.attempt_index for item in self.attempts if not item.finished)

    @property
    def attempts_used(self) -> int:
        return len(self.attempts)

    @property
    def next_index(self) -> int:
        return self.attempts_used + 1

    def last_result(self) -> GoldAttemptResult | None:
        return None if not self.attempts else self.attempts[-1].result

    def decision_for(self, attempt_index: int) -> NextAttemptDecision | None:
        return next((item for item in self.decisions if item.attempt_index == attempt_index), None)

    def continuation_for(self, attempt_index: int) -> KnowledgeContinuationEvidence | None:
        return next((item for item in self.continuation_evidence if item.attempt_index == attempt_index), None)


def _attempt_indexes(store: RunRecordStore, *, kind: str, maximum: int) -> tuple[int, ...]:
    indexes: list[int] = []
    for key in store.iter_keys(kind=kind):
        if not key.isascii() or not key.isdecimal() or str(int(key)) != key:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "attempt record key is not canonical")
        index = int(key)
        if not 1 <= index <= maximum:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "attempt record exceeds run budget")
        indexes.append(index)
    return tuple(sorted(indexes))


def _basis_indexes(store: RunRecordStore, *, maximum: int) -> tuple[int, ...]:
    indexes: list[int] = []
    for key in store.iter_keys(kind=RecordKind.ATTEMPT_KNOWLEDGE_BASIS):
        if not key.startswith("attempt-"):
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "basis record key is not canonical")
        suffix = key[len("attempt-"):]
        if not suffix.isascii() or not suffix.isdecimal() or str(int(suffix)) != suffix:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "basis record key is not canonical")
        index = int(suffix)
        if not 1 <= index <= maximum or basis_record_key(index) != key:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "basis record exceeds run budget")
        indexes.append(index)
    return tuple(sorted(indexes))


def _same_run(record: object, manifest: GoldRunManifest, label: str) -> None:
    if getattr(record, "run_id", None) != manifest.run_id or getattr(record, "gold_run_id", None) != manifest.gold_run_id:
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, f"{label} belongs to another run")


def _load_basis_map(
    store: RunRecordStore,
    *,
    manifest: GoldRunManifest,
    indexes: tuple[int, ...],
) -> dict[int, tuple[AttemptKnowledgeBasis, str]]:
    result: dict[int, tuple[AttemptKnowledgeBasis, str]] = {}
    for index in indexes:
        record = store.get(kind=RecordKind.ATTEMPT_KNOWLEDGE_BASIS, key=basis_record_key(index))
        if record is None:
            raise _fail(GoldRunFailureCode.RECORD_MISSING, "attempt basis disappeared during scan")
        basis = basis_from_payload(record.payload)
        if basis.run_id != manifest.run_id.value or basis.attempt_index != index or basis.attempt_id != str(index):
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "attempt basis belongs to another run or attempt")
        if basis.digest() != record.sha256:
            raise _fail(GoldRunFailureCode.IDENTITY_MISMATCH, "attempt basis digest differs from stored bytes")
        result[index] = (basis, record.sha256)
    return result


def _load_attempt_states(
    store: RunRecordStore,
    *,
    manifest: GoldRunManifest,
    context_indexes: tuple[int, ...],
    result_indexes: tuple[int, ...],
    basis_map: dict[int, tuple[AttemptKnowledgeBasis, str]],
) -> tuple[tuple[AttemptState, ...], dict[int, GoldAttemptResult]]:
    attempts: list[AttemptState] = []
    results: dict[int, GoldAttemptResult] = {}
    for index in context_indexes:
        context_record = store.get(kind=RecordKind.ATTEMPT_CONTEXT, key=str(index))
        if context_record is None:
            raise _fail(GoldRunFailureCode.RECORD_MISSING, "attempt context disappeared during scan")
        context = _attempt_context_from_payload(context_record.payload)
        _same_run(context, manifest, "attempt context")
        if context.attempt_index != index:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "attempt context index differs from key")
        basis, basis_sha = basis_map[index]
        if context.phase_refs.knowledge_basis_sha256 != basis_sha or basis.digest() != basis_sha:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "attempt context names different knowledge basis")
        result: GoldAttemptResult | None = None
        if index in result_indexes:
            result_record = store.get(kind=RecordKind.ATTEMPT_RESULT, key=str(index))
            if result_record is None:
                raise _fail(GoldRunFailureCode.RECORD_MISSING, "attempt result disappeared during scan")
            result = _attempt_result_from_payload(result_record.payload)
            _same_run(result, manifest, "attempt result")
            if result.attempt_index != index or result.context_sha256 != context.context_sha256:
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "attempt result differs from durable context")
            has_worker_context = context.phase_refs.worker_context_id is not None
            if result.outcome in (AttemptOutcome.DELIVERY_REFUSED, AttemptOutcome.DELIVERY_UNAVAILABLE) and has_worker_context:
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "pre-C1 failure fabricates worker context")
            if result.outcome not in (
                AttemptOutcome.DELIVERY_REFUSED,
                AttemptOutcome.DELIVERY_UNAVAILABLE,
                AttemptOutcome.CONTROLLER_INTERRUPTED,
            ) and not has_worker_context:
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "C1-classified result lacks worker context")
            if result.worker_result_ref is not None and not has_worker_context:
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "worker result exists without worker context")
            results[index] = result
        attempts.append(AttemptState(index, context, result))
    return tuple(attempts), results


def _load_continuation_evidence(
    store: RunRecordStore,
    *,
    manifest: GoldRunManifest,
    indexes: tuple[int, ...],
    attempts: tuple[AttemptState, ...],
    basis_map: dict[int, tuple[AttemptKnowledgeBasis, str]],
) -> tuple[KnowledgeContinuationEvidence, ...]:
    by_attempt = {item.attempt_index: item for item in attempts}
    evidence: list[KnowledgeContinuationEvidence] = []
    for index in indexes:
        record = store.get(kind=RecordKind.CONTINUATION_EVIDENCE, key=str(index))
        if record is None:
            raise _fail(GoldRunFailureCode.RECORD_MISSING, "continuation evidence disappeared during scan")
        stored = continuation_evidence_from_payload(record.payload)
        attempt = by_attempt[index]
        if stored.run_id != manifest.run_id.value or stored.attempt_index != index or attempt.result is None:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "continuation evidence belongs to another completed attempt")
        current_basis, current_sha = basis_map[index]
        current_finding = prior_attempt_evidence_from_result(
            attempt.result,
            attempt_index=index,
            accepted_plan_ref=attempt.context.phase_refs.plan_ref,
            plan_semantic_sha256=attempt.context.phase_refs.plan_semantic_sha256,
        )
        previous_basis = None
        previous_sha = None
        previous_finding = None
        if index > 1:
            previous = by_attempt.get(index - 1)
            if previous is None or previous.result is None:
                raise _fail(GoldRunFailureCode.PHASE_INVALID, "continuation evidence skips unfinished predecessor")
            previous_basis, previous_sha = basis_map[index - 1]
            previous_finding = prior_attempt_evidence_from_result(
                previous.result,
                attempt_index=index - 1,
                accepted_plan_ref=previous.context.phase_refs.plan_ref,
                plan_semantic_sha256=previous.context.phase_refs.plan_semantic_sha256,
            )
        recomputed = decide_completed_attempt_continuation(
            run_id=manifest.run_id.value,
            attempt_index=index,
            current_basis=current_basis,
            current_basis_sha256=current_sha,
            current_finding=current_finding,
            previous_basis=previous_basis,
            previous_basis_sha256=previous_sha,
            previous_finding=previous_finding,
            earlier_bases=tuple(basis_map[i][0] for i in range(1, index - 1)),
            earlier_findings=tuple(
                prior_attempt_evidence_from_result(
                    by_attempt[i].result,
                    attempt_index=i,
                    accepted_plan_ref=by_attempt[i].context.phase_refs.plan_ref,
                    plan_semantic_sha256=by_attempt[i].context.phase_refs.plan_semantic_sha256,
                )
                for i in range(1, index - 1)
            ),
        )
        if stored.digest() != record.sha256 or stored.payload() != recomputed.payload():
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "continuation evidence is not the durable-history result")
        evidence.append(stored)
    return tuple(evidence)


def _load_decisions(
    store: RunRecordStore,
    *,
    manifest: GoldRunManifest,
    decision_indexes: tuple[int, ...],
    results: dict[int, GoldAttemptResult],
    evidence_map: dict[int, KnowledgeContinuationEvidence],
) -> tuple[NextAttemptDecision, ...]:
    decisions: list[NextAttemptDecision] = []
    for index in decision_indexes:
        decision_record = store.get(kind=RecordKind.DECISION, key=str(index))
        if decision_record is None:
            raise _fail(GoldRunFailureCode.RECORD_MISSING, "decision disappeared during scan")
        decision = _decision_from_payload(decision_record.payload)
        _same_run(decision, manifest, "decision")
        result = results[index]
        evidence = evidence_map[index]
        if (
            decision.attempt_index != index
            or decision.attempt_result_sha256 != result.result_sha256
            or decision.continuation_evidence_sha256 != evidence.digest()
        ):
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "decision differs from result or continuation evidence")
        decisions.append(decision)
    return tuple(decisions)


def _audit_attempt_progress(
    store: RunRecordStore,
    *,
    manifest: GoldRunManifest,
    attempts: tuple[AttemptState, ...],
) -> dict[int, object]:
    from .run_progress import AttemptProgressPhase, AttemptProgressState, load_attempt_progress, progress_key

    contexts = {item.attempt_index: item.context for item in attempts}
    for key in store.iter_keys(kind=RecordKind.ATTEMPT_PROGRESS):
        try:
            index_text, phase_text = key.split(".", 1)
            index = int(index_text)
            phase = AttemptProgressPhase(phase_text.upper())
        except (ValueError, TypeError) as exc:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "progress key is unknown") from exc
        if (
            not index_text.isascii()
            or not index_text.isdecimal()
            or str(index) != index_text
            or progress_key(index, phase) != key
        ):
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "progress key is not canonical")
        if index not in contexts:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "attempt progress has no context")
    observed: dict[int, AttemptProgressState] = {}
    for index, context in contexts.items():
        observed[index] = load_attempt_progress(store, manifest=manifest, context=context)
    return observed


def _load_preparation_failure(
    store: RunRecordStore,
) -> AttemptPreparationFailure | None:
    keys = store.iter_keys(kind=RecordKind.PREPARATION_FAILURE)
    if keys not in ((), ("final",)):
        raise _fail(
            GoldRunFailureCode.RECORD_CONFLICT,
            "attempt-preparation-failure namespace is not exact",
        )
    record = store.get(kind=RecordKind.PREPARATION_FAILURE, key="final") if keys else None
    return None if record is None else _preparation_failure_from_payload(record.payload)


def _load_final_result(store: RunRecordStore) -> GoldRunResult | None:
    keys = store.iter_keys(kind=RecordKind.RUN_RESULT)
    if keys not in ((), ("final",)):
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run-result namespace is not exact")
    record = store.get(kind=RecordKind.RUN_RESULT, key="final") if keys else None
    return None if record is None else _run_result_from_payload(record.payload)


def load_run_state(store: RunRecordStore) -> RunState:
    """Rebuild one resumable prefix from one immutable store view."""

    manifest = restore_manifest(store)
    maximum = manifest.config.max_attempts
    context_indexes = _attempt_indexes(store, kind=RecordKind.ATTEMPT_CONTEXT, maximum=maximum)
    basis_indexes = _basis_indexes(store, maximum=maximum)
    result_indexes = _attempt_indexes(store, kind=RecordKind.ATTEMPT_RESULT, maximum=maximum)
    evidence_indexes = _attempt_indexes(store, kind=RecordKind.CONTINUATION_EVIDENCE, maximum=maximum)
    decision_indexes = _attempt_indexes(store, kind=RecordKind.DECISION, maximum=maximum)
    if context_indexes != tuple(range(1, len(context_indexes) + 1)):
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "attempt contexts are not a gapless prefix")
    if basis_indexes != context_indexes:
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "knowledge-basis namespace differs from attempt contexts")
    if not set(result_indexes).issubset(context_indexes):
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "attempt result exists without context")
    if evidence_indexes != decision_indexes:
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "continuation evidence and decision namespaces differ")
    if not set(decision_indexes).issubset(result_indexes):
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "decision exists without attempt result")

    basis_map = _load_basis_map(store, manifest=manifest, indexes=basis_indexes)
    attempts, results = _load_attempt_states(
        store,
        manifest=manifest,
        context_indexes=context_indexes,
        result_indexes=result_indexes,
        basis_map=basis_map,
    )
    evidence = _load_continuation_evidence(
        store,
        manifest=manifest,
        indexes=evidence_indexes,
        attempts=attempts,
        basis_map=basis_map,
    )
    evidence_map = {item.attempt_index: item for item in evidence}
    decisions = _load_decisions(
        store,
        manifest=manifest,
        decision_indexes=decision_indexes,
        results=results,
        evidence_map=evidence_map,
    )
    preparation_failure = _load_preparation_failure(store)
    audit_preparation_starts(store, manifest=manifest, attempts=attempts, decisions=decisions)
    if preparation_failure is not None and preparation_failure.detail_code in EXHAUSTED_BUDGET_CODES | UNKNOWN_BUDGET_CODES:
        start = store.get(kind=RecordKind.PREPARATION_STARTED, key=str(preparation_failure.target_attempt_index))
        if start is None:
            raise _fail(GoldRunFailureCode.RECORD_MISSING, "budget refusal has no durable clock observation")
        observation = AttemptPreparationStarted.from_payload(start.payload)
        expected = preparation_budget_failure(
            store=store, manifest=manifest, attempts=attempts,
            observed_at_unix_ms=observation.started_at_unix_ms,
        )
        if preparation_failure.detail_code != expected:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "budget refusal differs from execution evidence")
    final_result = _load_final_result(store)
    progress = _audit_attempt_progress(store, manifest=manifest, attempts=attempts)
    state = RunState(
        manifest,
        attempts,
        decisions,
        evidence,
        preparation_failure,
        final_result,
    )
    validate_state_consistency(
        manifest=state.manifest,
        attempts=state.attempts,
        decisions=state.decisions,
        continuation_evidence=state.continuation_evidence,
        preparation_failure=state.preparation_failure,
        final_result=state.final_result,
        progress=progress,
        build_run_result=build_run_result,
    )
    return state


def build_run_result(
    *,
    manifest: GoldRunManifest,
    attempts: tuple[GoldAttemptResult, ...],
    terminal_decision: NextAttemptDecision | AttemptPreparationFailure,
) -> GoldRunResult:
    manifest.validate_identity()
    if type(attempts) is not tuple:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run attempts must be an exact tuple")
    for attempt in attempts:
        if type(attempt) is not GoldAttemptResult:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "attempt result must be exact")
        attempt.validate_identity()
        _same_run(attempt, manifest, "attempt result")
    indexes = [item.attempt_index for item in attempts]
    if indexes != list(range(1, len(indexes) + 1)):
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "run attempts must be a gapless prefix")

    if type(terminal_decision) is NextAttemptDecision:
        terminal_decision.validate_identity()
        _same_run(terminal_decision, manifest, "terminal decision")
        if terminal_decision.decision not in TERMINAL_DECISIONS:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "run result requires terminal decision")
        if not attempts:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "attempt decision requires attempt records")
        if (
            terminal_decision.attempt_index != indexes[-1]
            or terminal_decision.attempt_result_sha256 != attempts[-1].result_sha256
        ):
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "terminal decision is not bound to terminal attempt")
        decision_kind = terminal_decision.decision
        decision_sha256 = terminal_decision.decision_sha256
        fallback_arm_id = terminal_decision.fallback_arm_id
    elif type(terminal_decision) is AttemptPreparationFailure:
        terminal_decision.validate_identity()
        _same_run(terminal_decision, manifest, "attempt preparation failure")
        if terminal_decision.manifest_sha256 != manifest.manifest_sha256:
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "attempt preparation failure names another manifest",
            )
        if terminal_decision.source_attempt_index is None:
            if attempts:
                raise _fail(
                    GoldRunFailureCode.PHASE_INVALID,
                    "initial preparation failure cannot follow an attempt",
                )
        elif (
            not attempts
            or terminal_decision.source_attempt_index != indexes[-1]
            or terminal_decision.source_attempt_result_sha256 != attempts[-1].result_sha256
        ):
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "attempt preparation failure is not bound to the run tail",
            )
        decision_kind = terminal_decision.terminal_decision
        decision_sha256 = terminal_decision.failure_sha256
        fallback_arm_id = terminal_decision.fallback_arm_id
    else:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "terminal authority has an unknown type")

    summaries = tuple(
        AttemptSummary(
            attempt_index=item.attempt_index,
            attempt_id=item.attempt_id.value,
            outcome=item.outcome,
            c1_status=item.c1_status,
            result_sha256=item.result_sha256,
        )
        for item in attempts
    )
    resolved = [item.attempt_index for item in attempts if item.outcome is AttemptOutcome.RESOLVED]
    return GoldRunResult.create(
        run_id=manifest.run_id,
        gold_run_id=manifest.gold_run_id,
        manifest_sha256=manifest.manifest_sha256,
        final_status=final_status_for_decision(decision_kind),
        structured_outcome=project_run_outcome(manifest=manifest, attempts=attempts, terminal_decision=terminal_decision),
        terminal_decision=decision_kind,
        terminal_decision_sha256=decision_sha256,
        attempts=summaries,
        resolved_attempt_index=resolved[0] if resolved else None,
        fallback_arm_id=fallback_arm_id,
        telemetry_completeness=TelemetryCompleteness.UNAVAILABLE,
        telemetry_refs=(),
        mechanism_activation=MechanismActivationStatus.NOT_EVALUATED,
        mechanism_activation_refs=(),
    )
