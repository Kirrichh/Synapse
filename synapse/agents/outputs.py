"""Registered domain output codecs. Shape validation never mints a verdict."""

from dataclasses import dataclass
import hashlib
from typing import Protocol

from .codec import canonical_bytes, decode_json
from .contracts import AgentOutputEnvelope


PATCH_CANDIDATE_OUTPUT_V1 = "synapse.agent.output.patch-candidate/v1"
DOCUMENT_OBSERVATIONS_V1 = "synapse.agent.output.document-observations/v1"


@dataclass(frozen=True)
class PatchCandidate:
    status: str
    diff_text: str | None
    touched_files: tuple[str, ...]
    diagnostics_json: bytes
    summary: str | None
    failure_reason: str | None


@dataclass(frozen=True)
class DocumentObservation:
    kind: str
    field: str
    value_json: bytes
    page: int | None


@dataclass(frozen=True)
class DocumentSourceObservations:
    artifact_id: str
    source_sha256: str
    status: str
    observations: tuple[DocumentObservation, ...]
    failure_code: str | None


@dataclass(frozen=True)
class DocumentObservationSet:
    sources: tuple[DocumentSourceObservations, ...]


class OutputCodec(Protocol):
    schema_id: str
    def decode(self, output: AgentOutputEnvelope) -> object: ...


def _object(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required, "additionalProperties": False}


_TEXT = {"type": "string"}
_NULL_TEXT = {"type": ["string", "null"]}
_REPORT = _object({"summary": _NULL_TEXT, "failure_reason": _NULL_TEXT})
_PATCH_SCHEMA = _object({
    "status": {"enum": ["PROPOSED_PATCH", "NO_PATCH", "ERROR", "TIMEOUT"]},
    "diff_text": _NULL_TEXT,
    "touched_files": {"type": "array", "items": _TEXT, "uniqueItems": True},
    "diagnostics": {"type": "object"}, "report": _REPORT,
})
_OBSERVATION = _object({
    "kind": {"enum": ["EXTRACTED_FIELD", "LAYOUT_ELEMENT", "STRUCTURAL_OBSERVATION", "SIGNATURE_OBSERVATION"]},
    "field": {"type": "string", "minLength": 1}, "value": {},
    "page": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
})
_DOCUMENT_SCHEMA = _object({"sources": {"type": "array", "minItems": 1, "items": _object({
    "artifact_id": {"type": "string", "minLength": 1},
    "source_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "status": {"enum": ["PARSED", "ERROR", "REFUSED"]},
    "observations": {"type": "array", "items": _OBSERVATION},
    "failure_code": _NULL_TEXT,
})}})


def validate_json_schema(value, schema):
    from jsonschema import Draft202012Validator
    from referencing import Registry
    from referencing.exceptions import NoSuchResource
    def refuse(uri):
        raise NoSuchResource(ref=uri)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, registry=Registry(retrieve=refuse)).validate(value)


class PatchCandidateCodec:
    schema_id = PATCH_CANDIDATE_OUTPUT_V1

    def decode(self, output):
        output.__post_init__()
        value = decode_json(output.payload)
        validate_json_schema(value, _PATCH_SCHEMA)
        patch = value["diff_text"]
        if value["status"] == "PROPOSED_PATCH" and (not patch or not value["touched_files"]):
            raise ValueError("proposed patch requires exact diff bytes and touched paths")
        if value["status"] == "NO_PATCH" and (patch or value["touched_files"]):
            raise ValueError("no-patch output cannot contain a patch")
        return PatchCandidate(value["status"], patch, tuple(value["touched_files"]),
            canonical_bytes(value["diagnostics"]), value["report"]["summary"], value["report"]["failure_reason"])


class DocumentObservationCodec:
    schema_id = DOCUMENT_OBSERVATIONS_V1

    def decode(self, output):
        output.__post_init__()
        value = decode_json(output.payload)
        validate_json_schema(value, _DOCUMENT_SCHEMA)
        sources = []
        identities = set()
        for item in value["sources"]:
            if item["artifact_id"] in identities:
                raise ValueError("document output repeats a source")
            identities.add(item["artifact_id"])
            if (item["status"] == "PARSED") != (item["failure_code"] is None):
                raise ValueError("document source status and failure disagree")
            if item["status"] != "PARSED" and item["observations"]:
                raise ValueError("failed source cannot claim extracted observations")
            sources.append(DocumentSourceObservations(item["artifact_id"], item["source_sha256"],
                item["status"], tuple(DocumentObservation(o["kind"], o["field"],
                    canonical_bytes(o["value"]), o["page"]) for o in item["observations"]), item["failure_code"]))
        return DocumentObservationSet(tuple(sources))


class OutputRegistry:
    def __init__(self, codecs: tuple[OutputCodec, ...] = ()):
        self._codecs = {c.schema_id: c for c in (PatchCandidateCodec(), DocumentObservationCodec())}
        for codec in codecs:
            if codec.schema_id in self._codecs:
                raise ValueError("output profile is already registered")
            self._codecs[codec.schema_id] = codec

    def supports(self, schema_id: str) -> bool:
        return schema_id in self._codecs

    def decode(self, output: AgentOutputEnvelope) -> object:
        if output.output_schema not in self._codecs:
            raise ValueError("output profile has no admitted codec")
        return self._codecs[output.output_schema].decode(output)


def output_envelope(schema_id: str, value) -> AgentOutputEnvelope:
    raw = canonical_bytes(value)
    return AgentOutputEnvelope(schema_id, "application/json", raw, hashlib.sha256(raw).hexdigest(), len(raw))


@dataclass(frozen=True)
class SchemaDefinedOutput:
    schema_id: str
    schema_sha256: str
    canonical_json: bytes


@dataclass(frozen=True)
class SchemaOutputCodec:
    """A new domain profile has an exact schema; it cannot replace builtins."""
    schema_id: str
    schema_bytes: bytes

    def __post_init__(self):
        from jsonschema import Draft202012Validator
        Draft202012Validator.check_schema(decode_json(self.schema_bytes))

    def decode(self, output):
        output.__post_init__()
        value = decode_json(output.payload)
        validate_json_schema(value, decode_json(self.schema_bytes))
        return SchemaDefinedOutput(self.schema_id, hashlib.sha256(self.schema_bytes).hexdigest(), canonical_bytes(value))
