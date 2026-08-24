# Synapse Replay Determinism Model

- **Status:** RATIFIED AND FROZEN — OD-10/V1
- **Frozen:** 2026-08-22, by decision of the repository owner
- **Purpose:** supply the formal foundation Stage 4 §23 (Patch 9) needs, by
  deriving it from the implemented runtime rather than by choosing it
- **Resolves:** OD-10-A (Gold replay host-call profile), OD-10-B (activity
  record schema and side-effect policy). Both were carried as *proposed* until
  the date above; every earlier statement of them in this document and in
  `docs/CHANGELOG.md` is superseded by §9 below.
- **Derivation base:** `synapse/cvm.py`, `synapse/runtime/vm_routing.py`,
  `synapse/runtime/host_abi.py`, `docs/DETERMINISM_CONTRACT.md`
- **Verified by:** `tests/test_replay_determinism_model.py` — every definition
  and every theorem below has an executable check, and §9 additionally has
  conformance checks that read the frozen contents out of the implementation, so
  the decision and the code cannot drift apart silently
- **Authority:** ratification is a human act under §41 and this document does not
  perform one; it records the decision that was taken. What ratification does
  *not* do is listed in §9.6 — it certifies no pull request, closes no audit
  finding, establishes no FULL, replaces no oracle, and changes neither the
  single canonical entry point nor the protected core.

---

## 0. Why derive instead of choose

§23 requires a frozen replay host-call profile and a frozen activity schema
before Patch 9 may be implemented. Both could be *chosen*, but a chosen profile
is an opinion that a reviewer has to accept on trust.

The runtime already fixes the answer. `CognitiveVM` implements a concrete
transition function, a concrete state projection into the transition hash, and a
concrete gas cost function; `DETERMINISM_CONTRACT.md` already states the
categories in prose. Together these determine which operations can be replayed
and what an activity record must contain. This document extracts that, states it
formally, and proves the consequences.

The method has a useful side effect: where the derivation cannot be discharged,
the obstruction is a defect in the runtime, not a gap in the document. Three such
obstructions were found and are recorded in §7 as proof obligations.

---

## 1. State space

**Definition 1.1 (VM state).** A state is the tuple

```
σ = ( ip, stack, locals, gas, call_stack, guard_stack,
      context_stack, actor_stack, policy_stack, name_save_stack,
      mailbox_inbound, mailbox_outbound,
      pending_message_receive, pending_host_call, transition_hash )
```

taken exactly from `VMState`. Σ denotes the set of states.

**Definition 1.2 (program).** A program `P` is an ordered instruction sequence
with a constant pool, identified by `program_hash`. `P[i]` is the instruction at
`ip = i`.

**Definition 1.3 (transition).** One `step()` is a partial map

```
δ : Σ × P → Σ ∪ { ⊥_gas, ⊥_op, ⊥_stack, ⊥_host }
```

where the ⊥ values are the typed failures `OutOfEnergy`, `UnknownOpcodeError`,
`VMStackUnderflow` and `VMHostError`. δ is partial because a pending host call
suspends it: `step()` raises when `state.pending_host_call` is set.

---

## 2. Observable projection

The transition hash is what the replay contract actually compares. It does not
observe all of σ.

**Definition 2.1 (projection π).** From `CognitiveVM._hash_transition`:

```
π(σ) = ( ip,
         sorted(locals.keys()),          # keys only
         |stack|,                        # length only
         repr(stack[-1]) if stack else None,   # top only
         gas,
         context_stack, actor_stack, policy_stack,
         mailbox_inbound, mailbox_outbound,
         pending_message_receive )
```

**Definition 2.2 (π-equivalence).** σ₁ ≡π σ₂ iff π(σ₁) = π(σ₂).

**Proposition 2.3.** ≡π is strictly coarser than state equality: there exist
σ₁ ≠ σ₂ with σ₁ ≡π σ₂.

