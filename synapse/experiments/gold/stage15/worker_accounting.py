"""Bind the existing Mini invocation to its frozen run's capture inventory.

This adapter joins neutral worker transport fields to Gold hash-bound retained
sources. It does not dispatch a worker, synthesize calls from Stage 3A totals,
or participate in correctness and publication decisions.
"""

import re

from synapse.worker.provider_transport import (
    MINI_ACCOUNTING_PROFILE, MiniProviderConfiguration, MiniProviderTransport,
)

from .capture_store import CaptureStore
from .telemetry import UsageProfile


class WorkerAccounting:
    def __init__(self, *, store: CaptureStore, configuration: MiniProviderConfiguration):
        if type(store) is not CaptureStore or type(configuration) is not MiniProviderConfiguration:
            raise TypeError("worker accounting requires exact production bindings")
        self.store, self.configuration = store, configuration

    def begin_invocation(self, *, invocation_id: str, attempt_id: str, context_id: str,
                         payload_sha256: str, payload_byte_length: int,
                         envelope_sha256: str) -> MiniProviderTransport:
        invocation = {"invocation_id": invocation_id, "attempt_id": attempt_id, "context_id": context_id,
                      "payload_sha256": payload_sha256, "payload_byte_length": payload_byte_length,
                      "envelope_sha256": envelope_sha256}
        capture = self.store.open_invocation(invocation_id=invocation_id, attempt_id=attempt_id,
            invocation_payload=invocation, provider="openai", model=self.configuration.model,
            profile=UsageProfile.OPENAI_CHAT, worker_profile=MINI_ACCOUNTING_PROFILE)
        return MiniProviderTransport(configuration=self.configuration, capture=capture)


def validate_accounting_declaration(worker: dict) -> dict:
    value = worker.get("accounting")
    if (type(value) is not dict or set(value) != {"profile", "endpoint", "credential_env"}
            or value["profile"] != MINI_ACCOUNTING_PROFILE
            or type(value["credential_env"]) is not str
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", value["credential_env"]) is None):
        raise ValueError("Stage 15 requires a complete frozen provider accounting profile")
    # Validate URL/model without reading or persisting any credential.
    MiniProviderConfiguration(model=worker["model"], api_key="profile-validation", endpoint=value["endpoint"])
    return dict(value)
