"""Public Mini input and terminal proposal capabilities.

The worker proposes changes; C1 owns repository effects. A private-information
invocation never receives a general shell capability. These declarations contain
no repository observations, local knowledge, SDK state or provider credentials.
"""

from .input_contract import SPLIT_INPUT_PROFILE_V1, WorkerInputViolation
from .local_edits import LOCAL_EDIT_INSTRUCTIONS, LOCAL_EDIT_PROFILES

MINI_EFFECT_POLICY_V1 = "synapse.worker.proposal-effects/v1"
PUBLIC_TASK_TEMPLATE = "{{ task }}"
SUBMIT_COMMAND = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
SPLIT_INPUT_INSTRUCTIONS = (
    "This invocation has a separate local information input. It grants no shell, "
    "filesystem or network effects. Only a terminal completion declaration is "
    "supported in this input profile. Use the bash tool with exactly "
    + SUBMIT_COMMAND + " to finish without a candidate. Code changes require the "
    "declared local text-edit proposal profile and independent C1 verification."
)


def public_input_messages(task: str, profile: str) -> list[dict]:
    if type(task) is not str or not task:
        raise WorkerInputViolation("public task must be nonempty text")
    if profile not in {SPLIT_INPUT_PROFILE_V1, *LOCAL_EDIT_PROFILES}:
        raise WorkerInputViolation("unknown public input profile")
    instructions = LOCAL_EDIT_INSTRUCTIONS if profile in LOCAL_EDIT_PROFILES else SPLIT_INPUT_INSTRUCTIONS
    # Mini's fixed Jinja rendering drops one literal final newline from its
    # system template. Bind that exact public wire value before the child starts.
    instructions = instructions.removesuffix("\n")
    return [{"role": "system", "content": instructions}, {"role": "user", "content": task}]


def require_terminal_declaration(action: dict) -> None:
    """Validate data only, before Mini can dispatch any external operation."""
    if (type(action) is not dict or set(action) - {"command", "tool_call_id"}
            or action.get("command") != SUBMIT_COMMAND):
        raise WorkerInputViolation("the input profile grants no external action")