*Proof.* Exhibited: two states differing only in local *values* under an equal
key set, and two states differing only in non-top stack entries under equal
length and top. Both collide. Checked by
`test_projection_is_strictly_coarser_than_state_equality`. ∎

Proposition 2.3 is not a defect by itself — a projection is meant to be coarser.
It becomes one in §7.1, where it is combined with the injection rule.

---

## 3. Determinism predicate

`DETERMINISM_CONTRACT.md` §3 states three categories in prose. Formally, for an
instruction `op` and the history ρ available to replay:

**Definition 3.1 (Category A — canonical deterministic).**

```
D_A(op) ⟺ ∀σ. δ(σ, op) is a function of (σ, op) alone
```

No live uuid, random, host clock, provider call or external source contributes.

**Definition 3.2 (Category B — replay-safe recorded nondeterminism).**

```
D_B(op) ⟺ ∃ recorded r ∈ ρ such that
           δ_replay(σ, op, r) = δ_live(σ, op)  ∧  replay does not invoke the live producer
```

The contract is explicit that recorded consumption is the *only* approved
mechanism; excluding an event from hash comparison is not an alternative.

**Definition 3.3 (Category C).** ¬D_A(op) ∧ ¬D_B(op).

**Definition 3.4 (contagion).** If event at index n is Category C then every
hash from n onward is unstable. Formally the chain hash is
`h_n = H(h_{n-1}, e_n)`, so instability at n propagates to all m > n.

---

## 4. Theorem 1 — the admissible replay profile (OD-10-A)

**Theorem 4.1.** An instruction `op` is admissible under `REPLAY_IDENTICAL` iff

```
D_A(op)  ∨  ( D_B(op) ∧ op's result is bound by an activity record
                        whose identity separates results that may differ )
```

*Proof.* (⇐) If D_A, δ is a function of state and instruction, so replay from an
equal state yields an equal state, preserving π and hence the chain hash. If D_B
with a separating identity, replay resolves the same record, injects the same
result, and reaches the same successor state. (⇒) If neither holds, δ_replay may
differ from δ_live at some σ; by Definition 3.4 the divergence propagates, so the
run is not `REPLAY_IDENTICAL`. ∎

**Corollary 4.2 (the profile).** Partitioning the implemented opcode set by
Definition 3.1:

*Category A — admissible without any activity record.* Stack and constant
operations (`LOAD_CONST`, `LOAD_NAME`, `LOAD_NONE`, `LOAD_TRUE`, `LOAD_FALSE`,
`STORE`, `POP`, `DUP`, `SAVE_NAME`, `RESTORE_NAME`); control flow (`JUMP`,
`JUMP_IF_FALSE`, `JUMP_IF_TRUE`, `CALL`, `RETURN`, `MAKE_FUNCTION`, `HALT`);
arithmetic, comparison and logic; structure building and access (`BUILD_LIST`,
`BUILD_DICT`, `INDEX`, `MEMBER`); and the scope-boundary opcodes
(`CONTEXT_*`, `ACTOR_*`, `POLICY_*`, `GUARD_*`), whose effect is confined to
stacks already inside σ.

*Category B — admissible only through a governed activity.* Every opcode whose
result originates outside σ: `LLM_EVAL`, `LLM_REQUEST`, `LLM_RESUME`,
`PROMPT_BUILD`, `DREAM`, `IMPRINT`, `RECALL`, `AFFECT_EVENT`, `AFFECT_STATE`,
`METRICS`, `HOST_EVAL`, `CALL_HOST`, `FRACTURE_SELF`, `HABIT_SUGGEST`,
`THRESHOLD_CHECK`, `SEND`, `RECEIVE`, `MSG_SEND`, `MSG_RECEIVE`.

*Category C — inadmissible.* Any opcode reaching a live producer without a
record. No implemented opcode is inherently C; an opcode falls into C when its
governing activity record is missing, which is a runtime condition, not a
property of the instruction.

