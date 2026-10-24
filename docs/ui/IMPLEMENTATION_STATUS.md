# UI implementation status

Version 1.1. Last updated: 2026-10-19.

Current stage: **P1 — trusted application and bridge implementation in progress.**
The implementation branch is `codex/ui-foundation-v1`, based on the main commit
recorded in [provenance](SOURCE_MANIFEST.md). Windows verification continues in
the isolated `codex/pr110-windows-tr2-p1` branch from PR #110 head `330483b`.

The English specification, detailed rules, separated source references, original
prototype, and progress registers are prepared. Publication and final document
verification are recorded in [foundation verification](FOUNDATION_VERIFICATION.md).
P1 control/bootstrap and scoped invocation application code are implemented under
`synapse/access`, `synapse/commands`, `synapse/machine` and `synapse/ui`.
The [development guide](DESKTOP_DEVELOPMENT.md) describes its module
boundaries; [P1 verification](P1_VERIFICATION.md) records actual evidence. None of
the 44 complete installed/domain scenarios is claimed passed by component checks.

The [UI security design](UI_SECURITY_DESIGN.md) records public-source research
and its application to the architecture. Bootstrap controls now have implementation
and component evidence; Windows installed control/crash checks now have scoped
evidence. Full platform/security acceptance remains open.
The consent trust-boundary clarification remains F-05.

## Delivery stages and remaining work

Stages organize dependencies, not reduced scope. Finish the entire assigned
contract at each stage. A finished subtask does not complete a containing feature.
Independent capabilities may progress while unrelated open conditions remain.

| Stage | Scope and exit evidence | Current state |
|---|---|---|
| P0 — Foundation | English v1, all original requirements/map entries, permanent rules, researched UI security design, prototype, traceability, reviewed branch | Documentation prepared |
| P1 — Trusted application and bridge | Bundled canonical machine mode, schemas/JCS, handshake/framing/queues, trusted caller context, diagnostics/CSP, Windows/Linux containment/shutdown; behavioral and mutation evidence | IN_PROGRESS at the full platform gate: Windows canonical access/owner integration, protected cold recovery and three delivery classes now have real installed evidence. Scoped implementation and mutation checks pass; native Linux and clean-machine/signing/wider-version qualification remain open. See the current Windows integration record below. |
| P2 — Authorized reading and synchronization | Project/material reads, domain snapshots/coverage/epochs, progress/freshness, unsubscribe, retained evidence gaps and safe source projections | IN_PROGRESS: authorized invocation subscriptions, core-issued epochs, fresh snapshots, retained rereads and source-class queues are integrated. Complete continuous multi-domain synchronization and project/material surfaces remain required. |
| P3 — Durable commands and permissions | Intent/pre-send recovery, owner acceptance/status/retention, binding conflicts, grant/refuse/revoke, exclusive once-binding, new-task mode policy | IN_PROGRESS: invocation cancellation, current access checks and protected restart recovery pass through the installed application. Other command owners, approvals and new-task policy remain separate required work. |
| P4 — Task workspace and editing | Required panel layout, main conversation contract, task submit/control, real execution cards, Monaco reads/diffs/versioned own edits, RU/EN draft/focus preservation | Not started |
| P5 — Connection administration | Agent definitions/admission/preferences/probes, distinct model targets/secrets, MCP/instructions, disabling/recovery dependencies, honest cost/limit presentation | Not started |
| P6 — Memory and learning | Derived parameter metadata, mathematical validation, isolated bound preview, safe-point durable acceptance/recovery, storage accounting/enforcement, supported cache/metrics, required controls/chart | Not started |
| P7 — Installed-product acceptance | Component/desktop/installed levels, every applicable source case, actual artifact identity, platform/environment matrix, mutation and security evidence, full requirement closure | Not started |

P1 lifecycle and security work must cover actual existing adapter/process paths.
P2 read-only work must not assemble writable execution worlds. P3 acceptance
records remain owner-specific. P5/P6 depend on their own open contracts and cannot
gain authority through forms. P7 is cumulative; run relevant acceptance and
mutation checks while implementing every earlier stage rather than postponing
security and verification to the end.

Managed local-model installation/GPU ownership (OC-27) is conditional in the
source. A user-owned compatible server connection does not imply that capability.
Mark a conditional capability out of product scope only through an explicit
recorded scope decision; do not silently call it implemented.

## Requirement and evidence registers

- [Requirement matrix](REQUIREMENT_MATRIX.md): 201 original normative IDs,
  implementation state, delivery stage, and evidence references.
- [Acceptance matrix](ACCEPTANCE_MATRIX.md): the 44 numbered source scenarios;
  all are currently NOT_RUN for the product workstream.
- [Open conditions](OPEN_CONDITIONS.md): all 33 source conditions, closure
  requirements, and local findings. None is closed by translating it.
- [Foundation verification](FOUNDATION_VERIFICATION.md): historical foundation-document checks.
- [P1 verification](P1_VERIFICATION.md): implementation checks and their exact limits.

