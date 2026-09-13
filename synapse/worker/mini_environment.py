"""Mini Environment contract for proposal-only private-information invocations.

No subprocess, file or network executor exists in this environment. Local edit
proposals are interpreted by MiniInformationAgent; application and verification
remain at the existing C1 boundary.
"""

from minisweagent.exceptions import InterruptAgentFlow, Submitted

from .input_contract import WorkerInputViolation
from .mini_protocol import MINI_EFFECT_POLICY_V1, require_terminal_declaration


class MiniProposalEnvironment:
    def __init__(self, **configuration):
        # Stock mini.yaml supplies environment presentation settings. They are
        # not capabilities and must never turn into a process environment.
        self._configuration_keys = tuple(sorted(configuration))

    def get_template_vars(self):
        return {}

    def execute(self, action, **kwargs):
        try:
            if kwargs:
                raise WorkerInputViolation("action overrides are not capabilities")
            require_terminal_declaration(action)
        except WorkerInputViolation:
            raise InterruptAgentFlow({
                "role": "exit", "content": "The input profile grants no external action.",
                "extra": {"exit_status": "LocalInformationBoundary", "submission": ""},
            }) from None
        raise Submitted({
            "role": "exit", "content": "",
            "extra": {"exit_status": "Submitted", "submission": ""},
        })

    def serialize(self):
        return {"info": {"environment": {
            "effect_policy": MINI_EFFECT_POLICY_V1,
            "capabilities": [], "configuration_keys": list(self._configuration_keys),
        }}}
