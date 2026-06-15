# Synapse Runtime Capability Maturity Matrix

Status source: Synapse Runtime Capability Integrity Program.

Verified against `main` at merge commit `49c771f4edff140a96e505f4a96a31ccf61a87ef`.

The matrix distinguishes implemented internal mechanics from production-reachable, user-observable capabilities. A capability is marked production only when it has a canonical execution path, observable result, durable/replay contract, failure semantics, and acceptance evidence.

| Capability | Maturity | Canonical status | Evidence / boundary |
|---|---|---|---|
| Affective resonance profile provenance | **Production** | `MERGED` | `profile_source` is observable in the returned bridge and stored once at top level in `affective_resonance_applied`; LIVE supports `explicit`, `history`, `neutral_fallback`; legacy replay derives `legacy_unknown` without mutating history. Implemented by PR #10. |
| CVM execution and checkpoint/resume | **Deep production semantics** | Existing | Execution state, ABI validation, checkpoint/resume and history-boundary validation exist. Changes require separate conformance evidence. |
| Deterministic replay and tamper-evident history | **Deep production semantics** | Existing | Typed replay matching and full-payload event-chain hashing are active runtime contracts. |
| Governance refusal and replayed verdict | **Deep production semantics** | Existing; evidence track continues | Fail-closed refusal and durable verdict exist. User-facing evidence remains part of S2. |
| Canonical async durable execution through CLI | **Partial** | P2 requires RFC | Internal suspension/resume semantics exist, but the full supported CLI lifecycle for pending state, external resolution and restart/resume is not yet defined. |
| Distributed consensus | **Semantic facade** | P3 requires RFC | Current behavior does not yet represent content-sensitive participant votes. It must not be described as completed distributed consensus. |
| Habit capability | **Production mechanics; evidence incomplete** | P4 diagnostic required | Evaluation, suppression, fatigue, recovery and activation orchestration exist, but the canonical user scenario must distinguish activation from non-activation/suppression. |
| CVM / tree-walker coverage | **Unproven conformance** | P5 matrix required | Routing declarations do not by themselves prove compiler, opcode, VM-handler, state, error, history and replay parity. |
| AS2 family | **Internal / test-oriented infrastructure** | Not connected to production execution | AS2 contains substantial mechanics but is not production-reachable through the canonical interpreter/CLI/CVM path. P6 requires an architectural decision. |
| Cross-node routing | **Runtime half of external protocol** | Outbound intent only | Runtime resolves routes and records outbound packets/intents. Network delivery belongs to an external transport daemon. |

## Status rules

- **Production** means the capability is reachable through its supported runtime path and has observable, replay-aware behavior.
- **Partial** means meaningful runtime mechanics exist, but the canonical product lifecycle is incomplete.
- **Semantic facade** means the public name promises more than the current implementation provides.
- **Internal / test-oriented infrastructure** means implementation exists but is not production-reachable.
- **Unproven conformance** means declarative routing or component presence has not yet been demonstrated end to end.

This matrix is a signaling document. It does not change parser, AST, interpreter, runtime semantics, durable schemas, CLI behavior or feature flags.
