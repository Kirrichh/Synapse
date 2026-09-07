"""OBS-05: physical provider inventory, canonical records and worker sources.

This evaluator has no writer, dispatcher, recovery or domain authority. It
reopens exact retained sources and compares them. The report's declared scope
is a capture prefix; the run owner additionally binds that prefix to actual
worker/phase records before using it in an execution completeness assessment.
"""

from collections import Counter
from enum import Enum
import json

from synapse.llm.capture import CaptureUnavailable
from synapse.worker.provider_transport import MINI_ACCOUNTING_PROFILE, MINI_MODEL_CLASS

from ..canonicalization import HashBoundRef
from ..persistence import PersistenceViolation
from .capture_store import CAPTURE_SCHEMA, CaptureCut, inspect_capture, read_source
from .telemetry import (
    TELEMETRY_SCHEMA, LLMCallRecord, TelemetryViolation, UsageConsistency, UsageProfile,
    call_record_from_capture, canonical, exact_fields, identity, normalize_usage, reference,
)

TELEMETRY_RECONCILIATION_SCHEMA = "synapse.stage4.gold.telemetry-reconciliation/v1"
COMPLETENESS_SCHEMA = "synapse.stage4.gold.completeness-manifest/v1"
TELEMETRY_EVALUATOR = "synapse.stage4.physical-telemetry-evaluator/v1"


class TelemetryStatus(str, Enum):
    COMPLETE = "COMPLETE"
    MISSING_CALL = "MISSING_CALL"
    TOTAL_MISMATCH = "TOTAL_MISMATCH"
    DOUBLE_COUNT_RISK = "DOUBLE_COUNT_RISK"
    SOURCE_INCONSISTENT = "SOURCE_INCONSISTENT"


_PRECEDENCE = (TelemetryStatus.SOURCE_INCONSISTENT, TelemetryStatus.DOUBLE_COUNT_RISK,
               TelemetryStatus.MISSING_CALL, TelemetryStatus.TOTAL_MISMATCH)
_SEAL = object()


class TelemetryReconciliationReport:
    __slots__ = ("_bytes", "_seal")

    def __new__(cls, *args, **kwargs):
        raise TypeError("physical reconciliation reports are evaluator-created")

    def __setattr__(self, name, value):
        raise TypeError("reconciliation evidence is immutable")

    @property
    def status(self) -> TelemetryStatus:
        return TelemetryStatus(self.to_dict()["status"])

    @property
    def reference(self) -> HashBoundRef:
        return reference(self.to_dict(), TELEMETRY_RECONCILIATION_SCHEMA)

    def to_dict(self) -> dict:
        if self._seal is not _SEAL:
            raise TelemetryViolation("untrusted reconciliation report")
        return json.loads(self._bytes)



def reconstruct_call_records(cut: CaptureCut, frames: tuple[dict, ...] | None = None) -> tuple[LLMCallRecord, ...]:
    """Expected immutable call bytes from the independently retained inventory."""
    frames = inspect_capture(cut) if frames is None else frames
    invocations = {r["payload"]["invocation_id"]: r["payload"] for r in frames if r["kind"] == "INVOCATION_OPEN"}
    ends = {r["payload"]["call_id"]: r for r in frames if r["kind"] in {"CALL_RESPONSE", "CALL_FAILED"}}
    result = []
    for frame in frames:
        if frame["kind"] != "CALL_STARTED":
            continue
        started = frame["payload"]
        end = ends.get(started["call_id"])
        response_ref = None if end is None else end["payload"].get("response_ref")
        result.append(call_record_from_capture(run_id=cut.run_id, invocation=invocations[started["invocation_id"]],
            started=started, terminal=None if end is None else end["payload"],
            response=None if response_ref is None else read_source(cut.root, HashBoundRef.from_dict(response_ref)),
            capture_ref=reference(end or frame, CAPTURE_SCHEMA)))
    return tuple(result)


