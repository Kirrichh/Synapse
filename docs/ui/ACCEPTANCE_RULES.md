# Acceptance and mutation rules

Version: 1.0. Status: mandatory for all new or changed executable code in this
workstream, including supporting core changes.

## 1. Permanent separation from the product

Tests are exclusively acceptance code. "Component", "integration", "contract",
"end-to-end", "security", "property-based", and "mutation" describe acceptance
techniques, not permission to put tests into product modules.

- Product UI must not reference acceptance files as navigation destinations,
  assets, runtime data, status feeds, startup hooks, or implementations. Keep
  source citations exclusively in developer documents and acceptance tooling.
  The product is the subject of verification, never a consumer of test code.
- Product code must never import test modules, fixtures, fake providers,
  scenario builders, or acceptance harnesses.
- Product code must never contain tests, test-only algorithms, bypasses,
  synthetic authority, fixture-driven outcomes, or a duplicate implementation
  written to satisfy acceptance.
- Keep new UI acceptance files and their runner configuration outside product
  source and outside shipped application resources. Keep existing `tests/` and
  `acceptance/` as acceptance-owned layers.
- Rust tests live in separate acceptance/integration files or a test crate,
  not inline in product modules. TS/TSX tests and mocks stay outside shipped
  frontend `src`; Python acceptance code stays outside `synapse/`.
- Acceptance dependencies are development/test dependencies. Packaging must
  exclude acceptance source, fixtures, mutation output, and fake integrations.
- Dependency injection can exercise real production contracts; it must not
  change those contracts or skip their enforcement.
- A read-only observation of actual product state may assist acceptance. It
  must not maintain a second state machine or special outcome for tests.

## 2. What every meaningful check establishes

Each check must name the behavior/invariant it protects and the defect that
would make it fail. Drive an actual production entry or contract and observe
the relevant result, effect, refusal, durable record, or recovery outcome.

Assertions must distinguish correct behavior from a plausible incorrect
implementation. Assert more than "did not throw", truthiness, a success label,
or the output of a mock when the contract includes actual effects.

Coverage must address the written code and its reachable paths. An assertion
against a separately reimplemented algorithm does not establish product behavior.
Mocks may bound an external dependency; they cannot replace the very owner or
contract the check claims to verify.

Legitimate examples include:

- A second independent execution cannot consume an already bound once-grant.
- A replay of command B after C was accepted returns B's historical result
  while C remains effective.
- An outdated subscription cannot change the current projection.
- A read or preview leaves live owner records and external systems unchanged.
- A frame length is rejected before allocating the declared payload.
- A cancelled invocation is distinguished from a confirmed stopped task.
- A material save with an obsolete base returns a conflict and preserves the
  newer file.

## 3. Prohibited test content

Do not create acceptance tests whose claim of correctness is based only on:

- a filename, directory name, internal symbol name, or chosen spelling;
- a branch name, commit ID, commit count, or historical commit range;
- a file count, line count, function count, test count, or arbitrary size quota;
- the presence of a keyword, comment, method declaration, or documentation phrase;
- a prescribed folder layout without a behavioral dependency requirement;
- an implementation-specific call sequence when the public contract does not
  require that sequence;
- assertions that merely reproduce the implementation's calculation and would
  share its defect;
- cosmetic mocks or fixtures that always return the desired result.

A protocol identifier, schema field, error code, content hash, or command ID
may be checked when its value is part of an actual external behavior/integrity
contract. For example, rejecting a changed command binding is meaningful;
requiring that the implementation's filename contain a particular word is not.

Linting, formatting, document link checking, and source-reference checks are
maintenance checks. Keep them distinct from product acceptance. They cannot
replace tests of execution and must not inflate behavioral evidence counts.

## 4. Suite boundaries and parallel execution

1. Every heavy scenario gets a dedicated acceptance file so it can be scheduled
   independently. Document why it is heavy and its resource needs.
2. Split collections of small checks into multiple files by behavior or contract
   before one file becomes hard to understand or schedule.
3. Do not pack unrelated tests into one enormous suite, and do not make a new
   file for every trivial assertion without a useful scenario boundary.
4. Group shared setup only within the acceptance layer and only when it preserves
   a clear scenario. Avoid giant fixture libraries and hidden mutable state.
5. Every parallel worker uses isolated repositories, worktrees, databases,
   owner stores, output directories, and assigned ports as appropriate.
6. Never depend on execution order, another file's leftovers, real credentials,
   or the developer's personal environment for ordinary local acceptance.
7. Never install or change dependencies while a run is in progress; environment
   identity is evidence for contracts that inspect it.
8. Declare concurrency/resource limits. Parallel scheduling must not invalidate
   the process timings or exclusivity that a test intends to observe.
9. Use explicit barriers and fault points for races and recovery when feasible.
   A race test that silently loses the intended interleaving is weak evidence.
10. If a scenario must run serially, name the real shared resource and limitation.
    Do not mark the whole suite serial to conceal accidental coupling.

The goal is independent, meaningful, schedulable scenarios. No rule is enforced
by an acceptance assertion about how many files or tests exist.

## 5. Coverage expectations

For each affected requirement, cover the relevant combinations of:

- ordinary success and allowed no-op behavior;
- invalid, unknown, unsupported, and out-of-scope requests;
- boundaries, missing data, malformed input, and numerical limits;
- stale state and competing operations;
- duplicate delivery and changed bindings;
- cancellation, timeouts, process loss, and partial effects;
- recovery after every meaningful durable write/effect boundary;
- inaccessible evidence and unavailable dependencies;
- project/window/session identity isolation;
- absence of forbidden writes, disclosures, or authority escalation.

