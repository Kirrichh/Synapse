# Stage 16: external test platform

The test bench runs the existing Baseline and Gold, retains the full experiment
plan, reopens the physical evidence and checks the robustness of the safeguards
against predefined mutations. It is a maintained acceptance tool.

The practical goal is to see whether a task is solved, how many attempts and
resources it took and how the result changes on repetition. The product already
executes the task and collects the source measurements; the bench organizes
repeated trials and checks their reports. There is no additional control Gold
and no separate runtime.

Under the agreed change to the Stage 16 plan, the experiment dispatcher lives
in `acceptance/`, not in the product's `gold/paired.py`. The product Gold is
launched through `python -m synapse → synapse.cli.main`. Baseline uses the
existing `run_baseline_task`, which executes the same admitted agent profile
through the universal execution port, without Synapse context, memory or local
information. The C2 evaluator and the Stage 3A writer remain unchanged.

State of the target Gold: local information is delivered to the agent
separately from the task; the Synapse model broker lets only the protocol's
public conversation through to the provider, and Synapse interprets the agent's
proposal. The historical runs below were executed by the former built-in worker
and do not confirm this loop.
The exact boundary is described in the [ingestion instruction](../../../docs/GOLD_KNOWLEDGE_INGESTION.md#what-the-worker-receives).

## What the result means

`DIAGNOSTIC_COMPLETE` means that both actual runs finished, their measurements
and sources are available, and the parameters of the diagnostic pair matched.
This is not yet counterfactual evidence of savings from reuse. The old Baseline
and Gold have different execution policies: Gold additionally passes through an
accepted plan and Controlled Change. The report explicitly returns
`EXECUTION_POLICIES_DIFFER` and forbids an economic claim. C2 receives the real
`GOLD_WITH_CARRY` and keeps its `INVALID_GOLD_WITH_CARRY`; renaming the arm so
that C2 accepts it is forbidden. All checked axes, including the difference in
execution policies, are present in `parameter_alignment`;
`execution_policies_comparable` stays `false` on any mismatch. Diagnostic
completeness does not declare these policies identical.

In the v2 report, `runs` contains a row for every planned run: the execution
state, the original outcome and its reference, the number of attempts, tokens,
known cost, duration and measurement status. `task_results` groups repetitions
by task and arm: the number of requested/finished runs, of resolved, unresolved
and unknown outcomes, and the mean, spread and range of the measurements. An
unexecuted replicate stays in the denominator; mismatched replicate parameters
block their common mean. The original attempts, calls and reconciliation
reports are kept in the detailed data.

Task status and measurement completeness are independent. For example, a
`FULL` with a lost resource report remains the originally confirmed `FULL`,
while the accounting gets `INCOMPLETE`. Missing evidence of the result itself
gives no new confirmation of success. A green process exit does not replace
these fields.

The difference is computed as **Baseline minus Gold**. Tokens, external
duration, money and infrastructure measurements are given separately. Negative
differences are kept. A missed run stays in the sample with an unknown value;
the mean of an incomplete sample is not computed. For a complete sample, the
size, mean, sample standard deviation and range are published. The unit of
observation is a paired run; task membership is retained. This diagnostic
profile builds no confidence intervals and no inferences about the population.
The replicate parameters of one task must match too; a different
base/model/budget is not masked by a replicate number. The report separately
records the code and environment of the current evaluator, keeping the earlier
execution identities from preregistration.

The provider cache is visible in the original `LLMCallRecord`s and does not
create semantic reuse. Activation is checked separately per Stage 14/15: the
producer's publication, retrieval, snapshot, replay, admission before
consumption, context, worker result, MechanismUseRecord, promotion,
verification and outcome. The references to the outcome of the producer's
attempt and to the result of its whole run are distinct; they are not
rewritten. Only a complete comparable diagnostic pair with such a chain counts
toward the activation threshold. An unmet threshold gives
`MECHANISM_NOT_ACTIVATED / INCONCLUSIVE`.

Infrastructure reports keep the original Stage 15 limitations: CPU refers to
the measured owner threads, and child processes are excluded; I/O refers to the
measured persistence primitives, not to the whole disk; intervals of different
buckets cannot be summed mechanically. External duration is the sum of the
bench's observed invocations, without the operator's waiting time between
them. Prices are not invented; a missing cost stays unknown. Baseline
accounting does not turn its unmeasured file operations into measured Gold I/O.

## Preparing and running

Install the dependencies from the repository's pinned profile:

```bash
python -m pip install -e . pytest
```

The coding agent is not part of the product. The operator admits their own
profile (`docs/UNIVERSAL_AGENT_EXECUTION.md`); the acceptance scenarios use the
acceptance model agent from `acceptance/agents/coding_agents.py`.

Each arm of each replicate needs separate clean Git repositories, run
directories and, for Gold, a separate project state. Use independent clones,
for example `git clone --no-local`; shared worktrees and Git alternates are not
allowed. The run directory does not exist before the run. Store the
experiment's data and results outside the working checkout of the code. Keep
the original stores together with the experiment. This is a separation of
experimental data; it does not create a security sandbox for worker processes
and does not isolate the shared load of the host.

The Gold definition points to the ordinary, already prepared product inputs:

```json
{
  "arm": "GOLD",
  "repo_root": "/experiments/r0/gold/repo",
  "run_root": "/experiments/r0/gold/run",
  "state_root": "/experiments/r0/gold/project",
  "declaration_ref": {
    "path": "/experiments/r0/gold/input.json",
    "sha256": "<SHA-256 of the actual bytes of input.json>",
    "bytes": 1234
  }
}
```

`declaration_ref` is created by the `source(path)` function from `protocol.py`;
`bytes` and the hash above are placeholders and must be replaced with the
actual values. The Gold input uses schema v4 and an `agents` configuration
with one admitted profile. `cli_timeout_seconds` in the definition is optional;
the default is 600 seconds.

The Baseline definition contains existing contracts without new execution:

| Field | Contents |
| --- | --- |
| `arm` | `BASELINE` |
| `repo_root`, `run_root` | Absolute independent paths |
| `base_revision`, `max_attempts` | Exact base and attempt limit |
| `task` | `BaselineTask` fields: task_id, instance_id, statement, allowed_scope |
| `agents` | The same `synapse.agent.configuration/v1` configuration as in the Gold input |
| `oracle` | `SWEbenchHarnessOracleConfig` fields, with its own swebench_work_dir |

Credentials are passed through the environment variable named in the profile's
`model_access`. The secret's value is not part of the protocol. The agent's
identity is its `runtime_identity` from the admitted profile; both arms must
match on it. The host profile is measured directly and contains only
explicitly listed non-secret settings; contention and the provider cache are
not declared controlled.

For Gemini, the Synapse model broker accepts the official endpoint
`https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`
and a model name without a routing prefix, for example
`gemini-3.1-flash-lite`. The credential environment can be `GEMINI_API_KEY`.
The capture records the provider `gemini` and the profile
`gemini-openai-chat-usage/v1`; the OpenAI-compatible wire format does not
change the provider's identity. Prompt/completion totals are not added again
with cache/thinking. If Gemini sent no subset details, they stay unknown.
See the [official compatible API](https://ai.google.dev/gemini-api/docs/openai).
A paid response that did not become a proposal is also accounted for: it is
part of the public conversation and of the agent's response inventory.

### Preparing the first live scenario

`astropy__astropy-12907` is pinned to base
`d16bfe05a744909de4b27f5875fe0d4ed41ce607`. The preparation command checks the
SHA-256 of a specific SWE-bench Verified revision, stores the original task
separately from the evaluator data and creates a clean checkout. The
experiment data lives outside the Synapse repository.

```bash
python -m pip install 'swebench==4.1.0' 'pyarrow==21.0.0'
python -m acceptance.stage4.stage16 prepare-astropy --root /experiments/astropy-01
python -m acceptance.stage4.stage16 calibrate-astropy --root /experiments/astropy-01
```

The `calibrate-astropy` command requires Docker. It records the actual image
ID and runs the existing `SWEbenchHarnessOracleRunner` for two calibrations: a
comment-only change keeps the bug, and the SWE-bench reference patch must fix
it. The real outcomes and the absence of an infrastructure error are checked;
candidate patches, reports and logs are kept. This calibration has no model
responses. The `Astropy Pilot Preparation` workflow runs it before the live
pair is prepared. In a PR check, the workflow also prepares the pair's inputs
and checks a short exchange with Gemini. The full paid run is started
separately: Actions → Astropy Pilot Preparation → Run workflow, the required
branch and `run_pair: true`. This flag is off by default: a documentation edit
must not pay for the experiment again. The short connectivity check allows
three requests of 30 seconds each and 120 seconds for the whole process,
including saving the results. It does not check the Astropy solution.
The SWE-bench 4.1.0 pin matches the oracle's existing CLI contract, including
`env_image_tag`: 3.0.15 does not have this argument yet, and version 5 changed
the CLI. The check uses the existing oracle adapter without additional
launchers.

### Observations from the first live trial

These are diagnostic observations, not evidence that memory is useful.
The original failures are not deleted when the bench is fixed or on a new run.

In the [first run](https://github.com/Kirrichh/Synapse/actions/runs/34158843317)
Gold stopped before calling the model: the preparation passed the former
built-in worker an extra argument that the pinned profile forbids. Baseline
solved the task on the second attempt, spending 201 490 tokens. The
preparation fix removed this argument for both modes; the product profile's
restriction stayed unchanged.

In the [next run](https://github.com/Kirrichh/Synapse/actions/runs/34159761879)
both modes actually executed:

| Mode | Task result | Attempts | Tokens | External duration |
| --- | --- | ---: | ---: | ---: |
| Baseline | Confirmed by an independent check | 1 | 87 789 | 111.97 s |
| Gold | Candidate rejected: an extra file outside the permitted scope | 1 | 146 403 | 493.66 s |

Preparing the second pair took a separate 33.31 s. The confirmed monetary cost
is unknown. The model configuration, the starting commit, the task description
and the worker agent's limits matched; the modes' execution policies differed.
In Gold, the token limit is checked between attempts: the configured 100 000
is not a hard limit on consumption within one attempt. The overrun is kept in
the actual accounting.

The Gold trajectory shows a fix to `astropy/modeling/separable.py`, a
successful reproduction after the edit and 11 passed tests available to the
agent. But the agent also created `reproduce_issue.py`, although the task
allowed changing only the source file, and exhausted 12 steps without a
completion command. The adapter kept the proposed edit; materialization
rejected it with `REJECTED_SCOPE_VIOLATION`. It was this refusal, not the
number of steps, that produced the final `NO_CANDIDATE`. The independent final
checking tool was not run for Gold. Removing the extra file from the candidate
unilaterally and counting a success would have meant changing the observed
result.

The publications of source knowledge, their delivery through replay, the
context and the physical reconciliation are confirmed. Beneficial use of
memory is not confirmed. The `DIAGNOSTIC_COMPLETE` status refers to diagnostic
completeness; the outcome of the Gold task remains unsuccessful. The separate
short Gemini check in the same workflow reached two requests, but exceeded the
earlier overall limit of 45 seconds and did not finish saving the sources.
Raising its limit to 120 seconds does not change this historical failure or
the budgets of the Astropy task.

`CALIBRATED` confirms only the oracle environment. The following command
checks the worker agent's real commands, populates memory and freezes one live
pair:

```bash
python -m acceptance.stage4.stage16 prepare-astropy-pair --root /experiments/astropy-01
python -m acceptance.stage4.stage16 run --protocol /experiments/astropy-01/pair/protocol.json --experiment /experiments/astropy-01/experiment --approve-pending
python -m acceptance.stage4.stage16 report --experiment /experiments/astropy-01/experiment --format table
```

Both modes get separate clean clones, the same task, the model
`gemini-3.1-flash-lite`, a limit of three attempts and identical verification
commands. The agent uses 12 steps and 600 seconds per attempt.
The `GEMINI_API_KEY` key is needed at the `run` step; the preparation does not
call the model. The workflow runs these steps for a branch of the same
repository; external forks do not get the secret and do not run the live pair.

The verification command mounts only the permitted `separable.py` from the
current checkout into the immutable Docker image of the calibration. Python
and the Astropy dependencies come from this image. Before learning, the public
bug is actually reproduced in both clones and the pre-existing tests pass. A
broken command blocks the preparation before the model is called. The
reference fix and the evaluator's hidden checks are not included in the task,
the memory or the mounted files.

Gold receives two records through the real `python -m synapse project learn`:
a verified fact about the structure of the source file and a historically
verified reproduction procedure. The new task has its own contract and
observation; the knowledge source is not passed off as a past successful Gold
run. The full protocol is created after these operations, with seed 17 and one
pair. There are no fixture publications or permissive test probes on this
path.

`pair/preparation-result.json` holds references to the environment check
results, the learning receipts and the total duration of the preparation. The
`learn-*-result.json` files keep the process exit code, the original response,
stderr and the full duration of the CLI call. The verifier's internal
`elapsed_ns` measures only the check, without publication; it must not be
passed off as the full cost of populating memory. The costs of the pair itself
are kept separately in the bench's ordinary report. Failures are kept; the
pair's readiness must not be inferred from one successful previous step.

This pilot checks the connectedness of real execution. Both modes are given
the same public way of reproducing the bug in advance; the differences in
their execution policies are kept. Delivery of the sources after replay does
not prove that they helped the model and does not create a
`MechanismUseRecord`. A conclusion about transfer of experience needs separate
control tasks; this run does not replace them. The ingestion command and its
boundaries are described in the [Gold instruction](../../../docs/GOLD_KNOWLEDGE_INGESTION.md).

The design file lists **all** tasks, replicates and both arms in advance:

```json
{
  "experiment_id": "pilot-diagnostics-01",
  "seed": 17,
  "minimum_activated_pairs": 1,
  "specification": {
    "version": "Stage4-v2.2; external Stage16 acceptance profile",
    "path": "/experiments/governing-specification.docx"
  },
  "pairs": [{
    "pair_id": "task-a-r0",
    "task_id": "task-a",
    "replicate_id": 0,
    "inputs": {
      "BASELINE": "/experiments/r0/baseline-definition.json",
      "GOLD": "/experiments/r0/gold-definition.json"
    }
  }]
}
```

```bash
python -B -m acceptance.stage4.stage16 freeze --design /experiments/design.json --output /experiments/protocol.json
python -B -m acceptance.stage4.stage16 run --protocol /experiments/protocol.json --experiment /experiments/journal
python -B -m acceptance.stage4.stage16 run --experiment /experiments/journal --approve-pending
python -B -m acceptance.stage4.stage16 report --experiment /experiments/journal > /experiments/assessment.json
python -B -m acceptance.stage4.stage16 report --experiment /experiments/journal --format table
```

The table is a short view of the same physically checked report. It shows the
task, replicate, arm, completion, outcome, attempts, tokens, seconds and
accounting completeness. The JSON keeps the detailed evidence and the
measurement limitations.

Freeze binds the actual specification bytes, code revision, product/verifier
source hashes, host profile, the full schedule, the nested inputs and the
initial repository/knowledge states. The order of arms alternates within each
task; the initial orientation and the order of pairs are determined by the
seed. For one replicate or an odd number of replicates, perfect balance is
impossible and is not claimed.

Without `--approve-pending`, the bench stops at the ordinary Gold approval
request with exit code 3. The flag performs the standard operator action
through the canonical CLI. A failed/interrupted arm gives exit code 2; a
completed dispatch inventory gives 0. The `report` command returns 0 when the
report is built successfully, including an `INCOMPLETE` report; interpret its
fields, not the process exit code, as the acceptance verdict.

## Recovery and ownership

The SQLite journal uses WAL and `synchronous=FULL`. A durable STARTED precedes
the effects; an OS lock rules out two dispatchers. The history binds the
protocol identity, slot, order and previous event hash. The check is not a
protection against a complete malicious rewrite by a trusted local operator:
this is the project's existing local trust boundary.

A repeated `run` command continues the earlier Gold run through
`project resume`. The legacy Baseline has no durable resume: losing the
process after STARTED leaves `INTERRUPTED`, unknown usage and the retained
sources; repeated effects are not started. Completed arms are not executed
again. Substituting the inputs, initial state, code, environment, order or
shared repository allocation blocks the run. If the receipt of an external
call is lost, its duration is unknown: `duration.total_ns = null`. The sum of
the surviving receipts stays separately in `observed_subtotal_ns`. Gold
recovery does not turn this sum into the full time and does not create the
missing measurement. Such a comparison gets `INCOMPLETE`.

The report is built under the same lock and rereads the retained sources
through the existing reconciliation owners. Losing a file blocks a new
complete comparison while keeping the historical receipts and domain outcomes.
A standalone JSON does not replace the stores.

| Module | Responsibility |
| --- | --- |
| protocol.py | Immutable design and physical preconditions |
| harness.py | Durable scheduling, exclusion of concurrent dispatch, and recovery |
| execution.py | Boundary of the existing Baseline API and Gold CLI |
| run_evidence.py | Translation and rechecking of the arms' physical evidence |
| assessment.py | External diagnostic assessment of the full set |
| mutations.py | Isolated weakening, killer and restoration evidence |
| __main__.py | Operator interface of the external bench |

## Acceptance and mutations

Heavy scenarios live in separate `test_*_acceptance.py` files and separate CI
shards. The earlier Stage 4 suites, C2 regression, dependency direction,
ownership and canonical entrypoint checks are kept. Added here are actual
paired execution, full replicates, real mismatches, two different recovery
contracts and activation through a real agent process and the model broker
with a controlled HTTP provider and an external oracle.
The separate scenario `test_task_result_acceptance.py` executes a real fix in
both arms and checks the independent oracle, Gold `FULL`, costs, the table
report and the preservation of the outcome when measurements are physically
lost.
Controlled responses check the architecture; they do not measure the quality
of a commercial model and do not replace the technical pilot.

`mutations.json` defines six Stage 16 weakenings, including a lost time
interval wrongly counted as zero usage. This is an addition to the earlier
adversarial suite, not a claim that the whole Appendix F matrix has been done.
Each CI shard first runs the unmodified killer, changes one specified region
in an isolated checkout, requires the expected failure and checks the return
of the original bytes/code identity and of a clean Git status. An environment
error does not count as KILLED. JSON, JUnit and logs are kept as CI artifacts
with the revision and patch identity.

```bash
python -B -m acceptance.stage4.stage16.mutations --case S16-MUT-FINGERPRINT --output /experiments/mutant-fingerprint
```

The mutation runner needs a clean commit. Any SURVIVED, an unexpected error or
a failed restoration turns the corresponding CI check red. A green bench does
not close Stage 4: full normative traceability, the pilot, an economic design
with sufficient causal isolation and human acceptance require separate data.

The design of blocks and paired differences relies on
[NIST: randomized block designs](https://www.itl.nist.gov/div898/handbook/pri/section3/pri332.htm)
and [NIST: paired observations](https://www.itl.nist.gov/div898/handbook/prc/section3/prc311.htm).
The durability boundary follows
[SQLite synchronous](https://www.sqlite.org/pragma.html#pragma_synchronous) and
[SQLite transactions](https://www.sqlite.org/lang_transaction.html).


## Initial knowledge from real sources

`project learn` is implemented in the canonical product CLI. The order of
inputs, the restrictions on procedures and the transfer of the result into
`knowledge_path` are described in the
[maintained instruction](../../../docs/GOLD_KNOWLEDGE_INGESTION.md).
The `test_source_*` acceptance files are split by publication, procedures,
recovery, retention, consumption and refusal under a changed environment; CI
runs them separately. The `test_src_layout_*` files check a project with
declared import roots (binding contract v2): learning, refusals, study and
freezing of the Gold inputs — each heavy scenario in its own file.
`source_recall_profile_measure.py` is a reproducible measurement of the recall
profiles v1 and v2 on pallets/flask and Synapse
(`reports/source_recall_profile_v1.json`); it is a measurement, not a test.
They use real stores and separate processes, without a test corpus admitted in
advance. A deterministic external worker checks the delivery and does not
serve as the result of a live experiment.

Delivery of source knowledge does not satisfy the earlier activation
threshold, which requires an independently confirmed prevention of a repeated
C1 dispatch. A missing `MechanismUseRecord` must not be invented to raise the
assessment. The new profile requires a separate measurement of the practical
benefit of the received content.

The practical sequence of the trial: first one live diagnostic pair with a
verified worker environment, then a series fixed in advance. The proposed
series contains two preparatory tasks and four control tasks: two for useful
transfer, one with a change of conditions and one with irrelevant experience.
Three repetitions of each of the two modes give 24 runs. The corpus for each
control replicate is taken from one initial state; the answers of the control
tasks do not reach other replicates. The scenarios and criteria must be fixed
before the first model response. This is a sampling plan, not a statement that
these tasks have already been prepared or executed. The current exact-commit
profile limits transfer between versions; the comparison with Baseline keeps
the differences in execution policies.
