"""Generated lifecycles of one memory through the canonical launch (review §9.2).

Hypothesis drives one memory through sequences of real sessions (the scenario
of ``_lifecycle.py``): bookings whose answer arrives, is lost after or before
the booking, is a documented refusal, or arrives late; reservations checked by
the record of the very request; restarts that rest on a content hypothesis read
from a card and checked by the runbook (or by a mirror that can never confirm
it); updates of the card; empty windows; forgetting the case that holds a
check; re-entering a completed session; and crashes injected before anything is
sent, after the booking before its answer, after every result before the
consolidation, and between the committed decision and its snapshot boundary,
each followed by recovery.

A small reference model — written from the rules, not from the court — says
what the environment must show after every step: how many seats each flight
holds, which reservations exist, how often the runbook was asked and how many
restarts happened. After every step the invariants hold:

* no flight is ever booked twice, and every attempt for one flight carries one
  key (a repeat never changes it);
* an unknown effect is never credited as a success: a reservation is settled
  only by the record of its own request;
* a confirmation of another claim (the mirror's check) grants nothing, and a
  claim read from a new revision of the card never inherits the old
  confirmation — it is checked again;
* re-entering a completed session changes nothing, so a consolidation never
  credits the same evidence twice;
* empty windows never degrade the stream: a confirmed, unchanged claim is still
  reused after any number of them;
* the memory state is always exactly the fold of the court's reports.
"""
from __future__ import annotations

import os
import shutil
import signal
import tempfile
import time
from pathlib import Path

from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from acceptance.memory import _lifecycle as lifecycle
from synapse.memory_consolidation.court.projection import fold

#: What a flight of each kind holds once its session ended: the lost-before and sold-out ones hold nothing
#: (the dead tool server loses the repeat in the same process; the sold-out answer is kept for its key).
SEATS = {"OK": 1, "LOST": 1, "GONE": 0, "SOLD": 0}


def _await(condition, seconds=300):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "the session never reached its crash point"
        time.sleep(0.2)


class Model:
    """The reference: what the environment must show, from the rules alone."""

    def __init__(self) -> None:
        self.seats: dict[str, int] = {}
        self.reservations: set[str] = set()
        self.revision = 1
        self.confirmed: int | None = None   # The card revision the court holds the runbook's confirmation for.
        self.forgotten = False              # That confirmation's check was forgotten and not yet revoked.
        self.runbook = 0
        self.restarts = 0

    def consolidated(self) -> None:
        """Any consolidation after a forget revokes the confirmation that rested on it."""
        if self.forgotten:
            self.forgotten, self.confirmed = False, None

    def restart(self, checker: str) -> None:
        if checker == "runbook" and not (self.confirmed == self.revision and self.forgotten):
            if self.confirmed != self.revision:
                self.runbook += 1  # Nothing to reuse for this revision: checked again.
                self.confirmed = self.revision
            self.restarts += 1
        # The mirror descends from the card: its check never confirms, nothing restarts. A reused confirmation
        # whose check was forgotten is refused before the effect.
        self.consolidated()


