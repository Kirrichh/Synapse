# RFC-CONSENSUS-P3 — Content-Sensitive Distributed Consensus Contract

**Title:** P3a — Content-Sensitive Distributed Consensus Contract  
**Requirement ID:** `REQ-CONSENSUS-01`  
**Patch unit:** `P3-RFC-CONSENSUS-01`  
**Document status:** `DRAFT — TEAM REVIEW AND PRODUCT OWNER APPROVAL REQUIRED`  
**Implementation status:** `NOT IMPLEMENTED`  
**Production implementation:** `UNAUTHORIZED UNTIL THIS RFC IS APPROVED AND MERGED`  
**Primary implementation stage:** `P3a — Semantic Consensus Core`  
**Deferred stages:** `P3b Runtime / Actor Vote Integration`, `P3c Durable / Replay Closure`, `P3d LLM-Assisted Voting`  
**Current capability status before evidence closure:** `Semantic facade`  
**Grammar expansion in P3a:** `NOT ALLOWED`  
**Durable allowlist expansion in P3a:** `NOT ALLOWED`  
**Live LLM in P3a:** `NOT ALLOWED`  
**Network / daemon vote integration in P3a:** `NOT ALLOWED`

---

# 1. RFC rule

This document defines the normative contract for P3a.

Production implementation of P3a is unauthorized until this RFC is reviewed, approved, and merged.

No production code may be written, modified, or merged under the label `P3a implementation` before this RFC reaches `APPROVED` status on `main`.

If implementation starts before this RFC is approved, the process must stop with:

```text
BLOCKED — P3A_PREMATURE_IMPLEMENTATION
```

This RFC is documentation-only. It does not authorize implementation changes.

---

# 2. Executive Summary

This RFC defines the final contract for P3: Content-Sensitive Distributed Consensus.

The current `distributed consensus` runtime behavior is a semantic facade. It exposes syntax and runtime output that appear to represent a consensus decision, but the decision is not based on meaningful participant votes. The current implementation constructs automatic `"yes"` votes, injects the current actor as an additional `"yes"` voter, does not represent `"no"`, `"abstain"`, or `"missing"` votes as first-class states, does not validate quorum boundaries, does not enforce consensus strategy semantics, and uses nondeterministic deferred ticket generation.

P3a replaces this facade with deterministic content-sensitive consensus semantics.

For this RFC, content-sensitive consensus means:

```text
The consensus outcome depends on explicit participant votes, validated participant identity, validated quorum, selected consensus strategy, and deterministic outcome rules.

The outcome MUST NOT depend on hardcoded default votes, implicit current actor voting, participant count alone, wall-clock time, live LLM output, network side effects, daemon packet state, entropy-based identifiers, or hidden interpreter fallback behavior.
```

P3a is strictly scoped. It does not introduce new grammar, new AST nodes, parser extensions, durable replay support, actor/network vote collection, live LLM voting, governance policy execution, or production distributed consensus protocol behavior.

P3a MUST be implemented as an in-process synchronous reducer through a dedicated consensus engine boundary. The interpreter MUST become a narrow adapter for `DistributedConsensusStmt`; it MUST NOT own consensus mathematics or strategy semantics.

P3 is divided into four stages:

1. **P3a — Semantic Consensus Core**  
   Deterministic in-process reducer. Explicit votes only. No waiting. No suspension. No network. No durable support. No live LLM. No grammar expansion.

2. **P3b — Runtime / Actor Vote Integration**  
   Future integration of controlled runtime or actor vote sources. This stage may define actor vote delivery, mailbox integration, daemon bridging, or runtime VoteSource registry after P3a is approved.

3. **P3c — Durable / Replay Closure**  
   Future durable/replay contract for consensus. This stage must define event consumption, vote persistence, replay matching, fail-closed behavior, and durable artifact/hash coverage.

4. **P3d — LLM-Assisted Voting**  
   Future LLM vote production stage under separate RFC. Live LLM MUST NOT be part of P3a.

This RFC intentionally does not claim that P3a implements Paxos, Raft, Tendermint, PBFT, ZAB, or a production distributed consensus protocol. P3a borrows only the engineering discipline required for this runtime layer: explicit participants, explicit votes, quorum validation, deterministic proposal identity, canonical event semantics, fail-closed validation, and honest capability signaling.

---

# 3. Normative Language

The following terms are normative:

- **MUST** means required.
- **MUST NOT** means forbidden.
- **SHOULD** means strongly expected unless an approved documented reason exists.
- **MAY** means permitted only inside the stated boundary.
- **P3a** means the deterministic semantic consensus core.
- **P3b** means future runtime or actor vote integration.
- **P3c** means future durable/replay closure.
- **P3d** means future LLM-assisted voting.
- **Consensus engine** means the deterministic semantic component that owns participant validation, vote validation, quorum calculation, strategy evaluation, outcome selection, proposal identity construction, and canonical decision construction.
- **Interpreter adapter** means the runtime bridge that evaluates AST expressions, resolves environment values, invokes the consensus engine, appends approved runtime events, and binds the result.
- **Coordinator** means the actor or runtime context that initiates evaluation. The coordinator is not automatically a voter.
- **Proposal** means the canonical decision object under consideration.
- **VoteSource** means an approved deterministic input mechanism that supplies participant votes to the P3a reducer.
- **Semantic field** means a field that participates in deterministic decision identity, canonical event identity, or replay-relevant comparison.
- **Advisory field** means a field that is observable metadata but does not participate in semantic identity unless promoted by a later approved RFC.
- **Structural validation failure** means an invalid consensus request shape or invalid evaluated input that prevents consensus evaluation from producing a valid operational result.
- **Operational consensus outcome** means a valid consensus result produced from a valid request, currently `committed`, `rejected`, or `deferred` in P3a.

---

# 4. Current State and Facade Findings

## 4.1 Existing capability shape

The language already contains `DistributedConsensusStmt` with the following conceptual fields:

```text
participants
topic
quorum
timeout
policy_ref
binding
```

The current parser accepts the existing distributed consensus form:

```synapse
distributed consensus with [participants] on topic {
    quorum <expr>
    timeout <expr>
    policy <policy_ref>
    bind <binding>
}
```

This RFC does not introduce a new P3a grammar form.

## 4.2 Swarm syntax is separate

Forms such as:

```synapse
consensus weighted
consensus majority
consensus unanimous
```

belong to `swarm fracture` behavior, not `DistributedConsensusStmt`.

P3a MUST NOT use `swarm fracture` grammar as the distributed consensus grammar basis.

## 4.3 Confirmed facade defects

The following defects are P3 blockers:

