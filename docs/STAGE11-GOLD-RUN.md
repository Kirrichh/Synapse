# Stage 11: frozen Gold experiments

The canonical application can start and reopen a Gold run from persisted
operator inputs. It uses the existing snapshot, compatibility, admission,
retrieval, replay, Stage 10 and C1/C2 owners. Runtime inputs contain data;
they cannot name a Python factory or supply a gate callback.

## Run, approve, resume

Connect the repository with the existing `project connect` command and explicit
identities and entitlements. The project needs previously admitted behavior
records in its library, provenance, lifecycle and taint stores. Connecting an
empty project does not manufacture knowledge. Seed production/publication is
outside this Stage 11 consumption path.

```sh
python -m synapse project run --state-dir /state/project --input /inputs/experiment.json --run-dir /state/run-001
```

The first call returns `APPROVAL_REQUIRED`, the request file and the complete
`approve_command`. Review the request and execute that one command:

```sh
python -m synapse approve /state/run-001/approvals/requests/REQUEST.json --store /state/run-001/approvals --resume-run /state/run-001
```

The operator grant covers matching plans in this frozen run for its declared
lifetime. Each attempt still receives an independent decision and fresh
point-of-use checks. A later attempt with the same task, scope and policy does
not require another prompt just because its attempt or snapshot ID changed.
For governing task v2, approval request v2 explicitly covers selection from
`CURRENT_ADMITTED_SNAPSHOT`. Its knowledge references can change between
attempts without changing the task grant. Operation structure, non-knowledge
inputs, scope, capabilities, verification, policy and executor stay bound to
the request. Selection must still pass independent compatibility/admission;
the grant alone cannot authorize an unselected subject. Historical task v1
and approval request v1 keep their exact required knowledge references.
New conditions, expiration or revocation require a new grant.

```sh
python -m synapse project resume --run-dir /state/run-001
python -m synapse revoke-approval GRANT_SHA256 --store /state/run-001/approvals
```

Exit `3` means approval is needed; `1` means input or dependency validation
failed; `0` means a terminal run record was returned. Only the result status
`GOLD_RESOLVED` denotes a resolved Gold run. A stopped or unavailable experiment
is not reported as resolved merely because its record was read successfully.

Keep the run directory outside the worker repository. Resume uses
`run-001/experiment.json`, not the original operator JSON or seed export.
Terminal resume reads completed records without repeating seed assembly,
replay, worker execution or C1. An interrupted preparation/external effect with
no durable completion follows the existing uncertainty policy; it is not
silently repeated under the same attempt ID.

## Explicit experimental scope

Input schema `synapse.stage4.gold.experiment-input/v1` selects a controlled edit
of one existing target file. The task's expected effect is `PATH_MODIFIED`;
its scope and the unchanged C1 command policy name that exact file. Binding
records resolve against the frozen Git revision before the plan is accepted
and again at first-effect revalidation. Verification conditions name the hash
of the actual C1 command policy. An unsupported task shape is refused.

The frozen replay profile is `pure-cvm/v1`: selected behavior units use the
existing compiler/CVM and declare no external capabilities. All external
activity kinds are forbidden during reference/replay. Worker generation and
the independent C1/C2 oracle execute in their existing phases. Other replay
profiles and arbitrary task operations are not implicitly enabled by this
schema. This is a concrete Stage 11 experiment, not the full Stage 4 product.

The governing task is separate from the planner's proposal. New
`GoverningTaskContract` objects use `synapse.stage4.gold.governing-task/v2`:
task, revision, scope, capabilities, targets, effects and acceptance remain
fixed; the wire contract has no `behavior_refs` field. Supplying that field,
even as null or an empty list, is refused. Historical task v1 remains readable
with its exact required behavior references; old records are not rewritten.

Intent v3 still includes `task_contract_ref`, `target_bindings` and nonempty
`behavior_refs`. For task v2, those behavior references come from this attempt's
admitted handle and the plan names the same selection. Plan authority checks
the selected subjects against independently minted consumption evidence and
the durable compatibility records. Rehashing a proposal cannot replace that
selection or the governing conditions. Stage 12 checks the recorded plan
against the retained knowledge basis bound to the dispatch's attempt identity
and digest. Historical inspection grants no fresh execution authority.

