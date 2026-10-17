# Gold memory and library ingestion

This instruction describes a maintained product process: how a connected
project obtains its first verified knowledge from its own repository. It
extends the Stage 12–15 contracts with a new knowledge provenance and does not
change the meaning of task results. Stage 16 remains the external acceptance of
this process.

The canonical ingestion operation is `python -m synapse project learn`.
It retains the study experience regardless of the result. For an explicitly
checkable claim about the source files it also publishes knowledge through the
existing admission, publication, retention and lineage. A later `project run`
reads this publication from the same stores. Acceptance goes through separate
CLI processes, a real verification of the procedure, replay and an external
worker process. This confirms the acquisition, verification, retention and
delivery of knowledge. The supported profiles of local application and their
acceptance are described below. Usefulness on live tasks and savings require a
separate comparable experiment.

DSL execution and CLI startup are accelerated as part of the
[host interpreter optimization](INTERPRETER_PERFORMANCE.md). The boundaries of
loop limiting and measurements on fixed programs are published there. These
measurements do not replace the experiment on memory usefulness and model call
savings.

### Source scope in worker information

New delivery uses the profile
`synapse.stage4.gold.source-delivery/matched-paths-v2`. From a jointly studied
operation the worker receives source bodies, symbols and information about
missing parses only for the `match.paths` of the current request. A dependency
is included in this output when its path lies within the declared task scope;
an import by itself does not widen access.

The recipe and the command result belong to the whole source operation. If
only part of its paths is selected, these two fields are not delivered:
`operation_observation_scope=OMITTED_UNSELECTED_SOURCE_PATHS`. This rules out
unrelated code getting back in through the command or its output. When the
whole scope is selected, the original observations are delivered unchanged.
The full operation stays with the owner of the evidence; its retained bytes,
provenance, verification status and admission rules do not change.

The profile is bound to the retained worker context. A new context build
rejects a delivery without the current profile. Historical envelopes without a
profile remain verifiable by the earlier exact projection; this mode is used
only to verify recorded history. New delivery from an old frozen snapshot is
also limited to the selected paths. The neutral projection code is in
`source_delivery.py`; capture and physical opening of evidence remain in
`source_snapshot.py`.

Read compatibility for old delivery does not permit an old Gold run to be
continued automatically after the runtime is replaced. Its existing binding to
the runtime source files is kept: a physical verification of an old pending
run on the changed code got `GOLD_UNAVAILABLE` before the worker call. At the
same time, the completed old history opens successfully without changes to the
retained bytes.

## Connecting research memory — the exact reuse profile

New owner jobs use `element-owner-lifecycle/v4` with
`active-memory-frame/v4`. Profiles v1–v3 are kept for previously recorded
jobs; resume does not move them to the new profile. After
`OUTCOME_RECORDED` the same owner writes `CONSOLIDATED` to the existing
ProjectMemoryStore. Consolidation reads the completed run, the publications
bound to its attempts and their physical C1/oracle/lineage evidence. It does
not create a second engine, publisher, admission or execution route. A failure
of this suffix does not repeat the completed task; `project resume` completes
the maintenance. A repeated consolidation returns the earlier immutable record.

The four logical layers introduced in Active Memory Frame v2 are kept in v4
alongside the eight element views:

| Layer | Supported content |
| --- | --- |
| declared | Explicitly declared recipes from studied sources |
| learned | Exact claims of published results with primary references |
| working | The current task; a new goal awaits fresh verification |
| episodic | Selected completed episodes |

These are projections for different purposes, not four independent writers of
one experience. `project_learning.build_memory_layers` receives verified claims
from the consolidation: an episode record, a retry or an owner counter does not
by itself move content into learned or grant admission. Matching
assertion/origin pairs are merged within the projection; new independent
episodes remain history. This admission rule is not a guarantee of a constant
size of the journal or of the whole process memory.

CONFIRMED means confirmation of an exact historical claim about a patch;
REFUTED is an independent negative result for the same claim. The absence of a
confirmed publication remains PROVISIONAL. Contradictory observations are
retained as CONFLICTED. Repeats and copies do not increase confidence and do
not become independent witnesses. This profile **does not give birth to a
generalized habit**: `generalization=NOT_ESTABLISHED` is kept even after many
successful repeats. Admission of generalized habits based on independent
primary sources, D1/D2 and semantic search remain separate stages.

Synapse forms the fix candidate (`stage10/local_candidate.py`), not the
pluggable agent. The agent is an ordinary admitted registry profile; there is
no built-in agent. It receives only neutral data and returns only the text of
its model's typed proposal (`local_edit_proposal`). Synapse parses this text
against the same task and local information bytes that it delivered, under the
frozen protocol profile, and checks the scope. This happens for all profiles
v1–v6. The agent cannot pass on a ready interpreted result: such a response is
rejected. An unparsable proposal, a proposal together with file changes, or
its absence becomes an explicit refusal
(`local_edit_refused`, `local_edit_proposal_absent_or_with_effects`).

An exact repeat is also a Synapse move. Before the agent is called,
`Stage10WorkerContextAdapter.dispatch` interprets the same bytes. If the court
admitted exactly one exactly applicable patch, Synapse forms the candidate
itself. All of the following are required at once: court admission
(`procedural_memory`), current admitted/replayed execution feedback, the
original bytes, an exact revision match, allowed paths and full coverage of
the expected changes. Competing variants, a contradiction, a data change,
missing material or incomplete coverage lead to an ordinary agent call.

Such a candidate carries `planning_route=EXACT_MEMORY`, and the delivery
carries the explicit status `SYNAPSE_EXACT_MEMORY` with the transport
`synapse.exact-memory/v1` and zero token consumption. The agent process is not
started; there are no `agent-executions` records and no call capture. The
agent transport cannot claim this status or name, and Synapse cannot pass its
own move off as a process run. From there, verification is as before: approved
plan → C1 → independent oracle. Gold independently reproduces the choice from
the delivered data, and the accounting reconciliation accepts such an attempt
only without agent calls. The economics are observed and do not govern
admission or the choice of procedure.

Run decisions are declared by Synapse protocol capabilities
(`PROTOCOL_CAPABILITIES` in `synapse/worker/local_edits.py`), not by the
agent's name:

| Profile | Full positive feedback | Checked partial patch | Automatic memory move |
| --- | --- | --- | --- |
| v1 | — | — | — |
| v2, v3 | yes | — | — |
| v4 | yes | yes | — |
| v5, v6 | yes | yes | yes |

The identifiers v1–v5 (`mini-2.4.6-local-edit-proposals/vN`) are frozen data
of earlier results; their spelling ties nothing to any agent. Any admitted
agent can carry any version; the new neutral profile v6 is interpreted as v5.
The profile only permits the route; Synapse decides and executes it. The agent
declares the protocol in its frozen configuration (`native.protocol` of an
STDIO profile, `local_edit_protocol` of an adapter). An agent that returns
ready patches itself declares no protocol and gets all three capabilities.

```json
{"native": {"protocol": "synapse.worker.local-edit-proposals/v6"}}
```

Provider call accounting does not read the agent's private trajectory either.
The capture of HTTP calls remains Synapse's independent evidence. At its
boundary the agent submits a neutral response inventory
`synapse.agent.response-inventory/v1` (`synapse/llm/capture.py`): the
accounting profile, the declared number of calls and, for each response, the
logical capture identifier and the usage. The inventory is retained when the
call is closed (`inventory_ref`), next to the agent's raw response. The stage15
reconciliation compares it with the capture: profile, number of calls, missing,
extra and repeated responses, matching usage. The agent submits the inventory
in the diagnostics of its STDIO response (`response_inventory`). Captures
retained before this contract have no inventory and honestly remain
unverifiable: a missing or unreadable inventory gives
`worker_response_inventory_missing_or_unreadable`.

The commands remain `project run` and `project resume`. To compare the modes:

| Mode | Agent protocol | knowledge-input/v4: run_memory_selection |
| --- | --- | --- |
| Automatic local proposal | v6 (or historical v5) | ALL |
| Same memory, mandatory model call | v4 | ALL |
| No memory of completed tasks | v6 | NONE |

The last row keeps the studied source files; it is an ablation of run memory,
not an experiment with completely empty memory. The old profiles v1–v4 and the
retained owner v1 jobs do not get the new rules retroactively. A new snapshot
pins both the episode history and the matching consolidation cut.

The checks are split: pure contracts of local selection, memory layers and
delivery status; a separate heavy scenario
`test_exact_memory_cycle_acceptance.py` through the CLI, with a restart, a mode
comparison and a new oracle for every completed fix. The scenario uses a
controlled provider response and does not prove model quality on new tasks or
monetary savings under production load. AS2 remains locked.

Local verification of the implementation on 20 September 2026 (Python 3.12,
Mini 2.4.6 installed): 815 checks of the affected contracts, memory, Mini and
architectural boundaries passed in 51.44 s. A separate full cycle passed in
2322.35 s:

| Run | Run memory / profile | Result | Model calls | New oracle executions |
| --- | --- | --- | --- | --- |
| First solution and consolidation | ALL / v5 | FULL | 1 | 1 |
| Exact repeat after restart | ALL / v5 | FULL, EXACT_MEMORY | 0 | 1 |
| Retained memory, slow mode | ALL / v4 | FULL | 1 | 1 |
| Control without run memory | NONE / v5 | No candidate | 1 | 0 |

A repeated `project resume` of the automatic run after later tasks returned
the same result: there were no new model calls, oracle executions or memory
records. The control without a candidate left the unverified observations
PROVISIONAL and created no confirmed claim. The original repository and the
previously frozen inputs were preserved. The ownership/dependency and
pre-commit checks passed: 45 Synapse examples were parsed; CVM corpus coverage
is 0.935620 with a threshold of 0.9332. This is a targeted check, not a full
repository run. The saving of one model call was measured; a speedup of the
whole process was not established — physical verification of the evidence
chains remains costly. The runs are sequential, and the history grows between
them. This scenario checks the lifecycle and the mode boundaries; comparing
the quality of the three modes on one unchanged snapshot requires a separate
experiment.

## Coding agent — a pluggable profile; no built-in executor

The built-in Mini executor is removed from Synapse entirely: its adapters,
models, environments, provider transport, request policy, the `mini` factory,
the Baseline wrapper `MiniInvocationConfig`, the `gold-worker` extra
(mini-swe-agent, LiteLLM, OpenAI SDK) and the related tests. The coding agent
is only an admitted registry profile (`synapse.agent.configuration/v1`)
selected through `agent_selection.select_coding_agent`. The only execution path
is `AgentExecutionPort`, for Gold and for Baseline; there is no parallel path.

| Responsibility | Owner |
| --- | --- |
| Agent access to the model, provider key, call capability | `synapse/agents/model_broker.py` (`ModelBroker`) |
| Public protocol conversation with local information | `model_broker.PublicConversation` + `local_edits.public_messages` |
| Protocol and proposal interpretation | `synapse/worker/local_edits.py`, `stage10/local_candidate.py` |
| Broker lifecycle in an agent call | `synapse/agents/stdio_adapter.py` (`LOCAL_BROKER` profiles) |
| Durable accounting of physical calls | `stage15/worker_accounting.py` (`WorkerAccounting.open_capture`) |
| Baseline-arm | `synapse/experiments/swebench/baseline.py` through `AgentExecutionPort` |

New runs accept only experiment input v4 (`agents`). Agent selection is
orthogonal to the way targets are resolved: v4 admits both explicit
`target_records` and an automatic task; frozen input v7 carries
`target_resolution`, `planning_profile` and a memory snapshot only when the
declaration selected them. A profile with `native.model_access` additionally
pins `resource_profile`. Runs frozen with the earlier built-in `worker` remain
readable records (memory, court, observations), but execution and resume refuse
with an explicit configuration error. Their captures without an agent response
inventory remain unverifiable, not guessed.

The operator configuration of a profile and the agent process contract are
described in `docs/UNIVERSAL_AGENT_EXECUTION.md`. Acceptance uses the
acceptance agent `acceptance/agents/coding_agents.py`: a deterministic process
without a model and a model agent through the broker. Neither of them is part
of the product.

Package verification on 24 September 2026 (Linux, Python 3.11, without Mini
installed): all 114 acceptance files of stage12–16, `acceptance/agents` and
`acceptance/runtime` passed with one process per file; stage 10 — 256 passed,
stage 11 — 84 passed in 28 min 58 s. The longest scenario,
`test_gold_memory_slices_acceptance.py`, took 1 h 35 min;
`test_court_acceptance.py` took 43 min 42 s. Eleven files that failed in the
first run were rerun after fixes: the acceptance agent explicitly reports an
empty proposal; the replicas of the paired experiment use one agent; resource
accounting expects one broker measurement per physical call instead of the
three HTTP operations of the earlier executor. `tests/` per file: 5069 passed,
12 skipped, 3 failed. The `test_swebench_gold_production_tripwire.py` failure
reproduces on the base revision without this package; the two
`test_swebench_measurement_output_boundary.py` failures compare the
uncommitted tree with a fixed list of files and are checked on a clean tree
after the commit. No live provider call was made.

The journal sections below keep past observations; mentions of Mini in them
refer to the removed executor. Renamed heavy files:
`test_*_mini_acceptance.py` → `test_*_agent_acceptance.py`,
`test_mini_local_edit*_acceptance.py` → `test_agent_local_edit*_acceptance.py`.
Scenarios that checked the internals of Mini (FormatError, Environment,
delivery through `mini -t`, continuation by Gemini tool state) are replaced by
`test_public_conversation_acceptance.py`; the duplicating
`test_agent_profile_gold_acceptance.py` is removed — every heavy scenario now
goes through an admitted profile.

## Current PR108 profiles

The canonical commands remain `project connect/learn/run/resume`.
A new `experiment-input/v4` input with an admitted agent is frozen as
`frozen-input/v7`; the earlier `experiment-input/v3` used frozen-input v6.
Old task, source request and frozen-input versions keep their rules;
they get no new permissions when read.

### Archive D4–D6: creation, refusal reasons, per-object admission

`PATH_CREATED` is resolved through `CreationBinding`: the exact path must be
absent from the declared Git commit. An existing path, a combination of
creation with a modification of the same path, or going outside the scope is
rejected. The project model represents such an object as a path-level
`CREATION` element: no symbols or content are attributed to a source file that
does not exist yet.

For task v3 a controlled change may cover several explicitly listed
`PATH_MODIFIED`/`PATH_CREATED` effects. The scope must exactly match their
paths; agent profile selection and the worker request receive the task's
effect classes, and C1 applies one verifiable patch. This is an extension of
the target contract, not a promise that any agent generates a new file: for
example, `synapse.worker.local-edit-proposals/v6` requires a non-empty `old`
in every replacement.

The candidate materializer retains `worker_report.summary`,
`worker_report.failure_reason` and `worker_diagnostics`, including for
`NO_PATCH`. The refusal reason is available in the attempt history but remains
a worker message: it does not replace the oracle, the court or the
verification of the result.

Compatibility is decided per object. `assemble_run_environment` first records
evidence for the whole declared set of candidates; when compatible ones exist,
only `COMPATIBLE` candidates with `COMPLETE` evidence enter the consumption
snapshot. If there are no compatible ones, the original set passes the gates
to retain the refusal decision: the run returns `NOT_ADMITTED`, and the
executor is not called.

Retrieval uses a single `gate_subject_refs`: the compatible candidates if there
are any; otherwise all resolved descriptors for the recorded refusal. Before
the gate the canonical run requires a resolved result of a new conflict scan.
An incomplete scan or an unresolved conflict gives `CONFLICT_SCAN_INCOMPLETE`
or `CONFLICT_UNRESOLVED` respectively; the retained evidence stays in the
history. An earlier scan from the environment assembly cannot replace this
check.
The loader requires the verdict subjects to match this set exactly before it
reads the content of the candidates. An incomplete verdict or additional
subjects give `CANDIDATE_SET_INCOMPLETE`. The full enumeration, the exclusion
reasons and the original frozen set are retained in the audit; an excluded
object does not block compatible objects. A genuinely empty enumeration or the
absence of resolved descriptors does not create an artificial gates decision.
Subsequent gates, freshness and the check of the specific use are mandatory
for every object that passed. All gates keep the requirement of the same
subjects as the previous decision: a change of the set after the chain has
started is rejected rather than rewriting the already recorded decision.
The formats of retained decisions and their historical readers are not
changed by this fix.

Contract owners: `bindings.py`, `task_targets.py`, `project_model.py`,
`run_environment.py`, `run_retrieval.py`, `runner_composition.py` in
`synapse/experiments/gold/`; attempt reasons —
`synapse/worker/candidate_materializer.py`.

### Procedures and plan selection

The new source-publication-request v3 retains the verified content and the
typed procedure `source-task-coverage/v1`. The procedure inputs are computed
from the actually resolved bindings of the task and the source. The CVM
performs the branching and the arithmetic; the return value describes
applicability, coverage and the remainder. The actual inputs, activity and
terminal snapshot are retained and verified on a repeated read. This is a
supported structural profile, not the execution of an arbitrary procedure
extracted from natural language.

The operation-plan v2 plan stores all considered methods, references to
physical observations, the selected path partition and the search boundaries:
up to 128 methods and 32 paths. The rule selects the largest set of paths not
yet covered; a tie is resolved by the canonical order of references. Paths
without a suitable method are explicitly passed to the public proposal. The
selection changes the operations, their inputs and the plan semantics. The
retained basis is verified against the original observations on resume.

Approval request v4 permits a specific partition rule within the unchanged
task effects, capabilities, conditions, participants and ordered verification
commands. The permission does not replace admission, compatibility,
point-of-use freshness or C1. Structural coverage does not prove the
correctness of the code; C1 and the independent oracle check it after a new
execution.

### Elements and persistent memory owners

`project_model.py` builds System, Subsystem and Element from the resolved
targets and the studied sources. The contract explicitly limits coverage to
studied and target elements; unknown dependencies are retained as UNRESOLVED.
An element's identity is bound to the project, path, name and symbol kind and
is kept between runs.

`project_agents.py` maintains persistent element owners through jobs
REQUESTED → STARTED → FRAME_COMPLETED → OUTCOME_RECORDED.
`project_memory_store.py` retains the transitions with the existing mechanism
of atomic immutable snapshots and a separate memory coordinator.
Unfinished preparation does not become a published record.
Repeated maintenance reads the original job and completes a clean context
build; repository effects are not repeated.

The Active Memory Frame contains eight routes of each owner:

| Memory | Source and purpose |
| --- | --- |
| Identity | Stable identity and the element's state of being known |
| Structural | Parent, resolved binding and known links |
| Goal | Immutable task, expected effects and criteria |
| Context | Base revision and allowed scope |
| Episodic | Retained study outcomes and physically opened run results |
| Decision | Recall rule and reasons for selecting records |
| Defect | Observed failures with their original conditions |
| Evidence | References to the source materials, the task and the results |

Target State contains the required effects; Current State contains the
evidence of the base revision. The Development Delta of a new run stays
AWAITING_FRESH_VERIFICATION even when there is successful past experience: the
working copy starts from the original base. A completed outcome is retained as
a separate event and becomes an episode of the next job. The FULL status does
not rewrite the state of the original branch.

Source snapshot v3 binds the original history cut to the actual
FRAME_COMPLETED event. The reader opens REQUESTED, STARTED, the result and the
past episodes, reproduces the model and reconciles the frame. The neutral
projection of the frame enters only the local information input of the
pluggable agent. Internal evidence and authority remain in Gold.

This lifecycle maintains memory through canonical jobs. It does not introduce
a second execution controller, a second knowledge publisher or a separate
connection to the LLM. Owner persistence means retained jobs, state and
experience; no universal background agent for arbitrary tasks is claimed.

Working profile boundaries: up to 1024 model nodes. Historical v1/v2 jobs
refuse when 128 past episodes are exceeded; v3/v4 show the latest 128 judged
episodes with the number omitted (see "Memory court"). The earlier limit of
4096 journal transactions is removed: this is not a budget for the whole
long-lived memory. Storage formats, reading and the remaining resource limits
are described in [MEMORY_STORAGE.md](MEMORY_STORAGE.md).

### Archive D8/D9: the long-lived owner in frame v4

`owner_profiles.py` derives `element-owner-registry/v1` from the **entire**
judged history of the pinned decision, not from the latest 128 episodes of the
frame. For a new project without a decision the history is empty. The
identity of a long-lived owner is set by the project and the path; the more
detailed frame elements are attached to it by path.

Target State accumulates requirement declarations and the expected effects of
the current task. Current State retains outcomes, claims with their
provenance, proposed patches and references to the court decision.
Development Delta distinguishes `VERIFIED_EARLIER`, `PARTIAL_VERIFIED_EARLIER`,
`DEFECT_ESTABLISHED`, `TASK_FAILED_NO_EVIDENCE`, `DEFERRED_NO_BASIS` and the
awaited fresh verifications. `DEFECT_ESTABLISHED` requires both a counted
`NOT_FULFILLED` and a claim with the status `CONFIRMED` or `REFUTED`. A single
proposed patch, an infrastructure failure or an unverified refusal does not
establish it. These are historical assessments at their own revisions: they do
not confirm the current working copy, do not generalize a habit and do not
bypass the court.

The full roster includes at most 1024 paths. If there are more historical
paths, it keeps the active owners and subsystem summaries over the whole
history; an active scope that is too large is refused. Publication of the
registry is separately limited to 8 MiB: first the history of inactive owners,
then, if necessary, that of all owners is replaced by exact counters and a
reference to the pinned decision. If this form does not fit either, the result
is a refusal. The full bases remain in the court history. This is a limit of
the published representation: building the full registry and summaries still
reads the history, so constant RAM and opening time are not guaranteed.

The common FRAME_COMPLETED envelope keeps the 16 MiB budget. The frame gets
only the owner's counters; `_memory_information` in `source_delivery.py` passes
them to the worker as a neutral `owner_profile`, without disclosing references
to evidence. The physical `compact-active-memory-frame/v1` is a separate
packing version: it supports logical frames v1–v4, restores the exact bytes and
resolves slots only inside the bound frame. Packing does not change the profile
of an old job and does not create a new admission owner.

### Archive D7: soft keywords

The lexer keeps the original name of a keyword token; `Parser.consume_name`
accepts it in a name position. Exceptions: `true`, `false`, `null`, `and`,
`or`, `not`. This applies to declarations, parameters, keys, members and
supported reference positions, including `under target` for `policy target`.
Special grammar takes precedence: a word being in the set of soft keywords
does not mean that it becomes a variable anywhere in the program. An earlier
command at the start of a statement or a special form of an expression keeps
its meaning.

### Golden replay: format of repeated model answers

The `run --record` / `replay --mock` artifact stores manifest schema 2;
schema 1 remains readable, with a check of only the state fields observed in
it. This is a separate golden replay contract, not the ProjectMemoryStore
format or a court decision. The change to the nested cache did not raise the
manifest version.

In `llm_cache.mock.json` the key still consists of the prompt hash and the
model. A new record carries `prompt_hash`, `model`, `results` — the list of
answers of all repetitions. Recorded `llm_call` events and `thought` steps are
merged by position in the common history; at the same position the thought
precedes the `llm_call` event. If positions are not recorded, the earlier merge
order is kept: first `llm_call`, then thought. A historical record with a
single `result` keeps the earlier multiple use of that answer.

For a new `results`, each request consumes exactly one repetition; a missing
record or an exhausted list gives `DeterministicReplayError` without a call to
the provider. Unreadable JSON/text and an unsupported manifest version give
`ReplayArtifactError`; a mismatch of the program, the chain or the replay
remains a determinism error. The owners are `synapse/golden_replay.py`, the
backend in `synapse/builtins.py` and the order binding in
`synapse/interpreter.py`. The cache replays model observations; it does not
prove their truth and does not add admission bases to the four memory layers.

### Episode outcome for the court

For every completed run the court retains, next to OUTCOME_RECORDED, the
events CONSOLIDATED (verified learning claims) and OBSERVED — the typed episode
outcome `episode-outcome/v1`. It is computed by `project_episode_outcome.py`
only from the sealed Stage 12 outcome of the physically opened run. A worker
message, a command exit code and an operator label are not inputs.