**Remark 4.3.** The profile is therefore not a list to be chosen but a
partition induced by Definition 3.1. What a human must ratify is not *which*
opcodes are in it, but whether Definition 3.1 is the intended predicate.

**Remark 4.4 (why the implemented profile has three groups, not two).**
Categories A and B above partition opcodes by whether δ is a function of (σ, op).
`CALL` and `CALL_METHOD` satisfy that as *instructions* and are listed in
Category A accordingly — but the implementation executes an ordinary Python
callable inline for both, without passing through host routing, so what they do
is a property of the *occurrence* rather than of the instruction. An occurrence
dispatching to a compiled Synapse `FunctionObject` is an internal transition; one
dispatching to arbitrary Python is not replayable and is not reachable by the
governed channel either. Neither Category A nor Category B describes that, so the
implemented profile splits them into a third group, decided per dispatch and
before the machine moves. §9.1 freezes it.

---

## 5. Theorem 2 — activity identity (OD-10-B)

**Definition 5.1 (activity).** A governed activity is a quadruple

```
a = ( kind, inputs, policy, result )
```

with an identity function `id(a)` and a recorded result hash.

**Theorem 5.2 (separation requirement).** For recorded injection to preserve
`REPLAY_IDENTICAL`, `id` must satisfy

```
∀ a₁, a₂ :  result(a₁) ≠ result(a₂)  ⟹  id(a₁) ≠ id(a₂)
```

*Proof.* Suppose `id(a₁) = id(a₂)` with different results. Replay resolving that
identity may inject either result, producing successor states that differ in the
stack top, hence in π, hence in the chain hash. By Definition 3.4 the divergence
propagates. ∎

**Corollary 5.3 (minimum content).** `id` must be a function of at least the
complete inputs, the governing policy version and the execution position,
because each can change a result independently.

**Corollary 5.4 (the current identity does not qualify).**
`compute_call_id(program_hash, ip, transition_hash, event_id, frame_depth)` takes
no argument vector, and `transition_hash` binds only the stack top by
Definition 2.1. Two calls to the same symbol at the same position with different
non-top arguments therefore share an identity while their results may differ,
violating Theorem 5.2.

This is an obstruction, not a design choice; see §7.2.

---

## 6. Cost, composition and equivalence

**6.1 Cost function.** Gas is already the cost function: `GAS_COSTS : op → ℕ`
with a back-edge surcharge `GAS_BACK_EDGE = 2` charged when `target_ip ≤
executed_ip`. Total cost of a run is the sum over executed instructions plus
surcharges. It is deterministic, so it does not weaken Theorem 4.1. Note that
gas measures *CVM execution*, a different resource from the provider tokens
modelled in the Gold token economy model; the two are not interchangeable and
must not be summed.

**6.2 Composition with §21.** A replay executes against exactly one committed
`AtomicSnapshotBoundary`. Because the snapshot fixes the library, index and
lifecycle roots, and because Theorem 4.1 requires the same records to be
resolvable, replay is only defined relative to a snapshot whose completeness
decision is `COMPLETE`. A replay request naming no boundary, or naming an
uncommitted one, is inadmissible before it begins.

**6.3 Composition with §22.** Every subject a replay loads must have crossed the
consumption gate, and the gate must run *before* compilation, not before
execution: compiling an inadmissible behavior already consumes it as input. The
ordering obligation is therefore

```
consumption gate  ≺  compile  ≺  first transition
```

**6.4 Theorem 3 — the only justified relation.** `REPLAY_IDENTICAL` is the only
equivalence for which recorded consumption is sound under Definitions 2.1–3.2.

*Proof.* Soundness of injection was established relative to π (Theorem 5.2). A
weaker relation ≈ would admit σ₁ ≈ σ₂ with π(σ₁) ≠ π(σ₂); the chain hash then
differs, and no mechanism in the contract reconciles two different hashes as
equivalent. Defining ≈ therefore requires a reconciliation rule that does not
exist. ∎

