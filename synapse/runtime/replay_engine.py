from __future__ import annotations

from typing import Any, Dict, List, Optional, Callable


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
    }

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
        """Append one event, or verify it against the history being replayed.

        A verified replay re-executes the program; an event it produces must be
        the recorded event at the cursor, byte for byte in canonical form. The
        recorded event is consumed, and once the history is exhausted the run
        continues LIVE. Outside a verified replay this is a plain append.
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
        self.get_history().append(event)
        return event

    def recorded_length(self) -> int:
        """How many events existed at this point of execution.

        A verified replay holds the whole record, but execution has only
        reached the cursor; identities derived from the history must see that
        prefix, exactly as the original run did.
        """
        if self.get_runtime_mode() == self.replay_mode and self.get_verified_replay():
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
        while idx < len(self.get_history()):
            event = self.get_history()[idx]
            if event.get("type") in self.AUDIT_ONLY_PEEK_TYPES:
                idx += 1
                continue
            return event
        self.set_runtime_mode(self.live_mode)
        return None

    def next_history_event(self, expected_type: str, name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if self.get_runtime_mode() != self.replay_mode:
            return None
        while self.get_replay_cursor() < len(self.get_history()):
            cursor = self.get_replay_cursor()
            event = self.get_history()[cursor]
            self.set_replay_cursor(cursor + 1)
            if event.get("type") == expected_type:
                if name is not None and event.get("name") != name:
                    raise RuntimeError(
                        f"Replay history mismatch: expected {expected_type}:{name}, "
                        f"got {event.get('type')}:{event.get('name')}"
                    )
                if self.get_verified_replay() and cursor + 1 == len(self.get_history()):
                    # A verified replay continues LIVE the moment its record ends.
                    self.set_runtime_mode(self.live_mode)
                return event
            if event.get("type") in self.SKIPPABLE_REPLAY_TYPES and not self.get_verified_replay():
                continue
            raise RuntimeError(f"Replay history mismatch: expected {expected_type}, got {event.get('type')}")
        self.set_runtime_mode(self.live_mode)
        return None

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
