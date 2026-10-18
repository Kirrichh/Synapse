# Documentation foundation verification

Date: 2026-10-18. Scope: the documentation change on
`codex/ui-foundation-v1`, based on the commit in [provenance](SOURCE_MANIFEST.md).

## Scope of the change

The change adds English Markdown working documents, the unchanged supplied
prototype as a reference asset, and permanent development guidance in root
AGENTS.md. It changes no product source, executable behavior, dependencies,
packaging, or acceptance implementation. Temporary document-processing tools
used during preparation are outside the repository and are not deliverables.

## Document review and integrity

| Check | Observed result |
|---|---|
| Normative identifiers | All 201 source identifiers retained once in the English specification; no missing or added normative IDs |
| Source scenarios and open conditions | All 44 numbered scenarios and 33 conditions retained and tracked; no product completion claimed |
| Source map | All 130 original paths/entries translated, retaining symbols, role, usage, boundary, and evidence basis |
| Product/acceptance reference separation | 122 non-test references in product mapping/index; eight test references only in development documentation, with Acceptance use labels |
| Source path inspection | 129 paths present; one stale acceptance path explicitly recorded as F-01, without replacement or execution claims |
| Prototype integrity | Retained HTML exactly matches supplied SHA-256 `cb0363ea4f347bfe66fd9c0ba898a790069e78e9b54e431d6a61a8755db52d92` |
| ZIP provenance | Markdown and text source copies have identical bytes/hashes; source archive and derivative map hashes recorded |
| Language | New Markdown documents use English; original bilingual HTML is an explicitly retained reference |
| Links and Markdown structure | Local file links/heading targets, fenced blocks, and table structure checked; whitespace checked with git diff --check |
| Public engineering references | All 11 coding-rule source URLs responded successfully during this review; the references support the stated principles, not a universal file-size gate |

The translation review preserves command identity, authoritative owner records,
single-use consent, coverage/epochs, process lifecycle, local-information limits,
memory preview/acceptance, administrative probes, and UI control requirements.
References to existing functions remain foundations with explicit limits.
The main document retains its original normative IDs and source numbering;
domain chapters split the long appendix without omitting entries.

These are document-maintenance checks, not product acceptance tests or evidence
that runtime behavior is correct. No file/line/test-count assertions have been
added to the repository as tests.

## Acceptance, mutation, and security status

- Product acceptance: **NOT_RUN**. This change contains no product implementation.
  All 44 future source cases remain NOT_RUN in the acceptance matrix.
- Mutation verification: **NOT_APPLICABLE** to this change because executable
  behavior was not changed. No mutation score or product-verification claim is
  inferred from document checks or the unchanged demo.
- Security review: checked the documented trust/authority boundaries, test/product
  separation, safe reads, secrets, local information, replay/recovery, and process
  ownership obligations. No live-system security validation is claimed.
- Full runtime suites: not rerun for prose and an unchanged reference asset.
  Historical results in existing documents retain their original scope.

## Owner clarification incorporated

The initial document layout used the label UI use for every original source-map
entry, including eight acceptance sources. This could suggest product integration
with tests. The final layout removes those entries from the product map/index
and places them in [development-only references](development-reference/ACCEPTANCE_SOURCES.md).
Root AGENTS.md and acceptance rules explicitly prohibit product use as imports,
assets, navigation, startup hooks, state feeds, or implementations. No product
code depended on those documents or tests during this work.

## Publication record

Deliver this work as a commit on the named branch. The branch head is the
reviewable artifact; publication does not merge it into main or implement P1–P7.
The final handoff identifies the published commit and document entry point.