This independently confirms §23's position that semantic equivalence stays
disabled, and supplies the reason: not caution, but the absence of a
reconciliation rule for the hash chain.

---

## 7. Proof obligations

The derivation is blocked at three points. Each is a defect in the runtime
surfaced by the model, each is empirically demonstrated by the accompanying
tests, and each must be discharged before the profile of §4 is safe to rely on.

**7.1 The projection does not bind local values or non-top stack entries.**
Two states differing in local values under an equal key set, or in non-top stack
entries under equal length and top, produce the same transition hash. Combined
with Theorem 5.2 this means the transition hash cannot serve as the sole
separator for host-call identity. *Demonstrated by*
`test_projection_is_strictly_coarser_than_state_equality`.

**7.2 Host-call identity does not bind the call arguments.** *Discharged in
Patch 9.*
`compute_call_id` has no argument parameter. By Corollary 5.4 this violates the
separation requirement, so Patch 9 could not reuse it. The obligation is
discharged by `compute_activity_lookup_key` in
`synapse/experiments/gold/activities.py`, which hashes exactly the content
Corollary 5.3 fixes — activity kind, the complete input vector, the governing
policy version and the execution position — under its own domain separator. It
is the key a replay resolves by, so it is the key whose collisions would inject
the wrong recorded result, which is what the obligation is about.

§23 additionally requires activity identity to include the *result* hash. That
cannot be the same key, since a replay looking a result up does not yet know it;
`compute_activity_identity` is the second, result-bound key — it folds the
lookup key together with the result hash and the reference the bytes live behind
— and it is what makes a substituted recorded result detectable to a holder of
the identity.

The runtime defect itself is *not* repaired: `compute_call_id` still binds no
inputs. What changed is that no governed replay path depends on it any more.
The obligation is no longer carried as a failing check against the protected
core — NR-03 forbids Stage 9 to repair that table, so such a check could only
record a debt another owner holds. What Stage 9 owes is that its own identity
separates what `compute_call_id` cannot, and that is asserted directly. That separation is deliberate — repairing `compute_call_id` would
alter the identity of every historical host call in the protected core, which
NR-03 does not permit from this layer. *Demonstrated by*
`test_call_identity_does_not_separate_arguments` (the defect stands) and
`test_activity_lookup_key_discharges_obligation_7_2` (the replacement satisfies
Theorem 5.2).

**7.3 Nine of seventeen effect-bearing opcodes are unclassified.**
`FIXED_HOST_ABI_OPCODES` covers 8 of the 17 opcodes that Corollary 4.2 places in
Category B. `CALL_HOST`, `HABIT_SUGGEST`, `LLM_REQUEST`, `MSG_RECEIVE`,
`MSG_SEND`, `PROMPT_BUILD`, `RECEIVE`, `SEND` and `THRESHOLD_CHECK` classify as
`HOST_EVAL / unknown_or_dynamic_opcode`. `LLM_REQUEST` is the alpha3e LLM bridge
and the most nondeterministic opcode in the machine. Separately, the opcode table
and the `SYS_*` symbol tables occupy disjoint namespaces with an empty
intersection, so neither is a complete classification of the other.
The runtime tables are another owner's to complete, so this is not carried as a
failing Stage 9 check. What Stage 9 asserts is that its own profile is total and
disjoint over `GAS_COSTS`, which is what keeps a governed replay off those
tables entirely.

*Partially discharged in Patch 9, for the replay path only.*
`synapse/experiments/gold/replay.py` publishes the partition of Corollary 4.2 as
`REPLAY_ADMISSIBLE_OPCODES` / `RECORDED_ONLY_OPCODES`, which is total over
`GAS_COSTS` and checked to be so by the acceptance layer, together with a
`ACTIVITY_KIND_BY_OPCODE` map that is total over the recorded-only half. A
governed replay therefore never executes an opcode whose determinism class is
unknown. This does not repair the runtime tables: `classify_host_opcode` still
answers `unknown_or_dynamic_opcode` for nine Category B opcodes, and the opcode
and `SYS_*` namespaces still do not meet. Any path that consults those tables
rather than the profile remains subject to §7.3 as written.

