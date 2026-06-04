from synapse import Lexer, Parser, Interpreter


def compile_ast(source):
    return Parser(Lexer(source).scan_tokens()).parse()


def run(source):
    interp = Interpreter()
    interp.source_code = source
    interp.interpret(compile_ast(source))
    return interp


def test_imprint_inline_pad_and_filter_negative():
    source = '''
memory palace "AgentMemory" { rooms { episodic semantic procedural } backend sqlite bind palace }
imprint into palace.episodic {
    content "Deployment failed at step 3"
    confidence 0.99
    source "plan_weave"
    affective_tag { valence -0.9 arousal 0.8 dominance -0.4 }
    affective_decay 7 days
    bind bad_id
}
imprint into palace.episodic {
    content "Deployment succeeded"
    confidence 0.95
    source "plan_weave"
    affective_tag { valence 0.7 arousal 0.2 dominance 0.6 }
    affective_decay never
    bind good_id
}
recall from palace.episodic {
    query "Deployment"
    affective_filter valence < -0.5
    limit 5
    bind bad_memories
}
'''
    interp = run(source)
    mems = interp.global_env.get("bad_memories")
    assert len(mems) == 1
    assert "failed" in mems[0]["content"]
    event = next(e for e in interp.execution_history if e.get("type") == "memory_imprinted" and e.get("imprint_id") == interp.global_env.get("bad_id"))
    assert event["affective_tag_snapshot"]["valence"] == -0.9
    assert event["affective_decay_original"] == "7 days"
    assert isinstance(event["affective_expires_at_event"], int)


def test_affective_event_reference_and_sort():
    source = '''
memory palace "AgentMemory" { rooms { episodic semantic procedural } backend sqlite bind palace }
affective state "AgentMood" { baseline { valence 0.0 arousal 0.0 dominance 0.0 } bind mood }
affective event "critical_failure" { valence -0.6 arousal 0.9 dominance -0.2 bind failure_tag }
imprint into palace.episodic { content "minor issue" confidence 0.9 affective_tag { valence -0.2 arousal 0.1 dominance 0.0 } bind one }
imprint into palace.episodic { content "critical issue" confidence 0.9 affective_tag failure_tag bind two }
recall from palace.episodic {
    query "issue"
    affective_filter tagged
    affective_sort arousal desc
    limit 2
    bind vivid
}
'''
    interp = run(source)
    vivid = interp.global_env.get("vivid")
    assert vivid[0]["content"] == "critical issue"
    assert vivid[0]["affective_tag_id"]


def test_affective_decay_expires_but_memory_remains():
    source = '''
memory palace "AgentMemory" { rooms { episodic semantic procedural } backend sqlite bind palace }
imprint into palace.episodic {
    content "short lived affect"
    confidence 0.9
    affective_tag { valence -0.7 arousal 0.5 dominance -0.1 }
    affective_decay 1 events
    bind mem_id
}
print("advance")
recall from palace.episodic {
    query "affect"
    affective_filter untagged
    limit 5
    bind neutralized
}
'''
    interp = run(source)
    neutralized = interp.global_env.get("neutralized")
    assert len(neutralized) == 1
    assert neutralized[0].get("affective_tag") is None
    assert any(e.get("type") == "memory_affective_tag_expired" for e in interp.execution_history)


def test_consolidate_affective_routing_promotes_danger_pattern():
    source = '''
memory palace "AgentMemory" { rooms { episodic semantic procedural } backend sqlite bind palace }
imprint into palace.episodic {
    content "rollback danger pattern"
    confidence 0.95
    affective_tag { valence -0.8 arousal 0.9 dominance -0.3 }
    bind danger_id
}
consolidate palace {
    rooms ["episodic"]
    affective_routing {
        when valence < -0.3 and arousal > 0.6 {
            promote_to semantic
            tag "danger_pattern"
        }
    }
    bind consolidation
}
recall from palace.semantic { query "rollback" limit 5 bind semantic_hits }
'''
    interp = run(source)
    result = interp.global_env.get("consolidation")
    assert result["promoted"]
    assert len(interp.global_env.get("semantic_hits")) == 1
    assert any(e.get("type") == "memory_consolidated" and e.get("promoted") for e in interp.execution_history)