1. **Hardcoded `"yes"` votes**  
   Current runtime constructs affirmative votes without participant decision input.

2. **Implicit current actor vote**  
   Current actor is injected as `"yes"` even when not listed in participants.

3. **Empty participant commit**  
   Empty participants can be syntactically accepted and can still commit through hidden actor injection.

4. **Invalid quorum behavior**  
   Values such as `0`, negative values, and values greater than participant count are not rejected correctly.

5. **No explicit vote model**  
   `"yes"`, `"no"`, `"abstain"`, and `"missing"` are not represented as first-class consensus vote states.

6. **Decorative policy reference**  
   `policy_ref` is copied into result data but does not define a strategy or enforce governance.

7. **Nondeterministic deferred ticket**  
   Deferred paths use entropy-based ticket generation.

8. **Durable unsupported boundary**  
   `DistributedConsensusStmt` is outside current durable-supported execution scope.

9. **Disconnected network vote path**  
   Daemon vote packet acceptance is not connected to interpreter consensus decision semantics.

10. **No LLM vote production in current consensus path**  
    The current consensus path does not call live LLM providers, and P3a MUST preserve that separation.

11. **Interpreter monolith risk**  
    Current behavior is concentrated inside interpreter evaluation. P3a MUST prevent further interpreter expansion by isolating semantic decision logic in a consensus engine.

---

# 5. P3 Scope

## 5.1 In scope for P3a

P3a includes:

1. Deterministic semantic consensus engine.
2. Interpreter adapter boundary.
3. Participant identity resolution contract.
4. Participant normalization.
5. Duplicate participant rejection.
6. Explicit vote model.
7. Deterministic VoteSource boundary.
8. Quorum derivation and validation.
9. Timeout validation as metadata only.
10. Strategy selection through existing `policy` clause.
11. Safe handling of evaluated `policy_ref` values, including governance policy object references.
12. Strategy semantics for approved strategy names.
13. Early termination and early rejection.
14. Canonical outcome model.
15. Canonical result shape.
16. Canonical event payload shape.
17. Proposal identity.
18. Statement identity extraction.
19. Advisory versus semantic field classification.
20. Existing canonical JSON / history integrity compatibility.
21. No implicit actor voting.
22. No live LLM.
23. No network or daemon dependency.
24. No durable allowlist expansion.
25. No stateful ticket lifecycle.
26. Tests proving facade removal.
27. Documentation honesty preserving Semantic facade status until evidence closure.

## 5.2 Out of scope for P3a

P3a excludes:

1. Parser extension.
2. New AST nodes.
3. New grammar clauses.
4. Grammar migration from `policy` to `strategy`.
5. Live LLM vote production.
6. Actor mailbox waiting.
7. Daemon packet voting.
8. Network delivery.
9. Suspension-based vote collection.
10. Blocking wait for future votes.
11. Durable vote persistence.
12. Durable allowlist changes.
13. Replay event consumption as production behavior.
14. Stateful consensus ticket lifecycle.
15. Governance policy guard execution.
16. Production distributed consensus protocol claims.
17. Byzantine behavior modeling.
18. Signature verification.
19. Leader election.
20. View-change protocol.
21. Runtime VoteSource registry.
22. User-facing vote syntax expansion.
23. Silent migration of legacy facade events.
24. Status upgrade to production.
25. Runtime-instance identity for repeated executions in loops or recursion.

---

# 6. Phase Split

## 6.1 P3a — Semantic Consensus Core

P3a is an in-process synchronous reducer.

P3a answers:

```text
Given this proposal, these normalized participants, this selected strategy, this validated quorum, this validated timeout metadata, and these explicit deterministic votes, what is the deterministic outcome?
```

P3a does not answer:

```text
How are votes collected over time from actors, networks, durable workflows, LLM providers, or distributed nodes?
```

P3a MUST NOT wait. P3a MUST NOT suspend. P3a MUST NOT poll. P3a MUST NOT call live providers. P3a MUST NOT mutate durable contracts.

## 6.2 P3b — Runtime / Actor Vote Integration

P3b is deferred.

P3b may later define:

- actor-backed vote sources;
- mailbox-backed vote sources;
- daemon packet integration;
- runtime VoteSource registry;
- pending vote lifecycle;
- repeated vote handling;
- vote transition rules;
- stateful ticket lifecycle;
- runtime correlation identifiers.

P3b MUST preserve P3a semantic rules.

## 6.3 P3c — Durable / Replay Closure

P3c is deferred.

P3c must later define:

- durable-supported classification;
- canonical event consumption;
- durable vote persistence;
- replay cursor behavior;
- fail-closed replay matching;
- legacy event handling;
- durable ticket lifecycle;
- hash coverage;
- artifact evidence.

P3c MUST NOT be inferred from P3a.

## 6.4 P3d — LLM-Assisted Voting

P3d is deferred.

P3d must later define:

- LLM VoteSource contract;
- prompt template versioning;
- model output schema;
- refusal handling;
- invalid output handling;
- provider timeout handling;
- cost and latency controls;
- replay rule: consume recorded vote and never call live model during replay.

P3d MUST NOT be bundled into P3a.

---

# 7. Grammar Contract

## 7.1 Existing grammar retained in P3a

P3a MUST use the existing distributed consensus grammar:

```synapse
distributed consensus with [participants] on topic {
    quorum <expr>
    timeout <expr>
    policy <policy_ref>
    bind <binding>
}
```

## 7.2 No parser or AST expansion in P3a

P3a MUST NOT introduce:

- new grammar clauses;
- new AST nodes;
- parser extensions;
- `strategy <strategy_name>` syntax;
- user-facing vote syntax;
- new statement forms.

## 7.3 `policy` clause interpretation in P3a

In P3a, the existing `policy` clause is interpreted only as a consensus strategy selector for approved strategy names.

Approved strategy names are:

```text
MajorityVote
UnanimousVote
NoVetoVote
```

P3a MUST NOT execute governance policy guards through this clause.

P3a MUST NOT call `policy_allows()` through this clause.

P3a MUST NOT trigger governance side effects through this clause.

If the evaluated `policy_ref` resolves to a Governance Policy object rather than a plain string, the interpreter adapter MUST safely extract only its stable identifier or name.

If the extracted identifier or name does not exactly match an approved P3a strategy name, validation MUST fail.

If the evaluated `policy_ref` is not a string and is not a recognized Governance Policy object with a stable identifier or name, validation MUST fail.

Grammar cleanup such as adding `strategy <strategy_name>` is deferred to a future patch or RFC.

## 7.4 Participants

Participants are provided in the existing bracketed participant list.

