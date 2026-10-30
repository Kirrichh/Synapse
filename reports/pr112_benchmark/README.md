# PR 112 CLI performance evidence

The original product candidate was `44dc07ea0d9f762ca96ffd0c90ba8925cfcc2ca8`; the base was `1e226e52a3400d4fb0336420569d8bab43c8f783`. The bytecode-controlled follow-up recorded candidate `e0188add452d6478dc4c65814ba0947229769812`, which is not available in the remote repository. Its measured source snapshot cannot be independently verified from the saved data; do not treat that SHA as a public provenance binding.

## Exact-ASCII fast path and in-call reuse: `d18a4c4b62fb1d7403e8a8fc62fdfa3fcbde2bd9`

The commit `d18a4c4b62fb1d7403e8a8fc62fdfa3fcbde2bd9` (parent `56585964`, merged unchanged so this SHA is published) changes four product files and nothing else:

- exact `str` ASCII text returns early from `canonical_values._canonical_text`, Gold `canonicalization._text`, behavior `_text` and `_inline_string`: ASCII has no surrogates, is already NFC and encodes to UTF-8 at its own length; the syntax pattern and the 4096-byte inline limit still apply to it, and subclasses of `str` keep the old path;
- the behavior core is validated by its owners (`BehaviorCore.to_dict`, `_seal_unit`) instead of again inside `_behavior_core_payload`, and `SynapseBehaviorUnit.to_dict` uses the core payload its own validation built;
- a verification record checks seal and digest, then decodes its bytes once per call (`_require_verification_payload`), for `payload()`, `to_dict()` and `require_verification_record`.

The series measures these three mechanisms together and does not attribute the gain among them. Failure codes and messages are unchanged for every record and unit the producers create; how `d18a4c4` answered a planted digest and how the follow-up restored `56585964` is described below; the follow-up is not part of the measured tree. Raw data are in `controlled_series_d18a4c4.json`, written by the same harness (`72d31d33…`), Python binary (`e50d468e…`), native addon (`5bfd634e…`) and replay artifact (`c7ed0e37…`) as the `56585964` series below. Both trees were verified against their commits (Git trees `2d12baa0…` for `56585964` and `c6b81036…` for `d18a4c4`) before execution and after every run. Linux 6.18.44-fc-v185 x86_64, CPython 3.12.3, Unicode 15.0.0, one host and one environment for all 20 runs; the series was resumed once across a background time limit on that host. Five alternating AB/BA pairs per comparison; the other conditions and units are those of the next section. "Faster" counts the pairs in which the second column used less CPU.

Every run matched the `56585964` series' behavior: publication and recall outcomes, `MODEL` then `EXACT_MEMORY` routes, `FULL` status, patch `c88993a7…`, provider and oracle counts, a restored repository, both resumes, and a zero-drift replay.

`56585964` Python versus `d18a4c4` Python:

| Scenario | Base wall | Candidate wall | Change | CPU | Faster | RSS MiB |
|---|---:|---:|---:|---:|---:|---:|
| source-publication | 5.27 [4.76–6.18] | 4.25 [4.13–4.51] | -19.4% | 5.1 → 4.1 | 5/5 | 63.3 → 63.3 |
| source-recall | 3.26 [2.90–4.02] | 2.90 [2.70–3.21] | -11.3% | 3.2 → 2.8 | 5/5 | 63.3 → 63.5 |
| run-initial | 6.09 [5.33–6.90] | 5.08 [4.94–5.45] | -16.5% | 5.9 → 5.0 | 5/5 | 70.4 → 71.0 |
| approve-initial | 106.83 [98.53–115.70] | 82.06 [79.83–90.90] | -23.2% | 105.3 → 80.1 | 5/5 | 79.5 → 79.7 |
| run-reuse | 16.48 [15.48–16.63] | 13.49 [12.74–14.16] | -18.1% | 16.2 → 13.3 | 5/5 | 75.2 → 75.2 |
| approve-reuse | 357.29 [340.85–363.83] | 283.51 [256.15–291.97] | -20.6% | 352.6 → 278.6 | 5/5 | 85.0 → 84.9 |
| resume-first | 65.73 [63.51–66.97] | 53.87 [50.39–56.28] | -18.0% | 64.6 → 53.2 | 5/5 | 83.4 → 83.4 |
| resume-repeat | 66.59 [64.77–72.81] | 53.03 [48.05–63.23] | -20.4% | 65.9 → 52.3 | 5/5 | 83.5 → 83.4 |
| replay-mock | 0.81 [0.76–1.16] | 0.97 [0.79–1.11] | +20.3% | 0.8 → 1.0 | 1/5 | 48.5 → 48.4 |