The outcome separates three facts: what each attempt established (Stage 12
status, oracle verdict, the verified candidate and references to the C1
result, the command report, the oracle and the publications), whether a later
independent attempt fulfilled the requirement (recovery), and whether the
declared requirement was fulfilled:

| Class | Stage 12 status | Meaning for memory |
| --- | --- | --- |
| FULFILLED | FULL | the only success basis |
| PARTIAL | VERIFIED_REUSABLE_PARTIAL | established non-fulfilment with a verified part |
| NOT_FULFILLED | UNRESOLVED, NO_CANDIDATE, FAIL | established non-fulfilment |
| UNCERTAIN | INFRA_ERROR | unknown effect: neither success nor failure |
| UNVERIFIABLE | INVALID_CONTRACT | invalid evidence: neither success nor failure |

UNCERTAIN and UNVERIFIABLE fall into neither SUCCESS_ONLY nor FAILURE_ONLY and
are not counted as element defects; the ALL selection shows them as
uncertainty. Only established outcomes have a basis (`basis`). Learning claims
in the frame are selected by their own verified status, so an uncertain
overall result of a run does not erase an independently verified attempt. The
neutral projection for the pluggable agent gets only the outcome class,
without references to evidence.

### Memory court

The only owner of decisions is the memory court `synapse/memory_consolidation`
(see "Learned habits" below). Gold owns the format of the decision chain, the
pure exact-patch rule (`project_court.judge`) and the append primitive
(`establish_tail`, `append_decision`), but it neither decides by itself nor
imports the memory subsystem. A project run receives the court as the port
`consolidate(store, guard, project_identity=)`: the CLI composition root
(`ProjectMemoryCourt`) passes it through `execute_gold_project_run` →
`freeze_gold_inputs` → `capture_project_source_snapshot`. The court is called
in two places. When an outcome is recorded (`record_project_outcome`), it
works in the same owner session. Before a new task pins its cut
(`capture_active_memory`), it judges the unjudged tail. There is no separate
background process. Without a bound memory configuration, the port decides
only on the exact-patch section; with one, it is a summary consolidation of
the same court.

1. **Window.** All OUTCOME_RECORDED of the current project identity that are
   not yet in any decision. This is the unfinished tail after a crash: it is
   found from the journal, not from the process's memory.
2. **Basis.** The run is opened physically; CONSOLIDATED and OBSERVED are
   derived again and retained. If the run's records are unavailable
   (`BASIS_UNAVAILABLE`) or contradictory (`BASIS_INVALID`), or the retained
   event differs from the new derivation (`RETAINED_DIFFERS`), the outcome
   stays PENDING: it is not lost, credits nothing and stays in the tail until
   the next judgment.
3. **Judgment** is the pure function `judge` of the previous state and the
   inputs. An established outcome (COUNTED) credits each exact patch of a task
   and revision with one vote per publication origin. A copy of the same
   origin is excluded with the reason `REPEATED_ORIGIN`. UNCERTAIN and
   UNVERIFIABLE get DEFERRED and credit nothing to either support or
   refutation.
4. **Application** is one immutable JUDGED event (`court-decision/v2`; `v1` is
   read as before). It holds the inputs, verdicts, credits, exclusions, state
   transitions, `state_sha256` and `consolidation`: the identity, the mode and
   the receipt of the consolidation report recorded before the decision. The
   event key is derived from the previous decision, so each decision can have
   only one successor. Two concurrent task streams pass through one exclusive
   owner session and continue one chain; a second version of trust is
   impossible.

The policy `court-policy/v1` is declared in advance and recorded in every
decision:

| State | Condition |
| --- | --- |
| ADMITTED | support ≥ 1, no refutations, the court in FULL mode |
| OBSERVED | support exists, but admission is withheld (emergency mode) |
| REFUTED | refutations only |
| SLOW_ONLY | both support and refutation; policy v1 does not lift the ban |

Emergency mode (EMERGENCY) holds while the tail contains a PENDING outcome.
Credits and restrictions apply, but no new automatic admission is granted
(`withheld: PENDING_OUTCOMES`), and there are no births or removals. When the
basis returns, the next judgment counts the outcome once and grants the
withheld admission. If the tail consists of the same PENDING outcomes with the
same reasons, no new decision is created. A repeated judgment on the same
inputs returns the earlier decision: `project resume` of a completed run does
not change the journal.

In REQUESTED, v3/v4 tasks pin the court decision they read. The frame is
built only from the judged history of that decision: on every read the chain
is verified from its root, and every decision is recomputed from its recorded
inputs. Only the inputs of a new judgment are opened physically, so a run
directory deleted later does not block subsequent runs. The frame shows the
last 128 judged episodes and counts the omitted ones explicitly
(`omitted_episodes`). It contains a `court` section: the mode, PENDING, the
states of the task's patches and `automatic_patches`. The automatic Synapse
move (`procedural_memory`) receives only the ADMITTED patches of the pinned
decision, if the operator's selection keeps positive experience (ALL,
SUCCESS_ONLY). The neutral projection also receives `episode_coverage` — the
number of judged, omitted and PENDING episodes. A resumed task sees its
earlier decision; new information enters the next task.

Tasks of profiles v1 and v2 are read by their earlier rules, including the
earlier patch hint rule for v2 frames. Every completed run is passed to the
court regardless of the profile of the task that recorded it. The result of
`project run`/`project resume` shows in the field `project_memory.court` the
court's verdict on the outcome of exactly this run, its reasons and the
decision that issued it (PENDING has no decision). So a repeated `project
resume` returns the same result even if the court has already moved on.

Limitations: the exact-patch section does not generalize and does not check
the independence of origins beyond distinct publications; generalization and
birth concern only tool procedures (section below), not code patches. If a
run's records are lost for good, the court stays in emergency mode: there is
no explicit operator for abandoning an observation yet. Emergency mode saves
only memory. Reading published knowledge re-verifies the producer's lineage,
including the records of its run. So the loss of the directory of a run that
published something still makes the project's knowledge unreadable,
regardless of the court.
The court and the memory journal accept concurrent writers: the coordinator
waits for its turn, and the decision chain does not branch. But the Gold core
stores (library, lifecycle, provenance, taint) take the write lock without
waiting. So a second simultaneous run of the same project can get `LOCK_BUSY`
at publication and finish as GOLD_UNAVAILABLE.
The journal is limited to 4096 events.

### Declared memory selection

An ordinary knowledge-input v3 selects all available experience. For a
controlled context, knowledge-input v4 can be used:

```json
{
  "schema_version": "synapse.stage4.gold.knowledge-input/v4",
  "files": [],
  "experience_limit": 64,
  "run_memory_selection": "ALL"
}
```

`files` still contains the external evidence a specific task needs. The
selection `ALL`, `SUCCESS_ONLY`, `FAILURE_ONLY` or `NONE` applies to the
confirmed experience of runs. The original studied content is retained
separately. The full original inventory of publications stays in the
snapshot, and the selected candidates pass the same gates as before. The
archive, provenance and checks do not change.

The selection value is fixed before the run starts and also applies to the
episodes of the Active Memory Frame; in profiles v3/v4, by the episode's
outcome class. The selection narrows the candidates but neither grants nor
revokes the court's admission. Changing the selection requires a new run;
resume uses the earlier cut. Unknown experience classes are not classified by
guesswork.

### Learned habits: sessions, the consolidation court, birth and loading

Stage 3 of the memory plan works only through the canonical launch. A Synapse
program with `tool`, `task_plan`, `context` segments and the slow path
`try { … } catch (ACTION_FAILED as refusal) { … }` executes in the durable
profile `synapse.durable.cognitive/v1` (see
`RFC-ASYNC-EXECUTION-AMENDMENT-03.md`). The session is bound to a connected
Gold project. There is one court for Synapse sessions and for Gold project
runs; decisions form one JUDGED chain.

The memory configuration is strict JSON fixed before the run (its digest is
recorded in every run and report; the owner binds itself to one configuration
and rejects any other):

```json
{
  "schema_version": "synapse.memory.configuration/v1",
  "tools": {
    "schema_version": "synapse.memory.tool-configuration/v2",
    "servers": [{"id": "airline", "argv": ["airline-mcp"], "env": {}, "cwd": "/abs/path"}],
    "tools": [{"name": "flights", "server": "airline", "descriptor_sha256": "<sha256 of the MCP descriptor>",
               "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
               "source": "flights:acct1", "event_fields": ["route_kind"],
               "contract": {"effect_on_err": {"BUSY": "none"}}}],
    "provenance": {"flights:acct1": {"ancestors": []}}
  },
  "court": {"decision_rule": "threshold", "parameters": {}},
  "advisor": null, "scorer": null, "element": "travel.search"
}
```

`descriptor_sha256` is the digest of the MCP descriptor of a tool the operator
admitted; a changed descriptor is rejected on every call. The tool contract
sets the effect of a refusal by its code (`effect_on_err`: none, partial,
applied, unknown), idempotency, compensation (`compensates`,
`compensation_signs`), the state check (`state_check_for`, `resolve_state`),
environment refusals (`environmental_errors`) and, for a tool that checks
hypotheses, the binding of the check to the claim (`verifies`). The HTTP class
of a response does not establish the effect.

Operator commands:

```bash
python -m synapse run task.syn --durable --state-dir runs --run-id r1 \
  --input-file r1.json --project-state <state> --memory-config memory.json
python -m synapse resume --state-file runs/r1.json        # recovery of RUNNING after a crash
python -m synapse run task.syn --durable --state-dir runs --run-id exam-b \
  --input-file exam.json --project-state <state> --memory-config memory.json \
  --exam-mode B --exam-snapshot bnd_…                      # exam on a fixed snapshot
python -m synapse run task.syn --durable --state-dir runs --run-id arm-a \
  --input-file arm.json --project-state <state> --memory-config memory.json \
  --exam-mode B --exam-snapshot bnd_… --exam-trial hab_…   # trial arm: one learned habit
python -m synapse project run --state-dir <state> …       # the court port is passed automatically
```

The loop:

1. **Session.** Every tool call goes through the owner's gateway
   (`<state>/memory-gateway`: the journal chain, the time journal, the
   evidence store) and is recorded before its result is used. A lost answer
   gives the effect unknown; a retry with `retry_of` is allowed only when the
   effect is known to be absent, the partial effect is repeatable or the
   operation is idempotent. A hidden retry of an unsettled operation is
   rejected.
2. **Window.** A consolidation takes the tail of every registered session
   after its cursor. A live session is consolidated only up to the start of
   the segment it is still executing: a verdict, a reaction and an episode are
   never split between windows.
3. **dream** (`court/evaluate`): integrity, segment verdicts (stage 1 —
   environment, requirement, anchor; 1b — verified re-execution of the
   session; 2–3 — the scorer and double advice, if declared), fire signals,
   reactions and candidates. Without an advisor, a free marker stays
   uncertain.
4. **integrate** (`court/decide`, `court/apply`): trust by the delta rule
   with minimum evidence, the conflict ladder, the pool and births,
   boundaries, the effectiveness automaton, quantization; the report is
   recorded before the decision, then the JUDGED decision and the snapshot
   boundary.
5. **Birth** requires all six criteria: repeatability (≥ 3 distinct
   observations), ≥ 2 tasks, successes only, a derivable argument binding,
   verifiability (replay_verified or matching anchor evidence) and independent
   witnesses of the same claim. A witness is an external answer that confirms
   the effect of exactly the failed operation: its successful retry or a state
   check that established applied. An answer about something else (for
   example, about a quota) is a witness of another claim and is not counted.
   Independence is three-valued (independent, dependent, not_established) and
   is decided only by the declared provenance graph. An observation is a
   request together with the source's answer; a copy of the same observation
   votes once.
   In tool configuration v2, an external witness can only be the answer of a
   tool with the role `action` whose source is not named as a service of the
   agent itself: the source prefix before the colon is not in
   `AGENT_OWN_SERVICES` (`synapse/memory_consolidation/tools/contracts.py`) —
   `palace`, `memory`, `store`, `scratchpad`, `ledger`, `journal`, `log`,
   `notes`, `notebook`, `cassette`, `self`, `agent`, `internal`. An answer
   from such a source is the agent's record about itself, not an external
   confirmation, and the court does not include it among the witnesses: an
   external registry with the source `ledger:erp` will not witness the
   operation, while `erp:ledger` will. Such an answer still resolves the
   uncertainty of the operation's effect; it just does not serve as a witness
   for a birth.
   The consolidation report `synapse.memory.consolidation-report/v2` explains
   the exclusion in the `witness_exclusions` section. For a candidate the
   window touched, it shows:
   - how many independent witnesses were counted (`counted`) against the
     number required (`required`), and the independence verdict;
   - every answer of the window that confirmed the effect but did not become
     a witness: `run_id`, `event_id`, `gw_seq`, `tool`, `source`, `by`, all
     reason codes (`agent_own_service` in v2, `not_declared_outside` in v3,
     `not_an_action`) and `resolves_effect` — whether this answer resolved
     the uncertainty of the effect;
   - a summary of the number of answers per reason (`reasons`).

   The section only explains. No decision reads it, and it does not enter
   `apply`, the state, the digest or the snapshot boundary. So the court's
   decisions stay the same as in report v1, and the court policy stays v6.

   Tool configuration `synapse.memory.tool-configuration/v3` replaces the
   name heuristic with a declaration. The name list is a deny list: it
   silently discards an external system with a "forbidden" name and would
   count the agent's own store under any other name. In v3 an allow list
   applies:
   - Every node of the provenance graph must state `"outside": true` or
     `false` explicitly.
   - Only the answer of a tool with the role `action` whose source is
     declared external can be a witness. Any other source, including the
     agent's own stores under any names, does not become a witness (reason
     `not_declared_outside`).
   - The service names in `AGENT_OWN_SERVICES` are reserved. Declaring such a
     source external is a configuration error; declaring it as what it is,
     `outside: false`, is allowed.
   - Two external sources of one service (for example `gds:search` and
     `gds:bookings`) are one operator. Their answers are dependent and do not
     give two independent witnesses. The `#operator:<service>` nodes are
     derived automatically, and names with the `#` prefix are forbidden in
     the graph.

   The operator writes the declarations in the frozen configuration; the
   agent does not change them. Configuration v2 works as before. The memory
   owner moves to v3 with the command
   `synapse memory reassess --memory-config <v3 configuration>`. From then on,
   new births and accumulating candidates are judged by the declaration: a
   witness recorded in a candidate in earlier windows counts only while the
   configuration in force admits its source. Narrowing the declaration takes
   effect at once. An answer excluded when it was recorded does not become a
   witness later, even after the declaration is widened: later answers count.
   Habits already born keep their birth: a reassessment checks their basis as
   before, and the independence of witnesses remains a criterion of the
   moment of birth.
6. **Legitimacy.** The behavior of a birth passes Gold's INGESTION and
   PUBLICATION gates (`learned-habit-request/v1`, lifecycle scope BINDING).
   A refused birth stays in the pool with its reason. Only a behavior in the
   consumable state with an unchanged tool binding is loaded. A successor
   supersedes its predecessor by a SUPERSEDE decision of the supersession
   authority; the operator can withdraw a behavior
   (`learned_habit_lifecycle.withdraw_learned_habit`, a WITHDRAW decision).
7. **Loading.** A new session pins the latest complete snapshot boundary and
   loads the habits Gold admitted; a resumed session keeps its cut. A learned
   habit fires only inside its typed scope.

Exam modes (section 17 of the refinement) are run parameters, not memory
policy, so all three run on one memory and one snapshot: A — accumulated
experience off, B — the snapshot's admitted habits, C — the same habits, each
in a persistent slow_only (selected and reported as `habit_suppressed` with
the reason `slow_only`, but not executed). An exam is not registered as a
session, is not consolidated and does not go through emergency consolidation
after a crash: exam results do not teach later tasks.

Verified limitations:

- Since court policy v3, the effectiveness automaton does not count windows:
  a lack of experience never demotes a habit (section "Review of PR108" below).
  Disuse is still measured by the number of consolidations without fires
  (`m_idle`, `n_t9`, `k_extinct`).
- Withdrawing a behavior is available only through the Gold API; there is no
  separate CLI command.
- The MCP transport does not restart a dead stdio server inside the process:
  all later calls of its tools are lost until the next run. The timeout of
  one call is 30 seconds.
- Tool procedures are learned; code patches stay in the exact memory section.
- This is not evidence of a live Baseline/Gold experiment and not AS2
  production admission.

### Memory stage 4: experience retention, hypotheses and palace admission

The court policy is `synapse.memory.court-policy/v2`: the declared parameters
`raw_capacity` (0 means no limit) and `hypothesis_fresh_windows` are added;
earlier reports are not rewritten.

**Retention by significance (section 9 of the refinement).**

- At substage 7a the owner takes into custody, in store D
  (`<state>/memory-gateway/evidence`), the raw trace of every case (its events
  and their positions in the session history) and the session's replay data
  (the program, bindings, the session's opening, the executor). The quantum
  refers to them; experience no longer depends on the run artifact.
- The tier is determined by the specification's table (part 3 §8.1). A known
  quantum that became the basis of a live habit rises to high at once; one
  that lost its basis returns to medium after `grace_windows`.
- The retention pass is a separate step after an applied consolidation, under
  the same owner session (`court/retention.py`, the logic of `retention.py` of
  the research engine v43). A medium case leaves custody when due only after
  its session, re-executed from the replay data and the gateway's recorded
  results alone (`reproduce_cognitive_session`, without live effects), has
  reproduced its events exactly at their recorded positions (`replay_only`).
  Otherwise the trace stays, and the case rises to the high tier with a
  reason. A low case is reduced to its tail form; tails older than `k_rollup`
  are rolled up into an aggregate with the list of qids.
- The basis of a live habit is never removed (`blocked`). `raw_capacity`
  compacts eligible cases before their time and publishes the loss
  (`capacity`: `limit`, `held`, `compacted_early`, `over_limit`); the limit is
  not paid for with a decision basis.
- The facts of a pass are recorded in the project journal (`MEMORY_RETENTION`)
  before any body is removed; a removed body leaves a marker (`compacted` or
  `forgotten` with a tombstone). An interrupted pass is completed by the next
  pass from its record and is not decided again; the next report applies the
  facts (the `retention` section).

```bash
python -m synapse memory restore --project-state <state> --memory-config memory.json --quantum <qid>
python -m synapse memory forget --project-state <state> --memory-config memory.json --quantum <qid> \
  --reason "<reason>" --operator <operator>
python -m synapse memory trial --project-state <state> --memory-config memory.json \
  --arm runs/arm-a.json --arm runs/arm-b.json --stand <stand> \
  --scope '{"fields": {"route_kind": ["intl"], "tool": ["flights"]}}'
```

`restore` returns a compacted case: the reproduced trace is written to the
same address, and there is no new vote. `forget` is a legally significant
removal: the raw trace and the recorded results of the case (and of every case
with the same results) are removed behind a tombstone with a reason and the
authority `GOVERNING_HUMAN`; for such a result the gateway returns an explicit
end (`forgotten`), not an integrity violation.

**Hypotheses (section 10).** Built-in functions of a durable session:
`hypothesis(claim)`, `probe(h)`, `established(h)`; the option
`tool(…, {"requires": [h, …]})`; the tool contract flag
`requires_established`.

- `claim`: `{"aspect": "entity" | "content" | "version", "subject",
  "statement": {…}, "scope", "source": {"tool", "args"}, "check": {"tool",
  "args"}}`. The source is a recorded successful observation of the same run;
  the address of its answer is the source version and part of the record's
  identity (`hyp_…`).
- The status starts as `provisional`. Only the declared check decides it,
  through the gateway, only with a checking tool independent of the source (by
  the provenance graph) and only when the check answered about this very claim
  (rule `synapse.memory.hypothesis-check/v2`): the checking tool's contract
  (`verifies`) binds a request argument and/or an answer field to the subject
  (`subject`), the scope (`scope`: an argument, a field or the single value
  `value` the service answers for) and each condition (`conditions`). The
  subject is compared by the exact identity rules (namespace, type,
  incarnation), the scope and conditions by value. Then: all fields matched —
  `confirmed`; a field is present and differs — `refuted`. A check without a
  binding (`check_unbound`), an answer about another subject or scope
  (`check_about_another:…`), a condition the binding does not bind or one with
  another value, a condition the check answered under that the claim does not
  name (`check_under_condition:…`), a refusal, a loss, a missing field,
  `"determinable": false`, a dependent or unestablished checking tool, a
  missing source — `provisional` with a reason: neither confirmation nor
  refutation. A confirmation of the entity says nothing about the content.

  ```json
  {"name": "ledger", "source": "ledger:bank", "contract": {"verifies": {
    "subject": {"request": "account", "answer": "account"},
    "scope": {"value": "bank"},
    "conditions": {"currency": {"request": "currency"}}}}}
  ```
- An action with `requires` (and any tool with `requires_established`) is
  refused by the gateway before its effect (`REJECTED`) while any named
  hypothesis is not established.
- The court records the statuses (the latest check decides; the basis is the
  case with the check's answer); snapshot boundary v2 carries them together
  with the window, the last checked source version per claim and the rule the
  status was decided by. Reuse applies only to the same source version, no
  older than `hypothesis_fresh_windows`, and only to a status decided by the
  current check rule; a source change (`source_changed`), unknown freshness
  or a status decided before checks were bound to claims
  (`checked_under_another_rule`) require a new check. Exam A does not see the
  statuses; B and C do.

**Admission of palace records (section 11).** `recall` returns candidates by
a lexical match of content fields only (scorer `palace-lexical/v2`; it is
recorded in every row and in the `memory_recalled` event). Confidence,
insertion time and metadata do not enter the score, zero matches means no
candidate, and equal scores are ordered by identity. Admission of a fact is a
separate built-in `admit(candidates, claim)` (`synapse/palace_admission.py`,
event `memory_admission`): entity, attribute, kind, scope, validity period,
provenance, the status `confirmed` (the live status of the hypothesis if the
record names one, otherwise the one the record itself states — this is
reported), at least two different normalized key tokens together with the
threshold. A contradiction of admissible values is a conflict without
admission; copies of one claim count once.

Changing the rule is a change of the behavior version. Delta analysis on the
labeled probes of `test_palace_admission_acceptance.py` at threshold 0.5. The
earlier rule (the top candidate by `(substring occurrence + confidence) / 2`,
on a tie the later record) answered the question about the restart command
and its paraphrase with a poisoned copy, answered on behalf of another entity
and picked a winner of a conflict. The new rule admits no wrong fact on any
probe. One useful find is lost: a record with a weak match (one key token) and
confidence 0.99 is no longer admitted — this is the minimum-keys requirement.
Sensitivity grid (threshold 0.34 / 0.5 / 0.67 / 1.0 × confidence 0.1 / 0.5 /
0.99): the share of wrong admissions is 0.0 in every cell; the share of lost
useful knowledge is 1/3 at thresholds up to 0.5 and 1/2 from 0.67; confidence
changes no cell. The existing palace tests were revised: their queries match
words of the content, and the results did not change.

**Segment requirement with a resource (section 8).** An `execute` requirement
can name a resource: `"resource": {"argument": "job_id", "from": {"tool":
"create_job", "field": "job_id"}}`. The required operation counts only if its
argument equals a field of an earlier successful result of the named tool in
the same scope; a success on another resource is
`required_operation_on_another_resource`. Without this key, the identity of
the task contract does not change.

Verified limitations of the stage:

- The executor is pinned to the owner as a whole: the court refuses a journal
  with episodes of another executor, so a case without raw material is always
  reproduced by the same executor. The procedure for moving to a new runtime
  version (specification part 3 §8.4 item 5) is not implemented.
- Cases of an emergency window (provisional) get no retention plan and stay in
  custody.
- Replay for retention is exact only for programs without `time`, `random`
  and model calls outside the gateway; otherwise the case is not compacted and
  rises to the high tier.
- `forget` does not remove the session's replay data (the program and
  bindings).
- `imprint` and `recall` are not part of the durable cognitive profile (the
  identities of palace records are nondeterministic). The palace admission
  path works in an ordinary `python -m synapse run`; in a durable session
  `admit` applies to records built from recorded answers. Lexical admission
  does not resolve a paraphrase without shared words — that is semantic
  search (memory stage 6).
- D1 and D2 are implemented by the next package (below); composition, semantic
  search and event-driven execution (stages 5b–7 of the refinement) are not
  part of the package.

### Memory stage 5a: generalizing applicability (D1) and passing results (D2)

**Applicability (section 12).** The trigger of a learned habit is a schema 1.2
record (`habit_trigger`): `applicability`, an explanation of the scope of
application that is part of the identity, is added to the earlier fields; 1.1
records stay verifiable. Generalization (`learning/applicability.py`) is
deterministic and uses no model:

- the base is the least general generalization of the positive episodes: a
  field with one value in all of them is an `==` condition, a field that varies
  is dropped, and shared context labels are required;
- a contrast is a verified failure of the same procedure (the same candidate
  key, the segment judged `failed`, the session confirmed by re-execution).
  Each contrast is compared with the base alone, so the result does not depend
  on the order of the episodes. The discriminators of a contrast are the base
  conditions it violates and the varying fields with a value that no positive
  had. With exactly one discriminator the field is **essential** (with the
  contrast in the explanation). A contrast inside the base gets a boundary from
  the values of the positives: a number beyond one edge of their range gives a
  boundary at that edge (`<=`/`>=`), otherwise the boundary is the set of
  values seen (`in`). Several discriminators bound each one of them without
  proving any (`bounded`). A contrast without a discriminator is an unexplained
  contradiction, and birth is blocked, as before;
- an event field that the binding reads and that has no condition of its own
  gets an `is` presence condition (`string`, `number`, `bool`, `object`,
  `array`, `null`): a missing field or a value of another kind does not trigger
  automation; when the positives have different kinds, birth is impossible
  (`boundary_not_derivable`);
- the explanation: `essential` (with contrasts), `bounded`, `irrelevant` (the
  fields varied: irrelevance is proven by variation), `unproven` (constant,
  there was no contrast), `required` (the fields read and their kind).
  Conditions are stored in one canonical order and value sets in sorted order,
  so equivalent triggers are one record.

The completion criterion of birth is judged inside the habit's scope:
`all_success` means that every episode inside it succeeded, and explained
contrasts are counted separately (`criteria.contrasts`). Gold's verification
did not change.

A boundary moves only on evidence. Three near misses of one condition in a
window, recovered by the same action, widen that condition by the values at
which they were recovered (`==` → `in`, `in` is extended, a boundary shifts);
the condition is never removed, and the other conditions are kept. A widening
that would admit the value of a recorded contrast is refused with a reason
(report section `boundary_refusals`). Narrowing on verified failures adds them
as contrasts of the field. The successor is born in `probation` and supersedes
its predecessor in Gold, as before. Context labels and `not_when` prohibitions
are still lifted as a whole.

The `habit_activated` event carries `matched`: every checked condition with its
actual value; a near miss still names one condition with its actual value.

A change of a tool's schema or contract is a different memory configuration:
the owner is bound to one configuration and refuses any other before any
effect, and Gold verifies the tool binding at every admission. An old habit
does not meet a new schema; carrying experience over a schema change is not
implemented.

**Passing results (section 13).** The binding rule gets the source
`{"result": {"step", "path", "kind"}}`, a field of the successful answer of an
earlier call of the same body (`learning/dependencies.py`):

- an argument value is linked to a producer's answer only where the value first
  appeared. Everything known before the answer — the program's literals (by the
  lexer), the session inputs, the fields of the reactive event, the arguments
  of the failed action, every argument and answer in the journal before the
  producer's answer — makes a match an echo, not a dependency. An echo in the
  answer of an intermediate step (a status that repeats an identifier) does not
  become a target;