## Operator input fields

Every field below is required; unknown fields and duplicate JSON keys fail
validation. Use the existing records' `to_dict()` methods for their wire form.

| Field | Content / owner |
| --- | --- |
| `schema_version` | `synapse.stage4.gold.experiment-input/v1` |
| `run_id` | New experiment identity |
| `config` | `GoldRunConfig.to_dict()`: task, instance, base, worker provider/model, oracle class identity, environment, budgets, attempts, replicate identity and fallback policy |
| `versions` | `GoldRunVersions.to_dict()`: specification and policy version/digest, implementation revision |
| `task_contract` | `GoverningTaskContract.to_dict()`: task ID/statement, revision, scope, capabilities, target refs, typed effects and acceptance criteria; v2 does not preselect knowledge, historical v1 requires `behavior_refs` |
| `target_records` | Complete Python, document or requirement binding records corresponding exactly to `target_bindings` |
| `command_policy` | Full JSON projection of existing C1 `GoldRunnerCommandPolicy`, including both reproduction expectations and all command groups |
| `worker` | Provider, executable argv, model, timeout, step limit and decimal-string cost limit |
| `oracle` | Full JSON projection of existing `SWEbenchHarnessOracleConfig`, including its default fields |
| `actor_namespace` | Explicit bounded namespace for the independent runtime actors |
| `observation` | Builder identity, base revision, task ref, policy/environment/tool inputs, source/verification refs and oracle observation |
| `knowledge_path` | Seed export JSON; relative paths resolve against the operator input directory |
| `replay_profile` | `pure-cvm/v1` |

The worker declaration has this form:

```json
{
  "provider": "mini",
  "command": ["mini"],
  "model": "YOUR_MODEL",
  "timeout_seconds": 600,
  "max_steps": 20,
  "cost_limit": "1.00"
}
```

Mini is the currently installed external worker integration. Its concrete
configuration is decoded at the Stage 10 composition boundary; run decisions
consume the shared worker contract. Mini supplies neither approval authority,
replay semantics, compatibility verdicts nor the final success decision.

`command_policy_reference(policy)` returns the exact condition reference used
by the task's effects and acceptance. `binding_to_ref(binding)` gives each
target reference. Attempt knowledge references use the existing library subject
identity, not an arbitrary label or a raw behavior transcript. New tasks do not
need those subject references before retrieval.

## Seed evidence

Seed schema `synapse.stage4.gold.knowledge-input/v1` has four fields:
`schema_version`, `candidates`, `files` and `conflicts`.

Each candidate carries the complete unit, manifest ID, attestation, binding
records, lifecycle context and taint closure. The closure contains `profiles`,
topologically ordered `derivations`, `decisions` and its `root_id`. These are
supporting records for objects already present in the project stores. They do
not bypass the stores: Library reopens CAS bytes; provenance checks history
membership; lifecycle checks current consumability; taint reconstructs and
checks the closure against its authoritative history.

Each `files` entry contains `ref` and an absolute `path`. The referenced bytes
must match their digest and byte length. This includes every declared current
observation input and conflict evidence file. Missing or changed evidence
stops the run before the worker.
The current observation's task reference must equal the governing task's
reference; its file contains `GoverningTaskContract.canonical_bytes()`. Naming
another task to obtain a favorable compatibility decision is refused.

Each `conflicts` entry contains `left` and `right` behavior content keys,
`kind` (a conflict kind or null), and nonempty `evidence_refs`. A candidate
pair with no evidenced assessment is unavailable. A single-candidate corpus
needs no pair assessment. The production ranking component is
`synapse.stage4.task-binding-relevance/v1`. It scores exact target coverage as
`floor(1_000_000 * matched_target_count / task_target_count)` using the candidate's
canonical binding references. Its input reference binds the query, descriptor,
governing task and both sets of bindings. Reordering the seed or substituting
another score input cannot change that evidence into a higher score.

This is a structural ranking feature, not semantic retrieval. The declared
seed remains the candidate universe and the current selection limit remains
its full size. A zero score alone does not exclude a candidate. Target bindings
are still supplied in the task. Automatic project exploration, search across
all experience, empty-result handling and applicable procedure alternatives
remain subsequent work. The separate worker-input extension described below
addresses delivery and provider provenance, not local interpretation or use.

