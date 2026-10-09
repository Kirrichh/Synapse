"""Sequences of consequential calls against an independent model of the provider (review R2, package 5).

Hypothesis drives the gateway through generated sequences: new bookings,
declared retries (some with changed arguments), identical new requests, and
answers of every kind — booked, sold out (documented: nothing booked), an
undocumented refusal after booking, a lost answer after or before booking, and
a process crash after or before booking followed by recovery. The provider is
modelled here, independently of the product: it books, deduplicates a keyed
request within its retention and remembers what it answered.

The reference model states the rules the review asks for, not the gateway's
code: an undocumented refusal or a lost answer leaves the effect unknown; a
repeat of an unknown effect is admitted only when the provider deduplicates it
by the operation's key; a retry that changes the arguments, or repeats a
success, is refused before any effect; an identical new request while an
operation's effect is unknown is that operation's repeat. After every step the
gateway's decision and effect class must equal the model's, and the provider
must never have booked twice for one operation.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from synapse.memory_consolidation.tools.contracts import parse_tool_configuration
from synapse.memory_consolidation.tools.gateway import Gateway

KEY = "request_key"
MODES = ("booked", "sold_out", "undocumented", "lost_after", "lost_before")
CRASHES = ("crash_after", "crash_before")
FLIGHTS = ("F1", "F2")


class Crash(BaseException):
    """The process dies while the call is in flight."""


class Provider:
    """The booking service as the environment runs it: it books, and deduplicates keyed requests."""

    def __init__(self) -> None:
        self.mode = "booked"
        self.bookings: list[str | None] = []  # the key (or None) each real booking was made with
        self.kept: dict[str, tuple[dict, tuple]] = {}
        self.calls = 0

    def call(self, contract, arguments):
        self.calls += 1
        key = arguments.get(KEY)
        essential = {name: value for name, value in arguments.items() if name != KEY}
        if key is not None and key in self.kept:
            args, answer = self.kept[key]
            return ("ok", {"ok": False, "err": "IDEMPOTENCY_KEY_REUSED"}) if args != essential else answer
        mode = self.mode
        booked = mode in {"booked", "undocumented", "lost_after", "crash_after"}
        if booked:
            self.bookings.append(key)
        answer = {"booked": ("ok", {"ok": True, "booked": True}), "sold_out": ("ok", {"ok": False, "err": "SOLD_OUT"}),
                  "undocumented": ("ok", {"ok": False, "err": "NEW_ERROR"})}.get(mode, ("ok", {"ok": True}))
        if key is not None and mode != "lost_before" and mode != "crash_before":
            self.kept[key] = (essential, answer)  # The provider answered; losing it in transit changes nothing.
        if mode in CRASHES:
            raise Crash()
        if mode in {"lost_after", "lost_before"}:
            return "lost", None
        return answer


def _configuration():
    contract = {"effect_on_err": {"SOLD_OUT": "none"}}
    tools = [{"name": name, "server": "airline", "descriptor_sha256": "0" * 64, "input_schema": {"type": "object"},
              "output_schema": {"type": "object"}, "source": f"airline:{name}",
              "contract": {**contract, **extra}} for name, extra in (
        ("book", {"idempotency_key": {"field": KEY, "retention_s": 3600}}), ("book_plain", {}))]
    return parse_tool_configuration({"schema_version": "synapse.memory.tool-configuration/v2",
                                     "servers": [{"id": "airline", "argv": ["airline"]}], "tools": tools,
                                     "provenance": {"airline:book": {"ancestors": []},
                                                    "airline:book_plain": {"ancestors": []}}})


EFFECT = {"booked": "applied", "sold_out": "none", "undocumented": "unknown", "lost_after": "unknown",
          "lost_before": "unknown"}
#: What the provider keeps under a key, by the answer it gave: the effect class of that answer when repeated.
KEPT = {"booked": "applied", "sold_out": "none", "undocumented": "unknown", "lost_after": "applied",
        "crash_after": "applied"}


class GatewaySequences(RuleBasedStateMachine):
    """The gateway against the reference model, one generated sequence at a time."""

    def __init__(self) -> None:
        super().__init__()
        self.root = Path(tempfile.mkdtemp(prefix="gateway-seq-"))
        self.provider = Provider()
        self.gateway = Gateway(self.root, _configuration(), executor="acceptance", transport=self.provider)
        self.ordinal = 0
        self.ops: list[dict] = []   # the model's operations: tool, flight, effect, resolved
        self.crashed: dict | None = None

    def teardown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    # -- the model's rules --------------------------------------------------------
    @staticmethod
    def _admits(op) -> bool:
        if op["resolved"]:
            return False
        if op["effect"] == "none":
            return True
        return op["effect"] == "unknown" and op["tool"] == "book"

    def _hidden(self, tool, flight):
        return next((op for op in reversed(self.ops) if op["tool"] == tool and op["flight"] == flight
                     and not op["resolved"] and op["effect"] == "unknown"), None)

    def _invoke(self, tool, flight, retry_of=None):
        self.ordinal += 1
        request = {"run_id": "seq", "ordinal": self.ordinal, "tool": tool, "args": {"flight": flight},
                   "op_scope": "scope", "episode": "scope", "path": "slow", "habit_id": None}
        if retry_of is not None:
            request["retry_of"] = retry_of
        return request, self.gateway.invoke(request)

    def _settle(self, op, mode, outcome, calls_before):
        view = outcome["view"]
        assert self.provider.calls == calls_before + 1, "an admitted attempt reaches the provider once"
        kept = op["tool"] == "book" and op["kept"] is not None
        assert view["effect"] == (op["kept"] if kept else EFFECT[mode])
        if op["tool"] == "book" and not kept and mode in KEPT:
            op["kept"] = KEPT[mode]
        op["effect"] = view["effect"]
        op["resolved"] = op["resolved"] or view["op_result"] == "ok"

    @staticmethod
    def _crashes(op, mode) -> bool:
        # A kept answer is given again at once: the provider does nothing that could be interrupted.
        return mode in CRASHES and not (op["tool"] == "book" and op["kept"] is not None)

    # -- the steps ----------------------------------------------------------------------
    @precondition(lambda self: self.crashed is None)
    @rule(tool=st.sampled_from(("book", "book_plain")), flight=st.sampled_from(FLIGHTS),
          mode=st.sampled_from(MODES + CRASHES))
    def new_request(self, tool, flight, mode):
        hidden = self._hidden(tool, flight)
        self.provider.mode = mode
        calls = self.provider.calls
        if hidden is not None and not self._admits(hidden):
            _, outcome = self._invoke(tool, flight)
            assert outcome["view"]["transport"] == "rejected" and self.provider.calls == calls
            return
        op = hidden or {"tool": tool, "flight": flight, "effect": "unknown", "resolved": False, "kept": None}
        if hidden is None:
            self.ops.append(op)
        if self._crashes(op, mode):
            try:
                self._invoke(tool, flight)
            except Crash:
                if op["tool"] == "book" and mode in KEPT:
                    op["kept"] = KEPT[mode]
                self.crashed = {"op": op, "ordinal": self.ordinal, "tool": tool, "flight": flight}
                return
            raise AssertionError("the provider crashed, the call did not")
        _, outcome = self._invoke(tool, flight)
        self._settle(op, mode, outcome, calls)

    @precondition(lambda self: self.crashed is None and self.ops)
    @rule(data=st.data(), mode=st.sampled_from(MODES), changed=st.booleans())
    def declared_retry(self, data, mode, changed):
        index = data.draw(st.integers(0, len(self.ops) - 1))
        op = self.ops[index]
        flight = next(item for item in FLIGHTS if item != op["flight"]) if changed else op["flight"]
        self.provider.mode = mode
        calls = self.provider.calls
        _, outcome = self._invoke(op["tool"], flight, retry_of=index + 1)
        if changed or not self._admits(op):
            assert outcome["view"]["transport"] == "rejected" and self.provider.calls == calls
            return
        self._settle(op, mode, outcome, calls)

    @precondition(lambda self: self.crashed is not None)
    @rule(mode=st.sampled_from(MODES))
    def recover(self, mode):
        crashed, self.crashed = self.crashed, None
        op = crashed["op"]
        self.provider.mode = mode
        calls = self.provider.calls
        request = {"run_id": "seq", "ordinal": crashed["ordinal"], "tool": crashed["tool"],
                   "args": {"flight": crashed["flight"]}, "op_scope": "scope", "episode": "scope", "path": "slow",
                   "habit_id": None}
        outcome = self.gateway.invoke(request)
        if op["tool"] == "book_plain":
            # Never repeated after a crash: the effect stays unknown.
            assert outcome["view"]["transport"] == "lost" and self.provider.calls == calls
            op["effect"] = "unknown"
            return
        self._settle(op, mode, outcome, calls)

    # -- what must always hold -------------------------------------------------------------
    @invariant()
    def never_booked_twice_for_one_operation(self):
        keyed = [key for key in self.provider.bookings if key is not None]
        assert len(keyed) == len(set(keyed)), "one keyed operation was booked twice"

    @invariant()
    def unknown_effects_are_never_repeated_without_a_key(self):
        records = self.gateway.records()
        started = [item["body"] for item in records
                   if item["kind"] == "STARTED" and item["body"]["tool"] == "book_plain"]
        attempts: dict[int, int] = {}
        for body in started:
            attempts[body["op_seq"]] = attempts.get(body["op_seq"], 0) + 1
        for op_seq, count in attempts.items():
            if count > 1:
                results = [item["body"] for item in records if item["kind"] == "RESULT"
                           and any(s["seq"] == item["body"]["started_seq"] and s["body"]["op_seq"] == op_seq
                                   for s in records if s["kind"] == "STARTED")]
                assert all(body["effect"] == "none" for body in results[:-1]), "a plain repeat of an unknown effect"


GatewaySequences.TestCase.settings = settings(max_examples=60, stateful_step_count=25, deadline=None,
                                              suppress_health_check=[HealthCheck.too_slow])
test_gateway_sequences = GatewaySequences.TestCase