`d18a4c4` Python versus `d18a4c4` native:

| Scenario | Python wall | Native wall | Change | CPU | Faster | RSS MiB |
|---|---:|---:|---:|---:|---:|---:|
| source-publication | 4.32 [3.91–4.57] | 4.00 [3.50–4.66] | -7.3% | 4.1 → 3.8 | 4/5 | 63.3 → 63.9 |
| source-recall | 2.79 [2.67–3.15] | 2.79 [2.35–2.94] | +0.3% | 2.7 → 2.7 | 3/5 | 63.4 → 63.8 |
| run-initial | 5.10 [5.03–5.85] | 4.36 [4.10–5.02] | -14.6% | 5.0 → 4.2 | 5/5 | 71.2 → 71.6 |
| approve-initial | 82.10 [78.16–86.15] | 56.71 [51.47–65.51] | -30.9% | 80.4 → 55.5 | 5/5 | 79.6 → 80.0 |
| run-reuse | 12.94 [12.73–13.39] | 9.47 [9.03–10.03] | -26.8% | 12.7 → 9.3 | 5/5 | 75.4 → 75.6 |
| approve-reuse | 261.93 [260.12–287.13] | 184.85 [172.33–190.50] | -29.4% | 257.8 → 182.1 | 5/5 | 84.9 → 85.6 |
| resume-first | 52.75 [50.42–55.20] | 35.84 [32.50–36.20] | -32.1% | 52.1 → 35.4 | 5/5 | 83.5 → 83.8 |
| resume-repeat | 51.30 [51.12–54.13] | 36.32 [34.11–39.04] | -29.2% | 50.6 → 36.0 | 5/5 | 83.5 → 83.8 |
| replay-mock | 0.86 [0.79–0.99] | 0.82 [0.79–1.13] | -4.8% | 0.9 → 0.8 | 3/5 | 48.5 → 48.3 |

Against `56585964` the candidate used less CPU in all five pairs of every scenario except `replay-mock`, and the wall ranges do not overlap in `source-publication`, `approve-initial`, `run-reuse`, `approve-reuse` and both resumes. `source-recall` and `run-initial` overlap; their direction holds in every pair but the size is not established. Median wall time fell 18–23% and median CPU 18–24% in the long scenarios (`approve-initial`, `run-reuse`, `approve-reuse`, both resumes). Median memory changes stay under 0.7 MiB per process (largest `run-initial`, +0.61 MiB).

`replay-mock` was slower in four of five pairs, so it was repeated alone after the series: 40 alternating pairs on the same host and trees, every call with zero drift and the same replay result, the artifact digest unchanged (`replay_recheck_d18a4c4.json`; a one-off loop through the pinned `timed_exec.py`, not the pinned series harness). Base wall 0.867 s [0.759–1.238], candidate 0.868 s [0.759–1.118]; median CPU 0.859 → 0.854 s; median paired CPU change -0.7%, candidate slower in 19 of 40 pairs. No change in `replay-mock` is established.

The native backend still removes about 30% in the long scenarios on top of the candidate, less than the 43% it removed on `56585964`, consistent with the candidate's Python path already doing less of that text and projection work; the share of each mechanism was not measured. The two comparisons ran in different halves of one series and the host was about 8% faster in the second half (candidate Python `approve-reuse` 283.5 s, then 261.9 s), so the combined effect from `56585964` Python to `d18a4c4` native (about 357 s to 185 s in `approve-reuse`) is an estimate, not a paired measurement. The `56585964` series below ran on a different, faster run of the same host type; compare figures only within a series.

### Follow-up after review (not part of the measured tree)

The series above binds to `d18a4c4`. Two review rounds shaped the follow-up; this is its current state against `d18a4c4`. Every seal and digest check runs on every call, and nothing is cached across calls.

