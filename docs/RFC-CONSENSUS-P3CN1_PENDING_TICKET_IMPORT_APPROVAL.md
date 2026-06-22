# RFC-CONSENSUS-P3CN1 Pending Ticket Import Approval Record

**Status:** APPROVED  
**Stage:** P3c-N1 pending-ticket import approval gate  
**Mutation type:** documentation approval record  
**Runtime code changed by this patch:** NO  
**Implementation authorized after this patch:** YES  
**Approval owner:** Кирилл Раков  
**Approved amendment:** `docs/RFC-CONSENSUS-P3CN1_PENDING_TICKET_IMPORT_AMENDMENT.md`  
**Source addendum PR:** `#55`  
**Source addendum merge commit:** `d3070be9754d0a5694870610b3caef8574b87715`  
**Implementation base SHA:** this approval commit, after it lands on `main`

---

## 1. Decision

The P3c-N1 pending ticket import amendment is approved for runtime implementation.

Approved implementation may add a bounded import path for restoring an existing pending consensus ticket from durable history before mailbox vote collection resolves it.

Approved source mechanism:

```text
consensus_ticket_import mailbox message
  -> distributed_consensus_ticket_imported durable event
  -> replay reconstruction of consensus_tickets[ticket_id]
```

This approval resolves only:

```text
PENDING_TICKET_SOURCE_IN_DURABLE_UNDEFINED
```

It does not close P3, does not claim production distributed consensus, and does not authorize unrelated runtime expansion.

---

## 2. Approved Runtime Contract

The later implementation may introduce `consensus_ticket_import` as an explicit mailbox import method.

The import path must:

1. consume the import message only through the existing P2 mailbox receive boundary;
2. validate the imported pending ticket projection as strict, closed data;
3. reject resolved or non-pending ticket projections;
4. recompute and verify `vote_counts`;
5. recompute and verify `votes_hash`;
6. compute `ticket_import_hash` from the canonical imported ticket projection;
7. reject conflicting duplicates;
8. record `distributed_consensus_ticket_imported` only after validation succeeds;
9. reconstruct `consensus_tickets[ticket_id]` from the durable event during replay;
10. allow `consensus_vote_response` processing only after the corresponding ticket is available.

The import path must not trust imported hash/count fields without recomputation.

---

## 3. Approved Vote Hash Rule

`votes_hash` must be recomputed from canonical vote data before the import is accepted.

Approved preimage:

```json
{
  "schema_version": "consensus.votes.v1",
  "votes": [["<participant>", "<vote>"]]
}
```

The vote list order must follow the imported ticket's canonical participant order.

Approved hash form:

```text
"sha256:" + sha256(canonical_json(votes_preimage)).hexdigest()
```

A mismatch is a fail-closed import error.

---

## 4. Approved Event Contract

The implementation may add this durable event:

```text
distributed_consensus_ticket_imported
```

Event schema version:

```text
consensus.ticket.imported.event.v1
```

The event must contain enough canonical pending ticket projection data to reconstruct the in-memory ticket during replay.

The event is the durable owner for imported ticket reconstruction.

`consensus_tickets` itself must remain runtime projection state and must not be added to `_REPLAY_STATE_KEYS`.

---

## 5. Approved Replay Contract

Replay must reconstruct imported pending tickets only from persisted event data.

Replay must not:

```text
call live import resolvers
read live mailbox state
trust current in-memory consensus_tickets
recompute the source of the ticket from external state
require consensus_tickets in _REPLAY_STATE_KEYS
mutate historical events
```

Replay must fail closed if a vote-received event appears before the corresponding ticket-import event.

---

## 6. Approved Idempotency and Conflict Rule

The implementation must treat imports deterministically:

```text
same ticket_id + same ticket_import_hash => idempotent replay-equivalent import
same ticket_id + different ticket_import_hash => conflict, fail closed
same delivery/import id + same ticket_import_hash => idempotent replay-equivalent import
same delivery/import id + different ticket_import_hash => conflict, fail closed
```

`ticket_id` remains the primary consensus-domain correlation key.

Any delivery-level id is secondary and must not replace `ticket_id` as the consensus identity.

---

## 7. Implementation Scope Authorized

The later implementation PR may modify only the P3c-N1 import/collection path and directly required evidence/status files.

Authorized implementation surfaces:

```text
synapse/interpreter.py
synapse/runtime/consensus_ticket_resolution.py
synapse/runtime/consensus_mailbox_collection.py
tests/test_consensus_mailbox_collection_p3cn.py
docs/evidence/P3C_EVIDENCE.md
docs/CAPABILITY_MATURITY_MATRIX.md
```

`ConsensusEngine` remains out of scope.

If implementation requires changing `ConsensusEngine`, the implementation PR must stop and report:

```text
CONSENSUS_ENGINE_CHANGE_REQUIRED_WITHOUT_APPROVAL
```

---

## 8. Explicit Non-Authorization

This approval does not authorize:

```text
fresh durable DistributedConsensusStmt execution
vote request delivery
network transport
daemon transport
persistent durable inbox
early delivery before active receive boundary
durable wall-clock timer service
scheduler timeout
public ticket API
parser expansion
lexer expansion
AST expansion
multi-pattern ReceiveBlock
general durable loops
general durable member access
live LLM vote production
production distributed consensus protocol behavior
overall P3 closure
adding consensus_tickets to _REPLAY_STATE_KEYS
changing artifact_schema_version
embedding ticket projection inside consensus_vote_response
changing ConsensusEngine
```

---

## 9. Required Acceptance Evidence for the Later Implementation

The later implementation must prove:

```text
valid ticket import accepted
invalid import kind rejected
invalid schema rejected
missing required field rejected
extra field rejected
resolved ticket rejected
non-pending ticket rejected
votes_hash mismatch rejected
vote_counts mismatch rejected
same ticket_id same ticket_import_hash is idempotent
same ticket_id different ticket_import_hash is conflict
import event reconstructs consensus_tickets[ticket_id] during replay
vote response before ticket import is rejected
vote response after ticket import is accepted
partial vote collection remains non-terminal
full collection calls resolution with Mapping[str, str]
message_received remains transport evidence
distributed_consensus_ticket_imported is emitted only after validation
distributed_consensus_vote_received is emitted only after validation
replay enforces import-before-vote ordering
P2 mailbox wait path remains compatible
P3c-2 ticket resolution path remains compatible
```

Evidence must be produced through the approved runtime path, not by replacing the production path with a test-only shortcut.

---

## 10. Status After This Approval

After this approval record lands on `main`:

```text
PENDING_TICKET_SOURCE_IN_DURABLE_UNDEFINED: RESOLVED
P3c-N1 runtime implementation: AUTHORIZED
P3 overall status: OPEN
Distributed consensus maturity: PARTIAL
Runtime code changed by this approval patch: NO
```
