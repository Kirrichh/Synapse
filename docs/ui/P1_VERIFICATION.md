# P1 implementation verification

Version 1.2. Linux observations: 2026-10-18 on `codex/ui-foundation-v1`;
Windows follow-up: 2026-10-19 on `codex/pr110-windows-tr2-p1`, based on PR #110
head `330483bc33bdba1e5d3901a44abd30fe82e29fe3`. **P1 remains IN_PROGRESS.**
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

## Windows verification follow-up

Environment: Windows x86_64, Python 3.12.14, Node 24.18.0/npm 11.16.0,
Rust 1.99.1 MSVC, Microsoft C++ Build Tools and WebView2. The isolated task
checkout starts at PR #110 head `330483bc33bdba1e5d3901a44abd30fe82e29fe3`.
These observations do not replace the historical Linux results above. This is
an existing-PC debug NSIS installation, not signed release or clean-machine
acceptance. [Structured installed evidence](../../acceptance/ui_bridge/evidence/windows-installed-results.json)
identifies the actual artifacts, executed checks, observation methods and limits.

| Check | Observed result | Scope and limits |
|---|---|---|
| Python UI acceptance at the PR head | **114 passed, 1 failed, 1 warning** | OS architecture had to be restored to the restricted process environment. The unread-output case exits within its deadline with status 2, but expects LF and receives Windows CRLF on stderr. Its flood writer also reports `OSError(EINVAL)`. This historical failure is fixed and rerun in the continuation below (F-08). |
| Targeted CLI regression checks | **39 passed, 1 failed, 1 warning** | The broken-chain replay case reports integrity code 8/digest mismatch. Its isolated failure reproduces on both PR head and PR base; no new PR regression is established. |
| TypeScript acceptance / frontend build | **46 passed / build passed** | Component contracts and production assets. |
| Windows native external acceptance | **28 passed** | Elevated host PowerShell: actual canonical core through Windows Job/native byte pipes, missing-interpreter refusal, overlapping-launch refusal, ping, confirmed shutdown and two successive sessions. This run precedes the separate containment and extended wire cases below. |
| Windows containment case | **1 passed** | Elevated host: missing-SystemRoot preparation refusal; unrelated inheritable event excluded; synthetic core observed inside the named product Job; owner drop and abrupt owner death terminate core and synthetic descendant; replacement after cleanup. Does not exercise actual adapters/MCP or assignment/resume failures. |
| Extended wire cases | **10 Python / 3 Rust passed** | Exact formed bytes with Cyrillic, emoji, escaped LF/CRLF/tab/literal backslash-n, literal CRLF whitespace, fragmented and consecutive frames. Component execution, not the full corpus through the installed UI. |
| Packaging privacy acceptance | **5 passed** | Two existing bundle-tool checks and three new cases exercise removal of build provenance, bound launchers and their RECORD hashes, plus staging outside the source checkout. |
| Rebuilt canonical core | **Passed** | Isolated import/canonical entry, content inventory, and scan for local build-path bytes. Runtime package modules/data remain available. |
| Debug NSIS build and isolated installation | **Passed** | Fresh separate destination; installer success; exact installed executable transformation and every declared resource hash verified. |
| Installed process failure/replacement harness | **Passed** | Elevated host: canonical bundled command, no overlapping core, terminating a refused second shell preserves the first core, abrupt shell death terminates its core, replacement after exit, core crash leaves shell for explicit recovery. |
| Real installed window | **Observed pass for listed transitions** | Actual Tauri/WebView2 displays `CORE_EXITED` after core loss. Force-close confirmation was user-assisted because that window was elevated. A subsequent ordinary desktop launch reached ready; Stop displayed confirmed shutdown; close removed the window. No automatic restart was observed. Token elevation was not independently measured for the desktop launch. |

The original builder retained local installation provenance in `direct_url.json`
and absolute build-interpreter paths in generated console launchers. Packaging
now removes these installation artifacts and their RECORD entries before inventory
binding. Raw pip staging stays outside the checkout; only sanitized resources
enter atomic publication. No private input or raw diagnostic log is retained in
repository evidence. This fix concerns build metadata; it grants no runtime authority.

The first packaging attempt failed with a Windows file lock; rebuilding with the
application closed passed. The first installed-shell assertion required raw equality
with the build executable. Inspection established Tauri's documented NSIS marker
patch (`__TAURI_BUNDLE_TYPE_VAR_UNK` to `__TAURI_BUNDLE_TYPE_VAR_NSS`); the corrected
check requires that exact single transformation and equality of every other byte.
This changes verification, not product code or an integrity prerequisite.

Initial restricted executions could not access resources or produce the native
witness report. Later publication into directories with normal inherited access
restored resource reads, and all 30 current native cases passed, including canonical
stderr diagnostics. These repaired environment failures are not behavioral passes.
An elevated-host result alone does not establish an ordinary-user platform matrix.
Product bundle verification and containment were not bypassed.

