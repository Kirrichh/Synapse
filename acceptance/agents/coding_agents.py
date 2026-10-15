"""Acceptance-only pluggable coding agents over Synapse's STDIO agent protocol.

Neither agent is part of Synapse. Each is an ordinary admitted STDIO profile
selected through the agent registry, exactly like any operator's agent:

* the process agent makes no model call and applies a scripted outcome queue;
* the model agent reaches its model only through Synapse's model broker. With
  local information it returns its model's raw local-edit proposal for Synapse
  to interpret; without it (the Baseline arm) it applies that proposal itself.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys

from synapse.agents.codec import digest
from synapse.agents.configuration import AGENT_CONFIGURATION_V1, capture_definition
from synapse.agents.contracts import AgentProfile, AgentTransportKind, LocalInformationPolicy
from synapse.agents.model_broker import MODEL_BROKER_PROFILE, model_connection
from synapse.agents.outputs import PATCH_CANDIDATE_OUTPUT_V1
from synapse.agents.policy import IsolationKind, ResourceBudget, RuntimePolicy
from synapse.agents.registry import AgentRegistry, CapabilityAdmission
from synapse.agents.stdio_adapter import StdioAgentAdapter, StdioAgentConfig


ACCEPTANCE_PROVIDER = "acceptance-agent"
CREDENTIAL_ENV = "SYNAPSE_ACCEPTANCE_PROVIDER_KEY"
COMPLETE = "COMPLETE"
_SCENARIO = "SYNAPSE_ACCEPTANCE_SCENARIO"
_MAX_STEPS = "SYNAPSE_ACCEPTANCE_MAX_STEPS"

_COMMON = r'''
import base64, json, os, subprocess, sys


def read_request():
    size = int.from_bytes(sys.stdin.buffer.read(4), "big")
    return json.loads(sys.stdin.buffer.read(size))


def respond(request, status, candidate=None, *, usage=None, diagnostics=None):
    outputs = [] if candidate is None else [{"output_schema": "synapse.agent.output.patch-candidate/v1",
        "media_type": "application/vnd.synapse.patch-candidate+json",
        "payload_base64url": base64.urlsafe_b64encode(json.dumps(candidate, sort_keys=True,
            separators=(",", ":")).encode()).rstrip(b"=").decode()}]
    value = {"schema_version": "synapse.agent.stdio-response/v1", "invocation_id": request["invocation_id"],
        "status": status, "outputs": outputs, "usage": usage or {"token_status": "UNAVAILABLE",
            "input_tokens": None, "output_tokens": None, "thinking_tokens": None, "total_tokens": None,
            "thinking_included": False, "diagnostics": {}},
        "diagnostics": diagnostics or {}, "report": {"summary": None, "failure_reason": None}}
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    sys.stdout.buffer.write(len(raw).to_bytes(4, "big") + raw)
    sys.stdout.buffer.flush()


def candidate(status, *, diff=None, touched=(), diagnostics=None, summary=None):
    return {"status": status, "diff_text": diff, "touched_files": list(touched), "diagnostics": diagnostics or {},
            "report": {"summary": summary, "failure_reason": None}}


def workspace_diff():
    diff = subprocess.run(["git", "diff", "--no-ext-diff"], capture_output=True, text=True, check=True).stdout
    names = subprocess.run(["git", "diff", "--name-only"], capture_output=True, text=True, check=True).stdout.split()
    return diff or None, tuple(sorted(names))
'''

_PROCESS_AGENT = _COMMON + r'''
request = read_request()
path = os.environ["SYNAPSE_ACCEPTANCE_SCENARIO"]
with open(path, encoding="utf-8") as stream:
    state = json.load(stream)
outcome = state["outcomes"][min(state["calls"], len(state["outcomes"]) - 1)]
state["calls"] += 1
with open(path, "w", encoding="utf-8") as stream:
    json.dump(state, stream, sort_keys=True)
if state.get("record_path"):
    # Record exactly what this agent received, for delivery acceptance.
    with open(state["record_path"], "w", encoding="utf-8") as stream:
        stream.write(request["task"]["text"])
    information = request["local_information"]
    with open(state["record_path"][:-len(".txt")] + ".information.json", "wb") as stream:
        stream.write(b"" if information is None else information["text"].encode("utf-8"))
if outcome == "ERROR":
    raise SystemExit(7)
usage = {"token_status": "PROVIDER_REPORTED", "input_tokens": None, "output_tokens": None, "thinking_tokens": None,
         "total_tokens": state["total_tokens"], "thinking_included": False, "diagnostics": {}}
if outcome == "PATCH":
    with open(os.path.join("src", "calc.py"), "w", encoding="utf-8") as stream:
        stream.write(state["patch_source"])
    diff, touched = workspace_diff()
    respond(request, "COMPLETED", candidate("PROPOSED_PATCH", diff=diff, touched=touched), usage=usage)
else:
    respond(request, "COMPLETED", candidate("NO_PATCH"), usage=usage)
'''

_MODEL_AGENT = _COMMON + r'''
import urllib.error, urllib.request

LOCAL_EDIT = "synapse-local-edit "
request = read_request()
endpoint, capability = os.environ["SYNAPSE_MODEL_ENDPOINT"], os.environ["SYNAPSE_MODEL_CAPABILITY"]
model = os.environ["SYNAPSE_MODEL_NAME"]


def call(method, path, value=None, logical=None):
    data = None if value is None else json.dumps(value).encode()
    headers = {"Authorization": "Bearer " + capability, "Content-Type": "application/json"}
    if logical is not None:
        headers["X-Synapse-Logical-Call"] = logical
    outbound = urllib.request.Request(endpoint + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(outbound, timeout=60) as response:
            return response.status, json.loads(response.read()), response.headers.get("X-Synapse-Logical-Call")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null"), error.headers.get("X-Synapse-Logical-Call")


private = request["local_information"] is not None
if private:
    _, conversation, _ = call("GET", "/synapse/conversation")
    messages, correction = conversation["messages"], conversation["correction"]
    if os.environ.get("SYNAPSE_ACCEPTANCE_LEAK") == "1":
        messages = messages + [{"role": "user", "content": request["local_information"]["text"]}]
else:
    messages = [{"role": "system", "content": "Reply with one synapse-local-edit proposal line or COMPLETE."},
                {"role": "user", "content": request["task"]["text"]}]
    correction = {"role": "user", "content": "Reply with one synapse-local-edit proposal line or COMPLETE."}
responses, proposal, failure = [], None, None
for _ in range(int(os.environ.get("SYNAPSE_ACCEPTANCE_MAX_STEPS", "3"))):
    logical = None
    for _attempt in range(3):  # This agent's own retry policy; each retry is a physical call.
        status, body, logical = call("POST", "/v1/chat/completions", {"model": model, "messages": messages}, logical)
        if status not in (429, 500, 502, 503):
            break
    if status != 200:
        failure = "provider_status_" + str(status)
        break
    reply = body["choices"][0]["message"]
    responses.append({"logical_call_id": logical, "usage": body.get("usage")})
    content = (reply.get("content") or "").strip()
    if content.startswith(LOCAL_EDIT):
        proposal = content
        break
    if content == "COMPLETE":
        # Nothing to change: the protocol's explicit empty proposal.
        proposal = LOCAL_EDIT + json.dumps({"schema_version": "synapse.worker.local-edit-proposal/v1",
                                            "alternatives": []})
        break
    messages = messages + [reply, correction]
totals = [item["usage"].get("total_tokens") if type(item["usage"]) is dict else None for item in responses]
usage = None if None in totals else {"token_status": "PROVIDER_REPORTED", "input_tokens": None,
    "output_tokens": None, "thinking_tokens": None, "total_tokens": sum(totals),
    "thinking_included": False, "diagnostics": {}}
diagnostics = {"response_inventory": {"schema_version": "synapse.agent.response-inventory/v1",
    "worker_profile": "synapse.agent.model-broker/v1", "declared_calls": len(responses), "responses": responses}}
if failure is not None:
    respond(request, "ERROR", usage=usage, diagnostics={**diagnostics, "failure_code": failure})
elif private:
    # Synapse, not this agent, interprets the proposal over the delivered bytes.
    respond(request, "COMPLETED", candidate("NO_PATCH", diagnostics={} if proposal is None else
        {"local_edit_proposal": proposal}, summary="LOCAL_EDIT_PROPOSAL"), usage=usage, diagnostics=diagnostics)
else:
    if proposal is not None:
        alternatives = json.loads(proposal[len(LOCAL_EDIT):])["alternatives"]
        for edit in (alternatives[0]["edits"] if alternatives else ()):
            with open(edit["path"], encoding="utf-8") as stream:
                text = stream.read()
            if edit["old"] in text:
                with open(edit["path"], "w", encoding="utf-8") as stream:
                    stream.write(text.replace(edit["old"], edit["new"], 1))
    diff, touched = workspace_diff()
    respond(request, "COMPLETED", candidate("PROPOSED_PATCH" if diff else "NO_PATCH", diff=diff, touched=touched),
            usage=usage, diagnostics=diagnostics)
'''


def _profile(*, profile_id: str, model: str, network: str, timeout_seconds: int) -> AgentProfile:
    return AgentProfile(profile_id=profile_id, agent_id="acceptance-coding-agent", agent_version="1",
        adapter_id="synapse-stdio", adapter_version="1", transport=AgentTransportKind.STDIO,
        capabilities=("repository.edit",), accepted_media_types=(), output_profiles=(PATCH_CANDIDATE_OUTPUT_V1,),
        local_information_policy=LocalInformationPolicy.LOCAL_ONLY, effect_classes=("PATH_MODIFIED",),
        provider_name=ACCEPTANCE_PROVIDER, model_name=model,
        runtime_policy=RuntimePolicy(isolation=IsolationKind.TRUSTED_PROCESS, network=network, writable_workspace=True),
        resource_limits=ResourceBudget(timeout_seconds=timeout_seconds, cpu_seconds=timeout_seconds))


def _admitted(root: Path, *, profile: AgentProfile, native: dict) -> dict:
    """The operator's explicit admission of this acceptance profile."""
    root.mkdir(parents=True, exist_ok=True)
    evidence = root / "admission-evidence.json"
    evidence.write_text(json.dumps({"authority": "acceptance scenario only", "profile": profile.profile_id}))
    return capture_definition(factory="stdio", profile=profile, native=native, distributions=(),
        evidence_paths=(evidence,), capabilities=("repository.edit",))


