# UI engineering rules

Version: 1.0. Status: mandatory for new and changed UI work.

These rules implement the owner's development instructions and the researched
engineering guidance. They apply together with the repository's `AGENTS.md`,
[acceptance rules](ACCEPTANCE_RULES.md), [security rules](SECURITY_RULES.md), and
[bridge specification](SYNAPSE_UI_BRIDGE_SPEC_V1.md). The acceptance separation,
mutation, completeness, and security requirements also govern supporting Python
and Rust changes. An implementation convenience does not waive a requirement.

## 1. Completion means the entire assigned contract

1. There is no "minimally completed task" category. Complete every requirement
   in the assigned task, including failures, recovery, integration, evidence,
   documentation, and applicable installation behavior.
2. A task may deliberately cover a bounded part of the specification. State its
   scope and dependencies before implementation; fulfill that scope completely.
   A completed subtask never means that its containing feature is complete.
3. Never silently reduce scope, leave a required branch as a stub, substitute
   demonstration output, or call a happy-path implementation done.
4. Record an actual blocker with the unmet requirement, consequence, owner, and
   next action. Blocked and partially implemented work remain unfinished.
5. Completion requires acceptance of behavior, mutation evidence for executable
   changes, security review, and updated traceability. Counting files, lines,
   commits, tests, or hours does not establish completion.
6. Keep scope changes explicit in the task record. A changed requirement must be
   reflected in its governing document; implementation cannot redefine it.

## 2. English and terminology

- Write new repository documentation, code comments, identifiers, commit
  messages, and development records in English.
- Product localization may contain the supported user languages. Preserve
  technical values, source records, hashes, and input drafts during localization.
- The supplied bilingual HTML prototype is retained as an unchanged reference
  artifact at the owner's request. It is not a product implementation or test
  fixture and does not change the English documentation rule.
- Use the specification's terms consistently: intent, command, invocation,
  task, attempt, approval request, grant, projection, and evidence are distinct.
- Distinguish requested, durably accepted, effective, completed, refused,
  cancelled, and unknown. Do not use an attractive label to hide uncertainty.

## 3. Establish responsibilities before creating modules

For every substantial module, identify:

1. The responsibility it owns and the reason it would change.
2. Its public inputs, outputs, and relevant invariants.
3. The state and resources it owns, their lifetime, and cleanup obligations.
4. Its allowed dependencies and which callers may use it.
5. Its failure and recovery semantics.

Record important cross-module boundaries in the relevant design/task record.
Small local components need clear code and names rather than boilerplate design
documents. Introduce a new abstraction only for a real responsibility or contract.

Split when responsibilities have different owners, lifecycles, dependencies,
failure handling, or independently changing contracts. Keep an invariant and its
enforcement together unless an explicit interface preserves it across the split.

## 4. Prevent monoliths without scattering ownership

- `App.tsx` composes the workspace. It must not accumulate every screen,
  reducer, subscription, command handler, dialog, and style.
- Tauri `lib.rs` performs application assembly and registration. Group commands
  and lifecycle responsibilities in their own modules when those boundaries
  exist. A single expanding `commands.rs` is not the intended end state.
- The canonical Python CLI delegates the machine subcommand to its transport
  handler. Do not grow `cli.py` into the implementation of all UI operations.
- Split a growing module by responsibility while the change is understood.
  Do not postpone every separation to an unspecified later cleanup.
- Do not create `helpers`, `utils`, `common`, `part2`, pass-through wrappers,
  aliases, or forwarding layers merely to make a size counter smaller.
- Do not move a monolith into a giant hook, service, reducer, context provider,
  or shared store. The same responsibility rules apply to all of them.
- A transfer has one replacement owner. Remove the superseded execution path
  and duplicated rules; do not retain competing implementations.
- A small, tightly related private helper may remain beside its caller.
  One function per file is not a goal.
- Avoid cyclic dependencies and chains of modules that must understand each
  other's internals to perform one action.
- Large coherent modules require an intelligible ownership explanation. Size
  does not justify splitting an atomic recovery or validation contract unsafely.

## 5. Size signals and their limits

The project adopts the following review signals for handwritten frontend code.
These are project choices, not universal limits claimed by the cited sources.

| Signal | Required response |
| --- | --- |
| A TS/TSX file exceeds 300 nonblank, non-comment lines | Produce a lint warning and inspect its responsibilities while reviewing the change. |
| A TS/TSX file exceeds 500 such lines | Record the decomposition considered and either perform it or explain the cohesive responsibility that warrants keeping it together. |
| A function becomes hard to explain, read, or test | Inspect its control flow and responsibilities immediately, regardless of line count. |
| Generated types, generated validators, or generated data are large | Review the generator, source contract, reproducibility, and runtime boundary; do not hand-split generated output. |
| Translation catalogs, schemas, or fixtures grow | Organize them by actual feature/schema/scenario ownership, keeping references and generation deterministic. |

