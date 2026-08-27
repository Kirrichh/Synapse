# Synapse Replay Determinism Model

- **Status:** RATIFIED AND FROZEN — OD-10/V1; executable conformance is
  corrected by the explicit OD-10/V1-E1 erratum in §9.9
- **Frozen:** 2026-08-22, by decision of the repository owner
- **Current executable profile:**
  `synapse.stage4.gold.replay-capability-profile-e1/v1`; V1 and V1-E1
  histories are different evidence domains and are never mixed
- **Purpose:** supply the formal foundation Stage 4 §23 (Patch 9) needs, by
  deriving it from the implemented runtime rather than by choosing it
- **Resolves:** OD-10-A (Gold replay host-call profile), OD-10-B (activity
  record schema and side-effect policy). Both were carried as *proposed* until
  the date above; every earlier statement of them in this document and in
  `docs/CHANGELOG.md` is superseded by §9 below.
- **Derivation base:** `synapse/cvm.py`, `synapse/runtime/vm_routing.py`,
  `synapse/runtime/host_abi.py`, `docs/DETERMINISM_CONTRACT.md`
- **Verification:** `tests/test_replay_determinism_model.py` retains the
  derivation checks for the original V1 decision. E1 conformance requires
  acceptance that executes real CVM occurrences and observes activity,
  structural history and refusal boundaries; equality with an opcode table is
  necessary profile integrity, but is not execution-conformance evidence
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

**Historical-status note.** This subsection preserves the original V1
three-group decision as it was frozen. It is not silently rewritten after the
fact. The executable correction in §9.9 supersedes only the opcode execution
partition, activity cardinality, structural-history and capability-mapping
semantics named there. The remaining V1 decisions continue unchanged.

The historical `synapse.stage4.gold.replay-capability-profile/v1` consisted of
three pairwise disjoint groups:

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

`LLM_REQUEST` uses the protected core's pending-call protocol rather than a
second replay-specific transition. The first CVM step creates a pending envelope
that preserves `call_id` and `executed_ip`; the machine adapter resolves the
exact recorded `LLM_CALL`, decodes its result canonically, calls
`resume_host_call(call_id, result)` once, verifies that pending state cleared,
and only then allows execution to continue. There is no live producer behind
this path. A pending envelope restored from a snapshot follows the same protocol,
so restart cannot turn one recorded result into a double injection.

`HOST_STATUS` is service traffic, not a recorded activity. It does not consult
the activity ledger and does not advance `ActivityPosition.sequence`; its event
identity is derived from the sealed deterministic execution context minted by
the owner from the admitted envelope and passed through the production
composition path. For recorded traffic the next sequence number is proposed
before resolution and committed only after the exact activity resolves
successfully. A miss therefore cannot silently shift every later activity
position.

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
| `replay.py` | owner — CognitiveVM integration ports and replay contracts |
| `replay_machine_binding.py` | internal module of `replay.py` — sealed root-selected factory binding |
| `replay_structural_history.py` | internal module of `replay.py` — E1 structural commands and canonical history |
| `activities.py` | owner — activity identity and recorded-result semantics |
| `activity_policy.py` | owner — activity-policy authority |
| `replay_store.py` | adapter of `replay.py` — durable Stage 9 history |
| `replay_vm_adapter.py` | adapter of `replay.py` — protected-core machine integration |
| `replay_vm_codec.py` | internal component of `replay_vm_adapter.py` — canonical VM state/value and snapshot/result codecs |
| `replay_capture.py` | adapter of `replay.py` — raw reference execution |
| `replay_composition.py` | composition root for `replay.py` |
| `activity_store.py` | adapter of `activities.py` |
| `activity_policy_store.py` | adapter of `activity_policy.py` |
| `persistence.py` | shared durability and integrity primitives |

An adapter depends on its owner and is never depended on by it.