def reconcile_telemetry(cut: CaptureCut) -> TelemetryReconciliationReport:
    if type(cut) is not CaptureCut:
        raise TelemetryViolation("telemetry reconciliation needs an exact retained capture cut")
    findings: list[dict] = []
    compared, observed, expected, duplicates = [], [], [], []
    totals = {"physical_provider_reported_tokens": None, "physical_component_tokens": None,
              "trajectory_success_response_tokens": None, "provider_reported_money": None,
              "money_completeness": "UNAVAILABLE"}

    def finding(status: TelemetryStatus, code: str, subject: str):
        findings.append({"status": status.value, "code": code, "subject": subject})

    frames = ()
    calls = ()
    try:
        frames = inspect_capture(cut)
        calls = reconstruct_call_records(cut, frames)
        for call in calls:
            expected.append(call.llm_call_id)
            ref = reference(call.to_dict(), TELEMETRY_SCHEMA)
            try:
                stored = json.loads(read_source(cut.root, ref))
                actual = LLMCallRecord.from_dict(stored)
                if actual.to_dict() != call.to_dict():
                    raise TelemetryViolation("retained observation differs from its source")
                observed.append(call.llm_call_id)
                compared.append(ref.to_dict())
            except (CaptureUnavailable, PersistenceViolation, ValueError, OSError):
                finding(TelemetryStatus.MISSING_CALL, "missing_or_changed_canonical_call", call.llm_call_id)
            status = {
                UsageConsistency.UNAVAILABLE: TelemetryStatus.MISSING_CALL,
                UsageConsistency.INCOMPLETE: TelemetryStatus.MISSING_CALL,
                UsageConsistency.TOTAL_MISMATCH: TelemetryStatus.TOTAL_MISMATCH,
                UsageConsistency.DOUBLE_COUNT_RISK: TelemetryStatus.DOUBLE_COUNT_RISK,
                UsageConsistency.SOURCE_INCONSISTENT: TelemetryStatus.SOURCE_INCONSISTENT,
            }.get(call.usage.consistency)
            if status is not None:
                finding(status, "physical_usage_" + call.usage.consistency.value.lower(), call.llm_call_id)
            if call.status == "UNKNOWN":
                finding(TelemetryStatus.MISSING_CALL, "provider_result_unknown", call.llm_call_id)
        for name, attr in (("physical_provider_reported_tokens", "provider_total_tokens"),
                           ("physical_component_tokens", "component_total_tokens")):
            amounts = [getattr(call.usage, attr) for call in calls]
            totals[name] = sum(amounts) if all(v is not None for v in amounts) else None

        provider_ids = [r["payload"]["provider_request_id"] for r in frames
                        if r["kind"] == "CALL_RESPONSE" and r["payload"]["provider_request_id"] is not None]
        duplicates = sorted(key for key, count in Counter(provider_ids).items() if count > 1)
        for key in duplicates:
            finding(TelemetryStatus.DOUBLE_COUNT_RISK, "provider_identity_reused_across_physical_requests", key)

        logical = {r["payload"]["logical_call_id"]: r["payload"] for r in frames if r["kind"] == "LOGICAL_OPEN"}
        closed = {r["payload"]["logical_call_id"]: r["payload"] for r in frames if r["kind"] == "LOGICAL_CLOSED"}
        for logical_id in logical:
            if logical_id not in closed or not any(c.logical_call_id == logical_id for c in calls):
                finding(TelemetryStatus.MISSING_CALL, "logical_request_not_fully_observed", logical_id)
        ended = {r["payload"]["invocation_id"]: r["payload"] for r in frames if r["kind"] == "INVOCATION_CLOSED"}
        response_totals = []
        for frame in frames:
            if frame["kind"] != "INVOCATION_OPEN":
                continue
            invocation = frame["payload"]
            name = invocation["invocation_id"]
            end = ended.get(name)
            if end is None or "trajectory_ref" not in end or end["process_status"] != "EXITED":
                finding(TelemetryStatus.MISSING_CALL, "worker_trajectory_or_terminal_boundary_missing", name)
                continue
            raw = read_source(cut.root, HashBoundRef.from_dict(end["trajectory_ref"]))
            trajectory = json.loads(raw)
            if (invocation["worker_profile"] != MINI_ACCOUNTING_PROFILE
                    or trajectory.get("info", {}).get("capture_profile") != MINI_ACCOUNTING_PROFILE
                    or trajectory.get("info", {}).get("config", {}).get("model_type") != MINI_MODEL_CLASS):
                finding(TelemetryStatus.SOURCE_INCONSISTENT, "worker_accounting_profile_differs", name)
                continue
            messages = [m for m in trajectory["messages"] if m.get("role") == "assistant" and "response" in m.get("extra", {})]
            model_calls = trajectory.get("info", {}).get("model_stats", {}).get("api_calls")
            if type(model_calls) is not int or model_calls != len(messages):
                finding(TelemetryStatus.SOURCE_INCONSISTENT, "worker_inventory_differs_from_responses", name)
            seen = set()
            for message in messages:
                extra = message["extra"]
                logical_id = extra.get("capture_logical_id")
                if logical_id not in logical or logical[logical_id]["invocation_id"] != name:
                    finding(TelemetryStatus.MISSING_CALL, "worker_response_has_no_capture", name)
                    continue
                if logical_id in seen:
                    finding(TelemetryStatus.DOUBLE_COUNT_RISK, "duplicate_worker_response", logical_id)
                seen.add(logical_id)
                responses = [call for call in calls if call.logical_call_id == logical_id and call.status == "COMPLETED"]
                if len(responses) != 1:
                    finding(TelemetryStatus.SOURCE_INCONSISTENT, "worker_success_does_not_select_one_physical_response", logical_id)
                    continue
                usage = normalize_usage(UsageProfile(invocation["usage_profile"]), extra["response"].get("usage"))
                if usage.to_dict() != responses[0].usage.to_dict():
                    finding(TelemetryStatus.SOURCE_INCONSISTENT, "worker_and_raw_provider_usage_differ", logical_id)
                response_totals.append(usage.provider_total_tokens)
            expected_responses = {call.logical_call_id for call in calls
                if call.status == "COMPLETED" and logical[call.logical_call_id]["invocation_id"] == name}
            for missing in sorted(expected_responses - seen):
                finding(TelemetryStatus.MISSING_CALL, "captured_response_missing_from_worker_trajectory", missing)
        totals["trajectory_success_response_tokens"] = (
            sum(response_totals) if all(v is not None for v in response_totals) else None)
    except (CaptureUnavailable, PersistenceViolation, TelemetryViolation, ValueError, OSError, KeyError, TypeError):
        finding(TelemetryStatus.SOURCE_INCONSISTENT, "retained_capture_source_unavailable_or_changed", cut.ledger_ref.sha256)

    status = next((v for v in _PRECEDENCE if any(f["status"] == v.value for f in findings)), TelemetryStatus.COMPLETE)
    payload = {"schema_version": TELEMETRY_RECONCILIATION_SCHEMA, "scope": "provider-capture-prefix/v1",
        "run_id": cut.run_id, "capture_cut": cut.to_dict(), "status": status.value,
        "discrepancies": findings, "compared_identities": compared, "source_totals": totals,
        "completeness_manifest": {"schema_version": COMPLETENESS_SCHEMA,
            "expected_physical_call_ids": expected, "observed_physical_call_ids": observed,
            "missing_call_ids": sorted(set(expected) - set(observed)), "duplicate_provider_ids": duplicates,
            "expected_logical_call_ids": [r["payload"]["logical_call_id"] for r in frames if r["kind"] == "LOGICAL_OPEN"],
            "expected_worker_invocation_ids": [r["payload"]["invocation_id"] for r in frames if r["kind"] == "INVOCATION_OPEN"],
            "observed_worker_invocation_ids": [r["payload"]["invocation_id"] for r in frames if r["kind"] == "INVOCATION_CLOSED"],
            "reconciliation_status": status.value},
        "decision_authority": TELEMETRY_EVALUATOR,
        "consumer_revalidation": "reopen-exact-capture-prefix-and-every-compared-source/v1",
        "status_precedence": [s.value for s in _PRECEDENCE]}
    payload["report_id"] = identity("synapse.telemetry.reconciliation/v1", payload)
    report = object.__new__(TelemetryReconciliationReport)
    object.__setattr__(report, "_bytes", canonical(payload))
    object.__setattr__(report, "_seal", _SEAL)
    return report


