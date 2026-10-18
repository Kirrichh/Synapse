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
| Python UI-bridge acceptance | **110 passed** | Framing, strict JSON, shared corpus, state machine, real canonical CLI, fixed negotiation deadline, unread-output deadline, transport failure, bundle tooling, installed wheel import isolation |
| Evidence-tool confidentiality acceptance | **5 passed** | Synthetic diagnostic canaries are not persisted or printed for killed/surviving/invalid/timed-out mutants or a failed baseline; separate from product acceptance |
| Native external acceptance crate | **30 passed** | Protocol/context binding, raw transport, bundle integrity, diagnostic redaction, caller checks, publication acknowledgment/isolation, real bundled-core negotiation and controller lifecycle, dead-writer/overload distinction, correlated refusals and shutdown acknowledgment boundaries |
| TypeScript external acceptance | **46 passed** | Same raw corpus, bootstrap validation, byte/depth/Unicode limits, session/subscription/sequence filtering, fresh-observer recovery, declared refusal projection, safe native diagnostic codes |
| Browser/component scenarios | **Passed** | Actual renderer in browser refusal state; RU/EN/focus; external peer exercises readiness, stale/foreign delivery, stop, forced-close confirmation, corrupt-channel closure, fresh subscription recovery without reload/spawn, failed ACKs, same-sequence recovery, native action eligibility, retry and BUSY presentation |
| Existing CLI regression checks | **40 passed** | Interpreter startup, debugger CLI, canonical execution paths; canonical help parity plus restored thin-adapter delegation, argument ownership and exit propagation |
| Existing ownership/canonical architecture checks | **906 passed** | Four existing manifest-related suites, including the actual composition-root constraint and DAG checks |
| Frontend production build | **Passed** | Schema generation, strict TypeScript, standalone bundled validators, Vite assets |
| Native Linux debug application build | **Passed** | Tauri application with packaged custom protocol, configured production CSP, bundled core resources and build-bound manifest |
| Windows native-core cross-check | **Passed** | `cargo check --no-default-features --target x86_64-pc-windows-gnu`; compilation only, no Windows execution or installed WebView claim |
| npm production dependency audit | **0 reported vulnerabilities** | `npm audit --omit=dev`; registry advisory result for this dependency snapshot, not a whole-product security proof |

The real-core/controller cases were rerun against the final rebuilt core resource.
The combined affected Python run passed **1061 cases**: 115 UI/tooling, 40 CLI,
and 906 existing ownership/canonical architecture checks; no skips or failures.
This is a related-suite run, not a whole-repository run.
The final Python invocation passed all 115 UI cases: 110 product/build checks and
five evidence-tool confidentiality checks.
The native application itself has not passed a real-window or installed run here.
Artifact digests are retained in the external
[build identity record](../../acceptance/ui_bridge/evidence/build-identity.json).

The [preview](assets/bootstrap-browser.png) is captured from the actual renderer,
displaying `DESKTOP_REQUIRED` in a browser. It is not a simulated ready session,
the original prototype, or evidence of a running installed Tauri window.

## Automatic affected checks

The Stage 4 workflow now handles push and pull-request selection, including calls
to the existing durable/version jobs and the new UI component workflow. Existing
Gold matrix catalogs stay beside their owning commands; only affected rows and
pytest files execute. New Python acceptance is discovered rather than requiring
an ever-growing duplicate test list.

The selector uses Python imports, package initialization, local helpers, parent
fixtures, embedded subprocess imports, declared governance-data readers, explicit machine/packaging dependencies,
and shared UI contract boundaries. A Python dependency-environment change affects
all automatic Python consumers; a package-data-only change follows packaging
consumers. New dynamic loading or non-import dependencies must be represented in
the selector when introduced. Provider tests requiring a live service retain
their existing separate execution policy.

| Change | Scheduled behavior |
|---|---|
| Renderer source | Renderer build, TypeScript/browser acceptance, affected renderer mutants |
| A single existing acceptance file | Its existing job/matrix row and its related mutation cases |
| A new Python acceptance file | Its own job when no existing group owns it |
| Shared CLI | Existing direct/transitive CLI consumers, including subprocess scenarios |
| Ownership manifest or unregistered governance JSON | Declared ownership readers and their transitive consumers; no unrelated product-wide fallback |
| Shared control profile/schema | Machine acceptance plus native and renderer contract consumers |
| Unreferenced development notes | No product test execution |

