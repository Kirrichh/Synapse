"""Committed governing task constraints, independent of planner proposals.

The composition root supplies this contract from frozen operator inputs.
Requirements stay fixed while an attempt resolves its knowledge separately.
The historical v1 wire contract retains its exact required behavior references;
v2 cannot embed a caller-selected knowledge set in the governing task.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from ..canonicalization import HashBoundRef, RefKind
from .context_codec import encode_canonical
from .intent import AcceptanceCriterion, EffectConstraint, IntentCandidate, validate_intent_candidate
from .repository_scope import RepositoryScope, validate_repository_scope


TASK_CONTRACT_SCHEMA_V1 = "synapse.stage4.gold.governing-task/v1"
TASK_CONTRACT_SCHEMA_V2 = "synapse.stage4.gold.governing-task/v2"


@dataclass(frozen=True)
class GoverningTaskContract:
    task_id: str
    task_statement: str
    repository_revision_sha256: str
    allowed_scope: RepositoryScope
    required_capabilities: tuple[str, ...]
    target_bindings: tuple[HashBoundRef, ...]
    effects: tuple[EffectConstraint, ...]
    acceptance: tuple[AcceptanceCriterion, ...]
    behavior_refs: tuple[HashBoundRef, ...] = ()
    schema_version: str = TASK_CONTRACT_SCHEMA_V2

    def __post_init__(self) -> None:
        if type(self.schema_version) is not str or self.schema_version not in {TASK_CONTRACT_SCHEMA_V1, TASK_CONTRACT_SCHEMA_V2}:
            raise ValueError("governing task schema is unknown")
        if type(self.task_id) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", self.task_id) is None:
            raise ValueError("governing task requires an exact task identity")
        if type(self.task_statement) is not str or not 1 <= len(self.task_statement) <= 4096:
            raise ValueError("governing task statement must be bounded")
        if type(self.repository_revision_sha256) is not str or re.fullmatch(r"[0-9a-f]{40,64}", self.repository_revision_sha256) is None:
            raise ValueError("governing task requires a frozen Git revision")
        validate_repository_scope(self.allowed_scope)
        for name, values, expected in (
            ("capabilities", self.required_capabilities, str),
            ("targets", self.target_bindings, HashBoundRef),
            ("effects", self.effects, EffectConstraint),
            ("acceptance", self.acceptance, AcceptanceCriterion),
        ):
            if type(values) is not tuple or not values or any(type(item) is not expected for item in values):
                raise ValueError(f"governing task {name} must be exact and non-empty")
            if len(set(values)) != len(values):
                raise ValueError(f"governing task {name} contains duplicates")
        if any(item.kind is not RefKind.BINDING for item in self.target_bindings):
            raise ValueError("task targets must reference resolved bindings")
        if type(self.behavior_refs) is not tuple or any(type(item) is not HashBoundRef or item.kind is not RefKind.ARTIFACT for item in self.behavior_refs):
            raise ValueError("historical task behaviors must reference exact library subjects")
        if self.schema_version == TASK_CONTRACT_SCHEMA_V1:
            if not self.behavior_refs or len(set(self.behavior_refs)) != len(self.behavior_refs):
                raise ValueError("historical governing task behaviors must be non-empty and unique")
        elif self.behavior_refs:
            raise ValueError("governing task v2 cannot preselect knowledge")
        if any(item.subject_path is not None and not self.allowed_scope.covers(item.subject_path) for item in self.effects):
            raise ValueError("task effect is outside its scope")

    def intent_fields(self) -> dict[str, object]:
        fields = {
            "task_statement": self.task_statement,
            "repository_revision_sha256": self.repository_revision_sha256,
            "allowed_scope": self.allowed_scope,
            "required_capabilities": self.required_capabilities,
            "target_bindings": self.target_bindings,
            "effects": self.effects,
            "acceptance": self.acceptance,
        }
        if self.schema_version == TASK_CONTRACT_SCHEMA_V1:
            fields["behavior_refs"] = self.behavior_refs
        return fields

    def to_dict(self) -> dict[str, object]:
        fields = {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "task_statement": self.task_statement,
            "repository_revision_sha256": self.repository_revision_sha256,
            "allowed_scope": self.allowed_scope.to_dict(),
            "required_capabilities": list(self.required_capabilities),
            "target_bindings": [item.to_dict() for item in self.target_bindings],
            "effects": [item.to_dict() for item in self.effects],
            "acceptance": [item.to_dict() for item in self.acceptance],
        }
        if self.schema_version == TASK_CONTRACT_SCHEMA_V1:
            fields["behavior_refs"] = [item.to_dict() for item in self.behavior_refs]
        return fields

    def canonical_bytes(self) -> bytes:
        return encode_canonical(self.to_dict())

    @property
    def reference(self) -> HashBoundRef:
        raw = self.canonical_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        return HashBoundRef(RefKind.CONTRACT_CONDITION, digest, self.schema_version,
                            digest, len(raw), "application/json")

    @classmethod
    def from_dict(cls, value: object) -> GoverningTaskContract:
        fields = {"schema_version", "task_id", "task_statement", "repository_revision_sha256",
                  "allowed_scope", "required_capabilities", "target_bindings",
                  "effects", "acceptance"}
        if (type(value) is not dict or type(value.get("schema_version")) is not str
                or value["schema_version"] not in {TASK_CONTRACT_SCHEMA_V1, TASK_CONTRACT_SCHEMA_V2}):
            raise ValueError("governing task contract has an unknown shape or schema")
        historical = value["schema_version"] == TASK_CONTRACT_SCHEMA_V1
        if historical:
            fields.add("behavior_refs")
        if set(value) != fields:
            raise ValueError("governing task contract has an unknown shape or schema")
        arrays = ("required_capabilities", "target_bindings", "effects", "acceptance")
        for name in arrays + (("behavior_refs",) if historical else ()):
            if type(value[name]) is not list:
                raise ValueError(f"governing task {name} must be a list")
        return cls(
            task_id=value["task_id"], task_statement=value["task_statement"],
            repository_revision_sha256=value["repository_revision_sha256"],
            allowed_scope=RepositoryScope.from_dict(value["allowed_scope"]),
            required_capabilities=tuple(value["required_capabilities"]),
            target_bindings=tuple(HashBoundRef.from_dict(item) for item in value["target_bindings"]),
            behavior_refs=tuple(HashBoundRef.from_dict(item) for item in value["behavior_refs"]) if historical else (),
            effects=tuple(EffectConstraint.from_dict(item) for item in value["effects"]),
            acceptance=tuple(AcceptanceCriterion.from_dict(item) for item in value["acceptance"]),
            schema_version=value["schema_version"],
        )

    def validate_intent(self, intent: IntentCandidate) -> None:
        validate_intent_candidate(intent)
        if intent.task_contract_ref != self.reference:
            raise ValueError("intent names a different governing task contract")
        for name, expected in self.intent_fields().items():
            if getattr(intent, name) != expected:
                raise ValueError(f"intent changes governing task {name}")
