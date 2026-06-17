# Матрица зрелости возможностей Synapse Runtime

Источник статусов: Synapse Runtime Capability Integrity Program.

Проверено относительно `main` на merge commit `edd8bf7177aa4d5ade0c9ea6d9f03b2b75a73f60`.

Матрица отделяет наличие внутренней механики от production-достижимости и наблюдаемого пользовательского поведения. Возможность получает статус production только при наличии канонического execution path, наблюдаемого результата, durable/replay-контракта, failure semantics и acceptance evidence.

| Возможность | Зрелость | Канонический статус | Evidence / граница |
|---|---|---|---|
| Provenance профиля affective resonance | **Production** | `MERGED` | `profile_source` наблюдаем в возвращаемом bridge и единожды сохраняется на верхнем уровне `affective_resonance_applied`; LIVE поддерживает `explicit`, `history`, `neutral_fallback`; legacy replay выводит `legacy_unknown`, не изменяя историю. Реализовано в PR #10. |
| CVM execution и checkpoint/resume | **Глубокая production-семантика** | Существует | Реализованы состояние исполнения, ABI validation, checkpoint/resume и проверка history boundary. Изменения требуют отдельного conformance evidence. |
| Deterministic replay и tamper-evident history | **Глубокая production-семантика** | Существует | Typed replay matching и hash chain по полному event payload являются действующими runtime-контрактами. |
| Governance refusal и replay сохранённого verdict | **Глубокая production-семантика** | Существует; evidence-контур продолжается | Fail-closed отказ и durable verdict существуют. Пользовательское evidence остаётся частью S2. |
| Каноническое async durable execution через CLI | **Partial; initial run production-reachable** | `P2a MERGED`; `P2b/P2c REQUIRED` | Канонический `run --durable` достигает `COMPLETED`, `PENDING` или структурированного `ERROR`, создаёт versioned Durable Run Artifact, применяет durable-safety validator, initial bindings, sibling lock и atomic commit. Реализовано в PR #16. Команда `resume`, boundary replay, signal injection, idempotency, multi-cycle и concurrent resume ещё не реализованы. Текущая граница зафиксирована в [статусе P2](ASYNC_DURABLE_EXECUTION_STATUS.md). |
| Distributed consensus | **Семантический фасад** | Для P3 требуется RFC | Текущее поведение ещё не представляет содержательные голоса участников. Его нельзя описывать как завершённый distributed consensus. |
| Habit capability | **Production-механика; evidence не завершено** | Требуется диагностика P4 | Evaluation, suppression, fatigue, recovery и activation orchestration существуют, но канонический пользовательский сценарий должен различать activation и non-activation/suppression. |
| Покрытие CVM / tree-walker | **Conformance не доказан** | Требуется матрица P5 | Routing declarations сами по себе не доказывают compiler, opcode, VM handler, state, error, history и replay parity. |
| Семейство AS2 | **Внутренняя / test-oriented инфраструктура** | Не подключено к production execution | AS2 содержит значимую внутреннюю механику, но недостижим через канонический interpreter/CLI/CVM path. Для P6 требуется архитектурное решение. |
| Cross-node routing | **Runtime-половина внешнего протокола** | Только outbound intent | Runtime разрешает маршруты и фиксирует outbound packet/intent. Сетевая доставка принадлежит внешнему transport daemon. |

## Правила статусов

- **Production** — возможность достижима через поддерживаемый runtime path и имеет наблюдаемое replay-aware поведение.
- **Partial** — значимая часть product lifecycle production-reachable, но полный канонический lifecycle ещё не завершён.
- **Семантический фасад** — публичное название обещает больше, чем предоставляет текущая реализация.
- **Внутренняя / test-oriented инфраструктура** — реализация существует, но не является production-reachable.
- **Conformance не доказан** — декларативная маршрутизация или наличие компонента ещё не подтверждены end-to-end исполнением.

Эта матрица является документом честной сигнализации. Она не изменяет parser, AST, interpreter, runtime semantics, durable schemas, CLI behavior или feature flags.
