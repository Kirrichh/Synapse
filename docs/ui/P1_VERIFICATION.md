# P1 implementation verification

Version 1.0. Observed on 2026-10-18, on the implementation tree of
`codex/ui-foundation-v1`, reviewed in PR #110. **P1 remains IN_PROGRESS.**
This record separates implemented control behavior from unverified installed
platform behavior. It does not close any complete domain/installed source case.

## Executed checks

Environment: Linux x86_64, Python 3.12.14, Node 24.19.0/npm 11.9.0,
Rust 1.99.1, Chromium browser automation. Python tools and Rust/native build
dependencies were provisioned outside the repository's product tree.

| Check | Observed result | Scope and limits |
|---|---|---|
| Python UI-bridge acceptance | **109 passed** | Framing, strict JSON, shared corpus, state machine, real canonical CLI, fixed negotiation deadline, unread-output deadline, transport failure, bundle tooling, installed wheel import isolation |
| Evidence-tool confidentiality acceptance | **5 passed** | Synthetic diagnostic canaries are not persisted or printed for killed/surviving/invalid/timed-out mutants or a failed baseline; separate from product acceptance |
| Native external acceptance crate | **21 passed** | Protocol/context binding, raw transport, bundle integrity, diagnostic redaction, caller checks, publication acknowledgment/isolation, real bundled-core negotiation and controller lifecycle |
| TypeScript external acceptance | **44 passed** | Same raw corpus, bootstrap validation, byte/depth/Unicode limits, session/sequence filtering, safe native diagnostic codes |
| Browser/component scenarios | **Passed** | Actual renderer in browser refusal state; RU/EN/focus; external peer exercises readiness, stale/foreign delivery, stop, forced-close confirmation, corrupt-channel closure, retry and BUSY presentation |
| Existing CLI regression checks | **39 passed after targeted correction** | Interpreter startup, debugger CLI, canonical execution paths; initial old help-list expectation failed and was replaced by the observable canonical-help comparison |
| Frontend production build | **Passed** | Schema generation, strict TypeScript, standalone bundled validators, Vite assets |
| Native Linux debug application build | **Passed** | Tauri application with packaged custom protocol, configured production CSP, bundled core resources and build-bound manifest |
| Windows native-core cross-check | **Passed** | `cargo check --no-default-features --target x86_64-pc-windows-gnu`; compilation only, no Windows execution or installed WebView claim |
| npm production dependency audit | **0 reported vulnerabilities** | `npm audit --omit=dev`; registry advisory result for this dependency snapshot, not a whole-product security proof |

The real-core/controller cases were rerun against the final rebuilt core resource.
The final Python invocation passed all 114 cases: 109 product/build checks and
five evidence-tool confidentiality checks.
The native application itself has not passed a real-window or installed run here.
Artifact digests are retained in the external
[build identity record](../../acceptance/ui_bridge/evidence/build-identity.json).

The [preview](assets/bootstrap-browser.png) is captured from the actual renderer,
displaying `DESKTOP_REQUIRED` in a browser. It is not a simulated ready session,
the original prototype, or evidence of a running installed Tauri window.

## Reproduce the component checks

Build the core and generated frontend contracts using
[desktop development](DESKTOP_DEVELOPMENT.md). Install external Python acceptance
tools (`pytest`, `build`, `setuptools`; `coverage` for instrumentation) in a
development environment. From the repository root:

```bash
PYTHONPATH=. python -m pytest -q -p no:cacheprovider acceptance/ui_bridge
cargo test --manifest-path acceptance/ui_bridge/native/Cargo.toml
node --import ./synapse/ui/node_modules/tsx/dist/loader.mjs --test acceptance/ui_bridge/typescript/*.test.ts
python -m pytest -q -p no:cacheprovider acceptance/runtime/test_interpreter_cli_optimizations.py tests/test_debug_cli_alpha3f.py tests/test_system_execution_path.py
```

The first command includes three separately schedulable heavy files: handshake
deadlines, output backpressure, and wheel construction. Each process uses isolated
temporary resources. The Rust crate is external and disables desktop dependencies
for component checks; no inline Rust tests exist in shipped modules.

For browser checks, install Playwright in the external development environment,
serve the built `synapse/ui/dist` locally, and run:

```bash
python acceptance/ui_bridge/browser_acceptance.py --url http://127.0.0.1:4174
```

The checked browser driver uses `/usr/bin/chromium` in this Linux environment.
Its injected peer is confined to external acceptance. Product builds do not import,
serve, package, navigate to, or consume that peer or its status.

## Mutation evidence

Seeded mutants run in isolated copies; the working tree is never mutated.
Relevant unmodified acceptance must pass before a campaign can classify a mutant.
An unbuildable mutant or infrastructure failure is not counted as a kill.

| Campaign | Final result | Evidence |
|---|---|---|
| Python, bundler, distribution boundary | **30 killed** | [Results and survivor resolution](../../acceptance/ui_bridge/evidence/python-mutation-results.json) |
| Native protocol/controller/security components | **33 killed** | [Results](../../acceptance/ui_bridge/evidence/native-mutation-results.json) |
| TypeScript, contract generation, rendered behavior | **19 killed** | [Results](../../acceptance/ui_bridge/evidence/renderer-mutation-results.json) |

