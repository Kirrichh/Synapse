"""Memory sessions of durable runs: the subsystem side of the core's adapter points.

A session serves one durable run of a memory owner. It fixes task plans
(formation), binds subsystem fields to events, loads learned habits only from
the complete snapshot boundary it pinned when the run started (a resumed run
keeps its original cut), sends every external action through the owner's
gateway, executes frozen learned bodies through the recorded action path, and
runs the court for the run.

An exam session (refinement §17) reads a fixed snapshot of the same memory and
never consolidates: mode ``A`` loads no accumulated experience, ``B`` loads
what Gold admits now, ``C`` loads the same habits as ``B`` with every learned
trigger slow-only, so the fast path stays closed.

A replay session serves a verified re-execution (stage 1b): the same pinned
entries as recorded, recorded answers only, and ``ReplayHorizon`` instead of
any live effect.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from synapse.memory_points import ActionPorts, LearnedHabitEntry, ReplayHorizon, TypedCondition

from .court.dependencies import forgotten_dependents
from .formation import bind_event, plan_task
from .hypotheses import check_basis, declare, resolve, reuse, verification
from .knowledge import search as knowledge_search
from .knowledge.statements import declare as declare_statement, instant
from .learning.behavior import execute, rivals
from .learning.composition import (joins_of, merge_joins, planning_contingency, ranked, recorded_contingency,
                                   stack_of)
from .learning.triggers import matches, render_template, typed_context
from .records import KINDS, digest


def opening_of(history) -> Mapping[str, Any] | None:
    for event in history or ():
        if isinstance(event, Mapping) and event.get("type") == "memory_session_opened":
            return event
    return None


EXAM_MODES = ("A", "B", "C")


def _entry(item: Mapping[str, Any], trust: float | None = None, *, slow_only: bool = False) -> LearnedHabitEntry:
    trigger = item["trigger"]
    return LearnedHabitEntry(
        habit_id=item["habit_id"], trigger_id=trigger["id"], event_types=tuple(trigger["event_types"]),
        context=() if trigger["context"] == "any" else tuple(trigger["context"]),
        when=tuple(TypedCondition(**condition) for condition in trigger["when"]),
        not_when=tuple(TypedCondition(**condition) for condition in trigger["not_when"]),
        priority=item["priority"], context_trust=item["context_trust"] if trust is None else trust,
        energy_cost=item["energy_cost"], slow_only=slow_only)


def rivalry(entries: tuple[LearnedHabitEntry, ...], boundary_habits: Mapping[str, Mapping[str, Any]],
            parameters: Mapping[str, Any]) -> tuple[LearnedHabitEntry, ...]:
    """Who gives way to whom among the loaded rivals when both apply to one event (review R5).

    A rival that lost a verified comparison yields to its winner (the boundary's
    ``yields_to``); otherwise an established trust gap makes the junior yield;
    otherwise the conflict is unresolved and neither acts. The runtime never
    compares outcomes itself: it holds an unresolved pair back before any
    external effect, including rivals whose triggers overlap only partly.
    """
    loaded = {entry.habit_id: entry for entry in entries}
    yields = {habit_id: set() for habit_id in loaded}
    unresolved = {habit_id: set() for habit_id in loaded}
    ordered = sorted(loaded)
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            a, b = boundary_habits[left], boundary_habits[right]
            if not rivals(a["habit"], b["habit"], parameters["action_same"]):
                continue
            if right in a.get("yields_to", ()) or left in b.get("yields_to", ()):
                winner = right if right in a.get("yields_to", ()) else left
                yields[left if winner == right else right].add(winner)
            elif abs(loaded[left].context_trust - loaded[right].context_trust) >= parameters["conflict_gap"]:
                senior, junior = sorted((left, right), key=lambda item: (-loaded[item].context_trust, item))
                yields[junior].add(senior)
            else:
                unresolved[left].add(right)
                unresolved[right].add(left)
    return tuple(replace(entry, yields_to=tuple(sorted(yields[entry.habit_id])),
                         unresolved_with=tuple(sorted(unresolved[entry.habit_id]))) for entry in entries)


class MemorySession:
    """One durable run's session of its memory owner."""

    def __init__(self, factory, run: Mapping[str, Any], boundary: Mapping[str, Any] | None,
                 digest: Mapping[str, Any] | None, exam: str | None = None, *,
                 trial: Mapping[str, Any] | None = None) -> None:
        if exam is not None and exam not in EXAM_MODES:
            raise ValueError("an exam runs in mode A, B or C")
        self.factory = factory
        self.run = run
        self.boundary = boundary
        self.digest = digest
        self.exam = exam
        self.learns = exam is None
        self.scores = 0
        self.embeddings = 0
        habits = [] if boundary is None else boundary["boundary"]["habits"]
        # A trial arm (review R5) runs exactly one learned habit, alone; nothing else is loaded.
        self.trial = None if trial is None else trial["habit_id"]
        self._habits = {item["habit_id"]: item for item in ([trial] if trial is not None else habits)}
        # The learned habits this session loaded (Gold admitted them at its start): its parts too.
        self._loaded: set[str] = set()
        # Learned habits not loaded because a contract they were verified under changed.
        self.unverified: list[dict[str, Any]] = []

    # -- formation and events ------------------------------------------------
    def declare_task(self, contract: Mapping[str, Any]) -> dict[str, Any]:
        return plan_task(contract, self.factory.configuration)

    def bind_event(self, event, working) -> dict[str, Any]:
        return bind_event(event, working)

    # -- registry load ----------------------------------------------------------
    def pinned(self) -> dict[str, Any]:
        return {"boundary": None if self.boundary is None else self.boundary["id"], "digest": self.digest}

    def registry_entries(self) -> tuple[LearnedHabitEntry, ...]:
        if self.exam == "A":
            return ()
        self.unverified = [{"habit_id": habit_id, "contracts_changed": changed}
                           for habit_id, item in sorted(self._habits.items())
                           for changed in [self._contracts_changed(item)] if changed]
        unverified = {item["habit_id"] for item in self.unverified}
        entries = tuple(_entry(item, slow_only=self.exam == "C") for habit_id, item in sorted(self._habits.items())
                        if habit_id not in unverified and self.factory.admitted_now(habit_id, item))
        self._loaded = {entry.habit_id for entry in entries}
        return rivalry(entries, self._habits, self.factory.configuration.parameters)

    def _contracts_changed(self, item) -> list[str]:
        """The tools whose contract is no longer the one the procedure was verified under; a procedure that
        names none is unverified as a whole. Its applicability is checked again by a reassessment, never assumed."""
        verified = (item.get("verified_under") or {}).get("contracts")
        if verified is None:
            return ["*"]
        tools = self.factory.configuration.tools.tools
        return sorted(name for name, ref in verified.items()
                      if name not in tools or tools[name].contract_ref != ref)

    def declared_trust(self, habit_identity: str) -> Mapping[str, float] | None:
        if self.boundary is None:
            return None
        return self.boundary["boundary"]["declared"].get(habit_identity)

    def veto_similarity(self, trigger_id: str, event: Mapping[str, Any]) -> dict[str, Any] | None:
        """Scorer similarity of the event text to a learned trigger's typical text; it only vetoes."""
        scorer = self.factory.configuration.scorer
        item = next((value for value in self._habits.values() if value["trigger"]["id"] == trigger_id), None)
        if scorer is None or item is None:
            return None
        template = item["trigger"]["context_template"]
        typical = {"fields": {condition["field"]: condition["value"] for condition in item["trigger"]["when"]}}
        self.scores += 1
        outcome = self._reason({"run_id": self.run["run_id"], "ordinal": f"score:{self.scores}", "episode": "score",
                                "op_scope": "score", "path": "score", "tool": scorer.tool, "retry_of": None,
                                "args": {"a": render_template(template, event),
                                         "b": render_template(template, typical)},
                                "task_id": None, "segment_marker_id": None, "habit_id": item["habit_id"]})
        value = outcome["view"]["payload"].get("similarity") if outcome["view"]["ok"] and isinstance(
            outcome["view"]["payload"], dict) else None
        if type(value) not in {int, float} or not 0.0 <= value <= 1.0:
            return None
        threshold = self.factory.configuration.parameters["semantic_veto_below"]
        return {"score": float(value), "veto": value < threshold, "threshold": threshold}

    def _reason(self, request):
        """One call of a ``reason`` component (similarity scorer, embedder), recorded by the gateway."""
        return self.factory.gateway.invoke(request)

    # -- semantic knowledge (refinement §15) ------------------------------------------
    def embed(self, text: str) -> list[float] | None:
        """The embedding of ``text`` by the declared embedder, or ``None`` without one or without an answer."""
        policy = self.factory.configuration.knowledge
        if policy is None or policy.embedder is None:
            return None
        self.embeddings += 1
        outcome = self._reason({"run_id": self.run["run_id"], "ordinal": f"embed:{self.embeddings}",
                                "episode": "embed", "op_scope": "embed", "path": "embed",
                                "tool": policy.embedder.tool, "retry_of": None, "args": {"text": text},
                                "task_id": None, "segment_marker_id": None, "habit_id": None})
        payload = outcome["view"]["payload"] if outcome["view"]["ok"] else None
        vector = payload.get("vector") if isinstance(payload, dict) else None
        if (type(vector) is not list or not vector
                or any(type(item) not in {int, float} or item != item for item in vector)):
            return None
        return [float(item) for item in vector]

    def embedded_by(self) -> str | None:
        """What a vector of this session's embedder is comparable with: the declared embedding tool, its declared
        version and the contract it is admitted under (review DEEP-6); ``None`` without an embedder."""
        policy = self.factory.configuration.knowledge
        if policy is None or policy.embedder is None:
            return None
        return digest({"tool": policy.embedder.tool, "version": policy.embedder.version,
                       "contract": self.factory.configuration.tools.contract(policy.embedder.tool).contract_ref})

    def declare_statement(self, statement, source, source_ref) -> dict[str, Any]:
        return declare_statement(statement, source, source_ref)

    def search_knowledge(self, query: str, *, valid_at, known_as_of, channels, embed) -> dict[str, Any]:
        """Candidates from the pinned snapshot's knowledge; nothing is admitted here."""
        policy = self.factory.configuration.knowledge
        if policy is None:
            raise ValueError("this memory declares no knowledge policy")
        versions = {} if self.boundary is None else self.boundary["boundary"].get("knowledge") or {}
        return knowledge_search.search(query, versions, policy, valid_at=instant(valid_at), known_as_of=known_as_of,
                                       embed=embed, channels=channels, embedded_by=self.embedded_by())

    def recorded_statement(self, identity, valid_at, known_as_of):
        """A statement of the pinned snapshot's knowledge resolved again for admission (``None`` for a candidate
        that is no statement of memory)."""
        if not (isinstance(identity, str) and identity.startswith(KINDS["statement"][1])):
            return None
        versions = {} if self.boundary is None else self.boundary["boundary"].get("knowledge") or {}
        return knowledge_search.recorded(identity, versions, self.factory.configuration.knowledge,
                                         valid_at=valid_at, known_as_of=known_as_of)

    # -- actions ----------------------------------------------------------------
    def invoke_action(self, request: Mapping[str, Any]) -> dict[str, Any]:
        request = {**request, "run_id": self.run["run_id"]}
        refusal = self._precondition(request)
        if refusal is not None:
            return self.factory.gateway.refuse(request, refusal)
        return self.factory.gateway.invoke(request)

    def identity_rules(self) -> dict[str, str]:
        """The operator's case rule of each entity namespace (``identity`` of the memory configuration)."""
        return dict(self.factory.configuration.identity)

    def answered(self, ordinals) -> dict[Any, int]:
        """Where the gateway's journal holds the final answers of these ordinals of this run, if it does."""
        return self.factory.gateway.finals(self.run["run_id"], ordinals)

    def _precondition(self, request: Mapping[str, Any]) -> str | None:
        """Why an action may not act: a graph's call that is no observation, or an action that relies on
        hypotheses not yet established (refinement §10)."""
        contract = self.factory.configuration.tools.tools.get(request["tool"])
        if request["path"] == "parallel" and contract is not None and not contract.observation:
            # A graph's concurrent calls only observe; its effect happens once, at its commit (refinement §16).
            return "a parallel graph calls only observations; its effect belongs to its commit"
        requires = request.get("requires")
        if requires is None and not (contract is not None and contract.requires_established):
            return None
        if not requires:
            return "the action requires an established hypothesis and names none"
        unestablished = sorted(item["hypothesis"] for item in requires if item["status"] != "confirmed")
        if unestablished:
            return "a required hypothesis is not established: " + ", ".join(unestablished)
        return self._bases_changed(requires)

    def _bases_changed(self, requires) -> str | None:
        """Before an effect, every basis read from the court's record is read again (review §8.2, after
        Kubernetes resourceVersion): a status the court revised after the window it was read at — a corrected
        source, a later check — or one resting on results the operator forgot, refuses the effect. A check this
        run made is fresh; an exam reads its fixed snapshot."""
        recorded = {item["hypothesis"]: item["read"]["window"] for item in requires
                    if (item.get("read") or {}).get("method") == "court_record"}
        if self.exam is not None or not recorded:
            return None
        state = self.factory.memory_now()
        changed = [f"{hypothesis_id} is now {entry['status']} ({entry['reason']})"
                   for hypothesis_id, window in recorded.items()
                   for entry in [state["hypotheses"].get(hypothesis_id)]
                   if entry is not None and entry["window"] > window and entry["status"] != "confirmed"]
        # A forget the operator recorded acts before the next consolidation applies it.
        for _, tombstone, found in forgotten_dependents(state):
            changed.extend(f"{hypothesis_id} rests on forgotten results ({tombstone['tombstone']})"
                           for hypothesis_id in found.get("hypothesis", [])
                           if hypothesis_id in recorded and recorded[hypothesis_id] <= tombstone["window"])
        changed = sorted(set(changed))
        return "a required basis changed since it was read: " + "; ".join(changed) if changed else None

    # -- hypotheses (refinement §10) ---------------------------------------------
    def declare_hypothesis(self, claim, source_ref) -> dict[str, Any]:
        return declare(claim, self.factory.configuration, source_ref)

    def resolve_hypothesis(self, record, view) -> dict[str, Any]:
        return resolve(record, view, self.factory.configuration)

    def hypothesis_verification(self, record, **decided) -> dict[str, Any]:
        return verification(record, self.factory.configuration, **decided)

    def hypothesis_source(self, record) -> dict[str, Any]:
        """Where the hypothesis read its claim: the tool, the source it answers for, the recorded answer."""
        contract = self.factory.configuration.tools.tools.get(record["source"]["tool"])
        return {"tool": record["source"]["tool"], "name": None if contract is None else contract.source,
                "ref": record["source"]["ref"]}

    def known_hypothesis(self, record) -> dict[str, Any]:
        """The court's status for this very hypothesis, if the pinned snapshot holds a fresh one."""
        if self.boundary is None:
            return {"status": None, "reason": "no_memory_snapshot"}
        boundary = self.boundary["boundary"]
        configuration = self.factory.configuration
        return reuse(record, boundary["hypotheses"].get(record["id"]), boundary["claims"], boundary["window"],
                     configuration.parameters, check_basis(record, configuration))

    # -- learned bodies and their composition (refinement §14) --------------------
    def _part(self, habit_id: str) -> Mapping[str, Any] | None:
        """A part for a composition: a learned habit this session loaded (the opening records them)."""
        return self._habits.get(habit_id) if habit_id in self._loaded else None

    def _parts(self) -> list[Mapping[str, Any]]:
        """Every admitted part, in rank order. Slow-only habits included: automation off leaves them
        material for slow planning."""
        return ranked(item for habit_id, item in sorted(self._habits.items()) if self._part(habit_id) is not None)

    def run_learned_body(self, habit_id: str, event: Mapping[str, Any], ports: ActionPorts) -> dict[str, Any]:
        item = self._habits[habit_id]
        habit = item["habit"]
        composition = habit.get("composition")
        contingency = None if composition is None else recorded_contingency(
            habit["action_pattern"], composition["joins"], self._part, self.factory.configuration,
            stack=stack_of(item))
        return execute(habit["action_pattern"], habit["binding"], event, ports, contingency=contingency)

    def plan_recovery(self, event: Mapping[str, Any], body: Mapping[str, Any] | None, ports: ActionPorts,
                      record) -> dict[str, Any]:
        """The slow planner: recover a failed action with a composition of admitted parts.

        A learned body that already ran and stopped is continued from its
        answers and the parts it already tried; otherwise the base is the first
        part whose trigger applies to the failure. At each impasse the planner
        tries the known alternatives, then the parts that may join, in rank
        order; ``record`` receives every hypothesis it forms. The composition's
        joins are the known ones extended by the ones this execution made.
        """
        parts = self._parts()
        base, prefix, prior = None, (), ()
        if body is not None and body.get("habit_id") in self._habits:
            base, prefix, prior = self._habits[body["habit_id"]], tuple(body["answers"]), tuple(body["parts"])
        else:
            context = typed_context(event)
            base = next((item for item in parts if matches(item["trigger"], context)[0] == "applicable"), None)
        if base is None:
            return {"base": None, "joins": [], "result": None}
        habit = base["habit"]
        composition = habit.get("composition") or {"base": habit["id"], "joins": []}
        contingency = planning_contingency(habit["action_pattern"], composition["joins"], parts,
                                           self.factory.configuration, record, stack=stack_of(base))
        result = execute(habit["action_pattern"], habit["binding"], event, ports, prefix=prefix, prior=prior,
                         contingency=contingency)
        return {"base": composition["base"], "used": habit["id"],
                "joins": merge_joins(composition["joins"], joins_of(result["parts"])), "prefix": len(prefix),
                "result": result}

    # -- court ------------------------------------------------------------------
    def consolidate(self, mode: str, *, history: list[dict[str, Any]]) -> dict[str, Any]:
        return self.factory.court(mode, current={**self.run, "history": history})


