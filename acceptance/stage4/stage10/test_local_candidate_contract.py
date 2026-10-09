"""Synapse forms the coding candidate; an agent's text is only a proposal."""
import hashlib

import pytest

from acceptance.stage4.stage10.test_local_edit_contract import information, proposal, task
from synapse.canonical_values import canonical_json_bytes
from synapse.experiments.gold.stage10.local_candidate import interpret_agent_proposal
from synapse.experiments.gold.stage10.worker_transport import (
    WORKER_INVOCATION_SCHEMA_V2, WorkerCandidateReport, WorkerCandidateResult, WorkerCandidateStatus,
    WorkerCandidateUsage, WorkerDeliveryEvidence, WorkerDeliveryStatus, WorkerInvocation, WorkerTokenStatus,
)
from synapse.worker.local_edits import (
    LOCAL_EDIT_COMMAND, LOCAL_EDIT_PROFILE_V1, LOCAL_EDIT_PROFILE_V5, LOCAL_EDIT_PROFILE_V6, PROTOCOL_CAPABILITIES,
    propose_local_edits,
)


def invocation():
    public, private = task(), information()
    return WorkerInvocation("inv_" + "1" * 64, "attempt-1", "ctx_" + "2" * 64, public.text,
        hashlib.sha256(public.canonical_bytes).hexdigest(), len(public.canonical_bytes), "3" * 64,
        ("src",), ("repository.edit",), schema_version=WORKER_INVOCATION_SCHEMA_V2, information_text=private.text,
        information_sha256=private.sha256, information_byte_length=len(private.canonical_bytes))


def agent(call, *, diagnostics, diff_text=None, status=WorkerCandidateStatus.NO_PATCH):
    return WorkerCandidateResult(status=status, diff_text=diff_text, touched_files=("src/calc.py",) if diff_text else (),
        usage=WorkerCandidateUsage(WorkerTokenStatus.PROVIDER_REPORTED, 10, 8, 0, 18, False),
        diagnostics=diagnostics, report=WorkerCandidateReport(summary="LOCAL_EDIT_PROPOSAL"),
        delivery_evidence=WorkerDeliveryEvidence(call.invocation_id, call.context_id, call.payload_sha256,
            call.payload_byte_length, call.envelope_sha256, WorkerDeliveryStatus.PROCESS_STARTED, "any-agent/v1",
            WORKER_INVOCATION_SCHEMA_V2, call.information_sha256, call.information_byte_length))


def command(*replacements):
    return LOCAL_EDIT_COMMAND + canonical_json_bytes(proposal(*replacements)).decode()


@pytest.mark.parametrize("profile", [LOCAL_EDIT_PROFILE_V1, LOCAL_EDIT_PROFILE_V5, LOCAL_EDIT_PROFILE_V6])
def test_synapse_interprets_the_raw_agent_proposal_it_delivered(profile):
    call = invocation()
    text = command(("a - b", "a + b"))
    candidate = interpret_agent_proposal(call, agent(call, diagnostics={"local_edit_proposal": text}), profile=profile)
    expected = propose_local_edits(task=task(), information=information(), proposal=proposal(("a - b", "a + b")),
                                   profile=profile)
    assert candidate.status is WorkerCandidateStatus.PROPOSED_PATCH
    assert candidate.diagnostics["local_edit_result"] == expected and candidate.diff_text == expected["diff_text"]
    assert candidate.diagnostics["local_edit_proposal"] == text and candidate.usage.total_tokens == 18
    assert expected["profile"] == profile


@pytest.mark.parametrize("diagnostics,diff_text,reason", [
    ({}, None, "local_edit_proposal_absent_or_with_effects"),
    ({"local_edit_proposal": command(("a - b", "a + b"))}, "--- a/src/calc.py\n", "local_edit_proposal_absent_or_with_effects"),
    ({"local_edit_proposal": "synapse-local-edit {not json"}, None, "local_edit_refused"),
    ({"local_edit_proposal": "rm -rf src"}, None, "local_edit_refused"),
])
def test_absent_effectful_or_malformed_proposals_are_explicit_refusals(diagnostics, diff_text, reason):
    call = invocation()
    candidate = interpret_agent_proposal(call, agent(call, diagnostics=diagnostics, diff_text=diff_text),
                                         profile=LOCAL_EDIT_PROFILE_V6)
    assert candidate.status is WorkerCandidateStatus.ERROR and candidate.report.failure_reason == reason
    assert candidate.diff_text is None and "local_edit_result" not in candidate.diagnostics


def test_an_agent_cannot_report_an_interpreted_result():
    call = invocation()
    with pytest.raises(ValueError, match="interpreted local result"):
        interpret_agent_proposal(call, agent(call, diagnostics={"local_edit_result": {"selected_index": 0}}),
                                 profile=LOCAL_EDIT_PROFILE_V6)


def test_the_neutral_protocol_keeps_v5_semantics_and_run_decisions():
    assert PROTOCOL_CAPABILITIES[LOCAL_EDIT_PROFILE_V6] == PROTOCOL_CAPABILITIES[LOCAL_EDIT_PROFILE_V5]
    assert PROTOCOL_CAPABILITIES[LOCAL_EDIT_PROFILE_V1] == frozenset()
    v5, v6 = (propose_local_edits(task=task(), information=information(), proposal=proposal(("a - b", "a + b")),
                                  profile=profile) for profile in (LOCAL_EDIT_PROFILE_V5, LOCAL_EDIT_PROFILE_V6))
    assert {**v5, "schema_version": None, "profile": None} == {**v6, "schema_version": None, "profile": None}
    assert v6["schema_version"] == "synapse.worker.local-edit-result/v6"
