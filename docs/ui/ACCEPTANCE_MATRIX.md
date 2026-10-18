# Acceptance scenario matrix

Version 1.0. Current product verdict: **all 44 scenarios NOT_RUN**.

[Specification section 15](SYNAPSE_UI_BRIDGE_SPEC_V1.md#15-acceptance) owns the full expected behavior. AC identifiers retain the original case numbers; titles below are navigation labels, not reduced test requirements.

All checks live in the external acceptance layer. Existing test-source references are [development-only](development-reference/ACCEPTANCE_SOURCES.md); product UI never imports, links to, or consumes them.

Pending means no evidence has been produced for this workstream. Mutation and security remain pending for future executable changes; they are not satisfied by this documentation change. One source case can require several independently scheduled scenarios. The cases are required examples, not a claim of complete coverage of every specification requirement.

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
| AC-14 | Core death | P1, P7 | CRASH-7 | NOT_RUN | Pending / pending / none |
| AC-15 | Shell death | P1, P7 | CRASH-2, CRASH-6 | NOT_RUN | Pending / pending / none |
| AC-16 | Mode change | P3, P7 | MODE-3, MODE-5 | NOT_RUN | Pending / pending / none |
| AC-17 | B → C → retry B | P6, P7 | CMD-17 | NOT_RUN | Pending / pending / none |
| AC-18 | Acceptance crash/lost reply | P6, P7 | CMD-18, CMD-19 | NOT_RUN | Pending / pending / none |
| AC-19 | Invalid UI/CLI/import parameters | P6, P7 | MEM-2, MEM-3 | NOT_RUN | Pending / pending / none |
| AC-20 | Preview | P6, P7 | MEM-4, MEM-6 | NOT_RUN | Pending / pending / none |
| AC-21 | Old-session binding after rebind | P6, P7 | MEM-8 | NOT_RUN | Pending / pending / none |
| AC-22 | Limit below unavoidable usage | P6, P7 | MEM-9 | NOT_RUN | Pending / pending / none |
| AC-23 | Rust/React reply loss | P3, P7 | CMD-3, CMD-20 | NOT_RUN | Pending / pending / none |
| AC-24 | Mutation before ready | P1, P7 | BOOT-7, BOOT-8, CMD-8 | NOT_RUN | Pending / pending / none |
| AC-25 | Oversize/corrupt frame | P1, P7 | TR-10, TR-11, TR-12, TR-13 | NOT_RUN | Pending / pending / none |
| AC-26 | Record restored behind cursor | P2, P7 | DEL-7, DEL-8 | NOT_RUN | Pending / pending / none |
| AC-27 | Local information boundary | P1, P7 | SEC-8 | NOT_RUN | Pending / pending / none |
| AC-28 | Discovery without loading | P5, P7 | AGT-1, CONN-4 | NOT_RUN | Pending / pending / none |
| AC-29 | Explicit probe | P5, P7 | CONN-4, CONN-5, SEC-11 | NOT_RUN | Pending / pending / none |
| AC-30 | Profile drift | P5, P7 | AGT-4, AGT-5, TOOL-2 | NOT_RUN | Pending / pending / none |
| AC-31 | Concurrent settings | P5, P7 | CMD-17, CONN-2 | NOT_RUN | Pending / pending / none |
| AC-32 | Disable/delete | P5, P7 | CONN-6 | NOT_RUN | Pending / pending / none |
| AC-33 | Cancellation scope | P4, P7 | MAP-8 | NOT_RUN | Pending / pending / none |
| AC-34 | Secrets | P1, P7 | CMD-21, LLM-3 | NOT_RUN | Pending / pending / none |
| AC-35 | Model/tool roles | P5, P7 | LLM-1, LLM-4, TOOL-1 | NOT_RUN | Pending / pending / none |
| AC-36 | Instructions | P5, P7 | INST-1, INST-2, INST-3 | NOT_RUN | Pending / pending / none |
| AC-37 | Effect-free reading | P2, P7 | MAP-7, READ-1 | NOT_RUN | Pending / pending / none |
| AC-38 | Changed material | P4, P7 | READ-2, READ-3, READ-4 | NOT_RUN | Pending / pending / none |
| AC-39 | Overlapping StateSync ranges | P2, P7 | SYN-10, SYN-20 | NOT_RUN | Pending / pending / none |
| AC-40 | Controls | P6, P7 | MEM-14, MEM-15, MEM-16 | NOT_RUN | Pending / pending / none |
| AC-41 | Chart | P6, P7 | MEM-10, MEM-17 | NOT_RUN | Pending / pending / none |
| AC-42 | Memory units | P6, P7 | MEM-18 | NOT_RUN | Pending / pending / none |
| AC-43 | Unsubscribe/UI restart | P2, P7 | MAP-8, SYN-21, SYN-22 | NOT_RUN | Pending / pending / none |
| AC-44 | Bottom-panel sources | P2, P7 | DEL-7, DEL-8, DEL-9, LLM-5, SEC-10 | NOT_RUN | Pending / pending / none |

## Evidence to add when executing a case

Record the exact scenario/environment, external acceptance entry, exercised product behavior, observed UI result and owner records, build identity when applicable, outcome, mutation method and surviving-mutant analysis, security-negative result, and remaining limitations. Heavy recovery/platform/concurrency cases get independent acceptance files and isolated resources. Do not mark a case passed from a similarly named historical test or fixture.
