# UI development notes

Version 1.0. Started on 2026-10-18. This is the single chronological notebook
for observed behavior, implementation decisions, problems, and follow-up work.
The specification remains authoritative; the matrices record completion evidence.

## 2026-10-18 — Affected checks on push and pull request

- Existing Gold jobs previously ran regardless of changed files; the UI acceptance
  suites were not connected to CI. The change now selects their affected owners
  and preserves existing Python versions, SDK dependencies, and heavy-file shards.
- Python selection follows imports, parent fixtures, subprocess snippets, shared
  machine data, and packaging inputs. The explicit lazy machine dispatch does not
  make other CLI commands consumers of machine-only implementation changes.
- Existing fixed pytest invocations are filtered before execution. Matrix rows
  without affected cases are omitted. Newly added Python acceptance files have an
  automatic independent job when no existing group owns them. Live-provider and
  external PostgreSQL acceptance retain their separate workflows.
- The current PR also changes the shared CLI and package metadata. Its affected
  legacy consumers therefore extend beyond the three initial CLI smoke files.
  This is different from an isolated renderer change, which selects the renderer.
- Feature pushes compare the complete branch against its base, so a later notes
  commit cannot cancel and replace code verification with an empty selection.
  Default-branch pushes use their actual before/head comparison and separate run
  concurrency. An unavailable comparison fails selection instead of claiming a pass.
- Only selection and CI execution support changed in this follow-up. Product
  behavior, Windows platform acceptance, and installed-window claims are unchanged.
  Static dependency edges need maintenance when new dynamic owners are introduced.
- The first remote run exposed an editable-install finder leaking the checkout's
  desktop namespace into the isolated wheel probe. UI jobs now install the normal
  distribution, so the acceptance probe observes packaged imports. The isolation
  assertion remains unchanged; editable development installation is not wheel
  isolation evidence.

## 2026-10-18 — P1 implementation begins

- The canonical `python -m synapse` entry exists; a machine subcommand, Tauri
  shell, and production React application do not yet exist at the starting point.
- Implement the bounded control protocol first, then connect the desktop shell.
  A negotiated control session must never imply that task execution, domain
  recovery, once-grants, or project subscriptions have been implemented.
- Continue on `codex/ui-foundation-v1` and update existing PR #110. The owner
  alone performs merges; agents cannot merge locally, remotely, or automatically.
- This Linux workspace has no running systemd user manager. It can establish
  explicit unsupported-environment behavior, but cannot establish the supported
  Linux containment guarantee. Windows lifecycle evidence needs Windows execution.
- Python acceptance tools are being installed in an isolated workspace virtual
  environment. Rust and native WebView build dependencies were absent initially.
- The supplied prototype can be rendered in Chromium as a design reference.
  A reference image must be labelled as a prototype, not installed-product proof.
- Keep acceptance drivers, fault injection, fixtures, and mutation tooling outside
  product code, imports, resources, and runtime status feeds.

Implementation observations and verified outcomes will be appended below.

## 2026-10-18 — Product source placement

- All new application source lives under the existing `synapse/` product root.
  The desktop application moved from the initial `apps/desktop` working location
  to `synapse/ui`; the Python machine endpoint remains in `synapse/machine`.
- Within `synapse/ui`, React components, connection state, schemas, and styles
  have separate owners. `src-tauri` owns the native bridge and process lifecycle.
- Desktop source and dependencies are excluded from Python package discovery.
  The desktop resource contains the canonical Python distribution, not another
  copy of the desktop application or its dependency/build directories.
- Acceptance and mutation drivers remain in `acceptance/ui_bridge`, outside
  product source and distributed application resources.

## 2026-10-18 — Protocol and controller observations

- `ready` identifies only `synapse.control.v1`. Every readiness/status projection
  explicitly carries `domain_ready: false`; unsupported domain methods are refused.
- A real Rust connection negotiated with the staged canonical Python entry,
  read status, checked liveness, and obtained shutdown acknowledgment plus exit.
- Strict cross-language parsing exposed negative zero as a representation edge:
  the control profile now requires `0` and rejects `-0`, decimal, exponent,
  unsafe-integer, duplicate-key, and unpaired-surrogate input consistently.
