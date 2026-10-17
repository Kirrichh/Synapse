"""A v2 memory owner adopts tool configuration v3 by reassessment, and the declaration judges what accumulates.

Two of the three recoveries a birth needs are recorded under v2, where the booking system ``gds:bookings``
counted as an outside witness. The owner then adopts v3 with ``synapse memory reassess``. The third recovery
is judged by the declaration: declared outside, the procedure is born; declared not outside, the witnesses the
candidate recorded under v2 stop counting too, and the report names the window's answer with
``not_declared_outside``.
"""
from __future__ import annotations

import json

import pytest

from acceptance.memory import _witnesses as witnesses
from acceptance.memory._witnesses import INSIDE, OUTSIDE
from synapse.memory_consolidation.tools.contracts import TOOL_CONFIGURATION_V2, TOOL_CONFIGURATION_V3


@pytest.mark.parametrize(("declared", "born"), [(OUTSIDE, True), (INSIDE, False)])
def test_a_v2_owner_adopts_v3_by_reassessment_and_its_declaration_judges_the_next_birth(tmp_path, declared, born):
    world = witnesses.world(tmp_path, {"flights:acct1": {"ancestors": []}, "quota:ops": {"ancestors": []},
                                       "gds:bookings": {"ancestors": []}},
                            schema=TOOL_CONFIGURATION_V2, booking="gds:bookings")
    witnesses.learn(world, routes=("BUS", "YVR"))
    configuration = json.loads(world.configuration_path.read_text())
    configuration["tools"].update(schema_version=TOOL_CONFIGURATION_V3, provenance={
        "flights:acct1": OUTSIDE, "quota:ops": INSIDE, "gds:bookings": declared})
    adopted = tmp_path / "memory.v3.json"
    adopted.write_text(json.dumps(configuration, sort_keys=True))
    code, _, stderr = world.memory("reassess", configuration=adopted)
    assert code == 0, stderr
    world.configuration_path = adopted

    report = witnesses.learn(world, routes=("MSQ",), first=2)

    assert bool(report["births"]) is born
    assert report["witness_exclusions"] == ([] if born else [
        {**report["witness_exclusions"][0], "reasons": {"not_declared_outside": 1}}])
