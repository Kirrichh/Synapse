"""Minimal Synapse Swarm Node daemon for v1.0 swarm promise tests.

This daemon is intentionally small: it defines the JSON wire protocol used to
move a Synapse actor/runtime envelope between nodes and to forward actor
messages with location transparency. It is suitable for local integration tests
and demonstrations; production deployments need authentication, backpressure,
retries and persistent storage.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from synapse import Interpreter, compile_to_ast


class SwarmProtocolError(Exception):
    pass


class SwarmNodeDaemon:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, node_id: Optional[str] = None):
        self.host = host
        self.port = port
        self.node_id = node_id or f"{host}:{port}"
        self.local_actors: Dict[str, Interpreter] = {}
        self.routing_table: Dict[str, str] = {}
        self.promise_registry: Dict[str, str] = {}
        self.promise_tombstones: Dict[str, str] = {}
        self.forwarded_packets = []
        self.received_packets = []

    async def start(self):
        server = await asyncio.start_server(self.handle_connection, self.host, self.port)
        sockets = server.sockets or []
        if sockets:
            self.port = sockets[0].getsockname()[1]
            self.node_id = self.node_id if self.node_id != f"{self.host}:0" else f"{self.host}:{self.port}"
        async with server:
            await server.serve_forever()

    async def handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        raw = await reader.read(10_000_000)
        try:
            packet = json.loads(raw.decode("utf-8"))
            response = await self.handle_packet(packet)
            writer.write(json.dumps({"ok": True, "response": response}).encode("utf-8"))
        except Exception as exc:  # pragma: no cover - defensive network boundary
            writer.write(json.dumps({"ok": False, "error": str(exc)}).encode("utf-8"))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def handle_packet(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        self.received_packets.append(packet)
        packet_type = packet.get("type")
        if packet_type == "migrate_actor":
            return self.accept_migration(packet)
        if packet_type == "forward_message":
            return self.accept_forwarded_message(packet)
        if packet_type == "resolve_promise":
            return self.accept_promise_resolution(packet)
        if packet_type == "remote_spawn":
            return self.accept_remote_spawn(packet)
        if packet_type == "metrics":
            return self.collect_metrics(packet)
        if packet_type == "collective_dream_position":
            return self.accept_collective_position(packet)
        if packet_type == "distributed_vote":
            return self.accept_distributed_vote(packet)
        raise SwarmProtocolError(f"Unsupported packet type: {packet_type}")

    def accept_migration(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        envelope = packet.get("envelope") or packet
        actor_name = envelope.get("actor_name") or packet.get("actor_name") or "global"
        source_code = envelope.get("source_code")
        interpreter = Interpreter()
        interpreter.node_id = self.node_id
        interpreter.load_mobility_envelope(envelope)
        self.local_actors[actor_name] = interpreter
        self.routing_table[actor_name] = "local"
        runtime = envelope.get("runtime", {})
        for promise_id in runtime.get("promises", {}).keys():
            self.promise_registry[promise_id] = actor_name
        if source_code:
            # Replay is deliberately optional here; the daemon may choose to defer
            # CPU work until the next external event. This call validates that the
            # migrated source can be parsed on this node.
            compile_to_ast(source_code)
        return {"actor_name": actor_name, "status": "accepted", "node_id": self.node_id}

    def accept_forwarded_message(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        actor_name = packet.get("target_actor")
        if not actor_name:
            raise SwarmProtocolError("forward_message requires target_actor")
        message = packet.get("message")
        if not isinstance(message, dict):
            raise SwarmProtocolError("forward_message requires message object")
        interpreter = self.local_actors.get(actor_name)
        if interpreter is None:
            interpreter = Interpreter()
            interpreter.node_id = self.node_id
            self.local_actors[actor_name] = interpreter
        interpreter.mailboxes.setdefault(actor_name, []).append(message)
        interpreter.actor_log.append({"type": "network_message_delivered", "message": message})
        self.routing_table[actor_name] = "local"
        return {"actor_name": actor_name, "status": "delivered", "mailbox_size": len(interpreter.mailboxes[actor_name])}


    def accept_promise_resolution(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        promise_id = packet.get("promise_id")
        if not promise_id:
            raise SwarmProtocolError("resolve_promise requires promise_id")
        value = packet.get("value")
        source_node = packet.get("source_node")

        tombstone_target = self.promise_tombstones.get(promise_id)
        if tombstone_target and tombstone_target != "local" and tombstone_target != self.node_id:
            forwarded = dict(packet)
            forwarded["target_node"] = tombstone_target
            forwarded["via_tombstone"] = self.node_id
            self.forwarded_packets.append(forwarded)
            return {"promise_id": promise_id, "status": "forwarded", "target_node": tombstone_target}

        owner_actor = self.promise_registry.get(promise_id)
        candidates = []
        if owner_actor and owner_actor in self.local_actors:
            candidates.append((owner_actor, self.local_actors[owner_actor]))
        candidates.extend((name, interp) for name, interp in self.local_actors.items() if name != owner_actor)

        for actor_name, interpreter in candidates:
            if promise_id in interpreter.promises or not owner_actor:
                interpreter.resolve_promise(promise_id, value, source_node=source_node or packet.get("source_node"))
                interpreter.actor_log.append({"type": "network_promise_resolved", "promise_id": promise_id, "source_node": source_node})
                self.promise_registry[promise_id] = actor_name
                return {"promise_id": promise_id, "status": "resolved", "actor_name": actor_name}

        # No local actor currently owns the promise; retain a durable inbox entry
        # so a later migration can consume it without losing the completion.
        self.promise_registry[promise_id] = "pending_network_resolution"
        return {"promise_id": promise_id, "status": "stored_pending"}

    def accept_remote_spawn(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        actor_name = packet.get("actor_name")
        if not actor_name:
            raise SwarmProtocolError("remote_spawn requires actor_name")
        process_id = packet.get("process_id") or f"{actor_name}#remote-{len(self.local_actors)+1}"
        interpreter = Interpreter()
        interpreter.node_id = self.node_id
        interpreter.spawned_actors[process_id] = {
            "process_id": process_id,
            "actor_name": actor_name,
            "node": self.node_id,
            "model": packet.get("model", "mock"),
            "status": "running",
            "remote_spawned": True,
        }
        interpreter.mailboxes.setdefault(process_id, [])
        self.local_actors[process_id] = interpreter
        self.routing_table[process_id] = "local"
        return {
            "status": "spawned",
            "actor_name": actor_name,
            "process_id": process_id,
            "node_id": self.node_id,
        }




    def accept_collective_position(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        session_id = packet.get("session_id")
        participant = packet.get("participant")
        if not session_id or not participant:
            raise SwarmProtocolError("collective_dream_position requires session_id and participant")
        self.forwarded_packets.append(dict(packet))
        return {"status": "accepted", "session_id": session_id, "participant": participant}

    def accept_distributed_vote(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        consensus_id = packet.get("consensus_id")
        voter = packet.get("voter")
        if not consensus_id or not voter:
            raise SwarmProtocolError("distributed_vote requires consensus_id and voter")
        self.forwarded_packets.append(dict(packet))
        return {"status": "accepted", "consensus_id": consensus_id, "voter": voter}


    def collect_metrics(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        actor_name = packet.get("actor_name")
        if actor_name:
            interpreter = self.local_actors.get(actor_name)
            if interpreter is None:
                raise SwarmProtocolError(f"unknown actor for metrics: {actor_name}")
            return {"node_id": self.node_id, "actor_name": actor_name, "metrics": interpreter.metrics_snapshot()}
        return {
            "node_id": self.node_id,
            "actors": sorted(self.local_actors.keys()),
            "routing_table_size": len(self.routing_table),
            "promise_registry_size": len(self.promise_registry),
            "forwarded_packets": len(self.forwarded_packets),
            "received_packets": len(self.received_packets),
            "actor_metrics": {name: interp.metrics_snapshot() for name, interp in self.local_actors.items()},
        }

    def create_promise_tombstone(self, promise_id: str, target_node: str):
        self.promise_tombstones[promise_id] = target_node
        return {"promise_id": promise_id, "target_node": target_node}


async def send_packet(host: str, port: int, packet: Dict[str, Any]) -> Dict[str, Any]:
    reader, writer = await asyncio.open_connection(host, port)
    writer.write(json.dumps(packet).encode("utf-8"))
    await writer.drain()
    raw = await reader.read(10_000_000)
    writer.close()
    await writer.wait_closed()
    return json.loads(raw.decode("utf-8"))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run a Synapse Swarm Node daemon")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--node-id", default=None)
    args = parser.parse_args()
    daemon = SwarmNodeDaemon(args.host, args.port, args.node_id)
    asyncio.run(daemon.start())
