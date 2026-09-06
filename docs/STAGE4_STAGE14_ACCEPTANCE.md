# Stage 14 acceptance and ownership

Base: merged PR #105 (`c4c90bd`). Implementation contract:
[Stage 14 lineage](STAGE4_STAGE14_LINEAGE_CONTRACT.md).

## Normative coverage

| Contract | Product owner and observable behavior | External acceptance |
| --- | --- | --- |
| LIN-01–03 / §29 | `stage14/graph.py`: closed node/edge vocabulary, content and occurrence identity, deterministic ordering, endpoints, mandatory relations, iterative cycle detection | `test_graph_contract.py`, `test_graph_mutants.py`, existing architecture and ownership DAG suites |
| LIN-04 / §39.11 | `stage14/sources.py` and `execution.py`: reopen original coordinated snapshot, retrieval, library, replay, run and Stage 10 stores; bind their exact records | `test_attempt_lineage_acceptance.py`, `test_publication_lineage_acceptance.py` |
| LIN-05 | The existing attempt materializer attaches the graph to every reached terminal path; refusals and interruptions retain their actual phase prefix | existing Stage 11 delivery, preparation, C1 and multi-attempt acceptance |
| LIN-06 / §28 | `stage14/publication.py`: producer inputs through independent verified outcome and publication authority; `lineage.json` is an atomic committed member | `test_publication_lineage_acceptance.py`, existing Stage 13 publication/recovery acceptance |
| LIN-07 | Existing RunRecordStore publishes attempt/run lineage before the corresponding result; restart reconstructs and compares exact graphs | `test_lineage_recovery_acceptance.py` |
| LIN-08 | Reachable native library identities feed `RetentionRootKind.LINEAGE` and the existing GC planner; shared content remains deduplicated | `test_attempt_lineage_acceptance.py`, existing library and publication dedup acceptance |
| LIN-09 | Run result retains its explicit telemetry completeness; Stage 14 does not mint provider accounting | existing Stage 11/12 result contracts; Stage 15 remains required |
| Producer/consumer closure | Consumer lineage reaches the producer's actual input snapshot, decisions, accepted plan, worker, C1/oracle and atomic publication; producer records remain immutable | `test_reuse_lineage_acceptance.py` |

Stage 14 tests live under `acceptance/stage4/stage14/`. Four heavy files run in
independent GitHub Actions matrix jobs. Their aggregate is required by
`gold-slow`. Tests and mutation harnesses remain outside product imports and
configuration.

## Mutation acceptance

Controlled mutations exercise endpoint integrity, required relations,
deterministic ordering, the distinction between a declared relation and
physically supported evidence, and publication's independent verification
binding. Every mutation has an unmodified control and restoration check.
A mutation is accepted as detected only when its acceptance verdict changes.

## Changes at existing boundaries

- The existing causal journal retains the exact retrieval decision and frozen
  candidate set before the causal summary. No second retrieval implementation
  or journal was introduced.
- The input source exposes the actual replay record owner; the materializer
  binds the actual run and Stage 10 record owners before sealing context v5.
- Publication result v3 includes the lineage reference. Its prepared transaction
  retains the context-bound source catalog alongside existing evidence; its
  committed transaction includes `lineage.json`.
- `stage14/execution.py` is the single execution-proof reader used by publication
  and final attempt reconstruction. `reconstruction.py` owns completion and
  producer/consumer attachment, avoiding a dependency cycle with publication.
- The existing controller validates physical lineage when reading a completed
  run. No new production entrypoint, interpreter path, runtime dependency or
  numeric Python LOC threshold was added.

The storage boundary remains explicit: lineage requires retained original
stores. A changed coordinator, missing physical source or incompatible schema
cannot be interpreted as complete proof. Whole Stage 4 completion still requires
Stage 15 telemetry/reconciliation and the later paired acceptance stage.