## State meanings

| State | Meaning |
|---|---|
| NOT_STARTED | No implementation/evidence for this workstream |
| IN_PROGRESS | Assigned behavior is being implemented; incomplete |
| BLOCKED | A specific unmet dependency is recorded, with impact and next action |
| IMPLEMENTED_UNVERIFIED | Code exists but required acceptance/mutation/security evidence is incomplete |
| VERIFIED | Full requirement behavior is supported by linked evidence for the stated environment/scope |
| NOT_RUN | An acceptance scenario has not been executed for this implementation |
| NOT_APPLICABLE | A documented applicability decision, never a synonym for skipped hard work |

No percentages are inferred from files, commits, tests, or hours. A backend
function or a green demo control does not make the corresponding UI contract
VERIFIED. Meaningful surviving mutants or unresolved security bypasses prevent
completion of affected behavior.

## Existing foundations versus completed UI work

The code map describes current owners, canonical roots, agent execution,
memory/court mechanisms, and evidence. They are integration foundations with
explicit limits. The new control bridge does not establish those owners' future
UI command contracts, readiness, durable recovery, or authority. Historical acceptance references are kept in
[development-only sources](development-reference/ACCEPTANCE_SOURCES.md), outside
the product integration index. They are not product links or data feeds.

## Implemented in this increment

- [x] F-06 mechanism decision: amended TR-2 to require native byte pipes and
  explicit byte/process guarantees, removing the mandatory shell-plugin API.
  Updated provenance, requirements, and the mandatory acceptance obligations.
  This documentation decision does not complete TR-2 or platform acceptance.
- [x] Review corrections: declared ownership-manifest dependencies and conservative
  governance-data selection; renderer recovery on a fresh channel without reload
  or process restart; native-state-based controls; correlated fatal/nonfatal
  refusal handling; precise writer/shutdown failures; explicit Python shutdown
  dispatch; restored canonical-adapter acceptance. See the current
  [verification record](P1_VERIFICATION.md) and [notebook](DEVELOPMENT_NOTES.md).

- [x] Canonical `python -m synapse machine` with closed control methods, finite
  negotiation, strict framed JSON, bounded pipe workers, and confirmed shutdown.
- [x] One product envelope schema/release profile shared by Python and Rust;
  generated TypeScript types and standalone incoming validators; common raw corpus.
- [x] RU/EN React bootstrap with honest browser/native failure states, session
  details, redacted diagnostics, and a separate confirmed force-close action.
- [x] Native controller, bundle inventory verification, trusted caller checks,
  scoped Channels, acknowledgment-bounded snapshots, and launch/restart ownership.
- [x] Windows suspended-create/Job code and Linux service/cgroup validation plus
  launch assets. These implementation checkmarks are not platform acceptance.
- [x] External acceptance, mutation campaigns, Python-distribution isolation,
  a single development notebook, and a preview of the actual renderer.
- [x] Permanent prohibition on repository user-data retention; reviewed pending
  artifacts and restricted mutation reporting to non-sensitive structured results.
- [x] Affected-test selection for push/PR checks: existing Python consumers and
  matrix rows, automatically discovered new acceptance, and UI component checks.
  Selection tooling has external behavioral and mutation acceptance.

## Remaining before P1 can close

