"""The same confirmation from an outside service witnesses, and the procedure is born.

The control of ``test_agent_own_witness_acceptance.py``: in the travel scenario (``_witnesses``) the booking
system confirms every recovered search under ``gds:ledger``, an outside service whose account happens to be
named ``ledger``. Its answers are independent outside witnesses, so three recoveries teach the procedure and
the report has nothing to explain.
"""
from __future__ import annotations

from acceptance.memory import _witnesses as witnesses
from synapse.memory_consolidation.tools.contracts import TOOL_CONFIGURATION_V2


def test_the_same_confirmation_from_an_outside_service_teaches_the_procedure(tmp_path):
    world = witnesses.world(tmp_path, {"flights:acct1": {"ancestors": []}, "quota:ops": {"ancestors": []},
                                       "gds:ledger": {"ancestors": []}},
                            schema=TOOL_CONFIGURATION_V2, booking="gds:ledger")

    report = witnesses.learn(world)

    birth, = report["births"]
    assert birth["criteria"]["independence"] == "independent"
    assert report["witness_exclusions"] == []
