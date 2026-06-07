# Personal Slice Variant A

Personal Slice Variant A is an executable local verification path for a prepared
Synapse bug-fix patch. It does not require an archive or any manual file import:
the task contract, reproduction file, and unified diff are committed in this
repository.

Variant A proves this end-to-end path:

1. strict `TaskContract` loading;
2. isolated detached git worktree creation;
3. prepared unified patch discovery;
4. pre-change reproduction;
5. `git apply --check --recount`;
6. patch application;
7. allowed-scope validation;
8. post-change reproduction;
9. targeted tests;
10. full test suite;
11. verified commit creation inside the isolated worktree;
12. atomic local-ref compare-and-swap;
13. runtime-generated JSON report.

## Prerequisite

The scaffold must already be committed before the runner is invoked. The runner
checks the configured `required_scaffold_paths` with `git cat-file -e` at the
resolved base revision; uncommitted local files are not accepted as task input.

## Run

```bash
python -m personal_slice run personal_slice/task.json --keep-worktree
```

`--keep-worktree` preserves the real detached worktree, including the applied
patch, verification outputs in the JSON report, and the verified commit checkout.
Without `--keep-worktree`, the runner removes the temporary worktree after the
report is written.

## Outcomes and exit codes

| Outcome | Exit code | Meaning |
| --- | ---: | --- |
| `APPLIED` | 0 | Verification passed and the local target ref was atomically updated. |
| `PATCH_REJECTED` | 11 | `git apply --check` or `git apply` failed. |
| `VERIFICATION_FAILED` | 12 | Reproduction, scope validation, tests, suite, or verified commit failed. |
| `APPLICATION_STALE_BASE` | 20 | Target ref did not match the expected base SHA for compare-and-swap. |
| `INTERNAL_ERROR` | 30 | Malformed contract, missing committed scaffold, unsafe ref, or runner error. |

`APPLIED` means only:

```json
{
  "application_scope": "LOCAL_REF_ONLY",
  "remote_updated": false
}
```

The runner never pushes to GitHub, never updates remotes, never merges, and never
moves the currently checked-out worktree branch. It updates only the configured
local ref, `refs/heads/personal-slice/verified-missing-method`, via `git
update-ref` compare-and-swap.

## Reports

Reports are written to:

```text
personal_slice/reports/<task-id>-<run-id>.json
```

A report is written for every outcome, including internal errors.

## Inspect the verified branch

After a successful run, inspect the local verified ref with:

```bash
git show --stat refs/heads/personal-slice/verified-missing-method
```

or check it out in a separate worktree.

## Prepared patch only

Variant A does not call a real LLM. `personal_slice/llm_gateway.py` records the
patch source as `provider = "prepared_patch"` with `model`, `input_tokens`, and
`output_tokens` set to `null`; token status is
`NOT_APPLICABLE_VARIANT_A`.
