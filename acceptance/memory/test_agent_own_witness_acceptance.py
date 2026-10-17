"""A confirmation from a source named as the agent's own service never witnesses, and the report says why.

Under tool configuration v2 the court reads a source's service, the part before the colon, and treats the
``AGENT_OWN_SERVICES`` names (``ledger``, ``journal``, ``log``, ...) as the agent's own record of itself. In
the travel scenario (``_witnesses``) the booking system confirms every recovered search under ``ledger:gds``,
so the procedure is never born. The report's ``witness_exclusions`` section counts the independent witnesses
against the required number and names the answer set apart with its reason. The section is report-only: no
applied section and nothing memory keeps carries it. ``test_outside_service_witness_acceptance.py`` runs the
same scenario under ``gds:ledger``.
"""
from __future__ import annotations

from acceptance.memory import _witnesses as witnesses
from synapse.memory_consolidation.tools.contracts import TOOL_CONFIGURATION_V2

EXPLANATION = {"witness_exclusions", "excluded_witnesses", "excluded_attestations", "resolves_effect"}


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*map(_keys, value.values()))
    if isinstance(value, list):
        return set().union(*map(_keys, value))
    return set()


def test_a_confirmation_from_a_service_named_as_the_agents_own_is_no_witness_and_the_report_says_why(tmp_path):
    world = witnesses.world(tmp_path, {"flights:acct1": {"ancestors": []}, "quota:ops": {"ancestors": []},
                                       "ledger:gds": {"ancestors": []}},
                            schema=TOOL_CONFIGURATION_V2, booking="ledger:gds")

    report = witnesses.learn(world)

    assert report["schema_version"] == "synapse.memory.consolidation-report/v2"
    assert report["births"] == []
    candidate, = report["pool_updates"]
    assert candidate["independence"]["witnesses"] == ["flights:acct1"]
    assert candidate["reasons"] == ["independence_not_established"]
    explained, = report["witness_exclusions"]
    assert {key: explained[key] for key in ("candidate_key", "independence", "counted", "required", "reasons")} == {
        "candidate_key": candidate["candidate_key"], "independence": "not_established", "counted": 1, "required": 2,
        "reasons": {"agent_own_service": 1}}
    excluded, = explained["excluded"]
    # The repeated search settled the refused one; the booking check only confirmed it, and cannot witness it.
    assert {key: excluded[key] for key in ("source", "by", "reasons", "resolves_effect")} == {
        "source": "ledger:gds", "by": "state_check", "reasons": ["agent_own_service"], "resolves_effect": False}
    # The explanation is the report's alone: no applied section and nothing memory keeps carries it.
    assert not EXPLANATION & set().union(*(_keys(item["apply"]) for item in world.reports()))
    assert not EXPLANATION & _keys(world.owner().state())