def agents_configuration(*definitions: dict) -> dict:
    return {"schema_version": AGENT_CONFIGURATION_V1, "profiles": list(definitions), "preferred_profiles": []}


def _registry(adapter: StdioAgentAdapter) -> AgentRegistry:
    return AgentRegistry((adapter,), admissions=(CapabilityAdmission(digest(adapter.profile),
        ("repository.edit",), digest({"authority": "acceptance scenario only"})),))


@dataclass(frozen=True)
class ProcessAgent:
    """Deterministic agent process with a scripted outcome queue and no model."""

    program_path: Path
    scenario_path: Path

    @property
    def calls(self) -> int:
        return json.loads(self.scenario_path.read_text(encoding="utf-8"))["calls"]

    def report_tokens(self, total_tokens: int) -> None:
        state = json.loads(self.scenario_path.read_text(encoding="utf-8"))
        self.scenario_path.write_text(json.dumps({**state, "total_tokens": total_tokens}), encoding="utf-8")

    def native(self) -> dict:
        return {"command": [sys.executable, str(self.program_path)],
                "environment": {_SCENARIO: str(self.scenario_path)}}

    def profile(self, *, model: str, timeout_seconds: int = 30) -> AgentProfile:
        return _profile(profile_id="acceptance-process-agent", model=model, network="NONE",
                        timeout_seconds=timeout_seconds)

    def registry(self, *, model: str) -> AgentRegistry:
        native = self.native()
        profile = self.profile(model=model)
        return _registry(StdioAgentAdapter(StdioAgentConfig(command=tuple(native["command"]),
            timeout_seconds=profile.resource_limits.timeout_seconds, profile=profile,
            environment=tuple(sorted(native["environment"].items())))))

    def definition(self, *, model: str) -> dict:
        return _admitted(self.program_path.parent / "admission", profile=self.profile(model=model), native=self.native())


