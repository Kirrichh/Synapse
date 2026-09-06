"""Version-pinned, explicitly lossy OTel GenAI attribute export.

No collector or exporter is an accounting source. The canonical physical call
and receipt remain authoritative. This maps attributes only: GenAI logical
span lifetimes/retries cannot be inferred from one physical HTTP attempt.
"""

from .telemetry import LLMCallRecord, TELEMETRY_SCHEMA, UsageConsistency, reference

OTEL_REVISION = "94f432d7126f5884d30a2cdde6f4e89908ebb6fd"
EXPORT_PROFILE = "synapse.stage4.gold.otel-genai-attributes/v1"
_MAPPING = {
    "input_tokens": "gen_ai.usage.input_tokens",
    "output_tokens": "gen_ai.usage.output_tokens",
    "cache_read_tokens": "gen_ai.usage.cache_read.input_tokens",
    "cache_write_tokens": "gen_ai.usage.cache_creation.input_tokens",
    "thinking_tokens": "gen_ai.usage.reasoning.output_tokens",
}


def export_mapping_profile():
    return {"schema_version": EXPORT_PROFILE, "canonical_schema": TELEMETRY_SCHEMA,
        "external_repository": "https://github.com/open-telemetry/semantic-conventions-genai",
        "external_revision": OTEL_REVISION, "external_stability": "Development",
        "mapping": dict(_MAPPING), "lossy": True,
        "inclusion": "input/output include cache/reasoning subsets;subsets-are-not-extra-tokens",
        "omitted": ["provider_total_tokens", "mixed_unallocated_tokens", "money", "phase_completeness",
                    "lineage_authority", "logical_span_duration", "raw_prompts", "raw_responses"],
        "scope": "physical-call-attributes-only;not-a-logical-span-or-run-total",
        "metric_emission": "none;do-not-sum-this-export-with-canonical-or-trajectory-totals"}


def export_call_attributes(record: LLMCallRecord):
    if type(record) is not LLMCallRecord:
        raise TypeError("OTel call export requires the canonical call type")
    checked = LLMCallRecord.from_dict(record.to_dict())
    attributes = {"gen_ai.provider.name": checked.provider, "gen_ai.request.model": checked.model,
                  "gen_ai.operation.name": "chat"}
    if checked.response_model is not None:
        attributes["gen_ai.response.model"] = checked.response_model
    # A partial/mismatched source is not converted into apparently usable
    # metrics. The canonical record preserves every discrepancy and raw ref.
    if checked.usage.consistency is UsageConsistency.CONSISTENT:
        for source, target in _MAPPING.items():
            value = getattr(checked.usage, source)
            if value is not None:
                attributes[target] = value
    return {"mapping_profile": export_mapping_profile(),
            "canonical_ref": reference(checked.to_dict(), TELEMETRY_SCHEMA).to_dict(),
            "attributes": attributes, "canonical_usage_consistency": checked.usage.consistency.value}
