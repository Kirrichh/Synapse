"""Stage 4 §22 gates in front of a library write (Patch 8 repair, round 13).

`put_behavior` used to ask for a `PublisherIdentity` and nothing else. A
publisher identity says *who* is writing; the ingestion and publication gates
decide *whether* the candidate may leave its source and whether the verified
object may be published. The one operation that puts an object into the library
asked neither of them, which is the bypass NR-09 forbids.

These tests are about that barrier being real rather than declared: the
capability cannot be assembled outside the gates, it is bound to one object, and
the two verdicts behind it are durable. The last test in the file is the
structural one — the name a write gives an object and the name a retrieval gives
the same object have to be the same name, or a §22 chain can never be carried
from ingestion through to consumption for anything that actually exists.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from synapse.experiments.gold import library_admission as LA
from synapse.experiments.gold.admission import AdmissionViolation, require_committed_decision
from synapse.experiments.gold.admission_journal import FileAdmissionJournal
from synapse.experiments.gold.behavior import (
    BehaviorCore,
    create_behavior_blob,
    create_behavior_manifest,
    create_behavior_unit,
)
from synapse.experiments.gold.canonicalization import GOLD_LIBRARY_SUBJECT_V1
from synapse.experiments.gold.contracts import (
    ContractViolation,
    GateKind,
    LibraryWriteAdmission,
    validate_library_write_admission,
)
from synapse.experiments.gold.library import (
    LIBRARY_PUBLISHER_IDENTITY_V1,
    BehaviorLibrary,
    LibraryFailureCode,
    LibraryViolation,
    PublisherIdentity,
    PutStatus,
)

from tests.gold_write_admission import gate_history as _gate_history
from tests.gold_write_admission import (
    GATE_NOW,
    write_admission,
    write_admission_evidence,
    write_gate_controller,
)
from tests.gold_store_fence import fence_for

_BEHAVIOR_VECTORS = Path(__file__).parent / "fixtures" / "gold" / "behavior_vectors_v1.json"


def _publisher() -> PublisherIdentity:
    return PublisherIdentity(
        LIBRARY_PUBLISHER_IDENTITY_V1,
        "stage4-library-publisher",
        "synapse.stage4.gold.publisher-policy/v1",
    )


def _behavior(*, output_name: str = "result"):
    vectors = json.loads(_BEHAVIOR_VECTORS.read_text(encoding="utf-8"))
    payload = copy.deepcopy(vectors["vectors"][0]["core"])
    payload["output_contract"]["fields"][0]["name"] = output_name
    core = BehaviorCore.from_dict(payload)
    unit = create_behavior_unit(
        behavior_kind=core.behavior_kind,
        canonical_program=core.canonical_program,
        input_contract=core.input_contract,
        output_contract=core.output_contract,
        capability_requirements=core.capability_requirements,
        replay_contract=core.replay_contract,
        verification_contract=core.verification_contract,
        binding_refs=core.binding_refs,
        source_evidence_refs=core.source_evidence_refs,
        artifact_refs=core.artifact_refs,
    )
    blob = create_behavior_blob(unit)
    manifest = create_behavior_manifest(unit, blob, compiler_binding=None)
    return unit, blob, manifest


def _library(tmp_path: Path) -> tuple[BehaviorLibrary, PublisherIdentity]:
    """A library and the exact publisher handle it was configured with.

    The library demands the configured instance, not an equal one, so a helper
    that minted a fresh publisher per call would fail on the publisher check
    before ever reaching the admission check under test.
    """

    publisher = _publisher()
    root = tmp_path / "library"
    root.mkdir()
    return BehaviorLibrary(
        root, publisher_identity=publisher, mutation_fence=fence_for(root),
        write_history=_gate_history(root.parent),
    ), publisher


# ---------------------------------------------------------------------------
# The capability cannot be assembled outside the gates
# ---------------------------------------------------------------------------


def test_the_write_admission_cannot_be_constructed() -> None:
    with pytest.raises(TypeError):
        LibraryWriteAdmission()


def test_a_hand_built_admission_with_every_right_field_is_refused(tmp_path: Path) -> None:
    """The seal, not the field values, is what the library trusts.

    The forgery is byte-identical to a genuine admission in everything the
    library compares. If the seal were not checked first, it would be accepted,
    and the gates would be a formality any caller could imitate.
    """

    unit, blob, manifest = _behavior()
    genuine = write_admission(unit, manifest, journal_root=tmp_path)
    forged = object.__new__(LibraryWriteAdmission)
    for field, value in genuine.to_dict().items():
        object.__setattr__(forged, field, value)
    object.__setattr__(forged, "admitted_at_utc", genuine.admitted_at_utc)

    with pytest.raises(ContractViolation):
        validate_library_write_admission(forged)

    # The write refuses under the forgery's own name rather than a library code.
    # "not gate minted" and "not admitted for this object" are different facts,
    # and collapsing the first into the second would report a valid capability
    # aimed at the wrong object — which is not what happened.
    library, publisher = _library(tmp_path)
    with pytest.raises(ContractViolation) as caught:
        library.put_behavior(
            unit, blob, manifest, publisher_identity=publisher, admission=forged
        )
    assert caught.value.failure_code.value == "TRUSTED_OBJECT_FORGED"
    assert library.search_index() == ()


def test_a_write_without_an_admission_does_not_typecheck(tmp_path: Path) -> None:
    """The barrier is in the signature, so it cannot be omitted at a call site."""

    unit, blob, manifest = _behavior()
    library, publisher = _library(tmp_path)
    with pytest.raises(TypeError):
        library.put_behavior(unit, blob, manifest, publisher_identity=publisher)


@pytest.mark.parametrize("substitute", [None, object(), "admitted", 1])
def test_a_write_admission_must_be_the_exact_sealed_record(substitute, tmp_path: Path) -> None:
    unit, blob, manifest = _behavior()
    library, publisher = _library(tmp_path)
    with pytest.raises(LibraryViolation) as caught:
        library.put_behavior(
            unit, blob, manifest, publisher_identity=publisher, admission=substitute
        )
    assert caught.value.failure_code is LibraryFailureCode.TYPE_MISMATCH


# ---------------------------------------------------------------------------
# The capability is bound to one object
# ---------------------------------------------------------------------------


def test_an_admission_for_another_object_does_not_authorize_this_write(tmp_path: Path) -> None:
    """A caller holding two candidates has the wrong admission lying around."""

    unit, blob, manifest = _behavior(output_name="first")
    other_unit, _, other_manifest = _behavior(output_name="second")
    assert other_unit.content_key.value != unit.content_key.value

    library, publisher = _library(tmp_path)
    with pytest.raises(LibraryViolation) as caught:
        library.put_behavior(
            unit,
            blob,
            manifest,
            publisher_identity=publisher,
            admission=write_admission(other_unit, other_manifest, journal_root=tmp_path),
        )
    assert caught.value.failure_code is LibraryFailureCode.WRITE_NOT_ADMITTED
    assert library.search_index() == ()


def test_an_admitted_write_is_stored_and_a_repeat_still_needs_one(tmp_path: Path) -> None:
    unit, blob, manifest = _behavior()
    library, publisher = _library(tmp_path)
    first = library.put_behavior(
        unit, blob, manifest, publisher_identity=publisher,
        admission=write_admission(unit, manifest, journal_root=tmp_path),
    )
    assert first.status is PutStatus.STORED

    with pytest.raises(TypeError):
        library.put_behavior(unit, blob, manifest, publisher_identity=publisher)

    second = library.put_behavior(
        unit, blob, manifest, publisher_identity=publisher,
        admission=write_admission(unit, manifest, journal_root=tmp_path),
    )
    assert second.status is PutStatus.DEDUPLICATED


# ---------------------------------------------------------------------------
# What the adapter refuses
# ---------------------------------------------------------------------------


def test_a_refused_ingestion_mints_nothing(tmp_path: Path) -> None:
    unit, _, manifest = _behavior()
    with pytest.raises(LA.WriteAdmissionViolation) as caught:
        write_admission_evidence(unit, manifest, journal_root=tmp_path, admit_ingestion=False)
    assert caught.value.failure_code is LA.WriteAdmissionFailureCode.INGESTION_REFUSED


def test_a_refused_publication_mints_nothing(tmp_path: Path) -> None:
    unit, _, manifest = _behavior()
    with pytest.raises(LA.WriteAdmissionViolation) as caught:
        write_admission_evidence(unit, manifest, journal_root=tmp_path, admit_publication=False)
    assert caught.value.failure_code is LA.WriteAdmissionFailureCode.PUBLICATION_REFUSED


def test_a_refused_gate_leaves_the_library_empty(tmp_path: Path) -> None:
    """No capability means no write, rather than a write with a missing record."""

    unit, blob, manifest = _behavior()
    library, _ = _library(tmp_path)
    with pytest.raises(LA.WriteAdmissionViolation):
        write_admission_evidence(unit, manifest, journal_root=tmp_path, admit_ingestion=False)
    assert library.search_index() == ()


def test_the_subject_must_be_named_by_exact_trusted_identities(tmp_path: Path) -> None:
    unit, _, manifest = _behavior()
    with pytest.raises(LA.WriteAdmissionViolation) as caught:
        LA.write_subject_ref(content_key=unit.content_key.value, manifest_id=manifest.manifest_id)
    assert caught.value.failure_code is LA.WriteAdmissionFailureCode.TYPE_MISMATCH


def test_the_evidence_record_is_factory_sealed() -> None:
    with pytest.raises(TypeError):
        LA.WriteAdmissionEvidence()


# ---------------------------------------------------------------------------
# The two verdicts behind the capability
# ---------------------------------------------------------------------------


def test_both_gates_are_evaluated_in_the_normative_order(tmp_path: Path) -> None:
    """Publication is decided with ingestion as its declared predecessor.

    That is what makes the pair a chain rather than two opinions: a publication
    verdict cannot exist without naming the ingestion verdict it followed, and
    the §22 predecessor check demands both describe the same subject set.
    """

    unit, _, manifest = _behavior()
    evidence = write_admission_evidence(unit, manifest, journal_root=tmp_path)
    assert evidence.ingestion.gate_kind is GateKind.INGESTION
    assert evidence.publication.gate_kind is GateKind.PUBLICATION
    assert evidence.ingestion.subject_keys() == evidence.publication.subject_keys()
    assert evidence.publication.predecessor_decision_digest == (
        evidence.ingestion.gate_decision_id.digest_sha256
    )


def test_the_admission_names_the_two_decisions_it_came_from(tmp_path: Path) -> None:
    unit, _, manifest = _behavior()
    evidence = write_admission_evidence(unit, manifest, journal_root=tmp_path)
    assert evidence.admission.ingestion_decision_id_sha256 == (
        evidence.ingestion.gate_decision_id.digest_sha256
    )
    assert evidence.admission.publication_decision_id_sha256 == (
        evidence.publication.gate_decision_id.digest_sha256
    )
    assert evidence.admission.admitted_at_utc == GATE_NOW


def test_both_verdicts_are_durable_and_survive_a_restart(tmp_path: Path) -> None:
    """A permission that is not durable is one a restart can silently forget.

    The receipts are re-verified against a journal object built fresh on the
    same path, so a pass cannot come from state the writer was still holding.
    """

    unit, _, manifest = _behavior()
    evidence = write_admission_evidence(unit, manifest, journal_root=tmp_path)
    assert len(evidence.receipts) == 2

    journals = sorted((tmp_path / "gate-journals").iterdir())
    assert len(journals) == 1
    reopened = FileAdmissionJournal(journals[0], fence_for(journals[0].parent))
    for receipt, decision in zip(evidence.receipts, (evidence.ingestion, evidence.publication)):
        require_committed_decision(receipt, decision=decision, journal=reopened)


def test_a_receipt_is_refused_against_a_journal_that_never_saw_it(tmp_path: Path) -> None:
    unit, _, manifest = _behavior()
    evidence = write_admission_evidence(unit, manifest, journal_root=tmp_path)
    elsewhere = FileAdmissionJournal(tmp_path / "elsewhere" / "decisions.journal", fence_for(tmp_path))
    with pytest.raises(AdmissionViolation):
        require_committed_decision(
            evidence.receipts[0], decision=evidence.ingestion, journal=elsewhere
        )


# ---------------------------------------------------------------------------
# The structural property: one name for the object's whole life
# ---------------------------------------------------------------------------


def test_the_write_and_the_retrieval_name_the_same_object_the_same_way(tmp_path: Path) -> None:
    """Without this, a §22 chain is unbuildable for every real object.

    The chain binds four decisions over one subject set and the predecessor
    check demands exact equality. The ingestion and publication verdicts are
    reached before the object is stored, when no index entry, attestation or
    lifecycle record exists; the retrieval and consumption verdicts are reached
    afterwards, from a full descriptor. If those two moments named the object
    differently, the four verdicts could never be joined — and nothing in the
    type system would have said so.
    """

    from tests.test_stage4_gold_compatibility import _make_harness
    from synapse.experiments.gold.retrieval import candidate_subject_ref

    harness = _make_harness(tmp_path / "harness")
    from_retrieval = candidate_subject_ref(harness.descriptor)
    from_write = LA.write_subject_ref(
        content_key=harness.unit.content_key,
        manifest_id=harness.manifest.manifest_id,
    )
    assert from_write == from_retrieval
    assert from_write.schema_id == GOLD_LIBRARY_SUBJECT_V1


def test_the_subject_name_binds_the_manifest_as_well_as_the_blob() -> None:
    """Same bytes, different manifest, different subject.

    Reachable in practice: a unit compiled under two compiler bindings yields
    one blob and two manifests. If the name ignored the manifest, an admission
    decided about one of them would authorize a write of the other, and the gate
    would have decided about an object that was never presented to it.
    """

    from synapse.experiments.gold.canonicalization import library_subject_ref

    blob_digest = "a" * 64
    shared = {"content_key": "synapse.stage4.gold.content-key/v1:" + blob_digest}
    left = library_subject_ref(
        **shared,
        manifest_id="synapse.stage4.gold.behavior-manifest-record/v1:" + "b" * 64,
        blob_digest_sha256=blob_digest,
        manifest_digest_sha256="b" * 64,
    )
    right = library_subject_ref(
        **shared,
        manifest_id="synapse.stage4.gold.behavior-manifest-record/v1:" + "c" * 64,
        blob_digest_sha256=blob_digest,
        manifest_digest_sha256="c" * 64,
    )
    assert left.ref_id == right.ref_id
    assert left.sha256 != right.sha256


def test_an_identity_that_does_not_carry_its_own_digest_is_refused() -> None:
    """One fact, one source.

    The digests are passed alongside the identities that already contain them,
    because ``ref_id`` needs one. That is two channels for the same fact, and
    two channels can disagree — so the identity must end with the digest it was
    given rather than the two being trusted to match.
    """

    from synapse.experiments.gold.canonicalization import (
        CanonicalizationViolation,
        library_subject_ref,
    )

    with pytest.raises(CanonicalizationViolation):
        library_subject_ref(
            content_key="synapse.stage4.gold.content-key/v1:" + "a" * 64,
            manifest_id="synapse.stage4.gold.behavior-manifest-record/v1:" + "b" * 64,
            blob_digest_sha256="c" * 64,
            manifest_digest_sha256="b" * 64,
        )
    with pytest.raises(CanonicalizationViolation):
        library_subject_ref(
            content_key="synapse.stage4.gold.content-key/v1:" + "a" * 64,
            manifest_id="synapse.stage4.gold.behavior-manifest-record/v1:" + "b" * 64,
            blob_digest_sha256="a" * 64,
            manifest_digest_sha256="c" * 64,
        )


def test_one_decision_cannot_stand_for_both_verdicts() -> None:
    """A capability naming the same verdict twice describes one gate, not two.

    Reaching for the private factory is the point: nothing a caller can do
    produces this, because the adapter always mints from two distinct verdicts.
    The guard exists for a future minting path that does not, and a guard that
    is never exercised is a guard nobody knows is there.
    """

    from datetime import datetime, timezone

    from synapse.experiments.gold.contracts import _mint_library_write_admission

    same = "d" * 64
    with pytest.raises(ContractViolation) as caught:
        _mint_library_write_admission(
            subject_ref_sha256="a" * 64,
            blob_digest_sha256="b" * 64,
            manifest_digest_sha256="c" * 64,
            policy_version="policy-v1",
            ingestion_decision_id_sha256=same,
            publication_decision_id_sha256=same,
            ingestion_decision_digest="e" * 64,
            publication_decision_digest="f" * 64,
            witnessed_journal_anchor="0" * 64,
            admitted_at_utc=datetime(2026, 8, 4, tzinfo=timezone.utc),
        )
    assert caught.value.failure_code.value == "MALFORMED_IDENTITY"


def test_two_objects_sharing_nothing_get_distinct_subject_names() -> None:
    left_unit, _, left_manifest = _behavior(output_name="left")
    right_unit, _, right_manifest = _behavior(output_name="right")
    left = LA.write_subject_ref(
        content_key=left_unit.content_key, manifest_id=left_manifest.manifest_id
    )
    right = LA.write_subject_ref(
        content_key=right_unit.content_key, manifest_id=right_manifest.manifest_id
    )
    assert left != right and left.sha256 != right.sha256


def test_the_gate_controller_used_by_these_suites_really_decides(tmp_path: Path) -> None:
    """The permissive harness must not be permissive by short-circuiting.

    A helper that returned an ADMIT without consulting a probe would make every
    test above pass while proving nothing, so the refusing configuration has to
    reach a refusal through the same code path.
    """

    controller, requested = write_gate_controller(admit_ingestion=False)
    unit, _, manifest = _behavior()
    subject = LA.write_subject_ref(
        content_key=unit.content_key, manifest_id=manifest.manifest_id
    )
    from synapse.experiments.gold.admission import evaluate_ingestion_gate

    decision = evaluate_ingestion_gate(controller, subject_refs=(subject,))
    assert not decision.admitted


def test_a_write_is_refused_after_its_gate_decisions_are_deleted(tmp_path: Path) -> None:
    """The reviewer's P0 sequence, and the test that should have come first.

    Both gates ran, both verdicts were committed, and the capability was minted
    from them — so holding it proved the decisions were durable *then*. Round 19
    never re-asked, so deleting the gate journal between the mint and the write
    left the library storing the object anyway. This module's own docstring named
    the hole and said closing it meant giving the library a journal port; the
    honesty of that note did not make the hole smaller.
    """

    library, publisher = _library(tmp_path)
    unit, blob, manifest = _behavior()
    admission = write_admission(unit, manifest, journal_root=tmp_path)

    history = _gate_history(tmp_path)
    assert history.contains_record(admission.ingestion_decision_digest)
    history.path.unlink()

    with pytest.raises(LibraryViolation) as caught:
        library.put_behavior(
            unit, blob, manifest, publisher_identity=publisher, admission=admission
        )
    assert caught.value.failure_code is LibraryFailureCode.WRITE_NOT_ADMITTED
    assert "extends the anchor" in caught.value.detail
    assert library.search_index() == (), "a refused write stores nothing"


def test_a_write_is_refused_when_the_gate_history_was_reordered(tmp_path: Path) -> None:
    """Membership survives a fork; the witnessed anchor does not.

    Both decisions are still in the journal — `contains_record` says yes twice —
    but the history was rebuilt in the other order, so it is a record of something
    that did not happen. Checking membership alone would have admitted this write,
    which is why the capability carries the anchor it witnessed at mint and the
    library asks whether committed history still extends it.
    """

    from synapse.experiments.gold.persistence import scan_journal

    library, publisher = _library(tmp_path)
    unit, blob, manifest = _behavior()
    admission = write_admission(unit, manifest, journal_root=tmp_path)

    history = _gate_history(tmp_path)
    payloads = [frame.payload for frame in scan_journal(history.path).frames]
    assert len(payloads) == 2
    history.path.unlink()
    for payload in reversed(payloads):
        history.append_record(payload)

    assert history.contains_record(admission.ingestion_decision_digest)
    assert history.contains_record(admission.publication_decision_digest)

    with pytest.raises(LibraryViolation) as caught:
        library.put_behavior(
            unit, blob, manifest, publisher_identity=publisher, admission=admission
        )
    assert caught.value.failure_code is LibraryFailureCode.WRITE_NOT_ADMITTED
    assert "extends the anchor" in caught.value.detail


def test_a_later_unrelated_write_does_not_invalidate_an_earlier_admission(
    tmp_path: Path,
) -> None:
    """Growth is legal; only a fork is not — and the control the two refusals need.

    Without this, the two tests above show only that something says no. A shared
    gate journal grows as other objects are admitted, and an anchor check that
    refused on ordinary growth would make one write's validity depend on whether
    anyone else wrote afterwards.
    """

    library, publisher = _library(tmp_path)
    unit, blob, manifest = _behavior()
    admission = write_admission(unit, manifest, journal_root=tmp_path)

    other_unit, other_blob, other_manifest = _behavior(output_name="second")
    write_admission(other_unit, other_manifest, journal_root=tmp_path)
    assert len(_gate_history(tmp_path)._digests()) == 4

    stored = library.put_behavior(
        unit, blob, manifest, publisher_identity=publisher, admission=admission
    )
    assert stored.status.value == "STORED"
