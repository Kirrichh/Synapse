# Original interface prototype

Open [Synapse_Prototype_RU_EN_v5.html](Synapse_Prototype_RU_EN_v5.html) in a
browser to inspect the supplied interactive design. Download the file first
when viewing the repository through GitHub, which displays HTML as source.
It is a standalone reference and needs no product backend.

The HTML is preserved byte for byte, including Russian/English presentation,
embedded scripts/styles, demonstration data, and honesty markers. Its SHA-256 is
`cb0363ea4f347bfe66fd9c0ba898a790069e78e9b54e431d6a61a8755db52d92`.
See [source provenance](../SOURCE_MANIFEST.md).

## Design to preserve

- Project/files/materials on the left; task, execution, editor, diff, and details
  in the center; persistent main-agent conversation/permissions on the right.
- Actual work cards across the top and Decisions and rationale at the bottom.
- In-place RU/EN switching that preserves drafts, content identities, and focus.
- Explicit proposals, user decisions, and visible execution states.
- Settings presentation and the restrained panel hierarchy shown in the demo.

## Contract boundaries

The [specification](../SYNAPSE_UI_BRIDGE_SPEC_V1.md) governs implementation.
The demo is not a working core, acceptance oracle, production dependency, or
source to paste wholesale into a component. Exclude it from product bundles.
Do not copy its embedded sample data, timers, test inspection helpers, or
single-file structure into product semantics.

Known differences requiring deliberate implementation:

- The specification makes permission-default changes affect new tasks; the demo
  can act on the current task. Implement MODE-3–6.
- Memory/learning controls and MCP administration specified by the bridge are
  absent from this prototype. Their absence does not reduce the required scope.
- Demo CSP `connect-src 'none'` is for this offline artifact. Production Tauri
  must allow its required IPC under a tested restrictive policy.
- Demo exports and state inspection identify demonstration content; they do
  not establish durable command acceptance, grants, evidence, or recovery.

Before adapting the design, follow the feature/module boundaries in
[coding rules](../CODING_RULES.md) and the mandatory memory layout in MEM-13–18.