RUN_TELEMETRY_SCHEMA = "synapse.stage4.gold.run-telemetry-reconciliation/v1"


def reconcile_run_telemetry(*, run_root, cut: CaptureCut | None, through_attempt: str | None = None):
    """Bind the physical inventory to independent, durable worker deliveries.

    Stage 3A's existing accounting contract is read without changing it. Its
    aggregate is a comparison source, never a manufactured provider call.
    """
    from ..admission_journal import FileSnapshotFence
    from ..runner.records import RunRecordStore
    from ..runner.state_machine import load_run_state
    from ..runner.run_progress import load_attempt_progress, AttemptProgressPhase, require_progress_payload
    from ..runner.completed_delivery_codec import restore_completed_worker_delivery
    from synapse.worker import ExternalWorkerUsage, ExternalWorkerTokenStatus
    from synapse.experiments.swebench.telemetry import token_accounting_from_worker_usage, usage_source_from_worker_status
    from .telemetry import SourceReconciliationReport

    findings, compared, expected, observed, stage3a = [], [], [], [], []
    totals = {"physical_provider_reported_tokens": None, "physical_component_tokens": None,
              "worker_reported_tokens": None, "provider_reported_money": None,
              "money_completeness": "UNAVAILABLE"}
    sources = {"run_root": str(run_root), "capture_cut": None if cut is None else cut.to_dict(),
               "through_attempt": through_attempt}
    try:
        store = RunRecordStore(run_root, mutation_fence=FileSnapshotFence(run_root / "run-coordinator", read_only=True), read_only=True)
        state = load_run_state(store)
        attempts = state.attempts
        if through_attempt is not None:
            positions = [i for i, item in enumerate(attempts) if item.context.attempt_id.value == through_attempt]
            if len(positions) != 1:
                raise ValueError("capture prefix must identify a retained attempt")
            attempts = attempts[:positions[0] + 1]
        sources["manifest_sha256"] = state.manifest.manifest_sha256
        sources["manifest_ref"] = reference(state.manifest.stored_dict(), state.manifest.payload()["schema_version"]).to_dict()
        frames = () if cut is None else inspect_capture(cut)
        if cut is None:
            findings.append({"status": "MISSING_CALL", "code": "required_capture_is_unavailable"})
        else:
            if frames[0]["payload"]["manifest_ref"] != sources["manifest_ref"] or cut.run_id != state.manifest.run_id.value:
                raise ValueError("capture inventory belongs to another run")
            report = reconcile_telemetry(cut)
            findings.extend(report.to_dict()["discrepancies"])
            compared.extend(report.to_dict()["compared_identities"])
            totals.update(report.to_dict()["source_totals"])
        invocations = {r["payload"]["invocation_id"]: r["payload"] for r in frames if r["kind"] == "INVOCATION_OPEN"}
        amounts = []
        for attempt in attempts:
            progress = load_attempt_progress(store, manifest=state.manifest, context=attempt.context)
            start = progress.get(AttemptProgressPhase.DELIVERY_STARTED)
            worker = progress.get(AttemptProgressPhase.WORKER_COMPLETED)
            if start is not None and worker is None:
                findings.append({"status": "MISSING_CALL", "code": "dispatch_has_no_retained_worker_completion", "subject": attempt.context.attempt_id.value})
            if worker is None:
                continue
            raw, ref = require_progress_payload(worker)
            completed = restore_completed_worker_delivery(raw, expected_ref=ref)
            binding = completed.invocation
            name = binding.invocation_id
            expected.append(name)
            compared.append(ref.to_dict())
            captured = invocations.get(name)
            if captured is None:
                findings.append({"status": "MISSING_CALL", "code": "actual_worker_has_no_capture_inventory", "subject": name})
            else:
                invocation_raw = json.loads(read_source(cut.root, HashBoundRef.from_dict(captured["invocation_ref"])))
                binding_fields = {"invocation_id": name, "attempt_id": binding.attempt_id, "context_id": binding.context_id,
                    "payload_sha256": binding.payload_sha256, "payload_byte_length": binding.payload_byte_length,
                    "envelope_sha256": binding.envelope_sha256}
                if invocation_raw != binding_fields or captured["attempt_id"] != attempt.context.attempt_id.value:
                    findings.append({"status": "SOURCE_INCONSISTENT", "code": "capture_and_actual_worker_binding_differ", "subject": name})
                else:
                    observed.append(name)
                ends = [r["payload"] for r in frames if r["kind"] == "INVOCATION_CLOSED" and r["payload"]["invocation_id"] == name]
                if len(ends) == 1 and "trajectory_ref" in ends[0]:
                    trajectory = json.loads(read_source(cut.root, HashBoundRef.from_dict(ends[0]["trajectory_ref"])))
                    usage = [m["extra"]["response"].get("usage") for m in trajectory["messages"]
                             if m.get("role") == "assistant" and "response" in m.get("extra", {})]
                    normalized = [normalize_usage(UsageProfile(captured["usage_profile"]), value) for value in usage]
                    counts = [u.provider_total_tokens for u in normalized]
                    expected_total = sum(counts) if all(v is not None for v in counts) else None
                    if completed.worker_result.usage.total_tokens != expected_total:
                        findings.append({"status": "TOTAL_MISMATCH", "code": "stage3a_worker_aggregate_differs_from_actual_trajectory", "subject": name})
            usage = completed.worker_result.usage
            external = ExternalWorkerUsage(token_status=ExternalWorkerTokenStatus(usage.token_status.value),
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, thinking_tokens=usage.thinking_tokens,
                total_tokens=usage.total_tokens, thinking_included=usage.thinking_included, diagnostics=dict(usage.diagnostics))
            aggregate_ref = ref
            if progress.get(AttemptProgressPhase.C1_COMPLETED) is not None:
                from ..runner.c1_boundary import inspect_c1_worker_usage
                aggregate_ref, written = inspect_c1_worker_usage(path=run_root / "gold_attempts.jsonl",
                    gold_run_id=state.manifest.gold_run_id, attempt_id=attempt.context.attempt_id.value)
                if written.to_dict() != external.to_dict():
                    findings.append({"status": "SOURCE_INCONSISTENT", "code": "durable_stage3a_aggregate_differs_from_worker_delivery", "subject": name})
                external = written
                compared.append(aggregate_ref.to_dict())
            legacy = token_accounting_from_worker_usage(external, usage_source=usage_source_from_worker_status(external.token_status))
            stage3a.append({"worker_ref": ref.to_dict(), "aggregate_ref": aggregate_ref.to_dict(), "accounting": legacy.to_dict(),
                "scope": "worker-trajectory-aggregate", "add_to_provider_total": False})
            amounts.append(usage.total_tokens)
        for foreign in sorted(set(invocations) - set(expected)):
            findings.append({"status": "SOURCE_INCONSISTENT", "code": "capture_invocation_has_no_matching_worker_completion", "subject": foreign})
        if state.final_result is None and through_attempt is None:
            findings.append({"status": "MISSING_CALL", "code": "run_has_not_closed_its_dispatch_inventory"})
        totals["worker_reported_tokens"] = sum(amounts) if all(v is not None for v in amounts) else None
    except (ValueError, TypeError, OSError, RuntimeError, KeyError) as exc:
        findings.append({"status": "SOURCE_INCONSISTENT", "code": "run_accounting_source_unavailable_or_changed", "subject": type(exc).__name__})
    sources["expected_worker_invocations"] = expected
    sources["observed_worker_invocations"] = observed
    sources["stage3a_comparisons"] = stage3a
    # This frozen plan/replay profile has no gateway LLM operation. Adding one
    # requires its own physical boundary and a new frozen accounting profile.
    sources["gateway_scope"] = "NO_GATEWAY_CALLS_IN_PURE_CVM_PLAN_PROFILE"
    status = next((v.value for v in _PRECEDENCE if any(f["status"] == v.value for f in findings)), "COMPLETE")
    return SourceReconciliationReport.evaluated(schema=RUN_TELEMETRY_SCHEMA, source=sources, status=status,
        discrepancies=findings, compared=compared, authority="synapse.stage4.run-telemetry-evaluator/v1",
        precedence=[v.value for v in _PRECEDENCE], totals=totals)