The lifecycle and containment files must run serially without another active
Synapse session because they share the production admission mutex and named Job:

```powershell
cargo test --offline --locked --manifest-path acceptance/ui_bridge/native/Cargo.toml --test windows_lifecycle -- --test-threads=1
cargo test --offline --locked --manifest-path acceptance/ui_bridge/native/Cargo.toml --test windows_containment -- --test-threads=1
python -B -m pytest -q -p no:cacheprovider acceptance/ui_bridge/test_bundle_tool_acceptance.py acceptance/ui_bridge/test_bundle_privacy_acceptance.py acceptance/ui_bridge/test_frame_acceptance.py
cargo test --offline --locked --manifest-path acceptance/ui_bridge/native/Cargo.toml --test transport
```

Build the canonical resources first using the development guide. The containment
probe is external acceptance code, never packaged. It witnesses OS enforcement
with synthetic descendants and does not pretend to be the canonical Python entry.
The independent lifecycle and installed checks establish that entry separately.

Four focused mutation campaigns on this Windows follow-up killed **10 mutants**
with passing baselines; they are separate from the historical campaigns below:

| Campaign | Result | Seeds / evidence |
|---|---|---|
| Bundle provenance, launchers, RECORD and staging | **6 killed** | [Seeds](../../acceptance/ui_bridge/mutation/bundle-privacy.json) / [results](../../acceptance/ui_bridge/evidence/windows-bundle-privacy-mutations.json) |
| Active-Job admission | **1 killed** | [Seed](../../acceptance/ui_bridge/mutation/windows-admission.json) / [result](../../acceptance/ui_bridge/evidence/windows-admission-mutation.json) |
| Job-close termination | **1 killed** | [Seed](../../acceptance/ui_bridge/mutation/windows-containment.json) / [result](../../acceptance/ui_bridge/evidence/windows-containment-mutation.json) |
| Python/Rust newline rewriting | **2 killed** | [Seeds](../../acceptance/ui_bridge/mutation/wire-fidelity.json) / [results](../../acceptance/ui_bridge/evidence/windows-wire-mutations.json) |

The run used isolated source copies outside the repository to avoid Windows
symlink requirements; no original source was mutated. Each seed identifies the
exact replacement and detecting case. The general mutation runner below accepts
these seed files when its resource/dependency symlink prerequisites are available;
run the two Windows campaigns serially. These manually executed campaigns are
not a claim that CI schedules Windows platform runs. The Job-close mutant reached
the surviving-core assertion after the bounded wait; cleanup then terminated the
witnesses. It was a behavioral kill, not a compiler failure or runner timeout.

### Continued Windows fault and runtime verification

The owner explicitly deferred Linux execution on 2026-10-19 because this task has
no usable Linux host. It remains acceptance debt, not a waiver or a passing result.
The continuation addresses Windows; TR-2/P1 retain their wider open obligations.

| Check | Observed result | Scope and limits |
|---|---|---|
| Current Python UI suite | **119 passed, no failures or warnings** | The original exact-LF assertion is preserved. Expected EINVAL when a Windows pipe closes no longer produces a flood-thread warning. |
| Current native suite | **31 passed** | Includes real rebuilt canonical stderr into the native diagnostic reader, Windows lifecycle and containment, framing, refusal and publication boundaries. |
| External startup debugger | **6 passed** | Normal startup; rejected Job-list preparation; invalid Job handle rejected by Windows; resume refusal; owner death immediately after CreateProcessW returns and immediately before ResumeThread. No product test hooks. |
| Actual adapter/MCP descendants | **2 passed** | Actual run_process and McpToolTransport with the declared MCP 2.2.0 SDK, synthetic peers and nested children, inside the production Job. Native owner drop and abrupt death terminate all retained processes. This does not establish UI domain execution authority. |
| Focused follow-up mutations | **6 killed** | Passing startup/runtime/stderr baselines and final reruns; Job-list omission, ignored preparation failure, missing suspended flag, ignored resume failure, missing Job-close termination, and removed stderr LF control. |
| Final ordinary-user installation | **Passed** | Rebuilt debug NSIS, exact executable transformation and every installed resource hash, canonical entry, overlap exclusion, shell/core death and replacement; elevation independently false. |
| Actual installed window security | **Passed after F-11 fix** | Forbidden native commands/foreign ACK, CSP eval/inline/fetch/frame/image refusal, denied new window, and foreign navigation with zero requests at the local HTTP receiver. This does not establish every foreign privileged caller context. |
| Real Channels and crash consent | **Passed** | Missing ACK does not block heartbeat or confirmed shutdown; stale ACK refused; reload restores stopped state. Real core loss, confirmation, cancel, second confirmation and shell exit observed. |
| Request-guard policy and mutations | **Native policy case passed; 6 mutants killed** | Four admission-policy mutations and two native filter/response mutations. Native kills require an actual outbound navigation request; readiness/debugger/build failures do not count. |

