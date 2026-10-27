# PR 112 review follow-up evidence

The tested product and test tree was commit `e0188add452d6478dc4c65814ba0947229769812`. It is the local pre-evidence snapshot used for testing and is not a separate commit on the final branch; after amending, the final commit adds or updates only reports and raw measurement data relative to that snapshot. The source tree identifier and individual mutation targets are recorded in `reports/pr112_targeted_mutations.json`. The original baseline is `1e226e52a3400d4fb0336420569d8bab43c8f783`.

1. Native sources are graph inputs to the Stage4 canonicalization owner, so kernel-only changes select and require `stage4-native`. Four kernel-only cases failed before the fix, while the Python codec and its native test controls passed. The final gate rejects a selected failure; an unrelated documentation change does not require native. The native job remains unconditionally executed by the workflow; no new skip policy was added.
2. The mutation report was regenerated on the named tested commit. Nineteen applicable mutations were detected by their individually passing baseline cases; the obsolete `_encode_stage4_legacy_failure` mutation was explicitly excluded, not counted. A test that inspects each current target and named case protects the evidence against stale references. The `cache-thaw-list-content` mutation tests decoded content, not independent copy isolation.
3. Executable benchmarks and their plugin moved into `acceptance/benchmarks/pr112/`; report files retain data and documentation. The plugin is only loaded explicitly. Before the move, collection registered the benchmark `conftest.py`; afterward a root collection completed without it. Product and architecture files do not import benchmark tooling.
4. Every encode and decode vector now has a deterministic typed-graph input SHA-256. Descriptions distinguish object types, references, cycles, ordered mappings, floating-point signs, byte contents, Unicode decomposition, surrogates and nesting. Swapping same-refusal examples fails the gate. Expected result/refusal vectors were generated via the base public API and verified unchanged against the old file. The generator checks the base tree against the pinned Git objects and checks both recorded source hashes.
5. Both immutable source archives contained no `.pyc` in the corrected A/B. The original replay claim is withdrawn: bytecode state was uncontrolled. The corrected replay and initial-run results, and a same-source precompiled-versus-source diagnostic, are in `reports/pr112_benchmark/README.md` and the adjacent JSON files. A service environment reconnect occurred during the series; the measurement process continued and completed, but the event limits interpretation.
6. Historical contract references are computed once per revision within each selection, without changing filtering or physical evidence validation. The decode comment now describes validation and canonical re-encoding without claiming a single traversal.

## Verification commands and observed results

Commands use the isolated environments prepared for this review; they are targeted checks, not a full test-suite claim.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /tmp/pr112-review-venv/bin/python -B -m pytest -q -p no:cacheprovider acceptance/ci/test_verification_gate.py acceptance/ci/test_plan_selection.py --tb=short
# 32 passed in 40.88s

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /tmp/pr112-review-venv/bin/python -B -m pytest -q -p no:cacheprovider acceptance/stage4/stage16/test_adaptive_task_identity_contract.py --tb=short
# 7 passed in 0.59s

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /tmp/pr112-review-venv/bin/python -B -m pytest --collect-only --trace-config -q -p no:cacheprovider
# 7924 tests collected in 11.19s; no report or benchmark instrumentation plugin registered

/tmp/pr112-review-venv/bin/python -B acceptance/stage4/generate_canonical_baseline_vectors.py --base-tree /tmp/pr112-review-base --output tests/data/stage4_baseline_1e226e52.json
# Completed successfully; old baseline results and provenance compared equal.

PATH=/tmp/pr112-cargo/bin:$PATH RUSTUP_HOME=/tmp/pr112-rustup CARGO_HOME=/tmp/pr112-cargo CARGO_TARGET_DIR=/tmp/pr112-rust-target /tmp/pr112-review-venv/bin/maturin build --release --locked --manifest-path native/stage4_canonical/Cargo.toml --interpreter /tmp/pr112-review-venv/bin/python --out /tmp/pr112-native-wheel
# Wheel built; Rust 1.99.1 emitted two PyO3 unexpected-cfg warnings for gil-refs.

SYNAPSE_REQUIRE_STAGE4_NATIVE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /tmp/pr112-native-venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_stage4_canonical_differential.py tests/test_stage4_native_direct.py tests/test_stage4_gold_native.py acceptance/stage4/stage11/test_native_runtime_resume_acceptance.py --tb=short
# 79 passed in 7.43s, 0 skipped.

