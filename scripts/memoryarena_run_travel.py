#!/usr/bin/env python3
"""Run MemoryArena's own ``run_travel.main`` on chosen travel groups.

A pilot or a dry run needs a few groups, not all 270; MemoryArena's runner has
no such option. This loads its checkout, narrows the data its runner reads to
the requested groups and calls the runner unchanged with the given config. The
environment server still serves every group; the evaluation scores the groups
that ran.

``--client gemini-rest`` replaces the model transport only. MemoryArena's
``GeminiClient`` rebuilds the conversation from the agent's messages and drops
the model's function-call turns, so tool results reach the model without the
calls they answer, and the thought signatures current Gemini models require
back are lost. ``GeminiRestClient`` returns every model turn exactly as the
API produced it and answers each turn's calls in one function-response turn.
The agent, its prompts and tools, memory and evaluation stay MemoryArena's.
``MEMORYARENA_GEMINI_ENDPOINT`` points the transport elsewhere for a dry run
(``scripts/memoryarena_oracle_endpoint.py``); the key travels in a header only.
``MEMORYARENA_USAGE_LOG`` names a file that receives one JSON line per call
with the provider's token counts, so usage survives a runner that stops early.
``MEMORYARENA_MIN_INTERVAL`` spaces the starts of calls by that many seconds,
for a key whose per-minute quota is small; a 429 that names a per-day quota is
final, since waiting minutes cannot clear it.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import uuid

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRIED_STATUSES = frozenset({429, 500, 502, 503, 504})
RETRY_DELAYS = (5, 10, 20, 40, 80, 160)


def gemini_rest_client(base):
    """``GeminiRestClient`` over the checkout's own client contract (``base_client``)."""

    class GeminiRestClient(base.BaseModelClient):
        def __init__(self, model_name: str) -> None:
            super().__init__(model_name)
            self.api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not self.api_key:
                raise ValueError("GEMINI_API_KEY or GOOGLE_API_KEY environment variable not set")
            self.total_thought_tokens = 0
            self.calls = 0
            self.retries = 0
            self.last_retries = 0
            self.interval = float(os.environ.get("MEMORYARENA_MIN_INTERVAL") or 0)
            self._last_start = 0.0
            self._turns: dict[str, list] = {}
            self._provider_ids: dict[str, str] = {}

        def _post(self, payload):
            request = json.dumps(payload).encode("utf-8")
            url = os.environ.get("MEMORYARENA_GEMINI_ENDPOINT", GEMINI_ENDPOINT).format(model=self.model_name)
            self.last_retries = 0
            for delay in (*RETRY_DELAYS, None):
                time.sleep(max(0.0, self._last_start + self.interval - time.monotonic()))
                self._last_start = time.monotonic()
                exchange = urllib.request.Request(url, data=request, method="POST", headers={
                    "Content-Type": "application/json", "x-goog-api-key": self.api_key})
                try:
                    with urllib.request.urlopen(exchange, timeout=180) as response:
                        return json.loads(response.read().decode("utf-8"))
                except urllib.error.HTTPError as error:
                    detail = error.read().decode("utf-8", "replace")[:2000]
                    daily = error.code == 429 and "PerDay" in detail
                    if error.code not in RETRIED_STATUSES or delay is None or daily:
                        raise RuntimeError(f"Gemini HTTP {error.code}: {detail}") from None
                except (urllib.error.URLError, TimeoutError) as error:
                    if delay is None:
                        raise RuntimeError(f"Gemini transport failure: {error}") from None
                self.retries += 1
                self.last_retries += 1
                time.sleep(delay)
            raise AssertionError("unreachable")

        def _contents(self, messages):
            system, contents = None, []

            def append(role, parts):
                if contents and contents[-1]["role"] == role == "user":
                    contents[-1]["parts"].extend(parts)
                else:
                    contents.append({"role": role, "parts": parts})

            for message in messages:
                role = message.get("role")
                if role == "system":
                    system = message.get("content")
                elif role == "user":
                    append("user", [{"text": message.get("content") or ""}])
                elif role == "assistant":
                    parts = message.get("gemini_parts")
                    if parts is None:
                        parts = [{"text": message["content"]}] if message.get("content") else []
                        parts += [{"functionCall": {"name": call["function"]["name"],
                                                    "args": json.loads(call["function"]["arguments"])}}
                                  for call in message.get("tool_calls") or ()]
                    if parts:
                        contents.append({"role": "model", "parts": parts})
                elif role == "tool":
                    response = {"name": message.get("name") or "unknown_function",
                                "response": {"result": message.get("content")}}
                    provider_id = self._provider_ids.get(message.get("tool_call_id"))
                    if provider_id:
                        response["id"] = provider_id
                    append("user", [{"functionResponse": response}])
            return system, contents

        def chat_with_tools(self, messages, tools, temperature: float = 0.0, max_tokens: int = 8192):
            system, contents = self._contents(messages)
            payload = {"contents": contents,
                       "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}}
            if system:
                payload["systemInstruction"] = {"parts": [{"text": system}]}
            declarations = [{"name": tool["function"]["name"], "description": tool["function"].get("description", ""),
                             "parameters": tool["function"].get("parameters", {})}
                            for tool in tools or () if tool.get("type") == "function"]
            if declarations:
                payload["tools"] = [{"functionDeclarations": declarations}]
                payload["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
            reply = self._post(payload)
            usage = reply.get("usageMetadata") or {}
            self.calls += 1
            self.total_input_tokens += usage.get("promptTokenCount") or 0
            self.total_output_tokens += usage.get("candidatesTokenCount") or 0
            self.total_thought_tokens += usage.get("thoughtsTokenCount") or 0
            if os.environ.get("MEMORYARENA_USAGE_LOG"):
                with open(os.environ["MEMORYARENA_USAGE_LOG"], "a", encoding="utf-8") as log:
                    log.write(json.dumps({"model": self.model_name, "input": usage.get("promptTokenCount") or 0,
                                          "output": usage.get("candidatesTokenCount") or 0,
                                          "thoughts": usage.get("thoughtsTokenCount") or 0,
                                          "cached": usage.get("cachedContentTokenCount") or 0,
                                          "retries": self.last_retries}) + "\n")
            candidate = (reply.get("candidates") or [{}])[0]
            parts = (candidate.get("content") or {}).get("parts") or []
            text = "\n".join(part["text"] for part in parts if part.get("text") and not part.get("thought"))
            calls = []
            for part in parts:
                if "functionCall" in part:
                    call = base.ToolCall(id=f"call_{uuid.uuid4().hex[:12]}", name=part["functionCall"]["name"],
                                         arguments=dict(part["functionCall"].get("args") or {}))
                    if part["functionCall"].get("id"):
                        self._provider_ids[call.id] = part["functionCall"]["id"]
                    calls.append(call)
            if calls:
                self._turns[calls[0].id] = parts
            return base.ModelResponse(content=text or None, tool_calls=calls or None, raw_response=reply)

        def format_tool_result(self, tool_call_id, result, name=None):
            return {"role": "tool", "tool_call_id": tool_call_id, "name": name or "unknown_function",
                    "content": result}

        def format_assistant_tool_calls(self, tool_calls):
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": call.id, "type": "function",
                 "function": {"name": call.name, "arguments": json.dumps(call.arguments)}} for call in tool_calls]}
            turn = self._turns.pop(tool_calls[0].id, None) if tool_calls else None
            if turn is not None:
                message["gemini_parts"] = turn
            return message

        def get_usage_stats(self):
            return {"total_input_tokens": self.total_input_tokens, "total_output_tokens": self.total_output_tokens,
                    "total_thought_tokens": self.total_thought_tokens, "calls": self.calls,
                    "retries": self.retries, "total_cost": 0.0}

    return GeminiRestClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("checkout", type=Path, help="a MemoryArena checkout")
    parser.add_argument("config", type=Path, help="a travel planner config (configs/travel_planner_configs)")
    chosen = parser.add_mutually_exclusive_group(required=True)
    chosen.add_argument("--first", type=int, help="run the first N groups")
    chosen.add_argument("--ids", help="run these group ids, comma-separated")
    parser.add_argument("--client", choices=("benchmark", "gemini-rest"), default="benchmark",
                        help="the model transport (default: the benchmark's own client for the model)")
    options = parser.parse_args()
    sys.path.insert(0, str(options.checkout.resolve()))
    import run_travel

    load = run_travel.load_travel_data
    if options.first is not None:
        run_travel.load_travel_data = lambda: load()[:options.first]
    else:
        wanted = {int(item) for item in options.ids.split(",")}
        run_travel.load_travel_data = lambda: [row for row in load() if row["id"] in wanted]
    if options.client == "gemini-rest":
        from agent import travel_planner
        from env.env_systems.travel_planner_env.clients import base_client

        client = gemini_rest_client(base_client)
        travel_planner.TravelPlannerAgent._create_client = lambda self, model_name: client(model_name)
    sys.argv = ["run_travel.py", "--config", str(options.config)]
    run_travel.main()


if __name__ == "__main__":
    main()
