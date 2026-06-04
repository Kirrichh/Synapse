import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from synapse import Lexer, Parser, Interpreter, compile_to_ast
from synapse.lexer import TokenType
from synapse.ast import MigrateStmt
from synapse.interpreter import Suspension, RuntimeMode
from synapsed import SwarmNodeDaemon


def parse(src):
    return Parser(Lexer(src).scan_tokens()).parse()


def test_migrate_token_and_parser():
    src = 'migrate "node-b:9000"'
    tokens = Lexer(src).scan_tokens()
    assert TokenType.MIGRATE in [t.type for t in tokens]
    ast = parse(src)
    assert isinstance(ast.statements[0], MigrateStmt)
    assert ast.statements[0].target.value == "node-b:9000"


def test_migrate_yields_durable_suspension_and_event():
    src = '''agent Worker { model "mock" }
let self = Worker
migrate "node-b:9000"'''
    i = Interpreter()
    i.source_code = src
    flow = i.interpret_async(compile_to_ast(src))
    status = next(flow)
    assert isinstance(status, Suspension)
    assert status.reason == "migration_requested"
    assert status.payload["target"] == "node-b:9000"
    assert i.execution_history[-1]["type"] == "migration_requested"


def test_dump_state_mobility_envelope_is_json_safe():
    src = '''agent Worker { model "mock" }
send Worker.process("job")'''
    i = Interpreter()
    i.source_code = src
    i.interpret(parse(src))
    envelope = i.dump_state(source_code=src, actor_name="Worker", target_node="node-b", reason="test")
    assert envelope["type"] == "synapse_mobility_envelope"
    assert envelope["version"] == "1.0.0"
    assert envelope["actor_name"] == "Worker"
    assert envelope["runtime"]["execution_history"]
    import json
    json.dumps(envelope)


def test_load_mobility_envelope_sets_replay_mode():
    src = '''let x = random()
print(x)'''
    i1 = Interpreter()
    i1.source_code = src
    i1.interpret(parse(src))
    envelope = i1.dump_state(source_code=src, actor_name="global", target_node="node-b")
    i2 = Interpreter()
    i2.load_mobility_envelope(envelope)
    assert i2.runtime_mode == RuntimeMode.REPLAY
    assert i2.source_code == src
    i2.interpret(parse(i2.source_code))
    assert i2.get_output() == i1.get_output()


def test_location_transparent_remote_send_emits_packet_not_local_mailbox():
    src = '''agent Worker { model "mock" }
send Worker.process("remote-job")'''
    i = Interpreter()
    i.register_route("Worker", "node-b:9000")
    i.interpret(parse(src))
    assert i.mailboxes.get("Worker") is None
    assert i.outbound_packets[0]["type"] == "forward_message"
    assert i.outbound_packets[0]["target_actor"] == "Worker"
    assert i.execution_history[-1]["type"] == "message_forwarded"


def test_swarm_daemon_accepts_migration_and_forwarded_message():
    src = '''agent Worker { model "mock" }
receive { sender => msg { print(msg.payload) } }'''
    i = Interpreter()
    envelope = i.dump_state(source_code=src, actor_name="Worker", target_node="node-b")
    daemon = SwarmNodeDaemon(node_id="node-b")
    result = daemon.accept_migration({"type": "migrate_actor", "envelope": envelope})
    assert result["status"] == "accepted"
    msg = {"sender": "global", "receiver": "Worker", "method": "process", "args": ["payload"], "payload": "payload"}
    delivered = daemon.accept_forwarded_message({"type": "forward_message", "target_actor": "Worker", "message": msg})
    assert delivered["status"] == "delivered"
    assert daemon.local_actors["Worker"].mailboxes["Worker"][0]["payload"] == "payload"


if __name__ == "__main__":
    test_migrate_token_and_parser()
    test_migrate_yields_durable_suspension_and_event()
    test_dump_state_mobility_envelope_is_json_safe()
    test_load_mobility_envelope_sets_replay_mode()
    test_location_transparent_remote_send_emits_packet_not_local_mailbox()
    test_swarm_daemon_accepts_migration_and_forwarded_message()
    print("All swarm mobility tests passed!")