---

## 8. What a ratifier was asked to accept

*Answered on 2026-08-22. The four points below are the question as it stood; the
answer, and the contents it froze, are §9. This section is kept unrewritten
because what was asked is part of the record of what was decided.*


1. That Definition 3.1 is the intended determinism predicate. Everything in §4
   follows from it mechanically.
2. That Theorem 5.2 is the intended soundness requirement for activity identity.
   Corollary 5.3 then fixes the minimum content of the activity record.
3. That the three obligations in §7 are accepted as defects to be closed in
   Patch 9, rather than as tolerances. §7.2 is discharged; §7.1 and §7.3 stand.

Nothing else in this document requires a decision; the remainder is derivation.

---

## 9. OD-10/V1 — the frozen decision

Ratified and frozen on 2026-08-22 by decision of the repository owner. Sections
0–8 remain the derivation; this section is the decision itself, and where the two
differ in wording this one governs. Everything here has a conformance check in
`tests/test_replay_determinism_model.py` that reads the frozen content out of the
implementation rather than restating it.

### 9.1 Capability profile

`REPLAY_CAPABILITY_PROFILE_V1` consists of three pairwise disjoint groups:

- `REPLAY_ADMISSIBLE_OPCODES`
- `RECORDED_ONLY_OPCODES`
- `DISPATCH_GUARDED_OPCODES` = `{"CALL", "CALL_METHOD"}`

`CALL` and `CALL_METHOD` are **not** unconditionally deterministic. Their check
runs before dispatch and may not invoke user Python code — no descriptors, no
properties, no `__getattribute__`, no `__repr__` or other representation method.
This is not a style rule: an ordinary attribute lookup *is* the value's own code,
so a guard that asks a value a question has already run what it was deciding
whether to run.

An unknown opcode, an opaque value, an ordinary Python callable and a target that
cannot be resolved statically are each refused fail-closed.

`REPLAY_IDENTICAL` is the only admissible relation. Semantic equivalence is not
permitted, and no failure reason may produce it.

### 9.2 Activity schema V1

An activity record binds all of:

- the closed `ActivityKind` vocabulary;
- the complete input-digest vector;
- the execution position `(program_hash, instruction_pointer, frame_depth, sequence)`;
- the governing policy version;
- the result digest and the hash-bound result reference;
- `ACTIVITY_RESULT_CODEC_V1`;
- run, attempt, repository and environment provenance.

The result-bound **activity identity** binds the lookup key, the result digest,
the result reference *and* the codec. The codec is there because it is the one of
the four that the other three cannot see: the same bytes read under a different
codec denote a different value while digest and reference hold still.

A result is accepted only on an exact canonical round-trip — `decode` then
`encode` must return the same bytes. Parsing is not acceptance: JSON has many
spellings of one value, and accepting them would let several identities name one
injected value, which is the collision identity exists to prevent, running
backwards.

### 9.3 Side-effect policy

The policy vocabulary is exactly:

- `RECORDED_CONSUMABLE`
- `FORBIDDEN_IN_REPLAY`
- `REQUIRES_FRESH_AUTHORITY`

Only `RECORDED_CONSUMABLE` permits injecting an already-recorded result. The
other two permit no fresh call, no retry, and no later automatic escalation of
authority. In particular `REQUIRES_FRESH_AUTHORITY` is a refusal *during replay*
and never a weaker permission that ripens with time.

### 9.4 Who decides

