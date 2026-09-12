"""Real Mini model-plugin boundary: logical queries to captured HTTP attempts.

The installed Mini model owns prompt/tool formatting, retries and trajectory
serialization. This adapter binds its logical query to the parent's physical
HTTP inventory. It has no Gold import and cannot manufacture provider receipts.
"""

import hashlib
from importlib.metadata import version
import json
import math
import os
import urllib.request

import litellm
from minisweagent.models.litellm_model import LitellmModel
from minisweagent.exceptions import FormatError

from .provider_transport import MINI_ACCOUNTING_PROFILE, require_mini_dependencies
from .input_contract import SPLIT_INPUT_PROFILE_V1, WorkerInputViolation
from .provider_messages import PublicProviderConversation
from .local_edits import LOCAL_EDIT_PROFILE_V1


class MiniAccountingModel(LitellmModel):
    def __init__(self, **kwargs):
        require_mini_dependencies()
        input_profile = os.environ.get("SYNAPSE_MINI_INPUT_PROFILE")
        if input_profile not in {None, SPLIT_INPUT_PROFILE_V1, LOCAL_EDIT_PROFILE_V1}:
            raise WorkerInputViolation("Mini model input profile is unknown")
        self.requires_split_inputs = input_profile is not None
        self._public_conversation = None
        self._capture_endpoint = os.environ["SYNAPSE_MINI_CAPTURE_ENDPOINT"]
        self._capture_capability = os.environ["SYNAPSE_MINI_CAPTURE_CAPABILITY"]
        self._capture_model = os.environ["SYNAPSE_MINI_CAPTURE_MODEL"]
        self._capture_provider = os.environ["SYNAPSE_MINI_CAPTURE_PROVIDER"]
        if self._capture_provider not in {"openai", "gemini"}:
            raise RuntimeError("unknown captured provider")
        if kwargs.get("model_name") not in {self._capture_model, "openai/" + self._capture_model}:
            raise RuntimeError("worker model differs from the frozen capture model")
        model_kwargs = dict(kwargs.get("model_kwargs", {}))
        if any(key in model_kwargs for key in ("api_base", "api_key", "extra_headers", "stream", "fallbacks", "mock_response")):
            raise RuntimeError("worker configuration may not override the physical capture boundary")
        model_kwargs.update(api_base=self._capture_endpoint + "/v1", api_key=self._capture_capability,
                            stream=False, num_retries=0)
        kwargs.update(model_name="openai/" + self._capture_model, model_kwargs=model_kwargs)
        super().__init__(**kwargs)
        self._input_model_config = self.config.model_dump_json()

    def bind_public_conversation(self, conversation: PublicProviderConversation):
        if (not self.requires_split_inputs or type(conversation) is not PublicProviderConversation
                or self._public_conversation is not None):
            raise WorkerInputViolation("public conversation must be bound once to the configured Mini input")
        self._public_conversation = conversation

    def require_public_messages(self, messages):
        if not self.requires_split_inputs:
            return
        if self._public_conversation is None:
            raise WorkerInputViolation("separate Mini input has no public task binding")
        if self.config.model_dump_json() != self._input_model_config:
            raise WorkerInputViolation("local data cannot change the configured provider request profile")
        self._public_conversation.require_public(messages)

    def _capture(self, path: str, value: dict) -> dict:
        request = urllib.request.Request(self._capture_endpoint + path,
            data=json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self._capture_capability}, method="POST")
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read(65536))

    def query(self, messages, **kwargs):
        self.require_public_messages(messages)
        if self.requires_split_inputs and kwargs:
            raise WorkerInputViolation("local data cannot add provider request fields")
        if any(key in kwargs for key in ("api_base", "api_key", "extra_headers", "stream", "fallbacks", "mock_response")):
            raise RuntimeError("query may not bypass the captured transport")
        logical_request = json.dumps(messages, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        logical_id = self._capture("/capture/logical", {
            "request_identity": hashlib.sha256(logical_request).hexdigest(),
            "worker_profile": MINI_ACCOUNTING_PROFILE,
        })["logical_call_id"]
        try:
            message = super().query(messages, extra_headers={"X-Synapse-Logical-Call": logical_id}, **kwargs)
        except BaseException as exc:
            # FormatError messages are part of Mini's real trajectory too.
            for message in getattr(exc, "messages", ()):
                message.setdefault("extra", {})["capture_logical_id"] = logical_id
            if self.requires_split_inputs and type(exc) is FormatError:
                self._public_conversation.record_provider_messages(*getattr(exc, "messages", ()))
            self._capture("/capture/logical/finish", {"logical_call_id": logical_id, "status": "FAILED"})
            raise
        self._capture("/capture/logical/finish", {"logical_call_id": logical_id, "status": "COMPLETED"})
        message.setdefault("extra", {})["capture_logical_id"] = logical_id
        if self.requires_split_inputs:
            self._public_conversation.record_provider_messages(message)
        return message

    def _calculate_cost(self, response):
        if self._capture_provider != "gemini":
            return super()._calculate_cost(response)
        # The OpenAI prefix selects the wire protocol, not Google's tariffs.
        # Use the pinned SDK's Gemini estimator for Mini's operational budget.
        # Reconciliation continues to derive money only from provider receipts.
        input_cost, output_cost = litellm.cost_calculator.cost_per_token(
            model=self._capture_model, custom_llm_provider="gemini", usage_object=response.usage,
            service_tier=getattr(response, "service_tier", None))
        cost = input_cost + output_cost
        if not math.isfinite(cost) or cost <= 0.0:
            raise RuntimeError("Gemini SDK cost estimate is unavailable")
        return {"cost": cost}

    def serialize(self):
        value = super().serialize()
        # This is Mini's serialization boundary, before it writes a trajectory.
        # The response artifacts remain byte-exact; a transient local transport
        # capability is configuration, not retained provider evidence.
        config = value["info"]["config"]["model"]
        config["model_kwargs"].pop("api_key", None)
        value["info"]["capture_profile"] = MINI_ACCOUNTING_PROFILE
        value["info"]["capture_provider"] = self._capture_provider
        value["info"]["capture_dependencies"] = {
            name: version(name) for name in ("mini-swe-agent", "litellm", "openai")}
        return value
