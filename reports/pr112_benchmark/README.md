# PR 112 CLI performance evidence

The original product candidate was `44dc07ea0d9f762ca96ffd0c90ba8925cfcc2ca8`; the base was `1e226e52a3400d4fb0336420569d8bab43c8f783`. The bytecode-controlled follow-up recorded candidate `e0188add452d6478dc4c65814ba0947229769812`, which is not available in the remote repository. Its measured source snapshot cannot be independently verified from the saved data; do not treat that SHA as a public provenance binding.

## Corrected bytecode-controlled comparison

Five alternating AB/BA pairs used the same CPython 3.12.14 environment and Python backend. Both immutable source archives contained no `.pyc`; `-B` and `PYTHONDONTWRITEBYTECODE=1` prevented new bytecode. The filesystem cache was uncontrolled. A service environment reconnect occurred during the series; the process continued and recorded completed outcomes, but this is a limit on the interpretation. Wall and CPU are seconds; RSS is KiB, measured as described below.

| Scenario | Base wall (min–max) | Candidate wall (min–max) | Wall change | CPU base → candidate | RSS base → candidate |
|---|---:|---:|---:|---:|---:|
| approve-initial | 120.6954 (115.3257–123.3155) | 47.0798 (45.7860–48.2824) | -73.6156s / -61.0% | 120.6375 → 47.0722 | 73424 → 78056 |
| replay-mock | 0.4352 (0.4210–0.4578) | 0.4379 (0.4313–0.4791) | +0.0027s / +0.6% | 0.4347 → 0.4373 | 42428 → 42444 |
| run-initial | 4.1774 (4.1482–4.2473) | 2.5853 (2.5238–2.6101) | -1.5921s / -38.1% | 4.1776 → 2.5841 | 64620 → 64788 |
| source-publication | 4.5733 (4.4303–4.8560) | 2.1608 (2.1065–2.2771) | -2.4125s / -52.8% | 4.5710 → 2.1613 | 57964 → 58060 |
| source-recall | 2.0724 (1.8928–2.2826) | 1.5169 (1.3921–1.5630) | -0.5555s / -26.8% | 2.0701 → 1.5162 | 57824 → 58008 |

The prior reported `replay --mock` slowdown of 19.9% and +5.5 MiB RSS is withdrawn. In the controlled series no regression was detected; the cause of the earlier result is not conclusively established. With both trees source-only, the replay median difference was +0.6% and ranges overlap; the RSS median differed by 16 KiB. The old series did not record actual bytecode state, so its precise cause cannot be reconstructed from its saved data. In a separate five-pair diagnostic on identical base source and inputs, source-only versus precompiled replay measured 0.4457 versus 0.2644 s and 42,352 versus 36,780 KiB RSS, reproducing the same kind of distortion. This diagnostic does not prove which state each old run actually had.

The checked long scenario used the normal initial `project run` and approval with a real deterministic local provider and fresh C1/oracle verification. All ten samples agreed on publication/recall, patch, `MODEL` route, `FULL` status and provider/oracle counts, with a clean restored repository. Replay outcomes matched and had zero drift. The same external benchmark plugin was explicitly loaded for each run. Full input and raw data are in `bytecode_controlled_results.json`; the same-source bytecode diagnostic is in `replay_bytecode_diagnostic.json`.

## Earlier comparison: bytecode state not recorded

The following earlier data are retained for transparency, but their source bytecode state was not recorded. The earlier replay row is intentionally removed; the corrected replay result is above. The other earlier comparisons, including native and reuse, should be interpreted with this limitation.

Five alternating AB/BA pairs per series. Linux 6.18.55 x86_64, CPython 3.12.14, Unicode 15.0.0. The same Python executable and dependencies were used throughout. The baseline comparison used Python on both sides. The separate native series adds the installed wheel package to the candidate Python path. The JSON includes dependency versions and the native binary and wrapper SHA-256 identities. Each CLI invocation starts a new process. Filesystem cache was uncontrolled; no cold-cache claim is made. Resume-first and resume-repeat are separate new CLI processes over the same completed state, not persistent-process measurements. The diagnostics below include an in-process warm decode experiment.

Wall and CPU are seconds; RSS is KiB. Each cell is median, with min–max in parentheses for wall. CPU is user + system from `wait4`, including children waited for by the command. RSS is the largest individual-process `wait4` maximum in the waited tree, not an aggregate simultaneous process-tree peak. A negative wall change means faster.

## Base Python versus candidate Python

