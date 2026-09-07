"""Real Mini model-plugin boundary: logical queries to captured HTTP attempts.

The installed Mini model owns prompt/tool formatting, retries and trajectory
serialization. This adapter binds its logical query to the parent's physical
HTTP inventory. It has no Gold import and cannot manufacture provider receipts.
"""

import hashlib
from importlib.metadata import version
import json
import os
import urllib.request

from minisweagent.models.litellm_model import LitellmModel

from .provider_transport import MINI_ACCOUNTING_PROFILE, require_mini_dependencies


class MiniAccountingModel(LitellmModel):
    def __init__(self, **kwargs):
        require_mini_dependencies()
        self._capture_endpoint = os.environ["SYNAPSE_MINI_CAPTURE_ENDPOINT"]
        self._capture_capability = os.environ["SYNAPSE_MINI_CAPTURE_CAPABILITY"]
        self._capture_model = os.environ["SYNAPSE_MINI_CAPTURE_MODEL"]
        if kwargs.get("model_name") not in {self._capture_model, "openai/" + self._capture_model}:
            raise RuntimeError("worker model differs from the frozen capture model")
        model_kwargs = dict(kwargs.get("model_kwargs", {}))
        if any(key in model_kwargs for key in ("api_base", "api_key", "extra_headers", "stream", "fallbacks", "mock_response")):
            raise RuntimeError("worker configuration may not override the physical capture boundary")
        model_kwargs.update(api_base=self._capture_endpoint + "/v1", api_key=self._capture_capability,
                            stream=False, num_retries=0)
        kwargs.update(model_name="openai/" + self._capture_model, model_kwargs=model_kwargs)
        super().__init__(**kwargs)

    def _capture(self, path: str, value: dict) -> dict:
        request = urllib.request.Request(self._capture_endpoint + path,
            data=json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self._capture_capability}, method="POST")
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read(65536))

    def query(self, messages, **kwargs):
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
            self._capture("/capture/logical/finish", {"logical_call_id": logical_id, "status": "FAILED"})
            raise
        self._capture("/capture/logical/finish", {"logical_call_id": logical_id, "status": "COMPLETED"})
        message.setdefault("extra", {})["capture_logical_id"] = logical_id
        return message

    def serialize(self):
        value = super().serialize()
        # This is Mini's serialization boundary, before it writes a trajectory.
        # The response artifacts remain byte-exact; a transient local transport
        # capability is configuration, not retained provider evidence.
        config = value["info"]["config"]["model"]
        config["model_kwargs"].pop("api_key", None)
        value["info"]["capture_profile"] = MINI_ACCOUNTING_PROFILE
        value["info"]["capture_dependencies"] = {
            name: version(name) for name in ("mini-swe-agent", "litellm", "openai")}
        return value
