#!/usr/bin/env python3
"""A scripted model endpoint for a MemoryArena dry run, OpenAI- and Gemini-shaped.

It stands in for the agent's model so the benchmark's pipeline can be checked
without a provider: for each traveler it first asks for the destination
cities' restaurants, accommodations and attractions (real tool calls, real
tool results into the scratchpad memory stores), then answers with the
ground-truth plan. Plan quality is therefore not measured — the run proves the
wiring (environment, memory server, parsing, combination, evaluation) and
measures, end to end, whether the earlier travelers a question names reached
the agent's prompt through memory, and how large that memory context was.

``/v1/chat/completions`` answers the benchmark's OpenAI client.
``/v1beta/models/<model>:generateContent`` answers the Gemini REST transport of
``scripts/memoryarena_run_travel.py`` and refuses, with HTTP 400, the histories
the Gemini API refuses: a function-response turn that does not follow a
function-call turn with as many calls, and a call turn returned without the
thought signature issued with it.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
import time
import uuid

MEMORY_PREFIX = "Here is relevant context from memory"
TOOLS = ("RestaurantSearch", "AccommodationSearch", "AttractionSearch")


def format_person_plan(name, daily_plans):
    """``run_travel.format_person_plan``, copied verbatim."""
    lines = [f"=== {name}'s Plan ==="]
    for day in daily_plans:
        day_idx = day.get('days') or day.get('day')
        lines.append(f"Day {day_idx}:")
        lines.append(f"Current City: {day.get('current_city', '-')}")
        lines.append(f"Transportation: {day.get('transportation', '-')}")
        lines.append(f"Breakfast: {day.get('breakfast', '-')}")
        lines.append(f"Attraction: {day.get('attraction', '-')}")
        lines.append(f"Lunch: {day.get('lunch', '-')}")
        lines.append(f"Dinner: {day.get('dinner', '-')}")
        lines.append(f"Accommodation: {day.get('accommodation', '-')}")
        lines.append("")
    return "\n".join(lines)


def index(dataset: Path):
    by_query = {}
    for line in dataset.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        names = [row["base_person"]["name"]]
        for round_idx, (query, plans) in enumerate(zip(row["questions"], row["answers"]), 1):
            name = re.match(r"I am (\w+)\.", query).group(1)
            body = "\n".join(query.split("\n")[2:])
            relevant = sorted(n for n in names if re.search(r"\b%s\b" % re.escape(n), body))
            by_query[query.strip()] = {"group": row["id"], "round": round_idx, "name": name, "plans": plans,
                                       "relevant": relevant}
            names.append(name)
    return by_query


def cities(plans):
    found = []
    for day in plans:
        city = day.get("current_city", "")
        city = city.rsplit(" to ", 1)[-1] if city.startswith("from ") else city
        if city and city != "-" and city not in found:
            found.append(city)
    return found


def tool_requests(case):
    return [(tool, {"city": city}) for city in cities(case["plans"]) for tool in TOOLS]


def case_of(prompt, by_query):
    match = re.search(r"Query: (.*?)\n\nInstructions:", prompt or "", re.DOTALL)
    return by_query.get(match.group(1).strip()) if match else None


class Oracle:
    def __init__(self, by_query, log_path: Path) -> None:
        self.by_query = by_query
        self.log_path = log_path
        self.lock = threading.Lock()
        self.signatures: set[str] = set()

    def log(self, case, memory: str) -> None:
        reached = [name for name in case["relevant"] if f"=== {name}'s Plan ===" in memory]
        with self.lock, self.log_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps({"group": case["group"], "round": case["round"], "relevant": case["relevant"],
                                  "reached": reached, "memory_chars": len(memory)}) + "\n")

    def openai(self, body):
        messages = body["messages"]
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        case = case_of(messages[last_user]["content"], self.by_query)
        message = {"role": "assistant", "content": ""}
        if case is not None and not any(m["role"] == "tool" for m in messages[last_user:]):
            self.log(case, next((m["content"] for m in messages
                                 if m["role"] == "user" and m["content"].startswith(MEMORY_PREFIX)), ""))
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                 "function": {"name": tool, "arguments": json.dumps(args)}} for tool, args in tool_requests(case)]}
        elif case is not None:
            message["content"] = format_person_plan(case["name"], case["plans"])
        return 200, {"id": f"chatcmpl-{uuid.uuid4().hex}", "object": "chat.completion",
                     "created": int(time.time()), "model": body["model"],
                     "choices": [{"index": 0, "message": message,
                                  "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
                     "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}

    def refusal(self, body):
        """Why the Gemini API would refuse this history, or ``None``."""
        contents = body.get("contents") or []
        for position, turn in enumerate(contents):
            responses = [part for part in turn["parts"] if "functionResponse" in part]
            calls = [part for part in turn["parts"] if "functionCall" in part]
            if responses:
                previous = contents[position - 1] if position else {"role": None, "parts": []}
                issued = [part for part in previous["parts"] if "functionCall" in part]
                if previous["role"] != "model" or len(issued) != len(responses):
                    return ("Please ensure that function response turn comes immediately after a function call "
                            "turn. And the number of function response parts should be equal to number of "
                            "function call parts of the function call turn.")
            if calls and calls[0].get("thoughtSignature") not in self.signatures:
                return "Function call is missing a thought_signature in functionCall parts."
        return None

    def gemini(self, body):
        refused = self.refusal(body)
        if refused:
            return 400, {"error": {"code": 400, "message": refused, "status": "INVALID_ARGUMENT"}}
        contents = body["contents"]
        texts = [(position, part["text"]) for position, turn in enumerate(contents) if turn["role"] == "user"
                 for part in turn["parts"] if "text" in part]
        prompt_at, prompt = texts[-1] if texts else (0, "")
        case = case_of(prompt, self.by_query)
        if case is None:
            parts = [{"text": ""}]
        elif not any(turn["role"] == "model" for turn in contents[prompt_at:]):
            self.log(case, next((text for _, text in texts if text.startswith(MEMORY_PREFIX)), ""))
            signature = uuid.uuid4().hex
            with self.lock:
                self.signatures.add(signature)
            parts = [{"functionCall": {"name": tool, "args": args}} for tool, args in tool_requests(case)]
            parts[0]["thoughtSignature"] = signature
        else:
            parts = [{"text": format_person_plan(case["name"], case["plans"])}]
        return 200, {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}],
                     "usageMetadata": {"promptTokenCount": 0, "candidatesTokenCount": 0, "totalTokenCount": 0}}


def handler_for(oracle: Oracle):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if re.fullmatch(r"/v1beta/models/[^/:]+:generateContent", self.path):
                status, payload = oracle.gemini(body)
            else:
                status, payload = oracle.openai(body)
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8020)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(Oracle(index(args.dataset), args.log)))
    print(f"oracle endpoint on 127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