SYNAPSE_REQUIRE_STAGE4_NATIVE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /tmp/pr112-native-venv/bin/python -B -m pytest -q -p no:cacheprovider acceptance/ci/test_verification_gate.py acceptance/ci/test_plan_selection.py acceptance/ci/test_benchmark_isolation.py acceptance/ci/test_mutation_evidence_integrity.py acceptance/stage4/stage16/test_adaptive_task_identity_contract.py tests/test_stage4_canonical_differential.py tests/test_stage4_native_direct.py tests/test_stage4_gold_native.py acceptance/stage4/stage11/test_native_runtime_resume_acceptance.py tests/test_stage4_gold_architecture.py tests/test_stage4_gold_dependency_direction.py tests/test_stage4_decode_cache_budget.py acceptance/stage4/stage10/test_memory_history_routing_contract.py --tb=short
# 714 passed in 68.32s, 0 skipped.

PYTHONDONTWRITEBYTECODE=1 /tmp/pr112-review-venv/bin/python -B /tmp/pr112-bench-harness/remeasure_bytecode.py --base /tmp/pr112-review-base --candidate /tmp/pr112-review-candidate --python /tmp/pr112-review-venv/bin/python --artifact /workspace/synapse/reports/pr112_benchmark/replay --output /tmp/pr112-bytecode-results.json --base-sha 1e226e52a3400d4fb0336420569d8bab43c8f783 --candidate-sha e0188add452d6478dc4c65814ba0947229769812 --pairs 5
# Five complete pairs, matching observable outcomes; raw results are retained.

/tmp/pr112-review-venv/bin/python -B acceptance/benchmarks/pr112/replay_bytecode_diagnostic.py --source-only /tmp/pr112-review-base --precompiled /tmp/pr112-review-compiled --python /tmp/pr112-review-venv/bin/python --artifact reports/pr112_benchmark/replay --output /tmp/pr112-replay-pyc-diagnostic.json --commit 1e226e52a3400d4fb0336420569d8bab43c8f783 --pairs 5
# Five complete pairs on identical source contents; raw results are retained.
```

Expected red tests before fixes were observed: four native-source selector cases failed and two Python controls passed; the report collection loaded the old `conftest.py`; the old positional vector check failed to notice a swap with an identical refusal; the per-claim reference test saw repeated calculations; and bytecode preflight did not reject a tree containing `.pyc`. Reverting each relevant guard was caught by the corresponding targeted mutation test. The initial root collection had three errors because `hypothesis` was absent; it was installed in the isolated test environment before the successful collection above. The first benchmark attempt loaded the working checkout instead of the base archive and failed its provenance assertion before timing; the harness was copied outside the checkout and the successful series used that copy. These setup failures are not counted as successful mutation kills.

The `ub-mode` tools were run on the affected product and selector files in a temporary copy. `crapper --no-coverage` reported cyclomatic complexity, not CRAP scores: `_adaptive_patches` 26 and `add_runtime_edges` 24. `dryer --threshold 0.8` reported one pre-existing structural match between two short validation blocks in `canonicalization.py` (score 1.0); it was not changed because this review prohibits unrelated refactoring and structural similarity alone does not establish identical semantics. `mutator --scan` listed sites; the targeted campaign above supplied the actual mutation evidence. No claim is made that a full CRAP or mutation-quality gate passed.

## Existing base API issue: not changed

The public base API raises a bare `TypeError` rather than `CanonicalizationViolation` when constructing a diagnostic whose detail exceeds the accepted length. Both the pinned baseline and the current candidate reproduce the following with CPython 3.12:

```python
from synapse.experiments.gold import canonicalization as c

values = [
    {'e\u0301' * 130: {'bad': '\ud800'}},
    type('x' * 300, (), {})(),
]
for value in values:
    c.canonicalize_stage4_payload(
        value, profile_id=c.STAGE4_CANONICAL_PROFILE_V1,
        codec_id=c.STABLE_CANONICAL_CODEC_ID,
    )
```

Run the call separately for each value: both raise `TypeError('detail must be a non-empty safe string up to 256 characters')`. The differential vectors preserve this baseline behavior. Deciding whether and how to change the public failure contract is outside this PR follow-up.

`ADAPT_MEMORY` remains a functionality change for the project owner to decide. This evidence does not declare the PR ready to merge.
