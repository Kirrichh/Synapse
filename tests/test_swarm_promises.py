import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from synapse.interpreter import Interpreter, Suspension
from synapsed import SwarmNodeDaemon


def test_interpreter_emits_remote_resolve_promise_packet():
    i = Interpreter()
    i.node_id = "node-b"
    i.register_promise_owner("promise-4512", "node-a")
    result = i.emit_or_apply_promise_resolution("promise-4512", {"status": "success"})
    assert result["status"] == "forwarded"
    packet = i.outbound_packets[-1]
    assert packet["type"] == "resolve_promise"
    assert packet["version"] == "1.0.0"
    assert packet["target_node"] == "node-a"
    assert packet["promise_id"] == "promise-4512"


def test_daemon_accepts_resolve_promise_for_local_actor():
    daemon = SwarmNodeDaemon(node_id="node-a")
    interp = Interpreter()
    interp.node_id = "node-a"
    interp.promises["promise-4512"] = {"promise_id": "promise-4512", "status": "pending", "reason": "await"}
    daemon.local_actors["Coordinator"] = interp
    daemon.promise_registry["promise-4512"] = "Coordinator"

    packet = {
        "type": "resolve_promise",
        "version": "1.0.0",
        "source_node": "node-b",
        "promise_id": "promise-4512",
        "value": {"status": "success", "data": "AI Response"},
    }
    response = daemon.accept_promise_resolution(packet)
    assert response["status"] == "resolved"
    assert interp.promises["promise-4512"]["status"] == "resolved"
    assert interp.promises["promise-4512"]["result"]["data"] == "AI Response"
    assert any(e.get("type") == "promise_resolved" for e in interp.execution_history)


def test_await_returns_network_resolved_promise_without_suspending():
    source = 'let result = await promise_token\nprint(result)'
    # Build the AwaitExpr path using a variable that points to a DurablePromise record
    from synapse.builtins import DurablePromise
    from synapse import compile_to_ast
    interp = Interpreter()
    interp.global_env.define("promise_token", DurablePromise("promise-net", "network", status="pending"))
    interp.promises["promise-net"] = {"promise_id": "promise-net", "status": "resolved", "result": "remote-ok"}
    flow = interp.interpret_async(compile_to_ast(source))
    try:
        next(flow)
        assert False, "resolved promise should not suspend"
    except StopIteration:
        pass
    assert interp.get_output() == "remote-ok"


def test_daemon_tombstone_forwards_promise_resolution():
    daemon = SwarmNodeDaemon(node_id="node-a")
    daemon.create_promise_tombstone("promise-moved", "node-c")
    response = daemon.accept_promise_resolution({
        "type": "resolve_promise",
        "version": "1.0.0",
        "source_node": "node-b",
        "promise_id": "promise-moved",
        "value": True,
    })
    assert response["status"] == "forwarded"
    assert daemon.forwarded_packets[-1]["target_node"] == "node-c"
    assert daemon.forwarded_packets[-1]["via_tombstone"] == "node-a"


def test_remote_spawn_packet_returns_network_actor_ref_descriptor():
    daemon = SwarmNodeDaemon(node_id="node-gpu")
    response = daemon.accept_remote_spawn({
        "type": "remote_spawn",
        "version": "1.0.0",
        "actor_name": "HeavyAgent",
        "model": "local-llama-70b",
    })
    assert response["status"] == "spawned"
    assert response["process_id"].startswith("HeavyAgent#")
    assert response["node_id"] == "node-gpu"
    assert response["process_id"] in daemon.local_actors


if __name__ == "__main__":
    test_interpreter_emits_remote_resolve_promise_packet()
    test_daemon_accepts_resolve_promise_for_local_actor()
    test_await_returns_network_resolved_promise_without_suspending()
    test_daemon_tombstone_forwards_promise_resolution()
    test_remote_spawn_packet_returns_network_actor_ref_descriptor()
    print("All swarm promise tests passed!")
