"""Generated streams against an independent model of the effectiveness rules (review R6, package 5).

Hypothesis draws a stream of verified outcomes for one learned habit, the state
it starts in and two different cuts of the stream into consolidation windows.
The court decides window after window (followed by as many empty
consolidations as the stream is long, so what a window carried over is judged).
The reference model below reads the same stream fire by fire from the review's
rules alone — promote on sufficient experience, demote on confirmed errors
among the last fires, never on too little experience, archive nothing on a
count of windows — and never imports the court. For every stream and every
cut the court must reach exactly the model's transitions, in the model's order,
each at the very fire the model reaches it.

Disuse is measured in consolidations by design, so it is set out of reach here;
trust is held fixed so the automaton's own rules are what is compared.
"""
from __future__ import annotations

from hypothesis import example, given, settings
from hypothesis import strategies as st

from acceptance.memory import _court_data as data

T1_FIRES, T1_TASKS, T3_FIRES, T3_SIGNAL, T4_FIRES, T4_SIGNAL = 5, 2, 5, 0.65, 5, 0.7
LEARNING_RATE = 0.2
PARAMETERS = {"min_evidence": 100_000, "t1_trust": 0.7, "t1_fires": T1_FIRES, "t1_tasks": T1_TASKS,
              "t3_fires": T3_FIRES, "t3_signal": T3_SIGNAL, "t4_fires": T4_FIRES, "t4_signal": T4_SIGNAL,
              "m_idle": 100_000, "n_t9": 100_000, "k_extinct": 100_000}


def reference(stream, state, batch=None):
    """The transitions the review's rules reach, reading one fire at a time.

    With ``batch``, trust starts at 0.5 and every ``batch`` signals move it by the learning rate towards their
    mean while the habit is born; promotion also needs trust 0.7. Without it trust is held at 0.9. Each transition
    names the fire it happened at."""
    moves, since_birth, in_state, tail = [], 0, [], []
    trust, waiting = (0.9, None) if batch is None else (0.5, [])
    for index, success in enumerate(stream):
        if state == "dormant":
            break  # An archived habit is not loaded: it never fires again.
        since_birth += 1
        value = 1.0 if success else 0.0
        in_state.append(value)
        tail = (tail + [value])[-T3_FIRES:]
        if waiting is not None:
            waiting.append(value)
            if len(waiting) == batch:
                if state == "born":
                    trust += LEARNING_RATE * (sum(waiting) / batch - trust)
                waiting = []
        if len(tail) >= T3_FIRES and sum(tail) / len(tail) < T3_SIGNAL:
            rule, state = {"born": ("T2", "probation"), "active": ("T3", "probation"),
                           "probation": ("T5", "dormant")}[state]
            moves.append((rule, state, "confirmed_errors", index))
            in_state, tail = [], []
        elif state == "born" and since_birth >= T1_FIRES and since_birth >= T1_TASKS and trust >= 0.7:
            moves.append(("T1", "active", "sufficient_experience", index))
            state, in_state, tail = "active", [], []
        elif state == "probation" and len(in_state) >= T4_FIRES and sum(in_state) / len(in_state) >= T4_SIGNAL:
            moves.append(("T4", "active", "sufficient_experience", index))
            state, in_state, tail = "active", [], []
    return moves


def _index(at):
    """The position in the stream of the fire a transition names (the court's fires are numbered in order)."""
    return None if at is None else int(at["event_id"].rpartition("-")[2])


def court(stream, state, cut, parameters=PARAMETERS, trust=0.9):
    config, court_state, ids = data.world(parameters, trust=trust, state_name=state)
    habit_id = ids["q"]
    trigger_id = court_state["habits"][habit_id]["trigger_id"]
    fires = [data.fire(habit_id, trigger_id, index, success) for index, success in enumerate(stream)]
    moves, start = [], 0
    for size in [*cut, *[0] * len(stream)]:
        if court_state["habits"][habit_id]["state"] not in {"born", "active", "probation"}:
            break  # Archived: not loaded, so its later fires never happen.
        court_state, decision = data.window(config, court_state, fires[start:start + size])
        moves.extend((item["rule"], item["to"], item["cause"], _index(item.get("at")))
                     for item in decision["sections"]["transitions"])
        start += size
    return moves


@st.composite
def _cuts(draw, length):
    cut, left = [], length
    while left:
        size = draw(st.integers(1, left))
        cut.append(size)
        left -= size
    return cut


@st.composite
def _cases(draw):
    # Mostly successes, as a working habit's stream is: promotions, errors after them and recoveries all occur.
    stream = draw(st.lists(st.sampled_from((True, True, True, False)), max_size=24))
    state = draw(st.sampled_from(("born", "active", "probation")))
    return stream, state, draw(_cuts(len(stream))), draw(_cuts(len(stream)))


@settings(max_examples=300, deadline=None)
@given(_cases())
@example(([False] * 7 + [True] * 4, "born", [1] * 9 + [2], [1, 1, 1, 1, 7]))
@example(([True] * 5 + [False] * 5, "born", [10], [1] * 10))
def test_every_cut_of_a_stream_reaches_the_reference_transitions(case):
    stream, state, first, second = case
    expected = reference(stream, state)
    assert court(stream, state, first) == expected
    assert court(stream, state, second) == expected


LIVE_TRUST = {**PARAMETERS, "min_evidence": 2, "lr": {"born": LEARNING_RATE}}


@settings(max_examples=300, deadline=None)
@given(_cases())
@example(([True] * 8, "born", [8], [1] * 8))
def test_promotion_waits_for_the_trust_each_fire_had_reached(case):
    stream, state, first, second = case
    expected = reference(stream, state, batch=2)
    assert court(stream, state, first, LIVE_TRUST, 0.5) == expected
    assert court(stream, state, second, LIVE_TRUST, 0.5) == expected
