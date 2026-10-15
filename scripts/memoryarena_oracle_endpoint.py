#!/usr/bin/env python3
"""A scripted OpenAI-compatible chat endpoint for a MemoryArena dry run.

It stands in for the agent's model so the benchmark's pipeline can be checked
without a provider: for each traveler it first asks for the destination
cities' restaurants, accommodations and attractions (real tool calls, real
tool results into the scratchpad memory stores), then answers with the
ground-truth plan. Plan quality is therefore not measured — the run proves the
wiring (environment, memory server, parsing, combination, evaluation) and
measures, end to end, whether the earlier travelers a question names reached
the agent's prompt through memory, and how large that memory context was.
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


def completion(model, content=None, tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"id": f"chatcmpl-{uuid.uuid4().hex}", "object": "chat.completion", "created": int(time.time()),
            "model": model, "choices": [{"index": 0, "message": message,
                                         "finish_reason": "tool_calls" if tool_calls else "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


def handler_for(by_query, log_path: Path):
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            messages = body["messages"]
            last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
            prompt = messages[last_user]["content"]
            match = re.search(r"Query: (.*?)\n\nInstructions:", prompt, re.DOTALL)
            case = by_query.get(match.group(1).strip()) if match else None
            if case is None:
                payload = completion(body["model"], content="")
            elif not any(m["role"] == "tool" for m in messages[last_user:]):
                memory = next((m["content"] for m in messages
                               if m["role"] == "user" and m["content"].startswith("Here is relevant context")), "")
                reached = [name for name in case["relevant"] if f"=== {name}'s Plan ===" in memory]
                with lock, log_path.open("a", encoding="utf-8") as log:
                    log.write(json.dumps({"group": case["group"], "round": case["round"],
                                          "relevant": case["relevant"], "reached": reached,
                                          "memory_chars": len(memory)}) + "\n")
                calls = [{"id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                          "function": {"name": tool, "arguments": json.dumps({"city": city})}}
                         for city in cities(case["plans"])
                         for tool in ("RestaurantSearch", "AccommodationSearch", "AttractionSearch")]
                payload = completion(body["model"], tool_calls=calls)
            else:
                payload = completion(body["model"], content=format_person_plan(case["name"], case["plans"]))
            data = json.dumps(payload).encode("utf-8")
            self.send_response(200)
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
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(index(args.dataset), args.log))
    print(f"oracle endpoint on 127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
