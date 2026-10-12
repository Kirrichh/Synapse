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

# --- Observation agent suite (embedded Synapse-language agents) ----------
# The runtime observes itself: four observation agents authored in the
# Synapse language are embedded here and executed by this hardening pass.
_OBSERVATION_AGENTS = {
    'durable_watch': '''// Долговременный наблюдатель: дрейф контрольных сумм между эпохами.
// Стабильность чекпойнтов оценивается как размах наблюдаемых значений.
fn epoch_drift(checksums) {
    return max(checksums) - min(checksums)
}

fn watch_verdict(drift, tolerance) {
    if drift <= tolerance {
        return "STABLE"
    }
    return "DRIFTING"
}

let epochs = [118, 121, 119, 120, 122]
let drift = epoch_drift(epochs)
let verdict = watch_verdict(drift, 4)
print("durable_watch " + verdict + " drift " + str(drift))
print("DURABLE_WATCH_OK")
''',
    'health_consensus': '''// Консенсус здоровья: голосование наблюдателей рантайма.
// Кворум и простое большинство определяют общий вердикт здоровья.
fn consensus_verdict(upvotes, downvotes, votes_required) {
    let total = upvotes + downvotes
    if total < votes_required {
        return "NO_QUORUM"
    }
    if upvotes + upvotes > total {
        return "HEALTHY_MAJORITY"
    }
    return "SPLIT_VERDICT"
}

let verdict = consensus_verdict(7, 2, 8)
print("health_consensus " + verdict)
print("HEALTH_CONSENSUS_OK")
''',
    'memory_sentinel': '''// Наблюдатель Memory Palace: баланс закрепления, забвения и распада блоков.
// Следит за тем, чтобы закрепление памяти не вырождалось в потери.
fn retention_margin(imprinted, forgotten, decayed) {
    let losses = forgotten + decayed
    return imprinted - losses
}

fn memory_verdict(margin, imprinted) {
    if margin + margin >= imprinted {
        return "HEALTHY"
    }
    if margin > 0 {
        return "DEGRADED"
    }
    return "CRITICAL"
}

let margin = retention_margin(72, 18, 10)
let verdict = memory_verdict(margin, 72)
print("memory_sentinel " + verdict + " margin " + str(margin))
print("MEMORY_SENTINEL_OK")
''',
    'policy_auditor': '''// Аудитор политик: наблюдение за исполнением бюджета операций рантайма.
// Разрешённые и отказанные операции сравниваются с операционным лимитом.
fn policy_verdict(allowed, refused, budget_cap) {
    let total = allowed + refused
    if total > budget_cap {
        return "OVER_BUDGET"
    }
    if refused == 0 {
        return "CLEAN"
    }
    return "REVIEW_REFUSALS"
}

let verdict = policy_verdict(46, 2, 48)
print("policy_auditor " + verdict)
print("POLICY_AUDITOR_OK")
''',
}

for _agent_source in _OBSERVATION_AGENTS.values():
    _observer = Interpreter()
    _observer.source_code = _agent_source
    _observer.interpret(compile_to_ast(_agent_source))
    print(_observer.get_output())
