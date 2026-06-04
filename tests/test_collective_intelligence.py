from synapse import compile_to_ast, Interpreter, ResonancePrivacyException
from synapsed import SwarmNodeDaemon


def run(src):
    interp = Interpreter()
    interp.source_code = src
    interp.interpret(compile_to_ast(src))
    return interp


def test_collective_dream_consensus_event_and_signature():
    src = '''
agent Peer { model "mock" }
policy SharedPolicy {
    target "collective.*"
    collective_dream: true
}
let self = Peer
collective dream with [Peer] under "SharedPolicy" {
    scenario "resource conflict"
    converge_on "protocol_v2"
    timeout 300
    bind cd
}
print(cd.status)
'''
    interp = run(src)
    assert "consensus_reached" in interp.get_output()
    events = [e for e in interp.execution_history if e.get("type") == "collective_dream_consensus_reached"]
    assert len(events) == 1
    assert events[0].get("signature")
    assert events[0].get("document_hash")


def test_collective_dream_requires_policy():
    src = '''
agent Peer { model "mock" }
let self = Peer
collective dream with [Peer] {
    scenario "resource conflict"
}
'''
    interp = Interpreter()
    try:
        interp.interpret(compile_to_ast(src))
        assert False, "expected policy violation"
    except Exception as exc:
        assert "collective dream requires" in str(exc)


def test_cross_agent_resonance_privacy_default_private():
    src = '''
agent Guide { model "mock" }
agent Peer { model "mock" }
let self = Guide
resonate with Peer { aspects ["trust_level"] bind rp }
'''
    interp = Interpreter()
    try:
        interp.interpret(compile_to_ast(src))
        assert False, "expected privacy exception"
    except ResonancePrivacyException:
        pass


def test_cross_agent_resonance_with_opt_in_policy():
    src = '''
agent Guide { model "mock" }
agent Peer { model "mock" }
policy PeerReadable {
    target "resonance.Peer"
    resonance_readable: true
}
let self = Guide
resonate with Peer { aspects ["trust_level", "value_alignment"] bind rp }
print(rp.aspects.value_alignment.value)
'''
    interp = run(src)
    assert "0." in interp.get_output() or "1" in interp.get_output()


def test_distributed_consensus_commits_with_quorum():
    src = '''
agent Peer { model "mock" }
let self = Peer
distributed consensus with [Peer] on "deploy_v2" {
    quorum 1
    timeout 30
    policy "MajorityVote"
    bind vote
}
print(vote.committed)
'''
    interp = run(src)
    assert "True" in interp.get_output()
    assert any(e.get("type") == "distributed_consensus_committed" for e in interp.execution_history)


def test_swarm_fracture_policy_gate_and_metrics():
    src = '''
agent Peer { model "mock" }
policy SharedPolicy {
    target "collective.*"
    swarm_fracture: true
}
let self = Peer
swarm fracture with [Peer] under "SharedPolicy" {
    scenario "failure recovery"
    roles { Peer -> Analyst }
    consensus unanimous
    timeout 60
    bind sw
}
print(sw.status)
'''
    interp = run(src)
    assert "consensus_reached" in interp.get_output()
    metrics = interp.metrics_snapshot()
    assert metrics["swarm_fractures_total"] == 1
    assert metrics["collective_events_total"] >= 1


def test_swarm_daemon_collective_packets():
    daemon = SwarmNodeDaemon(node_id="node-test")
    import asyncio
    r1 = asyncio.run(daemon.handle_packet({"type": "collective_dream_position", "session_id": "s1", "participant": "a", "position": "x"}))
    r2 = asyncio.run(daemon.handle_packet({"type": "distributed_vote", "consensus_id": "c1", "voter": "a", "vote": "yes"}))
    assert r1["status"] == "accepted"
    assert r2["status"] == "accepted"
