# Open conditions and findings

Version 1.0. All 33 original conditions remain OPEN.

[Specification section 16](SYNAPSE_UI_BRIDGE_SPEC_V1.md#16-open-condition-register) owns the conditions. OC numbers match its rows. Each is scoped to dependent capabilities; unrelated work can proceed.

To close a row, establish its owner and precise contract/configuration, implement its mechanism where required, and link observable acceptance, mutation evidence for executable changes, and security review. A design statement or existing similarly named function alone cannot close it. The stage lead must name the actual responsible owner before implementation.

| Condition | Work still required | Primary stage | Status / evidence |
|---|---|---|---|
| OC-01 | Implement/test Windows Job and Linux systemd user profiles, actual adapter launch paths, and environment matrix; CRASH-2 otherwise cannot hold (Architecture/implementation) | P1 | OPEN / [Control and scoped Windows installed evidence](P1_VERIFICATION.md#windows-verification-follow-up); full condition remains pending |
| OC-02 | Locate durable once-grant/execution binding in the actual execution path; define proposal-identity record (Authority owner) | P3 | OPEN / none |
| OC-03 | Define state-domain registry, snapshot bases, position encoding, progress cadence, subscription buffer size/retention, multiple subscribers (Contract/implementation) | P2 | OPEN / none |
| OC-04 | Coordinate whole-task stopping separately from invocation cancellation (Contract) | P4 | OPEN / none |
| OC-05 | Define project.tree.read and material.read (Contract) | P2 | OPEN / none |
| OC-06 | Define main-agent conversation history and composite independently versioned snapshots without global atomicity (Contract) | P4 | OPEN / none |
| OC-07 | Define authoritative acceptance records for approvals/messages/policy changes, using invocation claim as a pattern (Contract) | P3 | OPEN / none |
| OC-08 | Reconcile lost command identity; protect sensitive pre-send content (Procedure) | P3 | OPEN / none |
| OC-09 | Define protocol core shutdown and confirmation (CRASH-15) (Contract) | P1 | OPEN / [Control and scoped Windows installed evidence](P1_VERIFICATION.md#windows-verification-follow-up); full condition remains pending |
| OC-10 | Define terminal-result retention/TTL; expiry never makes an old command new (Policy) | P3 | OPEN / none |
| OC-11 | Implement isolated dry reassessment, all mutable stores/references, report and basis binding (MEM-4–6) (Contract/implementation) | P6 | OPEN / none |
| OC-12 | Core mathematical invariants and common UI/CLI/import validation (MEM-3) (Contract) | P6 | OPEN / none |
| OC-13 | Authoritative memory acceptance records and late B→C→retry B reconciliation (Memory owner) | P6 | OPEN / none |
| OC-14 | Verify recovery at every acceptance effect: republication/report/decision/snapshot/boundary/retention/rebind/reply loss (Acceptance) | P6 | OPEN / none |
| OC-15 | Byte accounting, reserves, shared-block attribution, double-count prevention, raw_capacity category (Contract) | P6 | OPEN / none |
| OC-16 | Define/implement memory RAM-cache measurement and enforcement (Contract/implementation) | P6 | OPEN / none |
| OC-17 | Define consequence metrics, sources, and normalization (Contract) | P6 | OPEN / none |
| OC-18 | Exact hello/select/ready schemas, common profile, permitted command classes (Contract) | P1 | OPEN / [Control implementation and partial evidence](P1_VERIFICATION.md); full condition remains pending |
| OC-19 | Intent issuance/storage, durable scope/content/command binding, restart recovery (Contract/implementation) | P3 | OPEN / none |
| OC-20 | Numeric frame limits and exhaustion behavior (Contract) | P1 | OPEN / [Control implementation and partial evidence](P1_VERIFICATION.md); full condition remains pending |
| OC-21 | Secret exclusion and diagnostic log/display volume limits (Contract) | P1 | OPEN / [Control implementation and partial evidence](P1_VERIFICATION.md); full condition remains pending |
| OC-22 | Exact production CSP permitting IPC and excluding unnecessary networking (Configuration/acceptance) | P1 | OPEN / [Control and scoped Windows installed evidence](P1_VERIFICATION.md#windows-verification-follow-up); full condition remains pending |
| OC-23 | Evidential missing-record rechecks/range invalidation for restoration behind cursors (Contract/implementation) | P2 | OPEN / none |
| OC-24 | Agent-definition administration: accepted versions, admission, preferences, expected revision, disabling, command recovery (Contract/implementation) | P5 | OPEN / none |
| OC-25 | Safe bounded probes, no work data, cost/resource bounds, result sources, no hidden plugin loading during reads (Contract/acceptance) | P5 | OPEN / none |
| OC-26 | Model administration, versioned protected secret references, separate runtime/agent model access (Contract/implementation) | P5 | OPEN / none |
| OC-27 | Managed local model runtime ownership/files/readiness/download/GPU/memory/shutdown, only if this mode is enabled (Contract/implementation) | P5 | OPEN / none |
| OC-28 | MCP saved definitions, inventory/descriptor verification, contract updates, server shutdown (Contract/implementation) | P5 | OPEN / none |
| OC-29 | Versioned instruction registry, scope/order/variables, public-model restrictions (Contract/implementation) | P5 | OPEN / none |
| OC-30 | Own-material saves with expected version/conflict; evidence remains immutable (Contract/implementation) | P4 | OPEN / none |
| OC-31 | Derived control metadata, exact input/equalizer, volume units, scoped measurements, four-axis chart data (Contract/UI) | P6 | OPEN / none |
| OC-32 | Rust/Python delivery split, pre-negotiation bootstrap limit, damaged-stream shutdown, control queues, read-only command reconciliation (Contract/implementation) | P1/P3 | OPEN / [Control and scoped Windows installed evidence](P1_VERIFICATION.md#windows-verification-follow-up); full condition remains pending |
| OC-33 | Real-executor card projections, domain freshness, unsubscribe without cancellation, distinct role/profile/invocation (Contract/implementation) | P2/P4 | OPEN / none |

## Local findings

| Finding | Status and impact | Next action / closure evidence |
|---|---|---|
| F-01 — stale acceptance reference | OPEN. The source names acceptance/stage4/stage16/test_agent_profile_gold_acceptance.py, which is absent at the reviewed baseline. Preserved only in development documentation; it is never a product integration target or executed evidence. | Locate the documented scenario/history or correct its source reference after inspection. Do not invent a substitute or a passing result. |
| F-02 — demo permission semantics | OPEN for implementation. Demo behavior can affect the current task; MODE-3 fixes policy for existing tasks. | Implement the specification and verify source case AC-16; preserve visual intent without copying demo execution semantics. |
| F-03 — required screens absent from demo | OPEN for implementation. Memory/learning and MCP administration are specified beyond the retained HTML. | Implement their full specified contracts/layout and acceptance; absence from demo cannot reduce scope. |
| F-04 — acceptance references mixed with integration | RESOLVED in this documentation edition. Eight source references are separated from product mapping/index. | Developer-only acceptance source document preserves the translation; permanent rules prohibit product consumption, imports, navigation, assets, or runtime feeds from acceptance. No product code was changed. |
| F-05 — consent assurance under renderer compromise | OPEN design clarification. Trusted window context and Rust-issued intent do not prove human approval if the privileged renderer is fully compromised. Existing APR proposal binding, current authority, and durable once-use requirements still apply. | Define the approval threat model and trusted display/input boundary; if consent must withstand this compromise, design and accept a separately trusted confirmation path before claiming that guarantee. See [UI security design](UI_SECURITY_DESIGN.md#2-threat-model-and-trust-boundaries). |
| F-06 — TR-2 transport mechanism | RESOLVED by the owner-approved amendment of 2026-10-18: native byte pipes are the sole core transport mechanism; the mandatory plugin-shell API is removed. Byte fidelity, canonical entry, containment before execution, handle restrictions, and failure cleanup remain required. No alternate plugin launcher or fallback is permitted. | [Amended TR-2 and section 15.4](SYNAPSE_UI_BRIDGE_SPEC_V1.md#4-framing-and-transport), [decision provenance](SOURCE_MANIFEST.md#approved-semantic-amendments), and [acceptance obligations](ACCEPTANCE_MATRIX.md#native-transport-and-launch-obligations). Closure records the mechanism decision only. TR-2/P1 remain IN_PROGRESS; OC-01/09/32 and F-07 remain OPEN pending behavioral/platform evidence. |
| F-07 — installed-platform evidence | OPEN acceptance limitation. Windows debug NSIS installation, nominal IPC, shutdown, shell/core death and replacement now have scoped evidence; host failure checks were elevated. The continuation verifies Windows startup faults, creation-time Job membership, actual adapter/MCP descendants and canonical diagnostics. Ordinary-user final artifact, installed CSP/command/navigation, real Channels and crash-consent checks now pass after F-11. Broader caller/environment and clean-machine/release coverage remain open. Linux systemd/native-window execution is explicitly deferred by the owner (2026-10-19). | Complete the [remaining platform obligations](ACCEPTANCE_MATRIX.md#native-transport-and-launch-obligations), including clean-machine/release checks and platform mutants. No sandbox bypass, one-PID fallback or browser substitute is permitted. |
| F-08 — Windows diagnostic line endings | RESOLVED for the canonical Windows stderr/native-reader boundary. The producer explicitly emits one LF delimiter; the reader retains its strict allowlist. | [119 Python and 31 native checks](P1_VERIFICATION.md#continued-windows-fault-and-runtime-verification), including INVALID_LENGTH through real bundled stderr; removing LF control restores the exact-byte failure and kills the mutant. |
| F-09 — bundled installation provenance | RESOLVED for the exercised bundle builder. Raw pip output retained local provenance and build-interpreter launchers. The builder removes those artifacts and their RECORD rows before inventory binding; raw staging stays outside source. | [Five acceptance cases and six killed mutants](P1_VERIFICATION.md#windows-verification-follow-up); rebuilt and installed resource inventories verified. This is a scoped build-metadata fix, not a general historical privacy audit. |
| F-10 — suspended child orphan during startup | RESOLVED at the exercised boundaries. Post-creation Job assignment left a suspended child alive if its owner died before assignment. Creation now includes the Job-list attribute; failures refuse launch without fallback. | [External debugger evidence and mutations](P1_VERIFICATION.md#continued-windows-fault-and-runtime-verification): preparation/refusal, invalid Job, resume failure, and owner death immediately after creation or before resume. The observer retained child handles; cleanup after a failed assertion was not counted as success. |
| F-11 — denied navigation still sends GET | RESOLVED for the exercised Windows request boundary. Navigation cancellation preserved the document but the local synthetic receiver observed an outbound GET. Native request interception now supplies an empty local refusal before dispatch; startup content waits for successful guard installation. | [Installed before/after and six detecting mutants](P1_VERIFICATION.md#windows-navigation-request-boundary-f-11). The fixed installed artifact records zero requests and working IPC/Channels/crash consent. No private data disclosure was observed. The broader OC-22/platform matrix remains OPEN. |

Maintain unresolved findings with impact and next action. Historical source errors do not authorize weakening runtime or acceptance contracts.
