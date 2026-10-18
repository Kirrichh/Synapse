# UI implementation status

Version 1.0. Last updated: 2026-10-18.

Current stage: **P0 — documentation foundation prepared; product implementation
has not started in this workstream.** The branch is `codex/ui-foundation-v1`,
based on the main commit recorded in [provenance](SOURCE_MANIFEST.md).

The English specification, detailed rules, separated source references, original
prototype, and progress registers are prepared. Publication and final document
verification are recorded in [foundation verification](FOUNDATION_VERIFICATION.md).
This status does not claim that existing backend foundations satisfy the new
desktop contracts or that any of the 44 product cases have passed.

The [UI security design](UI_SECURITY_DESIGN.md) records public-source research
and its application to the planned architecture. Its controls await implementation
and acceptance; the consent trust-boundary clarification is tracked as F-05.

## Delivery stages and remaining work

Stages organize dependencies, not reduced scope. Finish the entire assigned
contract at each stage. A finished subtask does not complete a containing feature.
Independent capabilities may progress while unrelated open conditions remain.

| Stage | Scope and exit evidence | Current state |
|---|---|---|
| P0 — Foundation | English v1, all original requirements/map entries, permanent rules, researched UI security design, prototype, traceability, reviewed branch | Documentation prepared |
| P1 — Trusted application and bridge | Bundled canonical machine mode, schemas/JCS, handshake/framing/queues, trusted caller context, diagnostics/CSP, Windows/Linux containment/shutdown; behavioral and mutation evidence | Not started |
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
- [Foundation verification](FOUNDATION_VERIFICATION.md): only checks actually
  performed for this documentation change.

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
explicit limits. This change adds no Tauri application, React implementation,
machine-mode runtime, new domain command, product test dependency, or runtime
behavior. Historical acceptance references are kept in
[development-only sources](development-reference/ACCEPTANCE_SOURCES.md), outside
the product integration index. They are not product links or data feeds.

## Immediate next implementation work

1. Define the first machine profile and per-command ownership contracts from
   BOOT/TR/COMP/MAP, including finite bootstrap/resource values and read-only status.
2. Design Windows/Linux containment around the actual existing process paths;
   state supported environment requirements before enabling execution.
3. Establish module boundaries for the shell, transport, client intents, Python
   dispatch, and frontend projections according to the coding rules; define the
   capability/CSP profiles and threat boundaries from the UI security design.
4. Implement and externally accept that assigned scope fully, with targeted
   mutants and security-negative cases, before recording VERIFIED.

These are next tasks, not code changes included in the documentation branch.

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
