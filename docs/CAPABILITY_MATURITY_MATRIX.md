# Матрица зрелости возможностей Synapse Runtime

Источник статусов: Synapse Runtime Capability Integrity Program.

Проверено относительно `main` на post-merge P2c commit `4eb2ec86c91a5412ce183261000bdc884b1b0d85`.

Матрица отделяет наличие внутренней механики от production-достижимости и наблюдаемого пользовательского поведения. Возможность получает статус production только при наличии канонического execution path, наблюдаемого результата, durable/replay-контракта, failure semantics и acceptance evidence.

| Возможность | Зрелость | Канонический статус | Evidence / граница |
|---|---|---|---|
| Provenance профиля affective resonance | **Production** | `MERGED` | `profile_source` наблюдаем в возвращаемом bridge и единожды сохраняется на верхнем уровне `affective_resonance_applied`; LIVE поддерживает `explicit`, `history`, `neutral_fallback`; legacy replay выводит `legacy_unknown`, не изменяя историю. Реализовано в PR #10. |
| CVM execution и checkpoint/resume | **Глубокая production-семантика** | Существует | Реализованы состояние исполнения, ABI validation, checkpoint/resume и проверка history boundary. Изменения требуют отдельного conformance evidence. |
| Deterministic replay и tamper-evident history | **Глубокая production-семантика** | Существует | Typed replay matching и hash chain по полному event payload являются действующими runtime-контрактами. |
| Governance refusal и replay сохранённого verdict | **Глубокая production-семантика** | Существует; evidence-контур продолжается | Fail-closed отказ и durable verdict существуют. Пользовательское evidence остаётся частью S2. |
| Каноническое async durable execution через CLI | **Production** | `P2a + P2b + P2c IMPLEMENTED / VERIFIED_ON_MAIN / CLOSED` | P2a реализует канонический `run --durable` и создаёт versioned Durable Run Artifact. P2b реализует канонический `python -m synapse resume`, проверку artifact integrity, embedded source ownership, deterministic replay до сохранённой boundary, signal injection в тот же generator, output-prefix suppression, terminal idempotency, conflicting duplicate rejection, PENDING→PENDING next suspension mechanics и process-level resume lock race proof. P2c закрывает full multi-cycle campaigns, stale IDs across later boundaries, duplicate same/different signals across cycles, late-boundary process races, P2a/P2b compatibility и schema `1.0.0` preservation. Подробности зафиксированы в [статусе P2](ASYNC_DURABLE_EXECUTION_STATUS.md), [P2b evidence](evidence/P2B_EVIDENCE.md) и [P2c evidence](evidence/P2C_EVIDENCE.md). |
| Distributed consensus | **Семантический фасад** | Для P3 требуется RFC | Текущее поведение ещё не представляет содержательные голоса участников. Его нельзя описывать как завершённый distributed consensus. Фаззинг-находка `with [] quorum 1 -> committed=True` сохраняется как кандидат P3 evidence/RFC. |
| Habit capability | **Production-механика; evidence не завершено** | Требуется диагностика P4 | Evaluation, suppression, fatigue, recovery и activation orchestration существуют, но канонический пользовательский сценарий должен различать activation и non-activation/suppression. Недетерминизм affective ID сохраняется как known behavior для future evidence tracking. |
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

## Merge-gate для следующих stages

Перед merge будущих product PR тело PR должно отражать final head SHA, test counts, CI run IDs, known failures и финальный review status. Это правило предотвращает evidence mismatch между фактическим кодом и публичной записью ревью.
