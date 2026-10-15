"""Synapse forms the coding candidate; an agent only proposes.

Every local-edit protocol run has one owner of interpretation. Before dispatch
Synapse may take its own exact-memory route: a single court-admitted, exactly
applicable patch becomes the candidate and no agent process starts. Otherwise
the agent returns its model's typed proposal verbatim, and Synapse interprets
it over the exact task and local information it delivered, with the frozen
protocol profile, then checks the delivered scope. Either candidate still needs
C1 and the independent oracle.
"""

from __future__ import annotations

from synapse.worker.input_contract import LocalInformationInput, WorkerInputViolation, WorkerTaskInput
from synapse.worker.local_edits import (
    LOCAL_EDIT_PROFILES, parse_local_edit_command, propose_local_edits, propose_verified_memory,
)

from .worker_transport import (
    SYNAPSE_MEMORY_TRANSPORT, WorkerCandidateReport, WorkerCandidateResult, WorkerCandidateStatus,
    WorkerCandidateUsage, WorkerDeliveryEvidence, WorkerDeliveryStatus, WorkerInvocation, WorkerTokenStatus,
)

#: The only diagnostic an agent contributes to interpretation: its model's
#: typed proposal text, never an interpreted result or selection.
AGENT_PROPOSAL = "local_edit_proposal"


def _inputs(invocation: WorkerInvocation):
    return (WorkerTaskInput(invocation.payload_text.encode("utf-8")),
            LocalInformationInput(invocation.information_text.encode("utf-8")))


def _within(invocation: WorkerInvocation, paths) -> bool:
    return all(any(path == root or path.startswith(root + "/") for root in invocation.allowed_scope) for path in paths)


def admitted_memory_candidate(invocation: WorkerInvocation, *, profile: str) -> WorkerCandidateResult | None:
    """Synapse's exact-memory route over the same bytes an agent would receive."""
    if invocation.information_text is None:
        return None
    task, information = _inputs(invocation)
    result = propose_verified_memory(task=task, information=information, profile=profile)
    if result is None or not _within(invocation, result["touched_files"]):
        return None
    return WorkerCandidateResult(
        status=WorkerCandidateStatus.PROPOSED_PATCH, diff_text=result["diff_text"],
        touched_files=tuple(result["touched_files"]),
        # No model or agent ran; Synapse reports its own exact zero consumption.
        usage=WorkerCandidateUsage(token_status=WorkerTokenStatus.TOOL_REPORTED, input_tokens=0, output_tokens=0,
                                   thinking_tokens=0, total_tokens=0, thinking_included=False),
        diagnostics={"local_edit_result": result, "scope_violations": ()},
        report=WorkerCandidateReport(summary=result["status"]),
        delivery_evidence=WorkerDeliveryEvidence(
            invocation_id=invocation.invocation_id, context_id=invocation.context_id,
            payload_sha256=invocation.payload_sha256, payload_byte_length=invocation.payload_byte_length,
            envelope_sha256=invocation.envelope_sha256, status=WorkerDeliveryStatus.SYNAPSE_EXACT_MEMORY,
            transport_name=SYNAPSE_MEMORY_TRANSPORT, input_schema_version=invocation.schema_version,
            information_sha256=invocation.information_sha256,
            information_byte_length=invocation.information_byte_length))


def interpret_agent_proposal(invocation: WorkerInvocation, worker: WorkerCandidateResult, *,
                             profile: str) -> WorkerCandidateResult:
    """Replace an agent's raw proposal with Synapse's interpreted candidate.

    A failed or timed-out agent keeps its own status. A proposal that does not
    parse, bind to the delivered sources or stay in scope becomes an explicit
    refusal; the agent's text is never an interpreted result.
    """
    if profile not in LOCAL_EDIT_PROFILES:
        raise ValueError("interpretation needs a local-edit protocol profile")
    diagnostics = dict(worker.diagnostics)
    if worker.status not in {WorkerCandidateStatus.PROPOSED_PATCH, WorkerCandidateStatus.NO_PATCH}:
        return worker
    if "local_edit_result" in diagnostics:
        raise ValueError("an agent cannot report an interpreted local result")
    command = diagnostics.get(AGENT_PROPOSAL)

    def refused(reason):
        return WorkerCandidateResult(status=WorkerCandidateStatus.ERROR, diff_text=None, touched_files=(),
            usage=worker.usage, diagnostics=diagnostics, report=WorkerCandidateReport(failure_reason=reason),
            delivery_evidence=worker.delivery_evidence)

    if type(command) is not str or worker.diff_text is not None or worker.touched_files or invocation.information_text is None:
        return refused("local_edit_proposal_absent_or_with_effects")
    task, information = _inputs(invocation)
    try:
        result = propose_local_edits(task=task, information=information,
                                     proposal=parse_local_edit_command(command), profile=profile)
    except (WorkerInputViolation, ValueError, TypeError, KeyError, RecursionError):
        return refused("local_edit_refused")
    if not _within(invocation, result["touched_files"]):
        return refused("local_edit_scope_mismatch")
    return WorkerCandidateResult(
        status=WorkerCandidateStatus.PROPOSED_PATCH if result["diff_text"] is not None else WorkerCandidateStatus.NO_PATCH,
        diff_text=result["diff_text"], touched_files=tuple(result["touched_files"]), usage=worker.usage,
        diagnostics={**diagnostics, "local_edit_result": result}, report=WorkerCandidateReport(summary=result["status"]),
        delivery_evidence=worker.delivery_evidence)