Feature pushes and PRs use the cumulative merge-base comparison. Renames select
both ownership boundaries. Missing comparison data fails the selector; failed,
cancelled, or unexpectedly skipped selected jobs fail the existing Gold gate.
An entire cancelled workflow does not start the final gate. A failed selector or
missing/invalid plan in an active workflow fails explicitly instead of attempting
to parse empty output or treating absent checks as success.
The build/test layer does not enter product imports or packaged runtime data.

Local verification of the final selection behavior: **42 acceptance cases passed**
and **27 seeded tooling mutants killed**. The existing **115 Python UI cases**,
the browser/component scenarios using the isolated CI server, and actionlint
validation also passed. All three reporting-confidentiality mutants were killed
again on the review-correction tree.
These results are local verification; GitHub workflow outcomes are reported by
the PR checks, not inferred from this document. P1 platform gaps remain open.

```bash
python scripts/ci/select_tests.py --base origin/main
python -m pytest -q -p no:cacheprovider acceptance/ci
python acceptance/ci/mutate.py
```

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
| Python, bundler, distribution boundary | **33 killed** | [Results and survivor resolution](../../acceptance/ui_bridge/evidence/python-mutation-results.json) |
| Native protocol/controller/security components | **43 killed** | [Results](../../acceptance/ui_bridge/evidence/native-mutation-results.json) |
| TypeScript, contract generation, rendered behavior | **26 killed** | [Results](../../acceptance/ui_bridge/evidence/renderer-mutation-results.json) |

Final executed total: **102 killed, 0 unexplained survivors, 0 invalid results**.
The separate reporting campaign killed all three mutants; the CI selector campaign
killed all 27 tooling mutants. Each retained product record matches its final
source SHA-256. The review correction cases specifically kill the missing-manifest
edge and broken-reconnection mutants that the earlier campaign did not include.
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
do not inflate the 102 product/build mutation results above. Use the same runner
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

## F-06 mechanism decision

The owner-approved [TR-2 amendment](SOURCE_MANIFEST.md#approved-semantic-amendments)
resolves the mandatory plugin-API discrepancy. Native byte pipes remain the
single launch/transport mechanism per platform. Canonical entry, byte fidelity,
bounded I/O, containment before core execution, and failure cleanup are required;
no fallback launcher is authorized.

This follow-up changes documentation only. Executable-change mutation status is
**NOT_APPLICABLE**; the earlier executed results above are retained at their
original scope, not claimed as new runs. The
[acceptance obligation table](ACCEPTANCE_MATRIX.md#native-transport-and-launch-obligations)
maps component evidence and remaining byte/platform cases. F-06 resolution does
not close TR-2, OC-01/09/32, F-07, any complete source case, or P1.

Documentation maintenance passed: local links and heading targets, table/fence
structure, English additions, whitespace, preserved normative/case identifiers,
and the original prototype digest. Reviewed the cited Tauri Command and Windows
creation/Job-assignment documentation; all three public sources returned HTTP
200. These checks validate the amendment record, not runtime conformance.
The selector returns no product or tooling suites for this documentation delta
alone. The PR still selects the consumers of its cumulative executable changes.

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
- TR-2's mechanism choice is resolved as F-06. The complete byte-preservation
  variants, startup-fault/handle/descendant behavior, and desktop/installed path
  still require the evidence recorded in the acceptance obligation table.
- JCS parameter hashing, domain command ownership/recovery, domain delivery classes,
  and the later UI workspace/features are not supplied by the control profile.

See [remaining P1 work](IMPLEMENTATION_STATUS.md#remaining-before-p1-can-close),
[all requirements](REQUIREMENT_MATRIX.md), [source cases](ACCEPTANCE_MATRIX.md),
and [open conditions](OPEN_CONDITIONS.md). No merge or full-product completion
is authorized by these results.