Empty participants MUST fail validation.

## 7.5 Topic

Topic is evaluated by the interpreter adapter and passed to the consensus engine as canonical proposal input.

## 7.6 Quorum

Quorum expression is evaluated by the interpreter adapter.

After evaluation, quorum MUST be a strict integer value.

The adapter MUST NOT coerce:

- strings;
- floats;
- booleans;
- `None`;
- lists;
- dictionaries;
- objects;
- any non-integer value.

Boolean values MUST NOT be accepted as integer quorum values even if host-language implementation treats booleans as integer-compatible.

For `MajorityVote`, if quorum is omitted, default quorum is:

```text
participant_count // 2 + 1
```

Explicit quorum MUST satisfy:

```text
1 <= quorum <= participant_count
```

Invalid quorum values MUST fail validation before consensus decision binding or event append.

## 7.7 Timeout

Timeout expression is evaluated by the interpreter adapter.

After evaluation, timeout MUST be a strict integer value.

The adapter MUST NOT coerce strings, floats, booleans, `None`, or other non-integer values into timeout.

P3a treats timeout as validated metadata only.

P3a MUST NOT:

- start a timer;
- sleep;
- poll;
- wait;
- suspend;
- call a scheduler;
- measure wall-clock time;
- derive a timeout outcome from elapsed time.

`timeout < 0` MUST fail validation.

`timeout = 0` means immediate synchronous evaluation metadata.

`timeout > 0` is retained as proposal metadata for future stages but has no waiting behavior in P3a.

The `timeout` outcome is reserved for P3b/P3c.

## 7.8 Binding

Binding names where the valid consensus result is stored.

Structural validation failures MUST NOT bind a result.

---

# 8. Participant Identity Contract

## 8.1 Accepted participant identity sources

P3a accepts participant identities resolved from:

1. `AgentRuntime.name`;
2. `DurableActorRef.actor_name`;
3. string literal identifier;
4. variable-resolved value that canonicalizes to an approved identifier.

## 8.2 Canonical participant identifier

Canonical participant identifiers MUST be:

- string-compatible;
- stable for the evaluated participant;
- non-empty;
- not whitespace-only;
- canonical JSON serializable;
- comparable for deterministic sorting;
- suitable for proposal hashing.

## 8.3 Resolution failure

If a participant expression cannot be resolved into an approved canonical identifier, validation MUST fail.

P3a MUST NOT fall back to ambiguous stringification that hides invalid participant identity.

## 8.4 Normalization pipeline

P3a participant normalization MUST follow this order:

```text
1. Interpreter adapter evaluates participant expressions.
2. Consensus engine resolves each value to canonical participant identifier.
3. Null, empty, whitespace-only, or unsupported identities fail validation.
4. Duplicates are detected after normalization.
5. Any duplicate participant fails validation.
6. Normalized participants are sorted for proposal_id and event payload canonicalization.
7. Original AST order may be preserved only for display or result presentation and is advisory.
```

Original participant order MUST NOT alter semantic outcome.

## 8.5 Duplicate participants

Duplicate participants MUST fail validation.

P3a MUST NOT silently deduplicate participant lists.

## 8.6 Current actor and coordinator

The current actor may be provided as coordinator metadata.

The current actor MUST NOT become a voter unless:

1. the actor is explicitly present in the normalized participant list;
2. the actor has an explicit vote from the approved VoteSource;
3. the vote passes the same validation rules as every other participant vote.

## 8.7 Top-level execution

If `distributed consensus` is evaluated outside an agent context, coordinator MUST be `null` or a deterministic advisory metadata value such as:

```text
"__top_level__"
```

The consensus engine MUST NOT crash, invent a voter, inject a default actor, or create quorum from top-level execution metadata.

---

# 9. Vote Model

## 9.1 Allowed vote states

P3a supports exactly these vote states:

```text
yes
no
abstain
missing
```

Unknown vote states MUST fail validation.

## 9.2 `yes`

`yes` means the participant affirmatively supports the proposal under the selected strategy.

## 9.3 `no`

`no` means the participant rejects the proposal under the selected strategy.

## 9.4 `abstain`

`abstain` means the participant explicitly declines to support or reject the proposal.

`abstain` is not the same as `missing`.

## 9.5 `missing`

`missing` means no valid vote is available for the participant at the time of P3a evaluation.

P3a MUST NOT call LLM, wait for actors, consult daemon packets, or synthesize votes to resolve `missing`.

## 9.6 Explicit missing representation

VoteSource normalization MUST produce one normalized vote state for every normalized participant.

If a participant has no supplied vote, that participant MUST be represented explicitly as `missing`.

## 9.7 Vote payload source values

P3a approved vote source labels are:

```text
explicit_map
test_controlled
recorded_test
missing
```

`recorded_test` is allowed only for test scope and MUST NOT imply P3c durable replay closure.

## 9.8 Conflicting vote entries inside one P3a evaluation

Within one P3a evaluation, each participant MUST have exactly one normalized vote state.

If the supplied VoteSource payload contains conflicting entries for the same participant, validation MUST fail.

Lifecycle transitions such as `missing -> yes`, repeated vote handling, stale vote handling, and vote override behavior are deferred to P3b/P3c.

---

# 10. VoteSource Model

## 10.1 P3a VoteSource boundary

P3a VoteSource is deterministic and directly supplied.

P3a MAY support:

1. explicit vote map;
2. test-controlled vote source;
3. static in-memory vote registry supplied before evaluation;
4. recorded test-only data used without P3c claims.

P3a MUST NOT implement runtime VoteSource registry.

## 10.2 Forbidden P3a VoteSource behavior

P3a VoteSource MUST NOT:

- perform I/O;
- perform network calls;
- call live LLM;
- access daemon packet buffers;
- wait on actor mailbox;
- poll;
- sleep;
- use wall-clock time;
- generate random values;
- synthesize fallback votes;
- mutate durable state.

## 10.3 Conceptual VoteSource interface

The following shape is conceptual and non-binding for class names:

```python
class VoteSource:
    def get_votes(self, proposal_id: str, participants: list[str]) -> dict[str, str]:
        """
        Returns one vote state per participant:
        {
            "A": "yes",
            "B": "no",
            "C": "abstain",
            "D": "missing",
        }
        """
```

The P3a implementation may use different class names, but the contract is mandatory: vote resolution is synchronous, deterministic, and side-effect-free.

## 10.4 Future VoteSource registry

VoteSource registry, runtime discovery, actor-backed providers, mailbox-backed providers, network-backed providers, and LLM-backed providers are deferred to future stages.

---

# 11. Consensus Model