These signals are lint/review aids, never acceptance tests. Do not fail product
acceptance because a file crossed an arbitrary number. Do not delete comments,
compress formatting, or move unrelated code into anonymous fragments to silence
a warning. Document justified suppressions close to their configuration.

Python follows Stage 4 NR-04 in `AGENTS.md`: no numeric file-size limit or LOC
merge gate. Rust modules likewise split on ownership and interfaces. The
frontend size signals do not create a new size mandate for either language.

## 6. Frontend organization

Organize by feature responsibilities: workspace/project, materials/editor,
tasks, chat, approvals, evidence, memory, and connection settings. Keep a
feature's presentation, state logic, and styles near one another. This is a
direction for organization, not a mandatory count of directories or files.

- Give reusable UI primitives a shared home when their contract is shared. Promote code to shared
  ownership only when its contract is shared; avoid a miscellaneous dumping ground.
- Keep the workspace layout and panel composition distinct from each panel's
  own behavior. Resizing a pane must not alter execution state.
- Components render data and handle UI interaction. Transport framing,
  command recovery, authorization, and memory decisions belong to their owners.
- Extract stateful React behavior into hooks named for concrete operations,
  such as an evidence subscription or a local draft. A hook's name and interface
  must describe its purpose. Avoid generic lifecycle wrappers hiding dependencies.
- Keep pure transformations and reducers independent of rendering when they
  have a standalone contract. Do not add hooks to ordinary pure functions.
- Place transient draft/focus/selection state with the feature that owns it.
  Store confirmed projections with their project, generation, revision, and
  freshness. Never turn the frontend store into an execution authority.
- Derive values that can be calculated from existing state. Avoid independently
  stored copies that can disagree, including locally inferred success flags.
- Keep RU/EN presentation separate from stable wire values and historical records.
- Keep tokens/theme primitives shared and component styles scoped. Avoid one
  stylesheet containing unrelated layouts, dialogs, and feature behavior.

## 7. Bridge and native responsibilities

### React/TypeScript

Own presentation, local drafts, typed command requests, and application of
validated projections. Use the designated bridge boundary. Components must not
invoke arbitrary Tauri shell/filesystem commands or call Python internals.

### Rust/Tauri

Own trusted window context, client session, intent issuance, durable pre-send
bindings, framed byte transport, bounded queues, channel delivery, secret
references, and the shell's process-lifecycle integration. These are separate
responsibilities even if a small initial implementation shares a module.

### Python machine handler

Own its protocol endpoint, schema checks, subscription routing, and dispatch to
approved composition roots. It must not become a second task scheduler, memory
court, permission engine, or independent source of execution truth.

### Existing domain owners

Retain acceptance records, effects, authoritative results, grants, memory,
budgets, evidence, and recovery. Reuse the permitted routes in the ownership
manifest. A module listed in the code map is not automatically a public API.

The UI STDIO protocol and agent STDIO protocol remain distinct contracts.
Matching frame headers do not permit reusing their semantics interchangeably.

## 8. Interfaces, imports, and contract ownership

- Expose the smallest useful interface of a feature or subsystem. Do not make
  all internal functions public to simplify integration.
- Declare dependency directions. Review changes to them as architectural changes.
- Use lint/import-boundary checks where they enforce actual dependency rules,
  such as preventing presentation from reaching privileged platform APIs.
- Prefer explicit typed results for refusal, conflict, stale state, and unknown
  outcomes. Do not flatten them into a boolean or an empty successful response.
- New bridge messages originate in JSON Schema draft 2020-12. Generate TS
  types and compiled validators; keep existing domain vocabularies derived from
  or checked against their existing owner.
- Zod must not become a second independently maintained message contract.
- Validate without stripping fields, inventing defaults, or silently coercing
  values. Preserve the specification's version and extensibility rules.
- No second copy of authorization, accounting, memory formulas, or recovery
  logic may appear in UI/Rust for apparent convenience.

## 9. Defensive implementation and explicit resource ownership

- Validate trust boundaries as code is written, not only before release.
- Bound frames, queues, reads, subscriptions, diagnostic output, subprocess
  streams, and timeouts according to the governing contract.
- Every created resource has a named owner, release condition, and failure path.
- A timeout or broken connection does not prove that an effect did not happen.
- Cleanup must preserve command identity and the evidence needed for recovery.
- Cancellation, unsubscription, window closure, process termination, and rollback
  are separate actions. Implement only the semantics actually supported.
- Never introduce a permissive fallback when a supported contract refuses.
  Unknown methods, incompatible schemas, stale proposals, missing evidence, and
  unsupported environments remain explicit states.
