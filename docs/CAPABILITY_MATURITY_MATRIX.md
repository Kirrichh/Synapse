# Матрица зрелости возможностей Synapse Runtime

Источник статусов: Synapse Runtime Capability Integrity Program.

Проверено относительно `main` на post-merge P3c-N2 implementation commit `83db81ec3e41226406009df194dec320632cb3f2`.

Матрица отделяет наличие внутренней механики от production-достижимости и наблюдаемого пользовательского поведения. Возможность получает статус production только при наличии канонического execution path, наблюдаемого результата, durable/replay-контракта, failure semantics и acceptance evidence.

| Возможность | Зрелость | Канонический статус | Evidence / граница |
|---|---|---|---|
| Provenance профиля affective resonance | **Production** | `MERGED` | `profile_source` наблюдаем в возвращаемом bridge и единожды сохраняется на верхнем уровне `affective_resonance_applied`; LIVE поддерживает `explicit`, `history`, `neutral_fallback`; legacy replay выводит `legacy_unknown`, не изменяя историю. Реализовано в PR #10. |
| CVM execution и checkpoint/resume | **Глубокая production-семантика** | Существует | Реализованы состояние исполнения, ABI validation, checkpoint/resume и проверка history boundary. Изменения требуют отдельного conformance evidence. |
| Deterministic replay и tamper-evident history | **Глубокая production-семантика** | Существует | Typed replay matching и hash chain по полному event payload являются действующими runtime-контрактами. |
| Governance refusal и replay сохранённого verdict | **Глубокая production-семантика** | Существует; evidence-контур продолжается | Fail-closed отказ и durable verdict существуют. Пользовательское evidence остаётся частью S2. |
| Каноническое async durable execution через CLI | **Production** | `P2a + P2b + P2c IMPLEMENTED / VERIFIED_ON_MAIN / CLOSED`; `P2 mailbox wait IMPLEMENTED / VERIFIED_ON_MAIN / CLOSED FOR APPROVED P2 SCOPE` | Подробности зафиксированы в [статусе P2](ASYNC_DURABLE_EXECUTION_STATUS.md), [P2b evidence](evidence/P2B_EVIDENCE.md), [P2c evidence](evidence/P2C_EVIDENCE.md) и [P2 mailbox wait evidence](evidence/P2_MAILBOX_WAIT_EVIDENCE.md). |
| Distributed consensus | **Partial — P3b local actor-method vote source verified; P3c-0 replay consumption closed; P3c-1 durable ticket creation/replay closed; P3c-2 durable ticket resolution via existing P2 resume boundary closed; P3c-N1 local mailbox-backed pending-ticket vote response collection closed; P3c Ticket Lifecycle terminal cancel/expire and replay integrity closed; P3c-N2 fresh DistributedConsensusStmt mailbox-backed vote request delivery and initial collection closed** | `P3a IMPLEMENTED / VERIFIED_ON_MAIN / S1/S2 EVIDENCE CLOSED`; `P3b POST_MERGE_ACCEPTED / EVIDENCE CLOSED`; `P3c-0 POST_MERGE_ACCEPTED / EVIDENCE CLOSED`; `P3c-1 POST_MERGE_ACCEPTED / EVIDENCE CLOSED`; `P3c-2 POST_MERGE_ACCEPTED / EVIDENCE CLOSED`; `P2 mailbox wait CLOSED FOR APPROVED P2 SCOPE`; `P3c-N1 POST_MERGE_ACCEPTED / EVIDENCE CLOSED`; `P3c Ticket Lifecycle POST_MERGE_ACCEPTED / EVIDENCE CLOSED`; `P3c-N2 POST_MERGE_ACCEPTED / EVIDENCE CLOSED` | P3c-N2 closes the approved fresh `DistributedConsensusStmt` mailbox-backed request-delivery and initial collection slice: deterministic request projection, `distributed_consensus_vote_requested`, local-only `consensus_vote_request` delivery after route precheck, deterministic request ids/hashes, replay reconstruction, fresh response binding, imported P3c-N1 compatibility, and terminal-ticket rejection. Broader distributed-consensus protocol behavior, external transport, remote participant delivery, scheduler behavior, persistent inbox behavior, live LLM vote production, public ticket API / external lifecycle surface, parser/AST/lexer syntax, and full content-sensitive consensus closure remain future work. Evidence: `docs/evidence/P3A_EVIDENCE.md`, `docs/evidence/P3B_EVIDENCE.md`, `docs/evidence/P3C_EVIDENCE.md`, `docs/evidence/P2_MAILBOX_WAIT_EVIDENCE.md`, PR #27 `60db4d3aa610c0cab6ec19cf532b47b7107de136`, PR #31 `dbdfc7252c83d9fc4be0f0b5eb2cbd2007f0e2ad`, PR #34 `16fdd5fb209a9ab387359888bf1952571cfe8fba`, PR #39 `88210654223b19a52bfddf9f3715e1a95af90367`, PR #45 `c5b129711ef76f919f263ac4dc6d35637890a347`, PR #50 `2a93ef6006ce4b86f2fe90cc4490ee3a1cefcb92`, PR #58 `a9497aa26b4450f40a541e16b6260129d36bb4f2`, PR #61 `8ff834bdeebd195ad7689af5c2137b04792b3025`, PR #64 `83db81ec3e41226406009df194dec320632cb3f2` / implementation head `0975af20446e48694e490825c1886b66bac0db95`. Post-merge evidence patch SHA: filled by PR merge commit. |
| Habit capability | **Production-механика; evidence не завершено** | Требуется диагностика P4 | Evaluation, suppression, fatigue, recovery и activation orchestration существуют, но канонический пользовательский сценарий должен различать activation и non-activation/suppression. Недетерминизм affective ID сохраняется как known behavior для future evidence tracking. |
| Покрытие CVM / tree-walker | **Conformance не доказан** | Требуется матрица P5 | Routing declarations сами по себе не доказывают compiler, opcode, VM handler, state, error, history и replay parity. |
| Семейство AS2 | **Внутренняя / test-oriented инфраструктура** | Не подключено к production execution | AS2 содержит значимую внутреннюю механику, но недостижим через канонический interpreter/CLI/CVM path. Для P6 требуется архитектурное решение. |
| Cross-node routing | **Runtime-половина внешнего протокола** | Только outbound intent | Runtime разрешает маршруты и фиксирует outbound packet/intent. Сетевая доставка принадлежит внешнему transport service. |

## Правила статусов

- **Production** — возможность достижима через поддерживаемый runtime path и имеет наблюдаемое replay-aware поведение.
- **Partial** — значимая часть product lifecycle production-reachable, но полный канонический lifecycle ещё не завершён.
- **Семантический фасад** — публичное название обещает больше, чем предоставляет текущая реализация.
- **Внутренняя / test-oriented инфраструктура** — реализация существует, но не является production-reachable.
- **Conformance не доказан** — декларативная маршрутизация или наличие компонента ещё не подтверждены end-to-end исполнением.

Эта матрица является документом честной сигнализации. Она не изменяет parser, AST, interpreter, runtime semantics, durable schemas, CLI behavior или feature flags.

## Merge-gate для следующих stages

Перед merge будущих product PR тело PR должно отражать final head SHA, test counts, CI run IDs, known failures и финальный review status. Это правило предотвращает evidence mismatch между фактическим кодом и публичной записью ревью.