def create_process_agent(root: Path, *, outcomes: tuple[str, ...], patch_source: str,
                         record_path: Path | None = None) -> ProcessAgent:
    if not outcomes or any(item not in {"PATCH", "NO_PATCH", "ERROR"} for item in outcomes):
        raise ValueError("agent outcomes must use the acceptance vocabulary")
    root.mkdir(parents=True, exist_ok=True)
    program, scenario = root / "process_agent.py", root / "process_agent_scenario.json"
    program.write_text(_PROCESS_AGENT, encoding="utf-8")
    if record_path is not None and record_path.suffix != ".txt":
        raise ValueError("the delivery record is a .txt path")
    scenario.write_text(json.dumps({"calls": 0, "outcomes": list(outcomes), "patch_source": patch_source,
        "total_tokens": 0, "record_path": None if record_path is None else str(record_path)}), encoding="utf-8")
    return ProcessAgent(program_path=program, scenario_path=scenario)


def _model_native(root: Path, *, endpoint: str, model: str, protocol: str | None, timeout_seconds: int,
                  max_steps: int, environment: dict | None, credential_env: str = CREDENTIAL_ENV) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    program = root / "model_agent.py"
    program.write_text(_MODEL_AGENT, encoding="utf-8")
    native = {"command": [sys.executable, str(program)],
              "environment": {_MAX_STEPS: str(max_steps), **(environment or {})},
              "model_access": {"profile": MODEL_BROKER_PROFILE, "model": model, "endpoint": endpoint,
                               "credential_env": credential_env, "timeout_seconds": min(60, timeout_seconds)}}
    if protocol is not None:
        native["protocol"] = protocol
    return native