[Structured before/after evidence](../../acceptance/ui_bridge/evidence/windows-followup-results.json),
[mutation seeds](../../acceptance/ui_bridge/windows_followup_mutants.json), and
[mutation results](../../acceptance/ui_bridge/evidence/windows-followup-mutations.json)
retain only reviewed fields and normalized source hashes. Raw logs, process IDs,
private paths and runtime stores are not retained in repository evidence.

The original launch left an unowned suspended child when its owner died after
CreateProcessW returned but before AssignProcessToJobObject. It had not executed,
but remained alive. The fixed path supplies PROC_THREAD_ATTRIBUTE_JOB_LIST during
creation, then resumes only after successful preparation. Membership is observed
at the returned creation boundary; death at either tested boundary terminates the
child. Removing the Job-list attribute reproduces the leak and kills the mutant.
This is F-10; it is not a claim about every possible interruption inside Windows.
The mechanism follows [Microsoft's Job-list attribute contract](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute).

The Windows diagnostic producer now emits one LF delimiter explicitly. The real
canonical bundle's INVALID_LENGTH code survives the native stderr reader. Removing
the producer setting restores the original exact-byte failure. The reader's
strict diagnostic allowlist was not relaxed (F-08).

Run the external probes serially, after building the acceptance binaries, with
scratch and result paths outside the source checkout:

```powershell
cargo build --offline --locked --manifest-path acceptance/ui_bridge/native/Cargo.toml --bins
python -B acceptance/ui_bridge/windows_startup_debugger.py --executable <startup-probe.exe> --output <startup-result.json>
python -B acceptance/ui_bridge/windows_runtime_descendants.py --executable <runtime-probe.exe> --scratch <scratch-directory> --output <runtime-result.json>
python -B acceptance/ui_bridge/windows_followup_mutations.py --workspace <scratch-directory> --cargo <cargo.exe> --output <mutation-result.json>
```

The runtime probe requires the declared agent-mcp extra in its development Python.
Its native executable is an acceptance host for real existing launch owners, not
a replacement packaged core. It never enters the shipped resources or UI routes.

The subsequent [release and boundary continuation](#additional-windows-release-and-boundary-acceptance)
adds installed control-pipe, foreign-caller and release evidence. Clean-machine
and wider platform coverage remain open. Linux execution is deferred debt. JCS and domain-owner contracts remain
unimplemented by this control profile. TR-2/P1, OC-01/09/32 and F-07 are not closed;
all 44 complete source-case verdicts remain NOT_RUN.

### Windows navigation request boundary (F-11)

The original installed window kept its packaged origin after a denied navigation,
but the synthetic HTTP receiver observed one GET with destination `document` and
mode `navigate`. The separate new-window probe made no request. No private input
was sent. Navigation cancellation alone therefore did not establish SEC-5/OC-22.
[Microsoft documents this timing](https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/icorewebview2navigationstartingeventargs?view=webview2-1.0.4191.47).

Windows now creates an empty hidden WebView, installs native WebResourceRequested
interception for every resource context and request source kind, then navigates
to packaged content. Only the configured Tauri asset and IPC origins reach their
local handlers. Other requests receive a local empty 403 before network dispatch.
Missing interception support/setup refuses startup; failure to apply a denial
closes the WebView and terminates its owner. There is no permissive fallback.
CSP, caller checks and navigation/new-window restrictions remain in force.
The native callback does not re-enter Tauri's locked WebView dispatcher.
The approach follows [Microsoft's request interception sequence](https://learn.microsoft.com/en-us/microsoft-edge/webview2/how-to/webresourcerequested).

The fixed real window and final installed artifact preserve the origin with
**zero HTTP requests**, while heartbeat, IPC, shutdown and crash consent pass.
[Request-guard mutations](../../acceptance/ui_bridge/evidence/windows-network-mutations.json)
record passing baselines/final reruns, four rejected weakened URL policies and
two real native variants whose missing filter/response each leaks the request.
The external observer keeps CSP enforced and uses per-process loopback CDP only;
it neither changes the shipped configuration nor adds product test hooks.
Unsupported WebView2 versions and arbitrary COM failures are not an executed
environment matrix; this is not a firewall for unrelated browser/OS traffic.

Reproduce with scratch outside source, the built EXE and its verified core bundle:

```powershell
python -B acceptance/ui_bridge/windows_network_mutations.py --prepare --workspace <private-variants> --cargo <cargo.exe> --baseline <built-desktop.exe> --output <mutation-result.json>
python -B acceptance/ui_bridge/windows_network_mutations.py --workspace <private-variants> --node <node.exe> --output <mutation-result.json>
node acceptance/ui_bridge/windows_installed_security.mjs --executable <installed-desktop.exe> --workspace <private-scratch> --output <security-result.json>
node acceptance/ui_bridge/windows_installed_security.mjs --executable <installed-desktop.exe> --workspace <private-scratch> --output <consent-result.json> --scenario crash --python <development-python.exe>
```

### Additional Windows release and boundary acceptance

The 2026-10-19 continuation tests the unchanged product implementation from
`1df84ff` on Windows x86_64 build 26200 with WebView2 155.0.4283.62. It adds
acceptance and evidence, not another product implementation. The complete
[release record](../../acceptance/ui_bridge/evidence/windows-release-results.json)
binds the executable, installer and resource inventory.

| Check | Result | Actual scope |
|---|---|---|
| Python UI acceptance regression | **155 passed** | Includes the new control-pipe corpus; no full-repository claim |
| Installed canonical endpoint through production native pipes | **51 passed** | The external relay calls the actual Windows launcher with the verified installed bundle. Fragmentation, Unicode/escapes, exact response frames, 1024/65536-byte boundaries, every invalid shared-corpus entry, corruption/no resynchronization, EOF, handshake and unread-output deadlines |
| Native pipe byte witness | **Passed** | Nine payloads across three fragment sizes, including every byte value and header LF/CR/SUB boundaries, plus five damaged-stream cases. A synthetic external child establishes exact bidirectional bytes and separate stderr; it is not the canonical core |
| Optimized release EXE and NSIS | **Built and installed** | Ordinary user; exact NSIS marker transformation and every resource hash verified. The resume run reverified 12 identity/process checks after the initial successful installation |
| Actual installed release window | **26 passed** | CSP, ungranted commands, foreign ACK, navigation with zero observed requests, real stalled Channels, renderer recovery, and additional script/style/object/form/Blob Worker/WebSocket probes |
| Runtime dependency isolation | **Passed** | The release window reaches Ready with empty PATH and synthetic PYTHONHOME/PYTHONPATH; this does not emulate a clean OS without WebView2 |
| Actual release crash consent | **7 passed** | Owned canonical core loss, visible recovery, cancel preserves the shell and confirmed force-close exits |
| Corrupt transport in the installed release window | **7 scenarios, 35 assertions passed** | Zero/oversized/maximum lengths, invalid UTF-8/JSON, and partial header/body at EOF enter the owned core stdout pipe. The real renderer shows failure/recovery and never reports successful shutdown |
| Actual native foreign caller contexts | **Two contexts refused all five commands** | External topology copies reach the unchanged native handler with a real foreign window label or foreign origin. Their capability/navigation accommodations are confined to private copies; the installed topology remains intact |

The installed protocol refuses arbitrary domain parameters. Their correlated
refusals prove parsing/rejection and exact response frames, not echo fidelity of
unimplemented domain replies. Exact arbitrary bytes are established separately
by the native byte witness. A separate Windows x64 observer duplicates only the
verified owned core's stdout pipe and writes a bounded synthetic frame; it does
not modify the installed files or add a product hook. The five malformed-frame
cases produce `PROTOCOL_ERROR` in the installed release window. Both partial-EOF
cases produce `CORE_EXITED`: process-exit observation may precede the framing
reader, so these GUI cases establish visible failure, while the native byte
witness separately establishes partial-frame rejection and no resynchronization.
These observations do not close full AC-25 or any complete domain case.

The new detecting mutations are recorded separately:
[four installed-corpus controls](../../acceptance/ui_bridge/evidence/windows-installed-corpus-mutations.json),
[two native endpoint controls](../../acceptance/ui_bridge/evidence/windows-native-byte-mutations.json),
and [two actual caller controls](../../acceptance/ui_bridge/evidence/windows-caller-contexts.json).
The corpus/native campaigns have passing baselines and final reruns. Caller
results combine independently successful runs of the same identified binaries:
label evidence from the first run and origin evidence from the default-port run.
The initial origin mutant survived behind the independent non-default-port
refusal; a standard-port loopback case detects it. The later label-mutant attempt
failed before establishing IPC context and is retained as infrastructure failure,
not counted as a kill. No meaningful survivor is hidden by this consolidation.

Two observer defects were also corrected without relaxing product assertions:
a cross-origin Worker was rejected before CSP, so a Blob Worker now tests CSP;
and the byte witness drains stderr concurrently so a misdirected stdout is
observed instead of blocking its own reader. The initial timeout is inconclusive.

Release artifact SHA-256 values:

| Artifact | SHA-256 |
|---|---|
| Built EXE | `f28982bd9f447152f605bc9791f12744a78be571c1c5aa116a8a3f098b09987d` |
| Installed EXE after exact NSIS patch | `c6574a80fd5157ed90b53766592c6224a89ba53ae01b9b692742e673be358820` |
| NSIS installer | `c05d6aaa2297108c0d2ac68eb5276f3f93f6329d51e9cc8ed81ac720f1c01563` |

The package is **unsigned**. Clean-machine dependencies, other supported
Windows/WebView2 versions and signing remain release-matrix work. Supported
Linux execution remains owner-deferred. JCS/parameter binding, owner integration,
domain readiness/recovery/delivery and complete source scenarios remain required
implementation and acceptance work. TR-2/P1 stay IN_PROGRESS.

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
The Windows follow-up above adds targeted lifecycle and byte-fidelity mutations.
The later continuation adds startup-fault and actual-runtime mutations. Negative
native WebView/capability/CSP acceptance remains separate from these results.

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
imports/resources from acceptance. The Windows continuation now executes the
listed installed-window scenarios and records the F-11 before/after result. Static
configuration and browser injection alone remain insufficient evidence.

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

The F-06 amendment itself changed documentation only. Its mutation status is
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

- The earlier Linux native-window attempt failed to start WebKitNetworkProcess
  at its distribution path. No Linux native IPC/CSP or installed UI pass is recorded.
- Linux direct-launch refusal is executed. A supported systemd user service,
  cgroup cleanup after shell death, real adapter descendants and replacement
  ordering are **not run** here. The owner explicitly deferred Linux execution.
- Windows installed control, core/shell death, replacement, synthetic descendants,
  one inheritance sentinel and the extended wire cases have the scoped evidence
  above. The continuation adds six real startup fault/boundary cases and actual
  adapter/MCP descendant cleanup. It does not establish UI domain authority or
  every supported environment.
- Windows installed CSP, ungranted commands, foreign ACK, navigation request
  denial, force-close cancel/confirm and stalled real Channels now have executed
  evidence. The additional continuation reaches actual foreign native contexts in
  explicitly weakened external topology copies; the full supported platform matrix
  remains unverified.
- The Windows stderr CRLF failure is resolved at the producer and verified through
  the rebuilt canonical stderr/native reader, with a detecting mutation.
- JCS parameter hashing, domain command ownership/recovery, domain delivery classes
  and the later UI workspace/features are not supplied by the control profile.
- Both debug and optimized release NSIS now run on this existing PC. The unsigned
  release does not establish signing, clean-machine dependencies, other supported
  Windows/WebView2 versions or complete release readiness.

See [remaining P1 work](IMPLEMENTATION_STATUS.md#remaining-before-p1-can-close),
[all requirements](REQUIREMENT_MATRIX.md), [source cases](ACCEPTANCE_MATRIX.md),
and [open conditions](OPEN_CONDITIONS.md). No merge or full-product completion
is authorized by these results.

## Invocation owner component review

The pending command foundations are not connected to the canonical machine
profile or installed renderer. This is component evidence from the Windows
working tree, not a new installed release result and not P1 completion.
The [development record](DEVELOPMENT_NOTES.md#2026-10-19--owner-boundaries-and-incomplete-command-foundations)
defines ownership, the current review gap, affected requirements and remaining
integration work.

| Check | Observed result | Scope and limits |
|---|---|---|
| Invocation owner acceptance and existing execution retention | 40 passed | Real SQLite cancellation/receipt/projection transactions, deterministic concurrent snapshot cut, scoped replay/status, original terminal bytes/evidence, read-only refusals and bounded change retention. Includes the existing four execution-retention cases. |
| Existing mutation-report confidentiality acceptance | 5 passed | Separate tooling evidence; no raw subprocess diagnostics retained in reports. |
| Existing Gold architecture, dependency direction and canonical entry | 884 passed | Three targeted suites; no whole-repository or new machine-route claim. |
| Owner seeded mutation round 1 | 22 killed, 1 survived | The missing digest guard escaped the original malformed-receipt fixture because semantic validation refused it independently. |
| Owner seeded mutation final round | 23 killed | Strengthened the digest-specific case and repeated passing baselines and all mutants; no invalid/timeout result counted as a kill. |

[Mutation seeds](../../acceptance/ui_bridge/mutation/invocation-owners.json) and
[reviewed structured results](../../acceptance/ui_bridge/evidence/invocation-owner-mutations.json)
retain source hashes, selected cases and the survivor resolution. The Windows
runner uses isolated source copies and the existing shared mutation executor.
Run from the repository root, with an external writable scratch directory:

```text
python -B -m acceptance.ui_bridge.mutation.invocation_owners --workspace ../invocation-mutation-work --output ../invocation-mutation-results
```

The digest mutant was also reproduced through this checked-in runner after the
full campaign. The final acceptance rerun passed 45 cases: 40 owner/regression
cases and five reporting cases. No new native or installed pass is inferred.
Actual user-bound Windows protected storage, three-language mutation coverage,
trusted scope resolution, machine routing and all delivery classes remain open.

Earlier draft-component runs observed 34 Python, two Rust and 25 TypeScript JCS
checks passing. They are not the mutation evidence or integrated COMP-6 closure.
The native intent-store run observed four acceptance-protector cases passing
and one actual Windows DPAPI case failing in the restricted process environment
(`CryptProtectData` returned Win32 error 2). That platform failure is retained as
a failure; no plaintext fallback or passing protection claim replaces it. A real
ordinary-user run is still required after the native changes are finalized.

## Protected command component continuation

This continuation supersedes the outstanding Windows component-protection check
above; it does not replace the recorded earlier failure or establish installed
command recovery. The unchanged control v1 endpoint still rejects domain commands.

The product contracts are
[`binding.schema.json`](../../synapse/commands/binding.schema.json) and
[`reconciliation.schema.json`](../../synapse/commands/reconciliation.schema.json).
New command JCS is bounded to 65,536 UTF-8 bytes and 32 container levels. Numeric
source spelling must preserve its decimal value through binary64 and ECMAScript
serialization; higher precision is represented as a string. Existing evidence
serialization and integer-only control v1 remain separate contracts.

The Windows native checks now use production DPAPI in a host process outside the
restricted agent sandbox. They verify ciphertext corruption refusal, persisted
identity across reopening, recovery without a renderer draft or write, exact
binding, scoped absence, historical replay and refusal of substituted responses.
A fixed independently serialized binding vector is checked by Python and Rust.
The earlier sandbox DPAPI failure remains a historical environment failure;
no passing test-protector result is substituted for actual protection.

| Final check | Observed result | Scope |
|---|---|---|
| Python command JCS and invocation-owner acceptance/regression | 88 passed | Includes malformed status queries before journal access and identifier/digest terminators; targeted run, not a full repository run |
| Python admitted-runtime drift | 3 failed before the fix; 3 passed after it | Fresh interpreters and physical isolated runtime copies: changed command code/schema and a self-consistent declaration omitting the JCS dependency are refused |
| Native command components on Windows | 11 passed | Two JCS, five intent-store and four recovery cases, including production DPAPI and the final shared schemas |
| TypeScript JCS | 26 passed | Shared raw corpus, canonical order/numbers, arrays with hidden properties/accessors, bounds and fixed refusals |
| TypeScript type checking | Passed | Maintenance check, separate from behavioral acceptance |
| Final command and runtime-admission mutations | 41 + 3 killed | Passing selected baselines, including the two recovered limit-test families and all three newly covered admission paths |
| Invocation-owner mutation rerun | 23 killed | Final owner source and selected acceptance; historical earlier rounds remain retained separately |

The native and mutation continuation results are recorded in
[structured component evidence](../../acceptance/ui_bridge/evidence/command-foundation-mutations.json).
The first command-foundation campaign killed 30 mutants and left four meaningful
survivors: Python/Rust depth and byte limits. Their cases incorrectly followed
mutated implementation constants. Independent released-boundary cases close that
gap; the first round is retained, not overwritten as a passing observation.

Reproduction uses isolated external scratch/output directories and an existing
toolchain. It never mutates the working checkout or installs dependencies:

```text
python -B -m acceptance.ui_bridge.mutation.command_foundations --workspace ../command-mutants --output ../command-results --cargo <cargo-executable> --node <node-executable>
cargo test --offline --locked --manifest-path acceptance/ui_bridge/native/Cargo.toml --test jcs --test intent_store --test command_recovery
node --experimental-strip-types --test acceptance/ui_bridge/typescript/jcs.test.ts
```

The status query is read-only. Its scope remains an input from an as-yet
unimplemented trusted application boundary, not permission granted by knowledge
of an object ID. Application IPC, negotiated domain transport, delivery classes,
new installed acceptance and the full documentation review remain unfinished.

## Primary-source and packaging follow-up

The [research record](DEVELOPMENT_NOTES.md#2026-10-19--primary-source-review-before-application-integration)
compares the command, authority, storage, schema and delivery requirements with
their primary engineering sources. It is not an acceptance result by itself.

F-16 was reproduced in the Windows build path: CPython test extensions entered
the bundle because the DLL tree bypassed the library exclusion policy. The
builder now filters both trees and excludes the upstream test-only extensions
whose names do not start with `_test`. Two affected packaging cases failed
before the change; all five cases in the two affected suites pass afterwards.
Three isolated mutants are killed, including a control that wrongly removes
the required ctypes extension. Actual staged CPython passes isolated-prefix,
ctypes, SQLite query and TLS-context checks without the test extensions.

Two separate native Windows cases pass for directory lifetime and exclusion
before creation. Their two final mutants are killed. The first lifetime mutant
removed a byte-range lock but retained its file handle and survived; the corrected
mutation discards the lifetime handle. No product storage change was justified
by those observations. Initial path-admission races and same-user isolation
are not established by these cases.

The initial packaging mutation run stopped at an invalid baseline: its isolated
copy lacked LICENSE. It counts as no success. After correcting the copy inputs,
the baseline and all three mutants completed. See the
[reviewed follow-up evidence](../../acceptance/ui_bridge/evidence/source-review-checks.json).
The older component campaigns retain their original scope and counts.

The canonical core was rebuilt from the current working tree into external
staging, including the command package. Every manifest-listed file hash verifies;
native test extensions are absent. Manifest SHA-256:
`76d22d6065250d024fab4a140b00d3ec3f39996793c5fec9e07c1f267745b7e5`.
All 36 canonical pipe-corpus cases pass against that actual bundled interpreter.
The first attempt from the agent sandbox could not access the host-created
directory and failed before product launch; it is an environment failure, not
product evidence. The successful rerun used a host process. This is core-bundle
acceptance, not an updated installed shell or native-launcher result.

The full Gold ingestion document has now been read. The broader historical
document inventory still has unread entries. P1 application integration,
delivery classes, fresh desktop/installed acceptance and native Linux verification
remain open; this follow-up does not close a stage or a complete source scenario.

## Delivery and decoder continuation

The bounded delivery scheduler is integrated with the canonical control writer.
It separates control capacity from projections, bounds subscriptions, schedules
fairly, marks Live loss, invalidates overflowing StateSync epochs for resnapshot,
and requests rereading for Evidential loss. This is queue behavior, not complete
authorized application subscriptions or owner acceptance. The control profile
still advertises `domain_ready: false`.

F-17 reproduced equal duplicate JSON keys passing the frontend's third-party
duplicate callback. A lexical uniqueness check now precedes both frontend
decoders; the same expanded raw corpus is checked in Python and Rust. Generated
command validators use the owner schemas and explicit UUID format checking.

| Check | Observed result | Boundary |
| --- | --- | --- |
| Python delivery, transport, JCS and control corpus | 102 passed | Final selected component suites |
| Frontend TypeScript acceptance | 91 passed | All current frontend acceptance files; type checking also passed separately |
| Native Windows command/protocol components | 16 passed | Real DPAPI, intent directory/store, recovery, protocol and JCS |
| Installed canonical core pipe corpus | 39 passed | Actual updated bundled Python through canonical machine entry |
| Delivery and transport mutations | 17 killed | Separate final campaigns of sixteen queue controls and one transport-close control |
| Command decoder mutations | 13 killed | Final campaign after adding missing escaped-quote/backslash duplicate refusals |
| Updated ordinary-user NSIS installation/process checks | 16 passed | Exact shell/resource identity, containment, replacement and core crash |
| Installed release security/Channels | 26 passed | CSP, denied commands, zero outbound navigation requests, ACK loss and recovery |
| Installed crash consent | 7 passed | Actual core crash; cancellation preserves the shell; confirmation closes it |

The first executable decoder campaign killed twelve mutants and left the
escaped-quote scanner mutant alive. Added independent refusal cases kill it in
the final thirteen-mutant campaign. An earlier loader-URL failure had an invalid
baseline and supplies no success. These observations are preserved in
[reviewed structured evidence](../../acceptance/ui_bridge/evidence/delivery-decoder-review.json).

The rebuilt release shell SHA-256 is
`8e543ebb923705d40125e3f4f686b82ada4f4a82e21cb98069e384f44bade656`;
the exact NSIS-installed shell transformation has SHA-256
`6b6774bde319cc4efca5e73abb0addcdca20e4066ed7bbe10494500e31d8d87d`.
Installer SHA-256:
`9309167d5597debcbb7cf8c8b3adf3a8b204db96e4b2374ea5e2537e3043845a`.
Core inventory SHA-256:
`710b1b6e65be98c00ca030c01270a8c391bba54f8455499eb7ddf85fc147ba56`.
Every installed inventory member verifies. CPython native test extensions are
absent; ctypes, SQLite query and TLS-context probes pass. This supplies the
previously missing fresh installed evidence for F-16 on this Windows PC.

These checks ran as an ordinary Windows user using host execution, without a
manual PowerShell handoff. They do not establish clean-machine, signed-release,
wider-version or native Linux acceptance. The full repository suite was not
rerun. Application authorization, domain integration, protected end-to-end
restart recovery and full class delivery remain unfinished. The newly approved
[access contract](WORKSPACE_ACCESS_CONTRACT.md) resolves the design choice;
it is not itself a passing implementation result.

## Protected access and native invocation integration

The complete branch document inventory was read before this continuation. The
[access contract](WORKSPACE_ACCESS_CONTRACT.md) now has a core authority, canonical
administrative CLI, protected Windows registry, signed application sessions and
native durable invocation routing. The Windows host check passed real UAC
administration and ordinary-user OS write refusal; its isolated state was cleaned.

Twenty-five selected Python cases pass. Native application/identity/protocol and
controller acceptance passes (3/2/2/3), including actual Windows credential
persistence and a real Rust-to-Python cancellation whose reply is lost before
restart recovery. These tests use a synthetic access registry and the production
invocation owner. They do not establish installed canonical composition.
The actual Tauri command/capability additions compile. Twenty domain/native
behavioral mutants are killed; the earlier twenty access-owner mutants are also killed on this tree.
See [reviewed evidence](../../acceptance/ui_bridge/evidence/access-native-review.json).

No stage or complete source scenario closes here. The new application handlers
still need renderer/channel delivery, current installed evidence and the remaining
platform boundary checks. Historical release hashes above identify earlier
artifacts and must not be presented as builds containing this continuation.

## Windows application and delivery integration

The previously open invocation integration is now exercised through the actual
installed release: ordinary native identity, separate UAC administrative issuance,
canonical core authentication, scoped source delivery, owner acceptance and cold
protected recovery. The shipped React view reads a fresh StateSync snapshot,
retained invocation facts and a separately labelled Live connection observation.
It does not infer that a retained invocation is still running.

The core owns access, actual object membership, subscription identity/filter,
generation and epoch. Collection and consumption have separate bounded steps;
delivery rechecks authority while retaining read custody through its bounded
reply. Native Channel replacement closes old source subscriptions. The native
controller separates application admission from stop/close capacity and refuses
replaced queued tickets before owner dispatch. Accepted work and protected intent
bindings survive delivery loss; only the domain owner decides acceptance/results.

| Final check | Result | Scope |
| --- | --- | --- |
| Selected Python integration regression | 148 passed | Access, Windows resource custody, owner/domain/subscription, JCS, commands, queues and transport before the location correction |
| Windows location/resource regression | 12 passed | Final location correction, minimal/forged environment, configured paths and actual object custody |
| Canonical CLI and bundle/privacy checks | 18 passed | Final source and packaging behavior |
| Complete native acceptance crate | 58 passed, none ignored | Current Windows source/resources, credentials, protected intents, control/application queues, protocols, containment and lifecycle |
| Frontend acceptance | 100 passed | All current TypeScript acceptance files; generation/type checking/build also pass |
| Installed canonical endpoint through native pipes | 52 passed | Final installed core and actual native launcher; control corpus and explicit application-readiness refusals |
| Installed access/owner/recovery sequence | 15 passed | Real issuance/discovery, shipped UI, foreign object refusal, lost ACK, abrupt shell loss, original identity/receipt recovery and current revocation |
| Installed artifact/process checks | 16 passed | Exact NSIS shell transformation and every core inventory member; overlap, owner loss, replacement and core crash |
| Installed CSP/network/Channels | 18 passed | No unauthorized native capability, no outgoing navigation request, bounded ACK loss and independent heartbeat/shutdown |
| Installed crash consent | 7 passed | Visible core loss, cancel preserves shell, confirm closes it |
| Installed malformed transport scenarios | 7 scenarios, 35 assertions passed | Five malformed frames and two partial-EOF cases; visible failure, never confirmed shutdown |
| Actual foreign native label/origin | Both baselines pass; both filter mutants killed | All ten current native commands, in external topology copies; not weakened installed binaries |

The final seeded campaigns have passing baselines and kill all 27 subscription/
delivery variants, 20 domain/native variants, 11 application-Channel variants and
4 Windows-location variants. These are separate from earlier command/owner/access
campaigns. Four initial subscription survivors were closed with behavioral cases:
invalidation before consuming its recovery signal, a forbidden method that would
otherwise be executable, and valid snapshots with mismatched grant/filter identity.
An intermediate native baseline failure and a stale domain mutation site do not
count as successful evidence. Final serial campaign evidence records their reruns.

Installed verification found F-18: the Shell known-folder API depended on
SystemDrive in the caller's environment, absent from the intentionally minimal
native child. Ordinary inherited-environment reads passed while the installed
core refused. The new location adapter reads the machine's ProgramData setting
and expands only the Windows API's system drive. The actual installed session
now authenticates successfully; caller environment overrides cannot reroute it.

A separate harness error created the synthetic owner under elevation, producing
administrator-private evidence ACLs. The application's refusal was correct.
Preparation now runs as the ordinary owner; the elevated process only registers,
grants and revokes. No product permission was relaxed. Both processes use the
installed canonical access CLI/core; the temporary default access registry is
created only when absent and is removed after the sequence. Existing registries
are never adopted or replaced by this acceptance.

The final sequence proves that an actual shared read transaction in one process
blocks administrative revocation in another; once revoked, queued protected
evidence and saved receipt recovery are denied without restarting the app.
The accepted cancellation remains durable and is not undone by access revocation.
Artifact identities, sanitized verdicts, source hashes, mutation descriptions and
investigation history are in [reviewed application evidence](../../acceptance/ui_bridge/evidence/application-delivery-review.json).
Raw logs, public registration material, private paths and synthetic runtime stores
remain outside the repository.

This closes the prior Windows invocation-application integration gap, not the
full multi-platform stage. Native Linux remains owner-deferred; its protected
application storage/access adapter is unavailable in this Windows implementation.
Clean-machine provisioning, signing and wider Windows/WebView2 qualification
remain open. P2 continuous multi-domain synchronization, later command owners,
trusted graphical administrative confirmation and F-05 are not inferred from
these results. The full repository suite was not rerun; the earlier independently
reproduced Windows replay failure remains recorded without weakening its test.
