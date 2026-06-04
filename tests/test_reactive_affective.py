from synapse import Lexer, Parser, Interpreter, ThresholdPurityViolation


def compile_ast(source):
    return Parser(Lexer(source).scan_tokens()).parse()


def run(source):
    interp = Interpreter()
    interp.source_code = source
    interp.interpret(compile_ast(source))
    return interp


def test_threshold_triggers_after_stable_events_and_suspend_allowed():
    source = '''
affective state "Mood" { baseline { valence -0.5 arousal 0.8 dominance 0.3 } bind mood }
affective threshold "HighStress" {
    when arousal > 0.7 and valence < -0.4
    for 2 events
    cooldown 10 events
    priority high
    action {
        suspend emergency_pause("high_stress_detected")
    }
}
affective event "stress1" { arousal 0.0 bind stress_tag1 }
affective event "stress2" { arousal 0.0 bind stress_tag2 }
'''
    interp = run(source)
    triggers = [e for e in interp.execution_history if e.get("type") == "affective_threshold_triggered"]
    assert len(triggers) == 1
    assert triggers[0]["threshold"] == "HighStress"
    assert triggers[0]["stable_for_events"] == 2
    assert any(e.get("type") == "threshold_suspend_requested" for e in interp.execution_history)


def test_threshold_cooldown_prevents_repeated_trigger():
    source = '''
affective state "Mood" { baseline { valence -0.5 arousal 0.8 dominance 0.3 } bind mood }
affective threshold "HighStress" {
    when arousal > 0.7 and valence < -0.4
    for 1 events
    cooldown 3 events
    action { suspend emergency_pause("pause") }
}
affective event "stress1" { arousal 0.0 bind a }
affective event "stress2" { arousal 0.0 bind b }
affective event "stress3" { arousal 0.0 bind c }
'''
    interp = run(source)
    triggers = [e for e in interp.execution_history if e.get("type") == "affective_threshold_triggered"]
    assert len(triggers) == 1


def test_priority_ordering_for_concurrent_thresholds():
    source = '''
affective state "Mood" { baseline { valence -0.8 arousal 0.95 dominance 0.2 } bind mood }
affective threshold "MediumStress" {
    when arousal > 0.7
    priority medium
    action { suspend emergency_pause("medium") }
}
affective threshold "CriticalStress" {
    when arousal > 0.7
    priority critical
    action { suspend emergency_pause("critical") }
}
affective event "stress" { arousal 0.0 bind stress_tag }
'''
    interp = run(source)
    triggers = [e for e in interp.execution_history if e.get("type") == "affective_threshold_triggered"]
    assert [t["threshold"] for t in triggers[:2]] == ["CriticalStress", "MediumStress"]


def test_threshold_purity_blocks_send_declare_intent_and_imprint():
    cases = [
        'send Worker.process("bad")',
        'declare intent payment',
        'imprint into palace.episodic { content "bad" bind id }',
    ]
    for body in cases:
        source = f'''
affective threshold "Bad" {{
    when arousal > 0.1
    action {{ {body} }}
}}
'''
        try:
            run(source)
            assert False, f"expected ThresholdPurityViolation for {body}"
        except ThresholdPurityViolation:
            pass


def test_policy_guard_mood_snapshot_rejects_and_is_read_only():
    source = '''
affective state "Mood" { baseline { valence -0.6 arousal 0.8 dominance 0.4 } bind mood }
agent Worker { model "mock" }
policy PanicSafety {
    target "Worker.process"
    guard (args) {
        if mood.arousal > 0.7 { reject "panic" }
    }
}
send Worker.process("x")
'''
    from synapse import PolicyViolationException
    try:
        run(source)
        assert False, "expected PolicyViolationException"
    except PolicyViolationException as e:
        assert "panic" in str(e)

    source_mut = '''
affective state "Mood" { baseline { valence -0.6 arousal 0.8 dominance 0.4 } bind mood }
agent Worker { model "mock" }
policy PanicSafety {
    target "Worker.process"
    guard (args) { mood.valence = 1.0 }
}
send Worker.process("x")
'''
    from synapse import GuardMutationError
    try:
        run(source_mut)
        assert False, "expected GuardMutationError"
    except GuardMutationError:
        pass


def test_affective_weighted_consensus_explicit_bias_and_order_independent():
    source = '''
affective state "Mood" { baseline { valence -0.3 arousal 0.6 dominance 0.7 } bind mood }
agent Guide { model "mock" }
let self = Guide
let r = fracture self into {
    Analyst { return "a" }
    Empath { return "e" }
    Critic { return "c" }
} consensus affective_weighted(mood) {
    Analyst bias dominance * 0.3
    Empath  bias -valence * 0.2
    Critic  bias arousal * 0.2
    Default bias 0.0
}
'''
    interp = run(source)
    events = [e for e in interp.execution_history if e.get("type") == "affective_consensus_computed"]
    assert len(events) == 1
    weights = events[0]["weights"]
    assert abs(sum(weights.values()) - 1.0) < 0.00001
    assert weights["Analyst"] > weights["Critic"] > weights["Empath"]

    source_reordered = source.replace('''    Analyst { return "a" }
    Empath { return "e" }
    Critic { return "c" }''', '''    Critic { return "c" }
    Analyst { return "a" }
    Empath { return "e" }''')
    interp2 = run(source_reordered)
    weights2 = [e for e in interp2.execution_history if e.get("type") == "affective_consensus_computed"][0]["weights"]
    assert weights == weights2


