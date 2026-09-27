"""ACP SDK integration; filesystem callbacks obey the frozen coding scope."""

import asyncio
from pathlib import Path
import subprocess

from .codec import canonical_bytes
from .contracts import AgentTransportKind, LocalInformationPolicy
from .outputs import PATCH_CANDIDATE_OUTPUT_V1
from .policy import IsolationKind
from .stdio_adapter import StdioAgentAdapter, StdioAgentConfig


class AcpAdapterFactory:
    def create(self, configuration):
        from .configuration import profile_from_dict
        profile = profile_from_dict(configuration["profile"])
        native = configuration["native"]
        if (profile.transport is not AgentTransportKind.ACP
                or profile.local_information_policy is not LocalInformationPolicy.NOT_SUPPORTED
                or profile.output_profiles != (PATCH_CANDIDATE_OUTPUT_V1,)
                or profile.effect_classes != ("PATH_MODIFIED",)
                or not profile.runtime_policy.writable_workspace
                or profile.runtime_policy.isolation not in (IsolationKind.BUBBLEWRAP, IsolationKind.OCI)):
            raise ValueError("ACP coding requires its scoped sandbox profile without private information")
        if set(native) != {"python", "command"} or type(native["command"]) is not list or not native["command"]:
            raise ValueError("ACP requires an explicit native command")
        native = {**native, "agent_id": profile.agent_id, "agent_version": profile.agent_version}
        return StdioAgentAdapter(StdioAgentConfig(
            (native["python"], "-m", "synapse.agents.native_worker", "acp"),
            profile.resource_limits.timeout_seconds, profile,
            tuple(sorted({"SYNAPSE_AGENT_NATIVE_CONFIGURATION": canonical_bytes(native).decode(),
                          "PYTHONPATH": str(Path(__file__).resolve().parents[2])}.items())),
        ))


def execute_acp(request, configuration):
    return asyncio.run(_execute_acp(request, configuration))


async def _execute_acp(request, configuration):
    from acp import Client, spawn_agent_process, text_block
    from acp.schema import (ClientCapabilities, ReadTextFileResponse, WriteTextFileResponse,
                            RequestPermissionResponse, AllowedOutcome, DeniedOutcome)
    from .native_worker import native_response
    if request["local_information"] is not None:
        return native_response(request, "REFUSED", failure="LOCAL_INFORMATION_POLICY_VIOLATION")
    root = Path.cwd().resolve()
    allowed = set(request["allowed_scope"])
    def checked(path, *, write=False):
        target = Path(path)
        target = target if target.is_absolute() else root / target
        if target.is_symlink() or not target.resolve().is_relative_to(root):
            raise PermissionError("ACP path is outside the supplied workspace")
        relative = target.relative_to(root).as_posix()
        if '.git' in Path(relative).parts or (write and (relative not in allowed or not target.is_file())):
            raise PermissionError("ACP effect is outside the frozen modification scope")
        return target
    class ScopedClient(Client):
        async def request_permission(self, session_id, tool_call, options, **kwargs):
            permit = False
            locations = getattr(tool_call, "locations", None) or []
            kind = getattr(tool_call, "kind", None)
            if kind in ("read", "edit") and locations:
                try:
                    for location in locations:
                        checked(location.path, write=kind == "edit")
                    permit = True
                except (PermissionError, ValueError):
                    pass
            option = next((o for o in options if o.kind == "allow_once"), None)
            if permit and option is not None:
                return RequestPermissionResponse(outcome=AllowedOutcome(option_id=option.option_id))
            return RequestPermissionResponse(outcome=DeniedOutcome())

        async def session_update(self, session_id, update, **kwargs):
            # Agent text/plans never become Synapse plans or authority.
            return None

        async def read_text_file(self, session_id, path, line=None, limit=None, **kwargs):
            target = checked(path)
            if target.stat().st_size > 4 * 1024**2:
                raise ValueError("ACP file read exceeds the input limit")
            lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
            start = max(0, (line or 1) - 1)
            return ReadTextFileResponse(content="".join(lines[start:] if limit is None else lines[start:start + limit]))

        async def write_text_file(self, session_id, path, content, **kwargs):
            target = checked(path, write=True)
            if len(content.encode("utf-8")) > 4 * 1024**2:
                raise ValueError("ACP file write exceeds the output limit")
            target.write_text(content, encoding="utf-8")
            return WriteTextFileResponse()

    command = configuration["command"]
    async with spawn_agent_process(ScopedClient(), command[0], *command[1:], cwd=root) as (agent, process):
        initialized = await agent.initialize(protocol_version=1,
            client_capabilities=ClientCapabilities.model_validate({"fs": {"readTextFile": True, "writeTextFile": True}, "terminal": False}))
        info = initialized.agent_info
        if info is None or info.name != configuration["agent_id"] or info.version != configuration["agent_version"]:
            return native_response(request, "REFUSED", failure="UNSUPPORTED_CAPABILITY")
        session = await agent.new_session(cwd=str(root), mcp_servers=[])
        prompt = await agent.prompt(session_id=session.session_id, prompt=[text_block(request["task"]["text"])])
        if prompt.stop_reason != "end_turn":
            return native_response(request, "ERROR", failure="NATIVE_PROTOCOL_ERROR")
    def git(*args):
        return subprocess.run(["git", *args], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    changes = git("diff", "--name-only", "-z").decode("utf-8").split("\0")
    paths = tuple(sorted(filter(None, changes)))
    if not set(paths).issubset(allowed) or git("ls-files", "--others", "--exclude-standard", "-z"):
        return native_response(request, "REFUSED", failure="UNSUPPORTED_CAPABILITY")
    for path in paths:
        checked(path, write=True)
    patch = git("diff", "--binary", "--no-ext-diff").decode("utf-8")
    payload = {"status": "PROPOSED_PATCH" if patch else "NO_PATCH", "diff_text": patch or None,
               "touched_files": list(paths), "diagnostics": {}, "report": {"summary": None, "failure_reason": None}}
    return native_response(request, "COMPLETED", [(PATCH_CANDIDATE_OUTPUT_V1, payload)])
