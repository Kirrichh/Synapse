"""Strict JSON wire encoding and retention of neutral execution records."""

import base64
from dataclasses import fields, is_dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from collections.abc import Mapping


def json_value(value):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {f.name: json_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value):
            raise TypeError("JSON keys must be strings")
        return {k: json_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if type(value) is bytes:
        return {"base64url": base64.urlsafe_b64encode(value).decode("ascii")}
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise TypeError("unsupported neutral JSON value")


def canonical_bytes(value) -> bytes:
    return json.dumps(json_value(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def decode_json(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def nonfinite(value):
        raise ValueError("non-finite JSON number")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=nonfinite)


def result_from_dict(value):
    from .contracts import (AgentExecutionResult, AgentExecutionStatus, AgentOutputEnvelope,
        AgentUsage, AgentTokenStatus, AgentReport, AgentDeliveryEvidence)
    data = dict(value)
    data["status"] = AgentExecutionStatus(data["status"])
    outputs = []
    for item in data["outputs"]:
        output = dict(item)
        encoded = output["payload"]
        if type(encoded) is not dict or set(encoded) != {"base64url"}:
            raise ValueError("unknown retained output encoding")
        output["payload"] = base64.b64decode(encoded["base64url"], altchars=b"-_", validate=True)
        outputs.append(AgentOutputEnvelope(**output))
    data["outputs"] = tuple(outputs)
    usage = dict(data["usage"])
    usage["token_status"] = AgentTokenStatus(usage["token_status"])
    data["usage"] = AgentUsage(**usage)
    data["report"] = AgentReport(**data["report"])
    data["delivery_evidence"] = AgentDeliveryEvidence(**data["delivery_evidence"])
    data["evidence_refs"] = tuple(data.get("evidence_refs", ()))
    return AgentExecutionResult(**data)
