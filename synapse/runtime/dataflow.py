"""Event-driven execution of a ``parallel`` graph (refinement §16).

A graph is a set of named nodes, the events they raise and the one node it
commits::

    parallel offer limit 4 {
      node quote = tool("billing_quote", {"plan": plan})      # a call node: one observation
      node stock = tool("inventory", {"sku": sku})
      node price on "repriced" = tool("price_feed", {"plan": plan})   # read again on the event
      node offer = {"price": price.payload.price, "stock": stock.payload.count}   # a pure node
      signal "repriced" when quote.payload.changed == true
      commit offer => tool("place_order", {"price": offer.price})
    }

A node depends on the nodes its expression names. It runs as soon as every
input it reads is settled — not in flight, not outdated, and computed from
settled inputs itself — and waits for nothing else (a process of a Kahn
network: deterministic nodes, so the committed result does not depend on the
order in which independent inputs arrived); settled inputs are mutually
consistent, so a node never combines a new value with one computed from an
older version (glitch freedom, as in FrTime and REScala). Call nodes run
concurrently: their observations go through the gateway on worker threads,
bounded by ``limit``; every other evaluation stays on the one interpreter
thread. A call node may only observe (its tool's contract says
``observation``; the gateway refuses anything else before any effect): effects
happen at the commit, once, by the single writer.

Every node value has a version, and every computation records the versions of
the inputs it read. A computation whose inputs move on while it runs — a newer
version arrives, or its event is raised again — is superseded at once by a
computation on the current versions; when it answers it is recorded **stale**
and never used (an optimistic read set checked at the end, Kung and
Robinson). Signals are evaluated once per combination of their inputs' settled
versions, so a repeated observation raises nothing twice; an event that never
came is no negative fact. The commit waits only for its own inputs, and for
the nodes whose signals could still move them: when those are settled it
records the versions it committed and runs the effect on the committed value.
A slow node it does not need never delays it; after the commit, what is still
in flight is **cancelled** — its answer is recorded and accounted once, never
used. Live, answers are integrated in the order the gateway's journal holds
them, so a reproduction answered from that journal integrates them in the same
order.

Replay follows the record, as a durable workflow replays its event history:
each call node's answer is the recorded one, in the recorded order, and its
ordinal names the graph instance, the node and the attempt, so a run that
crashed mid-graph repeats no observation it already made (the gateway returns
the recorded answer). The history explains the choice: every step with the
versions it read, every stale and cancelled computation, every signal and the
versions the commit rested on.
"""
from __future__ import annotations

import concurrent.futures
import queue
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .. import ast as synapse_ast
from .replay_engine import ReplayIntegrityError

#: Calls a pure node, a signal or a call node's arguments may make: deterministic and effect-free.
PURE_CALLS = frozenset({"len", "range", "type", "str", "int", "float", "list", "dict", "abs", "sum", "max", "min",
                        "sorted", "reversed", "enumerate", "zip", "any", "all"})
DEFAULT_LIMIT = 8
MAX_ATTEMPTS = 32


class GraphViolation(ValueError):
    """A ``parallel`` graph is outside its declared shape."""


def _names(expr: Any) -> set:
    """The variables an expression reads."""
    found = set()
    if isinstance(expr, synapse_ast.Variable):
        found.add(expr.name)
    elif isinstance(expr, synapse_ast.Node):
        for key, value in vars(expr).items():
            if key not in {"line", "column"}:
                found |= _names(value)
    elif isinstance(expr, (list, tuple)):
        for item in expr:
            found |= _names(item)
    elif isinstance(expr, dict):
        for item in expr.values():
            found |= _names(item)
    return found


def _calls(expr: Any) -> list:
    found = []
    if isinstance(expr, synapse_ast.CallExpr):
        found.append(expr)
    if isinstance(expr, synapse_ast.Node):
        for key, value in vars(expr).items():
            if key not in {"line", "column"}:
                found += _calls(value)
    elif isinstance(expr, (list, tuple)):
        for item in expr:
            found += _calls(item)
    return found


def _pure(expr: Any, where: str) -> None:
    for call in _calls(expr):
        if not isinstance(call.callee, synapse_ast.Variable) or call.callee.name not in PURE_CALLS:
            raise GraphViolation(f"{where} is pure: effects belong to call nodes and to the commit")
    for node in _walk(expr):
        if isinstance(node, (synapse_ast.LLMCall, synapse_ast.PromptExpr)):
            raise GraphViolation(f"{where} is pure: effects belong to call nodes and to the commit")


