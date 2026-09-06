"""OBS-01/02: immutable observation identities and provider accounting semantics.

Canonical input/output counts include their cache/reasoning subsets. Raw
provider totals stay separate from the calculated component sum. Missing
measurements remain unknown; neither a parser nor an exporter grants evidence
authority. Physical completeness belongs to the reconciliation evaluator.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
import hashlib
import re

from ..canonicalization import (
    HashBoundRef, RefKind, STAGE4_CANONICAL_PROFILE_V1,
    STABLE_CANONICAL_CODEC_ID, canonicalize_stage4_payload,
)

TELEMETRY_SCHEMA = "synapse.stage4.gold.telemetry/v1"
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")


class TelemetryViolation(ValueError):
    """An observation cannot be interpreted under the declared contract."""


class Phase(str, Enum):
    RUN = "RUN"
    SNAPSHOT = "SNAPSHOT"
    RETRIEVAL = "RETRIEVAL"
    REPLAY = "REPLAY"
    INTENT = "INTENT"
    PLAN = "PLAN"
    WORKER = "WORKER"
    CONTROLLED_CHANGE = "CONTROLLED_CHANGE"
    ORACLE = "ORACLE"
    OUTCOME = "OUTCOME"
    PUBLICATION = "PUBLICATION"
    TELEMETRY = "TELEMETRY"


class Component(str, Enum):
    LLM_GATEWAY = "LLM_GATEWAY"
    WORKER_SUBPROCESS = "WORKER_SUBPROCESS"
    COGNITIVE_VM = "COGNITIVE_VM"
    VERIFICATION = "VERIFICATION"
    RETRIEVAL = "RETRIEVAL"
    ADMISSION = "ADMISSION"
    PUBLICATION = "PUBLICATION"
    LIFECYCLE = "LIFECYCLE"
    RECONCILIATION = "RECONCILIATION"


class UsageProfile(str, Enum):
    OPENAI_CHAT = "openai-chat-usage/v1"
    GEMINI_NATIVE = "gemini-generate-content-usage/v1"
    ANTHROPIC_MESSAGES = "anthropic-messages-usage/v1"


class UsageConsistency(str, Enum):
    CONSISTENT = "CONSISTENT"
    UNAVAILABLE = "UNAVAILABLE"
    INCOMPLETE = "INCOMPLETE"
    TOTAL_MISMATCH = "TOTAL_MISMATCH"
    DOUBLE_COUNT_RISK = "DOUBLE_COUNT_RISK"
    SOURCE_INCONSISTENT = "SOURCE_INCONSISTENT"


def canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(value, profile_id=STAGE4_CANONICAL_PROFILE_V1,
                                       codec_id=STABLE_CANONICAL_CODEC_ID)


def reference(value: object, schema: str) -> HashBoundRef:
    raw = canonical(value)
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(RefKind.ARTIFACT, digest, schema, digest, len(raw), "application/json")


def identity(domain: str, value: object) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\0" + canonical(value)).hexdigest()


def identifier(value: object) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise TelemetryViolation("malformed observation identifier")
    return value


def exact_fields(value: object, expected: set[str]) -> dict:
    if type(value) is not dict or set(value) != expected:
        raise TelemetryViolation("unknown observation record shape")
    return value


def _count(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0 or value > 2**63 - 1:
        raise TelemetryViolation("measurement must be an unsigned bounded integer or unknown")
    return value


@dataclass(frozen=True)
class CoreTelemetryEnvelope:
    run_id: str
    attempt_id: str
    phase: Phase
    component: Component
    occurrence_id: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    clock_domain: str
    started_unix_ns: int
    started_monotonic_ns: int
    ended_monotonic_ns: int | None
    lineage_refs: tuple[HashBoundRef, ...]

    def __post_init__(self) -> None:
        for value in (self.run_id, self.attempt_id, self.occurrence_id, self.clock_domain):
            identifier(value)
        if type(self.phase) is not Phase or type(self.component) is not Component:
            raise TelemetryViolation("closed phase and component required")
        for value, width in ((self.trace_id, 32), (self.span_id, 16), (self.parent_span_id, 16)):
            if value is None and width == 16:
                continue
            if type(value) is not str or re.fullmatch(r"[0-9a-f]{" + str(width) + "}", value) is None or int(value, 16) == 0:
                raise TelemetryViolation("invalid trace correlation")
        if self.span_id is None:
            raise TelemetryViolation("span identity is required")
        for value in (self.started_unix_ns, self.started_monotonic_ns, self.ended_monotonic_ns):
            _count(value)
        if self.started_unix_ns is None or self.started_monotonic_ns is None:
            raise TelemetryViolation("start clock samples are required")
        if self.ended_monotonic_ns is not None and self.ended_monotonic_ns < self.started_monotonic_ns:
            raise TelemetryViolation("duration crosses an invalid clock interval")
        if type(self.lineage_refs) is not tuple or any(type(r) is not HashBoundRef for r in self.lineage_refs):
            raise TelemetryViolation("lineage requires exact retained references")
        if len({canonical(r.to_dict()) for r in self.lineage_refs}) != len(self.lineage_refs):
            raise TelemetryViolation("duplicate lineage reference")

    def to_dict(self) -> dict:
        return {
            **{f.name: getattr(self, f.name) for f in fields(self)},
            "phase": self.phase.value, "component": self.component.value,
            "lineage_refs": [r.to_dict() for r in self.lineage_refs],
            "timestamp_semantics": "unix-ns-correlation;monotonic-ns-same-clock-domain/v1",
        }

    @classmethod
    def from_dict(cls, value: object) -> CoreTelemetryEnvelope:
        v = exact_fields(value, {f.name for f in fields(cls)} | {"timestamp_semantics"})
        args = {f.name: v[f.name] for f in fields(cls)}
        args.update(phase=Phase(v["phase"]), component=Component(v["component"]),
                    lineage_refs=tuple(HashBoundRef.from_dict(r) for r in v["lineage_refs"]))
        result = cls(**args)
        if result.to_dict() != v:
            raise TelemetryViolation("clock semantics changed")
        return result


@dataclass(frozen=True)
class TokenUsage:
    profile: UsageProfile
    input_tokens: int | None
    output_tokens: int | None
    thinking_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    provider_total_tokens: int | None
    component_total_tokens: int | None
    mixed_unallocated_tokens: int | None
    consistency: UsageConsistency
    discrepancies: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.profile) is not UsageProfile or type(self.consistency) is not UsageConsistency:
            raise TelemetryViolation("unknown usage semantics")
        for f in fields(self):
            if f.name.endswith("tokens"):
                _count(getattr(self, f.name))
        if type(self.discrepancies) is not tuple or any(type(v) is not str for v in self.discrepancies):
            raise TelemetryViolation("invalid usage discrepancies")

    def to_dict(self) -> dict:
        return {**{f.name: getattr(self, f.name) for f in fields(self)},
                "profile": self.profile.value, "consistency": self.consistency.value,
                "discrepancies": list(self.discrepancies)}


def normalize_usage(profile: UsageProfile, raw: object) -> TokenUsage:
    """Interpret retained provider usage under its explicit inclusion contract.

    Anthropic has no reported total; its calculated total is not relabelled as
    one. Optional provider fields have profile-specific zero semantics only
    when a valid usage object exists. An absent object is never a zero call.
    """
    if type(profile) is not UsageProfile:
        raise TelemetryViolation("an explicit provider usage profile is required")
    if raw is None or raw == {}:
        return TokenUsage(profile, *(None for _ in range(8)), UsageConsistency.UNAVAILABLE, ("missing_usage",))
    if type(raw) is not dict:
        return TokenUsage(profile, *(None for _ in range(8)), UsageConsistency.SOURCE_INCONSISTENT, ("malformed_usage",))
    try:
        if profile is UsageProfile.OPENAI_CHAT:
            inputs, outputs, total = (_count(raw.get(k)) for k in ("prompt_tokens", "completion_tokens", "total_tokens"))
            detail_in = raw.get("prompt_tokens_details") or {}
            detail_out = raw.get("completion_tokens_details") or {}
            if type(detail_in) is not dict or type(detail_out) is not dict:
                raise TelemetryViolation("invalid token details")
            read, write, thinking = _count(detail_in.get("cached_tokens", 0)), None, _count(detail_out.get("reasoning_tokens", 0))
        elif profile is UsageProfile.GEMINI_NATIVE:
            inputs = _count(raw.get("promptTokenCount"))
            visible = _count(raw.get("candidatesTokenCount"))
            thinking = _count(raw.get("thoughtsTokenCount", 0))
            outputs = None if visible is None or thinking is None else visible + thinking
            total = _count(raw.get("totalTokenCount"))
            read, write = _count(raw.get("cachedContentTokenCount", 0)), None
            # Tool-use prompt tokens have their own scope. Preserve the mismatch
            # until that provider profile has a proved allocation contract.
            if raw.get("toolUsePromptTokenCount", 0) not in (None, 0):
                raise TelemetryViolation("unallocated tool-use prompt tokens")
        else:
            uncached = _count(raw.get("input_tokens"))
            read, write = _count(raw.get("cache_read_input_tokens", 0)), _count(raw.get("cache_creation_input_tokens", 0))
            inputs = None if None in (uncached, read, write) else uncached + read + write
            outputs = _count(raw.get("output_tokens"))
            thinking, total = None, None
        calculated = None if inputs is None or outputs is None else inputs + outputs
        discrepancy = []
        consistency = UsageConsistency.CONSISTENT
        if calculated is None or (profile is not UsageProfile.ANTHROPIC_MESSAGES and total is None):
            consistency = UsageConsistency.INCOMPLETE
            discrepancy.append("missing_required_component_or_total")
        if inputs is not None and (any(x is not None and x > inputs for x in (read, write)) or
                                   read is not None and write is not None and read + write > inputs):
            consistency = UsageConsistency.DOUBLE_COUNT_RISK
            discrepancy.append("cache_subsets_exceed_input")
        if outputs is not None and thinking is not None and thinking > outputs:
            consistency = UsageConsistency.DOUBLE_COUNT_RISK
            discrepancy.append("thinking_subset_exceeds_output")
        if total is not None and calculated is not None and total != calculated:
            if consistency is not UsageConsistency.DOUBLE_COUNT_RISK:
                consistency = UsageConsistency.TOTAL_MISMATCH
            discrepancy.append("provider_total_differs_from_components")
        remainder = None if total is None or calculated is None else max(0, total - calculated)
        return TokenUsage(profile, inputs, outputs, thinking, read, write, total,
                          calculated, remainder, consistency, tuple(discrepancy))
    except (TelemetryViolation, TypeError, OverflowError):
        return TokenUsage(profile, *(None for _ in range(8)), UsageConsistency.SOURCE_INCONSISTENT, ("invalid_usage_fields",))


@dataclass(frozen=True)
class LLMCallRecord:
    envelope: CoreTelemetryEnvelope
    llm_call_id: str
    logical_call_id: str
    provider: str
    model: str
    service_tier: str | None
    request_ref: HashBoundRef
    response_ref: HashBoundRef | None
    capture_ref: HashBoundRef
    status: str
    usage: TokenUsage
    reported_cost_decimal: str | None = None
    reported_cost_currency: str | None = None

    def __post_init__(self) -> None:
        if type(self.envelope) is not CoreTelemetryEnvelope or type(self.usage) is not TokenUsage:
            raise TelemetryViolation("exact canonical call records required")
        for value in (self.llm_call_id, self.logical_call_id, self.provider, self.model):
            identifier(value)
        for value in (self.request_ref, self.capture_ref):
            if type(value) is not HashBoundRef:
                raise TelemetryViolation("call requires physical evidence references")
        if self.response_ref is not None and type(self.response_ref) is not HashBoundRef:
            raise TelemetryViolation("invalid response reference")
        if self.status not in ("COMPLETED", "FAILED", "UNKNOWN"):
            raise TelemetryViolation("unknown physical call status")
        if self.status == "COMPLETED" and self.response_ref is None:
            raise TelemetryViolation("completed call lacks retained response")
        if self.service_tier is not None:
            identifier(self.service_tier)
        if (self.reported_cost_decimal is None) != (self.reported_cost_currency is None):
            raise TelemetryViolation("reported money requires amount and currency")
        if self.reported_cost_decimal is not None:
            if type(self.reported_cost_decimal) is not str or re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", self.reported_cost_decimal) is None:
                raise TelemetryViolation("cost must be an exact non-negative decimal")
            if type(self.reported_cost_currency) is not str or re.fullmatch(r"[A-Z]{3}", self.reported_cost_currency) is None:
                raise TelemetryViolation("invalid cost currency")

    def payload(self) -> dict:
        return {"schema_version": TELEMETRY_SCHEMA, "record_class": "LLMCallRecord",
                "envelope": self.envelope.to_dict(), "llm_call_id": self.llm_call_id,
                "logical_call_id": self.logical_call_id, "provider": self.provider,
                "model": self.model, "service_tier": self.service_tier,
                "accounting_category": "PROVIDER_CALL", "request_ref": self.request_ref.to_dict(),
                "response_ref": None if self.response_ref is None else self.response_ref.to_dict(),
                "capture_ref": self.capture_ref.to_dict(), "status": self.status,
                "usage": self.usage.to_dict(), "reported_cost_decimal": self.reported_cost_decimal,
                "reported_cost_currency": self.reported_cost_currency}

    @property
    def record_id(self) -> str:
        return identity("synapse.telemetry.record/v1", self.payload())

    def to_dict(self) -> dict:
        return {**self.payload(), "record_id": self.record_id}

    @classmethod
    def from_dict(cls, value: object) -> LLMCallRecord:
        v = exact_fields(value, {"schema_version", "record_class", "envelope", "llm_call_id", "logical_call_id",
            "provider", "model", "service_tier", "accounting_category", "request_ref", "response_ref",
            "capture_ref", "status", "usage", "reported_cost_decimal", "reported_cost_currency", "record_id"})
        u = exact_fields(v["usage"], {f.name for f in fields(TokenUsage)})
        usage = TokenUsage(**{**u, "profile": UsageProfile(u["profile"]),
            "consistency": UsageConsistency(u["consistency"]), "discrepancies": tuple(u["discrepancies"])})
        record = cls(CoreTelemetryEnvelope.from_dict(v["envelope"]), v["llm_call_id"], v["logical_call_id"],
            v["provider"], v["model"], v["service_tier"], HashBoundRef.from_dict(v["request_ref"]),
            None if v["response_ref"] is None else HashBoundRef.from_dict(v["response_ref"]),
            HashBoundRef.from_dict(v["capture_ref"]), v["status"], usage,
            v["reported_cost_decimal"], v["reported_cost_currency"])
        if record.to_dict() != v:
            raise TelemetryViolation("canonical observation identity or semantics changed")
        return record
