"""Projection of domain evidence into the typed findings the §22 gates read.

This is an adapter attached to the admission owner, and it exists to fix a
dependency that ran the wrong way.

The four gates need compatibility and taint stated in their own narrow
vocabulary — ``CompatibilityFinding``, ``TaintFinding`` — and something has to
convert the domain owners' rich records into those. An earlier revision put the
conversion inside the domain owners themselves, so ``compatibility.py`` and
``taint.py`` each imported ``admission``. That inverts the adapter-first
direction: stage 6 compatibility and stage 5 taint are earlier owners, and
making them import the stage 8 consumer means an earlier contour cannot be
built, reasoned about or changed without the later one's vocabulary. It also
put a latent cycle one import away.

The conversion belongs to whoever needs the conversion. So it lives here: this
module imports ``compatibility``, ``taint``, ``knowledge`` and ``admission``,
and none of those domain owners imports the gates. The direction is now what the
model asks for — the late layer depends on the early owners, not the reverse.

``knowledge`` joins that list for the boundary probe. §21 and §22 must not
import each other, and this is the module that is allowed to know both sides, so
the projection from a committed snapshot boundary to the boolean the consumption
gate reads belongs here for exactly the reason the compatibility projection
does.

Nothing here decides anything. The domain owners establish compatibility and
effective taint; the gates decide admission; this module only restates the
former in the vocabulary of the latter, losing detail in one direction and
adding no authority in either.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Callable

from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
    library_subject_ref,
)
from .admission import CompatibilityFinding, TaintFinding
from .compatibility import (
    CompatibilityConflictScan,
    CompatibilityContext,
    CompatibilityDecision,
    CompatibilityDecisionKind,
    CompatibilityFailureCode,
    CompatibilityRevalidationRecord,
    CompatibilitySubjectDescriptor,
    ConfiguredCompatibilityEvaluator,
    ConflictDecisionKind,
    EvidenceCompleteness,
    RevalidationOutcome,
    RevalidationStage,
    revalidate_before_consumption,
    validate_compatibility_conflict_scan,
    validate_compatibility_context,
    validate_compatibility_decision,
    validate_compatibility_revalidation_record,
    validate_compatibility_subject_descriptor,
)
from .contracts import RecordId
from .knowledge import (
    KnowledgeContext,
    KnowledgeFailureCode,
    KnowledgeViolation,
    atomic_boundary_ref,
    open_usable_snapshot,
    require_usable_snapshot,
)
from .taint import EffectiveTaint, TaintFailureCode, effective_taint_blocks

#: The non-public surface this adapter takes from the owners it projects
#: between. Declared for the same reason ``point_of_use.ADAPTER_PRIVATE_SEAM``
#: is: a shared internal is a decision, and a decision that is written down can
#: be reviewed when it changes.
ADAPTER_PRIVATE_SEAM = ("_context", "_evaluator")


def _compat_fail(code: CompatibilityFailureCode, detail: str) -> Exception:
    from .compatibility import CompatibilityViolation

    return CompatibilityViolation(code, detail)


def _unavailable(detail: str) -> Exception:
    """Say that the evidence source had no answer, in the gates' own type.

    A subject nobody prepared evidence for is not a subject found incompatible,
    and it is not a subject found clean either. Fabricating a finding to say so
    would put a verdict in the record that no evaluator reached. The gate has a
    vocabulary for exactly this — a dependency that could not answer — and it
    blocks on it, which is the outcome that is both fail-closed and true.
    """

    from .admission import GateDependencyUnavailable

    return GateDependencyUnavailable(detail)


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _ref_key(value: HashBoundRef) -> str:
    return f"{value.kind.value}\x00{value.ref_id}\x00{value.sha256}"


def consumer_context_ref_of(context: CompatibilityContext) -> HashBoundRef:
    """Derive the gate's consumer-context reference from the context itself.

    One fact, one statement. The compatibility context already *is* the frozen
    consumer context — its identity is computed from the observation, policy,
    environment, tool and scope inputs — so the gate's reference is derived from
    it rather than accepted beside it. Taking it as a separate argument would
    reintroduce the defect the compatibility port had: two sources for one fact,
    free to disagree, with the record naming one and the evidence computed
    against the other.
    """

    validate_compatibility_context(context, evaluator=context._evaluator)
    payload = _canonical(context.to_dict())
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=context.context_id.digest_sha256,
        schema_id=context.schema_version,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


def candidate_subject_ref(descriptor: object) -> HashBoundRef:
    """The hash-bound reference by which a candidate is known to the §22 gates.

    Built from the descriptor's own content key and blob digest, so the thing
    the gate decides about is the exact object the loader would read — not a
    name that could later resolve elsewhere. A library reference carries a
    namespace and a digest; a gate reference has to be a ``HashBoundRef``, and
    this is the one conversion between them.

    It lives with the other projections rather than in the retrieval owner
    because the write side needs the same name and both sides must get it from
    one place. Two modules deriving a gate name from a descriptor is one rule
    written twice, and a rule written twice cannot be shown to be enforced.
    """

    validate_compatibility_subject_descriptor(descriptor)
    return library_subject_ref(
        content_key=descriptor.content_key.value,
        manifest_id=descriptor.manifest_id.value,
        blob_digest_sha256=descriptor.blob_ref.digest_sha256,
        manifest_digest_sha256=descriptor.manifest_ref.digest_sha256,
    )


def _knowledge_fail(detail: str) -> Exception:
    """Refuse in the §21 owner's own vocabulary.

    A malformed argument to a snapshot-backed probe is a §21 type failure, not a
    compatibility one; borrowing the nearer module's error code would file the
    incident against the wrong owner.
    """

    return KnowledgeViolation(KnowledgeFailureCode.TYPE_MISMATCH, detail)


def _taint_fail(code: TaintFailureCode, detail: str) -> Exception:
    from .taint import TaintViolation

    return TaintViolation(code, detail)


def consumption_finding_from_revalidation(
    value: CompatibilityRevalidationRecord,
    *,
    decision: CompatibilityDecision,
    conflict_scan: CompatibilityConflictScan,
    subject_ref: HashBoundRef,
    consumer_context_ref: HashBoundRef,
) -> CompatibilityFinding:
    """State a compatibility revalidation in the vocabulary the gates read.

    The projection is deliberately lossy in one direction only: it exposes
    whether the evidence is complete, whether the subject is compatible, whether
    state drifted since the original decision and whether a conflict remains
    unresolved, and it exposes nothing that would let a gate infer an admission
    from a stale status.

    A revalidation that did not reach the consumption stage is refused rather
    than read as "not drifted": absence of a fresh check is not evidence of
    stability.

    ``conflict_scan`` is required for the same reason, and it used to default to
    ``None``. That default was a fail-open — a caller that ran no scan produced
    ``conflicts_unresolved=False``, so *missing evidence* and *evidence of no
    conflict* became one value. Conflict is a dimension the consumption gate
    declares it checked, and NR-10 forbids exactly this substitution: absent,
    unknown and false are not interchangeable. A caller with nothing to scan
    says so with a scan whose decision kind says so; it cannot say it by
    omission.

    The finding also names the subject and the frozen consumer context it is
    about, so the gate can check that the answer belongs to the question.
    """

    validate_compatibility_revalidation_record(value)
    if value.stage is not RevalidationStage.BEFORE_CONSUMPTION:
        raise _compat_fail(
            CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
            "consumption evidence requires a stage-3 revalidation record",
        )
    if type(decision) is not CompatibilityDecision:
        raise _compat_fail(
            CompatibilityFailureCode.TYPE_MISMATCH,
            "consumption evidence requires an exact decision",
        )
    validate_compatibility_decision(
        decision, evaluator=decision._evaluator, context=value._context, descriptor=None
    )
    if value.original_decision_id != decision.decision_id:
        raise _compat_fail(
            CompatibilityFailureCode.AUTHORITY_DECISION_INVALID,
            "revalidation does not belong to the supplied decision",
        )
    if type(conflict_scan) is not CompatibilityConflictScan:
        raise _compat_fail(
            CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE,
            "consumption evidence requires an exact conflict scan",
        )
    validate_compatibility_conflict_scan(conflict_scan, evaluator=decision._evaluator)
    for ref, name in ((subject_ref, "subject_ref"), (consumer_context_ref, "consumer_context_ref")):
        if type(ref) is not HashBoundRef:
            raise _compat_fail(
                CompatibilityFailureCode.TYPE_MISMATCH,
                f"consumption evidence requires an exact {name}",
            )
    return CompatibilityFinding(
        compatible=decision.decision_kind is CompatibilityDecisionKind.COMPATIBLE,
        evidence_complete=decision.evidence.completeness is EvidenceCompleteness.COMPLETE,
        drifted=value.outcome is not RevalidationOutcome.PASSED,
        conflicts_unresolved=conflict_scan.decision_kind is not ConflictDecisionKind.NO_CONFLICT_FOUND,
        subject_ref=subject_ref,
        consumer_context_ref=consumer_context_ref,
    )


def consumption_finding_from_effective_taint(value: EffectiveTaint) -> TaintFinding:
    """State a reconstructed effective taint in the vocabulary the gates read.

    Completeness is read off the record rather than taken as an argument. A
    profile is only meaningful together with the evidence that it is the *whole*
    chain, and the record already carries whether that was established: an
    ``EffectiveTaint`` from ``reconstruct_effective_taint`` says no, one from
    ``require_taint_consumable`` says yes, because only the latter consulted the
    anchored history store.

    A reduced profile presented without its complete source/derivation/authority
    closure therefore reaches the gate as incomplete rather than as permissive,
    and the gate refuses it instead of believing it — with no caller having had
    the opportunity to assert otherwise.
    """

    if type(value) is not EffectiveTaint:
        raise _taint_fail(TaintFailureCode.TYPE_MISMATCH, "effective taint must be an exact record")
    if type(value.chain_complete) is not bool:
        raise _taint_fail(TaintFailureCode.TYPE_MISMATCH, "chain_complete must be an exact bool")
    blocks_consumption, blocks_publication = effective_taint_blocks(value)
    return TaintFinding(
        consumable=not blocks_consumption and not value.quarantined,
        chain_complete=value.chain_complete,
        quarantined=bool(value.quarantined),
        blocks_publication=blocks_publication,
    )


_BINDING_SEAL = object()


@dataclass(frozen=True, init=False)
class ConsumptionEvidenceBinding:
    """The Stage 3 records that let one subject be revalidated at consumption.

    A binding is not evidence. It is the four things a stage-3 revalidation
    needs — the descriptor, the decision that was originally reached, the
    stage-2 record that decision has to extend, and the conflict scan — held
    together so that the revalidation cannot be run against a mismatched set.
    The evidence is produced later, at the moment the gate asks, because a
    revalidation performed in advance is a claim about a moment that has passed.
    """

    subject_ref: HashBoundRef
    context_ref: HashBoundRef
    descriptor: CompatibilitySubjectDescriptor
    original_decision: CompatibilityDecision
    before_loading: CompatibilityRevalidationRecord
    conflict_scan: CompatibilityConflictScan
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ConsumptionEvidenceBinding:
        raise TypeError("ConsumptionEvidenceBinding is produced only by bind_consumption_evidence")


def validate_consumption_evidence_binding(
    value: ConsumptionEvidenceBinding,
) -> ConsumptionEvidenceBinding:
    if (
        type(value) is not ConsumptionEvidenceBinding
        or getattr(value, "_trusted_seal", None) is not _BINDING_SEAL
    ):
        raise _compat_fail(
            CompatibilityFailureCode.TRUSTED_OBJECT_FORGED,
            "consumption evidence binding is not factory sealed",
        )
    return value


def bind_consumption_evidence(
    *,
    descriptor: CompatibilitySubjectDescriptor,
    original_decision: CompatibilityDecision,
    before_loading: CompatibilityRevalidationRecord,
    conflict_scan: CompatibilityConflictScan,
) -> ConsumptionEvidenceBinding:
    """Hold four Stage 3 records together, or refuse them.

    Each check exists because the corresponding mismatch produces a revalidation
    that looks valid and is about the wrong thing: a decision reached for another
    descriptor, a stage-2 record belonging to another decision, or a record that
    is not stage 2 at all and so cannot be the predecessor a stage-3 record
    extends.

    The subject reference is *derived* from the descriptor rather than accepted
    beside it, so a binding cannot claim to be about a subject the descriptor
    does not name.
    """

    validate_compatibility_subject_descriptor(descriptor)
    if type(original_decision) is not CompatibilityDecision:
        raise _compat_fail(
            CompatibilityFailureCode.TYPE_MISMATCH,
            "a binding requires an exact original decision",
        )
    evaluator = original_decision._evaluator
    validate_compatibility_revalidation_record(before_loading)
    # The context comes from the stage-2 record rather than from an argument.
    # A decision, a descriptor and a context are only meaningful together, and
    # accepting the context beside the records would let a caller validate the
    # decision against one context and revalidate it against another.
    context = before_loading._context
    validate_compatibility_decision(
        original_decision,
        evaluator=evaluator,
        context=context,
        descriptor=descriptor,
    )
    if before_loading.stage is not RevalidationStage.BEFORE_LOADING:
        raise _compat_fail(
            CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
            "a consumption binding requires the stage-2 record as its predecessor",
        )
    # Validating the decision against the stage-2 record's *own* context and this
    # descriptor is what ties the three together, and it does so completely: a
    # compatibility decision's identity is determined by its evaluator, context
    # and descriptor, so re-evaluating the same inputs reproduces the same
    # decision id. An earlier revision also compared ``original_decision_id`` and
    # ``descriptor_id`` explicitly. Both survived their own removal in the
    # campaign, and no separating case exists to write a test around — they were
    # restatements of what the line above already establishes, and a rule stated
    # in several places is a rule none of whose statements can be shown to be
    # the one enforcing it.
    if type(conflict_scan) is not CompatibilityConflictScan:
        raise _compat_fail(
            CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE,
            "a binding requires an exact conflict scan",
        )
    validate_compatibility_conflict_scan(conflict_scan, evaluator=evaluator)

    binding = object.__new__(ConsumptionEvidenceBinding)
    object.__setattr__(binding, "subject_ref", candidate_subject_ref(descriptor))
    object.__setattr__(binding, "context_ref", consumer_context_ref_of(context))
    object.__setattr__(binding, "descriptor", descriptor)
    object.__setattr__(binding, "original_decision", original_decision)
    object.__setattr__(binding, "before_loading", before_loading)
    object.__setattr__(binding, "conflict_scan", conflict_scan)
    object.__setattr__(binding, "_trusted_seal", _BINDING_SEAL)
    return validate_consumption_evidence_binding(binding)


def configured_revalidation_probe(
    *,
    evaluator: ConfiguredCompatibilityEvaluator,
    context: CompatibilityContext,
    bindings: tuple[ConsumptionEvidenceBinding, ...],
) -> Callable[[HashBoundRef, HashBoundRef], CompatibilityFinding]:
    """Build the consumption gate's compatibility probe from real Stage 3 records.

    Until this existed, ``configure_gate_controller`` took a
    ``compatibility_probe`` and every caller supplied a callable of its own. The
    §22 gate therefore consulted *something* about compatibility, and nothing in
    the system required that something to be a stage-3 revalidation of the exact
    subject against the exact consumer context. The projection from a
    revalidation record to a finding already existed; what did not exist was
    anything that made the gate read one.

    The probe this returns performs the revalidation **when the gate asks**, not
    in advance. That is the whole point of a stage-3 check: a record produced
    earlier describes a moment that has already passed, and an object can be
    revoked or drift between preparation and use.

    Three refusals, each fail-closed:

    * a consumer context reference that is not the configured context's — the
      probe answers about one consumer and refuses to be asked about another;
    * a subject with no binding — reported as a dependency that could not
      answer, because inventing a finding would put a verdict in the record that
      no evaluator reached;
    * a binding whose subject reference is not the one asked about, which cannot
      happen through ``bind_consumption_evidence`` and is checked anyway, since
      the index is built from values the caller supplied.
    """

    validate_compatibility_context(context, evaluator=evaluator)
    if type(bindings) is not tuple:
        raise _compat_fail(
            CompatibilityFailureCode.TYPE_MISMATCH,
            "the probe requires an exact tuple of bindings",
        )
    configured_context_ref = consumer_context_ref_of(context)
    indexed: dict[str, ConsumptionEvidenceBinding] = {}
    for item in bindings:
        validate_consumption_evidence_binding(item)
        key = _ref_key(item.subject_ref)
        if key in indexed:
            raise _compat_fail(
                CompatibilityFailureCode.SUBJECT_DESCRIPTOR_MISMATCH,
                "two bindings claim the same subject",
            )
        if _ref_key(item.context_ref) != _ref_key(configured_context_ref):
            raise _compat_fail(
                CompatibilityFailureCode.CONTEXT_MISMATCH,
                "a binding was prepared against another consumer context",
            )
        indexed[key] = item

    def probe(subject_ref: HashBoundRef, consumer_context_ref: HashBoundRef) -> CompatibilityFinding:
        for ref, name in (
            (subject_ref, "subject_ref"),
            (consumer_context_ref, "consumer_context_ref"),
        ):
            if type(ref) is not HashBoundRef:
                raise _compat_fail(
                    CompatibilityFailureCode.TYPE_MISMATCH,
                    f"the probe requires an exact {name}",
                )
        if _ref_key(consumer_context_ref) != _ref_key(configured_context_ref):
            raise _compat_fail(
                CompatibilityFailureCode.CONTEXT_MISMATCH,
                "the probe was asked about another consumer context",
            )
        binding = indexed.get(_ref_key(subject_ref))
        if binding is None:
            raise _unavailable("no stage-3 evidence was prepared for this subject")
        revalidation = revalidate_before_consumption(
            evaluator=evaluator,
            context=context,
            descriptor=binding.descriptor,
            original_decision=binding.original_decision,
            before_loading=binding.before_loading,
        )
        return consumption_finding_from_revalidation(
            revalidation,
            decision=binding.original_decision,
            conflict_scan=binding.conflict_scan,
            subject_ref=binding.subject_ref,
            consumer_context_ref=configured_context_ref,
        )

    return probe


@dataclass(frozen=True)
class SnapshotAdmissionHistory:
    """A decision journal restated in the §21 owner's failure vocabulary.

    `commit_atomic_snapshot_boundary` confirms the admission root it records
    against the journal that owns it, and `knowledge.py` may not import the
    admission package — so it cannot recognise `JournalAdapterViolation` or
    `GateDependencyUnavailable`, and it stops short of guessing.

    Translating them is this adapter's job, for the same reason projecting a
    compatibility record into a gate finding is: the conversion belongs to
    whoever needs the conversion, and this is the module allowed to know both
    sides. Passing a raw `FileAdmissionJournal` still *works*, but its failures
    arrive unclassified; wrapped, they arrive as what they are.
    """

    journal: object

    def current_anchor(self) -> str:
        return self._translated(lambda: self.journal.current_anchor())

    def extends(self, anchor: str) -> bool:
        return self._translated(lambda: self.journal.extends(anchor))

    def _translated(self, call: Callable[[], object]) -> object:
        from .admission import GateDependencyUnavailable
        from .admission_journal import JournalAdapterFailureCode, JournalAdapterViolation

        try:
            return call()
        except GateDependencyUnavailable as exc:
            raise KnowledgeViolation(
                KnowledgeFailureCode.STORE_UNAVAILABLE,
                "the admission journal could not be read",
            ) from exc
        except JournalAdapterViolation as exc:
            if exc.failure_code is JournalAdapterFailureCode.JOURNAL_CORRUPT:
                raise KnowledgeViolation(
                    KnowledgeFailureCode.ADMISSION_HISTORY_CORRUPT,
                    "the admission journal path holds a store this adapter did not write",
                ) from exc
            raise KnowledgeViolation(
                KnowledgeFailureCode.TYPE_MISMATCH,
                f"the admission journal refused with {exc.failure_code.value}",
            ) from exc


def configured_boundary_probe(
    *,
    root: Path,
    transaction_id: str,
    attempt_boundary_id: RecordId,
    expected_context: KnowledgeContext,
) -> Callable[[HashBoundRef], bool]:
    """Build the consumption gate's boundary probe from a committed §21 snapshot.

    ``configure_gate_controller`` takes a ``boundary_probe`` and every caller
    supplied a callable of its own, so the §22 gate asked *something* whether a
    snapshot boundary was committed and nothing required that something to have
    read a committed boundary. ``open_usable_snapshot`` and
    ``require_usable_snapshot`` existed and had no caller outside the tests.

    The probe re-opens the committed transaction **when the gate asks**, not when
    it is wired. That is deliberate and it is the same argument stage 3 makes for
    revalidation: a snapshot verified at wiring time describes a moment that has
    passed, and committed bytes can be edited, a marker can be rewritten and a
    boundary can be re-pointed in between. Paying a disk read per question is
    what buys the property in ``evaluate_consumption_gate``'s own docstring —
    nothing is taken from an earlier verdict.

    Four outcomes, as far apart as a boolean port allows:

    * ``True`` — the committed snapshot verifies and is the boundary asked about;
    * ``False`` — it verifies and the question was about a different boundary;
    * ``GateDependencyUnavailable`` — the store could not be read, an outage;
    * a typed §21 violation — the committed snapshot exists and is damaged, which
      is not "a different boundary" and must not be reported as one.
    """

    # ``isinstance`` rather than an exact type check: ``Path`` instantiates as a
    # platform subclass, so ``type(p) is Path`` is false for every real path.
    # ``persistence`` takes the same view for the same reason.
    if not isinstance(root, Path):
        raise _knowledge_fail("the boundary probe requires a Path to the boundary store")
    if type(transaction_id) is not str or not transaction_id:
        raise _knowledge_fail("the boundary probe requires an exact transaction id")

    def probe(boundary_ref: HashBoundRef) -> bool:
        if type(boundary_ref) is not HashBoundRef:
            raise _knowledge_fail("the boundary probe requires an exact HashBoundRef")
        try:
            snapshot = open_usable_snapshot(
                root, transaction_id=transaction_id, expected_boundary_id=attempt_boundary_id
            )
        except KnowledgeViolation as exc:
            if exc.failure_code is KnowledgeFailureCode.STORE_UNAVAILABLE:
                raise _unavailable("the committed snapshot store could not be read") from exc
            raise
        require_usable_snapshot(
            snapshot,
            attempt_boundary_id=attempt_boundary_id,
            expected_context=expected_context,
        )
        return _ref_key(boundary_ref) == _ref_key(atomic_boundary_ref(snapshot.boundary))

    return probe


__all__ = [
    "ADAPTER_PRIVATE_SEAM",
    "SnapshotAdmissionHistory",
    "configured_boundary_probe",
    "ConsumptionEvidenceBinding",
    "bind_consumption_evidence",
    "candidate_subject_ref",
    "configured_revalidation_probe",
    "consumer_context_ref_of",
    "consumption_finding_from_effective_taint",
    "consumption_finding_from_revalidation",
    "validate_consumption_evidence_binding",
]
