"""Bind an admitted agent's model calls to its frozen run's capture inventory.

This owner joins the agent runtime's neutral model broker to Gold hash-bound
retained sources. It does not dispatch an agent, synthesize calls from Stage 3A
totals, or participate in correctness and publication decisions.
"""

from pathlib import Path

from synapse.agents.model_broker import MODEL_BROKER_PROFILE, ModelConnection

from .capture_store import CaptureStore
from .telemetry import UsageProfile, reference


def selected_model_access(data) -> dict | None:
    """The frozen selected agent's declared model access, or None without one."""
    selection = data.get("agent_selection")
    if selection is None:
        return None
    from synapse.agents.configuration import profile_from_dict
    for definition in data["declaration"]["agents"]["profiles"]:
        if profile_from_dict(definition["profile"]).profile_id == selection["profile_id"]:
            native = definition["native"]
            return native.get("model_access") if type(native) is dict else None
    raise ValueError("frozen agent selection names no declared profile")


def create_worker_accounting(inputs):
    """Resolve the frozen run's capture owner once at the application boundary."""
    data, manifest = inputs.data, inputs.manifest
    if selected_model_access(data) is None:
        return None
    root = Path(data["run_root"]) / "stage15"
    root.mkdir(exist_ok=True)
    return WorkerAccounting(store=CaptureStore(root=root / "capture", run_id=manifest.run_id.value,
        manifest_ref=reference(manifest.stored_dict(), manifest.payload()["schema_version"])))


class WorkerAccounting:
    """The model-accounting port Gold hands to the agent runtime."""

    def __init__(self, *, store: CaptureStore):
        if type(store) is not CaptureStore:
            raise TypeError("worker accounting requires an exact capture store")
        self.store = store

    def open_capture(self, *, invocation: dict, connection: ModelConnection):
        if type(connection) is not ModelConnection or type(invocation) is not dict or set(invocation) != {
                "invocation_id", "attempt_id", "context_id", "payload_sha256", "payload_byte_length", "envelope_sha256"}:
            raise TypeError("worker accounting requires the exact invocation binding")
        return self.store.open_invocation(invocation_id=invocation["invocation_id"],
            attempt_id=invocation["attempt_id"], invocation_payload=dict(invocation),
            provider=connection.provider, model=connection.model,
            profile=(UsageProfile.GEMINI_OPENAI_CHAT if connection.provider == "gemini" else UsageProfile.OPENAI_CHAT),
            worker_profile=MODEL_BROKER_PROFILE)
