# Synapse

Synapse is a programming language and runtime for governed, durable,
reproducible, and auditable AI behavior. A `.syn` program can describe agents,
model calls, actor interactions, policies, memory, cognitive workflows, and
verification-oriented execution without making a model itself the authority
for runtime state.

Synapse is **not an AI model**. It orchestrates LLM and host capabilities behind
explicit language, capability, governance, history, and replay boundaries.
Where a behavior cannot be proved by the implementation and its evidence, the
project documents the boundary instead of promoting the behavior to a stronger
claim.

## Why Synapse

AI behavior is often assembled from prompts and callbacks whose control flow,
state transitions, and external effects are difficult to inspect after the
fact. Synapse makes those concerns programmable:

- prompt/tool loops become explicit source and runtime transitions instead of
  hidden control flow;
- policy semantics and authority checks become named language/runtime
  boundaries instead of conventions inside prompts;
- `.syn` source defines behavior as language semantics rather than an informal
  prompt convention;
- the interpreter and Cognitive VM separate orchestration from deterministic
  computation and governed host effects;
- execution history records meaningful transitions and can be hashed into a
  tamper-evident chain;
- golden artifacts support mock replay and trace comparison;
- controlled-change and SWE-bench experiment layers can attach bounded
  evidence to applied changes without treating a worker proposal as success;
- durable state and recorded results can be reused within their contracts,
  reducing accidental loss and repeated analysis without asserting that an
  economic benefit has been measured;
- self-report is kept separate from verification authority, and trace compare
  can locate the first recorded point of divergence;
- current guarantees, boundaries, and missing pieces are kept in an audited
  [implementation status register](docs/CURRENT_IMPLEMENTATION_STATUS.md).

## Execution Spine

```text
.syn
  -> Lexer
  -> Parser
  -> AST
  -> Interpreter / CVM
  -> VMBridge / Host ABI
  -> execution_history
  -> hash chain
  -> golden artifact
  -> replay
  -> trace compare
  -> diagnostics
```

The canonical owners for this spine are `synapse/lexer.py`,
`synapse/parser.py`, `synapse/ast.py`, `synapse/interpreter.py`,
`synapse/cvm.py`, `synapse/bytecode.py`, `synapse/runtime/vm_bridge.py`,
`synapse/runtime/host_abi.py`, `synapse/hardening.py`,
`synapse/golden_replay.py`, and `synapse/debugger_core.py`. See the
[architecture overview](docs/ARCHITECTURE_OVERVIEW.md) for module ownership and
canonical-versus-exploratory boundaries.

## Language and Runtime Surface

Synapse currently contains implementation paths, with different documented
boundaries, for:

- agents, sub-agents, actor messaging, mailboxes, spawn, suspension, durable
  promises, and `await`;
- policies, guards, intents, claims, verification records, and consequences;
- Memory Palace concepts, episodic/semantic/procedural memory, imprint, recall,
  consolidation, and governed forgetting;
- `dream` and transactional `integrate` workflows;
- `soulprint`, governed `evolve`, `fracture`, `resonate`, `debate`, and
  `reflect`;
- affective state, affective events and memory, thresholds, atomic affective
  resonance, somatic markers, and affective consensus inputs;
- habits, context, energy, fatigue, recovery, storage, and runtime metrics;
- controlled change, applied verification, Gold evidence, an external
  SWE-bench oracle binding, and success-only paired measurement contracts;
- mobility envelopes and network transport prototypes;
- verified reusable knowledge as a separate future direction, not as an
  authority supplied by raw transcript carry.

These items do not all have the same replay eligibility or production
authority. The [status register](docs/CURRENT_IMPLEMENTATION_STATUS.md) is the
authority for the status, evidence, guarantee, boundary, and explicitly absent
parts of each contour.

## Current Status Summary