def _walk(expr: Any):
    if isinstance(expr, synapse_ast.Node):
        yield expr
        for key, value in vars(expr).items():
            if key not in {"line", "column"}:
                yield from _walk(value)
    elif isinstance(expr, (list, tuple)):
        for item in expr:
            yield from _walk(item)


def _is_call(expr: Any) -> bool:
    return (isinstance(expr, synapse_ast.CallExpr) and isinstance(expr.callee, synapse_ast.Variable)
            and expr.callee.name == "tool")


@dataclass
class Graph:
    """The analysed shape of one ``parallel`` statement."""

    name: str
    limit: int
    order: List[str]
    nodes: Dict[str, Any]
    deps: Dict[str, List[str]]
    calls: Dict[str, bool]
    signals: List[Dict[str, Any]]
    commit: str
    effect: Any
    upstream: set = field(default_factory=set)


def analyse(stmt: synapse_ast.ParallelStmt) -> Graph:
    """Validate a graph: unique names, known inputs, no cycle of data, pure nodes pure, a commit it has."""
    names = [node.name for node in stmt.nodes]
    if not names or len(set(names)) != len(names):
        raise GraphViolation(f"parallel {stmt.name}: nodes have unique names")
    limit = DEFAULT_LIMIT if stmt.limit is None else stmt.limit
    if type(limit) is not int or not 1 <= limit <= 64:
        raise GraphViolation(f"parallel {stmt.name}: limit is between 1 and 64")
    known = set(names)
    nodes, deps, calls = {}, {}, {}
    for node in stmt.nodes:
        nodes[node.name] = node
        calls[node.name] = _is_call(node.expr)
        if calls[node.name]:
            if len(node.expr.args) != 2:
                raise GraphViolation(f"node {node.name}: a call node is tool(name, arguments)")
            for argument in node.expr.args:
                _pure(argument, f"node {node.name}'s arguments")
        else:
            _pure(node.expr, f"node {node.name}")
        deps[node.name] = sorted(_names(node.expr) & known - {node.name})
        if node.name in _names(node.expr):
            raise GraphViolation(f"node {node.name} reads itself")
    signals = []
    for signal in stmt.signals:
        _pure(signal.condition, f"signal {signal.event!r}")
        signals.append({"event": signal.event, "condition": signal.condition,
                        "deps": sorted(_names(signal.condition) & known)})
    subscribed = {node.event for node in stmt.nodes if node.event is not None}
    for event in subscribed:
        if not any(item["event"] == event for item in signals):
            raise GraphViolation(f"no signal raises event {event!r}")
    if stmt.commit not in known:
        raise GraphViolation(f"parallel {stmt.name} commits an unknown node {stmt.commit!r}")
    if stmt.effect is not None and _names(stmt.effect) & known - {stmt.commit}:
        raise GraphViolation(f"parallel {stmt.name}: the effect acts on the committed value only")
    order = _topological(names, deps)
    graph = Graph(stmt.name, limit, order, nodes, deps, calls, signals, stmt.commit, stmt.effect)
    graph.upstream = _upstream(graph)
    return graph


def _topological(names, deps) -> List[str]:
    order, done, visiting = [], set(), set()

    def visit(name):
        if name in done:
            return
        if name in visiting:
            raise GraphViolation(f"node {name} is part of a cycle of data")
        visiting.add(name)
        for dep in deps[name]:
            visit(dep)
        visiting.discard(name)
        done.add(name)
        order.append(name)

    for name in names:
        visit(name)
    return order


def _upstream(graph: Graph) -> set:
    """Every node the commit's value can depend on: its inputs, and the sources of events that refresh them."""
    found, frontier = set(), [graph.commit]
    while frontier:
        name = frontier.pop()
        if name in found:
            continue
        found.add(name)
        frontier += graph.deps[name]
        event = graph.nodes[name].event
        if event is not None:
            for signal in graph.signals:
                if signal["event"] == event:
                    frontier += signal["deps"]
    return found


