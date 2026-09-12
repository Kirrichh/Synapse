"""Installed Mini Agent extension with separate task and information inputs.

The stock CLI, SDK run loop and model remain in use. The declared local edit
profile binds text proposals to local source material without repository effects.
It does not interpret arbitrary prose. Local-to-provider flows without a public
projection terminate explicitly.
"""

import hashlib
import os
from pathlib import Path
import platform
import stat

from minisweagent.agents.interactive import InteractiveAgent
from minisweagent.exceptions import InterruptAgentFlow

from .input_contract import (
    LocalInformationInput, WorkerTaskInput, WorkerInputViolation,
    MAX_WORKER_INPUT_BYTES, SPLIT_INPUT_PROFILE_V1,
)
from .provider_messages import PublicProviderConversation
from .mini_environment import MiniProposalEnvironment
from .mini_protocol import PUBLIC_TASK_TEMPLATE, public_input_messages
from .local_edits import (
    LOCAL_EDIT_PROFILES, parse_local_edit_command, propose_local_edits,
)


def _read_information_input() -> LocalInformationInput:
    if os.environ.get("SYNAPSE_MINI_INPUT_PROFILE") not in {SPLIT_INPUT_PROFILE_V1, *LOCAL_EDIT_PROFILES}:
        raise WorkerInputViolation("Mini information input profile is unavailable")
    path = Path(os.environ["SYNAPSE_MINI_INFORMATION_PATH"])
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if path.is_symlink() or not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise WorkerInputViolation("Mini information input is not a regular file")
        raw = stream.read(MAX_WORKER_INPUT_BYTES + 1)
    information = LocalInformationInput(raw)
    if information.sha256 != os.environ["SYNAPSE_MINI_INFORMATION_SHA256"]:
        raise WorkerInputViolation("Mini information input differs from the dispatched bytes")
    return information


class MiniInformationAgent(InteractiveAgent):
    def __init__(self, model, env, **kwargs):
        super().__init__(model, env, **kwargs)
        if not getattr(model, "requires_split_inputs", False):
            raise WorkerInputViolation("Mini model lacks the separate information profile")
        if type(env) is not MiniProposalEnvironment:
            raise WorkerInputViolation("private information requires the proposal environment")
        self.information_input = _read_information_input()
        self._task_input = None
        self.input_profile = os.environ["SYNAPSE_MINI_INPUT_PROFILE"]
        self.local_edit_result = None
        roots = public_input_messages("public-root-placeholder", self.input_profile)
        self.config.system_template = roots[0]["content"]
        self.config.instance_template = PUBLIC_TASK_TEMPLATE

    def get_template_vars(self, **kwargs):
        # The installed template uses task and platform fields. In particular,
        # process environment, input paths and local history are not template
        # variables. No local value is forwarded as an apparently public task.
        return {**platform.uname()._asdict(),
                "task": "" if self._task_input is None else self._task_input.text,
                **kwargs}

    def run(self, task: str = "", **kwargs):
        if kwargs:
            raise WorkerInputViolation("separate worker inputs cannot be widened by template overrides")
        self._task_input = WorkerTaskInput(task.encode("utf-8"))
        if hashlib.sha256(self._task_input.canonical_bytes).hexdigest() != os.environ["SYNAPSE_MINI_TASK_SHA256"]:
            raise WorkerInputViolation("Mini task differs from the dispatched bytes")
        initial = [self.model.format_message(role="system", content=self._render_template(self.config.system_template)),
                   self.model.format_message(role="user", content=self._render_template(self.config.instance_template))]
        self.model.bind_public_conversation(PublicProviderConversation(initial))
        result = super().run(task)
        if result.get("exit_status") in {"LocalInformationBoundary", "LocalEditRefused"}:
            # Stock Mini returns zero for InterruptAgentFlow. Preserve its
            # saved trajectory, but report an actual worker refusal to Gold.
            raise SystemExit("mini_local_information_boundary" if result["exit_status"] == "LocalInformationBoundary"
                             else "mini_local_edit_refused")
        return result

    def execute_actions(self, message):
        if self.input_profile not in LOCAL_EDIT_PROFILES:
            return super().execute_actions(message)
        try:
            actions = message.get("extra", {}).get("actions", [])
            if type(actions) is not list or len(actions) != 1:
                raise WorkerInputViolation("local proposal requires one complete typed action")
            proposal = parse_local_edit_command(actions[0].get("command"))
            self.local_edit_result = propose_local_edits(
                task=self._task_input, information=self.information_input, proposal=proposal, profile=self.input_profile)
        except (WorkerInputViolation, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            # Terminal local refusal: no shell fallback, partial batch effect,
            # model observation, or exception text derived from private bytes.
            raise InterruptAgentFlow({"role": "exit", "content": "Local edit proposal refused.",
                "extra": {"exit_status": "LocalEditRefused", "submission": ""}}) from None
        raise InterruptAgentFlow({"role": "exit", "content": "Local proposal assessment completed.",
            "extra": {"exit_status": "LocalEditCompleted", "submission": ""}})

    def query(self):
        try:
            # Refusal precedes Mini's n_calls increment, so a blocked message
            # cannot be reported as a provider call with missing usage.
            self.model.require_public_messages(self.messages)
        except WorkerInputViolation:
            raise InterruptAgentFlow({"role": "exit", "content": "Local observation has no permitted provider projection.",
                "extra": {"exit_status": "LocalInformationBoundary", "submission": ""}}) from None
        return super().query()

    def serialize(self, *extra_dicts):
        result = super().serialize(*extra_dicts)
        result["info"]["input_delivery"] = {
            "profile": self.input_profile,
            "task_sha256": None if self._task_input is None else hashlib.sha256(self._task_input.canonical_bytes).hexdigest(),
            "information_sha256": self.information_input.sha256,
            "information_byte_length": len(self.information_input.canonical_bytes),
            "information_item_count": len(self.information_input.to_dict()["items"]),
            "local_interpretation": "NOT_PERFORMED" if self.local_edit_result is None else "LOCAL_TEXT_EDIT_PROPOSALS",
        }
        if self.input_profile in LOCAL_EDIT_PROFILES:
            result["info"]["local_edit_result"] = self.local_edit_result
        return result