## 11.1 Failure model

P3a uses a CFT-style trust assumption for semantic modeling.

This means:

- participants may be unavailable or missing;
- participants are not modeled as Byzantine adversaries;
- P3a does not verify signatures;
- P3a does not implement BFT validation;
- P3a does not implement leader election;
- P3a does not implement view-change;
- P3a does not claim distributed fault-tolerant protocol behavior.

## 11.2 Execution model

P3a execution model is:

```text
in-process synchronous reducer, no waiting
```

P3a MUST:

1. evaluate inputs synchronously;
2. validate inputs;
3. normalize participants;
4. normalize votes;
5. resolve strategy;
6. calculate outcome;
7. return immediately.

P3a MUST NOT:

1. wait;
2. suspend;
3. poll;
4. start timers;
5. call network;
6. call live LLM;
7. persist votes durably.

## 11.3 Coordinator model

P3a uses coordinator metadata, not leader election.

The coordinator:

- initiates evaluation;
- may be recorded as advisory or semantic metadata depending on final proposal identity design;
- does not automatically vote;
- does not expand participant set;
- does not override strategy;
- does not create quorum.

## 11.4 Proposal identity

P3a MUST assign a deterministic `proposal_id`.

`proposal_id` identifies semantic proposal content, not every runtime execution instance.

Recommended shape:

```text
proposal_id = sha256(canonical_json({
  "schema_version": "consensus.proposal.v1",
  "topic": canonical_topic,
  "participants": normalized_participants_sorted,
  "quorum": quorum,
  "timeout": timeout,
  "policy": policy_ref,
  "strategy": strategy_name,
  "coordinator": coordinator,
  "statement_identity": statement_identity
}))
```

## 11.5 Proposal identity and repeated execution

P3a MUST NOT rely on `proposal_id` alone to distinguish repeated executions inside loops, recursion, or repeated calls.

Runtime-instance identity for repeated executions is deferred to a future `consensus_instance_id` or `decision_id` design in P3b/P3c.

P3a MUST NOT add execution cursor, history length, or runtime-specific mutable counters into `proposal_id`.

Reason: `proposal_id` is a semantic proposal fingerprint. Runtime execution identity is a separate concern.

## 11.6 Statement identity

`statement_identity` is a semantic field and MUST participate in `proposal_id`.

The interpreter adapter MUST extract stable statement identity from the `DistributedConsensusStmt` AST node where available and pass it to the consensus engine.

Approved statement identity sources include:

- source line;
- source column;
- source span;
- AST path;
- statement ordinal;
- another deterministic source-level identity.

`statement_identity` identifies the source-level statement. It does not uniquely identify repeated runtime executions inside loops or recursion.

Runtime-instance identity is deferred to `P3-TD-003 — Consensus Instance Identity`.

## 11.7 Canonical hashing and serialization

Hashing MUST use canonical JSON serialization and SHA-256.

The canonical proposal payload and canonical consensus event payload MUST be serialized using the project-approved `canonical_json` mechanism or the existing equivalent hardening/history integrity utility.

The consensus engine MUST NOT introduce a parallel or incompatible JSON serializer.

Canonical consensus event payloads MUST remain compatible with existing history integrity behavior, including `hash_event_chain`.

The implementation SHOULD use the project’s existing canonicalization/hardening utilities where available, rather than inventing a parallel incompatible serializer.

## 11.8 Vote persistence

P3a vote persistence model is:

```text
ephemeral, in-memory, non-durable
```

Votes exist only for the synchronous evaluation.

P3a MUST NOT persist votes to durable storage.

P3a MUST NOT restore votes after crash.

P3a MUST NOT modify durable artifacts.

## 11.9 Early termination and early rejection

P3a MUST include deterministic early termination rules.

For `MajorityVote`:

```text
if yes_count >= quorum:
    committed
elif yes_count + missing_count < quorum:
    rejected with reason = "insufficient_quorum"
else:
    deferred
```

This prevents `deferred` when quorum is mathematically impossible.

In P3a, `insufficient_quorum` is represented as:

```text
outcome = "rejected"
reason = "insufficient_quorum"
```

`insufficient_quorum` is not a separate operational outcome in P3a.

A separate `outcome = "insufficient_quorum"` may be considered in P3b/P3c only if durable/replay or external API contracts require it.

## 11.10 Tie handling note

`MajorityVote` uses absolute majority quorum.

If `yes_count == no_count`, `yes_count` is below majority quorum. Therefore tie cannot commit. The outcome is `rejected` or `deferred` depending on missing vote availability and early rejection rules.

---

# 12. Policy and Strategy Semantics

## 12.1 `policy_ref` as strategy selector in P3a

P3a uses the existing `policy` clause as a strategy selector.

Approved initial strategy names:

```text
MajorityVote
UnanimousVote
NoVetoVote
```

Unknown strategy names MUST fail validation.

If `policy_ref` evaluates to a Governance Policy object, the adapter MUST extract only its stable identifier or name and compare that identifier or name against the approved P3a strategy names.

The adapter MUST NOT execute the policy guard.

The adapter MUST NOT call `policy_allows()`.

The adapter MUST NOT trigger policy side effects.

The adapter MUST NOT treat arbitrary governance policy content as consensus strategy logic.

## 12.2 No governance guard execution in P3a

P3a MUST NOT execute governance policy guards during consensus strategy resolution.

Governance integration is deferred.

## 12.3 `MajorityVote`

Commit rule:

```text
yes_count >= quorum
```

Early rejection:

```text
yes_count + missing_count < quorum
```

When early rejection occurs due to unreachable quorum, the result is:

```text
outcome = "rejected"
reason = "insufficient_quorum"
```

`no` and `abstain` do not count as `yes`.

## 12.4 `UnanimousVote`

`UnanimousVote` requires every participant to vote `yes`.

Rules:

```text
if yes_count == participant_count:
    committed
elif no_count > 0:
    rejected
elif abstain_count > 0:
    rejected
elif missing_count > 0:
    deferred
```

For `UnanimousVote`, `abstain` acts as rejection because unanimity requires affirmative support.

## 12.5 `NoVetoVote`

`NoVetoVote` commits when quorum is reached and no participant votes `no`.

Rules:

```text
if no_count > 0:
    rejected
elif yes_count >= quorum:
    committed
elif yes_count + missing_count < quorum:
    rejected with reason = "insufficient_quorum"
else:
    deferred
```

For default P3a `NoVetoVote`, `abstain` does not count as `yes` and does not count as `no`.

---

# 13. Outcome and Error Model

## 13.1 Valid operational outcomes

P3a operational outcomes are:

```text
committed
rejected
deferred
```

These are valid consensus results and MAY be bound to the requested binding.

## 13.2 `insufficient_quorum` in P3a

In P3a, `insufficient_quorum` is not a separate operational outcome.

It is represented as:

```text
outcome = "rejected"
reason = "insufficient_quorum"
```

This preserves the synchronous reducer scope while still representing the reason required by the consensus contract.

## 13.3 Reserved future outcome

`timeout` is reserved for P3b/P3c.

P3a MUST NOT produce `timeout` through wall-clock measurement.

## 13.4 Validation classification

`invalid_request` is a validation classification, not a normal bound consensus outcome in P3a.

Structural invalid request conditions MUST:

- raise a controlled validation error;
- fail closed;
- stop the current statement evaluation;
- not bind a consensus result;
- not append a consensus decision event;
- not mutate `actor_log`;
- not mutate `consensus_tickets`.

## 13.5 Structural invalid cases

Structural invalid cases include:

- empty participants;
- duplicate participants;
- unresolved participant identity;
- unsupported participant identity;
- invalid vote state;
- conflicting vote entries for one participant;
- vote for unknown participant;
- quorum less than 1;
- quorum greater than participant count;
- negative timeout;
- non-integer quorum;
- non-integer timeout;
- boolean quorum or timeout;
- unknown strategy;
- malformed VoteSource payload;
- unsupported evaluated `policy_ref`;
- Governance Policy object whose identifier or name does not exactly match an approved strategy.

## 13.6 Controlled validation error

The final implementation may define a dedicated exception type such as:

```text
ConsensusValidationError
```

or reuse an approved runtime validation exception. The behavior is normative; the exact class name may be implementation-specific.

If the consensus engine raises `ConsensusValidationError` or an equivalent structural validation failure, the interpreter adapter MUST halt current statement evaluation and translate the failure into a controlled Synapse runtime error.

The adapter MUST NOT swallow the error.

The adapter MUST NOT convert the error into `rejected` or `deferred`.

The adapter MUST NOT continue execution as if validation failure were a normal operational outcome.

---

# 14. Canonical Result Shape

P3a valid operational results SHOULD use a stable structured shape.

Recommended result shape:

```json
{
  "schema_version": "consensus.result.v1",
  "proposal_id": "sha256:...",
  "outcome": "committed",
  "committed": true,
  "reason": "quorum_reached",
  "topic": "deploy_v2",
  "participants": ["A", "B", "C"],
  "coordinator": "Guide",
  "strategy": "MajorityVote",
  "policy": "MajorityVote",
  "votes": {
    "A": {"vote": "yes", "source": "explicit_map"},
    "B": {"vote": "yes", "source": "explicit_map"},
    "C": {"vote": "missing", "source": "missing"}
  },
  "vote_counts": {
    "yes": 2,
    "no": 0,
    "abstain": 0,
    "missing": 1
  },
  "deferred": false,
  "ticket_id": null
}
```

`outcome` is the primary semantic field.

`committed` may remain as compatibility convenience but MUST NOT replace `outcome`.

---

# 15. Canonical Event Schema

## 15.1 Event type

P3a SHOULD emit one canonical decision event for valid operational outcomes:

```text
distributed_consensus_decided
```

Existing event names such as `distributed_consensus_committed` and `distributed_consensus_deferred` are legacy facade event names.

## 15.2 Event payload

Recommended event shape:

```json
{
  "type": "distributed_consensus_decided",
  "schema_version": "consensus.event.v1",
  "proposal_id": "sha256:...",
  "statement_identity": "source:line:column",
  "outcome": "committed",
  "reason": "quorum_reached",
  "participants": ["A", "B", "C"],
  "coordinator": "Guide",
  "strategy": "MajorityVote",
  "policy": "MajorityVote",
  "quorum": 2,
  "timeout": 30,
  "vote_counts": {
    "yes": 2,
    "no": 0,
    "abstain": 0,
    "missing": 1
  },
  "votes_hash": "sha256:...",
  "result_hash": "sha256:..."
}
```

The canonical event payload MUST be compatible with existing history integrity behavior.

The event payload MUST be serializable through the project-approved canonical JSON mechanism.

The consensus engine MUST NOT introduce an incompatible serializer for event payloads.

## 15.3 Event payload size discipline

The immediate `ConsensusDecision` result may include full normalized votes.

The canonical event payload SHOULD include `vote_counts` and `votes_hash`.

The full vote map SHOULD NOT be required in event payload.

P3c MUST preserve history-size discipline.

## 15.4 No timestamp

Canonical consensus events MUST NOT include nondeterministic timestamps in P3a.

## 15.5 Invalid request events

Structural validation failures MUST NOT append consensus decision events.

If a validation error occurs after request construction but before decision event append, the adapter MUST ensure that no partial `distributed_consensus_decided` event is emitted.

---

# 16. Ticket Semantics

## 16.1 No P3a ticket lifecycle

P3a MUST NOT create or mutate `consensus_tickets`.

P3a MUST NOT allocate persistent ticket state.

P3a deferred result MUST NOT allocate `ticket_id`.

## 16.2 Proposal ID as correlation

For P3a deferred results, `proposal_id` is sufficient for immediate correlation.

Stateful ticket lifecycle is deferred to P3b/P3c.

## 16.3 Future ticket design

P3b/P3c may later define:

- `ticket_id`;
- ticket state transitions;
- ticket persistence;
- pending vote lifecycle;
- stale ticket behavior;
- duplicate signal handling;
- replay behavior.

## 16.4 UUID prohibition

Entropy-based UUID generation MUST NOT appear in P3a consensus semantic path.

---

# 17. Advisory vs Semantic Fields

## 17.1 Semantic fields

The following fields are semantic in P3a:

```text
proposal_id
statement_identity
normalized participants
topic
quorum
strategy
policy-as-strategy-selector
vote_counts
votes_hash
outcome
reason
```

## 17.2 Advisory fields

The following fields are advisory unless promoted by a later approved RFC:

```text
trace_id
span_id
run_id
human-readable reason strings
debug metadata
display ordering
source display text
```

## 17.3 Advisory field rule

Advisory fields MUST NOT affect consensus outcome.

Advisory fields MUST NOT alter proposal semantic identity unless explicitly promoted by a later approved RFC.

---

# 18. Durable / Replay Boundary

## 18.1 P3a durable rule

P3a MUST NOT expand durable support.

`DistributedConsensusStmt` remains durable-unsupported until P3c.

## 18.2 P3c replay rule

P3c must later define:

```text
LIVE:
  validate proposal
  normalize participants
  collect approved votes
  compute outcome
  append canonical event

REPLAY:
  consume canonical event
  validate event matches current statement/proposal
  restore result from event
  do not recollect votes
  do not call live LLM
  do not regenerate proposal_id
  do not regenerate ticket identity
  fail closed on mismatch
```

## 18.3 Legacy event policy

Legacy `distributed_consensus_committed` and `distributed_consensus_deferred` events are treated as facade-era events.

P3c MUST define explicit migration or fail-closed consumption policy.

There MUST be no silent semantic upgrade of old facade events.

---

# 19. LLM Boundary

P3a MUST NOT:

- call `llm_backend.complete`;
- evaluate `LLMCall` as part of vote production;
- generate votes from model completions;
- fill missing votes through LLM;
- parse LLM output for consensus decisions;
- call provider APIs;
- add prompt templates for consensus votes;
- use `llm_context_cache` for vote production.

LLM-assisted voting belongs to P3d.

---

# 20. Implementation Architecture

## 20.1 Required separation

P3a SHOULD introduce a dedicated consensus engine.

Recommended module for later implementation:

```text
synapse/runtime/consensus_engine.py
```

This RFC does not create that module.

## 20.2 Consensus engine responsibilities

Consensus engine owns:

- participant normalization;
- duplicate detection;
- vote normalization;
- vote validation;
- strategy resolution;
- quorum derivation;
- quorum validation;
- timeout validation result handling;
- policy reference normalization after adapter extraction;
- outcome calculation;
- early rejection;
- proposal identity generation;
- result construction;
- event payload construction.

## 20.3 Interpreter adapter responsibilities

Interpreter adapter owns:

- AST node dispatch;
- expression evaluation;
- environment access;
- current actor/coordinator metadata extraction;
- `policy_ref` evaluation and safe identifier extraction;
- source coordinate / statement identity extraction;
- strict type validation before request construction;
- approved VoteSource acquisition;
- request construction;
- engine invocation;
- validation error translation;
- event append;
- actor log projection if approved;
- result binding.

## 20.4 Forbidden architecture

P3a MUST NOT expand `evaluate_distributed_consensus()` into a monolith.

The interpreter MUST NOT own:

- hardcoded votes;
- actor vote injection;
- strategy math;
- policy strategy implementation;
- governance policy guard execution;
- ticket state;
- daemon vote inspection;
- live LLM calls;
- durable behavior.

---

# 21. Interpreter Impact / Interpreter Contract

## 21.1 Interpreter role

The interpreter is an adapter, not the consensus engine.

`evaluate_distributed_consensus()` SHOULD become a narrow adapter during later implementation:

```text
1. Extract AST inputs.
2. Evaluate expressions through environment.
3. Extract stable statement identity from AST where available.
4. Safely evaluate policy_ref and extract a strategy identifier/name.
5. Validate primitive input types.
6. Build ConsensusRequest.
7. Call ConsensusEngine.decide(request).
8. Translate structural validation failures into controlled Synapse runtime errors.
9. Append canonical event for valid operational outcome.
10. Bind valid result.
11. Return valid result.
```

## 21.2 Interpreter MUST NOT

The interpreter MUST NOT:

1. calculate quorum semantics inline;
2. hardcode participant votes;
3. inject current actor as voter;
4. select outcome through local ad-hoc boolean logic;
5. generate UUID tickets;
6. call live LLM providers;
7. wait for actor votes;
8. inspect network or daemon state;
9. expand durable allowlists;
10. execute governance policy guards for P3a strategy resolution;
11. call `policy_allows()` for P3a strategy resolution;
12. silently coerce invalid values;
13. silently deduplicate participants;
14. mix P3a, P3b, P3c, and P3d in one method;
15. mutate `consensus_tickets`;
16. perform replay consumption for `DistributedConsensusStmt`;
17. swallow `ConsensusValidationError` or equivalent structural validation failures;
18. convert structural validation failure into a normal consensus result.

## 21.3 Runtime mode behavior

In P3a:

- LIVE may evaluate consensus as non-durable semantic runtime feature.
- REPLAY MUST NOT pretend durable consensus is supported.
- Durable/replay behavior remains blocked until P3c.

## 21.4 Event append ownership

Consensus engine prepares event payload.

Interpreter or approved runtime boundary appends event.

Engine MUST NOT mutate `execution_history` directly.

## 21.5 Actor log ownership

Engine MUST NOT mutate `actor_log` directly.

If actor log projection is approved, interpreter/runtime boundary performs it.

## 21.6 Durable contracts for other AST nodes

P3a MUST NOT alter existing durable contracts for other AST nodes.

## 21.7 Validation failure handling

If the consensus engine raises `ConsensusValidationError` or an equivalent structural validation failure, the interpreter adapter MUST:

1. catch or receive the validation failure through the approved error channel;
2. halt current statement evaluation;
3. translate the failure into a controlled Synapse runtime error;
4. avoid binding any consensus result;
5. avoid appending `distributed_consensus_decided`;
6. avoid mutating `actor_log`;
7. avoid mutating `consensus_tickets`;
8. avoid continuing as if the outcome were `rejected` or `deferred`.

---

# 22. Acceptance Criteria

## 22.1 Semantic acceptance

| ID | Case | Expected |
|----|------|----------|
| AC-P3A-001 | empty participants | controlled validation error |
| AC-P3A-002 | current actor not in participants | actor does not vote |
| AC-P3A-003 | no votes available | no commit |
| AC-P3A-004 | all required yes votes present | `committed` |
| AC-P3A-005 | yes below quorum with missing still able to reach quorum | `deferred` |
| AC-P3A-006 | explicit no under strategy reject rule | `rejected` |
| AC-P3A-007 | abstain vote | represented and strategy-handled |
| AC-P3A-008 | missing vote | represented as `missing` |
| AC-P3A-009 | duplicate participants | controlled validation error |
| AC-P3A-010 | quorum 0 | controlled validation error |
| AC-P3A-011 | quorum greater than participant count | controlled validation error |
| AC-P3A-012 | timeout less than 0 | controlled validation error |
| AC-P3A-013 | policy reference | approved strategy selector or validation error |
| AC-P3A-014 | event schema | versioned and canonical |
| AC-P3A-015 | P3a ticket behavior | no ticket allocation |
| AC-P3A-016 | docs matrix | remains Semantic facade until evidence closure |

## 22.2 LLM boundary acceptance

| ID | Case | Expected |
|----|------|----------|
| AC-P3A-017 | consensus execution | no live LLM provider call |
| AC-P3A-018 | vote source | deterministic direct source only |
| AC-P3A-019 | malformed vote payload | validation error, no LLM fallback |
| AC-P3A-020 | missing votes | remain `missing` |
| AC-P3A-021 | replay design | no model call during replay |
| AC-P3A-022 | docs/status | no claim that LLM produces P3a votes |

