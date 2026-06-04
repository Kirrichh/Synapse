from synapse import Lexer, Parser, Interpreter
from synapse.ast import MemoryPalaceDef, ImprintStmt, RecallStmt, IntentionCascadeDef, PlanWeaveStmt, HabitStmt


def compile_ast(source):
    return Parser(Lexer(source).scan_tokens()).parse()


def test_memory_palace_parse_and_runtime():
    source = '''
memory palace "AgentMemory" {
    rooms { episodic semantic procedural }
    decay_policy { episodic -> 30 days semantic -> never procedural -> 90 days }
    consolidate during dream
    backend sqlite
    bind palace
}
imprint into palace.semantic {
    content "User prefers Russian language"
    confidence 0.97
    source "resonate_with_user"
    bind imprint_id
}
recall from palace.semantic {
    query "Russian language"
    threshold 0.4
    limit 3
    bind memories
}
'''
    program = compile_ast(source)
    assert any(isinstance(stmt, MemoryPalaceDef) for stmt in program.statements)
    assert any(isinstance(stmt, ImprintStmt) for stmt in program.statements)
    assert any(isinstance(stmt, RecallStmt) for stmt in program.statements)
    interp = Interpreter()
    interp.interpret(program)
    assert interp.global_env.get("imprint_id").startswith("mem-")
    assert len(interp.global_env.get("memories")) == 1
    assert any(e.get("type") == "memory_imprinted" for e in interp.execution_history)
    assert any(e.get("type") == "memory_recalled" for e in interp.execution_history)


def test_intention_plan_weave_and_habit():
    source = '''
memory palace "AgentMemory" { rooms { episodic semantic procedural } backend sqlite bind palace }
intention cascade "ZeroDowntimeDeploy" {
    mission "Ensure continuous service"
    objective "Migrate database schema"
    task "Create consistent backup"
    action "run pg_dump --consistent"
    bind plan
}
plan weave with [self] under "SharedCollective" {
    intention plan
    checkpoint every 2 steps
    rollback on failure
    timeout 120
    bind execution
}
habit from pattern {
    frequency > 3
    stability > 0.9
    promote_to palace.procedural
    energy_cost 0.3
    bind habit_id
}
'''
    program = compile_ast(source)
    assert any(isinstance(stmt, IntentionCascadeDef) for stmt in program.statements)
    assert any(isinstance(stmt, PlanWeaveStmt) for stmt in program.statements)
    assert any(isinstance(stmt, HabitStmt) for stmt in program.statements)
    interp = Interpreter()
    interp.interpret(program)
    execution = interp.global_env.get("execution")
    assert execution["status"] == "completed"
    assert interp.global_env.get("habit_id").startswith("habit-")
    assert any(e.get("type") == "plan_weave_completed" for e in interp.execution_history)
    assert any(e.get("type") == "habit_formed" for e in interp.execution_history)


def test_memory_state_in_snapshot_and_metrics():
    source = '''
memory palace "AgentMemory" { rooms { episodic semantic procedural } backend sqlite bind palace }
imprint into palace.episodic { content "deployment rollback happened" confidence 0.9 bind id }
'''
    interp = Interpreter()
    interp.interpret(compile_ast(source))
    snap = interp.snapshot()
    assert "memory_palaces" in snap
    metrics = interp.metrics_snapshot()
    assert metrics["memory_palaces_total"] == 1
    assert metrics["memory_imprints_total"] == 1

if __name__ == "__main__":
    test_memory_palace_parse_and_runtime()
    test_intention_plan_weave_and_habit()
    test_memory_state_in_snapshot_and_metrics()
    print("All v1.9 memory palace/intention tests passed!")
