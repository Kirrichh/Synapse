"""A connected project with admitted scripted tools, driven only by the canonical CLI.

The world connects a Gold project (the stage 16 source fixture), serves the
scenario's tools from ``acceptance.memory.tool_server`` over MCP stdio, admits
their descriptors by digest in a frozen memory configuration, and runs Synapse
programs with ``python -m synapse run --durable --project-state
--memory-config``. Checks read three independent places: the run artifacts,
the project journal through the memory owner's reader, and the tool server's
own world record.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from acceptance.memory.tool_server import SCRIPT_V1, descriptor
from acceptance.stage4.stage16._source_inputs import prepare
from synapse.agents.codec import digest
from synapse.memory_consolidation.owner import MemoryOwner

REPOSITORY = Path(__file__).resolve().parents[2]
OBJECT = {"type": "object"}


def tool(name, source, answers, *, contract=None, event_fields=(), role="action", server="scripted"):
    """One scripted tool: its server, the server's answers and the operator's contract for it."""
    return {"name": name, "source": source, "answers": answers, "contract": contract or {},
            "event_fields": list(event_fields), "role": role, "server": server}


def answer(payload, *, when=None, sequence=(), effect=None):
    """A rule: ``sequence`` answers the first calls of one exact request, then ``payload``."""
    then = {"payload": payload} if effect is None else {"payload": payload, "effect": effect}
    return {"when": when or {}, "sequence": list(sequence), "then": then}


class MemoryWorld:
    """One project, its tool server and its runs."""

    def __init__(self, root: Path, tools, *, provenance, parameters=None, decision_rule="threshold",
                 state: Path | None = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # A world connects its own project, or joins the state of one another stream already uses.
        self.state = Path(state) if state is not None else prepare(self.root)[1]
        self.runs = self.root / "runs"
        self.runs.mkdir()
        self.world_path = self.root / "world.json"
        servers, served = [], {}
        for server_id in sorted({item["server"] for item in tools}):
            script = {"schema_version": SCRIPT_V1, "tools": [
                {"name": item["name"], "input_schema": OBJECT, "output_schema": OBJECT, "answers": item["answers"]}
                for item in tools if item["server"] == server_id]}
            served.update((entry["name"], entry) for entry in script["tools"])
            script_path = self.root / f"{server_id}.tools.json"
            script_path.write_text(json.dumps(script, sort_keys=True))
            servers.append({"id": server_id, "argv": [sys.executable, "-B", "-m", "acceptance.memory.tool_server",
                                                      str(script_path), str(self.world_path)],
                            "env": {"PYTHONPATH": str(REPOSITORY)}, "cwd": str(REPOSITORY)})
        admitted = []
        for item in tools:
            entry = {"name": item["name"], "server": item["server"], "input_schema": OBJECT, "output_schema": OBJECT,
                     "descriptor_sha256": digest(descriptor(served[item["name"]]).model_dump(
                         mode="json", by_alias=True, exclude_none=True)),
                     "source": item["source"], "role": item["role"], "contract": item["contract"],
                     "event_fields": item["event_fields"]}
            admitted.append(entry)
        self.configuration_path = self.root / "memory.json"
        self.configuration_path.write_text(json.dumps({
            "schema_version": "synapse.memory.configuration/v1",
            "tools": {"schema_version": "synapse.memory.tool-configuration/v1", "servers": servers,
                      "tools": admitted, "provenance": provenance},
            "court": {"decision_rule": decision_rule, "parameters": parameters or {}},
            "advisor": None, "scorer": None, "element": "acceptance.travel"},
            sort_keys=True))

    # -- the canonical launch --------------------------------------------------
    def _cli(self, *arguments) -> tuple[int, dict | None, str]:
        environment = {**os.environ, "PYTHONPATH": str(REPOSITORY)}
        completed = subprocess.run([sys.executable, "-B", "-m", "synapse", *map(str, arguments)], cwd=REPOSITORY,
                                   env=environment, capture_output=True, text=True, timeout=900)
        lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
        return completed.returncode, json.loads(lines[-1]) if lines else None, completed.stderr

    def _run_arguments(self, source: str, run_id: str, bindings: dict, exam=None) -> list:
        program = self.root / f"{run_id}.syn"
        program.write_text(source)
        inputs = self.root / f"{run_id}.input.json"
        inputs.write_text(json.dumps(bindings, sort_keys=True))
        arguments = ["run", program, "--durable", "--state-dir", self.runs, "--run-id", run_id,
                     "--input-file", inputs, "--project-state", self.state, "--memory-config", self.configuration_path]
        if exam is not None:
            mode, snapshot = exam
            arguments += ["--exam-mode", mode, "--exam-snapshot", snapshot]
        return arguments

    def run(self, source: str, run_id: str, bindings: dict, *, exam=None) -> dict:
        """One ordinary durable session, or an exam ``(mode, snapshot boundary id)`` on a fixed snapshot."""
        code, payload, stderr = self._cli(*self._run_arguments(source, run_id, bindings, exam))
        assert code == 0 and payload is not None and payload["status"] == "COMPLETED", (code, payload, stderr)
        return payload

    def start(self, source: str, run_id: str, bindings: dict) -> subprocess.Popen:
        """The same launch as its own process group, for a crash or a concurrent stream."""
        arguments = self._run_arguments(source, run_id, bindings)
        return subprocess.Popen([sys.executable, "-B", "-m", "synapse", *map(str, arguments)], cwd=REPOSITORY,
                                env={**os.environ, "PYTHONPATH": str(REPOSITORY)}, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)

    def resume(self, run_id: str) -> tuple[int, dict | None, str]:
        return self._cli("resume", "--state-file", self.runs / f"{run_id}.json")

    # -- observations ------------------------------------------------------------
    def history(self, run_id: str) -> list[dict]:
        artifact = json.loads((self.runs / f"{run_id}.json").read_text())
        return artifact["replay_state"]["execution_history"]

    def events(self, run_id: str, kind: str) -> list[dict]:
        return [event for event in self.history(run_id) if event.get("type") == kind]

    def opening(self, run_id: str) -> dict:
        opening, = self.events(run_id, "memory_session_opened")
        return opening

    def world(self) -> dict:
        return json.loads(self.world_path.read_text()) if self.world_path.exists() else {"calls": [], "effects": []}

    def calls(self, name: str) -> list[dict]:
        return [item["args"] for item in self.world()["calls"] if item["tool"] == name]

    def owner(self) -> MemoryOwner:
        return MemoryOwner(self.state, read_only=True)

    def reports(self) -> list[dict]:
        return [item["report"] for item in self.owner().applied() if item["report"] is not None]

    def journal(self) -> list:
        return self.owner().store.inventory()
