"""Record formats and identities of the memory subsystem (spec part 3 §2.3).

Every record is addressed by its content: the identity is Gold's
``compute_record_id`` over the canonical form, in the record kind's own domain,
so a record cannot be read under another kind and any substitution is found by
recomputing the identity on read. A record carries its identity as ``id`` with
the kind's short prefix; the identity never covers itself.

Frozen parts of triggers and learned habits never change (И2): a narrowed or
widened trigger, or a habit with another body or binding, is a new record. A
learned behavior's Gold admission is bound to the frozen habit, never inside it:
the admitted behavior is derived from the frozen identity, so a reference back
from the frozen part would be circular.

A kind's shape is versioned: a record keeps the schema version it was made
with, and every version a kind ever had stays verifiable. A trigger of schema
1.2 also carries the explanation of its applicability (refinement §12).
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping

from synapse.experiments.gold.canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    canonicalize_stage4_payload,
)
from synapse.experiments.gold.contracts import IdentityDomain, compute_record_id

RECORD_SCHEMA_V1 = "1.1"
RECORD_SCHEMA_V2 = "1.2"


def canonical(value: Any) -> bytes:
    """The existing Synapse canonical serialization (Stage 4 profile), shared with Gold."""
    return canonicalize_stage4_payload(value, profile_id=STAGE4_CANONICAL_PROFILE_V1,
                                       codec_id=STABLE_CANONICAL_CODEC_ID)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class RecordIntegrityError(ValueError):
    """A memory record differs from its content identity or declared shape."""


# kind -> (identity domain, id prefix, exact field set without "id")
KINDS: dict[str, tuple[IdentityDomain, str, frozenset[str]]] = {
    "task_marker": (IdentityDomain.MEMORY_TASK_MARKER, "mrk_", frozenset({
        "schema_version", "kind", "task_id", "task_contract_ref", "segment_ref", "element_part", "intent",
        "scope", "external_anchor", "requirement"})),
    "habit_trigger": (IdentityDomain.MEMORY_HABIT_TRIGGER, "trg_", frozenset({
        "schema_version", "kind", "event_types", "context", "when", "not_when", "context_template",
        "born_from", "source_episodes"})),
    "learned_habit": (IdentityDomain.MEMORY_LEARNED_HABIT, "hab_", frozenset({
        "schema_version", "kind", "origin", "layer", "trigger", "action_pattern", "binding",
        "expected_outcome", "born_from", "supersedes"})),
    "marker_verdict": (IdentityDomain.MEMORY_MARKER_VERDICT, "mvd_", frozenset({
        "schema_version", "kind", "marker_id", "run_id", "op_scopes", "verdict", "stage", "flags",
        "evidence", "similarity", "advice", "criterion", "replay"})),
    "candidate": (IdentityDomain.MEMORY_CANDIDATE, "cnd_", frozenset({
        "schema_version", "kind", "candidate_key", "context", "action_pattern", "binding", "expected_outcome",
        "request"})),
    "case_quantum": (IdentityDomain.MEMORY_CASE_QUANTUM, "sha256:", frozenset({
        "schema_version", "kind", "canonical"})),
    "element_part": (IdentityDomain.MEMORY_ELEMENT_PART, "sha256:", frozenset({
        "schema_version", "kind", "element_id", "part_id", "root"})),
    "element_root": (IdentityDomain.MEMORY_ELEMENT_ROOT, "sha256:", frozenset({
        "schema_version", "kind", "element_id", "root"})),
    "consolidation_report": (IdentityDomain.MEMORY_CONSOLIDATION_REPORT, "rpt_", frozenset({
        "schema_version", "kind", "report"})),
    "session_digest": (IdentityDomain.MEMORY_SESSION_DIGEST, "dig_", frozenset({
        "schema_version", "kind", "digest"})),
    "consolidation_inputs": (IdentityDomain.MEMORY_CONSOLIDATION_APPLIED, "cons_", frozenset({
        "schema_version", "kind", "inputs"})),
    "snapshot_boundary": (IdentityDomain.MEMORY_SNAPSHOT_BOUNDARY, "bnd_", frozenset({
        "schema_version", "kind", "boundary"})),
    "hypothesis": (IdentityDomain.MEMORY_HYPOTHESIS, "hyp_", frozenset({
        "schema_version", "kind", "aspect", "subject", "statement", "scope", "source", "check"})),
}

_TRIGGER_V1 = KINDS["habit_trigger"][2]
#: Kinds whose shape changed: schema version -> exact field set; the last entry is current.
VERSIONS: dict[str, dict[str, frozenset[str]]] = {
    "habit_trigger": {RECORD_SCHEMA_V1: _TRIGGER_V1, RECORD_SCHEMA_V2: _TRIGGER_V1 | {"applicability"}},
}


def _fields(kind: str, version: Any) -> frozenset[str] | None:
    """The exact field set of one kind at one schema version, if that version exists."""
    return VERSIONS.get(kind, {RECORD_SCHEMA_V1: KINDS[kind][2]}).get(version)


def current_version(kind: str) -> str:
    """The schema version new records of a kind are made with."""
    return list(VERSIONS.get(kind, {RECORD_SCHEMA_V1: None}))[-1]


def identity(kind: str, body: Mapping[str, Any]) -> str:
    """Content identity of one record body (without its ``id``)."""
    try:
        domain, prefix, _ = KINDS[kind]
    except KeyError as exc:
        raise RecordIntegrityError(f"unknown memory record kind {kind!r}") from exc
    fields = _fields(kind, body.get("schema_version"))
    if fields is None or set(body) != fields or body.get("kind") != kind:
        raise RecordIntegrityError(f"{kind} record has an unknown shape")
    return prefix + compute_record_id(domain=domain, canonical_bytes=canonical(dict(body))).digest_sha256


def make(kind: str, **fields: Any) -> dict[str, Any]:
    """A new record with its identity."""
    body = {"schema_version": current_version(kind), "kind": kind, **fields}
    return {**body, "id": identity(kind, body)}


def verify(record: Any, kind: str) -> dict[str, Any]:
    """The record, only if its identity is its content's identity."""
    if type(record) is not dict or "id" not in record:
        raise RecordIntegrityError(f"{kind} record lost its identity")
    body = {key: value for key, value in record.items() if key != "id"}
    if identity(kind, body) != record["id"]:
        raise RecordIntegrityError(f"{kind} record differs from its content identity")
    return record


def body_of(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "id"}
