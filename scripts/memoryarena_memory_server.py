#!/usr/bin/env python3
"""MemoryArena's memory API served by Synapse's lexical candidate channel.

MemoryArena's agents talk to a memory server through three calls
(``memory/client.py``): ``/memory/initialize``, ``/memory/add`` and
``/memory/wrap_user_prompt``. This server answers them for the memory system
``synapse-lexical``: chunks are split into the same 2048-token pieces as
MemoryArena's ``RAGMemorySystem`` and ranked by ``palace-lexical/v2`` through
``PreparedKnowledgeIndex.rank`` — the ranking ``knowledge.search`` uses — so
a comparison with the benchmark's BM25 changes the ranker only. The prompt
shape is the one ``RAGMemorySystem`` returns.

With ``--memoryarena PATH`` the server also answers ``bm25`` and
``long_context`` with MemoryArena's own ``RAGMemorySystem`` and
``LongContextMemorySystem``, loaded from that checkout, so baselines run the
benchmark's code without its other memory systems' dependencies.

Retrieval proposes context; it admits nothing. No model is called here.
"""
from __future__ import annotations

import argparse
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))

from synapse.memory_consolidation.knowledge.prepared import PreparedKnowledgeIndex  # noqa: E402

PIECE_TOKENS = 2048
SYSTEMS = {"synapse-lexical": 3, "synapse-lexical-5": 5}


class SynapseLexicalMemory:
    def __init__(self, top_k: int, tokenizer) -> None:
        self.top_k = top_k
        self.tokenizer = tokenizer
        self.pieces: list[str] = []

    def add_chunk(self, chunk: str) -> None:
        tokens = self.tokenizer.encode(chunk, disallowed_special=())
        pieces = [chunk] if len(tokens) <= PIECE_TOKENS else [
            self.tokenizer.decode(tokens[start:start + PIECE_TOKENS]) for start in range(0, len(tokens), PIECE_TOKENS)]
        self.pieces.extend(piece for piece in pieces if piece.strip())

    def retrieve(self, query: str) -> list[str]:
        versions = {f"m{index:06d}": {"record": {"id": f"m{index:06d}", "subject": "", "property": "", "value": "",
                                                 "conditions": {}, "text": text}}
                    for index, text in enumerate(self.pieces)}
        ranked = PreparedKnowledgeIndex(versions).rank(query, list(versions.values()), self.top_k)
        return [versions[identity]["record"]["text"] for identity in ranked]

    def wrap_user_prompt(self, prompt: str) -> str:
        found = self.retrieve(prompt) if self.pieces else []
        lines = ["<memory_context>", *(f"<memory>{piece}</memory>" for piece in found)]
        if not found:
            lines.append("None")
        lines.append("</memory_context>")
        lines.append(f"User: {prompt}")
        return "\n".join(lines)


def memoryarena_factories(checkout: Path) -> dict:
    def load(relative, attribute):
        spec = importlib.util.spec_from_file_location(f"memoryarena_{Path(relative).stem}", checkout / relative)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return getattr(module, attribute)
    rag = load("memory/memory_systems/rag.py", "RAGMemorySystem")
    long_context = load("memory/memory_systems/long_context.py", "LongContextMemorySystem")
    return {"bm25": lambda: rag(retrieval_method="bm25"), "long_context": long_context}


class Store:
    def __init__(self, tokenizer, baselines: dict) -> None:
        self.tokenizer = tokenizer
        self.lock = threading.Lock()
        self.users: dict[str, tuple[str, object]] = {}
        self.factories = {name: (lambda k=k: SynapseLexicalMemory(k, tokenizer)) for name, k in SYSTEMS.items()}
        self.factories.update(baselines)

    def initialize(self, body):
        name = body["memory_system_name"]
        if name not in self.factories:
            return 400, {"detail": f"Unsupported memory_system: {name}"}
        with self.lock:
            self.users[str(body["user_id"])] = (name, self.factories[name]())
        return 200, {"status": "ok", "user_id": body["user_id"], "memory_system_name": name}

    def memory(self, body):
        entry = self.users.get(str(body["user_id"]))
        if entry is None:
            return None, (404, {"detail": "User not initialized"})
        if entry[0] != body["memory_system_name"]:
            return None, (400, {"detail": "Mismatched memory_system for user"})
        return entry[1], None

    def add(self, body):
        memory, error = self.memory(body)
        if error:
            return error
        with self.lock:
            memory.add_chunk(body["chunk"])
        return 200, {"status": "ok", "user_id": body["user_id"], "response": None}

    def wrap(self, body):
        memory, error = self.memory(body)
        if error:
            return error
        with self.lock:
            prompt = memory.wrap_user_prompt(body["question"])
        return 200, {"status": "ok", "user_id": body["user_id"], "prompt": prompt}


def handler_for(store: Store):
    routes = {"/memory/initialize": store.initialize, "/memory/add": store.add,
              "/memory/wrap_user_prompt": store.wrap}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            route = routes.get(self.path)
            if route is None:
                status, payload = 404, {"detail": "Not Found"}
            else:
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                    status, payload = route(body)
                except (KeyError, TypeError, ValueError) as error:
                    status, payload = 422, {"detail": f"invalid request: {error}"}
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
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
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--memoryarena", type=Path)
    args = parser.parse_args()
    import tiktoken

    baselines = memoryarena_factories(args.memoryarena) if args.memoryarena else {}
    store = Store(tiktoken.encoding_for_model("gpt-4o-mini"), baselines)
    server = ThreadingHTTPServer((args.host, args.port), handler_for(store))
    print(f"synapse memory server on {args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