| Command | Base wall (range) | Candidate wall (range) | Wall change | CPU base → candidate | RSS base → candidate |
|---|---:|---:|---:|---:|---:|
| approve-initial | 128.52 (126.50–141.04) | 55.06 (50.82–65.86) | -73.46s / -57.2% | 128.52 → 55.03 | 73548 → 78192 |
| approve-reuse | 474.30 (462.31–524.16) | 183.55 (179.07–195.82) | -290.76s / -61.3% | 474.33 → 183.65 | 72792 → 81008 |
| resume-first | 61.12 (58.11–65.07) | 32.84 (31.57–33.30) | -28.29s / -46.3% | 61.15 → 32.87 | 70148 → 80088 |
| resume-repeat | 62.70 (57.30–65.52) | 33.10 (30.95–36.39) | -29.59s / -47.2% | 62.71 → 33.12 | 70236 → 79964 |
| run-initial | 4.61 (4.25–4.86) | 3.32 (2.92–3.73) | -1.30s / -28.1% | 4.62 → 3.31 | 64032 → 65272 |
| run-reuse | 16.86 (15.79–17.67) | 8.05 (7.81–10.33) | -8.81s / -52.3% | 16.85 → 8.05 | 65916 → 70560 |
| source-publication | 5.38 (4.45–5.52) | 2.47 (2.22–2.68) | -2.90s / -54.0% | 5.38 → 2.47 | 57944 → 58128 |
| source-recall | 2.13 (2.00–2.15) | 1.71 (1.52–2.19) | -0.42s / -19.8% | 2.13 → 1.71 | 57708 → 57996 |

## Candidate Python versus candidate native

| Command | Base wall (range) | Candidate wall (range) | Wall change | CPU base → candidate | RSS base → candidate |
|---|---:|---:|---:|---:|---:|
| approve-initial | 52.70 (52.36–57.30) | 31.16 (29.80–34.44) | -21.54s / -40.9% | 52.67 → 31.18 | 77960 → 78288 |
| approve-reuse | 176.51 (168.03–192.78) | 104.01 (101.61–106.40) | -72.50s / -41.1% | 176.58 → 104.06 | 81136 → 81708 |
| resume-first | 33.44 (30.45–37.96) | 20.02 (18.97–21.94) | -13.43s / -40.1% | 33.46 → 20.04 | 79988 → 80732 |
| resume-repeat | 32.28 (31.78–36.45) | 21.60 (19.37–22.57) | -10.68s / -33.1% | 32.30 → 21.63 | 80084 → 80756 |
| run-initial | 3.18 (2.87–3.41) | 2.50 (2.43–2.97) | -0.68s / -21.5% | 3.18 → 2.50 | 66112 → 66020 |
| run-reuse | 7.84 (7.61–9.25) | 5.56 (5.28–6.12) | -2.29s / -29.1% | 7.85 → 5.56 | 70540 → 70932 |
| source-publication | 2.68 (2.49–2.84) | 1.96 (1.85–2.49) | -0.73s / -27.1% | 2.68 → 1.96 | 58088 → 58624 |
| source-recall | 1.70 (1.52–2.08) | 1.54 (1.34–1.68) | -0.16s / -9.6% | 1.70 → 1.54 | 58224 → 58652 |

## Behavior and scope

All 20 complete scenario executions in the earlier series agreed on publication and recall outcomes, source content hashes, initial `MODEL` and reuse `EXACT_MEMORY` routes, `FULL` status, patch SHA-256 `c88993a78ded432079c2c2357e84fa2277c56f7b8552ef7a895896ce4bddcb32`, one provider request, two fresh successful oracle observations, no reuse agent execution, a clean restored repository, both completed-run resumes, and a deterministic unchanged replay artifact with zero drift. The `project run` commands returned their expected pending status 3; approval and resume succeeded. The replay is the `replay --mock` CLI supported by both versions, not an external-provider replay. Fresh C1, oracle, integrity and freshness checks ran in the project scenarios.

The corrected series establishes speedups in its listed scenarios, not every Synapse workload. The new `ADAPT_MEMORY` functionality is separate: the CLI acceptance tests cover it and a fresh negative oracle, but it is not presented as a speedup over the old route. No claim is made about a persistent CLI process, cold filesystem cache, production network latency, or p95 from five samples.

The local provider server request handling median was 0.445 ms for baseline and 0.590 ms for candidate in the Python series (five requests each). A separate instrumented candidate run measured 1.540 ms to client response headers; body reading is excluded. Neither measures external provider or network latency.

## Cache and CPU diagnostics

A separate instrumented real CLI scenario passed and recorded nine processes, 3,336,479 cache hits, 1,776 misses, 1,776 distinct byte keys counted separately per process, and zero evictions. The 99.95% hit rate and 0.053% distinct-key/lookup ratio apply only to this scenario. Its largest per-process cache recorded 3,850,020 raw bytes and 13,630,695 estimated retained bytes. Accounting uses `sys.getsizeof` and is not RSS or a strict process-memory limit. Tracking distinct keys itself retains keys and changes the diagnostic process.

The separate in-process, five-pair decode diagnostic uses 400 operations on identical inputs: warm repeated decode median 0.00293 s with cache versus 0.04727 s with cache disabled (~16.2×); 400 distinct inputs 0.08056 s versus 0.04721 s (~70.6% slower). The disabled-cache diagnostic still performs canonicality validation. These are function-level results, not end-to-end estimates. At the observed hit rate this experiment indicates that the cache can pay for repeated decoding, while unique streams incur overhead; it does not isolate the cache contribution to the CLI A/B.

