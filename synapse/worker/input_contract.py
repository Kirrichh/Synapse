"""Neutral, immutable task and local-information inputs for a coding worker.

These records carry data, never execution authority. Local information has no
implicit conversion to a task or a provider message. Consumers must select a
supported local interpretation; receiving bytes does not establish their use.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json

from synapse.canonical_values import canonical_json_bytes


WORKER_TASK_INPUT_V1 = "synapse.worker.task-input/v1"
LOCAL_INFORMATION_INPUT_V1 = "synapse.worker.local-information-input/v1"
SPLIT_INPUT_PROFILE_V1 = "mini-2.4.6-task-and-local-information/v1"
MAX_WORKER_INPUT_BYTES = 16 * 1024 * 1024


class WorkerInputViolation(ValueError):
    """An input or a requested information flow is outside its declared profile."""


def _decode(raw: bytes, fields: set[str], schema: str) -> dict:
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_WORKER_INPUT_BYTES:
        raise WorkerInputViolation("worker input must be bounded exact bytes")
    try:
        value = json.loads(raw.decode("utf-8"))
        if (type(value) is not dict or set(value) != fields
                or value["schema_version"] != schema or canonical_json_bytes(value) != raw):
            raise WorkerInputViolation("worker input has an unknown or noncanonical shape")
    except (UnicodeError, TypeError, ValueError, RecursionError) as exc:
        raise WorkerInputViolation("worker input has an unknown or noncanonical shape") from exc
    return value


def _strings(value, *, nonempty=True):
    if (type(value) is not list or (nonempty and not value)
            or any(type(item) is not str or not item for item in value)
            or value != sorted(set(value))):
        raise WorkerInputViolation("worker input requires sorted unique strings")


@dataclass(frozen=True)
class WorkerTaskInput:
    canonical_bytes: bytes

    def __post_init__(self):
        value = self.to_dict()
        for key in ("statement", "repository_revision"):
            if type(value[key]) is not str or not value[key]:
                raise WorkerInputViolation("task requires its statement and repository revision")
        _strings(value["allowed_scope"])
        _strings(value["capabilities"])
        for field, fields in (("effects", {"kind", "disposition", "subject_path"}),
                              ("acceptance", {"kind", "argv"})):
            if type(value[field]) is not list or not value[field]:
                raise WorkerInputViolation("task requires effect and acceptance constraints")
            for item in value[field]:
                if type(item) is not dict or set(item) != fields:
                    raise WorkerInputViolation("task constraint has an unknown shape")
                for key, text in item.items():
                    if key == "argv":
                        if type(text) is not list or any(type(token) is not str or not token for token in text):
                            raise WorkerInputViolation("acceptance command requires exact argument tokens")
                        continue
                    if key == "subject_path" and text is None:
                        continue
                    if type(text) is not str or not text:
                        raise WorkerInputViolation("task constraint requires exact text")

    def to_dict(self) -> dict:
        return _decode(self.canonical_bytes, {"schema_version", "statement", "repository_revision",
            "allowed_scope", "capabilities", "effects", "acceptance"}, WORKER_TASK_INPUT_V1)

    @property
    def text(self) -> str:
        return self.canonical_bytes.decode("utf-8")


@dataclass(frozen=True)
class LocalInformationInput:
    canonical_bytes: bytes

    def __post_init__(self):
        value = self.to_dict()
        if type(value["items"]) is not list:
            raise WorkerInputViolation("local information items must be a list")
        for item in value["items"]:
            if (type(item) is not dict
                    or set(item) != {"role", "media_type", "content_base64url"}
                    or type(item["role"]) is not str
                    or item["role"] not in {"REFERENCE", "REJECTED_HYPOTHESIS", "REPLAY_OBSERVATION", "EXECUTION_OBSERVATION"}
                    or type(item["media_type"]) is not str or not item["media_type"]
                    or type(item["content_base64url"]) is not str or not item["content_base64url"]):
                raise WorkerInputViolation("local information item has an unknown shape")
            try:
                encoded = item["content_base64url"].encode("ascii")
                raw = base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
                if base64.urlsafe_b64encode(raw).rstrip(b"=") != encoded:
                    raise ValueError("noncanonical base64url")
            except (UnicodeError, ValueError) as exc:
                raise WorkerInputViolation("local information content is not exact base64url") from exc

    def to_dict(self) -> dict:
        return _decode(self.canonical_bytes, {"schema_version", "items"}, LOCAL_INFORMATION_INPUT_V1)

    @property
    def text(self) -> str:
        return self.canonical_bytes.decode("utf-8")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()