- **Verification records as in `56585964`.** `d18a4c4` checked a record once in `to_dict()` where `56585964` checked it twice, so a `str` subclass planted in the private `_digest` with `object.__setattr__` could answer that single comparison: one that answers only its first comparison, or one that removes `_seal` while comparing, was returned where `56585964` raised `IDENTITY_MISMATCH` and `TYPE_MISMATCH`. The intermediate `6dd4b3f` refused every non-exact digest instead, which also changed `payload()`, `reference` and `require_verification_record`. In the profile of an `approve-reuse` run every `VerificationRecord` method together took about 0.2 s of 1123 s, so `verification_contract.py` and its readers in promotion and publication are restored to `56585964` byte for byte. A test pins the `56585964` results for planted digests on all four accessors.
- **The guard refusal reads its record again before persisting.** `6dd4b3f` reused the first read for `worker_result_ref` after `promote_verified_use` and `_verified_outcome` had run, so a record unsealed between the two reads was persisted. `controller_recovery.py` is restored to `56585964`; a test unseals the record during `_verified_outcome` and requires `TYPE_MISMATCH` with nothing persisted.
- **Behavior units refuse in the order of `56585964`: core, canonical bundle, projection.** `6dd4b3f` projected the core before checking the bundle, so a core whose refs had been replaced by their transport form failed with `AttributeError` instead of the bundle's refusal. 26 cases pin the bundle refusal for every damaged bundle field through `validate_behavior_unit` and `SynapseBehaviorUnit.to_dict`; 156 combinations of field, damage, refs and entry point give the same results as `2e123a8`. With only the refs replaced, `56585964` and this tree both still fail with `AttributeError`; that predates this PR and is left as is.
- **Projections are reused within one operation (owner decision).** Validation returns the program IR and contract projections it made, and the payload reuses them after the bundle check. How many times immutable sealed parts are projected is not part of the behavior contract; it is observable only through instrumented or stateful projections. Projections of program, input and output contract per call, summed:

  | Operation | `56585964` | `2e123a8` | this follow-up |
  |---|---:|---:|---:|
  | `BehaviorCore.to_dict` | 6 | 6 | 3 |
  | `validate_behavior_unit` | 9 | 6 | 3 |
  | `SynapseBehaviorUnit.to_dict` | 15 | 6 | 3 |

- **One owner for scalar text.** `scalar_text_utf8_size` in `synapse/canonical_values.py` returns the UTF-8 size of exact text, or nothing when it has a lone surrogate. Gold `canonicalization._text`, behavior `_text` and `_inline_string` use it; their encode branches, unreachable after the surrogate check, are removed, and so are the duplicate surrogate pattern and `_has_lone_surrogate`. `canonical_values._canonical_text` and `canonicalization._text` keep an inline ASCII return because they run for every canonical string: a call per string made `_validate_data_only` about 12% slower in a measurement on this host.

Checks of the follow-up on its working tree:

| Check | Result |
|---|---|
| Sensitivity of the new cases | On `6dd4b3f` 30 of them fail (26 refusal order, 3 planted digests, 1 guard reread); on `2e123a8` the 2 planted-digest cases of `to_dict()` fail; on this tree all pass, with and without the native addon |
| Mutation catalog `acceptance/verification/pr112_followup_mutants.json`, run by `acceptance/verification/run_manual_mutants.py` on a fresh export of the commit | 18 mutants, all killed; the record pinned to this commit is added by the next commit |
| Differential fuzz `acceptance/verification/pr112_differential_fuzz.py`, seeds 1 and 2 | 1,136,080 observations in 9 sections, identical section digests for `56585964`, `2e123a8`, `6dd4b3f` and this tree. `6dd4b3f` matches too: the corpus does not plant digest subclasses, damage two parts of a unit at once or change a record between reads, which is why the named tests above pin those cases |
| Repository suites, every file in its own process, four in parallel | `tests/` 217 files: 5547 passed, 41 skipped, 1 failed; `acceptance/stage4/stage12` 12 files: 107 passed; `acceptance/stage4/stage13` 20 files: 87 passed; the stage14 pre-context lineage recovery file 1 passed; canonical baseline integrity 16 passed; `acceptance/verification` 30 passed; `acceptance/ci` 58 passed. The failure is `test_p2c_late_boundary_process_races_same_and_different_hashes`, one of the two process-race cases of `tests/test_durable_execution.py` that fail on `main` (`e524990`) and on `4c420f1` in this environment; the other passed in this run. With the native addon loaded, the native, canonical differential, canonical value, Gold canonicalization, behavior and stage12 outcome contract files: 206 passed |

Speed of the follow-up is measured process by process here and by a controlled CLI series after this commit. CPU medians on the same host, alternating with `2e123a8`: `validate_behavior_unit` 0.387 → 0.340 s per 300 calls (-12.2%, faster in 7 of 8 pairs), `behavior_unit_from_dict` 0.294 → 0.261 s per 100 calls (-11.2%, 7 of 8), `_validate_data_only` on a 9,000-string payload 15.20 → 15.25 ms (no change established, slower in 4 of 10 pairs).

## Final controlled series: `56585964513a0b07af111d5a53e4023a1a72ed33`