- A deadline race initially reported WRITE_TIMEOUT at the handshake deadline.
  The core now bounds the whole handshake (including readiness output) and reports
  HANDSHAKE_TIMEOUT when that overall deadline expires. The silent/flooded peer
  acceptance passes after the fix.
- Renderer publication initially bounded its own queue but still needed to prevent
  accumulating messages in WebView delivery. One outstanding acknowledgment per
  subscription now bounds delivery; later complete bootstrap snapshots coalesce.
  A stale acknowledgment cannot release a new subscription's capacity.
- A confirmed stop must survive a later refused restart request. The controller
  retains terminal process state separately from its displayed error state; close
  after that refusal still completes normally.
- The production host is the sole connection between the controller and verified
  bundle/OS launch. External acceptance substitutes only that host boundary; it
  does not introduce a production switch or establish OS containment evidence.

## 2026-10-18 — Verification and remaining conditions

- Python acceptance: 109 passed. Native component/interop acceptance: 21 passed.
  The TypeScript corpus/state/diagnostic checks and browser component scenarios
  pass; precise commands and results are in P1_VERIFICATION.md.
- Seeded campaigns detected 82 final behavioral mutants: 30 Python/build/package,
  33 native, and 19 renderer/generator. The initial exit-status survivor revealed a
  missing refusal-exit assertion; a real CLI case now detects it. Platform-specific
  Windows mutations and native WebView/security execution remain outstanding.
- An existing CLI test pinned the old help command list and inspected source
  keywords. It now retains the observable canonical-help comparison; real machine
  execution is checked externally. No filename/line-count/branch-name gate was added.
- A rebuilt Python wheel excludes the desktop namespace while preserving the
  packaged machine schema/profile. Acceptance injects a contamination candidate
  only into an isolated build copy and verifies the installed import boundary.
- The Tauri binary builds locally; a Windows native-core cross-check compiles.
  Native window execution could not start WebKit's process at the distribution's
  fixed `/usr/lib` path. The workspace has extracted build libraries but cannot
  establish the required runtime mount layout. No sandbox was disabled to hide this.
- This workspace has no systemd user manager. Real Linux supported-profile cleanup,
  installed Windows execution, and actual Tauri IPC/CSP remain unverified. The
  screenshot deliberately shows the real browser `DESKTOP_REQUIRED` state.
- At this checkpoint, TR-2 named plugin-shell `set_raw_out(true)` while the
  implementation used native raw pipes to establish Windows containment before
  execution. The discrepancy was recorded as F-06; its later normative resolution
  is recorded in the F-06 entry below.
- No branch or pull request is merged. PR #110 is the review surface for this work.

## 2026-10-18 — Repository data privacy

- Recorded the owner's permanent prohibition on writing personal/private user
  data into repository files, including ignored outputs, examples, fixtures,
  documentation, logs, reports, and visual artifacts. Access does not authorize
  retention or publication; acceptance inputs must be invented.
- Pending mutation reports contained raw subprocess excerpts with absolute
  workspace paths. Removed those excerpts before the implementation commit.
  Reports now retain source digests, classifications, and relative acceptance
  references. The runner no longer persists raw output or baseline-failure logs.
- Removed the account reference from document provenance. Reviewed changed text,
  the retained reference, and the actual renderer preview; no private user state
  was identified in this scoped review. The preview has no embedded metadata.
- The final Python invocation passed 109 product/build cases and five separate
  reporting confidentiality cases. Three reporting-tool mutants were killed;
  they are separate from the 82 product/build mutants and do not close P1 gaps.
- No product behavior changed for this privacy follow-up. The permanent-rule
  prose has mutation status N/A; the changed reporting behavior has its own
  acceptance and mutation evidence.

## 2026-10-18 — PR review corrections