def model_agent_definition(root: Path, *, endpoint: str, model: str = "gpt-4o-mini", protocol: str | None = None,
                           timeout_seconds: int = 60, max_steps: int = 3, environment: dict | None = None,
                           credential_env: str = CREDENTIAL_ENV) -> dict:
    native = _model_native(root, endpoint=endpoint, model=model, protocol=protocol, timeout_seconds=timeout_seconds,
        max_steps=max_steps, environment=environment, credential_env=credential_env)
    return _admitted(root / "admission", native=native, profile=_profile(profile_id="acceptance-model-agent",
        model=model, network="LOCAL_BROKER", timeout_seconds=timeout_seconds))


def model_agent_registry(root: Path, *, endpoint: str, accounting, model: str = "gpt-4o-mini",
                         protocol: str | None = None, timeout_seconds: int = 45, max_steps: int = 3,
                         environment: dict | None = None) -> AgentRegistry:
    """The same admitted model agent, bound directly for boundary-level acceptance."""
    native = _model_native(root, endpoint=endpoint, model=model, protocol=protocol,
        timeout_seconds=timeout_seconds, max_steps=max_steps, environment=environment)
    profile = _profile(profile_id="acceptance-model-agent", model=model, network="LOCAL_BROKER",
                       timeout_seconds=timeout_seconds)
    return _registry(StdioAgentAdapter(StdioAgentConfig(command=tuple(native["command"]),
        timeout_seconds=timeout_seconds, profile=profile, environment=tuple(sorted(native["environment"].items())),
        protocol=protocol, model_access=model_connection(native["model_access"])), accounting=accounting))


__all__ = ["ACCEPTANCE_PROVIDER", "COMPLETE", "CREDENTIAL_ENV", "ProcessAgent", "agents_configuration",
           "create_process_agent", "model_agent_definition", "model_agent_registry"]


def use_model_agent(declaration: dict, root: Path, *, endpoint: str, protocol: str | None = None,
                    model: str = "gpt-4o-mini", timeout_seconds: int = 60, max_steps: int = 3) -> dict:
    """Select the admitted model agent for one Gold declaration (experiment input v4).

    Gold delivers local information, so an agent reaching a model speaks a
    Synapse protocol; without an explicit choice it is v1, which grants no
    additional run decision.
    """
    from synapse.experiments.gold.run_inputs import EXPERIMENT_INPUT_SCHEMA_V4
    from synapse.worker.local_edits import LOCAL_EDIT_PROFILE_V1
    protocol = protocol or LOCAL_EDIT_PROFILE_V1
    declaration.pop("worker", None)
    declaration["schema_version"] = EXPERIMENT_INPUT_SCHEMA_V4
    declaration["config"]["model"] = model
    declaration["agents"] = agents_configuration(model_agent_definition(root, endpoint=endpoint, model=model,
        protocol=protocol, timeout_seconds=timeout_seconds, max_steps=max_steps))
    return declaration