1. Finish the supported environment matrix: clean Windows provisioning, other
   supported Windows/WebView2 versions and signing. The current PC now has
   [unsigned release, installed control-pipe and caller evidence](P1_VERIFICATION.md#additional-windows-release-and-boundary-acceptance),
   in addition to startup faults, actual adapter/MCP cleanup and installed recovery.
2. Execute the supported Linux graphical systemd/cgroup and installed profile.
   The owner explicitly deferred Linux execution on 2026-10-19; it remains debt.
3. Preserve the scope of the completed [Windows application integration](P1_VERIFICATION.md#windows-application-and-delivery-integration):
   JCS/parameter binding, core-owned access, canonical invocation routing, native
   protected intents and current authorization through recovery/delivery are now
   wired and exercised. This resolves the previous integration gap for the
   invocation profile; it does not automatically implement every P3–P6 owner.
4. Complete the remaining platform/source-case obligations without treating
   Windows evidence as Linux evidence. The installed application now carries
   actual authorized domain replies and all three source classes. The complete
   P2 multi-domain reducer and full source scenarios remain their own work;
   explicit fresh observations are not a claim of continuous synchronization.
5. Complete affected requirement/source-case acceptance and security review at
   every required platform level before marking P1 VERIFIED. The 201 original
   requirement IDs and 44 complete source cases retain their existing identities;
   partial control evidence does not close complete scenarios.

P2–P7 remain fully required. Record observations in the single
[development notebook](DEVELOPMENT_NOTES.md); no reduced completion criterion applies.

## How to update this record

For each implementation change, record requirement IDs, actual state, owner,
remaining dependency, and next action. Link the change and acceptance evidence;
include environment/artifact identity where applicable, mutation method/results,
security findings/fixes, and known limitations. Update source-case and open-condition
rows affected by the work. Do not erase failed/incomplete evidence or label an
unsupported scenario passed. When a contract changes, revise the specification
and traceability together before changing what completion means.

## Work log

| Date | Work and result |
|---|---|
| 2026-10-19 | Integrated bounded control delivery and fixed F-17 equal-duplicate frontend decoding. Final checks: 102 selected Python, 91 frontend, 16 native and 39 installed-pipe cases; 17 delivery/transport and 13 decoder mutants killed. Fresh ordinary-user release installation/process, security/Channels and crash-consent checks passed (16/26/7). F-16 has fresh installed evidence. The owner approved a separate core access authority with protected administration; implementation, domain routing and end-to-end recovery remain open. |
| 2026-10-19 | Checked P1 boundaries against primary public sources and completed the Gold ingestion document reading. Fixed F-16: native CPython test extensions entered Windows resources. Five affected packaging cases, two native directory cases and five final mutants pass; the rebuilt canonical core passes 36 pipe-corpus cases and its full resource inventory check. No installed desktop or P1 completion is inferred; application routing, delivery and the remaining historical document review stay open. |
| 2026-10-19 | Verified protected native recovery without a renderer draft, scoped status, independent JCS limits and strict identifier/array admission. Targeted Python 88 plus three admission-drift, native 11 and TypeScript 26 cases passed; 41 command-foundation, three admission and 23 owner mutants were killed. Application domain routing/delivery and installed acceptance remain unfinished; no P1 closure or publication. |
| 2026-10-19 | Reviewed pending command foundations against the mandatory rules; separated invocation retention, command receipts and observation ownership without splitting their transaction. Forty owner/regression cases and 884 existing ownership cases passed. Twenty-three owner mutants are killed after closing one initially surviving digest-check mutant. These components are not wired into the application; P1, full branch documentation review and domain/installed acceptance remain unfinished. |
| 2026-10-19 | Added 155-case Python regression, 51 installed canonical/native-pipe cases, raw native byte witnesses, unsigned ordinary-user release NSIS, 26 release security/Channel, 7 consent checks and 7 installed GUI transport-damage scenarios (35 assertions). Actual foreign native label/origin contexts refuse commands; eight focused mutants are detected across the identified runs. Clean-machine/signing/wider versions, Linux and unfinished P1 owner/JCS contracts remain open. |
| 2026-10-19 | Continued Windows verification: 119 Python and 31 native checks, six startup cases and real adapter/MCP cleanup passed. Fixed F-08 diagnostic framing, F-10 startup Job ownership and F-11 navigation GET leakage. Six lifecycle/diagnostic and six request-guard mutants were killed. Final ordinary-user installed identity, CSP/navigation, Channels and crash consent passed. Linux remains owner-deferred debt; TR-2/P1 remain IN_PROGRESS. |
| 2026-10-19 | Verified Windows components, native Job launch/containment, exact wire bytes, debug NSIS installation, installed shell/core crash recovery and visible shutdown on an isolated PR #110 checkout. Fixed bundle provenance/launcher leakage; five packaging checks and ten focused mutants passed. Retained the Windows stderr failure, base-reproduced replay failure and explicit platform/security/JCS gaps. TR-2/P1 remain IN_PROGRESS. |
| 2026-10-18 | Created foundation branch from current main; translated supplied specification into English v1; documented detailed coding, acceptance, mutation, and security rules; retained original prototype; established all requirement/case/open-condition registers |
| 2026-10-18 | Clarified the owner's product/test separation: removed eight acceptance sources from product integration/index and preserved them only in development documentation; no product code changed |
| 2026-10-18 | Researched official OWASP, Tauri, React, MDN, and W3C guidance; added English UI security design with external acceptance/mutation scenarios and F-05 consent-assurance clarification; no product behavior or product verification state changed |
| 2026-10-18 | Recorded the owner's permanent prohibition on agent-performed merges in AGENTS.md and coding rules, including local merges, PR merge actions, auto-merge, merge queues, and equivalent integration workarounds; merge authority remains with the owner |
| 2026-10-18 | Implemented the P1 control/bootstrap increment; placed all new product source under `synapse/`; added external acceptance/mutation evidence, a development notebook, build guide, and actual renderer preview; kept platform, JCS, and TR-2 conditions open |
| 2026-10-18 | Recorded the permanent prohibition on writing private user data into repository files; reviewed pending artifacts, removed raw diagnostic excerpts, and verified reporting confidentiality with synthetic acceptance and a separate mutation campaign |
| 2026-10-18 | Connected changed-file dependency selection to existing CI environments and UI component checks; added external selection acceptance and 18 killed tooling mutants; retained platform and installed-desktop gaps |
| 2026-10-18 | Resolved F-06 through the approved TR-2 amendment: one native byte-pipe launch path per platform, no mandatory plugin API or fallback; synchronized provenance and acceptance obligations while retaining TR-2/P1 and platform gaps |