Only `ACTIVITY_POLICY_EVALUATOR` decides, and it must be independent of the
**actual** producer, recorder, worker, model, replay executor, machine adapter
and consumer. Actor identities are resolved from trusted execution provenance.
Caller-declared actor names are not authority evidence — an actor set that merely
*states* the evaluator is somebody else proves nothing, because the statement and
the thing it describes have no connection until the identities are resolved from
the record of what actually happened.

### 9.5 Stage 9 ownership

| module | role |
| --- | --- |
| `replay.py` | owner — CognitiveVM integration and replay contracts |
| `activities.py` | owner — activity identity and recorded-result semantics |
| `activity_policy.py` | owner — activity-policy authority |
| `replay_store.py` | adapter of `replay.py` — durable Stage 9 history |
| `replay_capture.py` | adapter of `replay.py` — raw reference execution |
| `replay_composition.py` | composition root for `replay.py` |
| `activity_store.py` | adapter of `activities.py` |
| `activity_policy_store.py` | adapter of `activity_policy.py` |
| `persistence.py` | shared durability and integrity primitives |

An adapter depends on its owner and is never depended on by it.

A composition root is neither. It is the one module permitted to import an owner
together with its concrete adapters, and it exists because nothing else may: the
owner cannot name the exact store or the exact machine, and an adapter that
assembled a run would be choosing what the run is pointed at. The root decides no
rule — it calls the owner's rules by name and settles only the order they are
asked in — and nothing inside the package may import it, which is what keeps the
permission from leaking to whatever reaches through it.

`replay_capture.py` was, for one revision, an adapter by declaration and an owner
by behaviour: it held the authority position that may seal a capture, the rules
deciding whether a capture may become a manifest, the assembly of the capture
record, and the orchestration of the durable writes around them. Those are
statements about what a replay record means, so they belong to the owner; what
is left in the adapter is building or restoring the exact machines and driving
the admitted set once. `replay_store.py`
was briefly declared an owner, which concealed a cycle — it imports the replay
contracts, and the binding factory imported `FileReplayStore` back.

The cycle was first inverted through a registration slot inside the owner, which
the adapter filled on import. That was a first-writer hole: anything registering a
class before `replay_store` was imported became the production store for the
process, and the real one was then refused as a forgery. The slot is gone. The
owner asks only what it can ask without the type — that the history offers the
operations a governed replay needs, and that it holds the authority's exact
coordinator — while exactness for the type is asserted by
`replay_store.require_production_replay_store`, called by the composition root
that legitimately imports both files. The architecture tripwire is left at full
strength with no `xfail` and no `skip`.

### 9.6 What a replay is measured against

An expected outcome supplied by the party asking for the run is that party's
opinion, hashed. `expected_transcript_root` and
`expected_terminal_snapshot_digests` arrived as optional call arguments, and the
terminal digests could be omitted entirely, so a caller could pin whatever a run
happened to reach and read the answer back as identity.

Both now come from a `ReplayExecutionManifest`: written before the run, appended
to the same durable history as the request and the result, and resolved by
reference. It states the behaviours in execution order, their program hashes and
host ABI versions, the **initial** state each machine must start from — as a
content-addressed reference plus the digest that state must have — the
order-sensitive transcript root, and the terminal state each machine must reach.
None of these is optional: a run with no expected terminal state has nothing to
be identical *to*.

The executor builds its machines from that manifest rather than accepting them,
so there is no moment at which a caller holds the object a verdict will be read
off. A continuation's starting state is durable for the same reason and is
compared with the terminal state its predecessor recorded; before this the caller
brought the machine, and after a restart that state had to come from outside the
system entirely.

### 9.7 What ratification does not do

Freezing OD-10 clears none of the audit's findings, certifies no pull request,
establishes no `FULL`, substitutes for no oracle, and changes neither the single
canonical entry point nor the protected core. It removes one specific obstacle:
§41 forbids dependent code against an unfrozen decision, and Stage 9 is dependent
code. Everything else that was open is still open.
