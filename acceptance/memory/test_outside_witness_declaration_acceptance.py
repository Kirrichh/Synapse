"""Tool configuration v3: a source the operator declares outside witnesses, whatever its name.

v2 excludes witnesses by service name (``AGENT_OWN_SERVICES``), a denylist; v3 is an allowlist: an answer
witnesses only when its source is a provenance node declared ``"outside": true``. In the travel scenario
(``_witnesses``) the booking system ``erp:bookings`` is declared outside, so its confirmations are the second
independent witness and three recoveries teach the procedure.
"""
from __future__ import annotations

from acceptance.memory import _witnesses as witnesses
from acceptance.memory._witnesses import INSIDE, OUTSIDE
from synapse.memory_consolidation.tools.contracts import TOOL_CONFIGURATION_V3


def test_a_source_declared_outside_witnesses(tmp_path):
    world = witnesses.world(tmp_path, {"flights:acct1": OUTSIDE, "quota:ops": INSIDE, "erp:bookings": OUTSIDE},
                            schema=TOOL_CONFIGURATION_V3, booking="erp:bookings")

    report = witnesses.learn(world)

    birth, = report["births"]
    assert birth["criteria"]["independence"] == "independent"
    assert report["witness_exclusions"] == []
