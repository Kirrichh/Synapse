# Stage 4 Stage 10 plan and worker-context model

Status: Stage 10 schema-v1 engineering decision. Merging the Stage 10 change
freezes these choices for the v1 records; changing one requires a new schema or
rendering profile rather than accepting old identities under new semantics.

## Authority boundary

Intent and operation-plan records are proposals. A plan becomes executable only
through an independent `PlanAuthorityDecision`; the accepted-plan identity is
different from both the plan proposal identity and the authority-decision
identity. The authority proof includes every actual intent/plan producer and
source actor plus the prospective executor. No caller supplies a reduced actor
set.

The executor revalidates repository revision, knowledge snapshot, full policy
hash, current admission and the Stage 1-9 before-consumption compatibility record
immediately before the first side effect. A change requires a new proposal and
decision. The later Stage 11 controller owns the compare-and-dispatch critical
section and spending of an attempt; Stage 10 does not create a second runner.

## Closed operation vocabulary

| Operation | Capability | Side effect | Verification | Rollback |
|---|---|---:|---:|---|
| `INSPECT_READ` | `repository.read` | no | no | not applicable |
| `RETRIEVE_KNOWLEDGE` | `knowledge.retrieve` | no | no | not applicable |
| `REPLAY_BEHAVIOR` | `behavior.replay` | no | yes | not applicable |
| `EDIT_CONTROLLED_CHANGE` | `repository.edit` | yes | yes | typed mechanical decision |
| `RUN_VERIFICATION_COMMAND` | `verification.run` | yes | yes | typed mechanical decision |
| `RECORD_ACTIVITY` | `activity.record` | yes | yes | governing human required |
| `PUBLISH_CANDIDATE` | `candidate.publish` | yes | yes | governing human required |

Operation capabilities are derived from the kind and cannot be proposed
independently. The graph is closed, acyclic, and ordered by deterministic Kahn
topology with operation-id ordering. Every dependency names a declared
operation; every side effect has an exact contract-condition verification ref.

## Human review and rollback

Human review is required for irreversible operations, policy-marked sensitive
capabilities, or any unresolved intent uncertainty. Accepting such a plan
requires a hash-bound approval condition and a `GOVERNING_HUMAN` independence
proof. A review-routing result is not approval. Rollback is never inferred from
worker prose; mechanical rollback happens only after a typed controller decision.

## Worker context and transport

The durable context audit and the delivered body are separate records. The audit
may name excluded refs and reasons; the delivered body cannot contain those refs
or their content. Delivered repository/knowledge bytes are checked against exact
hash and length and encoded as unpadded base64url data. Raw transcript, stdout,
stderr, full report, hidden instruction, rejected knowledge, or task/scope
override has no delivery field.

The worker channel is the canonical JSON body inside one fixed rendering
profile. No adapter may prepend, append, clip, select another channel, or log the
payload. Context identity binds the audit payload, delivery-body hash and prompt
hash. The envelope has its own hash because it contains the context identity;
this avoids a cyclic identity preimage.

The audit bytes and envelope bytes are both persisted and read back before an
invocation can be constructed. Delivery means the exact prompt was passed to a
started worker process. Parsed/referenced acknowledgements remain worker claims.
Influence is `INFLUENCED_PROVEN` only when a separate platform observer supplies
hash-bound evidence connected to the exact delivery and output artifact.

## Artifact dereference

Only hash-bound content whose bytes and byte length match a currently admitted
ref is dereferenced into the worker body. Replay observations use the sealed
typed Stage 1-9 observation record and expose its typed result fields, not a raw
transcript. Missing, oversized, changed, noncanonical, or unprovably delivered
data fails closed; there is no silent clipping or degraded context.
