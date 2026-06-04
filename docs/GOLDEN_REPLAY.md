# Golden Replay Suite — Alpha3e

Status: **ACTIVE FOR v2.2.0-alpha3e RELEASE GATE**

Golden replay is the final deterministic baseline before the `v2.2.0-alpha3e`
tag. It is intentionally an instrumentation/test layer only. It must not add new
language syntax, VM opcodes, lowering rules, debugger implementation, habit
interrupts, soulprint/audio/swarm features, or runtime semantics.

## Two-layer model

### Layer 1 — Strict Golden Suite

A small set of representative programs is pinned as a hard CI gate. These tests
validate exact deterministic behavior for core runtime paths:

- inline guard + local `catch(GUARD_VIOLATION)`;
- guard failure + compiler-inserted `GUARD_VIOLATION_ACK` recovery;
- LLM call served from embedded mock cache;
- actor messaging / mailbox-observable state;
- nested context / runtime history;
- durable or snapshot-oriented execution path;
- governed memory write lowering.

Layer 1 validates exact `program_hash`, `host_abi_version`, `history_length`,
`final_history_hash`, selected stable VM/interpreter state fields, LLM cache
hits, and `drift = 0`.

### Layer 2 — Corpus Smoke Replay

The full `examples/*.syn` corpus remains a smoke layer: parse and basic run where
runtime-safe. The corpus is not frozen as a strict hash baseline. This prevents
brittle tests where harmless example wording changes break release CI.

## Artifact layout

A golden replay artifact is a directory:

```text
manifest.json
source.syn
initial_vm_snapshot.json
vm_snapshot.json
history.json
llm_cache.mock.json
```

Minimum manifest schema:

```json
{
  "schema_version": 1,
  "layer": "strict",
  "metadata": {
    "program_hash": "sha256:...",
    "host_abi_version": "2.2.0-alpha3e",
    "language_version": "2.2.0-alpha3e",
    "runtime_version": "0.22.0-alpha3e",
    "spec_version": "2.2.0-alpha3e",
    "source_path": "..."
  },
  "environment": {
    "virtual_clock_start": "2026-01-01T00:00:00Z",
    "clock_mode": "deterministic",
    "clock_step": 1,
    "gas_limit": null
  },
  "final": {
    "history_length": 0,
    "final_history_hash": "...",
    "state_sanity": {}
  }
}
```

## Virtual clock contract

Replay mode must not read wall-clock time. Golden artifacts carry a deterministic
environment contract with `virtual_clock_start`, `clock_mode`, and `clock_step`.
If future host symbols expose time/random-like values, record/replay must serve
those values from `execution_history` or from this deterministic environment, not
from the real host clock.

## Stable state validation

The validator must not compare `VMState.__dict__` or an entire interpreter
snapshot. It compares only stable semantic fields:

- `program_hash`;
- `host_abi_version`;
- `history_length`;
- `final_history_hash`;
- `final_ip` when a VM snapshot exists;
- stack/frame depths;
- guard/context/policy stack depths;
- `guard_violation_active`;
- stable locals key/hash;
- mailbox, actor-log, and memory-audit lengths.

Debug counters, performance counters, object identities, temporary caches,
instrumentation flags, and wall-clock timestamps are ignored. New optional state
fields must have defaults when old artifacts are deserialized.

## LLM mock replay contract

`synapse replay --mock <artifact_dir>` must never call an external provider. LLM
calls are served by `llm_cache.mock.json`. A missing cache entry is a deterministic
failure, not a live fallback.

## Commands

Record:

```bash
python3 -m synapse.cli run examples/full_demo.syn --record --output /tmp/full_demo.golden
```

Replay:

```bash
python3 -m synapse.cli replay --mock /tmp/full_demo.golden
```

Gate:

```bash
make test-golden
```

`make test-golden` is an integration/release gate. It does not replace the fast
unit-test gate `make test`.
