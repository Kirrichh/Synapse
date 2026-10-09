# RFC-ASYNC-EXECUTION-AMENDMENT-03

**Title:** Durable cognitive execution profile (`synapse.durable.cognitive/v1`)  
**Requirement ID:** `REQ-ASYNC-CLI-01`  
**Parent RFC:** `docs/RFC-ASYNC-EXECUTION.md`  
**Prior amendments:** `docs/RFC-ASYNC-EXECUTION-AMENDMENT-01.md`, `docs/RFC-ASYNC-EXECUTION-AMENDMENT-02.md`  
**Document status:** `DRAFT — TEAM REVIEW AND PRODUCT OWNER APPROVAL REQUIRED`

The implementation described here already exists on the PR #108 branch
(`codex/stage4-stage16-acceptance`). This amendment documents it for review. It
is not an approval and does not by itself authorize a merge.

---

# 1. Amendment rule

This is an additive normative amendment. Every contract of the parent RFC and
of Amendments 01 and 02 remains in force for P2a durable runs (artifact schema
`1.0.0`). This amendment governs only runs whose program is outside the P2a
statement subset and inside the cognitive profile below (artifact schema
`1.1.0`). A program that fits P2a keeps the P2a profile.

# 2. Scope of the profile

`run --durable` accepts a program in the cognitive profile when its only
constructs are functions, loops, habits, the memory palace, `dream`,
`integrate`, `context` blocks (plan segments), `parallel` graphs outside
`dream` and `integrate` (section 5a), the memory builtins `tool`,
`task_plan`, `hypothesis`, `probe`, `established`, `recover`, `know` and
`search_knowledge`, the pure builtin `admit`
and the slow path `try { … } catch (ACTION_FAILED as name) { … }`.
Classification is fail-closed (`synapse/durable_profile.py`): an unsupported
construct refuses the run before any effect. The memory builtins exist only
while a memory session is bound (`--project-state` with `--memory-config`);
without one they are not callable. Palace `imprint` and `recall` stay outside
the profile: palace record identities are not deterministic.

# 3. Artifact

A cognitive artifact has `artifact_schema_version = "1.1.0"`,
`execution_profile = "synapse.durable.cognitive/v1"` and a `memory` descriptor:
`{schema_version, project_state, configuration_sha256, executor, exam}`. The
descriptor names the memory owner, the digest of its frozen configuration, the
executor (`synapse-runtime/<version>/synapse.durable.cognitive/v1`) and, for an
exam run, `{mode, snapshot}`; nothing else about memory is stored in the
artifact. Resume rebuilds the memory factory from the descriptor and refuses a
changed configuration or executor.

# 4. Crash points and recovery

1. A cognitive run is persisted `RUNNING` after every recorded external
   effect (the gateway result of an external action). Its lock names the owner
   process (`owner.json`: pid, create time, host); a lock whose named process no
   longer exists on the same host is provably stale and is removed. No other
   lock is presumed stale.
2. `resume --state-file` of a `RUNNING` artifact without `--suspension-id` is
   recovery. Before the program continues, the memory owner consolidates the
   recorded tail in emergency mode (no trust, births or boundary; verdicts are
   provisional). An exam run has no emergency consolidation.
3. Recovery re-executes the program and requires every event it produces to
   equal the recorded event at the same position; a divergence is
   `REPLAY_INTEGRITY_ERROR`. Recorded effects (external actions, LLM answers,
   clock, randomness) are consumed, never repeated. At the end of the record
   the run continues LIVE.
4. An action whose STARTED record has no result after a crash is recorded as
   lost with unknown effect. The gateway repeats it automatically only when its
   tool contract declares it idempotent; otherwise the program sees the loss.

# 5. Verified re-execution for the court

The same re-execution, bound to a replay session that answers only from
records and raises `ReplayHorizon` instead of any live effect, is the court's
replay check of a session (stage 1b): `replay_verified`, `replay_diverged`,
`unavailable` or `budget_exceeded` under the configured event budget.

A reproduction runs the same program from the replay data in the owner's
custody with a session that answers every action only from the gateway's
record (`reproduce_cognitive_session`); it never reaches a live effect.
Retention compacts a case's raw trace only when a reproduction yields the
case's exact events at their recorded positions.

