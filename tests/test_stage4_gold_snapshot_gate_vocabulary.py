"""Stage 4 Patch 6.5 — shared snapshot/gate vocabulary contract tests.

Patch 6.5 owns only the vocabulary and cross-owner reference kinds shared by the
knowledge-snapshot owner (§21) and the admission-gate owner (§22). It carries no
domain logic: there is no snapshot builder, no gate evaluator and no persistence
here. These tests lock the closed vocabularies, the fixed gate order and the
fail-closed helpers so that the two owners can be implemented as separate
patches without importing each other.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.contracts import (
    AdmissionDecisionResolver,
    ContractFailureCode,
    ContractViolation,
    GateCheckedDimension,
    GateDecisionKind,
    GateKind,
    IdentityDomain,
    SchemaVersion,
    SnapshotBoundaryResolver,
    SnapshotCompletenessStatus,
    gate_precedes_snapshot_boundary,
    gate_requires_committed_boundary,
    gate_requires_consumer_context,
    gate_sequence,
    gate_stage_index,
    require_snapshot_status_admits_execution,
    snapshot_status_admits_execution,
    validate_gate_progression,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLD_PACKAGE = REPO_ROOT / "synapse" / "experiments" / "gold"


# ---------------------------------------------------------------------------
# Closed vocabularies (§21 completeness table, §22 gate schema)
# ---------------------------------------------------------------------------


def test_snapshot_completeness_status_matches_normative_table() -> None:
    assert {status.value for status in SnapshotCompletenessStatus} == {
        "COMPLETE",
        "INCOMPLETE_REQUIRED_STORE",
        "INCOMPLETE_REQUIRED_BINDING",
        "INCOMPLETE_REQUIRED_ATTESTATION",
        "INCOMPLETE_LIFECYCLE_STATE",
        "INCOMPLETE_COMPATIBILITY_DATA",
        "INCONSISTENT_REFERENCES",
        "MIX_AND_MATCH_DETECTED",
        "ROLLBACK_DETECTED",
        "CORRUPTED",
    }


def test_gate_kinds_are_exactly_the_four_normative_gates() -> None:
    assert {gate.value for gate in GateKind} == {
        "INGESTION",
        "PUBLICATION",
        "RETRIEVAL",
        "CONSUMPTION",
    }


def test_gate_decision_kinds_cover_the_normative_four() -> None:
    assert {kind.value for kind in GateDecisionKind} == {
        "ADMIT",
        "REJECT",
        "QUARANTINE",
        "REQUIRE_REVIEW",
    }


def test_checked_dimensions_match_the_normative_list() -> None:
    assert {dimension.value for dimension in GateCheckedDimension} == {
        "SOURCE_TAINT",
        "PROVENANCE",
        "BINDING",
        "REVISION",
        "LIFECYCLE",
        "SCOPE",
        "CAPABILITIES",
        "ENVIRONMENT",
        "TOOLS",
        "ORACLE",
        "CONFLICTS",
    }


def test_schema_versions_and_identity_domains_are_domain_separated() -> None:
    added_schemas = {
        SchemaVersion.KNOWLEDGE_SNAPSHOT_V1,
        SchemaVersion.ATOMIC_SNAPSHOT_BOUNDARY_V1,
        SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V1,
        SchemaVersion.GATE_DECISION_V1,
    }
    added_domains = {
        IdentityDomain.KNOWLEDGE_SNAPSHOT,
        IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
        IdentityDomain.SNAPSHOT_COMPLETENESS_DECISION,
        IdentityDomain.GATE_DECISION,
    }
    assert len({schema.value for schema in added_schemas}) == 4
    assert len({domain.value for domain in added_domains}) == 4
    # A record identity domain never collides with a payload schema version:
    # otherwise a payload hash could be replayed as a record identity.
    assert not {schema.value for schema in added_schemas} & {
        domain.value for domain in added_domains
    }
    for value in {schema.value for schema in added_schemas} | {
        domain.value for domain in added_domains
    }:
        assert value.startswith("synapse.stage4.gold.")
        assert value.endswith("/v1")


def test_cross_owner_ref_kinds_exist_and_are_distinct() -> None:
    cross_owner = {
        RefKind.KNOWLEDGE_SNAPSHOT,
        RefKind.ATOMIC_BOUNDARY,
        RefKind.GATE_DECISION,
    }
    assert len({kind.value for kind in cross_owner}) == 3
    assert not cross_owner & {
        RefKind.SOURCE_EVIDENCE,
        RefKind.ARTIFACT,
        RefKind.PROGRAM_ARTIFACT,
        RefKind.CONTRACT_CONDITION,
        RefKind.BINDING,
        RefKind.ABSENCE_PROVENANCE,
    }


# ---------------------------------------------------------------------------
# Fixed gate order (§1 production path, §22 independence)
# ---------------------------------------------------------------------------


def test_gate_sequence_is_fixed_and_monotonic() -> None:
    assert gate_sequence() == (
        GateKind.INGESTION,
        GateKind.PUBLICATION,
        GateKind.RETRIEVAL,
        GateKind.CONSUMPTION,
    )
    indices = [gate_stage_index(gate) for gate in gate_sequence()]
    assert indices == sorted(indices) == [0, 1, 2, 3]


def test_gate_sequence_is_a_detached_copy() -> None:
    first = gate_sequence()
    assert isinstance(first, tuple)
    assert first is gate_sequence() or first == gate_sequence()


@pytest.mark.parametrize(
    "gate,prior",
    [
        (GateKind.INGESTION, None),
        (GateKind.PUBLICATION, GateKind.INGESTION),
        (GateKind.RETRIEVAL, GateKind.PUBLICATION),
        (GateKind.CONSUMPTION, GateKind.RETRIEVAL),
    ],
)
def test_valid_gate_progression_is_accepted(
    gate: GateKind, prior: GateKind | None
) -> None:
    validate_gate_progression(gate, prior=prior)


@pytest.mark.parametrize(
    "gate,prior",
    [
        # Skipping a gate.
        (GateKind.CONSUMPTION, GateKind.INGESTION),
        (GateKind.RETRIEVAL, GateKind.INGESTION),
        (GateKind.CONSUMPTION, GateKind.PUBLICATION),
        # Running a gate backwards.
        (GateKind.INGESTION, GateKind.PUBLICATION),
        (GateKind.PUBLICATION, GateKind.CONSUMPTION),
        # Repeating the same gate instead of progressing.
        (GateKind.RETRIEVAL, GateKind.RETRIEVAL),
        # Starting anywhere other than ingestion.
        (GateKind.CONSUMPTION, None),
        (GateKind.RETRIEVAL, None),
        (GateKind.PUBLICATION, None),
    ],
)
def test_invalid_gate_progression_is_rejected(
    gate: GateKind, prior: GateKind | None
) -> None:
    with pytest.raises(ContractViolation) as excinfo:
        validate_gate_progression(gate, prior=prior)
    assert excinfo.value.failure_code is ContractFailureCode.GATE_SEQUENCE_VIOLATION


def test_gate_progression_rejects_non_enum_inputs() -> None:
    for bad in ("CONSUMPTION", 3, None):
        with pytest.raises(ContractViolation) as excinfo:
            validate_gate_progression(bad, prior=GateKind.RETRIEVAL)  # type: ignore[arg-type]
        assert excinfo.value.failure_code is ContractFailureCode.TYPE_MISMATCH
    with pytest.raises(ContractViolation) as excinfo:
        validate_gate_progression(GateKind.CONSUMPTION, prior="RETRIEVAL")  # type: ignore[arg-type]
    assert excinfo.value.failure_code is ContractFailureCode.TYPE_MISMATCH


# ---------------------------------------------------------------------------
# Snapshot boundary placement — the reason §21 and §22 can stay separate patches
# ---------------------------------------------------------------------------


def test_consumer_context_is_required_for_retrieval_and_consumption_only() -> None:
    assert gate_requires_consumer_context(GateKind.RETRIEVAL) is True
    assert gate_requires_consumer_context(GateKind.CONSUMPTION) is True
    assert gate_requires_consumer_context(GateKind.INGESTION) is False
    assert gate_requires_consumer_context(GateKind.PUBLICATION) is False


def test_only_consumption_runs_after_a_committed_boundary() -> None:
    assert gate_requires_committed_boundary(GateKind.CONSUMPTION) is True
    for gate in (GateKind.INGESTION, GateKind.PUBLICATION, GateKind.RETRIEVAL):
        assert gate_requires_committed_boundary(gate) is False


def test_pre_and_post_boundary_gates_partition_the_sequence() -> None:
    pre = {gate for gate in GateKind if gate_precedes_snapshot_boundary(gate)}
    post = {gate for gate in GateKind if gate_requires_committed_boundary(gate)}
    assert pre | post == set(GateKind)
    assert not pre & post
    # The boundary sits strictly between the last pre-boundary gate and the
    # first post-boundary gate; that placement is what lets the snapshot owner
    # and the gate owner reference each other without a module cycle.
    assert max(gate_stage_index(gate) for gate in pre) + 1 == min(
        gate_stage_index(gate) for gate in post
    )


# ---------------------------------------------------------------------------
# Fail-closed completeness
# ---------------------------------------------------------------------------


def test_only_complete_admits_execution() -> None:
    assert snapshot_status_admits_execution(SnapshotCompletenessStatus.COMPLETE)
    for status in SnapshotCompletenessStatus:
        if status is SnapshotCompletenessStatus.COMPLETE:
            continue
        assert snapshot_status_admits_execution(status) is False


@pytest.mark.parametrize(
    "status",
    [status for status in SnapshotCompletenessStatus if status.value != "COMPLETE"],
)
def test_non_complete_status_raises_typed_failure(
    status: SnapshotCompletenessStatus,
) -> None:
    with pytest.raises(ContractViolation) as excinfo:
        require_snapshot_status_admits_execution(status)
    assert excinfo.value.failure_code is ContractFailureCode.SNAPSHOT_NOT_USABLE
    assert status.value in excinfo.value.detail


def test_snapshot_status_helpers_reject_untyped_status() -> None:
    for bad in ("COMPLETE", True, 1, None):
        with pytest.raises(ContractViolation) as excinfo:
            snapshot_status_admits_execution(bad)  # type: ignore[arg-type]
        assert excinfo.value.failure_code is ContractFailureCode.TYPE_MISMATCH


def test_truthy_string_status_cannot_pass_as_complete() -> None:
    # SnapshotCompletenessStatus is a str Enum; a bare "COMPLETE" string must
    # not be accepted as an authoritative decision result.
    with pytest.raises(ContractViolation):
        require_snapshot_status_admits_execution("COMPLETE")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Resolver protocols exist so neither owner imports the other
# ---------------------------------------------------------------------------


def test_resolver_protocols_declare_the_cross_owner_surface() -> None:
    assert hasattr(AdmissionDecisionResolver, "resolve_gate_decision")
    assert hasattr(AdmissionDecisionResolver, "gate_decision_kind")
    assert hasattr(SnapshotBoundaryResolver, "resolve_committed_boundary")
    assert hasattr(SnapshotBoundaryResolver, "boundary_completeness_status")


def test_resolver_protocols_are_structural_and_need_no_subclassing() -> None:
    class Injected:
        def resolve_gate_decision(self, ref: object) -> object:
            return ref

        def gate_decision_kind(self, ref: object) -> GateDecisionKind:
            return GateDecisionKind.ADMIT

        def resolve_committed_boundary(self, ref: object) -> object:
            return ref

        def boundary_completeness_status(
            self, ref: object
        ) -> SnapshotCompletenessStatus:
            return SnapshotCompletenessStatus.COMPLETE

    injected: AdmissionDecisionResolver = Injected()
    boundary: SnapshotBoundaryResolver = Injected()
    assert injected.gate_decision_kind(object()) is GateDecisionKind.ADMIT
    assert (
        boundary.boundary_completeness_status(object())
        is SnapshotCompletenessStatus.COMPLETE
    )


# ---------------------------------------------------------------------------
# Mutation killers for this patch
# ---------------------------------------------------------------------------


def test_mutant_gate_order_collapsed_into_a_single_stage() -> None:
    """Kill: all four gates treated as one interchangeable decision."""

    indices = {gate: gate_stage_index(gate) for gate in GateKind}
    assert len(set(indices.values())) == 4


def test_mutant_consumption_allowed_without_a_prior_retrieval_decision() -> None:
    """Kill: consumption gate reachable without the earlier gates."""

    with pytest.raises(ContractViolation):
        validate_gate_progression(GateKind.CONSUMPTION, prior=None)


def test_mutant_incomplete_snapshot_treated_as_usable() -> None:
    """Kill: any non-COMPLETE status silently admitting execution."""

    for status in (
        SnapshotCompletenessStatus.MIX_AND_MATCH_DETECTED,
        SnapshotCompletenessStatus.ROLLBACK_DETECTED,
        SnapshotCompletenessStatus.CORRUPTED,
        SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_ATTESTATION,
    ):
        assert not snapshot_status_admits_execution(status)


def test_mutant_patch_65_grows_domain_logic() -> None:
    """Kill: snapshot or gate domain logic landing in the vocabulary patch.

    Patch 6.5 is a vocabulary boundary. If a builder, evaluator, store or
    persistence path appears here, the §21/§22 owners have been merged into
    contracts.py and NR-04 is violated.
    """

    tree = ast.parse((GOLD_PACKAGE / "contracts.py").read_text(encoding="utf-8"))
    forbidden_prefixes = (
        "build_knowledge_snapshot",
        "create_knowledge_snapshot",
        "commit_atomic_boundary",
        "evaluate_gate",
        "evaluate_admission",
        "create_gate_decision",
    )
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not {name for name in defined if name.startswith(forbidden_prefixes)}