An adapter's internal component is a cohesion boundary inside that one concrete
integration. Only its adapter imports it; it cannot import the adapter, another
adapter, or a composition root. It therefore creates neither a parallel machine
path nor another owner/adapter point, and the adapter does not re-export its
symbols as a compatibility surface.

An internal owner module is different: it carries replay rules/contracts, not a
concrete integration. The owner and its adapters may therefore import these
components; they remain part of the one logical replay owner and are neither a
fourth replay adapter nor a new §12 responsibility.

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
is left in the adapter is handing admitted programs or durable snapshot bytes to
the owner-declared machine-factory port and driving the admitted set once through
the owner's common transition driver. It never imports `replay_vm_adapter.py`;
the two are sibling adapters. `replay_store.py`
was briefly declared an owner, which concealed a cycle — it imports the replay
contracts, and the binding factory imported `FileReplayStore` back.

The cycle was first inverted through a registration slot inside the owner, which
the adapter filled on import. That was a first-writer hole: anything registering a
class before `replay_store` was imported became the production store for the
process, and the real one was then refused as a forgery. The slot is gone. The
owner asks only what it can ask without concrete types — that the history and
machine factory satisfy its ports and are bound into the immutable production
configuration. The composition root constructs that binding with
`CognitiveVMReplayMachineFactory`, then checks both its exact type and
`REPLAY_MACHINE_ADAPTER_ID_V1`; it also asserts the exact durable history through
`replay_store.require_production_replay_store`. The root legitimately imports all
three adapters. The architecture tripwire is left at full
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

The executor builds its machines from that manifest through the exact factory
sealed into the production binding rather than accepting them, so there is no
moment at which a caller holds the object a verdict will be read off. Build and
restore receive a sealed execution context derived from the admitted run,
attempt, repository revision, environment and policy. A continuation's starting
state is durable for the same reason and is
compared with the terminal state its predecessor recorded; before this the caller
brought the machine, and after a restart that state had to come from outside the
system entirely.

### 9.8 OD-10/V1-A — architectural addendum

Recorded once, after the code reached the map it describes. It adds no normative
rule and changes nothing in §9.1 through §9.4: the capability profile, the
activity schema, the side-effect vocabulary and who decides are exactly as
ratified. What it settles is a question §9.5 answered only for the shape the code
had at ratification, and which the decomposition since then made too small.

**The logical owner is `synapse.experiments.gold.replay`.** That owner may be a
module or a package. Where it is a package, its internal division — public
contracts, declared ports, governance rules, orchestration — is an arrangement
inside one owner and not a set of new §12 owners. §9.5's table names files
because files were what existed; what it means is the owner and its adapters, and
a split of the owner across several files under one name does not multiply the
responsibility.

**The concrete adapters are three:** the CognitiveVM integration that runs a
machine, the raw execution that drives an admitted set and reports what happened,
and the durable Stage 9 history. Each depends on the owner and is never depended
on by it, and none depends on another adapter of the same owner.

**One production composition root binds the owner to those exact adapters.** It is
a third category, and it needs to be, because the star topology deliberately
permits exactly one party to touch every side. An owner cannot name the exact
store or the exact machine without importing its own adapter; an adapter that
assembled a run would be choosing what the run is pointed at. The root decides no
rule — it calls the owner's rules by name and settles only the order in which
they are asked — and nothing inside the package may import it, which is what
keeps the permission it holds from leaking to whatever reaches through it.

Three things are not trust boundaries and are not treated as such anywhere in the
Stage 9 code: a `Protocol` with `runtime_checkable`, which checks attribute
presence and not signatures; an `__all__` entry or a leading underscore, which
are conventions a caller may ignore; and a frozen dataclass, which prevents
ordinary assignment and not `object.__setattr__`. What carries trust is a factory
seal checked by the party that minted it, an identity re-derived from canonical
bytes, and a record resolved out of a durable store.

### 9.7 What ratification does not do

Freezing OD-10 clears none of the audit's findings, certifies no pull request,
establishes no `FULL`, substitutes for no oracle, and changes neither the single
canonical entry point nor the protected core. It removes one specific obstacle:
§41 forbids dependent code against an unfrozen decision, and Stage 9 is dependent
code. Everything else that was open is still open.

