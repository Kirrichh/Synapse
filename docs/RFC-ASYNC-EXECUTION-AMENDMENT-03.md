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
`integrate`, `context` blocks (plan segments), the memory builtins `tool` and
`task_plan` and the slow path `try { … } catch (ACTION_FAILED as name) { … }`.
Classification is fail-closed (`synapse/durable_profile.py`): an unsupported
construct refuses the run before any effect. `tool` and `task_plan` exist
only while a memory session is bound (`--project-state` with
`--memory-config`); without one they are not callable.

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