- R-01 was reproduced on the real selector: a composition-root manifest change
  altered the canonical-entry check but did not select it. `DATA_DEPENDENCIES` in
  `scripts/ci/dependencies.py` now declares the manifest readers, including the DAG
  tool and its importing acceptance. Unknown or removed JSON under `governance/`
  conservatively selects that ownership family. Unrelated notes select no product
  checks. Register new runtime data readers explicitly; a missing import edge does
  not prove independence. Acceptance executes an unchanged copy of the existing
  architecture check before/after changing a synthetic manifest, then checks the
  execution plan. Mutations remove these dependencies and are detected.
- R-02 was reproduced before any accepted snapshot and after a live-core snapshot.
  Renderer delivery failure now closes only that observer. Recovery replaces the
  native subscriber, validates a new initial snapshot, and accepts an unchanged
  native sequence. Old deliveries and ACK failures cannot restore the old channel's
  trust. Recovery never starts a process. The browser acceptance follows these
  transitions without a page reload, including failed ACKs, stopped-session mounts,
  and a separate explicit start after recovering an unstarted shell.
- Local delivery errors are distinct from native session state. Stop/Force close
  are hidden while delivery is unavailable; a nonfatal refusal on native `ready`
  leaves Stop available and does not offer Force close. Native authorization and
  the native force-close state guard were not weakened.
- A disconnected writer and a dropped completion receipt now report `CORE_EXITED`;
  a full queue remains `BUSY`. A declared Python method without a dispatcher branch
  fails explicitly instead of taking the shutdown branch. Existing canonical-entry
  acceptance again checks CLI delegation, exit propagation, and the thin adapter's
  responsibility boundary without freezing the old help command list.
- Rust now consumes validated machine error envelopes, requires the active request
  ID, retains nonfatal refusals without discarding the session, and surfaces their
  declared codes in the renderer. Fatal errors terminate the session. A refused
  shutdown cannot close the window or become an acknowledgment. Stop preserves
  protocol/process failures instead of relabeling every failure as a timeout.
- The stderr retention bound comes from the schema's longest code plus the prefix
  and one overflow byte. The overflow byte is necessary: truncating an unknown
  longer line to a declared code would incorrectly make it trusted.
- Review qualifications retained: Rust already rejects negative zero; all workflow
  checkouts already use the requested head; the native launch guard already prevents
  a second process. Heartbeat updates carry a new correlated reply ID visible in
  session details, so they are retained as explicit liveness evidence, not treated
  as identical snapshots. Initial subscription and channel delivery may duplicate
  a snapshot, but only channel deliveries occupy/acknowledge the publication slot.
- Cold Python startup and the current two-second ping budget remain considerations
  for installed-platform acceptance and future domain concurrency. No observed cold
  start failure or domain workload justifies changing the current release deadlines.
  At this checkpoint F-06, real WebView/Windows/systemd execution, installer and
  domain/JCS obligations remained open. These code corrections did not change
  the normative specification; the later F-06 decision below does so explicitly.

## 2026-10-18 — Cancelled push and empty verification plan

- The push workflow can be cancelled when the PR workflow for the same head starts.
  If cancellation interrupts `select`, it publishes no plan. The old `always()`
  final gate still ran, and `json.loads('')` failed before checking job results.
  This failure occurred in the cancelled push run; the concurrent PR run's UI and
  selection jobs completed successfully. Both event runs must be inspected when
  checking a pushed head, including the jobs of a cancelled run.
- The final gate now uses `!cancelled()`: active failed dependencies still reach
  verification, while a cancelled workflow does not start verification. The gate
  checks selection success first and explicitly refuses missing, malformed or
  incomplete plans. It never invents an empty successful selection.
- External process acceptance covers empty/truncated/non-object plans, failed or
  cancelled selection, missing/failed/cancelled selected jobs and unrelated skips.
  The added mutation replaces invalid input with an empty permissive plan and must
  be rejected by those cases. Product acceptance and product code are unchanged
  by this CI follow-up.

## 2026-10-18 — F-06 native transport amendment

- The owner authorized retaining native byte pipes and removing TR-2's mandatory
  shell-plugin API. Updated the governing specification, provenance, requirement
  row, open finding, acceptance obligations, security rules, and handoff status.
  F-06 is RESOLVED as a mechanism decision; TR-2 and P1 remain IN_PROGRESS.
