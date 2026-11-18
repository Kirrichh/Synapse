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

## 2026-10-19 — Owner boundaries and incomplete command foundations

The current implementation is unfinished. The first documentation review covered
the PR's UI documents and the invoked agent contracts, not every document on the
working branch. The full branch reading review remains outstanding; an inventory
or text search is not recorded as a completed read. Rechecked AGENTS, the coding,
acceptance and security rules, architecture, and universal-agent ownership before
reviewing the pending owner changes. No completion criterion is relaxed.

Affected obligations are COMP-6/11, CMD-1–14/20–22, CRASH-7, SYN-5/10/12/20 and
SEC-2/6/10. The current cancellation method is a bounded invocation-owner contract;
it does not supply approval, task, message, memory or administrative acceptance.
Trusted application scope resolution and canonical machine routing are still
required. A caller-constructed CommandScope is a binding input, not authorization.

Responsibilities were initially accumulated in retention.py. That was corrected:

| Owner | Inputs, state and responsibility | Dependency and failure boundary |
|---|---|---|
| `synapse/agents/retention.py` | Invocation claims, exact terminal records, artifacts, process events and execution exclusion; owns the SQLite connection lifetime | Writable opening initializes component tables; `open_existing` uses a read-only connection and performs no schema initialization or recovery |
| `synapse/agents/invocation_commands.py` | Bounded cancellation binding, durable receipt and read-only status by command identity | Uses the same owner transaction for cancellation, observation and receipt; returns only after commit; retains historical results without TTL and never dispatches an adapter |
| `synapse/agents/invocation_observation.py` | State revisions, consistent snapshots, complete filtered ranges and bounded projection history | Publishes through the supplied owner transaction; compaction makes old cursors unavailable without deleting invocation or command records; reads never manufacture liveness |
| `synapse/commands` | New command JCS and the closed cancellation binding schema | Does not change historical agent/evidence canonicalization; does not own effects or application permissions |
| Native JCS, command binding, intent store and protected-data modules | Separate serialization, immutable content binding, pre-send identity and platform protection responsibilities | Not yet registered as application IPC; no claim of protected end-to-end command recovery |

The transferred methods were removed from InvocationStore; there are no old
forwarding entry points or competing implementations. The existing execution
port remains the only adapter-dispatch owner. Observation publication receives
the caller's connection so splitting files does not split the transaction.

The review also found that receipt replay checked its digest/binding hash but
did not validate every returned result field against the requested command.
The owner now revalidates retained bindings and the exact closed result content,
including command/recipient identity, semantic version, parameter hash and
declared outcome. Malformed retained content has a fixed refusal; restoring a
terminal result invokes the existing evidence validator (F-12).

Observed on this Windows host: 40 invocation-owner/regression cases passed and
five existing mutation-report confidentiality cases passed separately. The
snapshot race now forces a competing durable commit between basis and item reads;
it no longer relies on scheduler luck. Real SQLite failure triggers exercise
claim, cancellation, command receipt and terminal publication rollback.
Three existing ownership suites passed 884 cases. These are targeted results.

The first owner mutation round killed 22 of 23 mutants. The digest-check mutant
survived because other semantic checks rejected the old damaged fixture first.
A valid but changed historical outcome with the old digest detects that defect.
The final round killed all 23; the affected acceptance passed again. See
[structured evidence](../../acceptance/ui_bridge/evidence/invocation-owner-mutations.json).
Raw diagnostics and runtime data remain outside the checkout.

JCS across languages, native pre-send storage, actual Windows user protection,
scope authorization, negotiated domain transport, independent delivery queues,
renderer recovery and installed acceptance are not closed by these owner checks.
The domain bridge remains control-only. P1 and every full source case stay open.

The continued Gold-document reading located the explicit removal rationale for
the stale admitted-profile test (F-01): it duplicated heavy cases that now use
admitted profiles. Corrected the current instructions and reference status while
preserving the original map entry and historical observations. This is a
documentation correction; mutation is not applicable and no new acceptance
execution is claimed for the removed scenario.

## 2026-10-19 — Protected command reconciliation and independent bounds

Continued COMP-6/11, CMD-4/7/12/14/20–22, CRASH-7 and SEC-2/6/10 on the
existing working branch. The native recovery boundary consumes only a persisted
prepared command and an owner response. It does not accept a renderer-supplied
command ID, infer absence from a timeout, execute the invocation, or establish
application authorization. Only a future authenticated core route may supply
its owner response; this function is not registered as renderer IPC.