### 9.9 OD-10/V1-E1 — execution conformance erratum

E1 records an execution-level correction to V1. It exists because a table that
names an opcode is not evidence about what a real occurrence consumes or sends
to the host. The original V1 text remains above as the decision history; this
section is the governing executable interpretation. E1 has its own profile ID,
profile digest and structural-history schema, so evidence created under the
original partition cannot be accepted as E1 evidence through a compatibility
alias or a best-effort conversion.

Its executable profile ID is named `REPLAY_CAPABILITY_PROFILE_V1_E1` and is
`synapse.stage4.gold.replay-capability-profile-e1/v1`; the V1 symbol is not an
alias for it.

E1 also has distinct, non-aliased identities for every protocol whose bytes or
observable machine semantics changed:

- `ACTIVITY_RESULT_CODEC_V1_E1` is
  `synapse.stage4.gold.activity-result-codec-e1/v1`;
- `REPLAY_MACHINE_ADAPTER_ID_V1_E1` is
  `synapse.stage4.gold.cognitive-vm-replay-adapter-e1/v1`;
- `SchemaVersion.REPLAY_VM_SNAPSHOT_V1_E1` is
  `synapse.stage4.gold.replay-vm-snapshot-e1/v1`;
- the embedded adapter envelope is
  `synapse.stage4.gold.replay-vm-adapter-snapshot-e1/v1`.

The corresponding V1 names remain historical identities, not compatibility
aliases. A V1 record or snapshot is refused as unknown/incompatible; no decoder
reinterprets it as E1 evidence.

#### 9.9.1 Four execution classes and cardinality

Every real opcode occurrence belongs to exactly one of four classes. An unknown
opcode is a typed refusal, not an implicit host dispatch.