- a reference is derived with first priority only if it is the same in every
  basis episode and the values differ; a value that does not change stays a
  constant. An argument that changes from task to task and matches an answer
  only as an echo of a value known in advance (a driver literal) is not
  derived: birth is impossible, and the report names the reason
  (`pool_updates[].unavailable.binding`);
- a reference may point only to the successful answer of an earlier step's own
  action; the declared input and output schemas of the tools are checked for
  kind (`dependency_type_incompatible`). References are part of the binding
  rule and therefore of the habit's identity;
- before the first effect, execution binds every argument that does not wait
  for an answer (`binding_not_applicable` with no calls). A reference is
  resolved from the recorded answer when its call is reached: a failure or an
  unknown effect of the producer (`producer_failed`, `producer_uncertain`, a
  `diverged_from_basis` stop listing the dependent steps), a missing field, an
  empty value, another kind (`field_missing`, `field_empty`, `type_mismatch`)
  are distinct reasons in `habit_activated.detail`; the dependent call is not
  made;
- the court recomputes the outcome of the fast path with the same executor from
  the journal's recorded answers (`run_recorded`), not from the runtime's
  label: a stopped body is a `failure` even though the last answer succeeded.
  Re-execution and resumption read the recorded identifier and create no new
  object.

Verified limitations of the stage:

- A reference covers a whole top-level argument; a value inside a composite
  argument cannot be expressed by a reference.
- A producer that always answers with one value is indistinguishable from a
  constant by the evidence and is bound as a constant.
- A boundary from a contrast is built from the values of the positives: values
  between the edge of the positives and the contrast remain unchecked and
  closed until recovered near misses widen the boundary.
- Habit composition (section 14) is implemented by the next package (below).

### Memory stage 5b: habit composition

Composition is hierarchical: a base procedure runs, and at a step that failed
otherwise than in its basis (an impasse), a part, another admitted learned
procedure, may be attached to that step's failure event
(`learning/composition.py`). The decisions rest on primary sources: a skill as
a tuple (applicability, policy, termination condition, interface) and
hierarchical composition where a policy calls a skill (SoK Agentic Skills,
arXiv 2602.20867: without a termination condition a skill does not compose); a
Soar subgoal folded into a new rule, and the risk of overgeneralization (which
is why a composite procedure is verified as a whole); HTN methods with
preconditions; admission into the library of a verified program only (Voyager);
saga compensations (Garcia-Molina, Salem) as a source of effect conflicts;
retraction of derived knowledge together with its premise (TMS, Doyle) for the
review of a composition; trying the applicable plans in turn when a plan fails
in BDI agents (Sardina, Padgham, the CAN language, Autonomous Agents and
Multi-Agent Systems, 2011) and retry with another method in a refinement
executor (Retry in RAE: Ghallab, Nau, Traverso, "Automated Planning and
Acting", 2016); choosing the more specific rule in conflict resolution (OPS5);
a limit on the nesting depth, without which hierarchical planning does not
terminate.

- **A part's interface** is read from its frozen records and the tool
  contracts: applicability (the typed trigger), inputs (the arguments of the
  failed action and the event fields the binding reads, with their kinds),
  guarantee and termination (the retry of the failed operation succeeds:
  `failed_operation_recovered`), effects (the body's tools and their
  `idempotent`/`compensates`), limits.
- **A join** is admitted only if the part's trigger applies to the step's
  failure event (the conditions of all parts are kept and checked again at
  every execution), its inputs are bound from that event, it terminates with a
  retry of the step's operation, and neither does it compensate an effect the
  base has already made nor do the base's remaining steps compensate its
  effects. Every rejected candidate is recorded with a reason
  (`conditions_not_met`, `inputs_unavailable`, `no_termination`,
  `effect_conflict`).
- **The executor** (the same as the fast path's): when a part's retry succeeds,
  the step's answer is replaced by that retry and the base continues
  (references to the step's result read the successful answer); the result of
  each part is recorded separately from the result of the body. A retry of the
  operation is a declared retry of the same operation, which the gateway checks
  before any effect.
- **Alternatives.** A join keeps the parts of an impasse in order. A part that
  did not recover the step yields to the next admissible one; that one is
  checked against the step's current failure (if the part retried the operation
  and got a different failure, the next one must apply to that failure), and
  the effects already applied by earlier attempts take part in the conflict
  check. Each part is tried once in an impasse; when none has recovered the
  step, the body stops (`component_failed`, listing the parts tried and the
  failures). A step or a part with an unknown effect ends the search
  (`component_uncertain`): nothing is chosen over an unknown state. The planner
  ranks parts by trust, then by trigger specificity (more conditions first),
  then by identity. Every call of a part other than the retry itself declares,
  in its request to the gateway, the failed operation it serves (`serves`)
  while that operation is unsettled; before any effect the gateway rejects a
  declaration that does not name an unsettled operation of the same scope, and
  a program cannot make such a declaration. A refusal of such a call with no
  effect is settled when the served operation then succeeded
  (`served_operation_settled`): an abandoned alternative that changed nothing
  does not fail the segment's substantive result, like an aborted alternative
  of a flexible transaction (Zhang, Nodine, Bhargava, Bukhres, SIGMOD 1994).
  Any other effect, or an unsettled served operation, keeps the refusal a
  failure.
- **Nesting.** An impasse inside a part is resolved the same way: first the
  part's own joins (if the part is itself composite), then the parts attached
  inside it. A part is never attached inside itself or inside a procedure it is
  a part of (`component_cycle`), so the depth is bounded by the number of
  distinct parts and execution is finite. The effects of parts are also checked
  for conflicts with the remaining steps of all enclosing procedures.
- **Sequence.** A procedure whose retry of the operation failed for a new
  reason ends with that new failure; a part whose trigger applies to it is
  attached to that retry. The output of the first procedure, the typed failure
  event of its retry, becomes the input of the second; the second retries the
  same operation, and the first continues. This is how procedures with
  different goals are joined (for example, a drain and a schema migration).
- **Three statuses.** Plan hypothesis — the slow planner: the built-in
  `recover(failure)` on the slow path continues the stopped body of the fast
  path from its recorded answers and the parts already tried (without repeating
  the calls made), or takes as its base the first admitted part whose trigger
  applies to the failure; at an impasse it tries the known alternatives, then
  the matching parts by rank. The events `composition_planned` (the place in
  the nesting, the join or a refusal listing the candidates) and
  `composition_executed` (the base, the joins, the tree of part attempts with
  their results, whether the failure was recovered) are recorded and checked at
  re-execution. Verified composition — the court re-executes each such episode
  with the same executor from the recorded answers, trying the parts in the
  recorded order and checking every join again (the parts are only those the
  session loaded when it opened); the court derives the composition's identity
  from this re-execution, not from the execution environment's report, and
  counts an episode only if the record contains exactly the composition's
  calls; re-reading evidence obtained in the same episode (a part and its
  procedure confirm one release) does not count as a copy; the goal is judged
  as a whole (the failure is settled by the journal, the segment did not fail);
  a part that recovered a step while the goal failed is a partial success,
  reported separately (`compositions`, `criteria.partial`), and never support.
  Admitted habit — under the same birth criteria as any habit (repeatability,
  different tasks, verification, independence of witnesses, success of every
  episode), and while every part is live, a composite habit is born: a
  `learned_habit` record of schema 1.2 with a `composition` field holding the
  base and the joins (the step, the failure class and the alternatives in
  order, each with its own nested joins). It supersedes the live procedure of
  the same base; Gold verifies it as a separate birth with the established
  criteria.
- **Review.** Every window, live composite habits are checked against their
  parts: a part that is archived, superseded or not admitted by Gold (an
  alternative, or a part nested in another) yields an entry in the
  `composition_reviews` section (part, base step, reason) and a TC transition
  to `probation`; during execution a part that is not admitted is not called
  (`component_unavailable`).
- With automation off (exam mode C), learned procedures remain material for
  slow planning: `recover` composes them, and there is no fast path. A chain
  spelled out in full by the program is learned as an ordinary procedure and
  does not count as a composition.

Verified limitations of the stage:

- A part is attached to the failure of a step that it recovers by retrying the
  operation; a sequence is built through the failure of a retry. Planning by
  postconditions (regression from the goal) is not implemented: a procedure
  that does not settle a failure (for example, a search only) is not born as a
  habit under the support criteria and therefore is never a supplier of inputs.
- The fast path of a composite habit tries only the alternatives verified in
  its episodes; only the slow planner attaches new parts, and such a
  composition is verified as a whole again.

### Memory stage 6: semantic search and knowledge updates

Memory's knowledge consists of statements
(`synapse.memory.statement-record/v1`, a record of kind `statement`,
`knowledge/statements.py`): a subject, a property, a value, a polarity (whether
the source asserts or denies), conditions, a valid time
(`valid.from`/`valid.until`, ISO 8601 in one canonical UTC form), a text and a
source (the tool, the arguments and the address of the recorded answer of the
same session). The built-in `know(statement)` records a statement read from an
observation of this session (`knowledge_declared`); a statement is never an
established fact. The court, the only writer, folds the window's statements
into memory (`court/knowledge.py`, report section `knowledge`):

- **Two times.** Valid time comes from the statement; transaction time is the
  windows in which memory held the version (`known_from`, and after a
  correction `known_until` and `corrected_by`). A contradiction deletes
  nothing: a correction closes the transaction time, so both questions are
  answered — "what was true at moment T" and "what memory knew in window K"
  (`known_as_of`).
- **Identity and correction.** A slot is the subject, the property and the
  conditions. The same record, or the same source (tool and arguments) with the
  same content, is a copy: a repetition adds neither knowledge nor confidence.
  The same source with different content for the same start is a correction. A
  different source with different content is a conflict: both versions are in
  force, and neither wins by insertion time, similarity or declared confidence;
  admission decides.
- **Currency by property kind** (open decision §15, declared in the schema v2
  memory configuration, `knowledge.properties`): `state` holds from its start
  until the next start of the same slot or its own end (the inertia of the
  event calculus); `bounded` is the same within `ttl_days`, after which
  currency is unknown, not false; `event` holds at its moment; an undeclared
  property is of unknown currency. A late report about the past takes its place
  by its start and does not override a later state. There is no universal
  forget timer.
- **Revision of dependents.** A correction returns to `provisional` the
  hypotheses read from the corrected version (`source_corrected:`) or checked
  by it (`check_source_corrected:`), and moves to `probation` (TC) the live
  habits in whose basis a session admitted the corrected version; the
  `revisions` section names them.
- **Semantic search** (`knowledge/search.py`, `search_knowledge(query,
  options)`, event `knowledge_searched`): a lexical channel
  (`palace-lexical/v2`) and a semantic one, the embedding model declared in the
  configuration (`knowledge.embedder`, a tool of the `reason` role through the
  gateway; the choice of model does not change memory's authority, and its
  answers are recorded and go through re-execution), with a shared candidate
  budget; ranks are merged by reciprocal rank fusion (k = 60), and the scores
  of different channels are not compared. A candidate is a version valid at
  `valid_at` according to knowledge as of `known_as_of`, with the channels that
  found it. Every search publishes the cost of the semantic channel: the model
  calls, the model's latency, the number and dimension of the vectors, the size
  of the index.
- **Admission** (`palace_admission.admit`) reads the structure of a statement,
  not its text: a different entity, a different polarity, different conditions,
  unknown currency, a time outside the valid time, and confirmation by a
  hypothesis about a different statement (a different subject or value) are
  reasons for refusal.

The decisions rest on primary sources: rank fusion without score calibration
(Cormack, Clarke, Buettcher, SIGIR 2009); the weakness of dense retrieval on
negation (NevIR, EACL 2024) and on rare entities (EntityQuestions, EMNLP 2021),
which is why an embedding only proposes; retrieval poisoning with similar texts
(PoisonedRAG, USENIX Security 2025), which is why similarity, confidence and
recency give no priority; the bitemporal model (Snodgrass; SQL:2011;
Zep/Graphiti, arXiv 2501.13956); inertia and the termination of states by
events (Kowalski, Sergot, 1986); retraction of conclusions together with their
premise (TMS, Doyle, 1979).

Verified limitations of the stage:

- In acceptance the embedding model is scripted (concepts with synonyms and
  translations; negation and a name change nothing, as with dense models); a
  live model was not checked. The semantic channel scans all vectors (no
  approximate index): the cost is linear in the number of statements and is
  published.
- Transaction time is measured in court windows; statements without a start
  hold from the start of the timeline. Conditions are compared for equality.

### Language stage 7: event-driven language execution

The construct `parallel NAME [limit N] { node X [on "e"] = expression;
signal "e" when condition; commit X [=> effect] }` executes a graph of nodes as
they become ready (`synapse/runtime/dataflow.py`). A node depends on the nodes
its expression names. A pure node is evaluated on the interpreter thread; a
call node `tool(name, arguments)` is an observation made on a worker thread
through the same gateway, with its own operation scope and ordinal
`df:<instance>:<node>:<attempt>`. Before any effect the gateway rejects a graph
call if the tool's contract does not declare `observation` (an observation is
also `idempotent`, its failure leaves no effect, and it compensates nothing).
The MCP transport sends each call as a separate session request, so the calls
of parallel callers overlap.

- **Readiness without mixing versions.** Every value has a version, and every
  computation has the versions of what it read. A node starts when each of its
  inputs is settled: not in flight and not outdated, as is everything that
  input was computed from. So a node does not combine a new price with a check
  made for the old one.
- **Revision.** A computation whose inputs moved while it ran is superseded at
  once by a new one; its answer is recorded as `stale` and not used.
- **Events.** A signal is evaluated once per combination of the settled
  versions of its inputs, so a repeated observation does not raise the event
  twice; `on "e"` nodes are read again. The absence of an event is not a
  negative fact.
- **Commit and effect.** The commit waits for the committed node, its inputs
  and the event sources that update them, and for nothing else; a slow
  unrelated node does not delay it. The effect acts only on the committed
  value, once, on the interpreter thread (the single writer); whatever is still
  running after it is awaited and recorded as `cancelled`: accounted for once
  and not used. The `limit` (1–64) bounds concurrent calls; a feedback loop is
  stopped by the bound on the number of computations of a node (32).
- **Recording and replay.** The history contains `dataflow_started`, a
  `dataflow_step` for each computation (node, attempt, status, the versions
  read and the version produced, time), `dataflow_signal`, `dataflow_commit`
  (the versions the commit is based on) and the `external_action` of each call
  in integration order. Live, answers are integrated in the order of the
  gateway's journal, so the retention replay from the journal repeats the same
  order. The court re-executes the session from the record (`replay_verified`);
  after a crash the run follows the record to its end and continues live, and
  an observation whose answer was lost is requested again under the same
  identity.

The decisions rest on primary sources: Kahn processes (deterministic nodes give
a result that does not depend on the execution order; Kahn, IFIP 1974);
propagation without intermediate inconsistent states (FrTime: Cooper,
Krishnamurthi, ESOP 2006; REScala: Salvaneschi, Hintz, Mezini, 2014);
optimistic validation of the versions read (Kung, Robinson, TODS 1981);
recomputation from input traces (Mokhov, Mitchell, Peyton Jones, "Build systems
à la carte", ICFP 2018); deterministic replay from the event history
(Temporal); cancellation in MCP is only a notification, and the server may
already have executed the request, so a started call is not interrupted but
awaited and accounted for.

Verified limitations of the stage:

- The program's observations run in parallel; pure nodes are evaluated on one
  interpreter thread (GIL); no speedup of computation is promised, and speedup
  is measured only on waits for external calls. On the acceptance machine, two
  observations of 1 s each, including the server start, were committed in about
  2.3–2.5 s with `limit 4`, and a revision with immediate superseding in 4.0 s
  instead of 5.4 s with the stale call awaited; these are observations of one
  run, not a guarantee.
- Call nodes only observe; a graph has one effect, at the commit. A graph is a
  program statement: events and signals act inside one graph. A started call is
  not interrupted.

### Review of PR108: identity, attestations, retries, conflicts, automaton, reassessment

The court policy is `synapse.memory.court-policy/v3`, the tool configuration is
`synapse.memory.tool-configuration/v2`: the meaning of the rules changed, so
these are new versions, not a reinterpretation of old reports. Tool
configuration v1 is rejected with a pointer to the migration path.

**Identity and admission bases (R4, R3).** An entity is an exact reference
`{namespace, type, id, uid}` (`synapse/entity_identity.py`): `"1"`, `1` and
`true` are different values; case is folded only in a namespace the operator
declared case-insensitive (`identity.namespaces` in memory configuration v2);
an object deleted and created again is another incarnation (`uid`). An alias is
a confirmed hypothesis of aspect `entity` with the statement `{"same_as": …}`,
not a similarity of names; a refuted link admits nothing. A knowledge record
does not confirm itself: admission reads the basis from the hypothesis journal
and requires it to cover the whole claim — entity, property, value, conditions,
scope and version; a foreign hypothesis, another property, another version and
a revoked basis are named as the reason for the refusal.

**Attestations and retries (R1, R2).** A state check is bound to the operation
by its contract (`binds` — request arguments and answer fields, `attests` —
`state` or `operation`, `operation_field` — the echo of the operation key).
"The state exists" does not mean "my operation created it": a foreign object,
another operation and an observation made before the operation leave
uncertainty and give the habit no credit. A goal of kind `reach_state` is
confirmed by a bound state check. A refusal code the contract does not describe
leaves the effect unknown. The gateway itself issues the idempotency key per
operation (a retry carries the same key, a new independent operation a new one;
a key from the agent is rejected), a retry of an unknown effect is admitted
only with a live provider key, and a retry with changed arguments is rejected
before the effect; after a process crash, an operation with a key is retried
with the same key, an operation without a key is not.

**Conflicts (R5).** A model's advice is only a proposal. The verdict of step 2
is a verified comparison of recorded outcomes (`court/comparison.py`): pairs
are made only within one recorded situation; uncertain episodes, copies, an
episode listed again and a situation where the procedure both succeeded and
failed do not count as a pair; a tie gives no winner. The loser records
`yields_to`; the result holds while new outcomes do not contradict it;
slow-path outcomes accumulate across windows (`compared`). A competitor born at
equal trust closes the shared trigger in the same consolidation (step 3 at
birth). During execution, competitors applicable to one event without a
verified resolution are both held back before the external effect — also when
their conditions overlap only partly; the court then records such a conflict
"on the overlap", without taking their own triggers away from the habits.

**Automaton (R6).** Causes are kept apart and named in every transition
(`cause`): sufficient experience, confirmed errors (the last `t3_fires` counted
signals in the state below `t3_signal`), disuse, a changed basis (TS, TC, TR),
a cold match. Under the `threshold` rule, the window's fires are read one by
one in the court's order; a transition happens at the fire that reached it
(`at`), with the trust reached by that fire; trust is updated in fixed batches
of `min_evidence`. So one stream gives the same transitions in the same order
however it is cut into windows. An event delivered again is counted once.

**Reassessment of recorded memory.** `synapse memory reassess --project-state …
--memory-config NEW.json` is the only way to adopt a new configuration: while
the owner is bound to the previous one, a session with the new one is rejected
before any action. A reassessment is a consolidation in `reassess` mode without
a window of sessions and without tool or model calls: the basis of every live
learned habit is judged again from the recorded histories, the gateway journal
and the evidence under the current contracts (the anchor of the first stage;
the admissibility of every retry of a recorded refusal); a record that can no
longer be read confirms nothing. A habit without a verified basis is archived
(TR, cause `changed_basis`); one that kept its basis is published to Gold again
under the new tool binding with verified evidence (a Gold refusal is also TR).
Fields missing from the old metadata are completed neutrally (`yields_to`,
`compared`, `tail` empty) and listed; competitors are judged again. The report
publishes the `reassessment` section: the "before/after" configuration and
policy versions, every habit and every episode with its recorded and current
status and reason, the republications, the completed fields and the new
slow-only triggers. Only then does the owner adopt the configuration (the
binding chain `synapse.memory.owner-binding/v2`); a repeated reassessment under
the same configuration and policy changes nothing. Memory decided under another
court policy is neither opened nor consolidated before a reassessment.

**Sequence tests and the paired exam.** Hypothesis drives the gateway through
generated sequences (new requests, retries, changed arguments, lost answers,
process crashes) against an independent model of the provider: no operation
books twice, and the decisions and effect classes match the model. The
automaton is checked on generated streams and two random window cuts against an
independent model of the rules, including the live trust dynamics. The paired
A/B/C exam runs the same tasks in three modes, restoring the environment to its
initial state before each mode.

Verified limitations of the package:

- A reassessment does not re-execute what was recorded and does not call
  models: the verdicts of the later stages (similarity, double advice) stay as
  recorded; they do not depend on tool contracts.
- The liveness of an idempotency key is measured by the wall-clock time of the
  side log; the provider must keep the key for at least the declared
  `retention_s`.
- The comparison of competitors is observational: these are recorded outcomes
  in the same recorded situation, not an experiment in independent copies of
  one state; the operator declares the pair threshold
  (`counterfactual_min_pairs`).
- When a transition happens inside a window, the later trust batches of the
  same window are computed with the state's rate at the start of the window;
  this does not affect the automaton's transitions (trust enters only T1, while
  the habit is in the born state).

**Second review of PR108 (5580007): check relevance, stand scope,
contradicting trials, experience for promotion.**

- *A check decides only its own claim (F1).* An independent source is not yet a
  source that answered about this claim: a check about account B no longer
  confirms a claim about account A, even when the value matched. The checking
  tool's contract must declare `verifies`; without it a check decides nothing.
  A session does not reuse a status recorded under the v1 rule; a reassessment
  decides it again from the recorded check (see below). After this package, an
  operator configuration in which the checking tools have no `verifies` leaves
  hypotheses `provisional`: the binding must be declared for every tool named
  in `check`.
- *The stand's scope covers the whole trigger (F2).* A trial says something
  about a trigger only if everything the trigger admits lies inside the tested
  scope: every field of the trigger is reproduced by the stand, and the trigger
  restricts every field of the stand (`==`, `in`) to tested values. A trigger
  open on a field the stand restricted (the stand in clear weather, the habit
  in any weather) is wider than the stand: the fast-path ban stays.
- *A contradicting trial is not lost (F3).* Identical results of one situation
  count once; different ones are a contradiction (`contradicting_trials`,
  `mixed_situations`): there is no winner, and the order of the records decides
  nothing.
- *Promotion only on confirmed experience (F4).* T1 reads the fires counted
  since birth (`counted_since_birth`) and the tasks with a counted fire
  (`counted_tasks_since_birth`), T4 the counted signals in the state; a fire
  with an undecided outcome is use (`fires_since_birth`), not experience: it
  does not fill the quota and does not become an error. For habits recorded
  earlier, a reassessment recounts the confirmed experience from the recorded
  fires (see below).

- *Reassessment of memory already recorded (court policy v4).* The fixes change
  the meaning of decisions, so the court policy version is raised to
  `synapse.memory.court-policy/v4`: memory decided under v3 is neither read nor
  extended until it is reassessed (`synapse memory reassess`). A reassessment
  calls neither tools nor models and decides again from what was recorded: a
  hypothesis status of the earlier rule — from the check answer that the
  gateway journal and the evidence keep (an answer about another object becomes
  `provisional`, an unavailable record `check_record_unavailable`); a T1/T4
  promotion the earlier policy decided — from the confirmed experience of the
  habit's recorded fires (`recent`), counting only the lower bound: a promotion
  that is not confirmed returns the habit to probation (`TU`), where it keeps
  acting; a conflict resolution gets back the basis it was found on (an outcome
  comparison or trials), and from then on the ordinary ladder judges it (a
  resolution from trials on a stand that did not cover the trigger brings the
  ban back). The confirmed experience since birth (`counted_since_birth`,
  `counted_tasks_since_birth`) is recounted from the recorded fires. All of
  this is the report's `reassessment` section (`hypotheses`, `promotions`,
  `resolutions`, schema `synapse.memory.reassessment/v2`), and the state after
  it is a fold of the journal that the owner reads from a verified snapshot and
  tail. The order for the operator: first declare `verifies` on every checking
  tool, then run `synapse memory reassess` once — otherwise confirmations by
  checks without a binding become `provisional` (`check_unbound`). This
  resembles audit in Kubernetes (Gatekeeper audit, Kyverno background
  scanning): admission checks only new objects, and audit rechecks the ones
  already stored. The difference is that there audit only reports violations,
  while here a reassessment revokes unconfirmed authority, because stored
  memory itself permits actions.