## 22.3 Scope-control acceptance

| ID | Case | Expected |
|----|------|----------|
| AC-P3A-023 | proposal_id | deterministic canonical hash, no UUID |
| AC-P3A-024 | failure model | CFT-style trust assumption, no BFT claim |
| AC-P3A-025 | execution model | synchronous reducer, no wait/suspend/network |
| AC-P3A-026 | coordinator | initiates but does not auto-vote |
| AC-P3A-027 | vote persistence | ephemeral in P3a |
| AC-P3A-028 | early rejection | impossible quorum rejected deterministically |
| AC-P3A-029 | duplicate participants after normalization | validation error |
| AC-P3A-030 | unknown policy strategy | validation error |
| AC-P3A-031 | deferred in P3a | no `ticket_id`, no `consensus_tickets` mutation |
| AC-P3A-032 | facade regression cases | empty participants, quorum 0, implicit actor injection removed |
| AC-P3A-033 | invalid_request structural failure | no binding, no consensus event |
| AC-P3A-034 | timeout in P3a | metadata only, no timeout outcome |
| AC-P3A-035 | top-level execution | no hidden voter |
| AC-P3A-036 | quorum/timeout types | strict integer validation, no coercion |
| AC-P3A-037 | policy clause | existing `policy` maps only to approved strategy names |
| AC-P3A-038 | event payload size | event uses `vote_counts` and `votes_hash` |
| AC-P3A-039 | grammar boundary | no parser/AST extension |
| AC-P3A-040 | conflicting votes | validation error |
| AC-P3A-041 | legacy events | no silent semantic upgrade |
| AC-P3A-042 | interpreter boundary | adapter delegates semantics to engine |
| AC-P3A-043 | Governance Policy object as policy_ref | identifier/name extracted safely, no guard execution |
| AC-P3A-044 | invalid Governance Policy object strategy | validation error |
| AC-P3A-045 | statement_identity extraction | stable AST/source identity passed to engine |
| AC-P3A-046 | validation error handling | translated to controlled runtime error, no event, no binding |
| AC-P3A-047 | canonical JSON compatibility | proposal/event payloads use approved canonical JSON mechanism |
| AC-P3A-048 | insufficient quorum | represented as rejected with reason `insufficient_quorum` |

---

# 23. Stop-Gates

Implementation remains blocked until these stop-gates are resolved.

```text
SG-P3-001 — VOTE_SOURCE_UNDEFINED
SG-P3-002 — PARTICIPANT_IDENTITY_UNDEFINED
SG-P3-003 — DUPLICATE_PARTICIPANT_POLICY_UNDEFINED
SG-P3-004 — QUORUM_FORMULA_UNDEFINED
SG-P3-005 — OUTCOME_MODEL_UNDEFINED
SG-P3-006 — POLICY_SEMANTICS_UNDEFINED
SG-P3-007 — EVENT_SCHEMA_UNDEFINED
SG-P3-008 — REPLAY_SEMANTICS_UNDEFINED
SG-P3-009 — TICKET_SEMANTICS_UNDEFINED
SG-P3-010 — LIVE_LLM_EXCLUSION_UNDEFINED
SG-P3-011 — FAILURE_MODEL_UNDEFINED
SG-P3-012 — P3A_EXECUTION_MODEL_UNDEFINED
SG-P3-013 — COORDINATOR_MODEL_UNDEFINED
SG-P3-014 — PROPOSAL_IDENTITY_UNDEFINED
SG-P3-015 — VOTE_PERSISTENCE_UNDEFINED
SG-P3-016 — EARLY_TERMINATION_UNDEFINED
SG-P3-017 — POLICY_STRATEGY_UNDEFINED
SG-P3-018 — INTERPRETER_BOUNDARY_UNDEFINED
SG-P3-019 — CONSENSUS_ENGINE_BOUNDARY_UNDEFINED
SG-P3-020 — EVENT_APPEND_OWNERSHIP_UNDEFINED
SG-P3-021 — P3A_RUNTIME_MODE_BEHAVIOR_UNDEFINED
SG-P3-022 — P3A_GRAMMAR_BOUNDARY_UNDEFINED
SG-P3-023 — VALIDATION_ERROR_BEHAVIOR_UNDEFINED
SG-P3-024 — P3A_TICKET_POLICY_UNDEFINED
SG-P3-025 — SEMANTIC_VS_ADVISORY_FIELDS_UNDEFINED
SG-P3-026 — GOVERNANCE_POLICY_REF_COLLISION_UNDEFINED
SG-P3-027 — STATEMENT_IDENTITY_EXTRACTION_UNDEFINED
SG-P3-028 — CANONICAL_JSON_HISTORY_INTEGRITY_UNDEFINED
SG-P3-029 — INSUFFICIENT_QUORUM_REPRESENTATION_UNDEFINED
```

---

# 24. Required Test Groups

P3a implementation MUST include tests for:

1. canonical distributed consensus grammar still parses;
2. no parser or AST extension introduced;
3. empty participants fail validation;
4. duplicate participants fail validation;
5. unresolved participant identity fails validation;
6. quorum derivation;
7. quorum 0 fails validation;
8. quorum above participant count fails validation;
9. negative timeout fails validation;
10. string quorum fails validation;
11. float quorum fails validation;
12. boolean quorum fails validation;
13. `None` quorum fails validation when explicit;
14. string timeout fails validation;
15. float timeout fails validation;
16. boolean timeout fails validation;
17. top-level execution has no hidden voter;
18. current actor not injected as voter;
19. explicit yes/no/abstain/missing handling;
20. missing is explicit state;
21. conflicting vote entries fail validation;
22. majority commit;
23. majority deferred;
24. majority early rejection;
25. insufficient quorum returns `rejected` with reason `insufficient_quorum`;
26. unanimous commit;
27. unanimous abstain rejection;
28. no-veto commit;
29. no-veto rejection;
30. unknown policy strategy fails validation;
31. Governance Policy object policy_ref extracts identifier/name only;
32. Governance Policy object guard is not executed;
33. invalid structural failure appends no event;
34. invalid structural failure binds no result;
35. validation error is translated to controlled runtime error;
36. valid operational outcome appends canonical event;
37. deferred in P3a has no `ticket_id`;
38. deferred in P3a does not mutate `consensus_tickets`;
39. no live LLM call;
40. no daemon packet inspection;
41. durable allowlist unchanged;
42. legacy facade regression cases covered;
43. interpreter delegates semantics to consensus engine;
44. statement identity is extracted and passed to engine;
45. canonical JSON mechanism is used for proposal/event payload hashing;
46. canonical event contains `vote_counts` and `votes_hash`.

