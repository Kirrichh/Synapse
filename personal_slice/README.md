# Controlled Change compatibility entry point

SYN-CORE-01 moved controlled-change ownership into the canonical
`synapse.change` package. The historical `personal_slice` package remains only
as a compatibility shell for existing invocations.

## Canonical command

```bash
python -m synapse change apply \
  --base <revision> \
  --task <task-path>
```

`--base` identifies the trusted committed revision that contains the task JSON,
prepared patch, scaffold, and reproduction inputs. The task file is read from
that base commit, so uncommitted local edits to the task file do not affect a
run.

## Compatibility command

```bash
python -m personal_slice run \
  --base <revision> \
  --task <task-path>
```

This compatibility command parses historical CLI flags, constructs a
`ControlledChangeRequest`, calls the same `synapse.change.execute_controlled_change`
runner as the canonical CLI, prints the returned `ControlledChangeResult`, and
returns `result.exit_code`.

## Ownership

Canonical controlled-change ownership is:

- `synapse.change.runner` — single orchestration and public API.
- `synapse.change.contract` — task-contract parser and validation.
- `synapse.change.workspace` — Git path, worktree, and candidate snapshot helpers.
- `synapse.change.verification` — command execution and phase results.
- `synapse.change.application` — local-ref compare-and-swap application.
- `synapse.change.report` — report schema/state writer.
- `synapse.change.prepared_patch` — current prepared-patch metadata.

Prepared patch is the current concrete acquisition mechanism. SYN-CORE-01 does
not introduce a provider framework, provider protocol, LLM provider, or a provider-selection CLI flag.

`python -m synapse.cli change apply` is a technical module form that remains
available for compatibility, but `python -m synapse change apply` is the canonical
user-facing controlled-change command. Runtime launch paths are unified through
`synapse.application` and are outside this compatibility shell.

## Reports and safety

Reports retain the historical schema family and Patch 2a writes the structured
`personal_slice.report/v0.4.0` report from `synapse.change.report`. Active task
contracts use `synapse.controlled-change.task/v1`, declare reproduction
`committed_inputs`, and record trusted task, patch, reproduction, and base-tree
provenance. The runner uses detached worktrees, trusted committed patch bytes,
NUL-safe Git path status parsing, exact/prefix allowed-scope checks, prepared
candidate integrity checks, and current legacy verified-commit/evidence/local-ref
application phases. Candidate snapshot summaries use the independent
`candidate_snapshot_sha256/v1` digest algorithm; that digest version is separate
from the report schema version. Patch 2b hardening such as evidence zero-OID CAS
and linked-worktree target protection is not claimed here. The runner does not
push, merge, rebase, or update remote refs.