Acceptance: `acceptance/memory/test_hypothesis_binding_contract.py`,
`test_hypothesis_binding_acceptance.py` (canonical run: a check about another
account does not unlock a transfer), `test_reassessment_rules_contract.py`
(reassessment of statuses, promotions and trial-based resolutions on plain
data), `test_earlier_policy_reassessment_acceptance.py` (memory of court v3:
refusal before reassessment, reassessment without calls, reading by a fresh
owner from the snapshot, sessions after it), new cases in
`test_stand_trial_contract.py` and `test_automaton_experience_contract.py`.

**Third review pass of PR108 (4905d47): check basis, trial-based resolutions.**

- *A confirmation rests on the check basis (N2).* The version of the check rule
  says nothing about the checking tool's contract, the provenance graph and the
  identity rules, yet the decision depends on them. A hypothesis status is
  recorded together with the check basis (`check_basis`: the checking tool's
  contract, the claim's source, the provenance relation between the source and
  the checking tool, the identity rules). A session reuses a status only on the
  basis of the current configuration; otherwise it checks again
  (`check_basis_changed`). A reassessment decides every recorded status again,
  whatever rule decided it, from the recorded check answer and without calls:
  after a change of the checking tool's contract (`verifies`) or of the
  provenance graph, a confirmation that the new configuration does not give
  becomes `provisional`; under an unchanged configuration a valid confirmation
  is kept.
- *A trial-based resolution holds while the trials give it (N1).* A conflict
  resolution remembers its basis (`resolved_by` on the yielding habit: a
  comparison of recorded outcomes or stand trials). A trial-based resolution
  stands only while all recorded trials name the same winner. A trial that
  contradicts the earlier ones (the same situation with another result, or a
  rival's win in its own situation) takes the resolution away in an ordinary
  consolidation, without a reassessment: step 3, both habits on probation, the
  trigger slow-only again. A repeat with the same result keeps the resolution;
  a resolution found by an outcome comparison rests on the comparison; a
  resolution with an unknown basis does not stand. This resembles signed
  in-toto/SLSA statements: a resolution carries a reference to its basis, and
  its validity is checked against the basis, not by the fact that it was
  recorded.

Acceptance: new cases in `test_stand_trial_contract.py`,
`test_hypothesis_binding_contract.py`, `test_reassessment_rules_contract.py`;
`test_stand_trial_revocation_acceptance.py` (canonical run: trials resolve the
conflict, a repeat keeps the resolution, a trial on a stand with a reservation
left over contradicts the earlier ones — the next ordinary session takes the
resolution away, a fresh owner reads this from the snapshot, the session after
it takes the slow path); `test_check_basis_reassessment_acceptance.py` (a
change of the checking tool's scope, a change of the provenance graph and an
unchanged configuration: refusal before reassessment, reassessment without
calls, a session after it).

**Recheck of PR108 (c4999a4): history, parallel results, order, admission, index.**

- *A forget closes the past (R2).* A forget ends the period in which it was
  applied and all earlier ones; a repeated reading holds the knowledge only
  from its own window — the window of the forget stays without that knowledge.
- *Historical period (R3).* The candidate and the attestation of a historical
  query carry the period in which memory held the version in the requested
  window.
- *Name scope (R1).* The durable profile checks a name where the graph
  executes: a parameter of an unrelated function does not interfere with the
  graph.
- *Only an accepted answer is a source (DEEP-1).* A call node's answer becomes
  the source of a statement or a hypothesis only once the graph has accepted
  it; a stale or cancelled answer is never a source.
- *The result is the program's copy (DEEP-2).* Changing the graph's result does
  not rewrite the recorded steps, the commit or the answers.
- *Order of observations (DEEP-3).* A joint consolidation of several sessions
  folds statements in the order their sources were observed (the gateway
  sequence), not by run names.
- *Admission as recorded (DEEP-4).* A statement of memory is admitted as it was
  recorded: its validity in time and its period are determined again from the
  record and the operator's currency rule; a copy of a candidate with changed
  content is rejected (`candidate_not_as_recorded`).
- *Index (DEEP-5, DEEP-6).* A repeated reading adds no confidence but restores
  a missing vector. A vector remembers its embedder (tool, declared version,
  contract); the semantic channel compares only vectors of the embedder in
  force. After a change of embedder, old vectors are not compared until the
  statement is read again under the new one — a reassessment does not call the
  model.

Acceptance: new cases in `test_knowledge_contract.py` and
`test_dataflow_contract.py`; `test_forget_relearn_history_acceptance.py`,
`test_dataflow_source_acceptance.py`, `test_knowledge_order_acceptance.py`,
`test_knowledge_index_acceptance.py`, new cases in
`test_event_admission_acceptance.py` and `test_knowledge_return_acceptance.py`
(canonical run).

**Independent audit of PR108 (746967d): AUD-1…AUD-5.**

- *A graph's purity is a property of the called function (AUD-1).* A call in a
  pure node, in a signal condition or in a call node's arguments must name a
  pure built-in function, and where the graph executes, its name must resolve
  to exactly that function. A built-in function's name taken by a user function
  (`fn len`, `let len = change`) or by a graph node is rejected before
  execution — by the executor and by the durable profile.
- *An event is admitted at its moment only (AUD-2).* Admission and the
  knowledge timeline place time by one rule: a claim about another moment is
  `outside_validity`, however the candidate was found (including from the
  history listed by a search without a time); a claim without a time is
  `freshness_unknown`.
- *A return to an earlier answer (AUD-3).* If the source corrects a value back
  (20 → 25 → 20), memory holds the earlier version again: it corrects the
  current one, and the periods when it was held before stay in `earlier_known`
  — every past window answers as it answered then.
- *An explicit consolidation applies a forget (AUD-4).* The operator's
  maintenance program (`consolidate palace`) applies retention acts that are
  recorded and not yet applied, without a new business session: the case
  becomes forgotten, and the knowledge from its results is withdrawn from
  search. Without unapplied acts, the consolidation decides nothing again.
- *A removal marker only strengthens (AUD-5).* A forget of an already compacted
  body replaces `compacted` with `forgotten`, with its own tombstone;
  compaction does not lower `forgotten`; a forgotten body keeps the tombstone
  of the forget it left with.

Acceptance: new cases in `test_dataflow_contract.py` and
`test_knowledge_contract.py`, `test_evidence_marker_contract.py`;
`test_event_admission_acceptance.py`, `test_knowledge_return_acceptance.py`,
`test_forget_maintenance_acceptance.py`, `test_forget_compacted_acceptance.py`
(canonical run).

**Resilience review of PR108 (1b58d18): the order of checks, a window's knowledge, admission, the basis in force.**

- *A hypothesis declaration is visible to the whole session (M1).* A check made
  after a consolidation names a hypothesis declared earlier in the same
  session: the declaration is resolved from the session's verified history up
  to the end of the window, and a window applies only its own checks — a
  repeated consolidation decides nothing twice.
- *The order is the gateway's sequence (M2).* The latest check by the place of
  its answer in the gateway's journal (`at`) sets a hypothesis status, not the
  run's name or the window's number: an earlier check consolidated later is
  only marked (`superseded`); a check without a recorded answer does not
  displace a recorded one.
- *Knowledge after a window boundary (M3).* A window in which a session
  declared knowledge (`know`) is consolidated even when it holds no other
  significant events: the statement enters memory once.
- *A correction in the same window (M4).* A source correction returns to
  `provisional` the statuses decided before it on the corrected answer or
  checked against the corrected source — also when both sessions fold in one
  window. A check at the place of the correction or later stands; a correction
  without a known place reaches all statuses.
- *Admission in the replay protocol (M5).* An `admit` decision is a recorded
  event: session replay verification and crash recovery consume it in its
  place and compare it byte for byte; a different decision is an integrity
  violation, not a second record.
- *An action's basis is read again before its effect (M6).* An action's
  requirement (`requires`) carries how the decision was read: the session's
  own check or the court's record, the source observation and the check's
  answer (`gw_seq`, evidence). Before the effect, admission compares it with
  memory now — the state and the operator acts not yet applied: a later
  check, a correction of what the decision rests on, or a forget of its
  observations refuses the action before the effect. A decision made after
  such a change stands; an exam reads its fixed snapshot. An action already
  sent is not undone: the report section `relied` says whether the status was
  revoked before the action's admission or after it (`before_admission` /
  `after_admission`). A forget records the head of the gateway's journal
  (`after`), and "decided before the forget" is determined by it, and by
  windows only for unplaced records.
- *Court policy v5.* The rules change the meaning of recorded statuses: memory
  decided under v4 is not read until it is reassessed (`synapse memory
  reassess`). The reassessment (`synapse.memory.reassessment/v3`) folds again,
  without calls, all consolidated checks of all sessions and the timeline's
  corrections in the gateway's order; a status whose check no readable session
  shows (its history was deleted) is decided again from that check's answer
  recorded in the gateway's journal. Retention acts are written under the
  schema `synapse.memory.retention-pass/v2` (field `after`).
- *A run lock with its owner (LOCK).* A cognitive run lock appears at once
  with its owner record (a prepared directory is renamed into place), and the
  decision on the lock is made under an OS lock of the state directory
  (`.synapse-run-locks.guard`), which the kernel releases when its holder
  dies. A process killed while taking the lock does not leave it behind; a
  live process at the same point holds the run alone; the lock of a dead
  owner is cleared by the next run or by recovery. A lock without an owner
  (profile P2a or a version before this protocol) is still never guessed
  stale: only an operator clears it, after making sure that no live owner
  exists.

Limitations: a check of the same claim about another version of the source is
another hypothesis (another identifier), and it does not revoke the decision on
the old version; the causal order is given by the project gateway's journal,
and events outside it are not ordered by it; admission reads the basis again at
the moment the effect is sent, but a change recorded after the admission does
not undo an action already sent.

Acceptance: `acceptance/memory/test_causal_order_contract.py` (the gateway's
order, corrections, `changed_since`, a forget by the journal head, the `relied`
section on pure data); canonical run — `test_hypothesis_order_acceptance.py`
(M1, M2), `test_knowledge_window_acceptance.py` (M3, M4),
`test_admission_record_acceptance.py` (M5, including crash recovery),
`test_relied_revocation_acceptance.py` (M6: a refutation and a forget with the
session's own check and with the court's record, a control without changes, a
check after the change, an exam, the sent action not repeated),
`test_run_lock_acceptance.py` (a dead and a live process at the point of
naming the owner, a dead owner, a crashed recovery, a lock without an owner).

**Recheck of PR108 (556624d): the world's order holds across windows; forget, learning, reassessment and recovery (R1–R12).** P1-1 and P1-2 are findings R1 and R2 of the review package.

- *The order of statements (P1-2).* For each source, slot and start of
  validity, memory keeps the place in the gateway's journal of every folded
  statement, copies too (`knowledge.stated`). The slot holds what the source
  said last, whatever window the session finished in: a statement the source
  made before the one memory holds, and folded later (the session finished
  later), stays history with its provenance — closed in the same window in
  which it became known (`known_from = known_until`, `corrected_by` is what
  memory holds), marked `late` in the report — and every past window answers
  as it answered. A restatement of the same content keeps what memory holds
  current against a delayed intermediate reading; a source that really
  returned to an earlier answer says it again later and holds it again.
- *Corrections are derived from the order.* A statement whose content differs
  from the previous one corrects every answer of the series of equal
  statements that it ends, at its own place. A window's corrections are those
  that its statements add to the order: a late statement between two others
  gives the same corrections as in one window.
- *A delayed check (P1-1).* A hypothesis status keeps what its latest check
  decided (`checked`) and equals that decision revised by every correction
  that reaches it. A check made before a correction that another session
  consolidated first stays `provisional` at the place of the last correction —
  whether or not memory had a record of the hypothesis, `confirmed` or
  `provisional`; a correction also moves an already `provisional` status to
  its place. A check after the corrections stands. When a window's statements
  change a source's order, every status resting on that source is decided
  again: it moves to an earlier place or belongs to its check again
  (`hypotheses.restored`); a correction already known to memory that reached
  a late check is named in `hypotheses.corrected`. A revocation by a forget is
  not undone.
- *Admission reads the basis itself.* Before the effect, admission looks for
  the corrections that memory's order makes to the answer the hypothesis was
  read from, or to the source it was checked against, after the decision the
  action rests on — whether or not memory has a status for the hypothesis.
  One rule (`reaches`) decides for the fold, the admission and the
  reassessment. An action already sent is not undone; a recovered session
  takes it from its record and does not admit it again.
- *A report carries only what was added.* The report section
  `apply.knowledge.stated` carries the statements that the window added to
  each order; the projection joins them by the same rule as the court
  (`placed_in`), so the order of a source read in every window is not
  rewritten whole.
- *Court policy v6.* Memory decided under v5 is not read until it is
  reassessed (`synapse memory reassess`). The reassessment
  (`synapse.memory.reassessment/v4`) restores the order from the consolidated
  declarations of all sessions whose record verifies
  (`reassessment.knowledge.order`), holds each slot by what the source said
  last (a version folded late under v5 is corrected from the reassessment's
  window, earlier windows answer as they answered), and folds the statuses
  with the corrections of that order; a correction that the timeline recorded
  but the restored order does not place reaches every status it can. The
  state snapshot is the projection `synapse.memory.state-projection/v2`: v1
  snapshots are not used, the state is folded again from the chain of
  reports.

- *A forget and a new observation (R3).* A forget act removes the recorded
  results observed up to the head of the gateway's journal that it names
  (`after`). Store D addresses a result by its content, so the same answer
  received after the forget is a new observation: the next retention pass
  that completes the forget again does not remove its body, and the forgotten
  case stays forgotten.
- *The forget policy (R4).* The operator forgets the named case and every
  case carrying its recorded results. A result that a retained case also
  carries stays in store D, and that case still reproduces exactly.
- *A forget and a correction in one window (R5).* A forgotten basis case no
  longer names a run (`dependencies.basis_runs`), and the window in which a
  forget and a correction of knowledge are applied decides without it.
- *A forgotten example in a candidate (R6).* A forgotten example leaves the
  candidate — both in the pool and in a composition (`pool.retained`): it
  takes part neither in the criteria nor in the evidence that Gold verifies.
  Fresh examples teach; the same answer received after the forget is an
  example of its own.
- *Demand does not bring back a lost basis (R7).* Demand does not wake a
  habit that has fewer retained bases than a birth requires (`cold_checks`,
  `refused: basis_not_retained`). Its candidate accumulates examples again,
  fresh ones only, and the procedure is born as a separate habit (typed check
  `archived_unfounded`). The archived habit keeps the basis it was born on
  and no longer wakes. A habit archived for disuse wakes on demand as before.
- *Event identity (R8).* A wake consumes the events that matched by the pair
  "run, event"; the report names a match by both. An event number is unique
  only within a run.
- *A reassessment after a partial forget (R9).* An unavailable basis episode
  has no evidence. A republication carries the evidence of verified episodes
  only, and Gold's refusal is recorded with its reason.
- *A reassessment from store D (R10).* A reassessment reads every basis
  episode from the owner's own custody: the session is re-executed from the
  replay data and the recorded results and must carry the case's exact events
  at their places. An external run file is not needed. If store D no longer
  holds the replay data or the re-execution does not reproduce the case, the
  episode confirms nothing.
- *An emergency window (R11).* An emergency consolidation before a crashed
  session continues applies nothing: no cursor, no case, no signal, no birth,
  no boundary. Its report names the tail it saw in `window.deferred`, and
  `window.sessions` is empty. The session's next window judges that tail
  once, with re-execution: a segment completed before the crash teaches, and
  a fire before the crash counts once. A window that failed the integrity
  check is still closed: its cursors move on.
- *A new decision for a pair (R12).* A decision by trust gap (step 1) clears
  the pair's earlier resolution (`yields_to`, `resolved_by`): the report, the
  boundary and the runtime name one winner.

Limitations: a version that memory holds but no folded statement places
(decided before the order was kept, and not restored from the record) is
ordered by arrival; the order keeps one record per reading (about 160 bytes of
state), and a window's cost grows with the orders of the sources it reads and
the number of statuses resting on them (`docs/MEMORY_PERFORMANCE.md`); a
statement whose observation was forgotten keeps its place in the order — a
forget withdraws what was read, but not the fact that the source later said
something else: the correction it made stands, a reading made before it stays
history, and a reassessment does not hold the forgotten version again; of two
readings that say the same thing in different answers, the one folded first
stays the version and the other is a copy, whichever was read earlier (the
value is the same; an admission naming the copy's answer refuses as for
another version); the causal order is given by the project gateway's journal.
A reassessment re-executes once from store D the session of every basis case
of a live habit; a habit that a forget left with fewer bases than a birth
requires no longer wakes, and its procedure is learned again only from fresh
examples.

Acceptance: `acceptance/memory/test_causal_schedule_contract.py` (a property:
any schedule of the same observations — one window or several, sessions in
any order, late publication of statements and checks — gives the same as an
independent reading of the world's order, through the projection of the
reports; targeted cases); canonical run — `test_delayed_check_acceptance.py`
(no record, `confirmed`, `provisional` × one and two corrections, a control
without changes, a check after the corrections, crash recovery, a booking
already sent, reassessment), `test_delayed_statement_acceptance.py` (forward
and reverse order of completion, a restatement against a delayed intermediate
reading, a true return, a hypothesis from a superseded answer, recovery,
reassessment), `test_earlier_order_reassessment_acceptance.py` (memory of
court v5); findings R3–R12 — `test_forget_scope_acceptance.py` (R3, R4),
`test_forget_correction_window_acceptance.py` (R5),
`test_forget_learning_acceptance.py` and `test_forget_composition_acceptance.py`
(R6, R7), `test_cold_event_identity_acceptance.py` (R8),
`test_reassessment_custody_acceptance.py` (R9, R10; a contract case in
`test_reassessment_rules_contract.py`), `test_crash_closed_segment_acceptance.py`
(R11, beside the updated `test_crash_recovery_acceptance.py`),
`test_conflict_decision_execution_acceptance.py` (R12); each has a positive
control and its own CI shard.

**Completion of the PR108 review: bases, dependencies, trials, measurements.**

- *A contract violation by a habit's body (R6).* The gateway marks a refusal
  of what the contracts forbid unconditionally (a hidden retry of an unknown
  effect, a retry with changed arguments, an idempotency key from the agent)
  with the field `breach`; an unmet precondition (an unestablished
  hypothesis) does not count as a violation. A learned habit whose body made
  such an attempt goes to `dormant` in the same consolidation by rule TV
  (reason `contract_violation`) regardless of trust; for a declared habit the
  court publishes a recommendation to its author. Report section
  `contract_violations`.
- *The admission basis (R3, in-toto/SLSA).* Every candidate with a known
  basis carries `attestation` (`synapse.memory.admission-attestation/v1`):
  the statement with its value type, the version (the source's version, the
  validity period, the moment asked about), the verification (method `probe`
  or `court_record`, rule `synapse.memory.hypothesis-check/v1`, the checking
  tool's `contract_ref`, the recorded observations, the session and the court
  window), the provenance (the record, the source, the identity link, the
  copies), the outcome for this statement (`confirmed`, `refuted`,
  `undecided` — expired freshness refutes nothing) and the dependencies. An
  admitted fact carries the statement's attestation; the `memory_admission`
  event records it. A record that names a source other than the one the
  hypothesis read is a substituted source (`basis_from_another_source`).
- *The dependency graph (§8.2, W3C PROV).* `court/dependencies.py` builds a
  projection from the memory state: a case `hadMember` observations, a
  hypothesis status `wasDerivedFrom` the source answer and the check's answer
  and `wasGeneratedBy` a case, a knowledge version `wasDerivedFrom` an answer,
  a correction `wasRevisionOf`, a habit `wasDerivedFrom` its basis cases and
  `used` the versions its sessions admitted. Unknown relations are listed,
  not taken as absence. After `forget` the court separates three acts: the
  data are removed (the act itself), the versions read from forgotten answers
  are withdrawn from search (`withdrawn`, the history is kept), the
  authorities are revoked (hypothesis statuses decided before the forget — to
  `provisional`; a habit without a sufficient retained basis — TR; a habit
  whose basis admitted a withdrawn version — TC). Report section
  `dependencies`. A new reading of the same answer after the forget is a new
  observation.