class Lifecycles(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.root = Path(tempfile.mkdtemp(prefix="memory-lifecycle-"))
        self.world = lifecycle.world(self.root)
        self.model = Model()
        self.sessions = 0
        self.completed: list[str] = []

    def teardown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _run_id(self, kind: str) -> str:
        self.sessions += 1
        return f"{kind}-{self.sessions:03d}"

    def _flight(self, kind: str) -> str:
        return f"{kind}-{self.sessions}"

    def _signal(self, present: bool) -> None:
        path = self.world.world_path.parent / "gate.signal"
        if present:
            path.touch()
        elif path.exists():
            path.unlink()

    def _resume(self, run_id: str) -> None:
        code, payload, stderr = self.world.resume(run_id)
        assert code == 0 and payload["status"] == "COMPLETED", (payload, stderr)
        self.model.consolidated()
        self.completed.append(run_id)

    # -- sessions ---------------------------------------------------------------
    @rule(kind=st.sampled_from(sorted(SEATS)))
    def book(self, kind):
        run_id = self._run_id("book")
        flight = self._flight(kind)
        self.world.run(lifecycle.PROGRAM, run_id, lifecycle.inputs(run_id, "book", flight=flight))
        self.model.seats[flight] = SEATS[kind]
        self.model.consolidated()
        self.completed.append(run_id)

    @rule()
    def reserve(self):
        run_id = self._run_id("reserve")
        flight = self._flight("R")
        self.world.run(lifecycle.PROGRAM, run_id, lifecycle.inputs(run_id, "reserve", flight=flight))
        self.model.reservations.add(flight)
        self.model.consolidated()
        failure, = self.world.failures(run_id)
        # Settled only by the record that echoes this request's key.
        assert failure["settled"] and failure["effect"] == "applied"
        assert [item["by"] for item in failure["attestations"]] == ["state_check"]
        self.completed.append(run_id)

    @rule(checker=st.sampled_from(("runbook", "runbook", "kb_mirror")))
    def restart(self, checker):
        run_id = self._run_id("restart")
        self.world.run(lifecycle.PROGRAM, run_id, lifecycle.inputs(run_id, "restart", checker=checker))
        self.model.restart(checker)
        self.completed.append(run_id)

    @rule()
    def update_source(self):
        self.model.revision += 1
        lifecycle.publish(self.world, self.model.revision)

    @rule()
    def idle(self):
        run_id = self._run_id("idle")
        self.world.run(lifecycle.PROGRAM, run_id, lifecycle.inputs(run_id, "idle"))
        self.model.consolidated()
        self.completed.append(run_id)

    @precondition(lambda self: self.model.confirmed == self.model.revision and not self.model.forgotten)
    @rule()
    def forget_the_check(self):
        hypotheses = self.world.owner().state()["hypotheses"]
        newest = max((entry for entry in hypotheses.values()
                      if entry["status"] == "confirmed" and entry["record"]["check"]["tool"] == "runbook"),
                     key=lambda entry: entry["window"])
        basis = newest["basis"]
        assert basis is not None, "a recorded confirmation names the case that holds its check"
        code, payload, stderr = self.world.memory("forget", "--quantum", basis, "--reason", "data subject request",
                                                  "--operator", "acceptance.operator")
        assert code == 0 and payload["status"] == "RECORDED", (payload, stderr)
        self.model.forgotten = True

    @precondition(lambda self: self.completed)
    @rule(data=st.data())
    def reenter(self, data):
        run_id = data.draw(st.sampled_from(self.completed))
        environment, journal = self.world.world(), self.world.journal()
        code, _, _ = self.world.resume(run_id)
        assert self.world.world() == environment and self.world.journal() == journal, code

    # -- crashes and recovery ----------------------------------------------------------
    @rule()
    def crash_before_sending(self):
        run_id = self._run_id("gated")
        flight = self._flight("OK")
        self._signal(False)
        process = self.world.start(lifecycle.PROGRAM, run_id, lifecycle.inputs(run_id, "book", flight=flight,
                                                                               pause=True))
        _await(lambda: {"run": run_id, "step": "wait"} in self.world.calls("gate"))
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(60)
        assert not any(item["flight"] == flight for item in lifecycle.effects(self.world, "booked"))
        self._signal(True)
        self._resume(run_id)
        self._signal(False)
        self.model.seats[flight] = 1

    @rule()
    def crash_after_the_booking(self):
        run_id = self._run_id("slow")
        flight = self._flight("SLOW")
        process = self.world.start(lifecycle.PROGRAM, run_id, lifecycle.inputs(run_id, "book", flight=flight))
        _await(lambda: any(item["args"]["flight"] == flight for item in self.world.world()["effects"]))
        os.killpg(process.pid, signal.SIGKILL)  # After the booking, before its answer.
        process.wait(60)
        self._resume(run_id)
        self.model.seats[flight] = 1

    @rule(point=st.sampled_from(("consolidation", "boundary")))
    def crash_in_the_court(self, point):
        run_id = self._run_id(point)
        flight = self._flight("OK")
        code = lifecycle.crash(self.world, run_id, lifecycle.inputs(run_id, "book", flight=flight), point)
        assert code == 9
        self._resume(run_id)
        self.model.seats[flight] = 1

    # -- what must always hold ------------------------------------------------------------
    @invariant()
    def seats_are_the_models(self):
        booked = {}
        for item in lifecycle.effects(self.world, "booked"):
            booked[item["flight"]] = booked.get(item["flight"], 0) + 1
        assert {flight: count for flight, count in booked.items()} == {
            flight: count for flight, count in self.model.seats.items() if count}

    @invariant()
    def one_key_per_flight(self):
        keys: dict[str, set] = {}
        for item in self.world.world()["calls"]:
            if item["tool"] == "book":
                keys.setdefault(item["args"]["flight"], set()).add(item["args"].get(lifecycle.KEY))
        assert all(len(found) == 1 and None not in found for found in keys.values()), keys

    @invariant()
    def reservations_are_the_models(self):
        assert sorted(item["flight"] for item in lifecycle.effects(self.world, "reserved")) == sorted(
            self.model.reservations)

    @invariant()
    def checks_and_restarts_are_the_models(self):
        assert len(self.world.calls("runbook")) == self.model.runbook
        assert len(lifecycle.effects(self.world, "restarted")) == self.model.restarts

    @precondition(lambda self: self.sessions)  # The journal exists from the first session on.
    @invariant()
    def the_state_is_the_fold_of_the_reports(self):
        owner = self.world.owner()
        assert owner.state() == fold(item["report"] for item in owner.applied() if item["report"] is not None)


Lifecycles.TestCase.settings = settings(max_examples=3, stateful_step_count=9, deadline=None,
                                        suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
test_memory_lifecycles = Lifecycles.TestCase