## Separate Mini inputs and recovery

New contexts use `worker-context-record/v3` and `worker-delivery-body/v5` (under
the existing `synapse.stage4.gold.stage10` namespace). `worker-delivery-envelope`,
`worker-invocation`, `delivery-receipt` and the runner's `completed-worker-delivery`
advance to v2. The context retains the full Gold evidence; the worker receives
two neutral data projections:

| Input | Delivery | Binding |
|---|---|---|
| `synapse.worker.task-input/v1` | Exact JSON through `mini -t` | Task SHA-256 and byte length |
| `synapse.worker.local-information-input/v1` | Private temporary file read by the installed Agent extension | Independent information SHA-256 and byte length |

Envelope, invocation, receipt and completed-delivery recovery must agree on
both inputs and versions. Empty information is an explicit `items: []` envelope;
missing information, null and unknown profiles are refused. Legacy record
readers retain their original bytes and do not grant a v2 delivery receipt.
The frozen Mini runtime profile is `mini-2.4.6-split-input-extension/v1`; the
existing runtime-source and SDK digests make an old run's silent upgrade fail.

Mini owns both provider requests and responses. The current extension allows
the public task, its configured templates and actual provider response history,
including provider FormatError correction. An ordinary local tool observation
has no accepted public projection yet: the next query stops before a provider
call with `LocalInformationBoundary`, and the adapter reports `ERROR` /
`mini_local_information_boundary`. A normal multistep coding session therefore
still requires the later local execution/projection work. No local understanding,
procedure application or pre-effect shell isolation is claimed by input delivery.
Mini records `local_interpretation: NOT_PERFORMED` in its retained input receipt.
The real Mini profile requires the existing `worker.accounting` configuration;
an arbitrary external command is not evidence of this SDK boundary.

Acceptance lives in `acceptance/` and `tests/`, never in the product. Independent
real-SDK files `test_mini_input_delivery_acceptance.py` and
`test_mini_information_boundary_acceptance.py` are separate Stage 15 CI jobs.
See `GOLD_KNOWLEDGE_INGESTION.md` for current observed results and remaining scope.

## Freeze and evidence

Run manifest v3 binds `inputs_sha256` to the complete frozen input envelope.
The envelope includes the seed export, project declaration digest, trusted
history heads, runtime source digest, resolved worker executable digest,
locations and freeze time. The operator's declared version labels remain
distinct from those observed byte digests. Resume verifies the actual runtime
and project identity. Gates check that the project declaration still matches;
changing its entitlements cannot retain permission from the old run.

Project authority histories remain shared under their existing mutation
coordinator. Snapshots, retrieval and replay records belong to the run. A
continuing attempt captures current authoritative heads; the frozen heads
remain required history prefixes. Copying history into a second permissive
project world is unnecessary and is not part of this path.

Worker usage remains available in the CLI's `worker_records` JSONL file,
under `payload.materialization_diagnostics.usage`, and in durable worker
completion records. Available fields include input/output/thinking/total
tokens, accounting status and source diagnostics. Missing token totals remain
unavailable. The run budget reads the same normalized usage; it does not
substitute a dollar total or an incomplete trajectory subtotal for tokens.

The six `test_project_gold_*_acceptance.py` scenarios run as separate GitHub
Actions jobs. They cover canonical approval/run, terminal resume, continuation
in a fresh process, changed frozen inputs, changed evidence and task identity
in the compatibility observation. Their worker
and SWE-bench subprocesses are deterministic external stand-ins; the Gold
runtime, C1 controlled changes and C2 report parsing are real. These tests do
not measure model quality, live SWE-bench performance or token savings.

### Project source experience input

A declaration's `knowledge_path` may name `synapse.stage4.gold.knowledge-input/v3`
with exactly `schema_version`, `files`, and `experience_limit` (integer 1–64).
`files` retains the existing reference/path format for task evidence. This mode
freezes actual project source publications and task-scoped retained experience
in `frozen-input/v4`; it does not accept an operator-selected candidate list.
Admission and governed replay still apply to executable behaviors. Raw retained
experience is a separate local-information input and grants no behavior authority.
See `GOLD_KNOWLEDGE_INGESTION.md` for retention, physical reopening, and limits.