- *A check of the basis version before the effect (§8.2, modeled on
  `resourceVersion`).* Before an action that requires hypotheses, every basis
  read from a court record is read again: a status that the court revised
  after the window in which it was read, or one that rests on forgotten
  results (including by a `forget` act not yet applied), rejects the effect
  before it starts. A check of this same run is fresh; an exam reads its
  snapshot.
- *An explanation of the generalization and contract versions (§8.1).* A
  birth publishes `generalization` (`synapse.memory.generalization/v1`): the
  verified conditions, the provenance of every argument and the reason it is
  a variable (the values differed and are explained by one source in every
  episode; an unchanging value stays a constant), the checks of every basis
  episode and the contract versions. The habit's metadata keep
  `verified_under.contracts`; a session does not load a habit whose called
  tool's contract is not the one it was verified under (`unverified` in the
  session opening) until a reassessment verifies it again
  (`contracts_changed` in the reassessment report).
- *Stand trials (R5 §4–5).* A trial arm is a mode B exam on one snapshot with
  `--exam-trial hab_…`: the habit runs alone, on a stand restored before
  every arm. `synapse memory trial` judges the two arms by what was recorded
  (the first reaction to one situation, identical observations before the
  habit's action, the outcome by the anchor and the body's result without
  the advisor, a contract violation is a failure) and records the trial with
  the basis `stand_trial`, the stand and the declared transfer scope. The
  trial comparison (`compare_trials`) counts decided trials inside the scope,
  each situation once, and only for a trigger all of whose values the scope
  reproduces; outside the scope a trial proves nothing. The trials' winner is
  a verified result of the ladder's second step when the recorded outcomes
  give no winner.
- *The state snapshot and the journal tail (§8.3).* Every eight reports the
  court records a state snapshot (`MEMORY_STATE`: the projection version, the
  cut — the decision and the report — and the state digest). The owner reads
  the latest snapshot that matches by version, cut and digest, and folds only
  the tail; otherwise the whole journal. A decision that named a report
  receipt different from the recorded report of its consolidation is
  rejected: the state is never folded from a report the decision did not
  name. Reports are read from the already verified journal scan, the newest
  snapshot boundary is searched from the head of the chain, verified event
  bytes are not checked for canonicity again (the hash and the marker are
  checked on every read). The semantic ranking is exact: every vector, the
  query norm once, the same floating-point numbers, equal scores ordered by
  identity. Measurements are in `docs/MEMORY_PERFORMANCE.md`.
- *The `confidence_sequence` rule (R6, next stage).* An optional automaton
  rule: a time-uniform bound (normal mixture, Howard et al., 2021) on the
  mean signal of the state's distinct tasks, read at every fire; one task is
  one observation. Promotion when the lower bound is not below `cs.promote`,
  demotion when the upper bound is below `cs.demote`. With `alpha = 0.05` a
  promotion requires about thirty distinct tasks — the price of continuous
  monitoring. The default remains `threshold`.

Verified limitations of this completion:

- A trial does not create the stand: restoring the environment before every
  arm is the stand operator's duty; the court only checks that the
  observations of both arms before the habit's action matched.
- After `forget`, derived versions leave search and authorities, but the
  court's historical reports (immutable records) contain the statements that
  were read; `forget` removes the bodies from store D.
- External batteries: LongMemEval is measured only in its retrieval part
  (`scripts/benchmark_longmemeval_retrieval.py`, the lexical channel, without
  a reader model and a judge); answer accuracy and the semantic channel are
  not measured; MemoryArena was not run (an agent environment and a model are
  needed).

### Acceptance and evidence boundaries

Acceptance of learned habits is `acceptance/memory/` (a separate CI job
`memory-acceptance` with `.[agent-mcp]`). Each scenario runs
`python -m synapse run --durable --project-state --memory-config` against real
MCP stdio servers (`acceptance/memory/tool_server.py`), and the checker reads
three independent places: the run artifacts, the project journal and the tool
server environment's own journal. Files: `test_habit_learning_acceptance.py`
(consolidation, birth, loading, applicability, trust, withdrawal, copies),
`test_provenance_acceptance.py` (the control cases of section 7),
`test_operation_outcome_acceptance.py` (an operation error, compensation, a
foreign resource, a lost answer, a state check),
`test_crash_recovery_acceptance.py`, `test_two_streams_acceptance.py`,
`test_conflict_acceptance.py`, `test_experiment_modes_acceptance.py`.
Memory stage 4: `test_court_decision_table_contract.py` (the court's decision
table over pure data — each trust and automaton threshold below, at and above
it; equal trust and permuted records), `test_conflict_trust_gap_acceptance.py`
and `test_conflict_counterfactual_acceptance.py` (steps 1 and 2 of the ladder),
`test_operation_identity_acceptance.py` (cancelling exactly what was created,
two independent operations, argument substitution on a retry),
`test_fast_path_outcome_acceptance.py` (the same rules in the body of a
declared habit), `test_retention_compaction_acceptance.py`,
`test_retention_crash_acceptance.py`,
`test_retention_substitution_acceptance.py`,
`test_retention_capacity_acceptance.py`, `test_forget_acceptance.py`,
`test_hypothesis_protocol_acceptance.py`, `test_hypothesis_reuse_acceptance.py`
(PR108 review: `test_admission_basis_contract.py`,
`test_admission_identity_acceptance.py`,
`test_effect_uncertainty_acceptance.py`,
`test_idempotency_crash_acceptance.py`, `test_gateway_sequence_contract.py`,
`test_conflict_resolution_contract.py`, `test_conflict_overlap_acceptance.py`,
`test_automaton_experience_contract.py`, `test_automaton_sequence_contract.py`,
`test_automaton_experience_acceptance.py`, `test_reassessment_contract.py`,
`test_memory_reassessment_acceptance.py`, `test_paired_exam_acceptance.py`;
completion: `test_contract_violation_acceptance.py`,
`test_fast_path_attestation_acceptance.py`,
`test_partial_effect_acceptance.py`, `test_hypothesis_expiry_acceptance.py`,
`test_dependency_graph_contract.py`, `test_forget_dependencies_acceptance.py`,
`test_forget_knowledge_acceptance.py`, `test_basis_version_acceptance.py`,
`test_generalization_explanation_acceptance.py`,
`test_state_snapshot_acceptance.py`,
`test_memory_lifecycle_sequence_acceptance.py` (end-to-end sequences with
crashes against an independent model), `test_stand_trial_contract.py`,
`test_stand_trial_acceptance.py`,
`test_automaton_confidence_sequence_contract.py`,
`test_order_outage_acceptance.py` (the training order and an environment outage
separately, a 2 × 2 design), `test_held_out_domain_acceptance.py` and
`test_held_out_knowledge_acceptance.py` (a held-out domain; a card, a copy and
a paraphrase in one part of the split)) and
`test_palace_admission_acceptance.py` (the sensitivity lattice is published as
the `admission_lattice` property of the pytest report). Memory stage 5a:
`test_applicability_generalization_contract.py` and
`test_result_dependency_contract.py` (rules over pure data),
`test_applicability_contrast_acceptance.py` (a boundary from contrasts, the new
admissible, near inadmissible and unknown situations, an unverified gap, a
change of the tool contract), `test_applicability_widening_acceptance.py`
(widening by recovered misses and a refusal over a contrast),
`test_result_dependency_acceptance.py` (cancelling exactly the created job
while a similar foreign one exists, re-execution and resumption without a new
job), `test_result_dependency_refusal_acceptance.py` (a producer refusal, a
missing field, an empty field and a field of another kind) and
`test_result_dependency_driver_literal_acceptance.py` (a driver literal gets no
dependency). Memory stage 5b: `test_composition_contract.py` (join rules,
interface, identity, executor over pure data), `test_composition_acceptance.py`
(a new procedure from two verified parts, accounting for an operation retry,
the birth and fast path of a composite habit),
`test_composition_refusal_acceptance.py` (incompatible conditions, partial
success, a spelled-out chain is not counted),
`test_composition_conflict_acceptance.py` (a compensation conflict),
`test_composition_review_acceptance.py` (mode C as material for slow planning,
withdrawal of a part and review of the composition),
`test_composition_alternatives_acceptance.py` (the next alternative recovers
what the first did not), `test_composition_nested_acceptance.py` (a part inside
a part, withdrawal of a nested part) and
`test_composition_sequence_acceptance.py` (a procedure that ended in a new
refusal, and the one that follows it). Memory stage 6:
`test_knowledge_contract.py` (the timeline, rank fusion, the court's fold and
admission checks over pure data), `test_knowledge_search_acceptance.py` (a
paraphrase with no shared words, a negation and another entity, recall at the
same budget and the channel's cost), `test_knowledge_timeline_acceptance.py` (a
late report, knowledge as of a past window, bounded and event currency) and
`test_knowledge_correction_acceptance.py` (a correction with revision and
history, a repetition, a poisoned mirror). Language stage 7:
`test_dataflow_contract.py` (the graph's shape, the profile, the observation
contract, consistent versions, the loop bound),
`test_dataflow_overlap_acceptance.py` (overlap and `limit 1`, an unrelated slow
node, another order of answers, the refusal of an action from a node),
`test_dataflow_revision_acceptance.py` (superseding a stale proposal, waiting
instead of mixing versions, replay by retention) and
`test_dataflow_crash_acceptance.py` (a crash in the middle of a graph and
resumption).

Heavy scenarios are split out into independent files and separate CI jobs:
`test_procedural_source_plan_acceptance.py`,
`test_project_memory_lifecycle_acceptance.py`,
`test_episode_outcome_acceptance.py`, `test_court_acceptance.py` and
`test_gold_memory_slices_acceptance.py`. The court check takes an interruption
before the decision, removed and restored run records, a pinned slice and two
subsequent task streams through the CLI; parallel journal writers are checked
by a contract with four processes; the court's contract over pure data is
`test_project_court_contract.py`. The episode outcome check takes a real
infrastructure failure of the oracle and a completed run through the canonical
CLI. `test_gold_memory_slices_acceptance.py` uses real canonical publications,
the installed Mini, C1 and the executing oracle, including control runs without
positive and without negative experience. The controlled HTTP endpoint returns
only neutral edit proposals.

The information boundary checks consist of code analysis, pure contracts and
ordinary successful Mini requests. Format refusals are checked by pure
contracts in test_local_edit_contract and test_public_request_policy_contract;
the real Mini scenario sends ordinary typed proposals. Active experiments in
bypassing the restrictions are not run at the user's request. The check with
the controlled endpoint is not a live Gemini run and does not establish savings
on real tasks. AS2 production readiness stays locked.

### GitHub checks and the cost of reading evidence

In GitHub Actions on the original head `8beef0d5`, two primary causes of
failure were found: the map of the narrow C1 boundary did not account for the
already existing pure comparison of the oracle environment fingerprint; the
reuse of successful memory exceeded the 3600-second limit of an individual CLI
command. The aggregate `gold-slow` job failed following this scenario.

The C1 list is checked against the actual read-only comparison of the retained
configuration. It does not run the oracle and adds no authority to the
executor. The ownership map separately declares the shared contracts
`source_procedures.py` and `stage10/planning_basis.py`;
`runner/procedural_observations.py` is the adapter from physical publications
and replay to the planning basis contract. Shared contracts are not disguised
as internal parts of a single consumer.

Profiling confirmed repeated compilation of one and the same immutable
behavior/manifest pair when delivery evidence is read. The owner `behavior.py`
now retains the result of this pure computation keyed by the full bytes of the
evidence and the content ref: up to 64 entries, each with a proof of up to
256 KiB. Larger proofs go through the same full computation path without the
result being retained. The reader gets a new typed ref from the immutable
bytes.

This result is not an admission. Physical files, provenance, lifecycle, current
compatibility and point-of-use gates are opened and checked again by their
owners. Changed bytes or another content ref require a separate check;
exceptions are not retained as successful results. Scenario timeouts are kept.

On two identical physical publications, the local read under cProfile took
22.72 seconds before the change and 10.44 after; the hash of the full transport
result matched. This is a measurement of the reading stage on one machine, not
an estimate of Gold's overall performance. An additional read of a pair of
publications that includes a successful result took 33.68 and 11.59 seconds
respectively; the full transport content matched as well.

A full local rerun of gold-fast completed: 2086 passed in 297.04 seconds. The
previously failed test_partial_memory_loop_acceptance completed: 1 passed in
1594.15 seconds. Its real outcomes are VERIFIED_REUSABLE_PARTIAL → FULL → FULL;
three provider calls, an independent oracle and a resume without repeated
effects. These are local results of the fixed code; the new GitHub status will
be known after the push and the workflow run.

### Final local acceptance before push

The five-run test_gold_memory_slices_acceptance completed: 1 passed in
3955.25 seconds. Outcomes: VERIFIED_REUSABLE_PARTIAL →
VERIFIED_REUSABLE_PARTIAL → FULL → NO_CANDIDATE → FULL. The control without
negative memory keeps the public choice; full experience selects the verified
partial memory. Without positive memory, an empty proposal yields no candidate;
with positive memory, it uses the stored method. The corresponding public
requests are equal under different local memory slices.

Five calls to the controlled provider and four real independent oracle checks
were recorded. The source files and frozen inputs did not change. A repeated
resume returned the earlier result without new requests, oracle effects or
memory events. After the comparison of Mini's public fields was refined, 13
pure SDK contracts and four ordinary scenarios of the installed Mini passed
separately.

The summary of the saved successful JUnit reports contains 2363 unique cases
without double counting: 0 failed, 0 errors, 0 skipped. It includes the full
gold-fast, the related contracts, architecture, source, plans, memory and the
named heavy scenarios. This is targeted acceptance of the changes, not a rerun
of the whole repository suite. The standard pre-commit passed: 45 Synapse
examples parse, CVM corpus coverage is 0.935620 against a threshold of 0.9332,
47 of 47 files.

Reproducible separate runs of the heavy scenarios:

```bash
python -B -m pytest -q acceptance/stage4/stage16/test_partial_memory_loop_acceptance.py
python -B -m pytest -q acceptance/stage4/stage16/test_gold_memory_slices_acceptance.py
python -B -m pytest -q acceptance/stage4/stage16/test_procedural_source_plan_acceptance.py
```

The scenarios ran on Linux with Python 3.12 and the pinned gold-worker
dependencies; separate files allow independent CI jobs to be assigned to them.
Raw reports and evidence are stored locally. The status of the new GitHub run
is checked after the push and is not inferred from the local PASS.

### Reading the coordinator, memory and evidence — 13 September 2026

The refinement keeps the existing owners and file contracts. When an already
created coordinator identity is read, the function
`create_coordinator_metadata_once` opens and checks the actual bytes without
new staging, writing or `fsync`. This is not an in-process value cache: the
next access reads the physical file again. The initial creation keeps
synchronization and publication without overwrite; when creators compete, the
winner's physical identity is used. An error reading an existing identity does
not count as the file's absence and does not permit creating another identity.

`ProjectMemoryStore` reads the initial inventory once on entering its own
exclusive session and retains it only for this live guard. A successfully saved
new event is first physically opened and checked, then added to the owner's
view. The returned records and receipt are separate from the retained state, so
a change to an object by the calling code does not change the owner's memory. A
new session, an inventory without a guard and a read-only reader open the
physical events again. An explicit `read` and an idempotent `put` also check
the retained bytes.

An error during staging, commit or the subsequent readback invalidates the
session's view, even if the calling code caught the exception. It cannot be
used further; a new session first checks the actually visible events and only
then recovers the unfinished epoch. Unconfirmed preparations do not become
visible events, but they count toward the transaction limit. The earlier size
limits, the immutability of events and the order of their durable publication
are kept.

`stage14/read_traversal.py` sets the lifetime of a shared traversal of
publications. It can span several roots of one outer synchronous readonly
reconstruction: `read_project_run_knowledge`, `read_source_snapshot`,
`read_frozen_source_experience`, `execution_graph`, `read_input_graph` and
`_attempt_graph`. `PublicationResult` retains only bytes already checked by
their physical reader, up to 128 entries / 32 MiB per such traversal. Results
that do not fit in the limit are not retained: their next read is done in full
again. The active and historical profiles have different keys; each consumer
gets a separate JSON object. The next outer call reads afresh. An exit by
exception also closes the scope; completed copied contexts and other threads do
not use its results. This boundary does not cover execution, publication or the
admission decision.

In `compatibility_store`, only the pure decoding of an individual history frame
is reused: the full raw bytes serve as the key, an immutable `_HistoryFrame` as
the result. Frames of up to 64 KiB inclusive may enter an LRU of 1024 entries;
larger ones go through the same decoder every time, without retention. The
total size of the raw keys is limited to 64 MiB; the size of the retained
`canonical_bytes` does not exceed the size of the raw frames. This is not a
limit on all of Python's memory: objects and auxiliary structures need
additional memory. Changed bytes get a different key, and a decoding error is
not stored as a successful result.

This LRU may outlive an individual read, because it holds the result of parsing
specific immutable bytes, not a read version of the journal. Every access still
physically scans the journal and checks its integrity, sequence, exact parent
anchor, coordinator and duplicates. Append synchronization, readback and the
history check after a write are kept; the cache creates no receipt and grants
no admission. Profiling found at least four repeated full parses of the old
compatibility history on append. In the examined slice, 18 journals contained
588 frames, and the maximum frame size was 47 758 bytes. This is a description
of the measured slice, not a new limit of the product format.

Acceptance reuses an already reconstructed result only between the assertions
of one unchanged phase. In automatic-negative, each completed run is
reconstructed once to check its attempts; after `approve` the consumer is
opened again. The shared `completed_attempt` is located in
`acceptance/stage4/stage16/_completed_attempt.py` and remains an
acceptance-only reader. The five heavy scenarios keep separate files and CI
shards: automatic-negative-replay, positive-replay, retained-patch-mini,
partial-memory-loop and gold-memory-slices. The number and order of runs, the
causal memory controls, the real Mini, C1, the independent oracle, the separate
`resume` process and the check of physically changed retained bytes are not
reduced.

The explicit `gold-fast` list includes
`tests/test_stage4_gold_coordinator_read_io.py`,
`tests/test_stage4_gold_compatibility_decode_reuse.py` and
`acceptance/stage4/stage16/test_project_memory_session_inventory_contract.py`.
They check short operations on real files and individual simulated save errors.
The already included
`acceptance/stage4/stage13/test_publication_read_contract.py` is extended with
contracts for several roots and for an exception in the outer scope; in this
file the physical reader is replaced, so it demonstrates only the traversal's
lifecycle.

The initial heavy acceptance of this refinement was deliberately stopped after
the repeated CPU parsing of the compatibility history was found. The stop is
neither a PASS nor a FAIL caused by a regression. There are no final results
for the five heavy scenarios yet. Speed-up percentages and an overall reduction
of physical writes to the SSD are not claimed; the physical writes needed to
save new events and evidence remain. The completed results given above refer to
the previous verified slice, not to the acceptance of this refinement.

Local verification of this refinement: before the last decoder optimization,
837 architecture checks on Windows, 93 short file contracts on Linux and the
existing corpus gate passed. After the decoder fix, exactly the two affected
files passed: 16 tests on Windows. These groups are not declared a new full
repository run. On the operator's instruction, further local tests were
stopped; the final verification of the five heavy scenarios is handed over to
the standard GitHub CI after a push of the existing PR #108 branch.

### Decisions checked against primary open sources

The comparison was made on 12 September 2026. The applicability of the
approaches to Synapse is a conclusion drawn from the project's contracts and
acceptance, not the result of a performance comparison of these systems. No new
infrastructure dependencies are needed.

