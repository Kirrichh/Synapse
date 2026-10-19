# Acceptance scenario matrix

Version 1.0. Complete end-to-end verdict: **all 44 scenarios NOT_RUN**. Control/component portions now have evidence; this is not a full scenario pass.

[Specification section 15](SYNAPSE_UI_BRIDGE_SPEC_V1.md#15-acceptance) owns the full expected behavior. AC identifiers retain the original case numbers; titles below are navigation labels, not reduced test requirements.

All checks live in the external acceptance layer. Existing test-source references are [development-only](development-reference/ACCEPTANCE_SOURCES.md); product UI never imports, links to, or consumes them.

Pending means the complete scenario has not been established. [P1 verification](P1_VERIFICATION.md) records executed control/component checks and mutations, including their installed-platform and domain limits. One source case can require several independently scheduled scenarios. The cases are required examples, not a claim of complete coverage of every specification requirement.

| Case | Scenario | Primary stage | Source requirement references | Run | Mutation / security / evidence |
|---|---|---|---|---|---|
| AC-01 | Refuse a new request | P3, P7 | APR-1, APR-2 | NOT_RUN | Pending / pending / none |
| AC-02 | Two-file batch, one once-grant | P3, P7 | APR-5, APR-6 | NOT_RUN | Pending / pending / none |
| AC-03 | Competing executions, one once-grant | P3, P7 | APR-8 | NOT_RUN | Pending / pending / none |
| AC-04 | Redeliver a running operation | P3, P7 | APR-6, CMD-12 | NOT_RUN | Pending / pending / none |
| AC-05 | Interrupt after file one | P3, P7 | APR-6, APR-9 | NOT_RUN | Pending / pending / none |
| AC-06 | Changed proposal | P3, P7 | APR-7 | NOT_RUN | Pending / pending / none |
| AC-07 | True single use | P3, P7 | See source case and section contract | NOT_RUN | Pending / pending / none |
| AC-08 | Quiet/filtered project | P2, P7 | SYN-4, SYN-12 | NOT_RUN | Pending / pending / none |
| AC-09 | "Approvals-101" counterexample | P2, P7 | SYN-17 | NOT_RUN | Pending / pending / none |
| AC-10 | Record leaves filter | P2, P7 | SYN-16 | NOT_RUN | Pending / pending / none |
| AC-11 | Late epoch versus current gap | P2, P7 | SYN-14, SYN-15 | NOT_RUN | Pending / pending / none |
| AC-12 | Recover command ID | P3, P7 | CMD-7, CMD-14 | NOT_RUN | Pending / pending / none |
| AC-13 | Acceptance moment | P3, P7 | CMD-10, CMD-11 | NOT_RUN | Pending / pending / none |
| AC-14 | Core death | P1, P7 | CRASH-7 | NOT_RUN | [P1 component and mutation evidence](P1_VERIFICATION.md); Control failure/restart behavior exercised; domain outcome restoration pending |
| AC-15 | Shell death | P1, P7 | CRASH-2, CRASH-6 | NOT_RUN | [P1 component and mutation evidence](P1_VERIFICATION.md); Lifecycle code exists; real OS shell-death/descendant evidence pending |
| AC-16 | Mode change | P3, P7 | MODE-3, MODE-5 | NOT_RUN | Pending / pending / none |
| AC-17 | B → C → retry B | P6, P7 | CMD-17 | NOT_RUN | Pending / pending / none |
| AC-18 | Acceptance crash/lost reply | P6, P7 | CMD-18, CMD-19 | NOT_RUN | Pending / pending / none |
| AC-19 | Invalid UI/CLI/import parameters | P6, P7 | MEM-2, MEM-3 | NOT_RUN | Pending / pending / none |
| AC-20 | Preview | P6, P7 | MEM-4, MEM-6 | NOT_RUN | Pending / pending / none |
| AC-21 | Old-session binding after rebind | P6, P7 | MEM-8 | NOT_RUN | Pending / pending / none |
| AC-22 | Limit below unavoidable usage | P6, P7 | MEM-9 | NOT_RUN | Pending / pending / none |
| AC-23 | Rust/React reply loss | P3, P7 | CMD-3, CMD-20 | NOT_RUN | Pending / pending / none |
| AC-24 | Mutation before ready | P1, P7 | BOOT-7, BOOT-8, CMD-8 | NOT_RUN | [P1 component and mutation evidence](P1_VERIFICATION.md); Early request refusal exercised in real CLI and shared contracts; domain intent recovery and installed UI pending |
| AC-25 | Oversize/corrupt frame | P1, P7 | TR-10, TR-11, TR-12, TR-13 | NOT_RUN | [P1 component and mutation evidence](P1_VERIFICATION.md); Framing/corruption/backpressure exercised in core/native components; installed Channels and domain delivery classes pending |
| AC-26 | Record restored behind cursor | P2, P7 | DEL-7, DEL-8 | NOT_RUN | Pending / pending / none |
| AC-27 | Local information boundary | P1, P7 | SEC-8 | NOT_RUN | Pending / pending / none |
| AC-28 | Discovery without loading | P5, P7 | AGT-1, CONN-4 | NOT_RUN | Pending / pending / none |
| AC-29 | Explicit probe | P5, P7 | CONN-4, CONN-5, SEC-11 | NOT_RUN | Pending / pending / none |
| AC-30 | Profile drift | P5, P7 | AGT-4, AGT-5, TOOL-2 | NOT_RUN | Pending / pending / none |
| AC-31 | Concurrent settings | P5, P7 | CMD-17, CONN-2 | NOT_RUN | Pending / pending / none |
| AC-32 | Disable/delete | P5, P7 | CONN-6 | NOT_RUN | Pending / pending / none |
| AC-33 | Cancellation scope | P4, P7 | MAP-8 | NOT_RUN | Pending / pending / none |
| AC-34 | Secrets | P1, P7 | CMD-21, LLM-3 | NOT_RUN | [P1 component and mutation evidence](P1_VERIFICATION.md); Diagnostic redaction exercised; protected domain secrets/bindings pending |
| AC-35 | Model/tool roles | P5, P7 | LLM-1, LLM-4, TOOL-1 | NOT_RUN | Pending / pending / none |
| AC-36 | Instructions | P5, P7 | INST-1, INST-2, INST-3 | NOT_RUN | Pending / pending / none |
| AC-37 | Effect-free reading | P2, P7 | MAP-7, READ-1 | NOT_RUN | Pending / pending / none |
| AC-38 | Changed material | P4, P7 | READ-2, READ-3, READ-4 | NOT_RUN | Pending / pending / none |
| AC-39 | Overlapping StateSync ranges | P2, P7 | SYN-10, SYN-20 | NOT_RUN | Pending / pending / none |
| AC-40 | Controls | P6, P7 | MEM-14, MEM-15, MEM-16 | NOT_RUN | Pending / pending / none |
| AC-41 | Chart | P6, P7 | MEM-10, MEM-17 | NOT_RUN | Pending / pending / none |
| AC-42 | Memory units | P6, P7 | MEM-18 | NOT_RUN | Pending / pending / none |
| AC-43 | Unsubscribe/UI restart | P2, P7 | MAP-8, SYN-21, SYN-22 | NOT_RUN | [P1 recovery component evidence](P1_VERIFICATION.md): fresh bootstrap subscription after corruption without reload/spawn; complete domain unsubscribe/restart remains pending |
| AC-44 | Bottom-panel sources | P2, P7 | DEL-7, DEL-8, DEL-9, LLM-5, SEC-10 | NOT_RUN | Pending / pending / none |

## Evidence to add when executing a case

Record the exact scenario/environment, external acceptance entry, exercised product behavior, observed UI result and owner records, build identity when applicable, outcome, mutation method and surviving-mutant analysis, security-negative result, and remaining limitations. Heavy recovery/platform/concurrency cases get independent acceptance files and isolated resources. Do not mark a case passed from a similarly named historical test or fixture.

## Native transport and launch obligations

The approved F-06 amendment resolves the mechanism choice. It does not waive
the following behavioral obligations from
[TR-2 and section 15.4](SYNAPSE_UI_BRIDGE_SPEC_V1.md#154-native-transport-and-launch-acceptance).
The rows expand coverage within the existing requirements and cases; they do not
renumber the 44 source scenarios or mark any of them passed.

| Boundary / requirements | Required observable behavior | Existing evidence and remaining work |
|---|---|---|
| Byte fidelity — TR-1/2/3/4, ACC-1/2/3 | Exact headers/payloads survive fragmented and consecutive delivery, Cyrillic, multibyte characters, and escaped line endings; read boundaries never become frame boundaries; stderr never enters stdout. | Component portions: [Python frames](../../acceptance/ui_bridge/test_frame_acceptance.py), [Rust transport](../../acceptance/ui_bridge/native/transport.rs), and [real canonical interop](../../acceptance/ui_bridge/native/canonical_core.rs). Existing cases cover fragmentation, consecutive frames, Unicode, and short writes. The Windows follow-up adds executed escaped/literal line-ending cases (10 Python, 3 Rust) and two killed rewrite mutants. The full corpus through desktop/installed pipes remains pending. |
| Bounds and damage — TR-10/11, AC-25 | Refuse oversized length before payload allocation; reject partial header/body at EOF; terminate a corrupt stream without scanning for another header. | Component frame cases, [raw corpus](../../acceptance/ui_bridge/test_corpus_acceptance.py), and [real CLI](../../acceptance/ui_bridge/test_machine_cli_acceptance.py) have recorded evidence in [P1 verification](P1_VERIFICATION.md). Desktop/installed execution and complete AC-25 remain pending. |
| Writes and overload — TR-6/9/12, CMD-8, CRASH-15 | Complete short writes; distinguish full queue, closed pipe/dead writer, and deadline; never equate a write receipt with owner acceptance or shutdown confirmation. | [Python transport](../../acceptance/ui_bridge/test_transport_acceptance.py), [heavy backpressure](../../acceptance/ui_bridge/test_output_backpressure_acceptance.py), [native writer failures](../../acceptance/ui_bridge/native/writer_failure.rs), and [native refusal/shutdown cases](../../acceptance/ui_bridge/native/refusals.rs) have component evidence. Installed nominal shutdown passed. The Windows unread-output case remains failed on its stderr CRLF assertion (F-08); overload, real-Channel and domain-acceptance portions remain pending. |
| Windows startup faults — TR-2, BOOT-2/4, CRASH-9/10/11 | Observe no core execution before Job assignment. Inject preparation, assignment, and resume failures; interrupt intermediate startup stages; verify no uncontrolled execution, leaked process, or fallback. | Windows host execution covers missing-SystemRoot preparation and missing-interpreter refusal without fallback. Assignment/resume failures, interrupted startup and pre-execution ordering remain NOT_RUN under OC-01/F-07. Exercise real boundaries without product test hooks. |
| Handles and actual descendants — CRASH-9/10/11/12, TOOL-4 | Observe only required child pipe ends inherited; no Job lifetime handle retained by a child; exercise actual adapters and MCP server launch paths, including descendants and nested launch behavior. | [Windows containment](../../acceptance/ui_bridge/native/windows_containment.rs) excludes one unrelated inheritable event and observes Job termination of a synthetic core/descendant after owner loss. Actual adapter/MCP integration, exhaustive lifetime-handle checks and Linux execution remain NOT_RUN under OC-01/F-07. |
| Shell death and replacement — CRASH-2/6/12/14/15, AC-14/15 | On Windows and Linux, establish the fate of old local executors before replacement effects in the same scope. Unsupported Linux service/cgroup conditions refuse startup. Keep remote outcomes explicitly uncertain. | [Windows installed evidence](../../acceptance/ui_bridge/evidence/windows-installed-results.json) records shell/core death, no overlapping core and replacement after exit. Active-Job and Job-close mutants were killed. Real adapters, broader races and supported Linux systemd cleanup remain NOT_RUN; OC-01/09 and F-07 remain OPEN. |
| Installed path — BOOT-1/9, TR-2/4/5, SEC-5, ACC-2/3/4/5 | Drive the real window and verified installed artifact through the same canonical native byte/process path on Windows/Linux; observe Channels, caller checks, production CSP, crash recovery, and build identity. | Windows debug NSIS installed identity/resources and real ready/crash/shutdown window states passed within the [recorded scope](P1_VERIFICATION.md#windows-verification-follow-up). Negative caller/CSP/navigation/Channel cases, release/clean-machine and Linux installed acceptance remain NOT_RUN. P1/F-07 remain open. |

Existing component mutation results are linked in [P1 verification](P1_VERIFICATION.md#mutation-evidence);
they do not establish the unexecuted platform rows. Add mutations for byte
rewriting/truncation, bound removal, failure misclassification, premature resume,
handle leakage, and replacement before cleanup as the affected cases are executed.
Keep heavy platform scenarios in independent acceptance files with isolated
synthetic resources. No product imports, fixtures, hooks, or alternate launch
mode may be introduced to satisfy them.
