# Stage 12: research and implementation decisions

Review date: 2026-09-05. Inputs: Gold Execution Specification v2.2 normative,
Implementation Patch Plan v1.3, the supplied product description, and the five
Stage 12 draft attachments. Implementation contract:
[OD-13 amendment](../STAGE4_STAGE12_OUTCOME_CONTRACT.md).

New product code is grouped in `synapse/experiments/gold/stage12/`:
`verification.py` owns attempt proof checking, `reusable.py` owns independent
reusable-output verification and admission, and `outcome.py` owns result semantics.
The existing runner and Stage 10 owners integrate these responsibilities;
the former draft root-level locations have no aliases or alternate runtime.

## What changed relative to the drafts

The seven-status vocabulary and the negative acceptance scenarios were useful.
The supplied patch did not connect its result to the controller or recovery.
Its FULL path accepted caller-declared discharged operation/binding sets; its
partial path treated admission reference presence as admission. These are not
acceptable authority inputs.

The implementation reads the actual dispatched four-member Stage 10 bundle,
checks it against the frozen governing task, resolves the required repository
bindings, and rechecks retained C1 evidence through the existing C1 boundary.
The report is checked against the committed task and command policy. The C1
bridge must descend from the frozen base and contain only its two scaffold
files. C2 commit-pair diagnostics and the retained SWE-bench report must agree
with that same execution. Discharged obligations refer to the actual report.

Outcome identities bind the verifier version, manifest, all phase references,
worker delivery, C1 receipt, report/task/evidence/oracle references, plan
authority, obligations and bindings. They also bind explicit publication and
telemetry states. Only FULL maps to the existing controller success stop.
An invalid outcome does not erase or reinterpret the underlying C1 verdict.

The C1 completion checkpoint precedes verification. The final attempt result
contains both verification and outcome, using the existing immutable store and
recovery protocol. Completed consumers rerun read-only verification, without
rerunning worker, commands or oracle. The canonical CLI exposes `outcome_status`
and `outcome_ref` as projections of that stored result.

Attempt result schema is now v4 and run result schema v3. Old result schemas do
not acquire Stage 12 authority by automatic relabeling. Their history remains
unchanged; a new claim requires execution/verification under the current
contract. The report-byte reference profile is
`synapse.stage4.gold.c1-report-bytes/v1`; its `report_schema` preserves C1's
foreign `personal_slice.report/v0.5.0`, and its digest covers the original bytes.

## Appendix C sources reviewed

The table records applicable ideas and their limits. Research demonstrations
are not evidence of Synapse task success or cost savings.

