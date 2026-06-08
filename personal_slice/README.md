# Personal Slice Runner

Personal Slice is an executable local verification path for a prepared Synapse
bug-fix patch. PS-CORE-01 hardens the runner trust boundary: task contracts are
loaded from a trusted base commit, verification-created files are checked before
commit, local refs use compare-and-swap, and runtime reports default outside the
source worktree.

## Execution order

1. Resolve `--base` to a commit SHA.
2. Read `--task` from that base commit, not from the current working tree.
3. Verify committed scaffold inputs at that same base commit.
4. Create an isolated detached worktree at the base commit.
5. Run `reproduction_before`.
6. Run optional `baseline_commands`; failures are classified as
   `BASELINE_PREEXISTING_FAILURE` before the patch is applied.
7. Run `git apply --check --recount`.
8. Apply the prepared patch.
9. Run `scope_after_patch`.
10. Run `reproduction_after`, acceptance commands, and full-suite commands.
11. Run `scope_before_commit` immediately before staging.
12. Create the verified commit with deterministic local identity.
13. Create `refs/personal-slice/verified/<run-id>` as durable local evidence.
14. Apply the verified commit to the target local ref with compare-and-swap.
15. Write a JSON report for every outcome.

## Task contract

The trusted base is an invocation parameter, not executable data inside the task:

```bash
python -m personal_slice run \
  --base <base-sha-or-ref> \
  --task path/to/committed-task.json
```

`--task` must be a repository-relative POSIX path. Absolute paths and `..` path
traversal are rejected. The current working tree cannot alter the executable
`TaskContract`; the runner reads the task with `git show <base>:<task>`.

The preferred allowed-scope schema is explicit:

```json
{
  "allowed_scope": {
    "exact": ["path/to/file.py"],
    "prefixes": ["tests/generated/"]
  }
}
```

`exact` entries and `prefixes` must be repository-relative paths with no absolute
paths and no `..`. Prefixes are normalized with a trailing `/`. Empty scope is
rejected. Historical exact-only list contracts remain accepted for compatibility,
but strings such as `tests/` are not silently treated as prefixes unless they are
placed in `allowed_scope.prefixes`.

Optional baseline health checks can be declared as:

```json
{
  "baseline_commands": [
    ["python", "-m", "pytest", "-q", "relevant/test.py"]
  ]
}
```

A failing baseline command exits with `BASELINE_PREEXISTING_FAILURE` and the patch
is not applied.

## Scaffold integrity

The task path, `patch_path`, reproduction command inputs that are repository
files, and `required_scaffold_paths` are checked against the resolved base commit.
Reports include SHA-256 hashes for the committed task contract, patch, and
reproduction inputs. Reports also include base and verified tree IDs.

## Reports

Reports use schema `personal_slice.report/v0.3` and default to:

```text
.git/personal-slice/reports/<task-id>-<run-id>.json
```

Use `--report-dir <path>` to write reports elsewhere. The default report path is
outside the source tree, so normal runs do not create untracked JSON files in the
user worktree.

## Outcomes and exit codes

| Outcome | Exit code | Meaning |
| --- | ---: | --- |
| `APPLIED` | 0 | Verification passed and the local target ref was atomically updated. |
| `PATCH_REJECTED` | 11 | `git apply --check` or `git apply` failed. |
| `VERIFICATION_FAILED` | 12 | Reproduction, scope validation, tests, suite, or verified commit failed. |
| `BASELINE_PREEXISTING_FAILURE` | 13 | Baseline health failed before the candidate patch was applied. |
| `APPLICATION_STALE_BASE` | 20 | Target ref did not match the expected base SHA for compare-and-swap. |
| `INTERNAL_ERROR` | 30 | Malformed contract, missing committed scaffold, unsafe ref, evidence-ref failure, or runner error. |

`APPLIED` means only:

```json
{
  "application_scope": "LOCAL_REF_ONLY",
  "remote_updated": false
}
```

The runner never pushes to GitHub, never updates remotes, never merges, and never
moves the currently checked-out worktree branch. It updates only the configured
local target ref, and only when that ref is not checked out in any linked
worktree.

## Worktree durability

`--keep-worktree` preserves the detached verification worktree and reports
`PROCESS_ENVIRONMENT_PRESERVED`. Without `--keep-worktree`, the temporary
worktree is removed and reports `REMOVED`. The evidence ref remains after cleanup.

## Historical evidence

The first missing-method Personal Slice task is retained under
`personal_slice/examples/missing_method/` as historical evidence. It is explicitly
marked `HISTORICAL_EVIDENCE` and `NOT RERUNNABLE AGAINST CURRENT HEAD`; it is not
a default task for current `main`.

## Prepared patch only

The runner does not call a real LLM. `personal_slice/llm_gateway.py` records the
patch source as `provider = "prepared_patch"` with `model`, `input_tokens`, and
`output_tokens` set to `null`; token status is
`NOT_APPLICABLE_VARIANT_A`.
