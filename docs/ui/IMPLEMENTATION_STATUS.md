# UI implementation status

Version 1.0. Last updated: 2026-10-18.

Current stage: **P1 — trusted application and bridge implementation in progress.**
The branch is `codex/ui-foundation-v1`,
based on the main commit recorded in [provenance](SOURCE_MANIFEST.md).

The English specification, detailed rules, separated source references, original
prototype, and progress registers are prepared. Publication and final document
verification are recorded in [foundation verification](FOUNDATION_VERIFICATION.md).
P1 control/bootstrap code is now implemented under `synapse/machine` and
`synapse/ui`. The [development guide](DESKTOP_DEVELOPMENT.md) describes its module
boundaries; [P1 verification](P1_VERIFICATION.md) records actual evidence. None of
the 44 complete installed/domain scenarios is claimed passed by component checks.

The [UI security design](UI_SECURITY_DESIGN.md) records public-source research
and its application to the architecture. Bootstrap controls now have implementation
and component evidence; native WebView/installed-platform acceptance remains open.
The consent trust-boundary clarification remains F-05.

## Delivery stages and remaining work

Stages organize dependencies, not reduced scope. Finish the entire assigned
contract at each stage. A finished subtask does not complete a containing feature.
Independent capabilities may progress while unrelated open conditions remain.

| Stage | Scope and exit evidence | Current state |
|---|---|---|
| P0 — Foundation | English v1, all original requirements/map entries, permanent rules, researched UI security design, prototype, traceability, reviewed branch | Documentation prepared |
| P1 — Trusted application and bridge | Bundled canonical machine mode, schemas/JCS, handshake/framing/queues, trusted caller context, diagnostics/CSP, Windows/Linux containment/shutdown; behavioral and mutation evidence | IN_PROGRESS: control/bootstrap implementation and local evidence exist; platform execution, production IPC/CSP, JCS, and F-06 remain open |
| P2 — Authorized reading and synchronization | Project/material reads, domain snapshots/coverage/epochs, progress/freshness, unsubscribe, retained evidence gaps and safe source projections | Not started |
| P3 — Durable commands and permissions | Intent/pre-send recovery, owner acceptance/status/retention, binding conflicts, grant/refuse/revoke, exclusive once-binding, new-task mode policy | Not started |
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

1. Execute Windows Job/handle inheritance, actual adapter/MCP descendants, shell
   death, replacement admission, forced close, and clean installation; mutation-check
   platform-specific behavior on Windows. Cross-compilation is insufficient.
2. Execute the supported Linux systemd/cgroup profile with a real graphical user
   session, shell death, descendant cleanup, and replacement ordering. This workspace
   establishes only refusal outside the service.
3. Run real Tauri-window and installed-artifact checks of Channels, caller identity,
   navigation, IPC under production CSP, and shutdown. WebKit runtime startup is
   blocked by this workspace's native-library layout; browser evidence cannot substitute.
4. Resolve the concrete TR-2 raw-pipe/plugin-shell discrepancy recorded as F-06.
   The specification is unchanged and TR-2 is not marked complete.
5. Finish JCS/parameter-binding and remaining P1 contracts with their actual owner
   integrations. The integer-only control profile does not implement COMP-6 or
   generic domain parameter hashing, readiness, recovery, or delivery classes.
6. Complete affected requirement/source-case acceptance and security review at the
   required platform levels before marking P1 VERIFIED or advancing its completion gate.

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
| 2026-10-18 | Created foundation branch from current main; translated supplied specification into English v1; documented detailed coding, acceptance, mutation, and security rules; retained original prototype; established all requirement/case/open-condition registers |
| 2026-10-18 | Clarified the owner's product/test separation: removed eight acceptance sources from product integration/index and preserved them only in development documentation; no product code changed |
| 2026-10-18 | Researched official OWASP, Tauri, React, MDN, and W3C guidance; added English UI security design with external acceptance/mutation scenarios and F-05 consent-assurance clarification; no product behavior or product verification state changed |
| 2026-10-18 | Recorded the owner's permanent prohibition on agent-performed merges in AGENTS.md and coding rules, including local merges, PR merge actions, auto-merge, merge queues, and equivalent integration workarounds; merge authority remains with the owner |
| 2026-10-18 | Implemented the P1 control/bootstrap increment; placed all new product source under `synapse/`; added external acceptance/mutation evidence, a development notebook, build guide, and actual renderer preview; kept platform, JCS, and TR-2 conditions open |
| 2026-10-18 | Recorded the permanent prohibition on writing private user data into repository files; reviewed pending artifacts, removed raw diagnostic excerpts, and verified reporting confidentiality with synthetic acceptance and a separate mutation campaign |
| 2026-10-18 | Connected changed-file dependency selection to existing CI environments and UI component checks; added external selection acceptance and 18 killed tooling mutants; retained platform and installed-desktop gaps |