`intent_store` now recovers the original prepared command without a renderer
draft and without rewriting protected data. `command_recovery` owns response
validation and correlation, separate from storage, JCS and owner execution.
The product reconciliation JSON Schema defines the closed query/response
vocabulary. Python rejects invalid query identities before opening the journal.
ABSENT binds principal, workspace, owner, object and original command ID;
only that answer permits resending the same persisted command. A historical
terminal receipt preserves its binding and outcome and never schedules a retry.
The cancellation receipt establishes a cooperative request, not process or
whole-task termination. These semantics do not replace other owners' recovery.

The previous DPAPI failure was reproduced only in the restricted agent process.
Running production protection in a host process outside that sandbox succeeded,
including ciphertext corruption refusal. Protected restart, original identity,
read-only recovery and both reconciliation branches also passed with real DPAPI.
No plaintext fallback was added. This is Windows native-component evidence;
the new components still have no installed application route.

Two additional boundary defects were reproduced and fixed. Array canonicalization
could discard extra/symbol properties and evaluate indexed accessors. It now
requires plain data elements and refuses hidden constraints without invoking
getters. Python's end-of-line regex behavior also admitted a trailing newline in
an object identifier. Explicit length and excluded-character constraints now
reject line terminators; command and nested invocation identifiers share one
schema definition. Historical evidence codecs remain unchanged.

The initial three-language mutation round exposed four acceptance weaknesses:
Python/Rust limit cases derived their boundaries from the implementation's
constants, so increasing those constants escaped detection. The cases now test
the released 32-level and 65,536-byte boundaries independently. Failed and
surviving observations are retained in the component verification record.

P1 remains unfinished. Required work still includes trusted application scope
resolution, negotiated domain routing through the canonical machine endpoint,
registered native IPC, independent delivery classes/queues, renderer recovery
and acceptance against a newly built installed artifact. Full historical branch
document reading also remains outstanding. No stage, source case or open
condition is closed by the component results below; no new branch or publication
was performed for this unfinished increment.

The final dependency review found a further owner-integration defect: admission
captured the agent package but omitted the newly imported command code, schemas
and JCS distribution. Three isolated fresh-interpreter cases reproduced changed
code/schema and an omitted dependency escaping verification. Runtime capture now
includes the command package's executable/schema inputs and `rfc8785`; verification
requires them before loading an admitted adapter. Existing definitions with an
older runtime identity require explicit operator re-admission, not automatic
refresh. The three cases pass after the fix and all three seeded omissions are
killed. This does not grant admission to any real agent or execute one.

Final scoped evidence: 88 Python command/owner cases, three separate admission
cases, 11 native cases with production DPAPI and 26 TypeScript cases passed.
All 41 command-foundation, three admission and 23 owner mutants were killed on
the corresponding final sources. TypeScript type checking also passed as a
maintenance check. See [reviewed evidence](../../acceptance/ui_bridge/evidence/command-foundation-mutations.json).

## 2026-10-19 — Primary-source review before application integration

This review supports the existing requirements; it does not replace them with
external API semantics or claim completion of P1. The PR document package and
linked mandatory rules have been read; the separate historical repository-wide
reading inventory remains incomplete. No new application authority or domain
protocol is inferred from the component interfaces.