A separate structural-budget diagnostic inserted 100 graphs under a 137,154-byte estimated budget and recorded 98 evictions, two retained entries, 6,798 raw bytes and 91,838 estimated retained bytes, with the raw and entry limits inactive. A thaw of one graph had 19,736 bytes of peak additional `tracemalloc` memory; process peak RSS for the whole diagnostic was 33,056 KiB. Ten fill/clear cycles left traced current allocations from 1,296 to 1,712 bytes; this is a small synthetic check, not a process-wide memory bound. Mutation isolation and oversized entries are covered by the regression tests.

A separate 10 ms CPU-sampling run of the candidate reuse approval passed and produced 15,418 Python-stack samples. Physical publication reads appeared in 58.3% of samples and Stage4 encoding in 56.1%; these inclusive counts overlap and are not additive. Leading leaf frames included Stage4 text validation (11.7%), stable text canonicalization (9.1%), JSON encoding (7.0%), and Stage4 data validation (6.6%). Sampling can miss native execution or defer signals, and instrumented wall time is not compared with ordinary A/B time. During ordinary runs, `ps` snapshots showed candidate approval at 98.6% CPU and 76,164 KiB RSS, and baseline approval at 99.5% CPU and 69,912 KiB RSS; these are instantaneous single-process observations.

## Reproduction

Copy the benchmark harness directory outside either source tree before invoking the harness, so pytest cannot import the wrong checkout. Prepare immutable archives of the exact SHAs, one Python 3.12 environment with the dependencies recorded in `cli_results.json`, and a separately installed native wheel addon before starting. The native wheel must actually load with Unicode 15.0.0. Restore the same artifact, fixed Git dates and fresh temporary project state per run. The runner checks backend identity, matching observable outcomes, the frozen replay artifact and required commands, and refuses source trees containing `.pyc`. No installations or source edits are performed by the runner. For example:

```sh
cp -R acceptance/benchmarks/pr112 /tmp/pr112-reproduction
python -B /tmp/pr112-reproduction/run_series.py \
  --base /path/to/base-archive --candidate /path/to/candidate-archive \
  --python /path/to/python312 --native-addon /path/to/installed-wheel-addon \
  --replay-artifact /path/to/reports/pr112_benchmark/replay --output /tmp/pr112-result.json \
  --pairs 5 --base-sha 1e226e52a3400d4fb0336420569d8bab43c8f783 \
  --candidate-sha 44dc07ea0d9f762ca96ffd0c90ba8925cfcc2ca8
```

The scripts under `acceptance/benchmarks/pr112/` reproduce the cache experiments. `diagnostic/sitecustomize.py` records per-process cache counts and local client time to headers when `PR112_DIAGNOSTIC_TRACE` is set. The separate `diagnostic/profile_sitecustomize.py` must be placed in a directory as `sitecustomize.py` and activated with `PR112_PROFILE_OUTPUT` for the CPU sampling run. Neither diagnostic is used for the ordinary A/B numbers.

## Verification performed

Targeted positive and negative contract checks: 281 passed, zero skipped, with the built native wheel. Relevant integration checks: 648 passed. The real adaptive CLI acceptance test: 2 passed in 206.83 s, including a fresh negative oracle and resume; the separate exact-memory CLI smoke: 1 passed in 156.80 s, including fresh oracle and resume. Native/direct/differential/identity/resume and CI checks: 119 passed; expanded direct-native and gate checks: 28 passed. Python 3.13 Unicode fallback: 37 passed and 17 native-only skipped. Cache portability: four tests passed on each of Python 3.11.17, 3.12.14 and 3.13.5. These are selected checks, not the full suite. The exact invocations for the grouped 281- and 648-test runs were not preserved in this evidence; their counts should not be read as a complete-suite result.

Targeted semantic mutants were observed failing for historical task filtering, route-only influence, structural eviction, candidate diagnostic and JSON hook changes, missing native gate dependency, and native-success fallback. The unrelated earlier mutation run with missing workflow fixture was invalid and discarded; the CI mutation harness now checks an unmutated fixture before each mutant. No merge was performed.

For the corrected replay and initial-run series, run the copied `remeasure_bytecode.py` with the same `--base`, `--candidate`, `--python`, `--base-sha`, `--candidate-sha`, and `--pairs` arguments, replacing `--replay-artifact` with `--artifact` and supplying `--output`. The original runner remains available for full reuse and resume measurements. Both runners refuse to start unless both trees match their stated commits and the harness matches the candidate, and recheck them after each run. `replay_bytecode_diagnostic.py` compares two copies of the same source after one is precompiled with `python -m compileall -q`, checking that all non-bytecode content matches.
