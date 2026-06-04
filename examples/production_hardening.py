from synapse import Interpreter, SQLiteStorage, RuntimeStressHarness, compile_to_ast

source = '''
agent Guide {
  model "mock"
  soulprint {
    values: [ clarity: 0.8, caution: 0.9 ]
    memory: long_term
    style: "operational"
  }
}
let self = Guide
print("production hardening ready")
'''

interp = Interpreter().attach_storage(SQLiteStorage("/tmp/synapse_v1_7_demo.db"), run_id="demo")
interp.source_code = source
interp.interpret(compile_to_ast(source))
interp.save_runtime_state()
print(interp.metrics_text())
print(RuntimeStressHarness(seed=7).run_integrity_scenarios(interp.execution_history))