This is the complete pinned series for the published candidate `56585964513a0b07af111d5a53e4023a1a72ed33` against base `1e226e52a3400d4fb0336420569d8bab43c8f783`; raw data are in `controlled_series_56585964.json`. Both source trees were verified against those commits (Git trees `2d08fc94…` and `2d12baa0…`) before execution and after every run, the harness matched the candidate and contained no bytecode, and the native series loaded the built wheel (binary `3b19d077…`, wrapper `2cb636cc…`). Linux 6.18.44-fc-v185 x86_64, CPython 3.12.3, Unicode 15.0.0, one environment and one host for all 20 runs; the series was resumed across background time limits on that host. Five alternating AB/BA pairs per comparison; each CLI call is a new process; the filesystem cache was uncontrolled and is not called cold. Wall and CPU are seconds (median, wall range in brackets); memory is the `wait4` maximum RSS of one process in MiB, not an aggregate process-tree peak.

Every run matched: publication and recall outcomes, initial `MODEL` and reuse `EXACT_MEMORY` routes, `FULL` status, patch `c88993a7…`, one provider request, two fresh resolved oracle observations, no reuse agent execution, a restored repository, both resumes, and a zero-drift replay.

Base Python versus candidate Python:

| Scenario | Base wall | Candidate wall | Change | CPU | RSS MiB |
|---|---:|---:|---:|---:|---:|
| source-publication | 9.00 [8.46–9.79] | 4.22 [3.92–4.30] | -53.1% | 8.8 → 4.1 | 63.2 → 63.5 |
| source-recall | 3.60 [3.47–4.09] | 2.80 [2.64–2.94] | -22.1% | 3.6 → 2.8 | 63.2 → 63.5 |
| run-initial | 7.77 [7.42–8.02] | 4.87 [4.67–5.31] | -37.3% | 7.7 → 4.8 | 70.2 → 70.6 |
| approve-initial | 221.13 [218.11–222.58] | 93.22 [90.83–94.65] | -57.8% | 218.9 → 91.8 | 75.0 → 79.7 |
| run-reuse | 28.08 [27.11–28.76] | 14.15 [14.01–14.46] | -49.6% | 27.8 → 14.0 | 70.9 → 75.2 |
| approve-reuse | 818.27 [799.21–836.73] | 314.22 [311.80–321.06] | -61.6% | 811.9 → 311.2 | 76.7 → 85.0 |
| resume-first | 100.87 [97.53–102.96] | 58.84 [57.23–61.39] | -41.7% | 100.0 → 58.3 | 73.9 → 83.6 |
| resume-repeat | 101.86 [97.68–102.69] | 59.05 [57.26–60.27] | -42.0% | 100.9 → 58.5 | 73.9 → 83.7 |
| replay-mock | 0.76 [0.66–0.82] | 0.80 [0.68–0.82] | +4.4% | 0.8 → 0.8 | 48.5 → 48.5 |

Candidate Python versus candidate native:

| Scenario | Python wall | Native wall | Change | CPU | RSS MiB |
|---|---:|---:|---:|---:|---:|
| source-publication | 4.24 [4.17–4.52] | 3.34 [3.07–3.65] | -21.3% | 4.1 → 3.2 | 63.4 → 64.0 |
| source-recall | 2.66 [2.63–3.01] | 2.45 [2.35–2.54] | -7.8% | 2.6 → 2.4 | 63.5 → 63.9 |
| run-initial | 5.03 [4.94–5.09] | 4.26 [4.07–4.48] | -15.2% | 4.9 → 4.2 | 71.2 → 71.7 |
| approve-initial | 92.81 [91.90–94.46] | 55.30 [53.99–56.43] | -40.4% | 91.3 → 54.4 | 79.8 → 80.0 |
| run-reuse | 14.00 [13.71–14.56] | 9.28 [9.12–9.63] | -33.7% | 13.8 → 9.1 | 75.4 → 75.7 |
| approve-reuse | 318.46 [309.90–326.15] | 182.56 [178.73–185.41] | -42.7% | 314.5 → 180.0 | 85.1 → 85.6 |
| resume-first | 59.31 [58.74–60.74] | 33.62 [33.11–35.45] | -43.3% | 58.8 → 33.4 | 83.6 → 84.0 |
| resume-repeat | 59.12 [58.36–59.63] | 34.25 [33.84–35.04] | -42.1% | 58.6 → 33.9 | 83.5 → 83.9 |
| replay-mock | 0.76 [0.71–0.83] | 0.75 [0.73–0.85] | -1.3% | 0.8 → 0.7 | 48.5 → 48.4 |

In every scenario except `replay-mock` the five base and five candidate wall times do not overlap, in both comparisons. `replay-mock` ranges overlap in both, so no change is established there. The Python candidate holds up to about 10 MiB more per process in the longest scenarios; the native backend adds under 1 MiB. Five samples give medians and ranges, not a reliable p95. A partial earlier series of seven runs on another host was discarded rather than merged, because the runner refuses to mix environments.

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