class Executor:
    """One execution of one graph instance by the interpreter ``host``."""

    def __init__(self, host, stmt: synapse_ast.ParallelStmt, env, instance: str) -> None:
        self.host, self.graph, self.env, self.instance = host, analyse(stmt), env, instance
        names = self.graph.order
        self.version = {name: 0 for name in names}
        self.values: Dict[str, Dict[int, Any]] = {name: {} for name in names}
        self.reads: Dict[str, Dict[str, int]] = {name: {} for name in names}
        self.attempts = {name: 0 for name in names}
        self.seen_events = {name: 0 for name in names}
        self.events: Dict[str, int] = {}
        self.signalled: Dict[int, tuple] = {}
        # The current computation of each call node in flight, and superseded ones still in flight (by ordinal).
        self.inflight: Dict[str, Dict[str, Any]] = {}
        self.superseded: Dict[str, Dict[str, Any]] = {}
        self.completions: "queue.Queue" = queue.Queue()
        self.arrived: Dict[str, Dict[str, Any]] = {}
        self.pool: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self.began = time.monotonic()
        self.steps: List[Dict[str, Any]] = []
        self.signals: List[Dict[str, Any]] = []

    # -- recording ----------------------------------------------------------
    def _recorded(self, kind: str, event: Dict[str, Any], compared: Dict[str, Any]) -> Dict[str, Any]:
        return self.host.runtime.memory.recorded_event(kind, event, compared)

    def _ms(self) -> float:
        return round((time.monotonic() - self.began) * 1000.0, 3)

    # -- state --------------------------------------------------------------
    def _current(self, name: str) -> Dict[str, int]:
        return {dep: self.version[dep] for dep in self.graph.deps[name]}

    def _event_count(self, name: str) -> int:
        event = self.graph.nodes[name].event
        return 0 if event is None else self.events.get(event, 0)

    def _outdated(self, name: str) -> bool:
        """Its value — or its computation in flight — does not reflect its current inputs and events."""
        entry = self.inflight.get(name)
        if entry is not None:
            return entry["reads"] != self._current(name) or self._event_count(name) > entry["events"]
        return (self.version[name] == 0 or self.reads[name] != self._current(name)
                or self._event_count(name) > self.seen_events[name])

    def _settled(self, name: str, memo: Dict[str, bool]) -> bool:
        """Its value is final for now: not in flight, not outdated, and so is everything it was computed from."""
        if name not in memo:
            memo[name] = (name not in self.inflight and not self._outdated(name)
                          and all(self._settled(dep, memo) for dep in self.graph.deps[name]))
        return memo[name]

    def _needs(self, name: str, memo: Dict[str, bool]) -> bool:
        """Outdated, and every input it reads is settled: its inputs are mutually consistent versions."""
        return self._outdated(name) and all(self._settled(dep, memo) for dep in self.graph.deps[name])

    def _scope(self, reads: Dict[str, int]):
        scope = self.host.make_environment(self.env)
        for dep, version in reads.items():
            scope.define(dep, self.values[dep][version])
        return scope

    # -- steps --------------------------------------------------------------
    def _step(self, name, attempt, status, reads, value=None, started=None) -> None:
        event = {"instance": self.instance, "node": name, "attempt": attempt, "status": status, "reads": reads,
                 "version": self.version[name] + (1 if status == "integrated" else 0),
                 "value": value if not self.graph.calls[name] else None,
                 "started_ms": started, "finished_ms": self._ms()}
        recorded = self._recorded("dataflow_step", event, {key: event[key] for key in (
            "instance", "node", "attempt", "status", "reads", "version", "value")})
        self.steps.append(recorded)

    def _integrate(self, name, value, reads, events) -> None:
        self.version[name] += 1
        self.values[name][self.version[name]] = value
        self.reads[name] = dict(reads)
        self.seen_events[name] = events

    def _raise_signals(self, memo: Dict[str, bool]) -> bool:
        """Evaluate each signal once per combination of its inputs' settled versions."""
        raised = False
        for index, signal in enumerate(self.graph.signals):
            if any(self.version[dep] == 0 or not self._settled(dep, memo) for dep in signal["deps"]):
                continue  # Never over versions still moving; an event that did not come is no negative fact.
            key = tuple(self.version[dep] for dep in signal["deps"])
            if self.signalled.get(index) == key:
                continue  # The same versions raise nothing twice.
            self.signalled[index] = key
            holds = self.host.evaluate(signal["condition"], self._scope(dict(zip(signal["deps"], key))))
            if holds is True:
                self.events[signal["event"]] = self.events.get(signal["event"], 0) + 1
                event = {"instance": self.instance, "event": signal["event"],
                         "count": self.events[signal["event"]], "by": dict(zip(signal["deps"], key))}
                self.signals.append(self._recorded("dataflow_signal", event, {k: event[k] for k in (
                    "instance", "event", "count", "by")}))
                raised = True
        return raised

    def _dispatch(self, name: str) -> None:
        self.attempts[name] += 1
        if self.attempts[name] > MAX_ATTEMPTS:
            raise GraphViolation(f"node {name} was computed more than {MAX_ATTEMPTS} times")
        attempt, reads, events = self.attempts[name], self._current(name), self._event_count(name)
        scope = self._scope(reads)
        node = self.graph.nodes[name]
        if not self.graph.calls[name]:
            started = self._ms()
            value = self.host.evaluate(node.expr, scope)
            self._step(name, attempt, "integrated", reads, value, started)
            self._integrate(name, value, reads, events)
            return
        tool = self.host.evaluate(node.expr.args[0], scope)
        arguments = self.host.evaluate(node.expr.args[1], scope)
        if not isinstance(tool, str) or not isinstance(arguments, dict):
            raise GraphViolation(f"node {name}: a call node is tool(name, arguments)")
        previous = self.inflight.pop(name, None)
        if previous is not None:
            # Its inputs moved while it ran: it is superseded now, recorded as stale when it answers, never used.
            self.superseded[previous["request"]["ordinal"]] = previous
        request = self.host.runtime.memory.parallel_request(tool, arguments, f"df:{self.instance}:{name}:{attempt}",
                                                            f"df:{self.instance}:{name}")
        self.inflight[name] = {"node": name, "attempt": attempt, "reads": reads, "events": events,
                               "request": request, "started": self._ms(), "future": None}
        self._submit(self.inflight[name])

    def _open(self) -> Dict[str, Dict[str, Any]]:
        """Every call in flight, current or superseded, by ordinal."""
        return {**{entry["request"]["ordinal"]: entry for entry in self.inflight.values()}, **self.superseded}

    def _submit(self, entry: Dict[str, Any]) -> None:
        """Start a call on a worker, unless the run is following its record."""
        if entry["future"] is not None or self._replaying():
            return
        if self.pool is None:
            self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=self.graph.limit,
                                                              thread_name_prefix=f"synapse-parallel-{self.instance}")
        future = self.pool.submit(self.host.runtime.memory.perform_parallel, entry["request"])
        entry["future"] = future
        ordinal = entry["request"]["ordinal"]
        future.add_done_callback(lambda done, ordinal=ordinal: self.completions.put((ordinal, done)))

    def _replaying(self) -> bool:
        """Following a record: the next answer is the recorded one; past its end the run is live again."""
        return self.host.peek_history_event() is not None

    def _complete(self, ordinal: str, outcome: Optional[Dict[str, Any]], *, cancelled: bool = False) -> None:
        superseded = self.superseded.pop(ordinal, None)
        entry = superseded if superseded is not None else self.inflight.pop(
            next(node for node, item in self.inflight.items() if item["request"]["ordinal"] == ordinal))
        recorded = self.host.runtime.memory.record_parallel(entry["request"], outcome)
        name = entry["node"]
        stale = (superseded is not None or entry["reads"] != self._current(name)
                 or self._event_count(name) > entry["events"])
        status = "stale" if stale else "cancelled" if cancelled else "integrated"
        self._step(name, entry["attempt"], status, entry["reads"], None, entry["started"])
        if status == "integrated":
            self._integrate(name, recorded["outcome"]["view"], entry["reads"], entry["events"])

    def _next_completion(self, *, cancelled: bool = False) -> None:
        """Integrate the next answered call: the recorded one when replaying, the first the journal holds when live."""
        open_calls = self._open()
        if self._replaying():
            upcoming = self.host.peek_history_event()
            ordinal = ((upcoming or {}).get("request") or {}).get("ordinal")
            if upcoming.get("type") != "external_action" or ordinal not in open_calls:
                raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: the record does not continue this graph")
            self._complete(ordinal, None, cancelled=cancelled)
            return
        for entry in open_calls.values():
            self._submit(entry)  # A run that followed its record until now starts what it had in flight.
        while True:
            # Answers are integrated in the order the gateway's journal holds them — the order they arrived —
            # so a reproduction answered from that journal integrates them exactly as this run did.
            while True:
                try:
                    ordinal, done = self.completions.get(block=not self.arrived)
                except queue.Empty:
                    break
                self.arrived[ordinal] = done.result()
            first = min(self.arrived, key=lambda item: self.arrived[item]["ref"]["gw_seq"])
            position = self.arrived[first]["ref"]["gw_seq"]
            waiting = [entry["request"] for ordinal, entry in open_calls.items() if ordinal not in self.arrived]
            answered = self.host.runtime.memory.parallel_answered(waiting) if waiting else {}
            if all(seq > position for seq in answered.values()):
                break
            # An answer the journal holds before this one is still on its way to this thread: wait for it.
            ordinal, done = self.completions.get()
            self.arrived[ordinal] = done.result()
        self._complete(first, self.arrived.pop(first), cancelled=cancelled)

    def _schedule(self) -> None:
        """Run every ready pure node and start every ready call within the limit, until nothing changes."""
        progressed = True
        while progressed:
            progressed = False
            memo: Dict[str, bool] = {}
            if self._raise_signals(memo):
                memo = {}
            for name in self.graph.order:
                if not self._needs(name, memo):
                    continue
                if self.graph.calls[name] and len(self.inflight) + len(self.superseded) >= self.graph.limit:
                    continue  # Bounded: a ready call waits for a free place, never for an unrelated result.
                self._dispatch(name)
                progressed = True
                break  # Readiness is re-derived after every change.

    def _committable(self) -> bool:
        """The committed node and everything that can still move it — its inputs, and the sources of the
        events that refresh them — are settled (their signals are raised by the scheduling before this)."""
        memo: Dict[str, bool] = {}
        return all(self._settled(name, memo) for name in self.graph.upstream)

    # -- the run -------------------------------------------------------------
    def run(self) -> Dict[str, Any]:
        graph = self._recorded("dataflow_started", {
            "instance": self.instance, "graph": self.graph.name, "limit": self.graph.limit,
            "nodes": [{"node": name, "deps": self.graph.deps[name], "call": self.graph.calls[name],
                       "on": self.graph.nodes[name].event} for name in self.graph.order],
            "commit": self.graph.commit}, {"instance": self.instance, "graph": self.graph.name})
        try:
            while True:
                self._schedule()
                if self._committable():
                    break
                if not self._open():
                    waiting = sorted(name for name in self.graph.upstream if self.version[name] == 0)
                    raise GraphViolation(f"parallel {self.graph.name} cannot commit: {', '.join(waiting)} "
                                         f"never had its inputs")
                self._next_completion()
            commit = self.graph.commit
            reads = {name: self.version[name] for name in sorted(self.graph.upstream)}
            value = self.values[commit][self.version[commit]]
            committed = self._recorded("dataflow_commit", {
                "instance": self.instance, "node": commit, "version": self.version[commit], "reads": reads,
                "value": value, "at_ms": self._ms()},
                {"instance": self.instance, "node": commit, "version": self.version[commit], "reads": reads,
                 "value": value})
            effect = None
            try:
                if self.graph.effect is not None:
                    effect = self.host.evaluate(self.graph.effect, self._scope({commit: self.version[commit]}))
            finally:
                while self._open():
                    self._next_completion(cancelled=True)  # Accounted once, never used.
        finally:
            if self.pool is not None:
                self.pool.shutdown(wait=True)
        return {"graph": graph["graph"], "instance": self.instance, "value": value, "version": committed["version"],
                "reads": committed["reads"], "effect": effect,
                "steps": [{key: step[key] for key in ("node", "attempt", "status", "reads", "version", "started_ms",
                                                      "finished_ms")} for step in self.steps],
                "signals": [{key: item[key] for key in ("event", "count", "by")} for item in self.signals],
                "committed_ms": committed["at_ms"],
                "stale": sum(1 for step in self.steps if step["status"] == "stale"),
                "cancelled": sum(1 for step in self.steps if step["status"] == "cancelled")}
