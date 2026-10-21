"""A dream is a sandbox: no statement inside it may change a memory palace.

``memory.write``, ``memory.forget`` and ``send`` were refused inside a dream,
but an ``imprint``, a ``consolidate`` with promotions, a ``plan weave`` (which
records its episode in the palace) and a palace declared again (which replaces
the held one) went through and outlived the dream. Each is refused now and the
palace is exactly what it was; an insight reaches memory through ``integrate``.
"""
import pytest

from synapse import Interpreter, compile_to_ast
from synapse.interpreter import DreamIsolationViolation

SETUP = '''
agent Lead { model "mock" }
memory palace "Incidents" { rooms { episodic semantic procedural } backend sqlite bind palace }
let self = Lead
imprint into palace.episodic {
    content "pool exhausted on db"
    confidence 0.9
    source "rca"
    affective_tag { valence -0.8 arousal 0.9 dominance -0.3 }
    bind seed
}
'''

LEAKS = {
    "imprint": 'imprint into palace.episodic { content "smuggled" confidence 0.9 source "dream" bind z }',
    "consolidation": ('consolidate palace { rooms ["episodic"] affective_routing { '
                      'when valence < -0.3 and arousal > 0.6 { promote_to semantic tag "danger" } } bind c }'),
    "plan weave": ('intention cascade "R" { mission "m" task "t" bind plan } '
                   'plan weave with [self] under "Ops" { intention plan timeout 60 bind ex }'),
    "palace declared again": 'memory palace "Incidents" { rooms { episodic } backend sqlite bind fresh }',
}


def records(interp):
    return {name: palace.content()["records"] for name, palace in interp.memory_palaces.items()}


def run(source):
    interp = Interpreter()
    interp.interpret(compile_to_ast(source))
    return interp


@pytest.mark.parametrize("name", list(LEAKS))
def test_a_dream_cannot_change_a_memory_palace(name):
    interp = run(SETUP)
    before = records(interp)
    with pytest.raises(DreamIsolationViolation, match="use integrate"):
        interp.interpret(compile_to_ast('let r = dream { scenario "what if" ' + LEAKS[name] + ' return 1 }\n'))
    assert records(interp) == before


def test_the_same_statements_still_run_outside_a_dream():
    # The control: each statement changes the palace when it is not inside a dream.
    for name, body in LEAKS.items():
        interp = run(SETUP)
        before = records(interp)
        interp.interpret(compile_to_ast(body + "\n"))
        assert records(interp) != before, name


def test_a_dream_reads_memory_and_its_insight_is_imprinted_through_integrate():
    interp = run(SETUP + '''
let insight = dream {
    scenario "what if the pool is exhausted again"
    recall from palace.episodic { query "pool exhausted" threshold 0.1 limit 3 bind seen }
    return "seen " + str(len(seen))
} integrate {
    imprint into palace.semantic { content dream_result confidence 0.8 source "dream" bind kept }
}
''')
    semantic = [r["content"] for r in records(interp)["Incidents"] if r["room"] == "semantic"]
    assert semantic == ["seen 1"]
