"""Tool configuration v3: how outside sources are declared, and how a declaration judges recorded witnesses.

Every provenance node states ``"outside"`` as true or false. A source of one of the agent's own services
cannot be declared outside; names beginning with ``#`` are reserved for the operator nodes v3 derives. A
witness an accumulating candidate recorded in an earlier window counts only while the configuration in
force still admits its source.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from acceptance.memory._witnesses import INSIDE, OUTSIDE
from synapse.memory_consolidation.court.pool import independence_of
from synapse.memory_consolidation.tools.contracts import (
    TOOL_CONFIGURATION_V2, TOOL_CONFIGURATION_V3, ToolConfigurationViolation, parse_tool_configuration,
)


def _configuration(schema: str, provenance: dict, source: str = "erp:bookings") -> dict:
    return {"schema_version": schema, "servers": [{"id": "tools", "argv": ["tool-server"]}],
            "tools": [{"name": "check", "server": "tools", "descriptor_sha256": "0" * 64,
                       "input_schema": {"type": "object"}, "output_schema": {"type": "object"}, "source": source}],
            "provenance": provenance}


@pytest.mark.parametrize("source", ["ledger:erp", "memory:notes", "palace:agent"])
def test_an_agent_own_service_cannot_be_declared_outside(source):
    with pytest.raises(ToolConfigurationViolation, match="the agent's own services"):
        parse_tool_configuration(_configuration(TOOL_CONFIGURATION_V3, {source: OUTSIDE}, source))
    # Declared as what it is, the agent's own record, it is accepted and never witnesses.
    assert parse_tool_configuration(_configuration(TOOL_CONFIGURATION_V3, {source: INSIDE}, source)).outside == set()


@pytest.mark.parametrize(("provenance", "refusal"), [
    ({"erp:bookings": {"ancestors": []}}, "unknown shape"),  # outside left unsaid
    ({"erp:bookings": {"ancestors": [], "outside": "yes"}}, "true or false"),
    ({"erp:bookings": OUTSIDE, "#operator:erp": INSIDE}, "reserved for operator nodes"),
    ({"erp-bookings": OUTSIDE}, "service:account"),
])
def test_v3_states_every_node_explicitly_and_reserves_operator_nodes(provenance, refusal):
    with pytest.raises(ToolConfigurationViolation, match=refusal):
        parse_tool_configuration(_configuration(TOOL_CONFIGURATION_V3, provenance))


def test_a_recorded_witness_counts_only_while_the_configuration_in_force_admits_its_source():
    claim = {"tool": "search", "op": "op-1", "effect": "applied"}
    success = [{"witnesses": [{"source": "flights:acct1", "gw_seq": 2, "by": "retry_of_same_operation",
                               "claim": claim},
                              {"source": "gds:bookings", "gw_seq": 3, "by": "state_check", "claim": claim}]}]

    def judged(schema, provenance):
        configuration = SimpleNamespace(tools=parse_tool_configuration(
            _configuration(schema, provenance, "gds:bookings")))
        verdict = independence_of(configuration, success, 2)
        return verdict["verdict"], verdict["witnesses"]

    assert judged(TOOL_CONFIGURATION_V2, {"flights:acct1": {"ancestors": []}, "gds:bookings": {"ancestors": []}}) == (
        "independent", ["flights:acct1", "gds:bookings"])
    assert judged(TOOL_CONFIGURATION_V3, {"flights:acct1": OUTSIDE, "gds:bookings": OUTSIDE}) == (
        "independent", ["flights:acct1", "gds:bookings"])
    # Narrowed: the booking system is no longer declared outside, and its recorded answer stops counting.
    assert judged(TOOL_CONFIGURATION_V3, {"flights:acct1": OUTSIDE, "gds:bookings": INSIDE}) == (
        "not_established", ["flights:acct1"])