class ReplaySession(MemorySession):
    """The session of a verified re-execution: recorded answers only, no live effect."""

    def __init__(self, factory, run, boundary, digest, recorded_learned, exam=None) -> None:
        super().__init__(factory, run, boundary, digest, exam)
        self.recorded_learned = list(recorded_learned or [])

    def registry_entries(self) -> tuple[LearnedHabitEntry, ...]:
        entries = []
        for item in self.recorded_learned:
            habit = self._habits.get(item["habit_id"])
            if habit is None or habit["trigger"]["id"] != item["trigger_id"]:
                raise ReplayHorizon("a recorded learned habit is not in its pinned boundary")
            entries.append(_entry(habit, item["context_trust"], slow_only=item["slow_only"]))
        self._loaded = {entry.habit_id for entry in entries}
        return rivalry(tuple(entries), self._habits, self.factory.configuration.parameters)

    def _reason(self, request):
        recorded = self.factory.gateway.recorded(request["run_id"], request["ordinal"])
        if recorded is None:
            raise ReplayHorizon("a re-execution asked for an unrecorded similarity or embedding")
        return recorded

    def invoke_action(self, request: Mapping[str, Any]) -> dict[str, Any]:
        raise ReplayHorizon("a re-execution reached a live external action")

    def consolidate(self, mode: str, *, history: list[dict[str, Any]]) -> dict[str, Any]:
        raise ReplayHorizon("a re-execution reached a live consolidation")


class ReproductionSession(ReplaySession):
    """The session of a reproduction: a live program whose actions are answered from the record.

    Retention reproduces a session from its replay data to prove a raw trace
    reconstructible. Every action is the gateway's recorded final outcome of
    the same run and ordinal, for the same request; nothing else is answered.
    """

    def invoke_action(self, request: Mapping[str, Any]) -> dict[str, Any]:
        gateway = self.factory.gateway
        recorded = gateway.recorded(self.run["run_id"], request["ordinal"])
        if recorded is None or gateway.recorded_request(self.run["run_id"], request["ordinal"]) != digest(
                {"tool": request["tool"], "args": request["args"]}):
            raise ReplayHorizon("a reproduction reached an action its record does not answer")
        return recorded