| Area | Status | What exists | Main boundary |
| --- | --- | --- | --- |
| Language | `IMPLEMENTED_WITH_BOUNDARIES` | Lexer, parser, typed AST, specifications, and examples | A parser/AST path alone is not subsystem completion |
| Interpreter and CognitiveVM | `IMPLEMENTED_WITH_BOUNDARIES` | Broad tree-walker plus bytecode/CVM/bridge paths | CVM and durable coverage are per construct |
| Replay and history | `IMPLEMENTED_WITH_BOUNDARIES` | Event history, hash chain, golden artifacts, and mock replay | Eligibility depends on the determinism class and recorded resources |
| Time-Travel Debugger | `IMPLEMENTED_WITH_BOUNDARIES` | Artifact trace adapters, first-divergence comparison, and isolated forks | Forks are exploratory and non-canonical |
| Actors and async execution | `IMPLEMENTED_WITH_BOUNDARIES` | Agents, messages, mailbox, spawn, suspension, promises, and await | Durable/distributed combinations are subset-bound |
| Governance | `IMPLEMENTED_WITH_BOUNDARIES` | Policies, guards, intents, claims, verification records, and consequences | Verdict authority is limited to its policy/verifier contract |
| Memory and cognitive constructs | `IMPLEMENTED_WITH_BOUNDARIES` | Memory Palace, imprint/recall, dream/integrate, identity and reflective constructs | Stored or inferred content is not automatically verified knowledge |
| Affective runtime and habits | `IMPLEMENTED_WITH_BOUNDARIES` | PAD state/events/memory, thresholds, resonance, somatic markers, context, energy, and habits | Computational state is not external or clinical truth |
| Mobility and network | `EXPERIMENTAL` | Mobility envelopes, routing, and an asyncio node prototype | No production network security/durability/SLO guarantee |
| Controlled Change | `IMPLEMENTED_WITH_BOUNDARIES` | Committed-input application, verification, evidence refs, and reports | Command execution is not OS-level sandbox proof |
| Applied verification | `IMPLEMENTED_WITH_BOUNDARIES` | Baseline/Gold adapters, GoldEvidence, verified-commit oracle, paired success-only contracts | No live/long-suite/FULL or economic authority |
| Verified reusable knowledge | `DESIGN_TARGET` | Evidence/output boundary contracts and named prerequisites | No admission, distilled carry, repository store, or integrated Gold runtime |

Later merged Alpha3g work may exist without constituting a new release
declaration or proving completion of the Alpha3g workline; release history
belongs in the [changelog](docs/CHANGELOG.md), not in this landing page.

## Version Identifiers

The repository currently uses the following package identifiers:

- Language: `2.2.0-alpha3e`
- Runtime: `0.22.0-alpha3e`
- Specification: `2.2.0-alpha3e`

Current language version: `v2.2.0-alpha3e`

<!-- Текущая версия: v2.2.0-alpha3e -->

## Quick Start

Use Python 3.10 or newer. A local Windows workspace may already have `.venv`;
otherwise create and activate a virtual environment using your normal Python
workflow. Install `pytest` only when you intend to run the test suite and it is
not already available in that environment.

Obtain the repository and enter it:

```bash
git clone https://github.com/Kirrichh/Synapse.git
cd Synapse
```

The runtime itself is repository-local and has no packaging step in the
current tree. Invoke it from the repository root with Python.

Inspect the canonical package CLI:

```bash
python -m synapse --help
```

Run deterministic and mock-backed examples:

```bash
python -m synapse run examples/math.syn
python -m synapse run examples/hello_agent.syn
```

Other representative programs include:

```bash
python -m synapse run examples/consequence_aware.syn
python -m synapse run examples/durable_actor.syn
python -m synapse run examples/replay_governance.syn
python -m synapse run examples/receive_timeout.syn
python -m synapse run examples/fifo_audit.syn
```

Start the REPL:

```bash
python -m synapse repl
```

The technical `python -m synapse.cli` form and legacy `main.py` entry point are
retained compatibility paths. New documentation uses `python -m synapse`.

## A Small Program

```synapse
agent Greeter {
    model "mock"

    fn greet(name) {
        let request = prompt "Greet the user"
        return llm(request)
    }
}

fn main() {
    let bot = Greeter()
    print(bot.greet("World"))
}
```

The mock model keeps this example local. The current live product gateway uses
Gemini and reads `SYNAPSE_LLM_PROVIDER=gemini`, `SYNAPSE_LLM_MODEL`, and
`GEMINI_API_KEY` from the environment. Do not place the key in source or a
tracked `.env` file. Multi-step `thought` blocks use 200 output tokens per step
by default; set a positive `SYNAPSE_LLM_THOUGHT_MAX_TOKENS` value when a live
task needs a larger bounded response. The
[live Gemini team trial](docs/tutorials/LIVE_GEMINI_TEAM_TRIAL.md) provides a
local example and a manually triggered GitHub Actions path suitable for a
repository secret. A live call does not become deterministic merely by using a
fixed temperature.

## Record, Replay, and Compare

Record a run into a golden artifact directory:

```bash
python -m synapse run examples/math.syn --record --output .synapse-artifacts/math
```

Replay from the artifact's embedded mock resources:

```bash
python -m synapse replay --mock .synapse-artifacts/math
```

Compare two artifact-backed traces:

```bash
python -m synapse debug compare .synapse-artifacts/math .synapse-artifacts/math
```

`replay --mock` must not call a live provider. Trace equality is evidence about
the recorded execution contract; it is not proof that arbitrary external
systems, unrecorded side effects, or semantically different programs are
equivalent. See the [debugger guide](docs/DEBUGGER_USER_GUIDE.md),
[trace comparison tutorial](docs/tutorials/TRACE_COMPARE_TUTORIAL.md), and
[determinism contract](docs/DETERMINISM_CONTRACT.md).