- The native path remains the only core launcher per supported platform. No
  plugin wrapper, selector, fallback, new executor, or renderer process authority
  is introduced. Tauri invoke/Channels, caller authorization, and capabilities
  retain their existing responsibilities.
- Byte fidelity applies to formed pipe frames. Validation and permitted renderer
  projection follow complete-frame receipt and cannot rewrite authoritative
  evidence or its identity. Bounded buffering/queues and explicit refusal remain
  required; a write receipt is not a domain-acceptance record.
- Windows must establish process/handle controls before the core executes.
  Preparation, assignment, resume, and interrupted startup require observable
  cleanup. Linux retains the required systemd/cgroup profile. Neither path may
  bypass a failed prerequisite or admit a replacement while old local executors
  can still affect the scope.
- The acceptance matrix now distinguishes recorded component evidence from
  outstanding escaped-line-ending variants, platform startup faults, inherited
  handles, actual adapter/MCP descendants, shell death/replacement, and the
  installed window. OC-01/09/32, F-07, and all complete source-case verdicts retain
  their open/not-run status. Cross-compilation is not Windows execution.
- This amendment changes Markdown documentation only. Mutation verification is
  NOT_APPLICABLE to the amendment; earlier executable evidence is not relabeled
  as a new run. The original prototype and supplied-source hashes are preserved.
  Only the English engineering decision is retained; no uploaded discussion or
  private input is copied into the repository.

## 2026-10-19 — Windows verification and bundle privacy

- Verified PR #110 in an isolated checkout. Host native acceptance passed 28 cases,
  with actual canonical Windows Job/native-pipe sessions, missing executable and
  overlap refusal, ping, confirmed shutdown and replacement. A separate heavy case
  passed preparation refusal, unrelated-event inheritance exclusion, named-Job
  membership, synthetic descendant cleanup after owner drop/abrupt death and
  replacement. These host process tests were elevated; restricted execution still
  fails before the witness report and does not establish another platform result.
- Recorded 114 passing Python UI cases and one failed unread-output diagnostic
  assertion (CRLF versus LF), with a flood-writer warning. Rust exact-code matching
  makes the newline boundary a diagnostic concern (F-08). CLI checks passed 39
  cases; the replay integrity failure reproduces on both PR head and base.
  TypeScript passed 46 cases and frontend assets built. Extended Unicode and
  escaped/literal line-ending cases passed 10 Python and 3 Rust frame checks.
- Fixed build provenance and interpreter-bound console launchers in pip output,
  removing their RECORD entries before inventory binding. Raw staging stays
  outside source; sanitized resources publish atomically. Runtime package modules
  and data are preserved. Five packaging cases passed. The sole product/tooling
  behavior change is this packaging boundary; tests remain external.
- Built and installed debug NSIS in a separate destination. Verified every resource
  and exact shell bytes after Tauri's one UNK-to-NSS bundle-marker transformation.
  The initial equality assertion did not account for that documented patch and
  was corrected without permitting other differences. The earlier file-lock build
  failure cleared with applications closed; no product workaround was added.
- Installed process checks passed canonical command, overlap exclusion, survival
  of the first core when a refused shell closes, core cleanup after shell death,
  replacement and a shell remaining available after core death. Actual WebView2
  displayed CORE_EXITED. Closing that elevated window required user-assisted
  force-close confirmation. A subsequent ordinary desktop launch reached ready,
  Stop displayed confirmed shutdown and the window closed; its token elevation
  was not independently measured. No test window was left open.
- Ten focused mutants were killed with passing baselines: packaging six, active
  Job admission one, Job-close termination one and wire newline rewriting two.
  Isolated copies avoided Windows symlink requirements; structured evidence and
  source hashes retain no raw host diagnostics. New fixtures are synthetic.
- Security review of the changed build boundary checked raw staging, provenance,
  RECORD hashes and preservation of runtime modules/data. Installed nominal IPC
  used configured production CSP; negative caller/CSP/navigation/Channel claims
  remain unverified. Assignment/resume/interrupted startup, actual adapters/MCP,
  Linux systemd, clean-machine/release and JCS/owner contracts remain open.
  TR-2/P1 stay IN_PROGRESS; no complete source-case verdict is closed.

