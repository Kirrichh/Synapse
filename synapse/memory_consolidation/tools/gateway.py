"""The single gateway of external actions (memory spec part 1 §8.7, part 3 §5).

Every external action of a memory-bound durable run leaves the process here,
from the slow path and from a habit body alike. Three rules are enforced in
code rather than by convention:

* an episode is derived only from what this journal recorded — no observer can
  hand the court a ready-made result;
* an evidence address belongs to its first write: a repeated write of the same
  address never replaces the content, and the journal records that it was
  already there;
* roles stay apart: ``reason`` answers cost the slow path, only ``action``
  results can enter a habit body or count as an outside witness.

Operation identity is fixed before a call. Operations are numbered within an
operation scope: the episode of a plan step, which the reaction to one of its
failed actions (a habit body or the slow path) shares, so a recovery can name
the very operation it repeats. A retry must name an existing operation of that
scope with the same tool and arguments, a hidden repeat of an operation with an
unknown effect is bound to it, and a repeat the tool contract does not admit is
refused before any effect. A STARTED record without
its RESULT is a call whose effect is unknown; recovery never repeats it unless
the contract makes the operation idempotent.

The journal is append-only JSON lines with its own hash chain, shared by all
runs of one memory owner under a process-level lock; wall-clock time never
enters it. Durations live in a separate side log with its own chain.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import time
from typing import Any, Mapping

from ..records import canonical, digest
from .contracts import ToolConfiguration, ToolContract
from .evidence import EvidenceStore
from .journal import ChainedLog, GatewayIntegrityError
from .mcp_transport import McpToolTransport
from .semantics import interpret, repeat_admissible

GATEWAY_RECORD_V1 = "synapse.memory.gateway-record/v1"
_GENESIS = hashlib.sha256(b"synapse.memory.gateway-journal/v1").hexdigest()
_SIDE_GENESIS = hashlib.sha256(b"synapse.memory.side-time/v1").hexdigest()
_KINDS = {"STARTED", "RESULT", "REJECTED"}
_FORBIDDEN_TIME_KEYS = frozenset({"ts", "timestamp", "time", "wall_time", "duration", "elapsed", "datetime", "date"})


def _chain_hash(seq: int, kind: str, prev: str, body: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical({"schema": GATEWAY_RECORD_V1, "seq": seq, "kind": kind, "prev": prev,
                                     "body": dict(body)})).hexdigest()


class Gateway:
    """External actions of one memory owner, recorded before their results are used."""

    def __init__(self, root: Path, configuration: ToolConfiguration, *, executor: str, transport=None) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.configuration = configuration
        self.executor = executor
        self.journal = ChainedLog(root / "journal.jsonl", _GENESIS,
                                   lambda r: _chain_hash(r["seq"], r["kind"], r["prev"], r["body"]))
        self.side_log = ChainedLog(root / "side-time.jsonl", _SIDE_GENESIS,
                                    lambda r: hashlib.sha256(canonical({key: r[key] for key in (
                                        "seq", "prev", "gw_seq", "duration_ms", "measured_by")})).hexdigest())
        self.evidence = EvidenceStore(root / "evidence")
        self.transport = transport if transport is not None else McpToolTransport(configuration)

    # -- reading ---------------------------------------------------------
    def records(self) -> list[dict[str, Any]]:
        return self.journal.read()

    def head(self) -> str:
        records = self.records()
        return records[-1]["hash"] if records else _GENESIS

    def side_records(self) -> list[dict[str, Any]]:
        return self.side_log.read()

    def recorded_outcome(self, seq: int, records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """The outcome a run recorded for one final journal record, recomputed from the journal."""
        records = self.records() if records is None else records
        if not 0 <= seq < len(records) or records[seq]["kind"] not in {"RESULT", "REJECTED"}:
            raise GatewayIntegrityError("a recorded action names no final gateway record")
        record = records[seq]
        return self._outcome(record, self.configuration.contract(record["body"]["tool"]), records)

    # -- one action ------------------------------------------------------
    def invoke(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Perform, recover or refuse one ordinal action of one run."""
        for key in _FORBIDDEN_TIME_KEYS & set(request.get("args") or {}):
            raise ValueError(f"wall-clock field {key!r} cannot enter the canonical request")
        contract = self.configuration.contract(request["tool"])
        run_id, ordinal = request["run_id"], request["ordinal"]
        records = self.records()
        mine = [item for item in records if item["body"].get("run_id") == run_id]
        request_canon = digest({"tool": request["tool"], "args": request["args"]})
        by_ordinal = [item for item in mine if item["body"].get("ordinal") == ordinal]
        finished = [item for item in by_ordinal if item["kind"] in {"RESULT", "REJECTED"}]
        stale = [item for item in by_ordinal if item["kind"] == "STARTED"]
        if by_ordinal and any(item["body"].get("request_canon", request_canon) != request_canon for item in by_ordinal):
            raise GatewayIntegrityError("a recovered run asks another action at the same ordinal")
        if finished and finished[-1]["body"].get("final"):
            # Recorded before a crash point was persisted: consumed, never repeated.
            return self._outcome(finished[-1], contract, records)
        scope = request["op_scope"]
        ops = self._scope_ops(mine, scope)
        if stale and not [item for item in finished if item["body"].get("started_seq") == stale[-1]["seq"]]:
            started = stale[-1]
            lost = self._append("RESULT", {
                "started_seq": started["seq"], "run_id": run_id, "ordinal": ordinal, "tool": contract.name,
                "request_canon": request_canon, "transport": "lost", "op_result": "unknown", "op_err": None,
                "effect": "unknown", "evidence_ref": None, "evidence_preexisting": False,
                "recovered": True, "final": not contract.idempotent})
            if not contract.idempotent:
                return self._outcome(lost, contract, self.records())
            ops = self._scope_ops([*mine, started, lost], scope)
            op = next(item for item in ops if item["op_seq"] == started["body"]["op_seq"])
            return self._attempt(request, contract, request_canon, op, retry_of=op["op_seq"], admitted=True)
        target, decision = self._resolve_operation(request, contract, ops)
        if decision is not None:
            rejected = self._append("REJECTED", {
                "run_id": run_id, "ordinal": ordinal, "episode": request["episode"], "op_scope": scope,
                "tool": contract.name,
                "request_canon": request_canon, "op_seq": None if target is None else target["op_seq"],
                "reason": decision, "task_id": request.get("task_id"), "segment": request.get("segment_marker_id"),
                "off_plan": bool(request.get("off_plan")), "path": request["path"], "habit_id": request.get("habit_id"), "final": True})
            return self._outcome(rejected, contract, self.records())
        if target is None:
            target = {"op_seq": len(ops) + 1, "attempts": []}
            return self._attempt(request, contract, request_canon, target, retry_of=None, admitted=None)
        return self._attempt(request, contract, request_canon, target, retry_of=target["op_seq"], admitted=True)

    def _resolve_operation(self, request, contract, ops):
        retry_of = request.get("retry_of")
        args_canon = digest(request["args"])
        if retry_of is not None:
            target = next((item for item in ops if item["op_seq"] == retry_of), None)
            if target is None:
                return None, "a declared retry names no operation of this scope"
            if target["tool"] != contract.name or target["args_canon"] != args_canon:
                return target, "a declared retry changes the tool or its essential arguments"
            allowed, reason = repeat_admissible(target["last_effect"], contract)
            return target, None if allowed and not target["resolved"] else (
                "the operation already succeeded" if target["resolved"] else reason)
        hidden = next((item for item in reversed(ops) if item["tool"] == contract.name
                       and item["args_canon"] == args_canon and not item["resolved"]
                       and item["last_effect"] == "unknown"), None)
        if hidden is not None:
            allowed, reason = repeat_admissible("unknown", contract)
            return hidden, None if allowed else f"a hidden repeat of an unresolved operation: {reason}"
        return None, None

    def _scope_ops(self, mine, scope):
        ops: dict[int, dict[str, Any]] = {}
        started = {item["seq"]: item for item in mine if item["kind"] == "STARTED"}
        for item in mine:
            body = item["body"]
            if body.get("op_scope") != scope or item["kind"] != "STARTED":
                continue
            op = ops.setdefault(body["op_seq"], {"op_seq": body["op_seq"], "tool": body["tool"],
                                                 "args_canon": body["args_canon"], "attempts": [],
                                                 "resolved": False, "last_effect": "unknown"})
            op["attempts"].append(item["seq"])
        for item in mine:
            if item["kind"] != "RESULT":
                continue
            origin = started.get(item["body"]["started_seq"])
            if origin is None or origin["body"].get("op_scope") != scope:
                continue
            op = ops[origin["body"]["op_seq"]]
            op["last_effect"] = item["body"]["effect"]
            op["resolved"] = op["resolved"] or item["body"]["op_result"] == "ok"
        return [ops[key] for key in sorted(ops)]

    def _attempt(self, request, contract, request_canon, op, *, retry_of, admitted):
        attempt = len(op["attempts"]) + 1
        started = self._append("STARTED", {
            "run_id": request["run_id"], "ordinal": request["ordinal"], "episode": request["episode"],
            "op_scope": request["op_scope"], "task_id": request.get("task_id"), "segment": request.get("segment_marker_id"),
            "off_plan": bool(request.get("off_plan")), "path": request["path"], "role": contract.role,
            "habit_id": request.get("habit_id"), "tool": contract.name, "args": dict(request["args"]),
            "args_canon": digest(request["args"]), "request_canon": request_canon, "op_seq": op["op_seq"],
            "retry_of": retry_of, "admitted": admitted, "attempt": attempt, "source": contract.source,
            "executor": self.executor, "contract_ref": contract.contract_ref})
        began = time.perf_counter()
        transport, payload = self.transport.call(contract, request["args"])
        duration_ms = round((time.perf_counter() - began) * 1000.0, 3)
        ref, preexisting = self.evidence.put({"tool": contract.name, "transport": transport,
                                              "payload": payload, "source": contract.source})
        reading = interpret(contract, transport, payload)
        result = self._append("RESULT", {
            "started_seq": started["seq"], "run_id": request["run_id"], "ordinal": request["ordinal"],
            "tool": contract.name, "request_canon": request_canon, "transport": transport, **reading,
            "evidence_ref": ref, "evidence_preexisting": preexisting, "recovered": False, "final": True})
        self.side_log.append(lambda seq, prev: {"seq": seq, "prev": prev, "gw_seq": result["seq"],
                                                "duration_ms": duration_ms, "measured_by": "wall_clock"})
        return self._outcome(result, contract, self.records())

    def _append(self, kind: str, body: dict[str, Any]) -> dict[str, Any]:
        if kind not in _KINDS:
            raise ValueError("unknown gateway record kind")
        return self.journal.append(lambda seq, prev: {"seq": seq, "kind": kind, "prev": prev, "body": body})

    def _outcome(self, record, contract, records) -> dict[str, Any]:
        """The strict JSON outcome a durable run records for one action."""
        body = record["body"]
        if record["kind"] == "REJECTED":
            view = {"ok": False, "tool": contract.name, "op": body["op_seq"], "attempt": None,
                    "transport": "rejected", "op_result": "rejected", "op_err": None, "effect": "none",
                    "payload": None, "source": contract.source, "reason": body["reason"]}
            fields = {"tool": contract.name, "transport": "rejected", "op_result": "rejected", "effect": "none"}
            return {"ref": {"gw_seq": record["seq"], "evidence": None}, "view": view, "event_fields": fields}
        started = next(item for item in records if item["seq"] == body["started_seq"])
        payload = None
        if body["evidence_ref"] is not None:
            content = self.evidence.get(body["evidence_ref"])
            if content is None:
                raise GatewayIntegrityError("a recorded result no longer resolves to its evidence")
            payload = content["payload"]
        view = {"ok": body["transport"] == "ok" and body["op_result"] == "ok", "tool": contract.name,
                "op": started["body"]["op_seq"], "attempt": started["body"]["attempt"],
                "transport": body["transport"], "op_result": body["op_result"], "op_err": body["op_err"],
                "effect": body["effect"], "payload": payload, "source": contract.source}
        fields = {"tool": contract.name, "transport": body["transport"], "op_result": body["op_result"],
                  "effect": body["effect"]}
        if body["op_err"] is not None:
            fields["op_err"] = body["op_err"]
        if isinstance(payload, dict):
            for name in contract.event_fields:
                if name in payload and isinstance(payload[name], (str, int, float, bool)):
                    fields[name] = payload[name]
        return {"ref": {"gw_seq": record["seq"], "evidence": body["evidence_ref"]}, "view": view,
                "event_fields": fields}