The compare command uses stable diagnostic exit codes:

| Exit | Meaning |
| --- | --- |
| `0` | Comparison completed and traces are equal |
| `7` | Comparison completed and found a divergence |
| `1` | Invalid input, malformed JSON, or missing path |
| `8` | Artifact integrity failure |

Additional debugger lifecycle errors use codes `2` through `6`; the debugger
guide is authoritative for the complete table. Exit `7` is a valid diagnostic
result, not a command-invocation failure.

## Controlled Change

Controlled change consumes a committed task contract and committed trusted
inputs:

```bash
python -m synapse change apply \
  --base <revision> \
  --task <task-path>
```

The command provides bounded application, verification, and reporting
semantics. It does not claim OS-level sandboxing, provider isolation, or live
SWE-bench authority by itself.

## Verification

Run the local Python suite:

```bash
python -m pytest -q
```

Useful focused checks include:

```bash
python -m pytest -q tests/test_lexer.py
python -m pytest -q tests/test_parser.py
python -m pytest -q tests/test_interpreter.py
python -m pytest -q tests/test_golden_replay.py
python -m pytest -q tests/test_controlled_change_hardening.py
```

On systems with Make and the required tools:

```bash
make test
make lint
make audit
make test-golden
```

Test counts change over time and are not maintained as a permanent README
claim. Consult the relevant CI run, verification report, or status-register
evidence for a dated result.

## Fail-Closed Boundaries

This repository does not currently claim that:

- every language construct executes in the CVM or is eligible for strict
  canonical replay;
- a parser branch, AST node, serializer, CLI flag, or runtime branch proves a
  complete subsystem guarantee;
- a valid hash proves trust, truth, semantic correctness, or safety;
- worker or LLM self-report is verification authority;
- source/history replay serializes Python frames or provides a universal
  continuation cursor;
- mobility and the `synapsed.py` transport prototype form a production network;
- verification-only Docker Compose or PostgreSQL/CDC evidence is production
  AS2 sign-off;
- controlled-change subprocess boundaries prove sandboxing;
- a proposed worker patch is a solved task;
- RawTranscriptCarry or baseline retry context is verified, admitted, or
  reusable Gold knowledge;
- a canonical C-stage provider telemetry gateway exists;
- all provider calls pass through one accounting gateway;
- token, cost, latency, throughput, ROI, or economic improvements have been
  established;
- Gold-with-carry, application/session memory append, RepositoryKnowledge,
  Cognitive VM replay of admitted knowledge, or `GOLD_FULL_VERIFIED` exists;
- the full integrated Gold execution runtime exists.

## Active Directions

The [roadmap](docs/ROADMAP.md) owns future sequencing and gates. Current
directions include:

1. preserve and extend deterministic execution without turning the CVM into a
   host-runtime monolith;
2. close durable and strict-replay gaps per construct;
3. keep controlled-change and SWE-bench evidence boundaries auditable;
4. design a canonical provider telemetry gateway before reusable token/cost
   comparison;
5. design validated, scoped, distilled reusable evidence before any
   Gold-with-carry or repository-knowledge authority;
6. retain explicit production gates for AS2 infrastructure enablement.

## Documentation Map

| Document | Authority |
| --- | --- |
| [Current Implementation Status](docs/CURRENT_IMPLEMENTATION_STATUS.md) | Current status, evidence, guarantees, boundaries, absent pieces, and replay eligibility |
| [Architecture Overview](docs/ARCHITECTURE_OVERVIEW.md) | Data flow, module ownership, execution boundaries, canonical versus exploratory paths |
| [Roadmap](docs/ROADMAP.md) | Future work, sequencing, dependencies, gates, deferred directions |
| [Changelog](docs/CHANGELOG.md) | Historical chronology and release/patch announcements |
| [Determinism Contract](docs/DETERMINISM_CONTRACT.md) | Event classes and canonical replay rules |
| [Golden Replay](docs/GOLDEN_REPLAY.md) | Artifact and mock-replay contract |
| [Debugger User Guide](docs/DEBUGGER_USER_GUIDE.md) | Record/replay/compare operation and diagnostics |
| [Controlled Change source](synapse/change/) | Controlled-change task, application, verification, and report contracts |
| [Language Specification](docs/SPEC.md) | Language syntax and semantics |

Subsystem RFCs and reports remain authoritative for their own narrow contracts
and dated evidence. If a summary conflicts with a subsystem contract, use the
status register to identify the governing document and audit base.

## History

Release and patch chronology, including the historical material previously
carried by this README, is maintained in the
[Synapse changelog](docs/CHANGELOG.md). The README intentionally stays focused
on what Synapse is, how to try it, and where to find authoritative boundaries.
