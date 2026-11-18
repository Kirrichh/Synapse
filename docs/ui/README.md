# Synapse UI working documents

Version 1.0. Prepared on 2026-10-18 for the UI foundation workstream.

Start with the [implementation status](IMPLEMENTATION_STATUS.md). P1 now has a
canonical control endpoint, a modular Tauri/React bootstrap application, and
external behavioral/mutation evidence. P1 remains open for the platform and
specification conditions listed in the status record.

F-06 is resolved by the [approved TR-2 amendment](SOURCE_MANIFEST.md#approved-semantic-amendments):
retain native byte pipes with explicit byte and process guarantees. The
[acceptance obligations](ACCEPTANCE_MATRIX.md#native-transport-and-launch-obligations)
remain separate from that mechanism decision; TR-2 and P1 are still IN_PROGRESS.

## Requirements and references

| Document | Purpose |
|---|---|
| [Bridge specification v1](SYNAPSE_UI_BRIDGE_SPEC_V1.md) | English edition of all source requirements, 44 acceptance cases, 33 open conditions, command catalogue, and panel mapping |
| [Code map v1](UI_CODE_MAP_V1.md) | Appendix D and 122 non-test references in eight chapters |
| [File/symbol index](CODE_MAP_INDEX.md) | Product integration and non-test reference navigation |
| [Development-only acceptance sources](development-reference/ACCEPTANCE_SOURCES.md) | Eight original acceptance entries/index, outside product UI integration |
| [Prototype reference](prototype/README.md) | Original bilingual HTML, preserved byte for byte |
| [Source manifest](SOURCE_MANIFEST.md) | Supplied-file provenance, hashes, translation/version policy, inspected baseline |
| [Machine control profile v1](MACHINE_PROFILE_V1.md) | Released control envelope, negotiation, resource limits, and explicit authority boundary |
| [Desktop development](DESKTOP_DEVELOPMENT.md) | Product layout under `synapse/`, build/launch steps, platform requirements, and delivery ownership |

## Mandatory development rules

| Document | Purpose |
|---|---|
| [Coding rules](CODING_RULES.md) | Responsibilities, module boundaries, user-data prohibition, size review signals, frontend/native/core organization, and researched sources |
| [Acceptance rules](ACCEPTANCE_RULES.md) | Strict product separation, useful coverage, parallel heavy scenarios, mutation verification, and full completion |
| [Security rules](SECURITY_RULES.md) | Continuous boundary review and concrete anti-bypass obligations |
| [UI security design](UI_SECURITY_DESIGN.md) | Researched threat model, native/renderer controls, consent limits, secret handling, and external acceptance scenarios |

The root [agent guide](../../AGENTS.md) links these rules so later contributors
encounter them before implementing UI or supporting changes. Existing ownership
and canonical-entry constraints continue to apply.

## Progress records

| Document | Purpose |
|---|---|
| [Implementation status](IMPLEMENTATION_STATUS.md) | Current stage, remaining delivery work, decisions, and handoff |
| [Development notes](DEVELOPMENT_NOTES.md) | One chronological record of system behavior, implementation decisions, findings, and follow-up work |
| [Requirement matrix](REQUIREMENT_MATRIX.md) | Every normative identifier and its implementation/verification state |
| [Acceptance matrix](ACCEPTANCE_MATRIX.md) | All 44 source scenarios, future evidence, and mutation/security status |
| [Open conditions](OPEN_CONDITIONS.md) | All 33 source conditions and local source-reference findings |
| [Foundation verification](FOUNDATION_VERIFICATION.md) | Checks performed for this documentation-only change |
| [P1 verification](P1_VERIFICATION.md) | Executed implementation checks, mutation results, security review, and unverified platform boundaries |

## Authority and maintenance

The owner's instructions and repository governance apply first. The bridge
specification is the implementation contract; its code map describes foundations
and their limits. Rules govern how work is done; progress records report evidence
and cannot weaken requirements. The prototype preserves visual intent, not
protocol truth. A discrepancy must be recorded and resolved in the governing
document instead of silently choosing convenient behavior.

Version 1.0 is the English documentation edition. It does not assert protocol
v1, product completion, or closed open conditions. Keep the original requirement
IDs stable. Record semantic amendments explicitly; update related acceptance and
progress records in the same change. Domain chapters split navigation without
reducing translation coverage. All new documents are Markdown and English.

The retained prototype is an intentional original RU/EN reference asset outside
product source and packaging. Its single-file structure is not the implementation
architecture. Product localization can remain RU/EN under English development
documentation.
