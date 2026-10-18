# Source provenance and edition policy

Version 1.0. Recorded 2026-10-18.

## Supplied artifacts

The owner supplied the following files. Upload-specific numeric prefixes are
transport names; the retained prototype uses its original descriptive filename.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| Synapse_Prototype_RU_EN_v5.html | 829987 | `cb0363ea4f347bfe66fd9c0ba898a790069e78e9b54e431d6a61a8755db52d92` |
| Synapse_UI_Bridge_Spec_0.7.zip | 202892 | `5ad2aa2536ad4d5eb524f505b5e20371daf073dedb306ef87e230b017e2d5c0b` |
| ZIP: Synapse_UI_Bridge_Spec_0.7.md | 362598 | `a411a61b90542d6b209ea8009579e4883356bf2bd993444e7e286b929713a82b` |
| ZIP: Synapse_UI_Bridge_Spec_0.7.txt | 362598 | `a411a61b90542d6b209ea8009579e4883356bf2bd993444e7e286b929713a82b` |
| ZIP: Synapse_UI_Code_Map.json | 101964 | `ee2695389293996fda914655cc04f58032ac4fa0a93b56a91513e7967627ae6d` |

The Markdown/text source copies are identical. The JSON is a derivative of
Appendix D. English Markdown replaces the duplicated prose representations;
the original bilingual prototype remains unchanged at the owner's request.

## English version 1.0

The [main specification](SYNAPSE_UI_BRIDGE_SPEC_V1.md) translates source sections
1–16 and Appendices A–C. [The code map](UI_CODE_MAP_V1.md), its eight domain
chapters, [index](CODE_MAP_INDEX.md), and separate
[development-only acceptance sources](development-reference/ACCEPTANCE_SOURCES.md)
contain the full Appendix D.

Coverage retained: 201 normative identifiers, 44 numbered acceptance scenarios,
33 numbered open conditions, and 130 detailed file entries. Each map entry keeps
symbols, role, usage, boundary, and evidence basis. Eight acceptance references
are excluded from the product index; their usage is labeled "Acceptance use".
This implements the owner's clarification without dropping source content.
Translation preserves
technical identifiers and distinguishes existing mechanisms from proposed APIs.
Paragraphs are reflowed and explanations translated into direct English; the
requirements are not reduced to an implementation summary.

The edition number is documentation metadata. It does not change wire schemas,
domain-record versions, implementation status, or acceptance verdicts. The three
development-rule documents add the owner's permanent requirements and researched
engineering guidance. Tracker IDs P0–P7, AC-01–AC-44, OC-01–OC-33, and findings
F-01 onward are navigation/status labels, not new protocol vocabulary.

## Approved semantic amendments

On 2026-10-18, the owner authorized resolution of F-06 by retaining native byte
pipes as the transport mechanism. TR-2 now specifies byte fidelity, bounded
stream processing, containment before core execution, and cleanup on launch
failure. The mandatory `tauri-plugin-shell.set_raw_out(true)` instruction was
removed. No parallel plugin launcher, runtime mechanism switch, or fallback
launch path is authorized. Tauri remains the application shell.

This is an explicit amendment to the English implementation contract, not a
claim that the supplied revision 0.7 already said this. Edition 1.0 and the
original requirement/case identifiers remain stable. The supplied-artifact
hashes above and the original prototype are unchanged. No uploaded discussion
or private source text is retained as a repository artifact.

The rationale is to specify the required byte and process behavior without
requiring a helper API that does not itself establish the complete lifecycle
contract. The existing native Windows path can create a suspended process,
assign it to the Job, and only then resume it. This source-level sequence is
not a substitute for platform fault and installed-artifact acceptance.

Public references for the mechanism decision:

- [Tauri shell Command API](https://docs.rs/tauri-plugin-shell/latest/tauri_plugin_shell/process/struct.Command.html):
  raw output and process-group options exist; neither establishes every required
  handle, crash, or replacement guarantee on its own.
- [Windows process creation flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags):
  `CREATE_SUSPENDED` prevents the primary thread running until resumed.
- [AssignProcessToJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject):
  Job assignment is a separate operation with explicit failure conditions.

Section 15.4 of the amended specification and the
[acceptance obligations](ACCEPTANCE_MATRIX.md#native-transport-and-launch-obligations)
retain byte, failure, platform, and installed-product evidence requirements.
F-06 is resolved as a mechanism decision; TR-2, OC-01/09/32, F-07, and P1 retain
their incomplete implementation/acceptance status.

## Inspected repository baseline

Repository: the Synapse checkout containing this document.
Base: `main`, commit `e5249908864ca9481f656092ecf08eb5f6bec567`.
Working branch: `codex/ui-foundation-v1`.

All 130 source-map entries are retained. At this baseline 129 paths exist.
`acceptance/stage4/stage16/test_agent_profile_gold_acceptance.py` is referenced
by the supplied map/documentation but absent from the tree. Its entry explicitly
records this discrepancy; no replacement or successful execution is claimed.
Resolve [F-01](OPEN_CONDITIONS.md#local-findings) before using that reference as
acceptance evidence.

Source-map descriptions express the source review's evidence basis. A path check
does not re-prove every implementation claim, and historical test results are
not new acceptance runs. Implementation must recheck actual owners and call
paths when changing them.

## Public engineering guidance

[Coding rules section 12](CODING_RULES.md#12-sources-and-how-they-are-used)
links the official React, Angular, Tauri, Rust, Python, ESLint, and Google review
guidance used for module organization and review. Specification Appendix C
preserves the source document's protocol precedents separately. Frontend size
signals are local review choices, not universal thresholds claimed by those sources.