| Primary source | Application and limit |
| --- | --- |
| [Voyager](https://arxiv.org/abs/2305.16291) | Executable, composable skills are a useful memory form. Results concern Minecraft; they do not grant repository execution or publication authority. |
| [Agent Workflow Memory](https://arxiv.org/html/2409.07429v1) | Workflow induction and variable binding inform future reusable behavior formation. Its online success judge is an LLM; Synapse cannot use that as independent verification. |
| [AgentPoison](https://arxiv.org/abs/2407.12784) | Retrieved memory can carry adversarial instructions. Preserve source taint and the existing admission boundaries. |
| [MINJA](https://arxiv.org/abs/2503.03704v5) | Memory injection can arise through ordinary interactions. A past conversation or apparently successful episode is not privileged authority. |
| [MemoryGraft](https://arxiv.org/html/2512.16962v1) | Poisoned experience records motivate checks on derived memory too. This is a preprint with a limited agent/task setup; proposed defenses are not a proven implementation of Synapse's gates. |
| [AgentRR](https://arxiv.org/html/2505.17716v1) | Structured experience plus explicit checks is useful for later behavior formation. It is not a replacement for durable replay and admission. |
| [From Loops to DAGs](https://arxiv.org/abs/2605.06365) | Stable artifact dependencies motivate incremental reconstruction in Stage 14. The preprint's policy-memo experiments do not establish repository-scale correctness or cost improvement. |
| [DFAH](https://arxiv.org/abs/2601.15322v2) | Determinism, accuracy and faithfulness need distinct measurements. Replay equality alone does not imply FULL. |
| [Refeeding Is Not Replay](https://arxiv.org/html/2606.15621v1) | Counterfactual token-credit measurements can be noisy. Its serving-stack experiments do not invalidate whole-run usage accounting and do not justify building a KV-cache replay engine in Stage 12. |
| [SLSA v1.2 VSA](https://slsa.dev/spec/v1.2/verification_summary), [artifact verification](https://slsa.dev/spec/v1.2/verifying-artifacts) | Bind subject, verifier, policy and input evidence in the verification record. Checking these relationships is necessary; hashes alone do not establish a trusted producer. No SLSA compliance level is claimed. |
| [W3C PROV-DM](https://www.w3.org/TR/prov-dm/) | Entity/activity/agent and derivation relationships inform Stage 14 lineage. Use existing typed records; an RDF or graph-database dependency is unnecessary. |
| [Temporal workflows](https://docs.temporal.io/workflow-definition), [activities](https://docs.temporal.io/activity-definition) | Durable history supports deterministic recovery. A side effect before a durable completion can still be uncertain; an exactly-once claim needs a corresponding external contract. Keep Synapse's existing checkpoints. |
| [Bazel remote caching](https://bazel.build/remote/caching) | Separate content storage from action metadata and bind reusable results to complete inputs. A log or cache hit is not correctness evidence. No second CAS is needed. |
| [Merkle DAGs](https://docs.ipfs.tech/concepts/merkle-dag/) | Immutable content references must be acyclic. This informs the verification → publication → final-outcome contract correction, without introducing IPFS. |
| [TUF specification](https://theupdateframework.github.io/specification/latest/) | Trusted roots, monotonic metadata and rollback/mix-and-match defenses distinguish an existing hash from current authorization. Reuse Synapse's lifecycle/fences rather than add another authority system. |
| [OpenTelemetry GenAI conventions](https://github.com/open-telemetry/semantic-conventions-genai/tree/main/docs/gen-ai), [span semantics](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md) | The separate GenAI conventions remain in Development; pin a version when implementing Stage 15. A logical span can cover retries. Cache-input and reasoning-output counts are subsets, not additive totals. Retain accounting provenance rather than infer completeness from traces. |

## Additional sources for subsequent stages and the product

| Primary source | Concrete decision |
| --- | --- |
| [SQLite atomic commit](https://sqlite.org/atomiccommit.html) | Stage 13 should extend the existing staged immutable publication and commit-marker owner. Define visibility, crash recovery and operation-ID idempotency over the whole write set. Do not introduce a parallel database merely to obtain a transaction label. |
| [Aider repository map](https://aider.chat/docs/repomap.html) | The product's structural map can rank symbols and relationships within a context budget, loading complete source on demand. A symbol graph is navigation evidence, not proof that a requirement is implemented. |
| [Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | Prefer small relevant context, durable references and deliberate loading. Keep memory views separate from instruction authority. |
| [Anthropic long-running harnesses](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) | Persistent task state, incremental work and explicit progress records inform durable product agents. An agent changing its own pass flag cannot replace Synapse's independent result verifier. |
| [AI Agents That Matter](https://arxiv.org/abs/2407.01502), [Princeton project](https://agents.cs.princeton.edu/) | Stage 16 should compare accuracy and total cost jointly under fixed paired conditions, with appropriate holdouts and reproducibility. Count failed attempts and platform overhead; do not choose only successful runs. |

## What follows Stage 12

Stage 12 now verifies an actual independently established rejected-patch guard
and its committed scoped admission through the existing owners. The earlier
deferral of the entire positive predicate to Stage 13 was incorrect against
Patch Plan v1.3 and has been removed. This narrow profile demonstrates reusable
negative knowledge without upgrading task correctness or claiming later utility.

Stage 13 generalizes candidate extraction and commits the complete publication,
attestation, lifecycle and admission set atomically. Publication
authority consumes the sealed verification record and exact write set; the
final outcome consumes the publication result. This removes the normative
hash cycle instead of simulating a successful publication in Stage 12.

Stage 14 adds durable lineage and reconstruction. Stage 15 implements complete
request accounting and the required reconciliations, with Mini usage remaining
one observable contributor. Stage 16 measures paired outcomes and costs.
Stage 17 reviews the accumulated evidence. The broader product's structural
map, durable element-agent state and user memory views remain product work;
they are not extra runtime branches hidden inside this evaluator.

No new product dependency was added from these sources. Acceptance scenarios
are external to production and heavy files have independent CI matrix entries.

## Earlier implementation validation

All 17 Stage 12 cases were exercised successfully: seven inexpensive public
contract cases and ten cases across five independent repository/process files.
After moving the product owners into their requested package and making
structured outcomes mandatory in result records, this invocation passed:

```bash
python -m pytest -q -p no:randomly \
  acceptance/stage4/stage12/test_outcome_contract.py \
  tests/test_stage4_gold_dependency_direction.py \
  tests/test_stage4_gold_architecture.py \
  tests/test_stage4_gold_ownership_dag.py \
  acceptance/stage4/stage12/test_canonical_outcome_acceptance.py
```

Result: **428 passed** on Python 3.12. Targeted Stage 10 authority/store and
Stage 11 controller, recovery, failure, continuation and usage checks also
passed during implementation. This is targeted evidence, not a claim that the
entire repository suite ran. The workflow runs the heavy Stage 12 files as
separate Python 3.11 jobs and includes them in the existing aggregate gate.

## Completion follow-up

The completion adds independent files for reusable outcome recovery and
admission/proof rejection, a cheap seven-status matrix, and transport mutants
for RUN relabelling and omitted FULL predicates. Each heavy file has its own
CI matrix job; no acceptance code is imported by product code. The retained
C1 report additionally exposes its already verified patch and revision, through
the sole existing C1 boundary. No C1/C2 runtime implementation was changed.

The project composition supplies physical stores through
`ReusableVerificationAuthority`; lower-level owners do not import the project
composition root. Its validation binds provenance, lifecycle, Library and the
admission journal to the same coordinator. Existing provenance now retains
attestations from multiple verified commits while still pinning the configured
builder, executable and runtime. Existing admission exposes an exact retained
grant check, so an ADMIT label cannot be substituted for this guard's domain.

Verification and outcome transports are v2: they include the scoped reusable
proof and nested attempt outcomes with an explicit RUN terminal-boundary kind.
They reject the earlier Stage 12 draft transport instead of maintaining a
second serializer or runtime compatibility path.

Completion validation includes 16,384 policy-channel combinations covering all
seven statuses, with 31 cheap matrix cases passing. Targeted existing authority
and architecture suites passed (687 cases); canonical CLI plus FULL transport
acceptance passed (18 cases). Reusable admission/proof checks, restoration with
reopened stores, runtime-identity rejection, invalid oracle-pair and missing-plan
acceptance passed in their independent invocations. These runs overlap and are
not a cumulative test count. The final focused contract/ownership run passed
325 cases. The ownership DAG reports no forbidden edges; whitespace validation
is clean. GitHub executes the seven heavy Stage 12 files independently and
requires them through the existing aggregate gate.
