"""LIN-02/03: immutable typed evidence DAGs, identity and structural obligations.

The uploaded Stage 14 draft supplies the iterative cycle-checking approach.
Completeness uses occurrence profiles instead of one universal success chain.
Physical completeness belongs to the source readers, not to this graph model.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import re

from ..canonicalization import (
    HashBoundRef, RefKind, canonicalize_stage4_payload,
    STAGE4_CANONICAL_PROFILE_V1, STABLE_CANONICAL_CODEC_ID,
)
from ..contracts import LineageEdgeKind

LINEAGE_SCHEMA_V1 = "synapse.stage4.gold.lineage/v1"
MAX_NODES = 100_000
MAX_EDGES = 400_000
_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")


class LineageNodeClass(str, Enum):
    RUN = "RUN"
    ATTEMPT = "ATTEMPT"
    KNOWLEDGE_BASIS = "KNOWLEDGE_BASIS"
    KNOWLEDGE_SNAPSHOT = "KNOWLEDGE_SNAPSHOT"
    SNAPSHOT_BOUNDARY = "SNAPSHOT_BOUNDARY"
    BEHAVIOR_BLOB = "BEHAVIOR_BLOB"
    BEHAVIOR_MANIFEST = "BEHAVIOR_MANIFEST"
    BINDING = "BINDING"
    ATTESTATION = "ATTESTATION"
    ADMISSION_DECISION = "ADMISSION_DECISION"
    CONSUMER_CONTEXT = "CONSUMER_CONTEXT"
    COMPATIBILITY_EVIDENCE = "COMPATIBILITY_EVIDENCE"
    RETRIEVAL_DECISION = "RETRIEVAL_DECISION"
    REPLAY_REQUEST = "REPLAY_REQUEST"
    REPLAY_RESULT = "REPLAY_RESULT"
    REPLAY_MANIFEST = "REPLAY_MANIFEST"
    REFERENCE_CAPTURE = "REFERENCE_CAPTURE"
    STRUCTURAL_HISTORY = "STRUCTURAL_HISTORY"
    FROZEN_CANDIDATES = "FROZEN_CANDIDATES"
    VM_SNAPSHOT = "VM_SNAPSHOT"
    TASK_CONTRACT = "TASK_CONTRACT"
    INTENT = "INTENT"
    PLAN_PROPOSAL = "PLAN_PROPOSAL"
    PLAN_DECISION = "PLAN_DECISION"
    PLAN = "PLAN"
    WORKER_CONTEXT = "WORKER_CONTEXT"
    WORKER_RESULT = "WORKER_RESULT"
    DELIVERY_RECEIPT = "DELIVERY_RECEIPT"
    PHASE_RECORD = "PHASE_RECORD"
    CONTROLLED_CHANGE_RESULT = "CONTROLLED_CHANGE_RESULT"
    GOLD_EVIDENCE = "GOLD_EVIDENCE"
    EVIDENCE_GAP = "EVIDENCE_GAP"
    COMMIT = "COMMIT"
    ORACLE_RESULT = "ORACLE_RESULT"
    VERIFICATION = "VERIFICATION"
    STRUCTURED_OUTCOME = "STRUCTURED_OUTCOME"
    PUBLICATION_REQUEST = "PUBLICATION_REQUEST"
    PUBLICATION_DECISION = "PUBLICATION_DECISION"
    PUBLICATION_RESULT = "PUBLICATION_RESULT"
    MECHANISM_USE = "MECHANISM_USE"
    REUSE_PROMOTION = "REUSE_PROMOTION"
    LIFECYCLE_RECORD = "LIFECYCLE_RECORD"
    TELEMETRY_RECORD = "TELEMETRY_RECORD"
    GOLD_EVENT = "GOLD_EVENT"
    LINEAGE = "LINEAGE"
    RUN_DECISION = "RUN_DECISION"
    ATTEMPT_RESULT = "ATTEMPT_RESULT"
    RUN_RESULT = "RUN_RESULT"


class LineageFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    ORPHAN_EDGE = "ORPHAN_EDGE"
    TYPE_CONSTRAINT = "TYPE_CONSTRAINT"
    CYCLE = "CYCLE"
    MISSING_MANDATORY_EDGE = "MISSING_MANDATORY_EDGE"
    MISSING_RECORD = "MISSING_RECORD"
    PHYSICAL_MISMATCH = "PHYSICAL_MISMATCH"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"


class LineageViolation(ValueError):
    def __init__(self, code: LineageFailureCode, detail: str):
        self.failure_code = code
        super().__init__(f"{code.value}: {detail[:256]}")


def canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(value, profile_id=STAGE4_CANONICAL_PROFILE_V1,
                                       codec_id=STABLE_CANONICAL_CODEC_ID)


def record_reference(value: object, schema: str) -> HashBoundRef:
    raw = canonical(value)
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(RefKind.ARTIFACT, digest, schema,
                        digest, len(raw), "application/json")


def _identity(domain: str, value: object) -> str:
    return hashlib.sha256(domain.encode() + b"\0" + canonical(value)).hexdigest()


def _text(value: object) -> str:
    if type(value) is not str or _TEXT.fullmatch(value) is None:
        raise LineageViolation(LineageFailureCode.TYPE_MISMATCH, "invalid lineage identifier")
    return value


def _shape(value: object, fields: set[str]) -> dict:
    if type(value) is not dict or set(value) != fields:
        raise LineageViolation(LineageFailureCode.TYPE_MISMATCH, "unknown lineage record shape")
    return value


@dataclass(frozen=True)
class LineageNode:
    node_class: LineageNodeClass
    reference: HashBoundRef
    run_id: str
    attempt_id: str

    def payload(self) -> dict:
        if type(self.node_class) is not LineageNodeClass or type(self.reference) is not HashBoundRef:
            raise LineageViolation(LineageFailureCode.TYPE_MISMATCH, "node requires exact types")
        return {"node_class": self.node_class.value, "reference": self.reference.to_dict(),
                "run_id": _text(self.run_id), "attempt_id": _text(self.attempt_id)}

    @property
    def node_id(self) -> str:
        return _identity("synapse.lineage.node/v1", self.payload())

    def to_dict(self) -> dict:
        return {**self.payload(), "node_id": self.node_id}

    @classmethod
    def from_dict(cls, value: object) -> LineageNode:
        v = _shape(value, {"node_class", "reference", "run_id", "attempt_id", "node_id"})
        node = cls(LineageNodeClass(v["node_class"]), HashBoundRef.from_dict(v["reference"]),
                   v["run_id"], v["attempt_id"])
        if node.node_id != v["node_id"]:
            raise LineageViolation(LineageFailureCode.IDENTITY_MISMATCH, "node content changed")
        return node


@dataclass(frozen=True)
class LineageEdge:
    kind: LineageEdgeKind
    source: str
    target: str

    def to_dict(self) -> dict:
        if type(self.kind) is not LineageEdgeKind:
            raise LineageViolation(LineageFailureCode.TYPE_MISMATCH, "unknown edge kind")
        return {"kind": self.kind.value, "source": _text(self.source), "target": _text(self.target)}

    @classmethod
    def from_dict(cls, value: object) -> LineageEdge:
        v = _shape(value, {"kind", "source", "target"})
        return cls(LineageEdgeKind(v["kind"]), v["source"], v["target"])


# Exact role contracts express the mandatory paths. A missing role is not
# synthesized by traversal. Each optional reached role brings its own edge.
_ROLE_CLASSES = {
    "run": "RUN", "context": "ATTEMPT", "basis": "KNOWLEDGE_BASIS",
    "snapshot": "KNOWLEDGE_SNAPSHOT", "boundary": "SNAPSHOT_BOUNDARY",
    "consumer": "CONSUMER_CONTEXT", "retrieval": "RETRIEVAL_DECISION",
    "retrieval_gate": "ADMISSION_DECISION", "replay_request": "REPLAY_REQUEST",
    "replay_result": "REPLAY_RESULT", "task": "TASK_CONTRACT", "intent": "INTENT",
    "plan_proposal": "PLAN_PROPOSAL", "plan_decision": "PLAN_DECISION", "plan": "PLAN",
    "worker_context": "WORKER_CONTEXT", "worker_result": "WORKER_RESULT",
    "worker_audit": "WORKER_CONTEXT", "input.snapshot": "KNOWLEDGE_SNAPSHOT",
    "input.retrieval": "RETRIEVAL_DECISION", "input.replay_result": "REPLAY_RESULT",
    "receipt": "DELIVERY_RECEIPT", "c1": "CONTROLLED_CHANGE_RESULT",
    "gaps": "EVIDENCE_GAP", "evidence": "GOLD_EVIDENCE", "commit": "COMMIT", "oracle": "ORACLE_RESULT",
    "verification": "VERIFICATION", "outcome": "STRUCTURED_OUTCOME",
    "verified_outcome": "STRUCTURED_OUTCOME", "request": "PUBLICATION_REQUEST", "publication_decision": "PUBLICATION_DECISION",
    "publication": "PUBLICATION_RESULT", "behavior": "BEHAVIOR_BLOB",
    "manifest": "BEHAVIOR_MANIFEST", "attestation": "ATTESTATION",
    "ingestion_gate": "ADMISSION_DECISION", "publication_gate": "ADMISSION_DECISION",
    "inputs": "LINEAGE", "mechanism_use": "MECHANISM_USE", "promotion": "REUSE_PROMOTION",
    "result": "ATTEMPT_RESULT", "run_result": "RUN_RESULT", "decision": "RUN_DECISION",
}
_LINKS = (
    ("boundary", "BOUND_TO", "snapshot"), ("consumer", "BOUND_TO", "retrieval_gate"),
    ("snapshot", "SELECTED_BY", "retrieval"), ("retrieval_gate", "ADMITTED_BY", "retrieval"),
    ("snapshot", "REPLAYED_AS", "replay_request"), ("retrieval", "CONSUMED_BY", "replay_request"),
    ("replay_request", "PRODUCED", "replay_result"),
    ("run", "BOUND_TO", "context"), ("basis", "BOUND_TO", "context"),
    ("inputs", "CONSUMED_BY", "context"), ("task", "BOUND_TO", "intent"),
    ("input.snapshot", "BOUND_TO", "intent"), ("plan", "VERIFIED_BY", "verification"),
    ("input.retrieval", "CONSUMED_BY", "worker_audit"),
    ("input.replay_result", "CONSUMED_BY", "worker_audit"),
    ("worker_audit", "MATERIALIZED_AS", "worker_context"),
    ("intent", "DERIVED_FROM", "plan_proposal"), ("plan_proposal", "ADMITTED_BY", "plan_decision"),
    ("plan_decision", "ADMITTED_BY", "plan"), ("plan", "MATERIALIZED_AS", "worker_context"),
    ("worker_context", "CONSUMED_BY", "worker_result"), ("worker_context", "OBSERVED_AS", "receipt"),
    ("worker_result", "PRODUCED", "c1"), ("c1", "VERIFIED_BY", "verification"),
    ("evidence", "VERIFIED_BY", "verification"), ("gaps", "OBSERVED_AS", "verification"), ("commit", "VERIFIED_BY", "verification"),
    ("oracle", "VERIFIED_BY", "verification"), ("context", "VERIFIED_BY", "verification"),
    ("verification", "PRODUCED", "outcome"), ("outcome", "PRODUCED", "result"),
    ("verification", "VERIFIED_BY", "publication_decision"),
    ("verification", "PRODUCED", "verified_outcome"),
    ("verified_outcome", "VERIFIED_BY", "publication_decision"),
    ("request", "ADMITTED_BY", "publication_decision"),
    ("publication_decision", "PUBLISHED_AS", "behavior"), ("behavior", "MATERIALIZED_AS", "manifest"),
    ("attestation", "BOUND_TO", "manifest"), ("ingestion_gate", "ADMITTED_BY", "manifest"),
    ("publication_gate", "ADMITTED_BY", "manifest"),
    ("publication", "DERIVED_FROM", "outcome"), ("manifest", "PUBLISHED_AS", "publication"), ("worker_result", "CONSUMED_BY", "mechanism_use"),
    ("mechanism_use", "VERIFIED_BY", "promotion"), ("promotion", "DERIVED_FROM", "outcome"),
    ("run", "BOUND_TO", "decision"), ("decision", "PRODUCED", "run_result"),
)
_ALLOWED = frozenset((LineageNodeClass(_ROLE_CLASSES[a]), LineageEdgeKind(k),
                      LineageNodeClass(_ROLE_CLASSES[b])) for a, k, b in _LINKS)
_REQUIRED = {
    "inputs/v1": ("boundary", "snapshot", "consumer", "retrieval_gate", "retrieval", "replay_request", "replay_result"),
    "publication/v1": ("verification", "verified_outcome", "request", "publication_decision", "behavior", "manifest", "attestation", "ingestion_gate", "publication_gate"),
    "execution/v1": ("run", "context", "basis", "inputs", "verification"),
    "execution-incomplete/v1": ("run", "context", "basis", "inputs", "verification", "gaps"),
    "attempt-incomplete/v1": ("run", "context", "basis", "inputs", "verification", "gaps", "outcome", "result"),
    "attempt/v1": ("run", "context", "basis", "inputs", "verification", "outcome", "result"),
    "run/v1": ("run", "decision", "run_result"),
}


def relation_is_allowed(source: LineageNodeClass, kind: LineageEdgeKind, target: LineageNodeClass) -> bool:
    if (source, kind, target) in _ALLOWED:
        return True
    # Embedded source proof has one typed direction; it never substitutes a
    # named mandatory relation. Physical readers verify the source field.
    if kind is LineageEdgeKind.DERIVED_FROM:
        sources = {
            LineageNodeClass.KNOWLEDGE_SNAPSHOT: {LineageNodeClass.BEHAVIOR_BLOB, LineageNodeClass.BEHAVIOR_MANIFEST,
                LineageNodeClass.COMPATIBILITY_EVIDENCE, LineageNodeClass.ATTESTATION, LineageNodeClass.ADMISSION_DECISION},
            LineageNodeClass.REPLAY_REQUEST: {LineageNodeClass.RETRIEVAL_DECISION, LineageNodeClass.FROZEN_CANDIDATES,
                LineageNodeClass.REPLAY_MANIFEST, LineageNodeClass.BINDING},
            LineageNodeClass.REPLAY_MANIFEST: {LineageNodeClass.REFERENCE_CAPTURE, LineageNodeClass.VM_SNAPSHOT,
                LineageNodeClass.STRUCTURAL_HISTORY},
            LineageNodeClass.REPLAY_RESULT: {LineageNodeClass.VM_SNAPSHOT},
            LineageNodeClass.INTENT: {LineageNodeClass.ATTEMPT_RESULT},
            LineageNodeClass.VERIFICATION: {LineageNodeClass.GOLD_EVIDENCE, LineageNodeClass.ORACLE_RESULT,
                LineageNodeClass.TASK_CONTRACT, LineageNodeClass.PHASE_RECORD, LineageNodeClass.REPLAY_RESULT,
                LineageNodeClass.WORKER_RESULT, LineageNodeClass.DELIVERY_RECEIPT, LineageNodeClass.WORKER_CONTEXT,
                LineageNodeClass.BINDING, LineageNodeClass.MECHANISM_USE},
            LineageNodeClass.PUBLICATION_REQUEST: {LineageNodeClass.CONSUMER_CONTEXT},
            LineageNodeClass.BEHAVIOR_MANIFEST: {LineageNodeClass.LIFECYCLE_RECORD},
            LineageNodeClass.LINEAGE: {LineageNodeClass.REPLAY_RESULT, LineageNodeClass.KNOWLEDGE_SNAPSHOT,
                LineageNodeClass.LINEAGE},
            LineageNodeClass.MECHANISM_USE: {LineageNodeClass.PUBLICATION_RESULT},
            LineageNodeClass.RUN_RESULT: {LineageNodeClass.ATTEMPT_RESULT, LineageNodeClass.RUN_DECISION},
        }
        return source in sources.get(target, set())
    if kind is LineageEdgeKind.OBSERVED_AS:
        return target in {LineageNodeClass.GOLD_EVENT, LineageNodeClass.TELEMETRY_RECORD,
                          LineageNodeClass.PHASE_RECORD}
    if kind is LineageEdgeKind.MEASURED_BY:
        return target is LineageNodeClass.TELEMETRY_RECORD
    if kind in {LineageEdgeKind.SUPERSEDES, LineageEdgeKind.INVALIDATES}:
        return target is LineageNodeClass.LIFECYCLE_RECORD
    return False


def _check_cycles(nodes: dict, edges: tuple[LineageEdge, ...]) -> None:
    successors = {key: [] for key in nodes}
    for edge in edges:
        successors[edge.source].append(edge.target)
    colors = dict.fromkeys(nodes, 0)
    for start in sorted(nodes):
        if colors[start]:
            continue
        colors[start] = 1
        stack = [(start, iter(sorted(successors[start])))]
        while stack:
            key, iterator = stack[-1]
            target = next(iterator, None)
            if target is None:
                colors[key] = 2
                stack.pop()
            elif colors[target] == 1:
                raise LineageViolation(LineageFailureCode.CYCLE, "lineage contains a cycle")
            elif colors[target] == 0:
                colors[target] = 1
                stack.append((target, iter(sorted(successors[target]))))


@dataclass(frozen=True)
class LineageGraph:
    profile: str
    run_id: str
    attempt_id: str
    nodes: tuple[LineageNode, ...]
    edges: tuple[LineageEdge, ...]
    roles: tuple[tuple[str, str], ...]

    def validate(self) -> None:
        if self.profile not in _REQUIRED:
            raise LineageViolation(LineageFailureCode.TYPE_MISMATCH, "unknown lineage profile")
        _text(self.run_id)
        _text(self.attempt_id)
        if type(self.nodes) is not tuple or type(self.edges) is not tuple or type(self.roles) is not tuple:
            raise LineageViolation(LineageFailureCode.TYPE_MISMATCH, "graph collections must be immutable")
        if len(self.nodes) > MAX_NODES or len(self.edges) > MAX_EDGES:
            raise LineageViolation(LineageFailureCode.RESOURCE_LIMIT, "graph exceeds its declared bounds")
        if any(type(n) is not LineageNode for n in self.nodes) or any(type(e) is not LineageEdge for e in self.edges):
            raise LineageViolation(LineageFailureCode.TYPE_MISMATCH, "graph members must be exact")
        nodes = {n.node_id: n for n in self.nodes}
        if len(nodes) != len(self.nodes) or len(set(self.edges)) != len(self.edges):
            raise LineageViolation(LineageFailureCode.IDENTITY_MISMATCH, "duplicate graph member")
        for e in self.edges:
            e.to_dict()
            if e.source not in nodes or e.target not in nodes:
                raise LineageViolation(LineageFailureCode.ORPHAN_EDGE, "edge endpoint is missing")
            if not relation_is_allowed(nodes[e.source].node_class, e.kind, nodes[e.target].node_class):
                raise LineageViolation(LineageFailureCode.TYPE_CONSTRAINT, "edge violates its type matrix")
        roles = dict(self.roles)
        if len(roles) != len(self.roles) or any(_text(k) != k or v not in nodes for k, v in self.roles):
            raise LineageViolation(LineageFailureCode.ORPHAN_EDGE, "role does not identify one existing node")
        for name in _REQUIRED[self.profile]:
            if name not in roles:
                raise LineageViolation(LineageFailureCode.MISSING_RECORD, "required lineage role is absent")
        for name, node_id in roles.items():
            if name in _ROLE_CLASSES and nodes[node_id].node_class.value != _ROLE_CLASSES[name]:
                raise LineageViolation(LineageFailureCode.TYPE_CONSTRAINT, "role class differs from contract")
        edge_set = set(self.edges)
        for a, k, b in _LINKS:
            if a in roles and b in roles and LineageEdge(LineageEdgeKind(k), roles[a], roles[b]) not in edge_set:
                raise LineageViolation(LineageFailureCode.MISSING_MANDATORY_EDGE, "required typed dependency is absent")
        _check_cycles(nodes, self.edges)

    def payload(self) -> dict:
        self.validate()
        return {"schema_version": LINEAGE_SCHEMA_V1, "profile": self.profile,
                "run_id": self.run_id, "attempt_id": self.attempt_id,
                "nodes": sorted((n.to_dict() for n in self.nodes), key=lambda n: n["node_id"]),
                "edges": sorted((e.to_dict() for e in self.edges), key=lambda e: (e["source"], e["kind"], e["target"])),
                "roles": dict(sorted(self.roles))}

    @property
    def graph_identity(self) -> str:
        return _identity("synapse.lineage.graph/v1", self.payload())

    def to_dict(self) -> dict:
        return {**self.payload(), "graph_identity": self.graph_identity}

    @classmethod
    def from_dict(cls, value: object) -> LineageGraph:
        v = _shape(value, {"schema_version", "profile", "run_id", "attempt_id", "nodes", "edges", "roles", "graph_identity"})
        if v["schema_version"] != LINEAGE_SCHEMA_V1 or type(v["nodes"]) is not list or type(v["edges"]) is not list or type(v["roles"]) is not dict:
            raise LineageViolation(LineageFailureCode.TYPE_MISMATCH, "invalid graph transport")
        if len(v["nodes"]) > MAX_NODES or len(v["edges"]) > MAX_EDGES:
            raise LineageViolation(LineageFailureCode.RESOURCE_LIMIT, "graph transport exceeds bounds")
        graph = cls(v["profile"], v["run_id"], v["attempt_id"], tuple(LineageNode.from_dict(n) for n in v["nodes"]),
                    tuple(LineageEdge.from_dict(e) for e in v["edges"]), tuple(v["roles"].items()))
        if graph.graph_identity != v["graph_identity"]:
            raise LineageViolation(LineageFailureCode.IDENTITY_MISMATCH, "graph identity differs from content")
        return graph

    def ancestors(self, node_id: str) -> tuple[LineageNode, ...]:
        self.validate()
        nodes = {n.node_id: n for n in self.nodes}
        if node_id not in nodes:
            raise LineageViolation(LineageFailureCode.MISSING_RECORD, "query root is absent")
        parents = {key: [] for key in nodes}
        for e in self.edges:
            parents[e.target].append(e.source)
        seen, pending = set(), [node_id]
        while pending:
            key = pending.pop()
            if key not in seen:
                seen.add(key)
                pending.extend(parents[key])
        return tuple(nodes[key] for key in sorted(seen))


class GraphBuilder:
    """Collect explicit source-reader facts; no authority is created here."""

    def __init__(self, profile: str, run_id: str, attempt_id: str):
        self.profile, self.run_id, self.attempt_id = profile, run_id, attempt_id
        self.nodes, self.roles, self.edges = {}, {}, set()

    def add(self, role: str, node_class: LineageNodeClass, ref: HashBoundRef) -> str:
        node = LineageNode(node_class, ref, self.run_id, "run" if node_class is LineageNodeClass.RUN else self.attempt_id)
        _text(role)
        if role in self.roles and self.roles[role] != node.node_id:
            raise LineageViolation(LineageFailureCode.IDENTITY_MISMATCH, "role was rebound")
        self.nodes[node.node_id] = node
        self.roles[role] = node.node_id
        if len(self.nodes) > MAX_NODES:
            raise LineageViolation(LineageFailureCode.RESOURCE_LIMIT, "node budget exhausted")
        return node.node_id

    def record(self, role: str, node_class: LineageNodeClass, value: object, schema: str) -> str:
        return self.add(role, node_class, record_reference(value, schema))

    def link(self, source: str, kind: LineageEdgeKind, target: str) -> None:
        self.edges.add(LineageEdge(kind, self.roles[source], self.roles[target]))
        if len(self.edges) > MAX_EDGES:
            raise LineageViolation(LineageFailureCode.RESOURCE_LIMIT, "edge budget exhausted")

    def merge(self, prefix: str, graph: LineageGraph) -> None:
        graph.validate()
        for n in graph.nodes:
            self.nodes[n.node_id] = n
        self.edges.update(graph.edges)
        for role, node_id in graph.roles:
            self.roles[f"{prefix}.{role}" if prefix else role] = node_id

    def link_roles(self) -> None:
        """Materialize the closed profile relationships for reached roles."""
        for source, kind, target in _LINKS:
            if source in self.roles and target in self.roles:
                self.link(source, LineageEdgeKind(kind), target)

    def finish(self) -> LineageGraph:
        graph = LineageGraph(self.profile, self.run_id, self.attempt_id,
                             tuple(self.nodes.values()), tuple(self.edges), tuple(self.roles.items()))
        graph.validate()
        return graph
