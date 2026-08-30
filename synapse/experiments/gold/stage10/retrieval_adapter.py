"""Narrow conversion from admitted retrieval output to typed Stage 10 refs."""

from __future__ import annotations

from dataclasses import dataclass

from ..canonicalization import HashBoundRef
from ..retrieval import RetrievalAdmission, validate_retrieval_admission


@dataclass(frozen=True)
class RetrievalCandidateRefs:
    admitted_refs: tuple[HashBoundRef, ...]
    consumer_context_ref: HashBoundRef
    boundary_ref: HashBoundRef
    frozen_candidate_set_ref: HashBoundRef

    def __post_init__(self) -> None:
        if type(self.admitted_refs) is not tuple or any(
            type(item) is not HashBoundRef for item in self.admitted_refs
        ):
            raise TypeError("admitted_refs must be a tuple of exact HashBoundRef values")
        keys = tuple(
            (item.kind.value, item.ref_id, item.sha256) for item in self.admitted_refs
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("admitted_refs must be sorted and unique")
        for value in (
            self.consumer_context_ref,
            self.boundary_ref,
            self.frozen_candidate_set_ref,
        ):
            if type(value) is not HashBoundRef:
                raise TypeError("retrieval binding refs must be exact HashBoundRef values")


def typed_candidate_refs(admission: RetrievalAdmission) -> RetrievalCandidateRefs:
    """Return only the exact refs a durable retrieval decision admitted."""

    validate_retrieval_admission(admission)
    ordered = tuple(
        sorted(
            admission.admitted_refs,
            key=lambda item: (item.kind.value, item.ref_id, item.sha256),
        )
    )
    return RetrievalCandidateRefs(
        admitted_refs=ordered,
        consumer_context_ref=admission.consumer_context_ref,
        boundary_ref=admission.boundary_ref,
        frozen_candidate_set_ref=admission.frozen_candidate_set_ref,
    )
