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

The governing task is separate from the planner's proposal. Intent v3 includes
`task_contract_ref`, `target_bindings` and `behavior_refs`. Rehashing a proposal
cannot authorize another task, target, behavior, scope, effect or acceptance
condition. Required behavior references must be admitted for the current
attempt and present in the worker's selected knowledge.

## Operator input fields

Every field below is required; unknown fields and duplicate JSON keys fail
validation. Use the existing records' `to_dict()` methods for their wire form.

| Field | Content / owner |
| --- | --- |
| `schema_version` | `synapse.stage4.gold.experiment-input/v1` |
| `run_id` | New experiment identity |
| `config` | `GoldRunConfig.to_dict()`: task, instance, base, worker provider/model, oracle class identity, environment, budgets, attempts, replicate identity and fallback policy |
| `versions` | `GoldRunVersions.to_dict()`: specification and policy version/digest, implementation revision |
| `task_contract` | `GoverningTaskContract.to_dict()`: task ID/statement, revision, scope, capabilities, target/behavior refs, typed effects and acceptance criteria |
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
target reference. Behavior references use the existing library subject
identity, not an arbitrary label or a raw behavior transcript.

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
needs no pair assessment. Ranking follows the task's explicit behavior order;
no learned ranking or retrieval-quality improvement is claimed.

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