---

# 25. Evidence Requirements

P3a completion evidence MUST include:

1. final RFC approval reference;
2. implementation commit reference;
3. changed-file list;
4. explicit statement that parser/AST were not expanded;
5. explicit statement that durable allowlist was not expanded;
6. test command list;
7. test result counts;
8. known failures, if any;
9. CI reference, if available;
10. before/after behavior comparison for facade cases;
11. empty participants regression proof;
12. quorum 0 regression proof;
13. implicit actor vote removal proof;
14. LLM exclusion proof;
15. daemon/network independence proof;
16. strict type validation proof;
17. no-ticket-in-P3a proof;
18. no-event-on-validation-error proof;
19. consensus engine boundary proof;
20. documentation/status honesty proof;
21. Governance Policy object collision proof;
22. statement identity extraction proof;
23. canonical JSON / history integrity compatibility proof;
24. insufficient quorum representation proof.

---

# 26. Documentation Requirements

P3a documentation MUST cover:

1. current status before evidence closure;
2. grammar retained in P3a;
3. no parser/AST expansion;
4. participant identity rules;
5. vote model;
6. strategy model;
7. outcome model;
8. `insufficient_quorum` as rejected reason in P3a;
9. validation error behavior;
10. timeout metadata-only behavior;
11. no-ticket P3a behavior;
12. no-live-LLM boundary;
13. durable boundary;
14. interpreter adapter boundary;
15. consensus engine ownership;
16. safe handling of `policy_ref` as approved strategy selector;
17. examples relying on facade behavior must be updated, quarantined, or explicitly marked obsolete.

Until P3a evidence closure, capability matrix and docs MUST continue marking distributed consensus as Semantic facade.

If no user-facing explicit vote syntax exists in P3a, documentation MUST NOT invent such syntax. A canonical user-facing distributed consensus example is deferred until an approved VoteSource surface exists.

---

# 27. Deferred Technical Debt / Future Patch Items

The following items are intentionally not part of P3a.

## P3-TD-001 — Strategy Grammar Cleanup

Evaluate adding `strategy <strategy_name>` as an alternative syntax or replacement for `policy <policy_ref>` in distributed consensus grammar after P3a.

P3a MUST NOT change parser grammar.

Any grammar change requires a separate RFC or amendment and is not part of P3a.

## P3-TD-002 — User-Facing VoteSource Example

Add canonical `.syn` example once an approved user-facing VoteSource surface exists.

P3a may update or quarantine facade examples but MUST NOT invent syntax.

## P3-TD-003 — Consensus Instance Identity

Define `consensus_instance_id` or `decision_id` for loops, recursion, repeated proposal evaluation, durable replay, and P3b/P3c event correlation.

P3a `proposal_id` remains semantic proposal fingerprint.

## P3-TD-004 — Vote Lifecycle and Immutability

Define allowed transitions such as:

```text
missing -> yes
missing -> no
missing -> abstain
```

and define repeated vote handling, stale vote handling, vote override behavior, and conflict behavior for P3b/P3c.

## P3-TD-005 — VoteSource Registry

Design runtime VoteSource registry for P3b.

This may include actor-backed, mailbox-backed, daemon-backed, and later LLM-backed providers under separate approval.

## P3-TD-006 — Durable Vote Payload Storage

Define whether full votes are stored, summarized, externalized, or hash-referenced in P3c durable history.

## P3-TD-007 — Governance Policy Integration

Decide whether distributed consensus strategies remain local named strategies or integrate with governance policy objects in a later RFC.

## P3-TD-008 — Timeout Runtime Semantics

Define actual timeout behavior only when P3b/P3c introduces waiting, suspension, scheduler interaction, or durable timeout handling.

## P3-TD-009 — Stateful Ticket Lifecycle

Define ticket creation, persistence, stale handling, duplicate signal handling, and replay behavior in P3b/P3c.

## P3-TD-010 — Separate `insufficient_quorum` Outcome

Evaluate whether future durable/replay or external API contracts require `outcome = "insufficient_quorum"` as a separate outcome instead of P3a’s representation as `outcome = "rejected"` with `reason = "insufficient_quorum"`.

---

# 28. Non-Goals

P3a does not provide:

1. production distributed consensus protocol;
2. cross-node consensus;
3. Byzantine fault tolerance;
4. leader election;
5. view-change;
6. durable vote persistence;
7. replay closure;
8. network transport;
9. daemon-integrated voting;
10. mailbox wait;
11. suspension wait;
12. live LLM vote generation;
13. timeout runtime behavior;
14. stateful ticket lifecycle;
15. parser grammar migration;
16. new AST nodes;
17. user-facing vote syntax;
18. governance policy guard execution;
19. compatibility guarantee for legacy facade event replay;
20. production status upgrade;
21. runtime-instance identity for repeated proposal evaluation;
22. separate `insufficient_quorum` outcome in P3a.

---

# 29. Final Decision Summary

P3a MUST close the semantic facade by replacing hardcoded votes and implicit actor voting with deterministic content-sensitive evaluation.

P3a MUST remain inside the approved semantic-core stage.

P3a MUST NOT expand into parser changes, durable changes, network behavior, live LLM voting, timeout waiting, stateful tickets, runtime VoteSource registry, or governance policy execution.

The interpreter MUST become an adapter.

The consensus engine MUST own semantic decision rules.

`policy_ref` MUST be handled safely as an approved strategy selector only.

`statement_identity` MUST participate in `proposal_id`.

Canonical proposal and event payloads MUST use the project-approved canonical JSON mechanism and remain compatible with history integrity rules.

Structural validation failures MUST produce controlled runtime errors and MUST NOT bind results or append consensus events.

In P3a, `insufficient_quorum` is represented as `outcome = "rejected"` with `reason = "insufficient_quorum"`.

Implementation remains blocked until this RFC is approved.

---

# 30. Final RFC Verdict

```text
RFC-CONSENSUS-P3 status: DRAFT — TEAM REVIEW AND PRODUCT OWNER APPROVAL REQUIRED
P3 Phase 0 audit status: COMPLETE FOR RFC APPROVAL
P3a implementation status: BLOCKED UNTIL RFC APPROVED
P3b implementation status: BLOCKED
P3c implementation status: BLOCKED
P3d implementation status: BLOCKED
Repository changes authorized by this RFC: DOCUMENTATION ONLY
```