| Area | Confirmed mechanism | Adopted decision and limit |
| --- | --- | --- |
| Repeated pure computation | [Python functools.lru_cache](https://docs.python.org/3/library/functools.html#functools.lru_cache) retains a bounded number of results keyed by arguments; the documentation distinguishes this from functions with side effects and mutable state. | Full immutable proof/content bytes and a result in bytes allow only the computation of the structural binding to be reused. This cache does not replace reading stores or decisions about authority; the separate retention of checked publication bytes is limited to the readonly traversal described above. |
| Mini Environment | [The official Mini documentation](https://mini-swe-agent.com/latest/advanced/environments/) provides for choosing the Environment class; the standard local one runs a subprocess directly on the host. | The Mini 2.4.6 profile plugs in its own Environment through the standard port. It completes a typed proposal, while C1 performs the changes and the checks. This is a capability contract of the installed extension; OS-level isolation of an arbitrary modified process is not claimed here. |
| Owner state | [Orleans: lifecycle](https://learn.microsoft.com/en-us/dotnet/orleans/grains/grain-lifecycle) loads state before activation and warns that lifecycle completion may not happen on a crash. | The task and every visible transition are saved before servicing completes. A retry reads the original slice; correctness does not depend on a finalizer being called. Orleans is not added as a second runtime. |
| Plan recovery | [Temporal: Workflow Execution](https://docs.temporal.io/workflow-execution) describes matching replay commands against the retained history. | The saved choice of methods is checked against the physical CVM observations, and then against the operation graph. A historical external action is not repeated to get a convenient result. The existing CVM, activity records and the Gold controller are used. |
| Local data boundary | [CaMeL, the authors' paper](https://arxiv.org/abs/2503.18813) separates data and control and restricts flows through capabilities. | Mini's output is built from fixed public roots and the actual public SDK history. The local information port does not become a provider message. The principle of explicit data provenance is borrowed; the guarantees of the whole CaMeL system are not attributed to Synapse. |
| Exact SDK contract | [Jinja API](https://jinja.palletsprojects.com/en/stable/api/) documents the removal of the trailing newline by default. | The public template is checked against the actual Mini/Jinja rendering. The normalization concerns one expected newline; the check of the outgoing request's content stays exact. |

The adopted versions of mini-swe-agent, LiteLLM and the OpenAI SDK are pinned
in the runtime profile. When they are updated, the format check of the actual
request and the ordinary Mini scenarios must be repeated. Observing the API
gives Gold no right to call an LLM on its own.

## Purpose and boundaries

`project connect` creates an empty library. Emptiness is allowed, but a Gold
run requires previously admitted, compatible knowledge. Initial ingestion
closes this gap by actually studying the sources. It does not create a
fictitious Gold attempt, a C1 result, an earlier successful run or `FULL`.

The initial profiles are checkable repository facts (`REPOSITORY_FACT_CHECK`)
and verified procedures for working with it (`VERIFICATION_RECIPE`). A behavior
type by itself does not mean that the profile is implemented or that a
particular record was admitted.

An observation, a candidate and admitted knowledge have different authority.
Model text or documentation can propose a candidate; the author of the proposal
does not certify its correctness and does not write it directly into the search
index. Confirmation is limited to the checked claim and its conditions. For
example, the presence of a symbol's definition does not prove that the function
is correct, and a successful run of a check does not prove that the whole
program is correct.

## Owners and dependency direction

| Responsibility | Owner |
| --- | --- |
| Operator entry point | `python -m synapse` → `synapse.cli.main` |
| Project configuration, the shared fence and opening stores | `knowledge_environment.py` |
| Binding a structural fact to committed bytes | existing `bindings.py` |
| Knowledge type and executable contract | existing `behavior.py` and compiler binding |
| Checking the source claim | `source_verification.py` |
| Assembling the ingestion operation and its journal | `source_ingestion.py` |
| Selecting stored study experience for a task | `source_experience.py`, a read assembly over the same journal |
| Scope of permitted reuse | existing `compatibility.py` |
| Publication decision | existing `stage13/publication.py` |
| Atomic write and recovery | existing `stage13/publication_store.py` |
| Typed lineage and physical reconstruction | existing `stage14/` |
| Measurements and source reconciliation | existing resource usage and Stage 15 owners |
| Testing the mechanism | external `acceptance/` and `tests/` |

The new provenance profile extends the existing publication pipeline. A second
publisher, an alternative runtime, a separate memory library or importing
acceptance fixtures into the product are forbidden. Shared semantics must not
be copied into the new profile. If a responsibility is transferred, the
previous ownership is removed. Python modules are split by contracts and
responsibility; there is no LOC limit.

## Process

1. The operator sets the exact Git commit, the allowed sources and the
   environment profile. Committed bytes are read. A modified working tree does
   not substitute for the source. Paths, symlinks and escapes from the allowed
   scope are checked before reading/execution.
2. Materials sufficient to reopen the source and check its identity are saved:
   the source bytes, the revision and the bindings. A reference or a SHA-256
   string alone, without retained bytes, is not enough.
3. A typed candidate is formed with a narrow claim, inputs, preconditions,
   expected effects and a scope of applicability.
4. An independent product verifier checks the facts. For procedures it performs
   real actions in the fixed environment and checks predefined conditions. A
   zero exit code without a check of the stated result is not enough. What was
   not executed, was interrupted or is unknown does not turn into success.
5. The existing publication owner checks authority, provenance, taint,
   lifecycle and the ingestion/publication gates. The checker is independent of
   the source, the candidate's author, the builder, the worker and the
   publisher.
6. Executable knowledge becomes available in the library index after an atomic
   commit of all required participants, including the retention of physical
   evidence and lineage. Materials and unsuccessful observations are available
   separately by reading the journal; saving and searching them is not an
   admission of executable behavior.
7. Later use goes through the ordinary retrieval/consumption gates,
   compatibility, snapshot and replay. A change of commit, environment or
   policy does not inherit the old permission automatically.

Bounded practice can propose new tasks to explore capabilities of the
environment that have not been checked yet. Its budget is set in advance;
repeating already confirmed claims does not count as new knowledge. The model
does not determine its own correctness by grading its own answer. Unsupported
claims stay unadmitted with an explicit reason.

## Reuse contract

The earlier `compatibility.py` profile still compares the exact task identity
and the full `OracleObservation`. For verified source files, a separate
checkable link from the source to the consumer is introduced: the same commit,
the saved source files, the reconstructed bindings, the allowed scope, the
observed Python environment, the policy and the tools. The presence of a
profile name does not replace the physical checks. The replay budget must match
the budget under which its trace was certified.

Both supported kinds of knowledge go through the following contract:

- For a structural fact, the source commit, the exact binding, the stated claim
  and membership in the consumer's allowed context are checked. A fact that a
  function exists does not become permission to change or call it.
- For a procedure, the environment, the tools, the inputs, the preconditions
  and the permitted effects are checked in addition. Success in one environment
  gives no permission to replay the procedure in another.
- The historical verifier of the knowledge and the oracle of the new task keep
  their different roles. A future oracle must not be recorded in provenance as
  already observed.
- The result of the applicability check contains the physical evidence and the
  reasons for each required axis. The comparison must not be skipped on the
  strength of the `behavior_kind` value, the profile name or the presence of a
  record in the library alone.
- The existing exact-match profile keeps its behavior. Its refusals do not turn
  into permissions because another profile was added.

The identity of source knowledge must not depend on a consumer contract that
does not exist yet. The new `GoverningTaskContract` v2 contains no
`behavior_refs`: the task fixes the requirements, and the Intent receives the
specific knowledge references from the admission of this attempt. The
historical v1 keeps the earlier contract and the exact references. The link
between a specific consumer and previously published knowledge is established
by a new applicability check; the producer attestation keeps the original
provenance.

## Full loop refinement history — 8 September 2026

The first part of the changes of 2026-09-08 separates the task's requirements
from the selection of knowledge. It touches the existing owners
`stage10/task_contract.py`, `runner/attempt_plan.py`, `stage10/approval.py`,
`stage12/verification.py`, `run_knowledge.py` and their assembly points. There
is no new runtime or memory owner.

For v2 the task does not specify the knowledge set. The Intent and the
operation are bound to the attempt's actually admitted references; an
independent plan check refuses a substitution of this set. Approval v2
explicitly permits selection from the current admitted snapshot while the
conditions of the task and the operations stay unchanged. Reading a completed
attempt checks its stored selection basis and that basis's digest and identity.
Historical task/approval v1 keep their exact conditions; no new grants are
derived from old records.

Instead of a knowledge item's position in the original list, ranking uses the
share of matched bindings to the task's targets. This is a limited structural
feature: the corpus is still set by the export, all of its admitted elements
can enter the selection, and semantic search and autonomous construction of
bindings are not implemented. Formats and current limitations are described in
`STAGE11-GOLD-RUN.md`.

Local verification of this part: 44 task/approval contract checks passed; two
variants of the two-attempt scenario (task v1 and v2) confirmed execution,
persistence of the grant and reading of the completed run without repeated
effects. For v2, a substitution of the knowledge made consistently in the
Intent and the operation was rejected by the independent compatibility check.
The canonical CLI scenario with two real publications (a fact and a procedure)
passed in 579.77 s: ranking, replay, delivery, the selection basis and
retention of the source evidence were checked. The dependency direction checks
and the separate `scripts/stage9_ownership_dag.py` passed.

The first run of the two-publication scenario was interrupted by the external
480 s watchdog before the worker started; the plan and the context had already
been saved at about the 430th second. The watchdog of the acceptance subprocess
was raised to 900 s; the product budgets of Gold and the worker were not
changed. This is neither a performance check nor evidence that performance has
not regressed. This verification had no physical Baseline/Gold run and no
Gemini calls.

The next block of the same step separates the task input, the local information
input and Mini's public history. New records use worker context v3, delivery
body v5, envelope/receipt/invocation/completed delivery v2. The links of both
inputs are checked against exact bytes, lengths and digest on delivery and
recovery. Earlier formats are read without changing their bytes; the new
runtime does not continue an old frozen run with a silent replacement of the
contract.

The remaining sequence of work after this block:

1. Wire in ordinary study of the project and search across all experience.
   Retain source materials, hypotheses, successes, failures, partial and
   unknown outcomes, contradictions and outdated information. Retention of
   experience and admission of its application have different conditions; an
   environment error does not prove that the method is unfit.
2. Link the substantive results of replay to the applicability of procedures,
   to operation variants and to their selection.
3. Implement local application of typed information in Mini and the
   authorization of each supported operation before its effect. Define the
   permitted route of public observations; currently an unknown provenance
   stops the dialogue.
4. Wire in subsequent learning, conditional negative knowledge, the useful
   parts of unsuccessful attempts and evidence of influence on the next
   selection.
5. After implementation, verify the whole loop with one external scenario that
   separately observes the outgoing requests at the Mini boundary.

Gemini remains Mini's external model: Mini sends requests and receives
responses. Gold passes local information through Mini's contract and receives
the result of the work under this contract. Separating the inputs removes the
earlier passing of a shared context into `mini -t`; it does not yet confirm
local application of memory or the readiness of the full loop for a physical
comparison.

Local acceptance of the separate-inputs block of 2026-09-08: **853 passed** in
total across the listed targeted runs, Python 3.12 on Linux:

| Checks | Observed result |
|---|---|
| `test_worker_input_contract.py` and three dependency direction / ownership DAG / acceptance boundary checks | 821 passed, 17.37 s |
| Stage 10: context codec, worker subprocess, replay context, delivery influence | 25 passed, 53.60 s |
| `test_mini_input_delivery_acceptance.py`: one task, two different local packages, the real Mini and FormatError continuation | 1 passed, 18.88 s |
| `test_mini_information_boundary_acceptance.py` and the earlier `test_provider_capture_acceptance.py` | 3 passed, 26.64 s |
| `test_task_selection_approval_acceptance.py`: two attempts, one grant, protection against knowledge substitution | 1 passed, 152.27 s |
| `test_run_lifecycle_acceptance.py` and `test_source_consumer_acceptance.py`: feedback recovery and delivery of real publications | 2 passed, 472.96 s |

`scripts/stage9_ownership_dag.py` also finished with no forbidden links. The
new heavy Mini scenarios are in separate acceptance files and separate CI
shards. The product does not import them. The last two checks were rerun after
the execution environment's session was lost; the lost results were not counted
as successful. The Mini acceptance used the installed SDK and a controlled HTTP
endpoint, including a Gemini-compatible format, without paid Gemini calls.

The following are confirmed: delivery of the separate inputs, unchanged
outgoing requests when the local memory changes, refusal to transmit a local
result/derived error, preserved accounting, exact recovery and the links to the
source publications. This result does not claim the full suite, a live
Baseline/Gold comparison or useful local application of memory. The earlier
canonical Unicode scan optimization is kept; this block has no new performance
work.

## Processing time and separate acceptance

The analysis of 2026-09-08 uses the saved data of the passed two-publication
scenario `acceptance/stage4/stage16/test_source_consumer_acceptance.py`. The
measurements reopen the real `run-records/lineage-sources` directory and the
first source `PublicationResult`; no new Gold run, worker or Gemini is started.

One call of `stage14.sources.read_input_graph` under `cProfile` yielded
22 046 911 function calls. The surrogate range check produced about
10.2 million `ord()` calls. In `canonical_values.py` and Gold
`canonicalization.py` it is replaced by a compiled search for the range
U+D800–U+DFFF in plain strings. The UTF-8 check, NFC normalization, canonical
bytes, error types and the shared serializer's earlier handling of string
subclasses are kept. There are no external dependencies; the search uses a
[character range of the standard `re`](https://docs.python.org/3/library/re.html).

The times below were measured separately with `perf_counter`, without the
profiler, on Linux and Python 3.12. Three consecutive reads were made for each
operation; the files were already available in the OS cache. The source code
before the change is commit `f4a629a`. The data and the serialized result of
each read matched before and after the change. The measurements after the
change ran concurrently with targeted tests on a machine with an 8 CPU quota;
this is a local estimate, not an isolated laboratory benchmark.

| Operation | Before: three reads, s | After: three reads, s | Ratio of medians |
| --- | --- | --- | --- |
| `read_input_graph` | 1.120; 1.182; 1.070 | 0.601; 0.495; 0.457 | 2.26 |
| `PublicationResult.payload` | 0.458; 0.449; 0.461 | 0.248; 0.246; 0.266 | 1.84 |

The profile is for finding costs; its time is not used as a benchmark because
it affects Python and C calls differently
([Python documentation](https://docs.python.org/3/library/profile.html)). These
numbers do not prove a speedup of a full Gold run or the absence of other
delays.

The following speedup priorities are not implemented yet:

1. Remove the repeated parsing of unchanged journals within one verified read.
   In the measured call, `FileCompatibilityStore` parses 53 records twice: on
   open and on `resolve_ref`. Reusing the parsed prefix requires checking the
   real bytes, digest, coordinator and chain continuation. On their own, the
   file size, a hash-based name or `mtime` do not replace such a check.
2. Combine the reading of a candidate's evidence in the existing owner:
   `run_knowledge.evidence_for` checks the publication again for the separate
   provenance, lifecycle and taint probes. Parsed unchanged data can be reused
   within a consistent snapshot; current grants, taint and point-of-use
   freshness must be checked at their own boundaries.
3. Add an index of candidates by task targets and applicability conditions
   before the expensive evidence check. A suitable technique comes from
   [SQLite indexed search](https://sqlite.org/queryplanner.html); a change of
   backend is not a required part of the solution. Retention of all experience,
   including failures and unknown outcomes, is preserved. Limiting the search
   selection must not hide matching negative constraints.
4. Parallelize independent reading of candidates only on a consistent snapshot,
   after the redundant work is removed. Publication and changes to shared state
   keep one owner and the current coordinator.

The heavy v1 and v2 approval checks are in
`acceptance/stage4/stage11/test_operator_approval_resume_acceptance.py` and
`acceptance/stage4/stage11/test_task_selection_approval_acceptance.py`
respectively. In `.github/workflows/stage4-gold.yml` they are the separate
matrix jobs `operator-approval-resume` and `task-selection-approval`, available
for
[parallel scheduling in GitHub Actions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations).
The split moves no tests, fixtures or scenario setup into product code;
`AGENTS.md` forbids a dependency of the product on acceptance.

Verification of this change: 920 targeted tests of serialization, Gold
contracts, compiler binding and architecture boundaries passed in 26.16 s. The
checks covered the edge surrogate code points, a surrogate pair, dictionary
keys, a long prefix, valid Unicode scalars and the existing reference
bytes/hash. The two split-out heavy scenarios were collected successfully with
`--collect-only`; their conditions and checks are kept. The full heavy
scenarios were not run locally after the optimization.

## Operator command and inputs

First connect the project with its current authority declaration:

```bash
python -m synapse project connect --repo /experiments/repo --state-dir /experiments/project --declaration /experiments/project-declaration.json
python -m synapse project learn --state-dir /experiments/project --input /experiments/learn.json > /experiments/learning-result.json
```

`learn.json` uses the closed schema `synapse.stage4.gold.source-ingestion/v1`:
only `schema_version`, `claim` and `files`. Each file has a `ref` (an ordinary
`HashBoundRef`) and a `path`. Relative paths are resolved against the input
JSON. The list covers the claim's external inputs exactly; extra, missing and
substituted bytes are rejected. The verifier reads the repository's own files
from Git.

`claim` requires the following fields:

| Field | Content |
| --- | --- |
| `schema_version` | `synapse.stage4.gold.source-claim/v1` |
| `operation_id` | Unique operation name; a repeat with a changed claim is forbidden |
| `revision` | Full SHA of the source Git commit |
| `kind` | `REPOSITORY_FACT_CHECK` or `VERIFICATION_RECIPE` |
| `sources` | Exact permitted paths of ordinary committed files |
| `symbols` | Non-empty list of Python bindings: `path`, `module`, `qualname`, `symbol_kind`, `contract_version` |
| `recipe` | `null` for facts; `command` and `expectation` for a procedure |
| `replay_gas_budget` | Budget of the pure replay, matching the later run; for example 10000 |
| `policy_inputs`, `environment_inputs`, `tool_inputs` | Complete `ObservedExternalInput` items, ordered by name, with their own retained files |

The binding version depends on the project. A project connected without import
roots binds symbols under `synapse.stage4.gold.binding-contract/v1`: the module
is derived from the path from the repository root. For example, the symbol
`src/calc.py`, `src.calc`, `add`, `FUNCTION` denotes an actually existing
function definition. The verifier itself parses the source file and computes
its identity. It is not possible to declare a nonexistent symbol or to attach a
ready-made positive verdict.

**A project with a src layout** declares its import roots in the
`project connect` declaration: `"python_roots": ["src"]`. The roots go into the
project record `synapse.stage4.gold.project-record/v2`, and `project status`
prints them as the line `Python import roots: src`. The symbols of such a
project are bound under `synapse.stage4.gold.binding-contract/v2`:

- **Module name.** A module is named from the longest declared root that
  contains its path: `src/calc.py` → `calc`. Paths outside the declared roots
  are named from the repository root: `tests/test_calc.py` → `tests.test_calc`.
  The order in which the roots are declared does not affect the name.
- **What a binding stores.** A v2 binding stores the chosen root
  (`python_root`, `""` for the repository root) and all declared roots
  (`python_roots`). So a stored binding is checked offline, without the
  project.
- **What a root must be.** A root is a directory of the commit. It cannot be a
  package or lie inside a package (`IMPORT_ROOT_INVALID`).
- **Ambiguous names.** A module name that is derived from two paths is rejected
  (`MODULE_AMBIGUOUS`). Examples are a package next to a module of the same
  name, or one package under two roots.
- **The contract in a claim.** A claim must name its project's contract:
  `"module": "calc"` with `contract_version` v2 in a project with roots. A v1
  symbol in a project with roots and a v2 symbol in a project without roots are
  rejected before the operation is recorded in the journal.
- **Task targets.** Task targets (`task-target-resolution/v2`) record the roots
  and are reproduced under them. So a task that mentions `calc.add` selects
  this symbol.
- **Renaming layouts** are not supported and yield `MODULE_MISMATCH`. These are
  `package_dir` in setuptools, `sources` in hatch and `to` in poetry.
- **Why the roots are declared explicitly.** Roots are not inferred from
  `pyproject.toml`: build tools have different rules, and the identity of a
  binding would depend on a hidden input.

For later use by Gold, the external input profile must contain the current
`compatibility-policy`, `host-abi` of version `synapse.stage4.host-abi/v1`, a
separate environment `runtime-environment` and information about the tools.
These are declarations of the project's concrete dependencies; do not replace
them with fictitious versions or a future oracle. In addition, the verifier
itself records the Python version, the system, the architecture and the
installed versions of Python packages. They are observed again at consumption.
Package versions are not a complete hash of the installed environment.

A procedure specifies the argument array `command`; its first element is the
exact `sys.executable` of the Synapse process. The initial profile supports
procedures of this Python environment. `expectation` requires
`expected_exit_codes`, `expected_nonzero_exit`, a non-empty
`combined_output_contains`, `combined_output_not_contains` and a bounded
`timeout_seconds`. Checking the exit code without an observed result is not
allowed. The project must grant `read` for the sources and `execute` for the
procedure. The command runs in a separate checkout; changes to it are
forbidden, including untracked files. For pytest, use `-B` and
`-p no:cacheprovider`. The checkout is not a sandbox for arbitrary external
actions of the command.

A successful result has `status: PUBLISHED` and a `knowledge` field with the
schema `synapse.stage4.gold.knowledge-input/v2`. Save exactly this field as an
ordinary `knowledge.json` for the `knowledge_path` of a later `project run`.
The export contains references to real publications and retained materials;
they must not be moved separately from the project state. The export creates no
grants by itself. A future task receives its own task contract, oracle
observation and behavior refs of the published records; the original
attestation is not rewritten.

Repeating an already confirmed claim under a new operation name returns
`ALREADY_KNOWN` and the earlier publication. It does not rerun the procedure
and does not grow the library. A failure records `REFUSED`, `REJECTED`,
`INFRA_ERROR` or `INTERRUPTED`. After an uncertain external action, the earlier
operation name does not permit a silent repetition. A deliberate new check
needs new inputs and a new operation, after the earlier interruption has been
analyzed.

## What the worker receives

Knowledge passes through the existing retrieval, compatibility, snapshot and
consumption gates and an actual replay. New source request v3 publications use
a typed target coverage procedure: the counts of required and matched bindings
go into the CVM, and a branch returns the applicability, the match count and
the remainder. Historical v1 source requests are read under the earlier
identity profile. A historical command is not executed again during replay.
The platform checks the actual inputs, the return value and the terminal
snapshot before selecting an operation and delivering material.

The agent, an admitted registry profile, receives one STDIO frame
`synapse.agent.stdio-request/v1` with two neutral projections:

- `task.text` — only `synapse.worker.task-input/v1`: the task statement, the
  revision, scope, capabilities, the kinds of expected/forbidden effects and of
  criteria, and the arguments of explicitly given verification commands.
  Internal references to conditions stay in Gold; listing the kinds does not
  replace a check of their content.
- `local_information.text` — `synapse.worker.local-information-input/v1` with
  its own digest and length. The information includes the original bytes of
  the selected records, replay observations and feedback. A rejected hypothesis
  keeps its status; the `REFERENCE` role does not declare a record to be
  successful experience.
- Full proofs, admission, the selected operations, plan authority and
  controller identifiers stay in the verifiable envelope/audit.

If a profile calls a model (`LOCAL_BROKER`), it must declare the Synapse local
edit protocol; otherwise a call with local information is rejected before the
process starts. The Synapse model broker (`synapse/agents/model_broker.py`)
keeps the provider key to itself; the agent receives only the address and the
capability of its call. While local information is delivered, the broker
passes to the provider only the protocol's public conversation: the
instructions and the public task (`public_messages`), then, for each observed
provider reply, the reply itself and the fixed correction message
`LOCAL_EDIT_CORRECTION`. The request fields are limited to `model` and
`messages`. A changed history, an unknown message or added parameters are
rejected before the external call. A client may omit a field that is already
null in the public reply; it cannot add a field.

The agent returns only the raw text of its model's proposal
(`local_edit_proposal`); Synapse interprets it. C1 performs the repository
effects after the plan and the permission are checked. The operator admits a
process of the `TRUSTED_PROCESS` profile on stored acceptance evidence; these
checks do not give OS isolation to an arbitrarily changed process.

NR-03/NR-05: the new dependencies of `stage10/context.py`, `context_codec.py`
and `worker_transport.py` are limited to the neutral immutable data of
`synapse.worker.input_contract`. This contract does not import Gold, an SDK,
the controller or acceptance, and it grants no permissions. Context building,
admission, planning and recovery keep their existing owners. The extension
points are limited to the neutral agent STDIO protocol and the Synapse model
broker.

An additional NR-03/NR-05 boundary: the owner `stage10/influence.py` reads the
neutral `synapse.worker.input_contract` and `synapse.worker.local_edits`.
Their dependencies have been checked: this is a bounded pure data interpreter
without an agent, an SDK, file effects, command runs or Gold imports. It
reinterprets the retained local proposal to observe how the selected edit
depends on the information. This path does not call a model, does not
authorize operations and does not establish that the result is correct; that
remains the duty of C1 and of independent verification. The import permission
is limited to this owner.

The full physical chain includes the original operation, its publication, the
consumer's snapshot, the replay and the delivered context. Loss of the
original bytes breaks reconstruction. Delivery does not count as evidence of
useful application and does not create a `MechanismUseRecord` on the model's
behalf. The earlier evidence of a prevented repeat of a rejected patch keeps
its own semantics.

For the live Astropy scenario, the external command `prepare-astropy-pair`
checks the real environment of the worker agent and calls exactly this
product process. It stores the full receipts and the time of each learning
call separately from solving the task. The internal `elapsed_ns` of the
`learn` result covers only the verifier and does not include publication. The
order of preparing, running and reading the results is given in the
[stand instructions](../acceptance/stage4/stage16/README.md).

The existing compatibility owner compares two verified records. Different
historical facts without joint effects can coexist; mismatched sources and
competing expectations of one command require a refusal/review. An answer of
"no conflict" given by the operator does not replace this check.

## Provenance, atomicity and recovery

The first root of the lineage is a stored source, for example a specific
version of a file in a Git commit. Then come the actual extraction operation,
its verification, the admission decision and publication. The operation has
its own identity; it is not passed off as an attempt to solve a user task.

The initial ingestion profile has a closed versioned schema and its own
required set of graph roles. It does not weaken the required roles of the
existing Gold graph. Reconstruction reopens the physical materials and checks
the links; one stored JSON graph does not replace the evidence.

Publication, admission, attestation, lifecycle, index visibility and retention
use a shared coordinator. Reopening a completed operation returns its earlier
publication without new effects. An unfinished record goes through the
existing recovery; an external action with an unknown result cannot be
silently retried or marked successful. Old task results are not rewritten.

Loss of a retained source, a hash mismatch, a profile change or an incomplete
provenance chain blocks a new confirmation. A historical receipt remains a
historical receipt. Taint does not disappear when knowledge is paraphrased,
hashed, successfully executed or republished.

A missing field, `null`, emptiness, `false`, `0` and an unknown value keep
distinct semantics. Internal closed contracts forbid unknown fields.

## Step 2 of the map: stored study experience

The first block of step 2 implements storing and selecting
**source-operation experience**. `project learn` stores the claim before the
effects, then the physical bytes of inputs and sources, the resolved bindings,
the start of the command, its result and the subsequent checks. Checkpoints
use the existing `source-operations/<sha256(operation_id)>`, snapshot
transactions and the operation's fence. Inputs and observations are linked in
a chain; the completion is linked to its head. Loss of committed bytes or of
part of a completed history causes a read refusal.

The resolved symbol and the command's stdout are stored even if a later
assertion is false or cleaning the working directory ended in an error. If
there is no committed result after the command started, the outcome stays
`STARTED_UNKNOWN`; a repeated `learn` does not run the command again. A
repeated read does not depend on the operator's original external files. Old
records are read without being rewritten; bytes that were not stored before
are not invented from them. If the bytes read for an input do not match the
declared identity, both references and the actual bytes are stored, and
verification/execution does not start.

An ordinary study of files uses `source-ingestion/v2` with `source-claim/v2`:
`kind: SOURCE_STUDY`, `symbols: []`, `recipe: null`. The other fields of the
v1 declaration, including the exact commit, the permitted `sources`, observed
dependencies and budgets, are required. The system derives the supported
Python bindings through the existing `bindings.py`. Documents, invalid Python
and ambiguous definitions are stored with the reason why they have no
interpretation. Code and commands from the text are not executed. A `RETAINED`
result does not create a publication. A detected span of a symbol does not
prove its runtime effects.

The read command:

```bash
python -m synapse project recall --state-dir /path/to/state --input /path/to/query.json
```

A closed query gives the task and the scope, not a list of memory records:

```json
{
  "schema_version": "synapse.stage4.gold.source-recall-query/v1",
  "statement": "Check the area calculation of Shape.area",
  "revision": "EXACT_GIT_COMMIT",
  "scope": ["src/shape.py"],
  "limit": 8
}
```

The `exact-revision-path-and-term-overlap/v1` profile selects operations of
the same revision with overlapping paths. Ranking takes into account the
number of matching paths, then the overlap of task terms with the source
files, symbol names and command output; a tie is broken by the operation's
identity. A positive outcome is neither a selection condition nor an extra
score. The response keeps the context of the whole selected operation,
including its dependencies and materials; the matched paths and terms are
listed separately. The scope is limited by the project's entitlements. A
result beyond the limit is counted explicitly in `omitted_by_limit`, and an
absence of matches is `NO_RELEVANT_EXPERIENCE`. The response budget is 16 MiB:
exceeding it causes a refusal that suggests narrowing the query, not a
truncation of the evidence.

v1 has a flaw: a large file contains almost every word of the task. So
operations over one file score the same, and their order is decided
alphabetically by their identity. The operation needed can end up beyond the
limit.

A `synapse.stage4.gold.source-recall-query/v2` query names the profile
explicitly, in the `"profile"` field. The profile
`exact-revision-path-and-tiered-term-overlap/v2` (response
`synapse.stage4.gold.source-recall/v2`) compares integers in order:

1. matching paths;
2. task terms in the operation's own identity: in its paths and the names of
   its bound symbols;
3. task terms in the output of its command;
4. task terms in the full text of the source files.

The operation's identity breaks the remaining ties. So an operation whose
symbol the task names comes before operations that matched only on the file
text. Command output is a separate tier: long test output would by itself
reproduce the large-file effect. In a v2 response, `match.terms` splits the
terms by tier (`identity`, `output`, `source`), and `match.tie_group` shows
how many operations were ordered by their identity alone.

The profile has no fractional scores: `log` values differ between the math
libraries of platforms, and the result is canonicalized and hashed. v1 queries
and the delivery of experience into a Gold run stay on profile v1: changing
what a run sees in the middle of an experiment would confound the
Baseline/Gold comparison.

Measurement (`reports/source_recall_profile_v1.json`, script
`acceptance/stage4/stage16/source_recall_profile_measure.py`): 36 labeled
queries against 36 operations learned on pallets/flask (root `src`) and
Synapse; ranks are computed with ties taken into account (within a group of
equal keys every order is equally likely). MRR 0.566 → 0.818, Success@1 0.30
→ 0.68, Success@3 0.87 → 0.98; 25 queries better, 7 the same, 4 worse.
Queries that name the needed symbol gain the most (MRR 0.50 → 0.98, not a
single loss), queries without names gain moderately (0.61 → 0.75). Queries
that name another studied symbol show no significant difference (0.60 → 0.64;
4 better, 3 worse): v2 puts the named symbol first even when the defect is in
a neighboring one. The set is small and was compiled by the profile's author;
the command output tier took no part in the measurement (fact checks have no
command) and is checked by an acceptance test.

Execution and verification have different statuses. `EXITED_NONZERO` together
with `VERIFIED` is possible if the contract declared in advance required
exactly that result. An environment failure stays `INFRA_ERROR`. Every record
found has `applicability: UNASSESSED`: a match of paths, words or exit code
neither proves the applicability of a method nor forbids it under all
conditions. A read opens the committed prefix of each operation under its
fence; this is not yet a shared atomic snapshot of the project for Gold
execution.

NR-03/NR-05: `source_experience.py` is the assembly that reads the journal and
the published evidence. The exact dependencies on `source_ingestion.py` and
`knowledge_environment.py` are entered in the assembly DAG. The owners of
journaling, verification, Python binding and publication are preserved. No
second mutable store, second publisher or executing agent has been added. The
separation of material, observed activity and result rests on the concepts of
[W3C PROV-DM](https://www.w3.org/TR/prov-dm/); this profile does not claim a
full implementation of the standard.

**The rest of step 2:** a general model of elements and current/target/delta,
typing of the other kinds of experience, extraction of substantive claims from
documents and automatic selection of the whole suitable corpus in a frozen
Gold run. The new `recall` is connected to the read CLI; its result is not yet
passed to Mini and takes no part in replay/the plan. The next block has to
route it through the existing snapshot, context and lineage, with separate
semantics for non-admitted materials. Studying a whole repository without a
given scope and carrying conclusions over between revisions are not claimed.
The whole Gold loop has not been accepted yet.

The heavy scenarios `test_source_experience_acceptance.py`,
`test_source_study_acceptance.py`, `test_source_experience_recovery_acceptance.py`
and `test_source_experience_publication_acceptance.py` have separate CI shards.
They check real CLI processes and commands, different outcomes, partial
results, interruptions, history corruption and search. Product execution does
not depend on acceptance scenarios or fixtures.

Verification of this block on 2026-09-09: 14 new source scenarios, 13 existing
ingestion/refusal/recovery/retention/consumer scenarios and 329 binding and
dependency direction checks — 356 unique targeted checks passed. The old
consumer confirmed the path publication → replay → separate worker delivery
and the physical lineage; the new scenarios separately confirmed the reading
of experience. The assembly DAG contains no forbidden edges. A full baseline,
live Gemini and the use of new materials inside Mini were not checked; there
is no result of the full Gold loop here.

## Acceptance of the implementation

Acceptance scenarios check the product path and do not create permissions,
permissive probes, a ready-made history or a test implementation of the
verifier for it. Heavy scenarios are placed separately for an independent run
in CI.

- A fresh project receives real verified knowledge through the canonical CLI;
  a later process opens the same stores and can use this knowledge.
- A false claim, an unknown profile, a changed source and an attempt at
  self-admission create no searchable publication.
- Executing a procedure checks the declared effect, stores the observations
  and distinguishes refusal, infrastructure error, interruption and
  confirmation.
- A failure at the publication boundaries leaves no visible knowledge without
  a complete commit; recovery creates no duplicate or new authority.
- The deletion of a physical source is detected by reconstruction. The new
  origin does not allow accepting an incomplete graph of an existing Gold
  attempt.
- A real consumer passes the current gates, snapshot and replay; incompatible
  conditions of use are rejected.
- Preparation costs are stored separately from the costs of solving the task.
  Missing measurements stay unknown.

## Use in the Astropy experiment

Initial ingestion uses only the permitted original version of the repository.
The SWE-bench reference fix, the evaluator test patch, calibration results and
the answers of earlier experimental replicates do not get into the sources or
the worker's context. The prepared corpus is frozen before the comparison;
each Gold replicate receives the same initial state in independent stores.

The report shows preparation and solving costs separately. A conclusion about
savings must account for the preparation and the actual number of reuses. The
result of one pair does not prove a causal benefit of memory. The differences
between the Baseline/Gold policies stay explicit; no additional control Gold
is introduced.

The oracle environment and the environment of worker commands are checked
separately: a successful calibration of the Docker oracle does not prove that
the Mini-worker environment is ready.

## Rationale for design decisions

- [PREPING](https://arxiv.org/html/2605.13880v1): limited practice in the real
  environment before the first task, coverage management and memory
  selection. Its LLM assessment of admission does not replace the independent
  executable verification of Synapse.
- [LangMem](https://langchain-ai.github.io/langmem/guides/extract_semantic_memories/):
  candidate extraction can be separated from store mutation. This process does
  not need a new LangMem dependency.
- [SLSA provenance](https://slsa.dev/spec/v1.2/build-provenance) and
  [in-toto](https://github.com/in-toto/in-toto): explicit materials,
  operations, executors and results. The lenient SLSA rules on missing data
  and on dependency completeness are not carried over into the internal
  contracts of Synapse.
- [W3C PROV](https://www.w3.org/TR/prov-dm/): provenance starts from a source
  and an activity; an earlier successful user task is not required.

Implementation limitations: an exact version of the repository and of the
Python environment; Python bindings; one controlled file edit in the current
canonical Gold run. Automatic study of an arbitrary repository by a model,
carrying knowledge over between changed versions, server-side execution and
learning from any successful tasks are not implemented by this profile. These
limitations must not be hidden behind the result of a single test.

## Recording experience for the canonical Gold run: stage history

This section and its subsections keep the results of intermediate stages
preceding the current rework of PR 108. Statements about unfinished work
describe the state of the corresponding stage. The profiles in force, the
limitations and the new results are listed at the start of the instruction;
earlier unsuccessful experiments are not redeclared successful.

`knowledge_path` in the `project run` declaration may point to the new profile:

```json
{
  "schema_version": "synapse.stage4.gold.knowledge-input/v3",
  "experience_limit": 64,
  "files": []
}
```

`files` holds the observations and evidence the task needs, in the earlier
format `{ "ref": ..., "path": ... }`. In this profile executable candidates are
taken from the project's real source publications; the operator does not list
them in the task. Their later admission, selection and replay are performed by
the same owners as before. The knowledge-input/v1 and v2 profiles keep their
earlier semantics.

Before execution, `frozen-input/v4` records consistent prefixes of the source
operation journal, the existing publications and the selection made under the
task contract. The record is taken under the inventory, operation and project
coordinators, in that order. Reopening checks the original physical records and
reproduces the selection without running commands. Later learning is not added
to a run that has already started; the loss of a record of the original prefix
makes its evidence invalid.

The worker receives the selected experience through a separate local input. It
holds the materials, structural symbols, the declared conditions of the command
and its observed result. Gold refs, publication permissions and the audit
journal stay on the Gold side. The `UNASSESSED` and `STARTED_UNKNOWN` statuses
and the absence of an observation are kept explicitly. A nonzero exit code of a
command does not create a ban on the method. The contract of the task itself
does not change with the content of this selection.

This transition uses context-record/v4 and delivery-body/v6; the neutral
two-input worker contract stays as it was. Lineage-sources/v5 binds the
physical `experiment.json`, the original journal and the delivered projection;
reconstruction checks that they match the run manifest and the task contract.

This connects retained experience to delivery. It does not implement an
interpreter of local materials for Mini, does not prove a useful application of
experience and does not permit a run with an empty library of admitted
executable behaviors. Mini still reports `local_interpretation: NOT_PERFORMED`.
The next transition is a verifiable local application to execution variants
and learning from the actual result, without passing memory to the provider.

The acceptance check of this transition: 361 unique targeted checks passed in
separate groups. The new scenarios check real source → frozen run → replay →
local worker input → physical lineage, an unknown outcome after the effect of a
command, an unchanged selection after later learning and a refusal when the
original record is lost. The codec check confirms that the task is unchanged
and that a downgrade of the context version that loses the source is refused.
The assembly DAG checks found no forbidden dependencies. The end-to-end worker
of this scenario only records the inputs it received and returns no candidate:
this is not a live Gemini result, not a check of local interpretation and not a
successful solution of the task.


### Live check of the Mini connection and incomplete usage after a retry

In GitHub Actions run `34296776480` Mini completed both steps, but between them
Gemini returned HTTP 503 in the form `[{"error": {"code": 503, ...}}]`. The
SDK's standard retry succeeded. Such a response carries no usage measurement:
`FAILED`, `UNAVAILABLE` and unknown tokens are kept for it. The HTTP status, the
Gemini profile and the shape of the error envelope are checked separately; an
unknown shape of a successful response and corrupted usage fields still yield
`SOURCE_INCONSISTENT`.

Connection acceptance requires retained physical requests and responses, a
successful continuation of every logical call and a full match of the known
measurements. The only permitted missing measurement is the usage of a retained
failed request after which a successful retry of the same logical call is
recorded. The absence of the call records themselves, corruption of the source
files and a divergence of known counters remain a failure.
`connection-capture.json` records the result of this check separately from the
original `reconciliation.json`.

Overall accounting with unknown usage stays `MISSING_CALL`; it cannot be
declared `COMPLETE` or used as full evidence of savings. The original failed CI
artifact is not rewritten. There are no new retry loops on top of the SDK. Such
transient errors and bounded retry are described in the
[Gemini API guide](https://ai.google.dev/gemini-api/docs/troubleshooting).

### Conditions of a failed attempt and a typed replay result

After a command has finished, the source verifier measures the environment
again on `PASS` as well as on `FAIL`, a timeout or a changed worktree. A
`source-observation/v2` observation keeps a reference to the actual
remeasurement; the historical reader compares both retained byte sequences. A
declaration of `unchanged` without the corresponding measurements does not
become new evidence. Old v1 observations are read under the earlier contract.

`source-execution-result/v1` distinguishes a violation of the command's
expectations, a worktree change, a change of the measured environment, a
timeout and termination by a signal. An environment change yields
`INFRA_ERROR`; a timeout and a signal yield `INTERRUPTED`, even if a negative
return code was recorded among the expected ones. A failed result is checked
against the original observations on recall. Source files, the bindings found,
stdout/stderr and useful intermediate observations are kept whatever the
outcome. These statuses describe one specific attempt; they create no general
ban on the method. The environment measurement still covers the runtime
metadata listed above and does not prove that all installed files are
unchanged.

### Correcting the boundary of the acceptance sample

In local commit `5054fe2` the acceptance stub `source_procedure.py` was
mistakenly wired into the product path of publication, compatibility and
delivery. It built a program that returned literal descriptions of operations.
Checking such a return is not execution of a procedure and does not close the
requirement of substantive replay in the general Gold model. Adding the stub to
the product ownership map to pass the architecture check was wrong.

After the user's clarification the stub was moved to
`acceptance/stage4/stage13/source_procedure.py`. The only remaining check,
`test_source_procedure_sample.py`, checks its literal sample through the real
CVM. The product does not import it. The product generator of the nested
literal certificate, the automatic publication of
`source-publication-request/v2` and the related `source-procedure/v1` routes
were removed.

Canonical learning again publishes `source-publication-request/v1`: verified
source knowledge and its provenance. Replay of this profile returns a knowledge
identity of five integers. Its result is compared by canonical bytes so that,
for example, a boolean does not replace an integer. Lineage keeps the matching
by the pair "admitted behavior, content reference" and the physical check of
the original publication. This is delivery of verified knowledge after a check
of its identity, not procedural execution and not a full Gold PASS.

The existing bytes of the test v2 artifacts are not deleted and not rewritten
into v1. The v2 prototype is no longer a supported product profile; its old
acceptance results are not used as evidence of product readiness. A later check
found a regression: the refusal of this profile blocked opening the project and
all of `recall`. The owner of `PublicationResult` now distinguishes reading a
supported result through `payload()` from reading retained history through
`retained_payload()`. The second path admits only an explicitly listed retired
profile and checks the original commit, the full physical composition of the
record, the journals, the write set, the lineage references and independent
evidence of the original facts. It does not restore support for the old
behavior or its generator.

A rerun of `learn`, recovery, recall and the experience snapshot use the
retained history; the old command is not executed again. The export of
executable knowledge excludes retired profiles. The ordinary reader for
admission/replay and the new publisher reject them. A historical `PUBLISHED`
remains a fact of the old record, not a new permission to execute. Corruption
of the original material or of lineage still blocks reading; skipping corrupted
history is not allowed.
Support for full procedural replay remains an open task.

The previous package's real environment measurements after failed commands,
the distinction between `INFRA_ERROR` and `INTERRUPTED`, the check of retained
observations on recall and the full retention of the original experience are
kept. A rerun of `learn` for a completed publication returns the full retained
result, including `operation_id`, `verification_ref` and the actually measured
`elapsed_ns`. Recovery between the publication commit and the result checkpoint
does not invent the missing time.

### Bounded profile of local text proposals for Mini

For a new run, the `worker` object of the declaration may explicitly state:

```json
"input_profile": "mini-2.4.6-local-edit-proposals/v1"
```

This profile requires separate task and local information inputs, an installed
Mini 2.4.6 and the existing accounting of its physical requests. Old
declarations without the field keep the earlier profile
`mini-2.4.6-task-and-local-information/v1`. The profile name is part of the
frozen declaration and the adapter's source files are part of the runtime
fingerprint; substituting the profile in a retained run does not obtain the
earlier identity or approval.

Mini receives the model's proposal through its ordinary SDK. A fixed public
protocol permits one typed message with at most eight alternatives of text
replacements. Each alternative holds up to sixteen replacements, one per file.
The model sees only the public task and the fixed description of the protocol.
Mini's local handler matches the proposals against the source materials from
the separate information input: the same revision, a permitted modifiable path,
unambiguous source bytes and a single match of the old text. On a mismatch or
contradictory source files the reason for inapplicability is kept. Arbitrary
prose is not interpreted.

Source bytes are acceptable as proposal material even after failed, interrupted
or unknown learning; its status does not become a ban on the method. An exact
`EXECUTION_OBSERVATION` that the caller forms from an independent check of the
current task allows excluding the same `evaluated_patch_sha256` with
`oracle_resolved: false`. An unknown outcome and a recipe failure do not give
this right. Contradictory results for one patch hash are kept as a conflict and
do not create such an exclusion. This is a narrow use of exact negative
feedback; extracting general conditions of a method's inapplicability is not
implemented yet.

Every alternative passed is evaluated; the first applicable one in the order of
the public proposal is selected. The original proposals, the reasons, the
selected index, the hash of the inputs and of the original and resulting bytes
and the hash of the patches are kept. This is a local choice in Mini, not an
implementation of the missing generator of Gold plan graphs. The full model
dialog does not become an input of the Gold planner.

Mini returns an **unverified patch** and leaves the worktree unchanged. This
profile has no shell fallback: an unsupported request is rejected before the
environment is called. The loop ends after the local evaluation; neither the
material, nor the patch, nor the local results and errors are added to the next
model request. The existing C1 receives the patch through the ordinary worker
contract, applies it in its controlled path and runs fresh checks. Neither the
local evaluation nor the worker's message declares the task solved or publishes
knowledge.

Bounded UTF-8 text files with LF and a trailing newline, portable ASCII paths
without spaces and a size of up to 1 MiB per source file are supported. The
proposal and the final patch are limited to 256 KiB. File creation/deletion,
binary edits, arbitrary commands and general local text analysis are not
supported by this profile. The standard extension point
[Mini execute_actions](https://mini-swe-agent.com/latest/advanced/cookbook/)
is used, without replacing the CLI, the SDK, the Gold controller or C1.

The `old` and `new` strings are kept literally. They and the hash of the
alternatives use deterministic JSON without Unicode normalization: NFC and NFD
can denote different program bytes. This fixes a silent replacement of text by
the general canonicalization, found during an audit. The check goes through the
real Mini, the retained trajectory and `git apply`, after which the exact bytes
of the modified file are compared.

The audit found the same loss of text in Gold's own retention of the worker
result. If the result contains strings that are not in NFC,
`completed-worker-delivery/v3` keeps the whole result as exact UTF-8 JSON in
base64url inside a canonical outer record. This covers the patch, the report
and the diagnostic fields. The reader checks the exact format, the reference,
the original delivery identities and the byte-for-byte restoration. For results
whose text is not changed by normalization, the earlier v1/v2 and their
identities are kept. Old records are not rewritten; differences that the
earlier codec already lost cannot be restored from them.
`stage11/test_worker_result_retention_acceptance.py` checks a real
`WORKER_COMPLETED` checkpoint and reading in a separate process. The worker of
this scenario is an acceptance subprocess; its check does not declare all of
Gold successful.

Acceptance of this profile lives only in `acceptance/`:

- `stage10/test_local_edit_contract.py`: applicability conditions, exact
  negative observations, unknown/contradictory outcomes, task and transport
  constraints.
- `stage15/test_mini_local_edit_acceptance.py`: the real Mini process, SDK and
  observed HTTP. The same external request when local memory changes; getting
  the patch locally; refusal of a shell request before any effect.
- `stage15/test_mini_local_edit_c1_acceptance.py`: actual Mini → existing C1 →
  independent executable oracle. The oracle really rejects the first edit, its
  exact negative feedback changes the choice of the next edit, and after that a
  new C1/oracle confirms the result.

In the two heavy scenarios only the response of the test external endpoint is
controlled; the real Gemini is not called in them. They have separate CI shards.
This is a check of the Mini and C1 boundaries, not a single run of full Gold:
plan generation from replay, publication of successful methods, later
autonomous retrieval and full influence/lineage still require implementation
and separate overall evidence.

Targeted checks of the package that actually completed, Python 3.12 / Linux:

| Check | Result |
|---|---|
| Mini adapter and local proposal conditions | 46 passed, 2.37 s |
| Real Mini, separate memory, refusal of shell requests | 4 passed, 36.99 s |
| Real Mini → C1 → independent oracle, then exact negative feedback and a new edit | 1 passed, 18.13 s |
| Earlier Mini profile: delivery and blocking of a private observation on the next request | 2 passed, 22.86 s |
| Source publication, command outcomes, runtime drift and experience recovery after the prototype's removal | 20 passed, 79.54 s |
| Module ownership check and ownership DAG after the sample's removal from the product | 130 passed, 39 deselected, 4.73 s |
| Two real v1 source publications → identity replay → separate recorder input → physical lineage | 1 passed, 321.80 s |

The last row uses the acceptance recorder worker and ends with `NO_CANDIDATE`;
it does not stand in for the separate Mini → C1 evidence and does not count as
full Gold. The table's results are targeted local checks, not a full repository
baseline and not the result of a new GitHub Actions run.

### Recheck after the sample's removal

The blocking of history by a retired source profile and a silent Unicode change
in two places, Mini and the Gold result checkpoint, were reproduced and fixed.
Targeted acceptance of the fixes on Python 3.12 / Linux:

| Check | Result |
|---|---|
| Owner boundaries and dependencies | 191 passed |
| Local proposals and real Mini → exact bytes after `git apply` | 32 + 1 passed |
| Ordinary source publication and experience snapshot | 5 passed |
| History of the retired profile, ban on use and corruption of the originals | 3 passed |
| Publication recovery and check of the exact composition of records | 3 + 2 passed |
| Checkpoint v2/v3 in a separate process and C1 continuation without rerunning the worker | 2 + 1 passed |

In addition, the old writer from the archive of commit `5054fe2` created a real
v2 project. The new reader opened the project and returned the experience and
the earlier `learn` result, keeping all 79 state files byte for byte. Zero
executable candidates were obtained from v2. After a separate new publication
in the same project, recall returned both experiences and the export returned
only the supported v1 knowledge. The sample and its generator were not brought
back into the product. These checks are not a full Gold PASS; the overall
scenario of planning, procedural replay and publication of a useful method is
not closed yet.

### Automatic targets of an ordinary task

The new declaration `synapse.stage4.gold.experiment-input/v3` accepts
`synapse.stage4.gold.governing-task/v3`. The task has no `target_bindings` and
`behavior_refs`, and the declaration has no `target_records`. The fields cannot
be passed even when empty. The user sets the goal, the base commit, the scope,
the permissions, the expected effects and the verification criteria. For
knowledge, the input already in force is used:

```json
{
  "schema_version": "synapse.stage4.gold.knowledge-input/v3",
  "files": [],
  "experience_limit": 64
}
```

`files` holds, when needed, verifiable source evidence of the environment and
of the task statement; it is not a manual list of selected memory records. The
declaration must still supply the evidence that `observation` references. The
commands remain canonical:

```bash
python -m synapse project run --state-dir /absolute/project-state --input /absolute/task.json --run-dir /absolute/gold-run
python -m synapse project resume --run-dir /absolute/gold-run
```

Before the plan is agreed, Gold reads the exact Git commit through the existing
resolver and stores `target_resolution` in `experiment.json` of version
`frozen-input/v5`. The record holds the elements found, the selection reasons,
a reference to the immutable task and the base revision. The module of the
expected modified path and the symbols mentioned exactly in the text are
selected. The other symbols found are kept in the explanation with an empty
list of selection reasons. The resolver does not import or execute the
project's code; the state of the working directory does not stand in for the
commit.

The result takes part in ranking, intent, the authority check and recovery. The
independent authority matches it against the source code again. A substitution
of the list of elements, the reasons, the references or the task is rejected.
Approval V3 binds the targets and the earlier rule of independent admission of
knowledge: an ordinary completion of retrieval does not require a second
request. The original bytes of the task and of the earlier records are kept.

The current execution profile supports one exact modification of an existing
Python file. The resolver itself is limited to 32 paths and 1024 elements; the
existing Python resolver's limits on syntax, source file size and the number of
declarations are kept. Exceeding the limits ends in a refusal, without silent
truncation. Analysis of arbitrary languages, a call graph, all eight kinds of
memory and the lifecycle of persistent agents are not implemented by this
change.

For V3 tasks a semantic fingerprint of the actual plan is introduced:
operations, input data, arguments, dependencies, scope and verification
conditions. A change of proposal or snapshot identifiers is not passed off as a
new method. Stage 12 compares the fingerprint with the accepted plan when it
reads the result. Old tasks keep the earlier algorithm. Generating variants
from substantive replay remains a separate unfinished item; the single EDIT
template is not renamed to a procedural planner.

The new acceptance lives only in `acceptance/`. The contract checks are kept
apart from the heavy CLI scenarios `test_automatic_task_targets_acceptance.py`
and `test_automatic_task_mini_acceptance.py`; each of them is assigned its own
CI shard. The first scenario uses the recorder and checks the immutability of
the task, target resolution, real replay, separate delivery and resume. It ends
with `NO_CANDIDATE` and does not count as evidence that the task was fixed.
The second uses the installed Mini, a controlled external model response, the
existing C1 and a separate executable acceptance oracle: the oracle applies the
patch it received in a fresh checkout and obtains its verdict from running the
checks. There are no predefined verdicts. This is an acceptance oracle through
the existing report format, not an official SWE-bench run. The result of such
a scenario does not close procedural planning, the publication of successful
methods and full Gold according to the map.

Actual targeted checks of this package, Python 3.12 / Linux:

| Check | Result |
|---|---|
| Task contracts, target resolution, authority and operation semantics | 30 passed, 3.19 s |
| Module ownership, dependencies, acceptance boundary and integrity of CLI inputs | 662 passed, 26.49 s |
| V3 task without manual bindings → replay → recorder → resume | 1 passed, 174.18 s |
| Historical declaration: two attempts and continuation | 1 passed, 206.75 s |
| V3 task → real Mini → C1 → executable acceptance oracle → resume | 1 passed, 194.65 s |

In the last scenario the base commit really fails a separate matrix of checks
and the fixed one passes it; the final record has `FULL` for this task. The
oracle receives a diff that C1 built from the verified pair of commits. Its
text hash can differ from that of Mini's proposal because of Git metadata; the
link is confirmed by an exact match of the final bytes of the file. The
proposal itself keeps its hash. A repeated resume does not rerun Mini or the
oracle. These local results are not a new GitHub Actions baseline or the
completion of the whole map.

### Typed replay and experience from completed tasks

The `typed-pure-replay/v1` profile binds an immutable invocation to the exact
behavior, input contract and output contract. REQUIRED, DEFAULTED, an absent
value, NULL and UNKNOWN are distinct. A Boolean does not pass as an INTEGER.
The initial snapshot must contain exactly the bound inputs; a continuation
checks the retained chain of previous transitions and the output contract. This
profile does not accept arbitrary host calls or external conditions without a
supported checking mechanism. For now the contract contains a fixed certified
trace: it is not a general certificate of all branches of the procedure.

`typed-pure-replay/v2` uses the same CVM and the same replay owner, but matches
the input-dependent trace against the real reference capture of this
invocation. A fixed trace, external activities and observations declared in
advance are forbidden in this profile. `REPLAY_IDENTICAL` is not established
until the retained initial inputs, the ordered trace and the final state match.
Completion and the type of the returned value are checked separately. This is
reproducibility of the computation; an independent contract of task correctness
remains mandatory.

The negative profile `rejected-patch-guard/v4` compares, in the CVM, the words
of the current task/base with the domain of the original evidence. On a
mismatch the pure computation returns an empty result; it does not extend the
failure to another task. Applying the ban additionally requires the exact
patch, the C1 policy, the oracle configuration, the environment and an actual
admitted replay. A refusal, an environment failure and an unknown outcome do
not turn into a confirmed failure of the method.

A new run automatically includes suitable results of completed tasks in
`source-experience-snapshot/v2`. The snapshot records the original publication
transactions. Reading an old snapshot again does not add later results. A retry
keeps the original set of candidates but reads the current lifecycle/taint
descriptors; a stale descriptor from the first attempt no longer stands in for
the current state at the next admission.

The planner receives verified observations through `execution_feedback`.
Stage 12 checks them again against the previous result, or against the original
publication and a real replay. Lineage includes this publication and
observation even when they come from another run. This is not yet a generator
of arbitrary graphs: the current execution plan is still limited to the
supported EDIT profile.

`stage10/influence.py` now takes part in execution, evidence retention,
independent verification and lineage. It interprets Mini's real separate inputs
and the same public proposal again. It then excludes the information roles one
at a time and checks whether the selected edit changes. A change of only the
ID, the alternative number or metadata does not count as influence. The
evidence concerns the dependence of the local selection on the information; the
correctness of the edit is established by the separate C1/oracle. Without a
local result only the proven delivery level is retained.

For V3 tasks with an independently established `FULL`, the
`publication-request/v4` publication, the `stage13-atomic-publication/v3`
policy and the `reusable-candidate/v3` registration are added. The
`verified-patch-guard/v1` profile asserts only the verified success of the
exact patch in the stated domain. Historical negative profiles and earlier
refusals keep their earlier semantics. A positive record cannot suppress C1
through the rejected-patch ban mechanism.

In Mini the new explicit profile `mini-2.4.6-local-edit-proposals/v2` prefers
an applicable alternative with an uncontradicted verified positive observation.
Without such an observation the order of the proposals is kept. Profile v1
keeps the old rule of the first applicable alternative. Both profiles need the
source materials: a past success does not cancel the checks of the current
task's text, scope and constraints. No information from this local assessment
is passed to the model. For v2/v3 an earlier positive oracle answer becomes a
preference signal only with a complete independent `FULL` of the previous
attempt. An incomplete execution stays in the history but is not given to this
port as a verified solution of the task.

The `mini-2.4.6-local-edit-proposals/v3` profile adds a local proposal of a
retained verified patch. A new positive publication binds its real bytes from
C1 to the behavior's `artifact_refs`. The publication reader checks their
physical presence, length and digest; the ordinary compatibility, taint,
admission and replay continue to determine whether use is permitted. A positive
historical record without such an artifact stays readable but cannot by itself
give Mini the missing bytes of the edit.

Mini receives the patch through its information input. Its pure local handler
checks the diff format, the exact original lines, the positions and sizes of
each hunk, the scope and an applicable independently verified positive
observation. It builds the proposal without file effects and requires the
resulting bytes to match the retained patch. Binary files, renames, fuzzy
positioning and other unsupported formats are refused. C1 then applies the
selected alternative again and verifies it independently.

The search is limited to 128 retained patches and eight candidates: first the
applicable local alternatives in a stable digest order, then the remaining
public proposals. The result retains the model's original proposal, the actual
local candidates, their provenance and the omissions due to limits. Different
independent confirmations of identical bytes keep different IDs of delivered
knowledge; Mini merges them into one patch alternative by digest. This is a
narrow method for reapplying an exact edit to the same task and original
revision; it is not presented as transferring a solution to another project or
as a generator of arbitrary procedural graphs. The correct edit may be absent
from the model's public proposal. Neither its content nor the local
interpretation results become messages to the model.

The canonical V3 run now accepts several explicitly named Python files to
change within the target selection budget. The scope must match their set
exactly. C1 applies one coherent patch; Stage 12 checks every expected effect.
A positive oracle answer with a missed target does not mean `FULL`. Historical
V1/V2 tasks keep the single-target contract.

A V3 task can explicitly include verification steps in the actual plan: for
this, `required_capabilities` contains `verification.run`, and the
`VERIFICATION_COMMAND` criteria list the exact argv of all
`acceptance_commands`, then of all `full_suite_commands` from the frozen C1
policy. Each criterion has its own ID and refers to the same
`command_policy_ref`. A missing command, a different argv or a different order
is rejected before execution. Ordinary `CONTRACT_CONDITION` criteria continue
to bind the effect of the change to C1.

The plan contains one atomic change operation and a `RUN_VERIFICATION_COMMAND`
sequence that depends on it. Independent acceptance of the plan, the operator's
prior approval and the semantic digest take these operations, arguments and
links into account. C1 remains the executor. Stage 12 reads the physically
retained C1 report and checks the separate execution of each command after the
patch is applied. Identical argv in acceptance and in the full suite are
different executions. A successful baseline, a skipped step and a broken order
do not substitute for this evidence; resume does not run the commands again.
Without command criteria a task keeps its previous plan. This is the connection
of real verification operations, not the completion of the search for arbitrary
procedural alternatives from memory.

Contracts of this extension and the related authority checks: 385 passed,
17.47 s; the separate initial package of plans and target resolution:
23 passed, 5.38 s. The full acceptance with real Mini/C1, two files and
recovery in
`acceptance/stage4/stage16/test_verification_plan_mini_acceptance.py`:
1 passed, 468.02 s. Only the external model answer is controlled; the
independent oracle really executes the original and the fixed code. The
historical single-operation path and the recovery of its result: 17 passed,
97.81 s. A separate scenario of a real failure at `reproduction_after`:
1 passed, 212.60 s. The subsequent commands were not executed and not counted;
C2 was not called, and its unknown outcome did not become a negative guard. The
old C1 status `GOLD_NO_CANDIDATE` here means the absence of a verified
candidate for C2: the report retains the application of the proposed patch and
`VERIFICATION_FAILED`. The acceptance's initial expectation of an overall
`FAIL` status was corrected after a cross-check against this contract in force;
the product classification did not change.

Targeted checks: 562 checks of architectural boundaries and local contracts; 28
checks of graphs, typed inputs and the conditional guard; 5 checks of delivery
and influence. Six heavy typed replay checks and one historical resume check
were run separately. Input-dependent replay: 4 passed, 270.84 s; extended
invocation contracts: 16 passed, 9.41 s. A separate end-to-end acceptance of
negative experience with influence/lineage completed: 1 passed, 1741.76 s on a
pinned version from before positive publication was enabled. Their combined
operation is now being checked separately. The scenario of an incomplete task
with two targets: 1 passed, 214.76 s; oracle true did not turn into `FULL`. The
full multi-target run: 1 passed, 420.41 s. On a retained incomplete result the
refusal of the positive preference port was checked separately. The combined
package of architectural boundaries, local contracts, invocation, plan
semantics and statuses: 621 passed, 45.26 s. The end-to-end positive repeat
with a new C1 execution and an independent oracle: 1 passed, 2196.17 s. Local
contracts of the retained patch, including the check of insertions and
deletions with a real `git apply` and the merging of identical patches from
different confirmations: 28 passed, 0.71 s; this package overlaps the 621
checks above. The first end-to-end scenario without the needed edit in the
model's answer passed C1/oracle up to `FULL` but ended with a refusal at
publication: Stage 14 did not yet open the patch of the previous attempt as the
physical source of delivered knowledge. This route is fixed. After the fix the
retained execution was independently reconstructed: 143 nodes, 211 edges,
27.10 s. After the fix, 565 targeted checks of architectural boundaries,
lineage and local contracts also passed in 23.67 s. The rerun reached a new
successful C1/oracle but ended in an external timeout: 2211.29 s in total, with
a limit of 1800 s for one CLI call. For the separate retained-patch file this
watchdog was raised to 3600 s without changing the product budgets and
criteria. A new full run on this tree completed: 1 passed, 2963.57 s. It
confirmed the repeated C1/oracle execution, the influence of exactly the
retained bytes, the absence of private values in Mini's requests, resume
without repeated effects and the refusal after a physical substitution of the
patch. The two previous failures do not count as a passed acceptance. The next
version of the reader, with a shared traversal of predecessors, is being
checked separately.

The combined scenario of negative and positive experience ended in an external
timeout after 2874.14 s: the new task did not finish checking/replaying its
inputs within the 1800 s of one CLI call. For this separate acceptance the
external watchdog was raised to 3600 s. The model, CVM and execution budgets
are unchanged; the correctness criteria are kept. This is neither a solution to
the performance problem nor its successful acceptance. Verified provenances
within one reconstruction of an execution are now read as one set for feedback
and delivered materials; no cache is kept between independent reads of
publications. Heavy scenarios are in separate acceptance files and CI shards.
This is not a general GitHub baseline.

### Verification of shared publication predecessors

When one publication was reconstructed, shared predecessors used to be opened
many times along different paths of the graph. `PublicationResult` now retains
only bytes that were already physically verified, within one synchronous
traversal. Each consumer receives a new JSON object. The next independent read
opens the files again; results do not pass to another thread or to a completed
copied context. Active and historical reads have different keys. A provenance
cycle causes a refusal. The limit of 128 records / 32 MiB bounds the retention;
when it is exhausted, the check is physically performed again rather than
skipped.

Eight lifecycle acceptance contracts and the dependency direction checks:
334 passed, 12.68 s. In these eight contracts only the physical reader was
replaced, so they do not count as a check of C1 or of publication authority. A
separate read of the retained real chain of patch reapplication under the
current reader matched the committed result: three physical nodes, each opened
once, 18.76 s. This is a reconstruction of previously executed experience, not
a repeated execution of the old frozen run under a changed runtime. A new full
retained-patch run completed: 1 passed, 2396.15 s. It confirmed the refusal
after a substitution of the retained bytes and the absence of repeated
execution on resume. The difference in the durations of the two runs cannot be
taken as a general benchmark: the load during the parallel acceptance differed.
The old mixed run without this change again ended in an external timeout:
4778.10 s in total, 3600 s for one CLI call. The third attempt already had a
real positive C1/oracle and a retained result, but the full completion check
did not return control in time. This run does not count as a PASS. A fresh
scenario under the fixed reader is being run with the same watchdog.

### Oracle configuration on reuse

A cross-check of the boundary found a gap: the fingerprint of the SWEbench
parameters does not include `python_executable` and `swebench_work_dir`. The
earlier comparison could permit using an old output with a different configured
executor or directory. `matches_retained_oracle_configuration()` now also
checks these values against the actual `cwd` and environment payload of the
retained oracle, and checks the environment digest and the consistency of the
version and the image tags. The historical Python/platform is compared as a
retained observation; the current environment of the reading process does not
substitute for the history.

Contracts and the existing acceptance of the SWEbench adapter/binding:
35 passed, 12.03 s. Two real publications — the successful repeat and the
negative C1-checked part — were read again by the current independent reader
and matched the committed bytes (18.42 and 5.17 s). Their retained observations
are rejected when another Python or directory is substituted. This cross-check
concerns the retained configuration; matching image tags do not by themselves
prove that the external installation or the contents of the remote dataset are
unchanged.

### Checked part of an unsuccessful attempt: Mini v4

The `mini-2.4.6-local-edit-proposals/v4` profile additionally includes
retention of `rejected-patch-guard/v5` material. The overall result stays
negative: the independent oracle did not confirm the task. The second, narrower
claim is that the exact patch passed the whole frozen C1 contract. It cannot be
obtained from an interruption, an infrastructure failure, skipped commands or
Mini's self-report. Stage 12 builds both claims from the boundary-sealed C1;
Stage 13 independently checks them and the physical bytes of the patch before
the ordinary atomic publication. V3/V4 negative guards keep their previous
behavior and readability.

After automatic selection, admission and a real CVM replay, Gold passes this
patch through Mini's local input together with the exact negative feedback. The
original task/base, C1 policy, oracle, environment and gas budget are checked;
memory does not widen the domain of applicability. The input contract of
Mini v4 requires its caller to provide independent evidence of the old C1
contract for such negative material. Mini itself does not derive this evidence
from the `oracle_resolved=false` flag alone.

The local proposal joins the retained part with a public alternative for
**other files**. Coinciding paths get `OVERLAPPING_PATHS`; resolving overlaps
and an arbitrary editing sequence are not supported yet. The composite patch is
checked again against the exact original text, scope and capabilities, has its
own digest and does not inherit success. If this new digest already has a
confirmed negative outcome, it is excluded too. Repeating the original
unsuccessful patch remains forbidden in its exact domain.

All considered alternatives and their provenance are retained: `LOCAL_MEMORY`,
`LOCAL_PARTIAL_MEMORY` with the original patch and the index of the public
extension, `PUBLIC_PROPOSAL`. The search boundary is up to 128 retained patches
and up to eight proposals; the reasons for omitting combinations are retained
explicitly. Positive applicable knowledge still has priority. Conflicting and
unknown outcomes create neither a positive method nor a checked partial basis.
Stage 10 recomputes the influence over the same inputs; Stage 14 opens the
physical source of the partial patch. External requests still belong only to
Mini and do not contain this local material or its derivatives.

Targeted checks of composition, the previous Mini v3, the conditional guard and
dependency direction: 369 passed, 13.52 s. An additional package of the two
guard kinds, partial proposals and publication/reusable contracts: 19 passed,
1.41 s (the sets overlap). The acceptance really applies the composite diff
through `git apply` and executes both fixed computations; this does not replace
Gold/C1. After all owners were connected, 492 checks of behavior, invocation,
local alternatives, statuses, dependency direction and lineage mutants passed
in 33.81 s. In CI the new heavy scenario has its own shard; in total the paths
of 136 different heavy files were checked, and each exists in the repository.

The separate heavy file
`acceptance/stage4/stage16/test_partial_memory_loop_acceptance.py` runs three
related tasks from one original commit: an actual failure of the overall oracle
after a C1 success; composing a new fix from the retained part and a public
extension; applying positive experience with an empty list of model proposals.
It checks the command graph, the exact exclusion of the old failure, the
independent final outcome, local causal comparisons, Mini's external requests,
the unchanged original repository and resume without repeated effects. The
scenario and its CI shard have been added; a full PASS is not claimed yet.

This is an initial profile of applicable composite methods with explicit
conditions and goal verification, following the
[GTPyhop](https://github.com/dananau/GTPyhop/blob/main/additional_information.md)
approach. There is no arbitrary HTN planner here, no automatic evidence that
any failure is useful and no completed connection of all eight kinds of memory.

### Universal agent execution port

The new coding run configuration uses `experiment-input/v4` with the `agents`
field; the frozen input v7 retains the exact profile and the deterministic
selection. Setup, migration of the earlier declaration and the observed
limitations are described in
[UNIVERSAL_AGENT_EXECUTION.md](UNIVERSAL_AGENT_EXECUTION.md).

The main run remains `python -m synapse project run`. Gold builds the context
from admitted knowledge and the accepted plan, persists it and authorizes
execution again. Only then does Stage 10 pass the exact task and a separate
local information channel to `AgentExecutionPort`. Mini is called through its
adapter. The executor's output remains a candidate; C1, the final outcome,
publication and memory keep their previous evidence owners. An unsuitable
profile receives an explicit refusal; there is no automatic selection of Mini
when another agent refuses.

The requirements for the whole accepted plan and for the operation delegated to
the agent differ. Stage 10 passes the capability `repository.edit` to the
resolver for producing the patch; `verification.run` remains a requirement for
the checks that C1 executes. The full bytes of the task and the historical
worker binding are retained. An agent profile does not get the right to confirm
the result on its own; unsupported operations and capabilities without
corresponding operations are rejected before the call.

The new profile passed the end-to-end Gold/C1 acceptance with publication and
recovery without a repeated call. A2A and MCP passed a separate transport
acceptance. Docling has no document task profile and no independent
verification inside Gold yet; no separate bypass of Gold was added. An isolated
run of Docling/ACP in the current environment is not confirmed: creating the
required namespaces is forbidden. The presence of the SDKs and a successful
transport acceptance are not presented as readiness of the whole universal
loop.

Tests remain external acceptance. The heavy Gold, A2A and MCP scenarios are in
separate files and separate CI jobs for parallel runs; the product does not
import tests, fixtures or scenario builders.

### Discrepancy with the general model at the previous stage

A cross-check against sections 5–10 of the actual model description dated
2026-09-09 does not allow the general layer of elements and persistent agents
to be declared complete:

| Requirement | Owner actually connected | What is still required |
|---|---|---|
| An element with a purpose, owned paths/symbols, API, dependencies and invariants | `task_targets.py` resolves real Python targets; source observations retain bytes and bindings | A long-term model of the element with requirements and relations; one discovered symbol does not replace such an element |
| Target State, Current State, Development Delta | GoverningTaskContract sets the goal and constraints; C1, outcome and lineage confirm the actual execution | Link the element's requirements, the observed state and open obligations across different tasks |
| Eight kinds of memory | Source operation journal stores the materials and all observed outcomes; run records and publications retain execution decisions and evidence | One typed representation of Identity/Structural/Goal/Context/Episodic/Decision/Defect/Evidence with claim statuses and relations, used in selection and execution |
| A persistent element agent | `AgentRuntime` lives in the interpreter and supports its own memory and serialization; `MemoryPalace` is used by the language runtime | Long-term assignment of the element's owner, its context and recovery in the canonical Gold loop |

`AgentRuntime.think()` currently calls its own `LLMBackend`; simply connecting
this method in place of the existing worker would break the accepted Mini
boundary. By default `MemoryPalace` has the episodic/semantic/procedural rooms,
and `backend="sqlite"` opens `:memory:`. Therefore adding eight room names does
not by itself create a durable project memory and the required link with Gold.
Both components have active consumers in `interpreter.py`; they are not
globally dead code and are neither removed nor renamed in this package.

### Primary sources for complex areas

- [Temporal: replay and execution history](https://docs.temporal.io/workflow-execution)
  — the foundation for binding the input-dependent trace to the history of a
  specific invocation. The implementation stays in the current Gold/CVM owner,
  without adding a second execution engine and without repeating external
  effects during replay.
- [Dixon, Ginsberg, Parkes: Learning and Relevance](https://www.cs.cmu.edu/afs/cs/project/jair/pub/volume21/dixon04a-html/node4.html)
  — the basis for the conditional ban on an exact confirmed conflict. In Gold
  this is the task/base/patch/C1/oracle/environment domain. Negative knowledge
  is retained; only a matching domain enters a specific selection.
- [GTPyhop, Dana Nau](https://github.com/dananau/GTPyhop)
  — the approach of explicit applicable methods and backtracking to
  alternatives was chosen for the next extension of the planner. Studying the
  algorithm by itself does not count as an implemented graph generator: this
  item remains open below. In the connected command plans, the verification
  that requirements are achieved is represented by separate obligations over
  the actual actions. The commands and results are executed and confirmed by
  the existing C1, not by a simulation in the planner.
- [CaMeL: Defeating Prompt Injections by Design](https://arxiv.org/abs/2503.18813)
  — the data flow boundary was checked, including derived values and errors. In
  the current Mini integration, local computations are terminal for the
  external dialogue; a local result does not become a model message. Ownership
  of the provider stays with Mini. The restriction concerns the contents of
  requests, without claiming the absence of any side channels through timing
  and the number of calls.
- [Python: difflib](https://docs.python.org/3/library/difflib.html)
  — the existing `unified_diff` representation is used. Local reconstruction is
  limited to this exact format; the conformance of the result is also checked
  by a real `git apply` in the external acceptance.

At this intermediate stage the following remained open: physical confirmation
of the partial profile and of the related end-to-end scenario, extension beyond
exact patches and non-overlapping edits, the general model of elements and of
the eight kinds of memory, and the lifecycle of persistent agents. The command
graph and local alternatives do not prove arbitrary procedural planning. Source
files, refusals, failures and unknown outcomes continue to be retained
regardless of the admission of executable knowledge.
