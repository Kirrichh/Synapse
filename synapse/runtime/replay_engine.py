from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple


class ReplayIntegrityError(RuntimeError):
    """Strict replay detected a corrupted or mismatched recorded event."""


class ReplayEngine:
    """Replay/history helper extracted from Interpreter.

    This first extraction intentionally keeps the interpreter as the owner of
    runtime state.  The engine receives explicit state accessors/functions and
    mirrors the original methods bit-for-bit where replay semantics are
    involved.  It does not evaluate AST nodes and does not call actor, habit,
    governance, affective, identity, or VM code.
    """

    AUDIT_ONLY_PEEK_TYPES = {
        "message_sent",
        "message_forwarded",
        "promise_resolution_forwarded",
        "policy_evaluated",
        "checkpoint",
    }

    SKIPPABLE_REPLAY_TYPES = {
        "message_sent",
        "message_forwarded",
        "promise_resolution_forwarded",
        "policy_violation",
        "policy_evaluated",
        "checkpoint",
        "debate_completed",
        "reflect_query",
        "integrate_committed",
        "integrate_rollback",
        "evolution_ticket_created",
        "evolution_approved",
        "soulprint_evolved",
        # VM audit records: the VM finds its LLM answers by content key
        # (LLM_RESPONSE_CACHED) and its recorded builtins as side_effect events.
        "vm_bytecode_compiled",
        "vm_executed",
        "vm_fallback",
        "vm_host_call",
        "vm_routing_cvm",
        "vm_checkpoint_saved",
        "LLM_RESPONSE_CACHED",
    }

    # Records an ordinary replay never produces again and no lookup of it consumes: those of the reactions it does
    # not run again (the energy pool, habits, affective thresholds) and of live-only work (a model answer's cache
    # hit, a resonance profile computed from the live history). An operation looking for its own record passes over
    # them (review F6).
    UNREPLAYED_TYPES = frozenset({
        "energy_pool_recharged",
        "agent_entered_rest",
        "agent_exited_rest",
        "habit_activated",
        "habit_suppressed",
        "habit_near_miss",
        "habit_miss",
        "habit_candidate_suggested",
        "habit_execution_failed",
        "habit_fatigued",
        "habit_resting",
        "habit_recovered",
        "affective_threshold_triggered",
        "affective_threshold_action_failed",
        "threshold_suspend_requested",
        "LLM_RESPONSE_CACHED_HIT",
        "resonance_profile_computed",
    })

    def __init__(
        self,
        *,
        history_getter: Callable[[], List[Dict[str, Any]]],
        runtime_mode_getter: Callable[[], Any],
        runtime_mode_setter: Callable[[Any], None],
        replay_cursor_getter: Callable[[], int],
        replay_cursor_setter: Callable[[int], None],
        live_mode: Any,
        replay_mode: Any,
        builtins_registry: Dict[str, Any],
        hash_event_chain_fn: Callable[..., List[Dict[str, Any]]],
        verify_event_chain_fn: Callable[..., bool],
        history_chain_seed_getter: Callable[[], str],
        verified_replay_getter: Callable[[], bool] = lambda: False,
        canonical_fn: Callable[[Any], str] = repr,
    ):
        self.get_history = history_getter
        self.get_runtime_mode = runtime_mode_getter
        self.set_runtime_mode = runtime_mode_setter
        self.get_replay_cursor = replay_cursor_getter
        self.set_replay_cursor = replay_cursor_setter
        self.live_mode = live_mode
        self.replay_mode = replay_mode
        self.builtins = builtins_registry
        self.hash_event_chain = hash_event_chain_fn
        self.verify_event_chain = verify_event_chain_fn
        self.get_history_chain_seed = history_chain_seed_getter
        self.get_verified_replay = verified_replay_getter
        self.canonical = canonical_fn

    def append_event(self, event: Dict[str, Any]) -> None:
        self.get_history().append(event)

    def record_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Append one event, or take the place the replayed run recorded for it.

        A replay re-executes the program, and an event it produces again is the
        recorded one. A verified replay requires it at the cursor, byte for byte
        in canonical form; an ordinary replay finds it by its type as any other
        lookup does, past the audit records it passes over, and refuses another
        record standing first (review F6). The recorded event is consumed; once
        the history is exhausted the run continues LIVE and the event is appended.
        """
        if self.get_runtime_mode() == self.replay_mode and self.get_verified_replay():
            history = self.get_history()
            cursor = self.get_replay_cursor()
            if cursor < len(history):
                recorded = history[cursor]
                if self.canonical(recorded) != self.canonical(event):
                    raise ReplayIntegrityError(
                        f"REPLAY_INTEGRITY_ERROR: event {cursor} differs from its recorded "
                        f"{recorded.get('type')!r} (produced {event.get('type')!r})"
                    )
                self.set_replay_cursor(cursor + 1)
                if cursor + 1 == len(history):
                    self.set_runtime_mode(self.live_mode)
                return recorded
            self.set_runtime_mode(self.live_mode)
        if self.get_runtime_mode() == self.replay_mode:
            recorded = self.next_history_event(event.get("type"))
            if recorded is not None:
                return recorded
        self.get_history().append(event)
        return event

    def recorded_length(self) -> int:
        """How many events existed at this point of execution.

        A replay holds the whole record, but execution has only reached the
        cursor; identities and positions derived from the history must see that
        prefix, exactly as the original run did — in a verified replay and, now
        that it consumes the records it produces again, an ordinary one (review
        F6).
        """
        if self.get_runtime_mode() == self.replay_mode:
            return self.get_replay_cursor()
        return len(self.get_history())

    def peek_history_event(self) -> Optional[Dict[str, Any]]:
        if self.get_runtime_mode() != self.replay_mode or self.get_replay_cursor() >= len(self.get_history()):
            return None
        return self.get_history()[self.get_replay_cursor()]

    def peek_next_history_event(self) -> Optional[Dict[str, Any]]:
        """Return next replay-significant event without advancing cursor."""
        if self.get_runtime_mode() != self.replay_mode:
            return None
        idx = self.get_replay_cursor()
        unreplayed = frozenset() if self.get_verified_replay() else self.UNREPLAYED_TYPES
        while idx < len(self.get_history()):
            event = self.get_history()[idx]
            if event.get("type") in self.AUDIT_ONLY_PEEK_TYPES or event.get("type") in unreplayed:
                idx += 1
                continue
            return event
        self.set_runtime_mode(self.live_mode)
        return None

    def _frontier(self, wanted: Callable[[Dict[str, Any]], bool],
                  ends: Callable[[Dict[str, Any]], bool]) -> Tuple[int, Optional[Dict[str, Any]]]:
        """Where an operation looking for its record stands: the first event at or after the cursor that it
        ``wanted``, that ``ends`` its search, or that this replay does not pass over. An ordinary replay passes
        over the audit records it does not re-check and the records it never produces again; a verified replay
        over none. No event: the record is exhausted."""
        history, index = self.get_history(), self.get_replay_cursor()
        verified = self.get_verified_replay()
        while index < len(history):
            event = history[index]
            unreplayed = not verified and event.get("type") in self.UNREPLAYED_TYPES
            if not unreplayed and (wanted(event) or ends(event) or verified
                                   or event.get("type") not in self.SKIPPABLE_REPLAY_TYPES):
                return index, event
            index += 1
        return index, None

    def _consume(self, index: int) -> Dict[str, Any]:
        """Consume the record at ``index`` with the audit records passed over before it. A verified replay
        continues LIVE the moment its record ends; an ordinary one at the end of the statement (``settle``)."""
        history = self.get_history()
        self.set_replay_cursor(index + 1)
        if self.get_verified_replay() and index + 1 == len(history):
            self.set_runtime_mode(self.live_mode)
        return history[index]

    def settle(self) -> None:
        """Between two statements of the program: a record consumed to its end ends the replay, and the run
        continues LIVE (review F6). Inside a statement it does not — an operation that records several events finds
        a record cut between them incomplete, never the end of the replay. Records the replay never produces again
        do not end it here: a reaction recorded last may follow a statement still to come, and would run twice; the
        next operation passes over them."""
        if self.get_runtime_mode() == self.replay_mode and self.get_replay_cursor() >= len(self.get_history()):
            self.set_runtime_mode(self.live_mode)

    def _exhausted(self, index: int) -> None:
        """Nothing is left to follow: the audit records before ``index`` are passed over and the run is LIVE."""
        self.set_replay_cursor(index)
        self.set_runtime_mode(self.live_mode)

    def recorded(self, wanted: Callable[[Dict[str, Any]], bool],
                 ends: Callable[[Dict[str, Any]], bool] = lambda event: False) -> Optional[Dict[str, Any]]:
        """The record a re-executed operation left, consumed: the first event at the frontier ``wanted`` accepts.

        ``None`` with the run LIVE: it is not replaying, or the record is exhausted, which ends the replay here —
        the operation is new and acts now, once. ``None`` with the run still replaying: another operation's
        record, or one ``ends`` names, stands first — this operation left no record here and nothing is consumed.
        """
        if self.get_runtime_mode() != self.replay_mode:
            return None
        index, event = self._frontier(wanted, ends)
        if event is None:
            self._exhausted(index)
            return None
        return self._consume(index) if wanted(event) else None

    def audit(self, event: Dict[str, Any]) -> bool:
        """An operation's audit record: appended while the run is live. An ordinary replay that re-executed the
        operation consumes the record it left when that record is next, so the replay ends where its record ends
        (review F6); past that end the operation is new and its record is appended. Whether it was appended."""
        if self.get_runtime_mode() == self.replay_mode and not self.get_verified_replay():
            self.recorded(lambda item: item.get("type") == event.get("type"), lambda item: True)
        if self.get_runtime_mode() != self.live_mode:
            return False
        self.get_history().append(event)
        return True

    def next_history_event(self, expected_type: str, name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if self.get_runtime_mode() != self.replay_mode:
            return None
        index, event = self._frontier(lambda item: item.get("type") == expected_type, lambda item: False)
        if event is None:
            self._exhausted(index)
            return None
        if event.get("type") != expected_type:
            self.set_replay_cursor(index + 1)
            raise RuntimeError(f"Replay history mismatch: expected {expected_type}, got {event.get('type')}")
        if name is not None and event.get("name") != name:
            self.set_replay_cursor(index + 1)
            raise RuntimeError(
                f"Replay history mismatch: expected {expected_type}:{name}, "
                f"got {event.get('type')}:{event.get('name')}"
            )
        return self._consume(index)

    def execute_side_effect(self, name: str, args: List[Any]) -> Any:
        event = self.next_history_event("side_effect", name=name)
        if event is not None:
            return event.get("result")

        if name not in self.builtins:
            raise RuntimeError(f"Unknown side-effect builtin: '{name}'")
        result = self.builtins[name](*args)
        self.get_history().append({
            "type": "side_effect",
            "name": name,
            "args": args,
            "result": result,
        })
        return result


    def promise_result_by_call_id(self, call_id: str) -> Any:
        """Return a unique promise resolution result by call_id for D2 replay.

        Promise results are history-bound by durable call_id, not by positional
        replay cursor. Duplicate or missing resolution events are replay errors.
        """
        matches = [
            event for event in self.get_history()
            if isinstance(event, dict)
            and event.get("type") in {"promise_resolved", "promise_rejected"}
            and event.get("call_id") == call_id
        ]
        if len(matches) == 0:
            raise RuntimeError(f"Replay promise resolution missing for call_id {call_id}")
        if len(matches) > 1:
            raise RuntimeError(f"Replay promise resolution duplicate for call_id {call_id}")
        event = matches[0]
        return event.get("error") if event.get("type") == "promise_rejected" else event.get("result")

    def compute_history_hash(self) -> str:
        chain = self.hash_event_chain(self.get_history(), seed=self.get_history_chain_seed())
        return chain[-1]["hash"] if chain else ""

    def history_hash_chain(self) -> List[Dict[str, Any]]:
        return self.hash_event_chain(self.get_history(), seed=self.get_history_chain_seed())

    def verify_history_chain(self, chain: List[Dict[str, Any]]) -> bool:
        return self.verify_event_chain(self.get_history(), chain, seed=self.get_history_chain_seed())