A learned habit's `habit_activated` event records every condition its typed
trigger checked with the value it saw (`matched`) and why its body stopped
before its end (`detail`); both follow deterministically from the recorded
event and answers, so a re-execution reproduces them. A learned body that
passes a result between its steps reads the earlier answer from the record,
so neither a re-execution nor recovery creates a new external object. The
typed trigger vocabulary adds `is`, a field present with one JSON kind.

`recover(failure)` is available only in the slow path of that same failure.
The memory composes admitted learned procedures to recover it and performs
their actions through the recorded action path of the slow path; a learned
body that already ran is continued from its recorded answers and the parts it
already tried, never repeated. At an impasse parts are tried in order, each
once, and never over an unknown effect; a part's own impasses are joined
inside it, never with itself. A part's calls declare the failed operation
they serve; the gateway refuses such a declaration before any effect unless
it names an unresolved operation of the same scope, and the language exposes
no way for a program to make it.
Every hypothesis (`composition_planned`) and the execution
(`composition_executed`) are recorded events; a re-execution recomputes them
from the pinned snapshot and the recorded answers and requires them equal.

`know(statement)` records a statement (subject, property, value, polarity,
conditions, valid time, text) read from a recorded observation of the run;
its embedding is a recorded answer of the declared embedder through the
gateway (`knowledge_declared`). `search_knowledge(query[, options])` returns
the candidates of the pinned snapshot's knowledge at a valid time as memory
knew it at a window, with each channel's ranking and the semantic channel's
cost (`knowledge_searched`); a re-execution answers the embedding from the
record and requires the same candidates. Neither decides anything:
`admit` does.

# 5a. Event-driven graphs

`parallel NAME [limit N] { node … ; signal "e" when … ; commit NODE [=> effect] }`
runs a graph of nodes by readiness (`synapse/runtime/dataflow.py`). A pure node
evaluates on the interpreter thread; a call node `tool(name, args)` is an
observation performed concurrently on a worker through the same gateway, under
its own operation scope and the ordinal `df:<instance>:<node>:<attempt>`; the
gateway refuses, before any effect, a call whose tool contract does not declare
`observation` (an observation is also idempotent and leaves no effect on a
refusal). Every value has a version and every computation the versions it
read; a node runs only when every input it reads is settled, so it never
combines versions from different moments. A computation whose inputs move
while it runs is superseded at once and recorded `stale` when it answers. A
signal is evaluated once per combination of its inputs' settled versions and
raises its event for the nodes subscribed to it (`on "e"`); an event that
never came is not a negative fact. The commit waits for the committed node,
its inputs and the sources of the events that refresh them, and nothing else;
the effect acts on the committed value only, once, on the interpreter thread.
What is still in flight after it is drained and recorded `cancelled`.

The history carries `dataflow_started`, one `dataflow_step` per computation
(node, attempt, status `integrated`/`stale`/`cancelled`, versions read,
version produced, timings), `dataflow_signal` and `dataflow_commit` (the
versions the commit rested on), with each call's `external_action` in the
order its answer was integrated. Live, answers are integrated in the order the
gateway journal holds them, so a reproduction answered from that journal
integrates them in the same order. A re-execution follows the record: each
answer is taken from it in its recorded order and nothing is performed; past
the end of the record the graph goes on live, and a call whose answer was lost
in a crash is asked again under its own identity. Parallelism concerns the
program's observations; computations inside one process still share one
interpreter thread.

# 6. Boundaries

The runtime core offers only the typed adapter points of
`synapse/memory_points.py`. It evaluates no experience and changes no trust.
Only the composition root (`synapse/cli.py`) builds the memory subsystem;
`tests/test_memory_dependency_direction.py` is the tripwire for that direction.

# 7. Evidence

Acceptance through the canonical launch lives in `acceptance/memory/`. Every
scenario starts `python -m synapse run --durable --project-state
--memory-config` against real MCP stdio tool servers. The crash scenario
(`test_crash_recovery_acceptance.py`) kills the process group during a slow
path and resumes it. See `docs/GOLD_KNOWLEDGE_INGESTION.md` for the operator
commands and the verified limits.