| Boundary | Primary source and consequence for this implementation |
| --- | --- |
| Canonical command data | [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785.html) requires unique keys, preserved Unicode strings, binary64 number serialization and UTF-16 property ordering. COMP-11 additionally rejects input precision loss before hashing; JCS serialization alone does not establish that stronger admission rule. Keep cross-language input and digest checks. |
| Native caller | [Tauri capabilities](https://v2.tauri.app/security/capabilities/) documents that registered application commands are unrestricted by default unless the application command manifest constrains them. The existing build manifest, main-window capability and native label/origin checks are all necessary. A new IPC command must enter that same boundary; none establishes domain permission. |
| Protected records | [CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata) binds protection to the Windows user by default. Machine-wide protection is inappropriate here. DPAPI is not isolation from every process of the same user, path containment, anti-rollback or command authority. Recovery must refuse unreadable/corrupt records without issuing a replacement identity. |
| Filesystem object lifetime | [CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew) distinguishes opening a reparse point from following it and keeps sharing constraints until the handle closes. Checking a path and later reopening it is not equivalent. Exercise root/ancestor replacement and leaf substitution at the actual intent-storage boundary before wiring it into IPC. |
| Durable publication | [MoveFileExW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw) documents replacement and write-through semantics. Keep the flushed temporary file on the same volume, check the move result and return no sendable command on failure. Process-crash acceptance is not a claim about physical power-loss behavior on every storage device. |
| Owner transactions | [SQLite transactions](https://www.sqlite.org/lang_transaction.html) permit one writer and can refuse BEGIN IMMEDIATE with BUSY. The owner must commit binding, cancellation request and receipt together, then return. A database error is not evidence that an earlier attempt never committed. |
| Read-only observation | [SQLite WAL](https://www.sqlite.org/wal.html) may need WAL/shared-memory coordination even for read-only access. mode=ro and query_only prevent application SQL writes, not all filesystem coordination. Do not use immutable=1 for a live owner database or construct a writable world to read it. The observed local SQLite 3.53.1 includes the documented WAL-reset fix; bundled runtime identity must be checked independently. |
| Idempotency and retention | [Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests) illustrates saved outcomes and parameter conflicts, but permits reuse after key pruning. CMD-14/20/22 forbid using that expiration behavior to revive an old intent. Only scoped owner absence permits resending the original persisted command. |
| Observation coverage | [etcd watch guarantees](https://etcd.io/docs/v3.6/learning/api_guarantees/) separate ordered revision coverage and progress notifications from linearizable reads. A delivery watermark cannot invent an owner snapshot cut or a global cross-domain order. Test quiet progress, expired history and generation changes independently. |
| Bounded delivery | [Tokio channels](https://tokio.rs/tokio/tutorial/channels) distinguishes bounded queues, response channels and latest-value observation, and warns about implicit unbounded work. TR-12 still determines the separate request/control/projection bounds and loss semantics. A bounded queue alone does not prove scheduling fairness or durable acceptance. No new async runtime is introduced by this review. |
| Schema validation | [JSON Schema 2020-12 validation](https://json-schema.org/draft/2020-12/json-schema-validation) separates format annotation from assertion. Keep explicit format validation, closed authoritative fields and shared schema-derived frontend contracts; a format label alone does not validate UUIDs or identifiers. |

The filesystem review concerns CMD-4/6/7/20, SEC-6 and the path rules in
[security rules section 6](SECURITY_RULES.md#6-files-documents-and-content). Two native Windows
cases passed on the existing implementation: the opened store's root and parent
cannot be renamed until the store drops, and an excluded agent root is refused
without creating a directory or retaining handles. The lifetime handle already
provides that exercised protection; no new product mechanism was added. Removing
only the byte-range lock survived the new directory case, because it left that
handle open. A corrected mutant that discards the directory's owner handle is
killed, as is bypassing the agent-root exclusion. The initial survivor is kept
as an observation, not relabeled killed. These cases do not establish absence
of races during initial path admission, same-user process isolation or complete
filesystem security.

The runtime audit found a confirmed packaging defect: the Windows DLL directory
was copied without the standard-library exclusions. The installed resource held
CPython's native acceptance modules. The upstream
[CPython 3.12 layout](https://github.com/python/cpython/blob/3.12/PC/layout/main.py)
classifies `_test*`, `_ctypes_test`, `xxlimited` and `xxlimited_35` as test-only
extensions. The builder now applies one exclusion policy to both the standard
library and Windows DLLs, including debug and ABI-tagged extension names.
Required runtime libraries keep their original bytes and inventory hashes.
This implements the permanent acceptance/product separation and supports BOOT-1;
it neither changes the canonical entry point nor adds execution authority.

Two packaging cases reproduced the defect before the fix; all five cases in the
two affected suites pass afterwards. Three isolated mutants are killed (bypass
the Windows filter, omit non-prefix tests, remove required ctypes). The first
campaign stopped on a baseline failure because its isolated source copy omitted
LICENSE; it contributes no mutation success. The copy manifest was corrected
before the successful campaign. A real staged CPython then passed isolated-prefix,
ctypes, SQLite query and TLS-context checks, with test extensions unavailable.
Both the local and previously installed interpreters report SQLite 3.53.1.
This does not upgrade or reverify the installed application. Trusted application
scope, domain routing, delivery classes and fresh installed acceptance remain
unfinished.

## 2026-10-19 — Delivery scheduling and shared command decoder review

TR-12 and DEL-3/5/8 require different loss semantics, not one unbounded event
list. `synapse.machine.delivery` owns bounded queue memory, scheduling and loss
notices; it does not authorize subscriptions, accept commands or rewrite source
records. The canonical pipe writer now uses its separate control queue. The
control profile still advertises no domain commands or projections. Routing,
owner acceptance and renderer synchronization retain their separate boundaries.

The implementation uses one condition lock for admission, wakeup and removal.
[Python condition documentation](https://docs.python.org/3.12/library/threading.html#condition-objects)
requires checking the predicate again after waking; notification does not release
the lock. [Python queue documentation](https://docs.python.org/3.12/library/queue.html#queue.Queue.qsize)
also explains why approximate queue size is not an admission guarantee. The
acceptance barriers observe the actual wait before closing a queue. No new
thread is created per message, stream, loss or renderer retry.

Command types and standalone validators are now generated from the existing
owner schemas. UUID validation uses pinned build dependency `ajv-formats` 3.0.1,
with only the UUID format enabled and the schema's exact 36-character bound.
[Ajv's format guidance](https://ajv.js.org/guide/formats.html) distinguishes
format support from core Ajv and documents standalone generation. Validation
does not coerce fields or remove unknown constraints. The decoders provide no
authority and do not generate intent or command IDs.

This review reproduced F-17: `lossless-json` skips its duplicate-key callback
when both values are deeply equal. Its
[upstream parser](https://github.com/josdejong/lossless-json/blob/main/src/parse.ts)
has the same condition. The frontend therefore accepted identical duplicate
authoritative fields that Python and Rust refused. A bounded lexical key check
now runs before either frontend decoder can discard duplicate information.
It decodes escaped key names, distinguishes object scopes, and ignores key-like
text inside string values. Syntax and numeric decoding remain with their
existing owners. Historical records are not recanonicalized.

The first decoder campaign killed twelve mutants and left the escaped-quote
scanner mutation alive: the cases checked distinct escaped keys but lacked an
equal duplicate containing an escaped quote. That missing refusal case and a
backslash-key case were added. All thirteen mutants are killed on the final
case set. An earlier campaign failed before its baseline because a Windows
loader path was passed as an import URL; it provides no acceptance evidence.

### Accepted application access ownership

CMD-21 requires trusted operator/workspace membership. Neither `CommandScope`,
a Windows account name, a selected filesystem path, nor a project declaration
alone supplies that membership. The existing run composition identifies the
invocation owner at `run_root/stage10/agent-executions`; reading it must not call
`compose_frozen_gold_run`, `open_gold_project`, or runtime/plugin admission.

The owner selected explicit operator delegation on 2026-10-19. The Python core
owns the authority and its durable registry. The canonical CLI is the initial
administrative surface, not proof of administration merely by being invoked.
`CommandScope` describes a recipient scope; it cannot confer permission. The
accepted [access contract](WORKSPACE_ACCESS_CONTRACT.md) applies to registration,
authenticated sessions, current grants, revocation and every protected read or
effect. The eventual trusted graphical confirmation must use that same owner.
This decision resolves the missing source-of-authority choice, not implementation
or acceptance of that boundary. Domain routing remains disabled until it exists.
## 2026-10-19 — Access-owner integration continuation

Completed the full 161-document branch inventory, including the historical
changelog. Reviewed Windows object security/token/locking and PyCA Ed25519
primary sources before implementing the accepted workspace-access boundary.

Added cohesive access record, authentication, authority, registry codec and
Windows custody/storage responsibilities. The canonical administrative CLI
delegates to that owner. Added an authorized invocation adapter that intersects
current grants with real retained membership, cancellation receipts and read-only
observation. `CommandScope` explicitly remains descriptive binding data.

Observed targeted acceptance: 34 passed for authority, actual invocation adapter
and existing cancellation cases; 3 Windows custody/ordinary-admin-refusal cases
passed in a normal host process. The sandbox could not open ancestor handles;
the host rerun passed without elevation. This is not positive administrative
persistence, installed application, full-suite or mutation evidence. Those
checks and machine/native integration remain in progress. P1 is not complete.

## 2026-10-19 — Protected Windows access and native owner recovery

The isolated elevated host check passed actual protected registry enrollment,
durable application/grant creation, reopen, signed authentication and revocation.
The ordinary host companion passed protected reading, administrative refusal and
actual OS write refusal. Cleanup removed the known synthetic registry/resource;
no private paths, SID, credentials or raw host report are retained in this record.

Added a closed application wire contract, explicit profile opening, single-use
connection proof, scoped grant discovery and access close. The core identifies
an application by its registered unique public key. The canonical machine entry
routes to the real invocation owner without opening a Gold world. Native handlers
use the same schemas, correlate replies, and keep domain content out of control
diagnostics. The Tauri capability list admits only the trusted packaged window.

Native instance keys use Windows Credential Manager; creation is serialized,
existing keys survive restart, and malformed storage is not replaced. This does
not isolate arbitrary same-user native code. The separately elevated registry
remains the issuance authority. Reviewed Microsoft's credential read/write and
persistence contracts and Ed25519-dalek documentation before implementation.

The native application owner now resolves current grants, issues protected
intents, submits bound cancellation and reconciles lost replies. Prepared-intent
resubmission first asks the owner for status. A grant's identity/version partitions
its intent storage. Actual Rust/Python acceptance lost an accepted cancellation's
reply, restarted the native state, recovered the original receipt without a
renderer draft, rejected changed parameters and denied revoked recovery.
Read-only grants cannot issue intents or turn the read surface into cancellation.

A test exposed Serde's empty unit-variant accepting extra renderer fields despite
the surrounding enum annotation. The empty request now uses a strict object
variant; the refusal test passes. No assertion was weakened.

Observed on the current tree: 25 Python access/domain/Windows-custody cases;
native application (3), identity (2), protocol (2) and existing controller (3)
cases passed, followed by successful compilation of the actual Tauri handlers.
Twenty isolated domain/native mutants were killed after passing their baselines.
The older twenty-mutant access-owner campaign also passes on this integration.
Reviewed structured evidence is retained in
[access/native evidence](../../acceptance/ui_bridge/evidence/access-native-review.json).

This is component and real cross-language pipe evidence with synthetic registry
composition, not installed canonical application evidence. UI/channel integration,
class-specific delivery, cross-process access races and fresh installed acceptance
remain required. P1 is still in progress; Linux remains explicitly deferred.

## 2026-10-19 — Installed application access, recovery and source delivery

Completed the invocation bridge's missing application wiring after reading the
entire branch-document inventory and reviewing primary sources. Responsibilities
remain separate: core access authority, platform custody, owner receipts/journal,
application session/subscriptions, native identity/protected intents, Channel
admission and React session/presentation. No product module imports acceptance.

The canonical application profile authenticates the registered native instance;
renderer fields cannot choose a principal, workspace root or delivery class.
Current authority and real owner membership cover reads, commands, saved outcomes,
subscription collection and physical response delivery. Native restart preserves
the original protected command identity; a missing reply never creates a new one.
The application has bounded acknowledged result delivery independent of control
publication and separate native admission queues for control and application work.

StateSync confirms the owner generation/P0, then obtains a snapshot Ps >= P0.
Collection uses the owner's complete journal ranges; overflow invalidates the
epoch. Evidential collection rereads the retained source, including restored data
behind a previous cursor, and never rewrites its bytes/hash. Live marks loss and
reports only last observed core response. The UI uses fresh explicit observations;
complete P2 continuous reducer behavior remains separate required work.

Reviewed [etcd API guarantees](https://etcd.io/docs/v3.6/learning/api_guarantees/)
for separating revision/coverage guarantees from watch timing, and
[Tauri frontend delivery](https://v2.tauri.app/develop/calling-frontend/)
for the Channel boundary. These sources informed explicit confirmation and
bounded acknowledgment; they do not provide Synapse authority or delivery policy.
During installed diagnosis, Microsoft's
[known-folder API](https://learn.microsoft.com/en-us/windows/win32/api/shlobj_core/nf-shlobj_core-shgetknownfolderpath),
[system environment API](https://learn.microsoft.com/en-us/windows/win32/api/userenv/nf-userenv-createenvironmentblock)
and [system Windows directory API](https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/nf-sysinfoapi-getsystemwindowsdirectoryw)
were checked. Actual subprocess probes showed inherited SystemDrive influencing
Shell/userenv resolution even when creating a non-inherited system block. F-18
therefore uses machine configuration and the system-directory API, without
process-environment path authority or a permissive default fallback.

Final Windows component, mutation and installed results are summarized in
[P1 verification](P1_VERIFICATION.md#windows-application-and-delivery-integration)
and [sanitized evidence](../../acceptance/ui_bridge/evidence/application-delivery-review.json).
The real installed sequence passed ordinary discovery, all three source classes,
foreign-object refusal, unacknowledged accepted cancellation, cold original-ID
recovery, separate-process read/revoke serialization and revoked delivery/recovery.
The temporary administrative registry was cleaned. Initial product and harness
failures, survivors and invalid baselines remain documented; none was counted as
success or hidden with a skip. Full-platform P1 and later-stage contracts remain
open exactly where their evidence/implementation is absent.

Publication maintenance checks cover changed documentation links, Git whitespace,
English additions, reviewed allowlist evidence and local-path scans of source and
generated resources. Disposable ignored bytecode containing build paths was
removed before staging. Unchanged historical reference documents are not treated
as new evidence or rewritten by this task. These maintenance checks are not
additional product acceptance or mutation passes.
