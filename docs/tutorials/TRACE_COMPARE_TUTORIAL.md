# End-to-End Trace Compare Tutorial

- **Status:** P10 Product Clarity tutorial
- **Scope:** examples + documentation only
- **Runtime changes:** none
- **Goal:** prove the current record → replay → compare workflow with real commands and real output.

This tutorial is the smallest verified walkthrough of the current Track C user
flow. It uses two `.syn` programs that differ by one actor-message payload:

```text
baseline.syn -> sends Worker.process("job-42")
modified.syn -> sends Worker.process("job-43")
```

Both programs avoid Category C constructs from `docs/DETERMINISM_CONTRACT.md`.
They do not use `dream`, generated habit/evolution IDs, consensus tickets,
`time`, `random`, or `uuid`.

---

## 1. Files used by this tutorial

```text
examples/tutorial_trace_compare/baseline.syn
examples/tutorial_trace_compare/modified.syn
examples/tutorial_trace_compare/README.md
```

`baseline.syn`:

```synapse
agent Worker {
    model "mock"
}

send Worker.process("job-42")
print("sent")
```

`modified.syn`:

```synapse
agent Worker {
    model "mock"
}

send Worker.process("job-43")
print("sent")
```

---

## 2. Record both artifacts

From the repository root:

```bash
rm -rf /tmp/synapse_trace_compare_demo
mkdir -p /tmp/synapse_trace_compare_demo

python3 -m synapse.cli run examples/tutorial_trace_compare/baseline.syn \
  --record --output /tmp/synapse_trace_compare_demo/baseline

python3 -m synapse.cli run examples/tutorial_trace_compare/modified.syn \
  --record --output /tmp/synapse_trace_compare_demo/modified
```

Verified output for the baseline artifact:

```json
{"final_history_hash": "9b29aaf2b163647d3abc430bad785b686cdcfa9040f2854fc8b7c1389e0d8858", "history_length": 1, "program_hash": "sha256:110b6a9592a4368b797705df496f75ecc0ae5b7e7839e53c76e39e774706171f", "recorded": "/tmp/synapse_trace_compare_demo/baseline"}
```

Verified output for the modified artifact:

```json
{"final_history_hash": "55a8dddd59f3c43e23298987fc3805590a21d6ff65449f41f3277ba95040ca02", "history_length": 1, "program_hash": "sha256:bee204e33b2a2f574e2f98d72da13ba790bebac21ec2a9fe8b11197906edb58c", "recorded": "/tmp/synapse_trace_compare_demo/modified"}
```

Both artifacts have one durable event: `message_sent`.

---

## 3. Replay both artifacts with embedded mocks

```bash
python3 -m synapse.cli replay --mock /tmp/synapse_trace_compare_demo/baseline
python3 -m synapse.cli replay --mock /tmp/synapse_trace_compare_demo/modified
```

Verified baseline replay result, abridged to the key fields:

```json
{
  "artifact_dir": "/tmp/synapse_trace_compare_demo/baseline",
  "drift": 0,
  "final_history_hash": "9b29aaf2b163647d3abc430bad785b686cdcfa9040f2854fc8b7c1389e0d8858",
  "history_length": 1,
  "program_hash": "sha256:110b6a9592a4368b797705df496f75ecc0ae5b7e7839e53c76e39e774706171f"
}
```

Verified modified replay result, abridged to the key fields:

```json
{
  "artifact_dir": "/tmp/synapse_trace_compare_demo/modified",
  "drift": 0,
  "final_history_hash": "55a8dddd59f3c43e23298987fc3805590a21d6ff65449f41f3277ba95040ca02",
  "history_length": 1,
  "program_hash": "sha256:bee204e33b2a2f574e2f98d72da13ba790bebac21ec2a9fe8b11197906edb58c"
}
```

`drift: 0` means the artifact can be replayed without changing the recorded
history or stable state sanity fields.

---

## 4. Compare an artifact with itself

```bash
python3 -m synapse.cli debug compare \
  /tmp/synapse_trace_compare_demo/baseline \
  /tmp/synapse_trace_compare_demo/baseline

echo $?
```

Verified output:

```json
{"equal": true, "first_divergence_index": null, "left_event": null, "left_history_hash": null, "reason": "equal", "right_event": null, "right_history_hash": null}
```

Verified exit code:

```text
0
```

`0` means the traces are equal.

---

## 5. Compare baseline against modified

```bash
python3 -m synapse.cli debug compare \
  /tmp/synapse_trace_compare_demo/baseline \
  /tmp/synapse_trace_compare_demo/modified

echo $?
```

Verified output:

```json
{"equal": false, "first_divergence_index": 0, "left_event": {"message": {"args": ["job-42"], "method": "process", "payload": "job-42", "receiver": "Worker", "sender": "global"}, "type": "message_sent"}, "left_history_hash": "9b29aaf2b163647d3abc430bad785b686cdcfa9040f2854fc8b7c1389e0d8858", "reason": "hash_mismatch", "right_event": {"message": {"args": ["job-43"], "method": "process", "payload": "job-43", "receiver": "Worker", "sender": "global"}, "type": "message_sent"}, "right_history_hash": "55a8dddd59f3c43e23298987fc3805590a21d6ff65449f41f3277ba95040ca02"}
```

Verified exit code:

```text
7
```

`7` means the comparison ran successfully and found a divergence. It is non-zero
so shell scripts and CI pipelines can treat drift as a failed predicate.

---

## 6. What the divergence means

The first divergence index is `0` because the first durable event differs:

```text
left:  Worker.process("job-42")
right: Worker.process("job-43")
```

The divergence engine compares the forensic hash chain, not a hand-written
payload diff in the CLI. The CLI loads both artifact directories, creates two
`GoldenArtifactTraceAdapter` instances, delegates comparison to
`find_trace_divergence()`, and prints `TraceDivergenceResult.to_dict()`.

---

## 7. Current limitations shown by the tutorial

This tutorial intentionally uses artifact directories, not fork IDs.

Current debugger compare supports:

```text
synapse debug compare <artifact_dir_a> <artifact_dir_b>
```

It does not yet support:

```text
synapse debug compare <fork_id_a> <fork_id_b>
```

Reason: `ForkRecord` currently stores identity and lineage metadata. It does not
persist a full `execution_history`. Fork-to-fork comparison is blocked until the
session persistence / replay-runtime work in Alpha3g.

This tutorial also does not demonstrate `dream` or `integrate`, because those
are explicitly deferred by `docs/DETERMINISM_CONTRACT.md` until their replay
contracts are approved.