- Review restored state, imports, diagnostics, previews, and "read" constructors
  for hidden writes or execution. See [SECURITY_RULES.md](SECURITY_RULES.md).

## 10. Acceptance code is a separate layer

All test functions, fixtures, scenario builders, fake providers, mutation
drivers, and acceptance harnesses belong exclusively to the acceptance/test
layer. Product modules never contain or import them.

For new UI work, use acceptance-owned locations such as `acceptance/ui/` and
an independently configured acceptance package/crate where the runner requires
one. Existing `tests/` remains part of the acceptance layer; this instruction
does not authorize relocating the whole historical suite.

Do not place inline Rust `#[cfg(test)]` modules in product source, frontend
`.test`/`.spec` implementations under shipped `src`, or Python test-only paths
in `synapse/`. Configure builds and packaging to exclude acceptance artifacts.
Production dependency manifests must not make test frameworks runtime dependencies.

Production interfaces may support ordinary dependency injection when the
contract requires it. They must not expose a test mode that skips real checks,
grant special test authority, or provide alternate behavior solely for a harness.

The detailed suite-division, behavior coverage, mutation, and completion rules
are mandatory in [ACCEPTANCE_RULES.md](ACCEPTANCE_RULES.md).

## 11. Review and change discipline

1. Name the affected specification IDs and acceptance cases before coding.
2. Identify touched owners, public contracts, and security boundaries.
3. Implement one coherent change with its necessary integration and evidence.
4. Keep large mechanical moves distinct from behavior changes where doing so
   makes review reliable; every resulting change remains buildable.
5. Review every handwritten changed path, including error and recovery paths.
6. Verify behavior with meaningful acceptance checks and a mutation campaign.
7. Record the security review and any unresolved findings accurately.
8. Update the status and traceability documents with observed evidence.
9. Agents must never merge branches or pull requests, perform local `git merge`,
   enable auto-merge, or add work to a merge queue. Merging belongs exclusively
   to the repository owner. Permission to implement, commit, push a task branch,
   or open/update a PR does not grant merge authority. Direct target-branch
   updates or history rewrites must not be used to bypass this prohibition.

A small PR is a focused, understandable change. It is not defined by file count
or an arbitrary diff quota. Splitting a change must not omit its necessary usage,
validation, recovery, or acceptance evidence.

## 12. Sources and how they are used

Reviewed on 2026-10-18. These are supporting engineering references; the owner's
requirements and Synapse contracts govern project behavior.

| Source | Adopted principle |
| --- | --- |
| [React: Thinking in React](https://react.dev/learn/thinking-in-react#step-1-break-the-ui-into-a-component-hierarchy) | Component responsibilities, hierarchy, minimal state, explicit data flow. |
| [React: Reusing Logic with Custom Hooks](https://react.dev/learn/reusing-logic-with-custom-hooks#keep-your-custom-hooks-focused-on-concrete-high-level-use-cases) | Hooks express concrete use cases with comprehensible dependencies. |
| [Angular: Style Guide](https://angular.dev/style-guide#organize-your-project-by-feature-areas) | Feature grouping and one coherent concept per file; applied as a general organization principle, without adopting Angular. |
| [Tauri: Commands in a Separate Module](https://v2.tauri.app/develop/calling-rust/#defining-commands-in-a-separate-module) | Move coherent command groups out of a growing assembly file. |
| [Rust Book: Packages, Crates, and Modules](https://doc.rust-lang.org/book/ch07-00-managing-growing-projects-with-packages-crates-and-modules.html) | Group related functionality and hide private implementation behind module interfaces. |
| [Python: Modules](https://docs.python.org/3/tutorial/modules.html) | Use modules to maintain growing programs and reuse definitions without copying. |
| [ESLint: max-lines](https://eslint.org/docs/latest/rules/max-lines) | Configurable size warning; the documentation states that no objective maximum exists. Its default option is 300 when the rule is enabled. |
| [ESLint: max-lines-per-function](https://eslint.org/docs/latest/rules/max-lines-per-function) | Function-size diagnostics are available; the default option of 50 is not adopted as a universal project limit. |
| [ESLint: no-restricted-imports](https://eslint.org/docs/latest/rules/no-restricted-imports) | Enforce declared dependency restrictions where static import rules fit. |
| [Google: What to Look For in a Code Review](https://google.github.io/eng-practices/review/reviewer/looking-for.html#complexity) | Review understandability, design, concurrency, and unnecessary generalization. |
| [Google: Small CLs](https://google.github.io/eng-practices/review/developer/small-cls.html) | Focused, self-contained changes with the related verification and a working build. |

Source guidance does not justify weakening test separation, replacing behavior
with structural counts, or closing an incomplete task.
