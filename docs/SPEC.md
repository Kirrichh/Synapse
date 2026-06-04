# Synapse Language Specification
Spec Version: v2.2.0-alpha3e
Status: RELEASE CANDIDATE
Last Updated: 2026-05-25

## Преамбула: канонические определения

Этот документ является актуальным reference manual для Synapse v2.2.0-alpha. Он должен содержать только действующую семантику языка: EBNF, AST-узлы, runtime-контракты, durable events, replay behavior, ограничения, PAD/CVM/HOST_ABI/Energy Pool/Legacy Flag.

Исторические изменения и патч-хронология вынесены в `docs/CHANGELOG.md`. Архитектурные trade-offs и rationale вынесены в `docs/ARCHITECTURE.md`. Operational semantics для когнитивных и аффективных примитивов вынесены в `docs/SEMANTICS.md`.

## Scope v2.2.0-alpha

Synapse v2.2.0-alpha introduces the first CVM Core expansion. It adds bytecode compilation and VM execution for deterministic base-language constructs: let/assign, expressions, arithmetic, comparisons, logic, if/else, while, for, functions, recursion, lists, indexing, and assertions.

Cognitive primitives (`DreamBlock`, `ResonanceStmt`, `CollectiveDreamStmt`) remain `HOST_EVAL` unless explicitly routed through fixed `HOST_ABI`.

## Backward Compatibility

- v2.1 snapshots remain accepted by `VMSnapshot.from_dict()`.
- v2.1.4 RuntimeFacade boundaries remain stable.
- `BytecodeProgram.version == "2.2"` is the new CVM core boundary.

## Normative References

- `docs/SEMANTICS.md` — строгие runtime-контракты когнитивных и аффективных примитивов.
- `docs/ARCHITECTURE.md` — design principles, trade-offs, roadmap.
- `docs/CHANGELOG.md` — хронология изменений.

## Runtime Consolidation Boundary

Начиная с v2.1.4-C `interpreter.py` является AST-оркестратором: он отвечает за dispatch, окружения, scope stack, builtin routing и публичную совместимость. Доменная runtime-семантика закреплена за `synapse/runtime/*`:

- `replay_engine.py` — history, replay cursor, side-effect logging, hash chain.
- `governance_engine.py` — policies, guards, purity checks, frozen mood, verdict logging.
- `affective_runtime.py` — PAD state, thresholds, resonance, atomic affective mutations.
- `habit_engine.py` — habit registry facade, activation routing, observer suppression.
- `actor_runtime.py` — mailboxes, receive/send, promises, spawn, migration.
- `vm_bridge.py` и `vm_routing.py` — CVM boundary, HOST_ABI dispatch, HOST_EVAL fallback visibility.

### CVM Boundary

`COMPILE_VM`, `RUN_VM` и fixed `HOST_ABI` выполняются через CVM/VM Bridge. CVM Core v2.2-alpha routes deterministic base-language AST nodes through `CognitiveCompiler`/`CognitiveVM`; cognitive primitives remain in tree-walking `HOST_EVAL` path. Fallback события логируются как `vm_fallback` и учитываются в `vm_coverage_ratio`, но не меняют результат выполнения.
