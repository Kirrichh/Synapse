"""Which recorded answers can witness a birth, and how the report explains the ones that cannot.

``witness_exclusions`` is the one rule: a reasoning tool's answer is never a witness (``not_an_action``);
under tool configuration v2 a source whose service, the part before the colon, is one of the agent's own
services is not either (``agent_own_service``); under v3 only a source the operator declared outside is
(otherwise ``not_declared_outside``). The report explains, per candidate a window touched, the answers inside
its held episodes' slow paths that attested the failed operation but could not witness it.
"""
from __future__ import annotations

from types import SimpleNamespace

from synapse.memory_consolidation.court.births import _witness_exclusions
from synapse.memory_consolidation.court.pool import candidate_key
from synapse.memory_consolidation.court.reactions import _excluded_witnesses
from synapse.memory_consolidation.tools.contracts import ToolConfiguration
from synapse.memory_consolidation.tools.semantics import witness_exclusions

V2 = ToolConfiguration(servers={}, tools={}, provenance={}, raw={})  # nothing declared outside: the v2 name rule
V3 = ToolConfiguration(servers={}, tools={}, provenance={}, raw={}, outside=frozenset({"erp:bookings"}))


def test_under_v2_the_service_before_the_colon_decides():
    assert witness_exclusions(V2, "action", "gds:ledger") == []
    assert witness_exclusions(V2, "action", "ledger:gds") == ["agent_own_service"]
    assert witness_exclusions(V2, "reason", "ledger:gds") == ["not_an_action", "agent_own_service"]


def test_under_v3_only_a_declared_outside_source_witnesses():
    assert witness_exclusions(V3, "action", "erp:bookings") == []
    # An own store under an innocent name, and an outside system never declared: neither witnesses.
    assert witness_exclusions(V3, "action", "kv:bookings") == ["not_declared_outside"]
    assert witness_exclusions(V3, "action", "ledger:gds") == ["not_declared_outside"]
    assert witness_exclusions(V3, "reason", "erp:bookings") == ["not_an_action"]


def test_only_answers_inside_the_slow_path_are_explained():
    answers = [{"gw_seq": seq, "tool": "check", "source": "ledger:x", "by": "state_check",
                "reasons": ["agent_own_service"]} for seq in (7, 9)]
    failure = {"resolution": {"by": "state_check", "gw_seq": 7}, "excluded_attestations": answers}

    assert _excluded_witnesses(failure, [{"gw_seq": 7}, {"gw_seq": 8}]) == [{**answers[0], "resolves_effect": True}]
    assert _excluded_witnesses({**failure, "resolution": None}, [{"gw_seq": 9}]) == [
        {**answers[1], "resolves_effect": False}]
    assert _excluded_witnesses(None, [{"gw_seq": 7}]) == []


def test_a_candidate_is_explained_by_its_own_held_episodes_only():
    own, other = [{"tool": "flights", "args": {}}], [{"tool": "other", "args": {}}]

    def reaction(run_id, steps, source):
        return {"run_id": run_id, "event_id": "e1", "context": {"event_type": "refusal"},
                "slow": {"steps": steps, "excluded_witnesses": [
                    {"gw_seq": 1, "tool": "check", "source": source, "by": "state_check",
                     "reasons": ["agent_own_service"], "resolves_effect": False}]}}

    context = SimpleNamespace(report={"witness_exclusions": []}, draft={"reactions": [
        reaction("held", own, "ledger:a"), reaction("held-again", own, "ledger:d"),
        reaction("another-candidate", other, "ledger:b"), reaction("consumed", own, "ledger:c"),
        {"run_id": "no-slow-path", "event_id": "e1", "slow": None}]})
    held = {"episodes": [{"run_id": "held", "event_id": "e1"}, {"run_id": "held-again", "event_id": "e1"},
                         {"run_id": "another-candidate", "event_id": "e1"}]}
    # Two witnesses of one operator: one counts, against the two required.
    assessment = {"independence": {"verdict": "dependent", "witnesses": ["gds:search", "gds:bookings"],
                                   "independent_set": ["gds:bookings"], "required": 2}}

    _witness_exclusions(context, candidate_key("refusal", own), held, assessment)

    explained, = context.report["witness_exclusions"]
    assert [(item["run_id"], item["source"]) for item in explained["excluded"]] == [
        ("held", "ledger:a"), ("held-again", "ledger:d")]
    assert (explained["independence"], explained["counted"], explained["required"], explained["reasons"]) == (
        "dependent", 1, 2, {"agent_own_service": 2})
