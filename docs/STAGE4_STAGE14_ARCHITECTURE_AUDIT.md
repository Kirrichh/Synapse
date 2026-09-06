# Stage 14 document and architecture audit

Audit baseline: `223f3c6cceb74a7beb09232570765323537a3bc3`, PR #106.
Correction branch: `codex/stage4-stage14-lineage-dag`.

## Governing sources

The audit uses the uploaded 2026-09-06 normative documents, checked directly
against their DOCX contents, and the supplied complete product description.

| Source | Scope used in this audit |
| --- | --- |
| Gold Execution Specification v2.2 NORMATIVE | §§1–2, 11–12, 21, 24–29, 39.11, 39.17–23: ownership, authority, typed provenance, physical reconstruction, recovery and completion evidence |
| Implementation Patch Plan v1.3 | Stage 14 purpose, completion criterion, mandatory mutants and NR-01–06/11–12; Stage 15/16 dependency boundaries |
| «Что такое синапс(8)» | §§3, 17–25: distinct target/current/evidence states, durable decisions, actual delivery, failed-attempt history and verified reuse |

Normative source digests:

- Plan DOCX SHA-256: `a970b2254687fd8ce3fecd3132fd66a565b87bb1b30e9b37289a989578b3df91`.
- Specification DOCX SHA-256: `a0f22a204501086b1b7404206b98626b99e16da533300f86cb428b287c7d35a2`.

The design-source recommendations and their limits are recorded in the
[implementation contract](STAGE4_STAGE14_LINEAGE_CONTRACT.md). They do not
replace these normative requirements or evidence from Synapse's own runtime.

## Confirmed gaps and corrections

The baseline's green CI did not establish complete requirement coverage.
The audit added direct checks of intermediate ancestry and prepared phases.

| Finding | Requirement | Correction and acceptance |
| --- | --- | --- |
| The delivered-context ancestry did not reach its actual input selection and replay, although those records appeared elsewhere in the final graph. | §29 and Stage 14's selected/replayed/delivered reconstruction | Resolve the original audit/delivery pair, check exact replay observations and add mandatory selection/replay/audit/delivery dependencies. `test_delivery_lineage_acceptance.py` checks the actual terminal reader and restoration. |
| Plans already persisted before delivery were omitted when no completed worker existed. A prepared context at an interrupted dispatch was likewise absent. | LIN-05 and the product's preservation of every reached phase | The existing Stage 10 store now reads prepared plan bundles and context pairs independently of worker completion. `test_pre_dispatch_lineage_acceptance.py` covers unavailable delivery and interrupted dispatch without inventing worker/C1/oracle results. |
| The next attempt's feedback did not provide an explicit ancestry path to the predecessor's proof. A union of attempt graphs at run level was insufficient. | NR-11, §29 and the product's multi-attempt history | Follow the intent's exact feedback reference, check the earlier same-run result and physical execution proof, then attach its existing graph to the new intent. `test_feedback_lineage_acceptance.py` checks ancestry, immutable history and the real reader's integrity gate. |

The delivery and two prepared-prefix acceptance cases were first run against
the baseline and failed on the missing ancestry/roles. They pass with the
corrections. Feedback acceptance passes with both actual attempts and retains
the original worker/oracle invocation counts through reconstruction.

## Architecture and integration checks

| Invariant | Inspected implementation and evidence |
| --- | --- |
| One canonical execution path | `python -m synapse` continues through the existing CLI, composition, run controller and attempt materializer. No Stage 14 entrypoint was added. |
| Cohesive ownership, no LOC threshold | `stage14/graph.py` owns structural DAG semantics; `sources.py` owns retained input locations; `execution.py` follows physical execution dependencies; `publication.py` projects atomic publication proof; `reconstruction.py` attaches terminal and producer/consumer results. |
| Historical records do not grant authority | Stage 10's context inspection and plan read-back return recorded evidence. Existing plan, delivery, C1, verification, publication and promotion authorities retain their roles. |
| One owner per contract | `read_plan_bundle` replaces the prior dispatched-only reader at its existing owner and call sites. The context owner supplies the replay observation projection for both construction and inspection. No alias, artificial adapter or runtime dependency was introduced. |
| Protected core and dependency direction | No changes to CVM, interpreter or C1/C2 implementation owners; no `swebench -> gold` import. Architecture/ownership/dependency tripwires pass. |
| Atomic publication and recovery | Publication's committed members include the lineage fragment. Existing result readers reopen participants and reconstruct the graph; attempt/run results are visible after their corresponding graph records. Separate publication and crash-recovery acceptance cover these boundaries. |
| Genuine future reuse | The consumer graph reaches the producer's inputs, accepted plan, verification/oracle and publication. Consumer and producer occurrences remain distinct; original outcomes are not rewritten. |
| Retention and identity | Reachable native library identities feed the existing LINEAGE retention category. Content deduplication does not merge execution occurrences. Missing original stores or inconsistent references fail reconstruction. |
| External acceptance | All new acceptance and mutation checks live under `acceptance/stage4/stage14/`. Seven independent heavy CI jobs feed the existing required aggregate. Product imports/configuration do not depend on these tests. |

## Verification and scope of the conclusion

The corrective local checks include the architecture/ownership/dependency and
fast graph suite (481 passed), the new delivery/prepared-prefix/feedback
acceptance, and the existing invalid-plan contract. Publication, recovery and
canonical producer/consumer reuse are checked separately because the shared
execution reader participates in those paths.

The PR description links the GitHub Actions runs for the exact corrective
commit. The baseline's 84-job result is evidence for the baseline only; it is
not reused as verification of the corrective commit.

Stage 14 establishes execution provenance within the current durable storage
contract. Canonical telemetry, reconciliation and EventStream remain Stage 15;
paired evaluation, complete accounting and economic claims remain later gates.
The product description's broader project-model and agent capabilities do not
become implemented merely by adding lineage. Full Stage 4 acceptance still
requires the simultaneous predicates in §39 and human review.