## 2026-10-19 — Windows startup ownership and diagnostics continuation

- The owner deferred Linux execution explicitly; retain it as acceptance debt.
- External x64 Windows debugger acceptance exposed an unowned suspended child
  between CreateProcessW return and the separate Job assignment. Creation now
  includes PROC_THREAD_ATTRIBUTE_JOB_LIST; six startup scenarios pass without
  product hooks, including actual OS refusal of an invalid Job handle (F-10).
- Canonical stderr explicitly emits LF. All 119 Python UI checks and 30 native
  checks pass, including INVALID_LENGTH over the rebuilt bundle's real stderr
  into the native diagnostic reader. The allowlist and original assertion remain
  strict (F-08).
- Actual run_process and McpToolTransport/MCP 2.2.0 owners launch synthetic peers
  with nested children inside the production Job. Orderly native owner drop and
  abrupt death terminate every retained process. This is containment evidence,
  not UI domain-command readiness.
- Six focused mutations are killed; startup/runtime/stderr baselines and final
  reruns pass. Evidence retains source hashes and structured verdicts only.
- Final ordinary-user artifact identity, process recovery, installed CSP and
  command refusal, real stalled Channels and crash cancel/confirm passed.
- F-11 exposed one GET before WebView2 navigation cancellation. Native request
  interception now precedes packaged content, permits local asset/IPC handlers
  only, and refuses incomplete setup. The installed receiver sees zero requests.
  Four policy mutants and two native filter/response mutants were killed with
  passing baselines/final reruns. No CSP bypass or product test hooks were used.
- Final package/window results are recorded in the continuing
  [Windows verification record](P1_VERIFICATION.md#continued-windows-fault-and-runtime-verification).
  TR-2/P1 remain IN_PROGRESS; JCS/domain ownership, Linux and release/environment
  obligations are not supplied by this continuation.

## 2026-10-19 — Windows release and remaining executable boundaries

- Continued on the existing task branch; the owner authorized publishing to the
  existing PR #110 head branch after verification. No new branch or merge is needed.
- Added an external relay that verifies the installed inventory and invokes the
  production Windows launcher with the real canonical bundle. All 51 control-pipe
  cases pass. Arbitrary parameter refusal is not claimed as domain echo fidelity.
- The separate native byte witness passes exact binary/Unicode/escape framing,
  fragmentation, independent stderr and damaged-stream termination. Four canonical
  corpus mutations and two endpoint-misdirection mutations are killed. The first
  misdirection observer blocked its stderr pipe; concurrent draining removes that
  observer defect and exposes the actual frame failure.
- Built optimized release with unchanged frontend assets, installed it as an
  ordinary user and verified exact patched executable/resource identity. Twelve
  resume identity/process checks, 26 security/Channel checks and seven actual crash
  consent checks pass. Empty PATH and synthetic Python variables do not prevent
  readiness. The installer is unsigned and the PC already has WebView2.
- Seven transport-damage scenarios in the actual installed release window pass
  all 35 assertions. Five malformed frames produce PROTOCOL_ERROR; partial header
  and body EOF produce CORE_EXITED. The owned stdout pipe is duplicated externally;
  no product hook or installed-file change is involved.
- Both actual foreign native contexts refuse all five custom commands in private
  topology copies. Removing label/origin guards permits commands. Initial origin
  coverage was masked by the port guard; a default-port loopback case detects the
  same mutant. One repeated label run failed before IPC setup; it is not a kill.
  Retain the independently successful label run and both attempts with exact binary
  identities. No weakened topology is installed or shipped.
- All 155 Python UI acceptance cases pass after observer changes. Product sources
  are unchanged from the preceding lifecycle/network fixes. Earlier native and
  TypeScript results remain historical scoped evidence, not newly rerun suites.
- Keep TR-2/P1 open for Linux, clean-machine/signing/wider versions and unfinished
  JCS/domain owner contracts. No complete source scenario is relabelled PASS.