Maintain a requirement-to-case mapping. Line/branch coverage reports can reveal
unvisited executable paths, but no percentage proves semantic correctness.
Explain uncovered meaningful branches and close the gap before completion.
Do not chase percentages with assertions that cannot detect a real defect.

## 6. Mutation verification is mandatory

Every new or modified executable behavior must undergo mutation verification.
Ordinary passing tests alone are insufficient for completion.

### Scope

- Mutate the implementation under test, not only fixture expectations.
- Include the changed behavior and the affected contract boundaries.
- Cover Python, TypeScript/JavaScript, and Rust changes with suitable tools or
  an explicit, reproducible seeded-mutation campaign when tooling is unsuitable.
- Mutations must represent credible defects: invert a condition, remove a
  check, alter a boundary, omit a durable write, lose a binding, consume a grant
  twice, advance a cursor over a gap, or leak an unauthorized field.
- Do not claim mutation coverage by changing comments, names, branches, or
  irrelevant dead code.

### Required workflow

1. Fix and record the implementation/environment being exercised.
2. Run relevant acceptance on the unmutated baseline; failures invalidate the
   campaign until diagnosed.
3. Create isolated mutants and run the behavior-relevant acceptance for each.
4. Inspect every survivor and every invalid/timeout result.
5. Add or strengthen behavioral cases for meaningful survivors, correct the
   implementation when needed, and rerun the affected baseline and mutants.
6. Record the campaign results on the final implementation.

### Result meanings

| Result | Meaning and required action |
| --- | --- |
| Killed | Acceptance reliably detects the injected behavioral defect. Retain the detecting case and diagnostic. |
| Survived | A meaningful defect escaped. Improve acceptance or resolve the contract; do not close the task while it remains unexplained. |
| Equivalent | No observable contract difference is possible in the supported domain. Provide a specific reviewed rationale; do not classify by guess. |
| Invalid/unbuildable | The mutant never produced an executable scenario. Record separately; it is not a behavioral kill. |
| Timeout | Investigate the cause. Count as a detected defect only when the bounded completion contract and evidence establish it; otherwise mark inconclusive. |
| Infrastructure failure | No conclusion about the mutant. Repair the run and repeat. |
| Not applicable | No executable behavior changed, for example translation-only documentation. Explain the scope; never use this for difficult-to-test product code. |

Do not inflate a mutation score with invalid mutants or unrelated failures.
No meaningful unexplained survivor may be hidden behind a passing percentage.
Equivalent mutants, exclusions, and tooling limits require explicit evidence.

### Evidence record

Never copy real user data into acceptance inputs or repository evidence. Use
invented values, including for secret-disclosure and path-isolation scenarios.
Keep only reviewed structured results, relative source/case references, and
necessary build identities. Do not retain raw stdout/stderr, environment dumps,
or screenshots of private user state. The
[permanent data prohibition](CODING_RULES.md#permanent-prohibition-on-storing-user-data)
also applies to temporary, generated, ignored, and untracked repository files.

Record: scope/requirement IDs, implementation reference, environment, tool and
configuration, baseline result, mutant descriptions, result classification,
detecting checks, survivor resolutions, and final reruns. Commit references may
identify evidence; acceptance must never pass merely because a commit has a
particular name or hash.

## 7. Security is part of ongoing acceptance

Use [SECURITY_RULES.md](SECURITY_RULES.md) during design, implementation, and
review. Exercise the real boundary with adversarial input and observe both the
refusal and absence of forbidden effects. Do not defer all bypass checks to a
release audit.

Security findings remain open until fixed and behaviorally verified. A disabled
test, permissive fallback, or weakened assertion does not resolve a finding.
Mutation cases should attempt to remove or weaken the controls being relied on.

## 8. Three levels of UI evidence

Follow specification ACC-1 through ACC-5:

1. Component/contract acceptance: actual reducers, localization, bootstrap,
   schemas, and feature behavior with bounded dependencies.
2. Desktop acceptance: a real Tauri window, actual Rust/Python transport,
   Channels, process failures, and recovery.
3. Installed artifact acceptance: the packaged sidecar, canonical entry,
   production CSP, platform process containment, and the tested build's identity.

Checking an internal snapshot is supplemental. UI acceptance must also observe
what the real window shows and relate it to the owner's actual records. A browser
demo cannot establish the behavior of an installed desktop application.

All 44 supplied acceptance cases are tracked explicitly. A smaller assigned
task must still fulfill every case and boundary applicable to that task.

## 9. Evidence honesty and completion gate

The completion record must contain:

- assigned requirements and their final status;
- observed acceptance commands/results and relevant environment;
- mutation results and the disposition of every survivor;
- security checks, findings, and resolution evidence;
- integration/platform limitations and remaining dependencies;
- links to implementation and retained evidence.

Distinguish inspected, executed-pass, executed-fail, blocked, and not-run.
Historical results must identify their scope and must not be represented as a
fresh run. A targeted result is not a full-suite result. A skipped prerequisite
does not establish production readiness.

Documentation-only changes require document validation and an explicit mutation
N/A statement. Do not invent product tests or a mutation score for translated prose.

The task is complete only when its entire contract is fulfilled. Keep unfinished
work visible in [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) and
[REQUIREMENT_MATRIX.md](REQUIREMENT_MATRIX.md).
