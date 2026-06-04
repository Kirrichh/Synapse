# Synapse Debugger User Guide

- **Status:** Current as of Track C conclusion (Alpha3f)
- **Scope:** Documentation only
- **Audience:** Engineers recording, replaying, and comparing Synapse runs.

This guide describes only commands that exist in `synapse/cli.py` today. Where a
capability is not yet implemented, it is named explicitly under "Current
limitations" rather than omitted.

For a complete verified walkthrough with concrete `.syn` files and real JSON
output, see `docs/tutorials/TRACE_COMPARE_TUTORIAL.md`.

---

## 1. What the debugger can do today

- **Record** a run into a deterministic golden artifact (`synapse run --record`).
- **Replay** that artifact using only embedded mocks, with no provider calls
  (`synapse replay --mock`).
- **Compare** two artifacts and locate the first point they diverge
  (`synapse debug compare`).
- **Fork lifecycle** primitives for exploratory debugging
  (`synapse debug fork / dispose / status / inject-event`).

## 2. What it cannot do yet

- No deterministic replay *runner* (step-by-step re-execution with cache
  injection) — deferred to Alpha3g.
- No session persistence, daemon, or REPL — `compare` works within one process
  on artifact directories.
- No `compare` by `fork_id` — comparison takes artifact directories, because a
  `ForkRecord` carries identity/lifecycle metadata but not its own
  `execution_history`.
- Category C constructs (Evolution tickets, Habit registration, deferred
  consensus) are **not** strict-golden-safe. Strict replay currently supports
  Category A and B events only; these Category C events will cause divergence
  until their replay contracts land (see `docs/DETERMINISM_CONTRACT.md`).
- `DreamBlock` is a special case: as of Alpha3g it is **Category B**
  (replay-safe via `dream_key`/`result_hash` verification), so it replays
  deterministically against its own recorded artifact. It is **still excluded
  from Strict Layer 1**; RFC-DREAM-STRICT-LAYER1-ELIGIBILITY denies A2 strict
  admission and requires a future consume-only/subtrace/state-delta replay
  model. Legacy pre-Alpha3g dream artifacts without the strict schema remain
  Category C.

---

## 3. Recording an artifact

```
synapse run <file.syn> --record --output <artifact_dir> --layer strict
```

This runs the program and writes a golden artifact directory containing
`manifest.json`, `source.syn`, `history.json`, `vm_snapshot.json`,
`initial_vm_snapshot.json`, and `llm_cache.mock.json`. The manifest records the
`program_hash`, `host_abi_version`, and `final_history_hash`.

`--layer` is `strict` (default) or `smoke`. Use `strict` only for programs
whose constructs are replay-safe per the determinism contract.

## 4. Replaying an artifact

```
synapse replay --mock <artifact_dir>
```

Replays the recorded artifact using only embedded mocks. The replay environment
uses a cache-only LLM backend, so provider calls are impossible — a missing
cache entry becomes a deterministic failure rather than a live call. Replay
verifies the recorded history chain and reports drift if the replayed run does
not match the recording.

## 5. Comparing two artifacts

```
synapse debug compare <left_artifact_dir> <right_artifact_dir>
```

Loads both artifacts as immutable trace adapters and runs the core divergence
engine. The CLI computes no hashes itself; all forensic logic is delegated to
`find_trace_divergence()`, which derives the per-event chain hash with the same
function the replay engine uses.

### Output

The command prints a structured JSON object. For identical traces:

```json
{"equal": true, "reason": "equal", "first_divergence_index": null, ...}
```

For diverging traces, the first point of divergence is reported:

```json
{
  "equal": false,
  "reason": "hash_mismatch",
  "first_divergence_index": 14,
  "left_event": {"type": "..."},
  "right_event": {"type": "..."},
  "left_history_hash": "...",
  "right_history_hash": "..."
}
```

Divergence reasons: `equal`, `hash_mismatch`, `type_mismatch` (secondary
diagnostic), `length_mismatch` (one trace is a clean prefix of the other).

## 6. Exit codes

The `debug compare` and surrounding debug commands use a stable exit-code
contract so the result is scriptable in CI. A divergence is a valid result, not
an error — but it is non-zero so a script can detect it.

| Exit | Meaning |
|------|---------|
| `0` | Compare succeeded, traces are equal |
| `7` | Compare succeeded, divergence found |
| `1` | Invalid argument / malformed JSON / missing path |
| `8` | Artifact integrity error (malformed/broken artifact) |
| `2` | Governance violation |
| `3` | Deterministic replay constraint violated |
| `4` | Fork disposed |
| `5` | Invalid fork lifecycle transition |
| `6` | Fork resource limit exceeded |

The distinction between `7` and `1` matters: `7` means "comparison ran fine and
found a difference," while `1` means "the command could not run as given." Do
not conflate diagnostic divergence with bad input.

## 7. Fork lifecycle commands (exploratory)

These operate on an in-process fork registry and are for exploratory debugging:

```
synapse debug fork --from <history_hash> --mode <deterministic|exploratory-live>
synapse debug status <fork_id>
synapse debug inject-event --fork-id <id> --type <event_type> --payload <json>
synapse debug dispose <fork_id>
```

Forks use copy-on-write state, so an exploratory branch never mutates its parent
or any golden artifact. Injected events are validated; forbidden injections
(guard verdict override, capability grant, hash rewrite, direct ACK) are
rejected.

## 8. Why compare takes directories, not fork IDs

A common expectation is `compare <fork_a> <fork_b>`. That is intentionally not
supported yet. A `ForkRecord` holds identity and lineage metadata but does not
yet carry its own `execution_history`, so there is nothing to compare at the
fork level. Comparison therefore operates on recorded artifact directories,
which do carry full history. Fork-to-fork comparison and cross-session
persistence are Alpha3g topics.
