"""Exact durable state reconstruction for one Stage 11 Gold run.

The store is the state machine.  This owner decodes only the current schemas,
binds every record to one manifest and one attempt index, and accepts only
phase prefixes that the controller can resume without replaying an effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.contracts import AttemptId, RunId
from synapse.experiments.gold.runner.models import (
    GOLD_ATTEMPT_CONTEXT_SCHEMA_V2,
    GOLD_ATTEMPT_RESULT_SCHEMA_V2,
    GOLD_RUN_DECISION_SCHEMA_V2,
    GOLD_RUN_MANIFEST_SCHEMA_V2,
    GOLD_RUN_RESULT_SCHEMA_V2,
    AttemptPhaseRefs,
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
from synapse.experiments.gold.runner.records import RecordKind, RunRecordStore
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
from .state_machine_validation import validate_state_consistency

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

def _manifest_from_payload(stored: dict[str, object]) -> GoldRunManifest:
    """Decode the complete frozen V2 manifest as one exact linear schema.

    Its nested config, budgets, replicate policy, and versions jointly define
    one manifest identity; separate partial decoders would admit combinations
    that were never validated as the stored authority object.
    """

    digest, raw = _stored_envelope(stored)
    payload = _exact_dict(
        raw,
        ("schema_version", "run_id", "gold_run_id", "config", "versions"),
        "run manifest",
    )
    if payload["schema_version"] != GOLD_RUN_MANIFEST_SCHEMA_V2:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "manifest schema is unknown")
    config_raw = _exact_dict(
        payload["config"],
        (
            "task_id",
            "instance_id",
            "base_revision",
            "provider",
            "model",
            "oracle_name",
            "environment_kind",
            "budgets",
            "max_attempts",
            "replicate_policy",
            "fallback_policy",
        ),
        "run config",
    )
    budgets_raw = _exact_dict(
        config_raw["budgets"],
        (
            "maximum_wall_clock_seconds",
            "maximum_worker_tokens",
            "replay_gas_budget",
            "replay_cognitive_budget",
        ),
        "run budgets",
    )
    replicate_raw = _exact_dict(
        config_raw["replicate_policy"],
        ("group_id", "replicate_count", "replicate_index"),
        "replicate policy",
    )
    versions_raw = _exact_dict(
        payload["versions"],
        (
            "specification_version",
            "specification_sha256",
            "implementation_revision",
            "policy_version",
            "policy_sha256",
        ),
        "run versions",
    )
    config = GoldRunConfig(
        task_id=config_raw["task_id"],  # type: ignore[arg-type]
        instance_id=config_raw["instance_id"],  # type: ignore[arg-type]
        base_revision=config_raw["base_revision"],  # type: ignore[arg-type]
        provider=config_raw["provider"],  # type: ignore[arg-type]
        model=config_raw["model"],  # type: ignore[arg-type]
        oracle_name=config_raw["oracle_name"],  # type: ignore[arg-type]
        environment_kind=config_raw["environment_kind"],  # type: ignore[arg-type]
        budgets=GoldRunBudgets(**budgets_raw),  # type: ignore[arg-type]
        max_attempts=config_raw["max_attempts"],  # type: ignore[arg-type]
        replicate_policy=GoldReplicatePolicy(**replicate_raw),  # type: ignore[arg-type]
        fallback_policy=_enum(
            config_raw["fallback_policy"], FallbackPolicy, "fallback policy"
        ),  # type: ignore[arg-type]
    )
    manifest = GoldRunManifest(
        run_id=_run_id(payload["run_id"], "manifest run id"),
        gold_run_id=payload["gold_run_id"],  # type: ignore[arg-type]
        config=config,
        versions=GoldRunVersions(**versions_raw),  # type: ignore[arg-type]
        manifest_sha256=digest,
    )
    manifest.validate_identity()
    return manifest

def _attempt_context_from_payload(stored: dict[str, object]) -> GoldAttemptContext:
    digest, raw = _stored_envelope(stored)
    payload = _exact_dict(
        raw,
        (
            "schema_version",
            "run_id",
            "gold_run_id",
            "attempt_index",
            "attempt_id",
            "phase_refs",
        ),
        "attempt context",
    )
    if payload["schema_version"] != GOLD_ATTEMPT_CONTEXT_SCHEMA_V2:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "attempt context schema is unknown")
    refs_raw = _exact_dict(
        payload["phase_refs"],
        (
            "knowledge_snapshot_ref",
            "retrieval_ref",
            "replay_ref",
            "intent_ref",
            "plan_ref",
            "worker_context_id",
            "worker_context_audit_sha256",
        ),
        "attempt phase refs",
    )
    context = GoldAttemptContext(
        run_id=_run_id(payload["run_id"], "attempt run id"),
        gold_run_id=payload["gold_run_id"],  # type: ignore[arg-type]
        attempt_index=payload["attempt_index"],  # type: ignore[arg-type]
        attempt_id=_attempt_id(payload["attempt_id"], "attempt id"),
        phase_refs=AttemptPhaseRefs(
            knowledge_snapshot_ref=_ref(
                refs_raw["knowledge_snapshot_ref"], "knowledge snapshot ref"
            ),
            retrieval_ref=_ref(refs_raw["retrieval_ref"], "retrieval ref"),
            replay_ref=_ref(refs_raw["replay_ref"], "replay ref"),
            intent_ref=_ref(refs_raw["intent_ref"], "intent ref"),
            plan_ref=_ref(refs_raw["plan_ref"], "plan ref"),
            worker_context_id=refs_raw["worker_context_id"],  # type: ignore[arg-type]
            worker_context_audit_sha256=refs_raw[
                "worker_context_audit_sha256"
            ],  # type: ignore[arg-type]
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
            "schema_version",
            "run_id",
            "gold_run_id",
            "attempt_index",
            "attempt_id",
            "outcome",
            "c1_status",
            "oracle_invoked",
            "oracle_resolved",
            "worker_result_ref",
            "c1_result_ref",
            "oracle_result_ref",
            "publication_refs",
            "context_sha256",
        ),
        "attempt result",
    )
    if payload["schema_version"] != GOLD_ATTEMPT_RESULT_SCHEMA_V2:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "attempt result schema is unknown")
    result = GoldAttemptResult(
        run_id=_run_id(payload["run_id"], "result run id"),
        gold_run_id=payload["gold_run_id"],  # type: ignore[arg-type]
        attempt_index=payload["attempt_index"],  # type: ignore[arg-type]
        attempt_id=_attempt_id(payload["attempt_id"], "result attempt id"),
        outcome=_enum(payload["outcome"], AttemptOutcome, "attempt outcome"),  # type: ignore[arg-type]
        c1_status=payload["c1_status"],  # type: ignore[arg-type]
        oracle_invoked=payload["oracle_invoked"],  # type: ignore[arg-type]
        oracle_resolved=payload["oracle_resolved"],  # type: ignore[arg-type]
        worker_result_ref=_optional_ref(payload["worker_result_ref"], "worker result ref"),
        c1_result_ref=_optional_ref(payload["c1_result_ref"], "C1 result ref"),
        oracle_result_ref=_optional_ref(payload["oracle_result_ref"], "oracle result ref"),
        publication_refs=_refs(payload["publication_refs"], "publication refs"),
        context_sha256=payload["context_sha256"],  # type: ignore[arg-type]
        result_sha256=digest,
    )
    result.validate_identity()
    return result

def _decision_from_payload(stored: dict[str, object]) -> NextAttemptDecision:
    digest, raw = _stored_envelope(stored)
    payload = _exact_dict(
        raw,
        (
            "schema_version",
            "run_id",
            "gold_run_id",
            "attempt_index",
            "attempt_result_sha256",
            "decision",
            "reason",
            "fallback_arm_id",
            "next_retrieval_causal_ref",
        ),
        "run decision",
    )
    if payload["schema_version"] != GOLD_RUN_DECISION_SCHEMA_V2:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "decision schema is unknown")
    decision = NextAttemptDecision(
        run_id=_run_id(payload["run_id"], "decision run id"),
        gold_run_id=payload["gold_run_id"],  # type: ignore[arg-type]
        attempt_index=payload["attempt_index"],  # type: ignore[arg-type]
        attempt_result_sha256=payload["attempt_result_sha256"],  # type: ignore[arg-type]
        decision=_enum(
            payload["decision"], TerminalDecisionKind, "decision kind"
        ),  # type: ignore[arg-type]
        reason=payload["reason"],  # type: ignore[arg-type]
        fallback_arm_id=payload["fallback_arm_id"],  # type: ignore[arg-type]
        next_retrieval_causal_ref=_optional_ref(
            payload["next_retrieval_causal_ref"], "next retrieval causal ref"
        ),
        decision_sha256=digest,
    )
    decision.validate_identity()
    return decision

def _attempt_summary(value: object) -> AttemptSummary:
    payload = _exact_dict(
        value,
        ("attempt_index", "attempt_id", "outcome", "c1_status", "result_sha256"),
        "attempt summary",
    )
    return AttemptSummary(
        attempt_index=payload["attempt_index"],  # type: ignore[arg-type]
        attempt_id=payload["attempt_id"],  # type: ignore[arg-type]
        outcome=_enum(payload["outcome"], AttemptOutcome, "summary outcome"),  # type: ignore[arg-type]
        c1_status=payload["c1_status"],  # type: ignore[arg-type]
        result_sha256=payload["result_sha256"],  # type: ignore[arg-type]
    )

def _run_result_from_payload(stored: dict[str, object]) -> GoldRunResult:
    digest, raw = _stored_envelope(stored)
    payload = _exact_dict(
        raw,
        (
            "schema_version",
            "run_id",
            "gold_run_id",
            "manifest_sha256",
            "final_status",
            "terminal_decision",
            "terminal_decision_sha256",
            "attempts",
            "resolved_attempt_index",
            "fallback_arm_id",
            "telemetry_completeness",
            "telemetry_refs",
            "mechanism_activation",
            "mechanism_activation_refs",
        ),
        "run result",
    )
    if payload["schema_version"] != GOLD_RUN_RESULT_SCHEMA_V2:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run result schema is unknown")
    result = GoldRunResult(
        run_id=_run_id(payload["run_id"], "final run id"),
        gold_run_id=payload["gold_run_id"],  # type: ignore[arg-type]
        manifest_sha256=payload["manifest_sha256"],  # type: ignore[arg-type]
        final_status=_enum(
            payload["final_status"], RunFinalStatus, "final status"
        ),  # type: ignore[arg-type]
        terminal_decision=_enum(
            payload["terminal_decision"], TerminalDecisionKind, "terminal decision"
        ),  # type: ignore[arg-type]
        terminal_decision_sha256=payload["terminal_decision_sha256"],  # type: ignore[arg-type]
        attempts=tuple(
            _attempt_summary(item) for item in _exact_list(payload["attempts"], "attempts")
        ),
        resolved_attempt_index=payload["resolved_attempt_index"],  # type: ignore[arg-type]
        fallback_arm_id=payload["fallback_arm_id"],  # type: ignore[arg-type]
        telemetry_completeness=_enum(
            payload["telemetry_completeness"],
            TelemetryCompleteness,
            "telemetry completeness",
        ),  # type: ignore[arg-type]
        telemetry_refs=_refs(payload["telemetry_refs"], "telemetry refs"),
        mechanism_activation=_enum(
            payload["mechanism_activation"],
            MechanismActivationStatus,
            "mechanism activation",
        ),  # type: ignore[arg-type]
        mechanism_activation_refs=_refs(
            payload["mechanism_activation_refs"], "mechanism activation refs"
        ),
        result_sha256=digest,
    )
    result.validate_identity()
    return result


def restore_manifest(store: RunRecordStore) -> GoldRunManifest:
    """Load the one exact persisted run manifest."""

    keys = store.iter_keys(kind=RecordKind.MANIFEST)
    if not keys:
        raise _fail(GoldRunFailureCode.RECORD_MISSING, "run manifest is not persisted")
    if keys != ("manifest",):
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "manifest namespace is not exact")
    record = store.get(kind=RecordKind.MANIFEST, key="manifest")
    if record is None:
        raise _fail(GoldRunFailureCode.RECORD_MISSING, "run manifest disappeared during scan")
    return _manifest_from_payload(record.payload)


@dataclass(frozen=True)
class AttemptState:
    """Durable truth about one attempt index."""

    attempt_index: int
    context: GoldAttemptContext
    result: GoldAttemptResult | None

    @property
    def started(self) -> bool:
        return True

    @property
    def finished(self) -> bool:
        return self.result is not None


@dataclass(frozen=True)
class RunState:
    """Recovered run state after exact cross-record validation."""

    manifest: GoldRunManifest
    attempts: tuple[AttemptState, ...]
    decisions: tuple[NextAttemptDecision, ...]
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
        return next(
            (item for item in self.decisions if item.attempt_index == attempt_index),
            None,
        )


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


def _same_run(record: object, manifest: GoldRunManifest, label: str) -> None:
    if (
        getattr(record, "run_id", None) != manifest.run_id
        or getattr(record, "gold_run_id", None) != manifest.gold_run_id
    ):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, f"{label} belongs to another run")


def _audit_attempt_progress(
    store: RunRecordStore,
    *,
    manifest: GoldRunManifest,
    attempts: tuple[AttemptState, ...],
) -> dict[int, object]:
    from synapse.experiments.gold.runner.run_progress import (
        AttemptProgressPhase,
        AttemptProgressState,
        load_attempt_progress,
        progress_key,
    )

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
        state = load_attempt_progress(store, manifest=manifest, context=context)
        observed[index] = state
    return observed


def _load_attempt_states(
    store: RunRecordStore,
    *,
    manifest: GoldRunManifest,
    context_indexes: tuple[int, ...],
    result_indexes: tuple[int, ...],
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
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "attempt context index differs from its key")
        result: GoldAttemptResult | None = None
        if index in result_indexes:
            result_record = store.get(kind=RecordKind.ATTEMPT_RESULT, key=str(index))
            if result_record is None:
                raise _fail(GoldRunFailureCode.RECORD_MISSING, "attempt result disappeared during scan")
            result = _attempt_result_from_payload(result_record.payload)
            _same_run(result, manifest, "attempt result")
            if result.attempt_index != index or result.context_sha256 != context.context_sha256:
                raise _fail(
                    GoldRunFailureCode.AUTHORITY_MISMATCH,
                    "attempt result differs from its durable context",
                )
            has_worker_context = context.phase_refs.worker_context_id is not None
            if result.outcome in (AttemptOutcome.DELIVERY_REFUSED, AttemptOutcome.DELIVERY_UNAVAILABLE) and has_worker_context:
                raise _fail(
                    GoldRunFailureCode.AUTHORITY_MISMATCH,
                    "pre-C1 delivery failure cannot fabricate worker context authority",
                )
            if result.outcome not in (
                AttemptOutcome.DELIVERY_REFUSED,
                AttemptOutcome.DELIVERY_UNAVAILABLE,
                AttemptOutcome.CONTROLLER_INTERRUPTED,
            ) and not has_worker_context:
                raise _fail(
                    GoldRunFailureCode.AUTHORITY_MISMATCH,
                    "a C1-classified result requires worker context authority",
                )
            if result.worker_result_ref is not None and not has_worker_context:
                raise _fail(
                    GoldRunFailureCode.AUTHORITY_MISMATCH,
                    "worker result authority exists without its worker context",
                )
            results[index] = result
        attempts.append(AttemptState(index, context, result))
    return tuple(attempts), results


def _load_decisions(
    store: RunRecordStore,
    *,
    manifest: GoldRunManifest,
    decision_indexes: tuple[int, ...],
    results: dict[int, GoldAttemptResult],
) -> tuple[NextAttemptDecision, ...]:
    decisions: list[NextAttemptDecision] = []
    for index in decision_indexes:
        decision_record = store.get(kind=RecordKind.DECISION, key=str(index))
        if decision_record is None:
            raise _fail(GoldRunFailureCode.RECORD_MISSING, "decision disappeared during scan")
        decision = _decision_from_payload(decision_record.payload)
        _same_run(decision, manifest, "decision")
        result = results[index]
        if (
            decision.attempt_index != index
            or decision.attempt_result_sha256 != result.result_sha256
        ):
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "decision differs from its exact attempt result",
            )
        decisions.append(decision)
    return tuple(decisions)


def _load_final_result(store: RunRecordStore) -> GoldRunResult | None:
    result_keys = store.iter_keys(kind=RecordKind.RUN_RESULT)
    if result_keys not in ((), ("final",)):
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "run-result namespace is not exact")
    final_record = store.get(kind=RecordKind.RUN_RESULT, key="final") if result_keys else None
    return None if final_record is None else _run_result_from_payload(final_record.payload)


def load_run_state(store: RunRecordStore) -> RunState:
    """Rebuild one resumable run prefix; every foreign or hidden edge fails closed.

    The local projections are kept in one linear assembly because contexts,
    results, decisions, checkpoints, and the final result must come from the same
    immutable store view before cross-record validation.
    """

    manifest = restore_manifest(store)
    maximum = manifest.config.max_attempts
    context_indexes = _attempt_indexes(store, kind=RecordKind.ATTEMPT_CONTEXT, maximum=maximum)
    result_indexes = _attempt_indexes(store, kind=RecordKind.ATTEMPT_RESULT, maximum=maximum)
    decision_indexes = _attempt_indexes(store, kind=RecordKind.DECISION, maximum=maximum)
    if context_indexes != tuple(range(1, len(context_indexes) + 1)):
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "attempt contexts are not a gapless prefix")
    if not set(result_indexes).issubset(context_indexes):
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "an attempt result exists without its context")
    if not set(decision_indexes).issubset(result_indexes):
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "a decision exists without its attempt result")
    attempts, results = _load_attempt_states(
        store,
        manifest=manifest,
        context_indexes=context_indexes,
        result_indexes=result_indexes,
    )
    decisions = _load_decisions(
        store,
        manifest=manifest,
        decision_indexes=decision_indexes,
        results=results,
    )
    final_result = _load_final_result(store)
    progress = _audit_attempt_progress(store, manifest=manifest, attempts=attempts)
    state = RunState(manifest, attempts, decisions, final_result)
    validate_state_consistency(
        manifest=state.manifest,
        attempts=state.attempts,
        decisions=state.decisions,
        final_result=state.final_result,
        progress=progress,
        build_run_result=build_run_result,
    )
    return state


def build_run_result(
    *,
    manifest: GoldRunManifest,
    attempts: tuple[GoldAttemptResult, ...],
    terminal_decision: NextAttemptDecision,
) -> GoldRunResult:
    """Build the exact Stage 11 final projection with honest unavailable metrics."""

    manifest.validate_identity()
    terminal_decision.validate_identity()
    _same_run(terminal_decision, manifest, "terminal decision")
    if terminal_decision.decision not in TERMINAL_DECISIONS:
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "run result requires a terminal decision")
    if type(attempts) is not tuple or not attempts:
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "run result requires attempt records")
    for attempt in attempts:
        if type(attempt) is not GoldAttemptResult:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "attempt result must be exact")
        attempt.validate_identity()
        _same_run(attempt, manifest, "attempt result")
    indexes = [item.attempt_index for item in attempts]
    if indexes != list(range(1, len(indexes) + 1)):
        raise _fail(GoldRunFailureCode.PHASE_INVALID, "run attempts must be a gapless prefix")
    if (
        terminal_decision.attempt_index != indexes[-1]
        or terminal_decision.attempt_result_sha256 != attempts[-1].result_sha256
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "terminal decision is not bound to the terminal attempt",
        )
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
        final_status=final_status_for_decision(terminal_decision.decision),
        terminal_decision=terminal_decision.decision,
        terminal_decision_sha256=terminal_decision.decision_sha256,
        attempts=summaries,
        resolved_attempt_index=resolved[0] if resolved else None,
        fallback_arm_id=terminal_decision.fallback_arm_id,
        telemetry_completeness=TelemetryCompleteness.UNAVAILABLE,
        telemetry_refs=(),
        mechanism_activation=MechanismActivationStatus.NOT_EVALUATED,
        mechanism_activation_refs=(),
    )
