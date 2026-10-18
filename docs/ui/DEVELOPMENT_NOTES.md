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
- TR-2 names plugin-shell `set_raw_out(true)` while the implementation uses native
  raw pipes to establish Windows containment before execution. Byte behavior has
  evidence, but the literal implementation discrepancy remains open as F-06.
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
  F-06, real WebView/Windows/systemd execution, installer and domain/JCS obligations
  remain open; these corrections do not complete P1 or change the normative spec.