1. **`admissible`.** The occurrence consumes zero recorded activities and
   produces zero external effects. Its successor is derived entirely from the
   admitted program and canonical VM state. `PROMPT_BUILD` belongs here: the
   existing [LLM/Prompt CVM Bridge RFC](../RFC-LLM-PROMPT-CVM-BRIDGE.md#21-prompt_build)
   defines it as pure deterministic envelope construction with no pause, host
   call or history event. E1 therefore corrects the earlier V1 Category B
   listing; it does not invent a replay-only implementation of the opcode.

2. **`recorded_only`.** A completed effect-bearing occurrence consumes exactly
   one recorded activity of its frozen `ActivityKind`, or it is refused before
   an effect is possible. Resolution happens before the CVM transition whenever
   an injection primitive is synchronous. A missing primitive is
   `INJECTION_PRIMITIVE_MISSING` before `CognitiveVM.step()`, not permission to
   run and inspect the damage afterwards. A second activity attempt from the
   same occurrence is `ACTIVITY_CARDINALITY_MISMATCH`, including when protected
   core code catches the exception raised at the host boundary.

3. **`dispatch_guarded`.** `CALL` and `CALL_METHOD` are classified from the
   actual target before dispatch. A compiled `FunctionObject` transition, and a
   VM-local mapping-member transition, consume zero activities. A governed host
   fallback consumes exactly one `HOST_DISPATCH` and requires
   `capability.host`. An ordinary Python callable, descriptor-driven lookup,
   opaque target or target that cannot be classified without executing user
   code is refused before dispatch.

4. **`recorded_structural_effect`.** The eight explicit structural opcodes and
   `RETURN` produce canonical structural commands, not activities. They consume
   zero activity records, do not advance `ActivityPosition.sequence`, and never
   invoke a live host. Capture appends them to a separate ordered,
   content-addressed structural history; replay exact-matches that history
   before allowing the CVM transition.

`HOST_STATUS` remains service traffic outside all recorded activity
cardinality. Its deterministic event identity comes from the sealed execution
context, and handling it neither consults the activity ledger nor changes its
sequence. For activity traffic, the next position is committed only after exact
resolution succeeds, so a miss cannot shift later positions.

This four-way partition is included in `capability_profile_digest()`. A change
to any class is therefore a profile change, not a compatible implementation
detail.

Deterministic adapter-conformance refusals are replay evidence, not
infrastructure faults, and therefore never become `INFRA_ERROR`:
`STRUCTURAL_HISTORY_MISMATCH` maps to `TRANSITION_MISMATCH`, activity-cardinality
failure maps to `ACTIVITY_HISTORY_MISMATCH`, recorded-result decode/substitution
failure maps to `ACTIVITY_SUBSTITUTED`, and a missing injection primitive or
ungoverned dispatch maps to `FORBIDDEN_HOST_CALL`. Every one of these outcomes
is `REPLAY_FAILED`.

#### 9.9.2 Pending LLM execution

`LLM_REQUEST` is one logical recorded occurrence implemented as two machine
phases:

```text
physical LLM_REQUEST step
  -> exact pending envelope (call_id, executed_ip, arguments, event identity)
  -> adapter-owned pending LLM_RESUME
  -> resolve exactly one recorded LLM_CALL
  -> canonical result decode
  -> resume_host_call(call_id, result) exactly once
  -> pending state cleared
```

The first physical step creates the pending envelope and performs no live
producer call. The pending completion is the one and only activity consumption
for that logical occurrence. A snapshot restored between the two phases follows
the same path and cannot inject the result twice. A physical `LLM_RESUME`
instruction encountered without a validated pending `LLM_REQUEST` has no safe
injection subject and is refused before its CVM step.

Creation and completion are each one adapter transaction over VM state. The
adapter checks the exact pending or resumed successor before returning: call
identity and `executed_ip`, single stack injection, unchanged unrelated state,
cleared pending state and a canonical successor transition. A failed successor
restores the pre-phase VM snapshot while preserving an activity that the sealed
channel already resolved; retry therefore cannot disguise a consumed record as
unused evidence.

#### 9.9.3 Message execution

`MSG_SEND` resolves exactly one `MESSAGE_SEND` occurrence. The CVM route name
`CALL_HOST` is transport metadata; it must not reclassify the occurrence as
`HOST_DISPATCH`. The outbound mailbox changes only after the recorded send
acknowledgement succeeds, so a missing/substituted record cannot leave an
unrecorded send behind.

`MSG_RECEIVE` with a queued message resolves exactly one `MESSAGE_RECEIVE`
before consuming or binding that message. With an empty mailbox, its physical
step creates an exact pending receive envelope; the adapter then resolves one
recorded `MESSAGE_RECEIVE`, resumes with that recorded message once, clears the
pending state and continues. A pause without the recorded occurrence and resume
protocol is not a completed receive. Snapshot/restore cannot convert one
receive into two injections. The `SEND`/`RECEIVE` aliases use the same
`MESSAGE_SEND`/`MESSAGE_RECEIVE` kinds and cardinality.

#### 9.9.4 Structural command history

The recorded structural class is exactly:

- `CONTEXT_ENTER`, `CONTEXT_EXIT`;
- `ACTOR_ENTER`, `ACTOR_EXIT`;
- `POLICY_ENTER`, `POLICY_EXIT`;
- `POLICY_RULE_ENTER`, `POLICY_RULE_EXIT`;
- `RETURN` when it unwinds structural scopes.

Each of the eight explicit opcodes resolves one canonical command before the
step. A top-level `RETURN`, or a framed return with no dangling structural
scope, resolves an empty batch. A return that crosses structural scopes resolves
one atomic batch of all required exits. The order is contexts inner-to-outer,
then actors inner-to-outer, then policies and policy rules inner-to-outer. The
whole batch is compared before the transition; accepting a prefix and failing
after the VM has partly unwound is forbidden.

Every structural record binds the E1 profile identity and digest, program hash,
instruction pointer, pre-step frame depth and transition hash, opcode, exact
`SYS_*` symbol, scope kind and label, metadata digest, direction, unwind reason
and host ABI version. `occurrence_index` and `occurrence_size` make one unwind
batch indivisible. The pre-transition hash also distinguishes repeated visits to
the same `RETURN` instruction and lets an empty actual batch refuse a non-empty
record for that exact occurrence before the VM step without consuming a later
occurrence. Records have a contiguous structural sequence and their own event
digests. The canonical transport is exact-round-trip checked and stored by
hash-bound reference. Extra, missing, reordered, substituted or
profile-mismatched commands are `STRUCTURAL_HISTORY_MISMATCH`; none is translated
into an activity or a live callback.

An E1 VM snapshot embeds the canonical structural prefix already resolved at
that machine state. Restore validates that the prefix belongs to the same E1
profile and is an exact prefix of the manifest-bound expected history before a
step is possible. Capture continuation preserves the same prefix even when no
expected tail is supplied. The CVM state remains limited to 8 MiB, structural
history remains limited to 8 MiB, and the outer E1 snapshot has a coordinated
combined ceiling; a valid component is not made unsnapshotable merely because
the other component is present.

#### 9.9.5 Capability mapping and fail-closed declaration

The E1 profile freezes the complete activity-to-capability map:

| `ActivityKind` | capability ID |
| --- | --- |
| `LLM_CALL` | `capability.llm` |
| `MEMORY_READ` | `capability.memory.read` |
| `MEMORY_WRITE` | `capability.memory.write` |
| `AFFECT_EVENT`, `AFFECT_READ` | `capability.affect` |
| `METRICS_EMIT` | `capability.metrics` |
| `HOST_DISPATCH` | `capability.host` |
| `SELF_MODIFICATION` | `capability.self.modify` |
| `HABIT_SUGGESTION` | `capability.habit.suggest` |
| `THRESHOLD_EVALUATION` | `capability.affect.threshold.evaluate` |
| `MESSAGE_SEND` | `capability.message.send` |
| `MESSAGE_RECEIVE` | `capability.message.receive` |

The mapping itself is canonical input to `capability_profile_digest()`. An
activity kind without a mapping is `CAPABILITY_NOT_CLASSIFIED`, never a raw
lookup error and never an empty requirement.

Capability derivation scans the admitted artifact's real instructions and
accounts for every reachable host route. In particular both dispatch-guarded
opcodes declare `capability.host` because that is their maximum reachable route,
even if one observed execution stayed internal. The sorted declared set must
equal the sorted derived set. Under-declaration and over-declaration therefore
fail closed by the same exact-equality rule; a superset is not accepted as
"more secure" because it would make the admitted authority description false.

#### 9.9.6 Primary-source basis

These sources are engineering precedent, not authority over this repository;
the OD-10/V1-E1 rules above remain the repository owner's decision.

- Temporal's official
  [determinism constraints](https://docs.temporal.io/workflow-definition#deterministic-constraints)
  require the same command sequence for the same input and compare emitted
  commands with the existing Event History during replay. The corresponding
  [documentation source](https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/workflow/workflow-definition.mdx#deterministic-constraints)
  is public. This is the precedent for checking actual occurrence order and
  history position instead of treating an opcode table as execution evidence.
- The WebAssembly project states in the
  [WASI design principles](https://github.com/WebAssembly/WASI/blob/main/docs/DesignPrinciples.md#capability-based-security)
  that external access is capability-provided and that WASI has no ambient
  authorities. E1 applies the same fail-closed principle locally: an external
  route must have an explicit derived capability; an unknown mapping grants
  nothing.
- Aumayr et al.,
  [*Efficient and Deterministic Record & Replay for Actor Languages*](https://arxiv.org/abs/1805.06267),
  record high-level nondeterministic actor events so replay can reproduce the
  recorded execution deterministically. That supports recording message
  occurrences at the actor abstraction boundary rather than reissuing a live
  producer call or inferring them from a lower-level route name.
