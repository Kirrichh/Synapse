# Trace Compare Tutorial Example

This directory contains the minimal end-to-end example for the current Track C
workflow:

```text
baseline.syn  -> records one `message_sent` event with payload `job-42`
modified.syn  -> records one `message_sent` event with payload `job-43`
```

The files intentionally avoid Category C constructs from
`docs/DETERMINISM_CONTRACT.md`:

- no `dream` / `integrate`;
- no generated habit/evolution/consensus IDs;
- no user `time`, `random`, or `uuid` builtins;
- no live provider dependency.

## Run the tutorial

From the repository root:

```bash
rm -rf /tmp/synapse_trace_compare_demo
mkdir -p /tmp/synapse_trace_compare_demo

python3 -m synapse.cli run examples/tutorial_trace_compare/baseline.syn \
  --record --output /tmp/synapse_trace_compare_demo/baseline

python3 -m synapse.cli run examples/tutorial_trace_compare/modified.syn \
  --record --output /tmp/synapse_trace_compare_demo/modified

python3 -m synapse.cli replay --mock /tmp/synapse_trace_compare_demo/baseline
python3 -m synapse.cli replay --mock /tmp/synapse_trace_compare_demo/modified

python3 -m synapse.cli debug compare \
  /tmp/synapse_trace_compare_demo/baseline \
  /tmp/synapse_trace_compare_demo/modified
```

Expected compare result: exit code `7`, with `reason` = `hash_mismatch` and
`first_divergence_index` = `0`.

See `docs/tutorials/TRACE_COMPARE_TUTORIAL.md` for the fully verified command
transcript and JSON output.
