"""The runtime side of the memory adapter points (``synapse.memory_points``).

The core records facts here and nothing else: the bound memory session owns
plans, the gateway, learned bodies and the court. This engine holds the
session of one durable run and its working memory — the fixed task plan, the
entered context blocks, the frames of reactions in progress and the ordinal of
external actions — and implements the language points:

* ``task_plan(contract)`` fixes a task contract and its markers (formation);
* ``tool(name, args[, {"retry_of": op}])`` performs one external action through
  the gateway, recorded before its result is used and consumed, never
  repeated, when the run is re-executed;
* a failed action raises the reactive event; a habit may recover it, otherwise
  ``catch (ACTION_FAILED as failure)`` is the event's slow path;
* ``memory_digest()`` reads the digest the session started from;
* ``hypothesis(claim)`` records a testable claim read from a recorded
  observation of this run, ``probe(h)`` performs its declared check through the
  gateway and records the status the subsystem gives it, ``established(h)``
  is true only for a confirmed hypothesis — checked in this run or offered
  fresh by the pinned snapshot for the same source version; an action may
  name the hypotheses it relies on (``{"requires": [...]}``) and is refused
  before any effect while one is not established;
* ``consolidate`` and the end of the session run the court through the session.

A reaction (habit body or slow path) shares the operation scope of the action
that failed, so it can name the operation it repeats.
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Optional

from synapse.memory_points import ACTION_FAILED, ACTION_FAILURE_EVENT, ActionFailed, ActionPorts
from synapse.runtime.replay_engine import ReplayIntegrityError

_MAX_WAIT_SECONDS = 60.0


class MemoryEngine:
    """Memory adapter points of one interpreter; inert without a bound session."""

    def __init__(self, host_getter: Callable[[], Any], live_mode, *, canonical, dream_violation,
                 integrate_violation, runtime_error) -> None:
        self._host_getter = host_getter
        self.live_mode = live_mode
        self._canonical = canonical
        # The interpreter's own runtime error: what a program (and ``main``) sees and may handle.
        self._runtime_error = runtime_error
        self._dream_violation = dream_violation
        self._integrate_violation = integrate_violation
        self.session = None
        self.active_task: Optional[Dict[str, Any]] = None
        self.contexts: List[Dict[str, Any]] = []
        self.frames: List[Dict[str, Any]] = []
        self.ordinal = 0
        self.opening: Optional[Dict[str, Any]] = None
        # Working memory of hypotheses: id -> record and current status; successful observations by request.
        self.hypotheses: Dict[str, Dict[str, Any]] = {}
        self.observed: Dict[bytes, str] = {}

    @property
    def host(self):
        return self._host_getter()

    # -- session ----------------------------------------------------------
    def bind(self, session) -> Dict[str, Any]:
        """Registry-load point: bind a session and register its pinned learned habits."""
        from synapse.habit import HabitRuntimeRecord
        from synapse.habit_triggers import TypedTrigger

        if self.session is not None:
            raise self._runtime_error("a durable run binds one memory session")
        self.session = session
        entries = tuple(session.registry_entries())
        pinned = session.pinned()
        opening = {"type": "memory_session_opened", "boundary": pinned["boundary"], "digest": pinned["digest"],
                   "exam": session.exam,
                   "learned": [{"habit_id": item.habit_id, "trigger_id": item.trigger_id,
                                "context_trust": item.context_trust, "slow_only": item.slow_only}
                               for item in entries]}
        self.opening = self.host.record_history_event(opening)
        for entry in entries:
            self.host.habit_registry.register(HabitRuntimeRecord(
                name=f"learned:{entry.habit_id}", activate_when=[], suppress_when=[],
                energy_cost=float(entry.energy_cost), priority=entry.priority, body=[], layer=2,
                habit_id=entry.habit_id,
                typed_triggers=(TypedTrigger(entry.trigger_id, tuple(entry.event_types), tuple(entry.context),
                                             tuple(entry.when), tuple(entry.not_when)),),
                context_trust={entry.trigger_id: float(entry.context_trust)}, slow_only=entry.slow_only))
        return self.opening

    def finish(self) -> Optional[Dict[str, Any]]:
        """End of session: full consolidation for a palace declared ``consolidate during dream``."""
        h = self.host
        if (self.session is None or not self.session.learns
                or not any(palace.consolidate_during_dream for palace in h.memory_palaces.values())):
            return None  # An exam session never consolidates: its results do not teach later tasks.
        recorded = h.next_history_event("session_consolidated")
        if recorded is None:
            report = self.session.consolidate("full", history=copy.deepcopy(h.execution_history))
            recorded = h.record_history_event({"type": "session_consolidated", "mode": "full", "report": report})
        return copy.deepcopy(recorded["report"])

    def require(self, operation: str):
        h = self.host
        if self.session is None:
            raise self._runtime_error(f"{operation} requires a durable memory session")
        if h.dream_depth > 0:
            raise self._dream_violation(f"dream cannot {operation}; it has no external effects")
        if h.integrate_depth > 0:
            raise self._integrate_violation(f"{operation} is forbidden inside integrate transaction")
        return self.session

    # -- working memory ----------------------------------------------------
    def context_entered(self, label: str, event_id: str) -> None:
        self.contexts.append({"label": str(label), "entered": event_id})

    def context_exited(self) -> None:
        if self.contexts:
            self.contexts.pop()

    def bind_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """The durable event adapter point: subsystem fields from working memory."""
        if self.session is None:
            return event
        working = {"task": copy.deepcopy(self.active_task), "contexts": copy.deepcopy(self.contexts)}
        return {**event, **self.session.bind_event(event, working)}

    def digest(self) -> Any:
        self.require("memory_digest")
        return copy.deepcopy((self.opening or {}).get("digest"))

    def declare_task_plan(self, args: List[Any]) -> Dict[str, Any]:
        """Formation contour: fix a TaskContract and its segment markers before execution."""
        session, h = self.require("task_plan"), self.host
        if len(args) != 1 or not isinstance(args[0], dict):
            raise self._runtime_error("task_plan expects one contract object")
        contract = copy.deepcopy(args[0])
        recorded = h.next_history_event("task_plan_declared")
        if recorded is not None:
            if self._canonical(recorded.get("contract")) != self._canonical(contract):
                raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: task plan differs from its recorded contract")
        else:
            recorded = h.record_history_event({"type": "task_plan_declared", "contract": contract,
                                               "plan": session.declare_task(contract)})
        self.active_task = copy.deepcopy(recorded["plan"])
        return copy.deepcopy(recorded["plan"])

    # -- external actions ----------------------------------------------------
    def invoke_tool(self, args: List[Any], env) -> Dict[str, Any]:
        """``tool(name, args[, {"retry_of": op}])`` through the session gateway."""
        self.require("tool")
        if len(args) not in {2, 3} or not isinstance(args[0], str) or not isinstance(args[1], dict):
            raise self._runtime_error("tool expects a tool name, an argument object and optional options")
        options = args[2] if len(args) == 3 else {}
        if not isinstance(options, dict) or set(options) - {"retry_of", "requires"}:
            raise self._runtime_error("tool options accept only retry_of and requires")
        retry_of = options.get("retry_of")
        if retry_of is not None and (type(retry_of) is not int or retry_of < 1):
            raise self._runtime_error("tool retry_of names a recorded operation number")
        requires = None
        if "requires" in options:
            handles = options["requires"]
            if not isinstance(handles, list) or not handles:
                raise self._runtime_error("tool requires lists the hypotheses the action relies on")
            requires = []
            for item in handles:
                self.established([item])  # Consults the pinned snapshot once, if this run has not decided it.
                entry = self._hypothesis(item)
                requires.append({"hypothesis": entry["record"]["id"], "status": entry["status"]})
        frame = self.frames[-1] if self.frames else None
        action = self.recorded_action(args[0], copy.deepcopy(args[1]), retry_of, frame, requires=requires)
        view = action["outcome"]["view"]
        if view["ok"] or (frame is not None and frame["kind"] == "habit"):
            return copy.deepcopy(view)
        return self._react(action, env)

    def recorded_action(self, tool: str, arguments: Dict[str, Any], retry_of: Optional[int],
                        frame: Optional[Dict[str, Any]], *, requires=None) -> Dict[str, Any]:
        """One external action: consumed from the record, or performed and recorded."""
        h = self.host
        request = {
            "type": "external_action", "tool": tool, "args": arguments, "retry_of": retry_of,
            "ordinal": self.ordinal, "path": "habit" if frame is not None and frame["kind"] == "habit" else "slow",
            "habit_id": frame.get("habit_id") if frame is not None else None,
            "episode": frame["episode"] if frame is not None else None,
            "op_scope": frame["op_scope"] if frame is not None else None}
        if requires is not None:
            request["requires"] = requires  # Present only when the program names them.
        request = self.bind_event(request)
        self.ordinal += 1
        recorded = h.next_history_event("external_action")
        if recorded is not None:
            if self._canonical(recorded.get("request")) != self._canonical(request):
                raise ReplayIntegrityError("REPLAY_INTEGRITY_ERROR: external action differs from its record")
        else:
            outcome = self.session.invoke_action(request)
            recorded = h.record_history_event({"type": "external_action", "request": request, "outcome": outcome})
            if h.durable_checkpoint is not None and h.runtime_mode == self.live_mode:
                h.durable_checkpoint()  # Crash point: persisted after every recorded effect.
        if frame is not None:
            frame["actions"].append(recorded["outcome"]["ref"])
            frame["outcomes"].append(recorded["outcome"]["view"])
        if recorded["outcome"]["view"]["ok"] and recorded["outcome"]["ref"]["evidence"] is not None:
            # A successful observation can be the source a hypothesis is read from.
            self.observed[self._canonical({"tool": tool, "args": arguments})] = recorded["outcome"]["ref"]["evidence"]
        return recorded

    # -- hypotheses -----------------------------------------------------------
    def _hypothesis(self, handle) -> Dict[str, Any]:
        entry = self.hypotheses.get(handle.get("id")) if isinstance(handle, dict) else None
        if entry is None:
            raise self._runtime_error("a hypothesis handle names no hypothesis of this run")
        return entry

    def _recorded_hypothesis_event(self, kind: str, event: Dict[str, Any], compared: Dict[str, Any]) -> Dict[str, Any]:
        h = self.host
        recorded = h.next_history_event(kind)
        if recorded is not None:
            if any(self._canonical(recorded.get(key)) != self._canonical(value) for key, value in compared.items()):
                raise ReplayIntegrityError(f"REPLAY_INTEGRITY_ERROR: {kind} differs from its record")
            return recorded
        return h.record_history_event(self.bind_event({"type": kind, **event}))

    def declare_hypothesis(self, args: List[Any]) -> Dict[str, Any]:
        """``hypothesis(claim)``: a testable claim, provisional, read from a recorded observation."""
        session = self.require("hypothesis")
        if len(args) != 1 or not isinstance(args[0], dict):
            raise self._runtime_error("hypothesis expects one claim object")
        claim = copy.deepcopy(args[0])
        source = claim.get("source") if isinstance(claim.get("source"), dict) else {}
        source_ref = self.observed.get(self._canonical({"tool": source.get("tool"), "args": source.get("args")}))
        try:
            record = session.declare_hypothesis(claim, source_ref)
        except ValueError as exc:
            raise self._runtime_error(str(exc)) from None
        reason = "declared" if source_ref is not None else "source_absent"
        recorded = self._recorded_hypothesis_event("hypothesis_declared", {
            "hypothesis": record, "status": "provisional", "reason": reason, "trace_id": self.host.current_trace_id()},
            {"hypothesis": record})
        self.hypotheses[record["id"]] = {"record": recorded["hypothesis"], "status": "provisional",
                                         "reason": reason, "decided_by": None}
        return {"id": record["id"], "aspect": record["aspect"], "status": "provisional", "reason": reason}

    def probe(self, args: List[Any]) -> Dict[str, Any]:
        """``probe(h)``: the declared check, through the gateway; the subsystem decides the status."""
        session = self.require("probe")
        if len(args) != 1:
            raise self._runtime_error("probe expects one hypothesis")
        entry = self._hypothesis(args[0])
        record, check_ref, view = entry["record"], None, None
        if record["source"]["ref"] is not None:
            frame = self.frames[-1] if self.frames else None
            action = self.recorded_action(record["check"]["tool"], copy.deepcopy(record["check"]["args"]), None, frame)
            view, check_ref = action["outcome"]["view"], action["outcome"]["ref"]
        result = session.resolve_hypothesis(record, view)
        self._recorded_hypothesis_event("hypothesis_probed", {
            "hypothesis": record["id"], "status": result["status"], "reason": result["reason"],
            "check_ref": check_ref, "trace_id": self.host.current_trace_id()},
            {"hypothesis": record["id"], "status": result["status"], "reason": result["reason"]})
        entry.update(status=result["status"], reason=result["reason"], decided_by="probe")
        return {"id": record["id"], "status": result["status"], "reason": result["reason"]}

    def hypothesis_status(self, hypothesis_id: str) -> Optional[str]:
        """The live status of one of this run's hypotheses, for palace admission; ``None`` if unknown."""
        entry = self.hypotheses.get(hypothesis_id) if self.session is not None else None
        return None if entry is None else entry["status"]

    def established(self, args: List[Any]) -> bool:
        """``established(h)``: confirmed in this run, or offered fresh by the pinned snapshot."""
        session = self.require("established")
        if len(args) != 1:
            raise self._runtime_error("established expects one hypothesis")
        entry = self._hypothesis(args[0])
        if entry["decided_by"] is None:
            known = session.known_hypothesis(entry["record"])
            self._recorded_hypothesis_event("hypothesis_reused", {
                "hypothesis": entry["record"]["id"], "status": known["status"], "reason": known["reason"],
                "trace_id": self.host.current_trace_id()},
                {"hypothesis": entry["record"]["id"], "status": known["status"], "reason": known["reason"]})
            if known["status"] is not None:
                entry.update(status=known["status"], reason=known["reason"])
            entry["decided_by"] = "memory"
        return entry["status"] == "confirmed"

    # -- reactions -----------------------------------------------------------
    def _react(self, action: Dict[str, Any], env) -> Dict[str, Any]:
        h = self.host
        outcome, request = action["outcome"], action["request"]
        event = h.record_history_event(self.bind_event({
            "type": ACTION_FAILURE_EVENT, "event_id": h.next_event_id(), "fields": copy.deepcopy(outcome["event_fields"]),
            "context_labels": [entry["label"] for entry in self.contexts], "action_ref": outcome["ref"],
            "action_scope": request["op_scope"],
            "failed_action": {"tool": request["tool"], "op": outcome["view"]["op"], "args": copy.deepcopy(request["args"])},
            "trace_id": h.current_trace_id()}))
        h.emit_runtime_event(event, env)
        reaction = h.runtime.habit.react(event, lambda habit, trigger: self._reactive_body(habit, event))
        if reaction["kind"] == "activated" and reaction["result"]["recovered"]:
            return copy.deepcopy(reaction["result"]["final"])
        raise ActionFailed({"tool": request["tool"], "event_id": event["event_id"], "op": outcome["view"]["op"],
                            "op_scope": request["op_scope"], "reaction": reaction["kind"],
                            "action": copy.deepcopy(outcome["view"])})

    def _reactive_body(self, habit, event: Dict[str, Any]) -> Dict[str, Any]:
        h = self.host
        frame = {"kind": "habit", "habit_id": habit.habit_id, "episode": f"{event['event_id']}|habit",
                 "op_scope": event["action_scope"], "actions": [], "outcomes": []}
        self.frames.append(frame)
        detail = None
        try:
            if habit.layer == 2:
                ports = ActionPorts(
                    invoke=lambda tool, arguments, retry_of=None: copy.deepcopy(
                        self.recorded_action(tool, copy.deepcopy(dict(arguments)), retry_of, frame)["outcome"]["view"]),
                    wait=self._wait)
                body = self.session.run_learned_body(habit.habit_id, copy.deepcopy(event), ports)
                outcome, detail = body["outcome"], body.get("detail")
            else:
                try:
                    h.execute_block(habit.body, h.make_environment(h.global_env))
                    outcome = self._declared_outcome(frame["outcomes"])
                except (self._runtime_error, RuntimeError, ValueError, TypeError, KeyError):
                    outcome = "failure"
        finally:
            self.frames.pop()
        failed = event["failed_action"]
        final = next((item for item in reversed(frame["outcomes"])
                      if item["tool"] == failed["tool"] and item["op"] == failed["op"]), None)
        return {"outcome": outcome, "recovered": bool(final is not None and final["ok"]),
                "action_refs": list(frame["actions"]), "final": final, "detail": copy.deepcopy(detail)}

    @staticmethod
    def _declared_outcome(outcomes: List[Dict[str, Any]]) -> str:
        """Local truth of a declared body: the last action it performed."""
        if not outcomes:
            return "unclear"
        last = outcomes[-1]
        if last["ok"]:
            return "success"
        return "uncertain" if last["effect"] == "unknown" else "failure"

    def _wait(self, seconds: float) -> None:
        """A body's wait passes real time once; a re-executed run does not wait again."""
        if self.host.runtime_mode == self.live_mode and seconds > 0:
            import time as _time
            _time.sleep(min(float(seconds), _MAX_WAIT_SECONDS))

    def recover(self, node, env) -> Any:
        """``try { … } catch (ACTION_FAILED as failure) { slow path }``: one reactive event's slow path."""
        h = self.host
        if node.catch_error != ACTION_FAILED:
            raise self._runtime_error(f"catch({node.catch_error}) is available only in compiled CVM programs")
        try:
            return h.execute_block(node.try_body, h.make_environment(env))
        except ActionFailed as failed:
            failure = failed.failure
            slow_env = h.make_environment(env)
            if node.catch_binding:
                slow_env.define(node.catch_binding, copy.deepcopy(failure))
            frame = {"kind": "slow", "episode": f"{failure['event_id']}|slow", "op_scope": failure["op_scope"],
                     "actions": [], "outcomes": []}
            self.frames.append(frame)
            completed = False
            try:
                result = h.execute_block(node.catch_body, slow_env)
                completed = True
                return result
            finally:
                self.frames.pop()
                h.record_history_event(self.bind_event({
                    "type": "slow_path_used", "event_id": h.next_event_id(),
                    "trigger_event_id": failure["event_id"], "action_refs": list(frame["actions"]),
                    "completed": completed, "trace_id": h.current_trace_id()}))

    # -- consolidation ---------------------------------------------------------
    def consolidate_summary(self, palace_name: str) -> Dict[str, Any]:
        """Summary consolidation (spec part 2 §2.1): the court, never the palace backend."""
        h = self.host
        self.require("consolidate")
        if not self.session.learns:
            raise self._runtime_error("an exam session does not consolidate memory")
        recorded = h.next_history_event("memory_consolidated")
        if recorded is None:
            report = self.session.consolidate("summary", history=copy.deepcopy(h.execution_history))
            recorded = h.record_history_event({"type": "memory_consolidated", "palace": palace_name, "mode": "summary",
                                               "report": report, "trace_id": h.current_trace_id()})
        return recorded