Final executed total: **82 killed, 0 unexplained survivors, 0 invalid results**.
This is a targeted campaign, not exhaustive mutation coverage of every platform
integration or an assertion that all written platform behavior is accepted.
Windows lifecycle mutations and native WebView/capability/CSP execution remain
required before the affected work can be complete.

`PY-session-exit-status` initially survived. The missing assertion concerned an
observable failure: a fatal, schema-valid negotiation refusal could exit with
success. The added real CLI refusal-exit case kills it. Evidence preserves the
initial survivor and its final result. Later outbound-validation and distribution
boundary checks also have executable mutants, rather than metadata-only assertions.

Seeds are split into Python/native/renderer records under
`acceptance/ui_bridge/mutation`. Run with writable scratch/output directories
outside the repository and the same installed dependencies:

```bash
python acceptance/ui_bridge/mutation/run.py acceptance/ui_bridge/mutation/python.json acceptance/ui_bridge/mutation/native.json acceptance/ui_bridge/mutation/renderer.json --python /path/to/development/python --cargo /path/to/cargo --workspace /path/to/scratch --output /path/to/evidence
```

The runner uses the Node dependencies and staged core already built in this
checkout. `--only MUTANT-ID` selects a targeted rerun. Per-mutant source hashes,
classifications, and selected acceptance references are retained in the linked
result records. Raw subprocess output is used transiently to classify failures;
the runner does not save it in reports or log files. This prevents private paths
or user data in diagnostics from becoming repository evidence.

The reporting tool has its own separate campaign in `mutation/reporting.json`:
three mutants attempt to print raw diagnostics, include them in structured
results, or persist a failed-baseline log. All three were killed by synthetic
confidentiality cases. [Reporting results](../../acceptance/ui_bridge/evidence/reporting-mutation-results.json)
do not inflate the 82 product/build mutation results above. Use the same runner
with that seed file to reproduce this tool-only campaign.

## Coverage and security review

An instrumented run covering the canonical subprocesses reported 96% combined
statement/branch coverage for `synapse.machine` at the preceding control revision
(106 acceptance cases). Codec, schema contract, and session state behavior reached
100% in that measured scope. The final 109-case run was uninstrumented; no final
coverage percentage is inferred from it. Remaining observed branches included
Windows binary-mode setup, exceptional I/O, queue saturation, and a generic internal
failure path. Coverage guides follow-up; it does not establish requirement closure.

Executed negative cases cover early/unknown requests, incompatible peers,
duplicate keys, precision loss, malformed streams, oversized lengths before payload
reads, stale/foreign replies, corrupted bundles, extra startup code, symlinks,
caller origin/window substitution, stale subscription acknowledgments, stalled
renderers/output, and secret-containing diagnostics. Core status creates no domain
store and grants no task authority. Error rendering uses declared codes and text.

Static review confirms closed Tauri commands/capabilities, no arbitrary renderer
filesystem/process command, no remote capability origin, navigation/new-window
restriction, packaged custom protocol, no runtime Ajv compilation, and no product
imports/resources from acceptance. Actual native enforcement still requires the
installed-window scenarios; static configuration and browser injection are not proof.

Before publication, changed text, the retained design reference, and the preview
were reviewed for private user data. Raw diagnostic excerpts were removed from
pending mutation evidence, and a repository-account reference was removed from
the source manifest. The preview has no embedded metadata or private user state.
Pattern matches were reviewed in context; synthetic security inputs and local
Tauri origins are not real account data. This scoped review is not a claim to
have audited all historical repository content. Existing project attribution was
not rewritten. The permanent data prohibition is now part of the development rules.

## Explicit incomplete evidence

- The attempted native-window run failed to start WebKitNetworkProcess at its
  distribution path. Extracted build libraries are insufficient for that runtime
  layout. No native IPC/CSP or installed UI pass is recorded.
- Linux direct-launch refusal is executed. A supported systemd user service,
  cgroup cleanup after shell death, adapter descendants, and replacement ordering
  are **not run** here.
- Windows suspended process creation, Job cleanup, handle inheritance, installer,
  actual adapters, and platform mutation checks are **not run**. Cross-compilation
  is explicitly separate evidence.
- TR-2's named plugin-shell API remains inconsistent with the native raw-pipe
  implementation; F-06 records the concrete review decision still needed.
- JCS parameter hashing, domain command ownership/recovery, domain delivery classes,
  and the later UI workspace/features are not supplied by the control profile.

See [remaining P1 work](IMPLEMENTATION_STATUS.md#remaining-before-p1-can-close),
[all requirements](REQUIREMENT_MATRIX.md), [source cases](ACCEPTANCE_MATRIX.md),
and [open conditions](OPEN_CONDITIONS.md). No merge or full-product completion
is authorized by these results.