def test_affective_weighted_missing_bias_without_default_raises():
    source = '''
affective state "Mood" { baseline { valence -0.3 arousal 0.6 dominance 0.7 } bind mood }
agent Guide { model "mock" }
let self = Guide
let r = fracture self into {
    Analyst { return "a" }
    Empath { return "e" }
} consensus affective_weighted(mood) {
    Analyst bias dominance * 0.3
}
'''
    from synapse import ConsensusBiasMissingError
    try:
        run(source)
        assert False, "expected ConsensusBiasMissingError"
    except ConsensusBiasMissingError:
        pass


def test_debate_affective_bias_injects_judge_prompt_guidance():
    source = '''
affective state "Mood" { baseline { valence -0.5 arousal 0.8 dominance 0.2 } bind mood }
let decision = debate {
    branch bull { return "go" }
    branch bear { return "stop" }
} judge "neutral" rounds 1 affective_bias(mood)
'''
    interp = run(source)
    event = [e for e in interp.execution_history if e.get("type") == "debate_completed"][-1]
    assert event["affective_bias"] is True
    assert "extra cautious" in event["judge_prompt"]
    assert "low confidence" in event["judge_prompt"]


def test_affective_resonance_atomic_no_intermediate_threshold_trigger():
    source = '''
affective state "Mood" { baseline { valence -0.3 arousal 0.6 dominance 0.5 } decay 0.1 per minute bind mood }
affective threshold "IntermediateArousal" {
    when arousal > 0.7
    for 1 events
    action { suspend emergency_pause("intermediate") }
}
resonate with @user {
    aspects ["emotional_tone"]
    window 10
    bind profile
}
// Force the profile into an anxious tone, then mirror+dampen should be atomic:
// arousal 0.6 + 0.15 - 0.2 = 0.55, so threshold must not see intermediate 0.75.
let profile = {"aspects": {"emotional_tone": {"value": "anxious", "confidence": 1.0}}}
affective resonance with @user {
    mirror emotional_tone
    dampen arousal 0.2
    bind bridge
}
'''
    interp = run(source)
    triggers = [e for e in interp.execution_history if e.get("type") == "affective_threshold_triggered"]
    assert not triggers
    applied = [e for e in interp.execution_history if e.get("type") == "affective_resonance_applied"]
    assert len(applied) == 1
    assert applied[0]["atomic"] is True


def test_affective_resonance_live_replay_same_final_pad():
    from synapse import AffectiveState, RuntimeMode
    from synapse.ast import AffectiveResonanceStmt, Literal

    source = '''
affective state "Mood" { baseline { valence -0.3 arousal 0.6 dominance 0.5 } decay 0.1 per minute bind mood }
let profile = {"aspects": {"emotional_tone": {"value": "anxious", "confidence": 1.0}}}
affective resonance with @user {
    mirror emotional_tone
    regulate valence
    dampen arousal 0.2
    bind bridge
}
'''
    live = run(source)
    event = [e for e in live.execution_history if e.get("type") == "affective_resonance_applied"][-1]
    live_after = event["after"]

    replay = Interpreter()
    replay.affective_states["Mood"] = AffectiveState(name="Mood", baseline={"valence": -0.3, "arousal": 0.6, "dominance": 0.5}, current={"valence": -0.3, "arousal": 0.6, "dominance": 0.5}, decay=0.1)
    replay.execution_history = [event]
    replay.runtime_mode = RuntimeMode.REPLAY
    node = AffectiveResonanceStmt(target=Literal(value="@user"), mirror="emotional_tone", regulate=["valence"], dampen={"arousal": Literal(value=0.2)}, binding="bridge")
    bridge = replay.evaluate_affective_resonance(node, replay.global_env)
    assert bridge["final_pad"] == live_after


def test_affective_resonance_forbidden_in_dream_and_kills_subagent_in_fracture():
    from synapse import AffectiveIsolationViolation
    dream_source = '''
dream {
    affective resonance with @user { mirror emotional_tone bind b }
}
'''
    try:
        run(dream_source)
        assert False, "expected AffectiveIsolationViolation"
    except AffectiveIsolationViolation:
        pass

    fracture_source = '''
agent Guide { model "mock" }
let self = Guide
let r = fracture self into {
    Analyst {
        affective resonance with @user { mirror emotional_tone bind b }
        return "should not happen"
    }
} consensus weighted
'''
    interp = run(fracture_source)
    deaths = [e for e in interp.execution_history if e.get("type") == "subagent_terminated"]
    assert deaths and deaths[-1]["death_type"] == "KILLED_ISOLATION"


def test_affective_resonance_binding_contains_final_pad_and_regulate_noop():
    source = '''
affective state "Mood" { baseline { valence 0.1 arousal 0.4 dominance 0.6 } decay 0.1 per minute bind mood }
let profile = {"aspects": {"emotional_tone": {"value": "neutral", "confidence": 1.0}}}
affective resonance with @user {
    mirror emotional_tone
    regulate valence
    bind emotional_bridge
}
print(emotional_bridge.final_pad.valence)
'''
    interp = run(source)
    assert "0.1" in interp.get_output()
    event = [e for e in interp.execution_history if e.get("type") == "affective_resonance_applied"][-1]
    assert any("noop" in item["name"] for item in event["events_applied"])
