"""Parent-side provenance check at Mini's actual outgoing HTTP boundary.

The adapter binds public roots before starting Mini. This policy only admits
those roots and continuations derived from actual provider responses using the
installed Mini parser. No child endpoint can register or widen the public roots.
"""

import json
from contextlib import redirect_stdout
import sys

from .input_contract import WorkerInputViolation
from .mini_protocol import public_input_messages
from .provider_messages import PublicProviderConversation


class MiniPublicRequestPolicy:
    def __init__(self, *, task, input_profile, model, configuration_path):
        import yaml
        # Mini prints a startup banner when imported as a library. Keep
        # diagnostics on stderr so the parent application's JSON API stays exact.
        with redirect_stdout(sys.stderr):
            from minisweagent.models.utils.actions_toolcall import BASH_TOOL

        self._conversation = PublicProviderConversation(public_input_messages(task, input_profile), provider_wire=True)
        # The pinned LiteLLM OpenAI adapter omits stream=False on the wire.
        self._request_fields = {"model": model, "tools": [BASH_TOOL]}
        with open(configuration_path, encoding="utf-8") as stream:
            configuration = yaml.safe_load(stream)
        self._format_error_template = configuration["model"]["format_error_template"]

    def require_request(self, value):
        if type(value) is not dict or "messages" not in value:
            raise WorkerInputViolation("provider request has no public conversation")
        fields = {key: item for key, item in value.items() if key != "messages"}
        # Canonical bytes distinguish bools from integers, and reject any
        # unbound extra field as well as modifications to configured tools.
        if json.dumps(fields, sort_keys=True, allow_nan=False) != json.dumps(
                self._request_fields, sort_keys=True, allow_nan=False):
            raise WorkerInputViolation("provider request fields differ from the public profile")
        self._conversation.require_public(value["messages"])

    def observe_response(self, body):
        """Only a captured successful provider response can extend the history."""
        from litellm import ModelResponse
        from minisweagent.exceptions import FormatError
        from minisweagent.models.utils.actions_toolcall import parse_toolcall_actions

        value = json.loads(body)
        response = ModelResponse(**value)
        if len(response.choices) != 1:
            raise WorkerInputViolation("provider response is outside the single-choice profile")
        choice = response.choices[0]
        try:
            parse_toolcall_actions(
                choice.message.tool_calls or [],
                format_error_template=self._format_error_template,
                template_kwargs={"finish_reason": choice.finish_reason},
            )
        except FormatError as exc:
            self._conversation.record_provider_messages(*exc.messages)
        else:
            self._conversation.record_provider_messages(choice.message.model_dump())
