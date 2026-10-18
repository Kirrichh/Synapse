# Synapse UI bridge specification

**Version 1.0 — English edition of the supplied revision 0.7.**

Status: implementation contract; not an assertion that the bridge exists.
Requirement identifiers, examples, limitations, and open conditions are retained.
This is a documentation version, not a negotiated wire-protocol version.
Appendix D is maintained in [the complete code map](UI_CODE_MAP_V1.md).
See [provenance](SOURCE_MANIFEST.md), [working documents](README.md), and
[implementation status](IMPLEMENTATION_STATUS.md).

## 1. Stack

The stack is fixed: Tauri 2, React/TypeScript, Monaco for editing and diffs,
and the Python core as a bundled sidecar. Use the canonical NR-02 entry point.
Shell/core communication uses pipes with the framing in section 4; UI delivery
uses Tauri Channels, not a global event bus. Contracts use JSON Schema draft
2020-12. Zod is outside the contract boundary.

## 2. Architectural foundations

### 2.1. Principles

**ARCH-1.** Presentation is separate from execution. Each domain operation has
an owner. The renderer has no authority of its own; every effect passes through
the core and the appropriate domain owner.

**ARCH-2.** The owner is the sole authority on execution. A synchronized UI copy
of state cannot authorize an operation; see section 10 and APR-10.

**ARCH-3.** Enter Gold only through the `composition_roots` declared in
`governance/stage4_ownership_v2.json`. The invariant is enforced by
`test_stage4_is_entered_only_through_a_declared_composition_root` in
`tests/test_stage4_gold_canonical_entrypoint.py`. A domain owner and an approved
composition root have different roles.

**ARCH-4.** The declared Gold roots, relative to `synapse/experiments/gold/`, are:

```text
knowledge_environment.py
library_composition.py
replay_composition.py
run_attempt_world.py
runner_composition.py
stage10_composition.py
source_ingestion.py
```

The governance manifest is authoritative. This list does not permit competing
entry points or owner bypasses. An approved root does not imply that the required
UI command already exists. Throughout the map distinguish (a) an existing
function, (b) an approved route to it, and (c) a proposed external operation.
A module's existence does not complete its map entry.

### 2.2. Domain owners, not composition roots

**ARCH-5.** List confirmed domain owners separately from roots. The machine
adapter cannot enter `project_court` or `runner/budgets` as if they were roots;
precise names determine permitted dependency directions.

| Area | Owner |
|---|---|
| Task execution | `runner_composition.execute_gold_project_run()` / GoldRunController |
| Approvals | `stage10_composition.execute_approval_action()` → `grant_approval()` / `revoke_approval()` |
| Agent invocation | AgentExecutionPort and InvocationStore: `claim()` / `cancel()` / `restore()` |
| Evidential events | `open_event_stream()` / `EventStream.page()` |
| C1 evidence | `C1EvidenceContext.read()` / `read_c1_verification_evidence()` |
| Memory and habits | `project_court`, `project_memory_store`, `learned_habit_profile`, `learned_habit_lifecycle`; memory policy: `MemoryOwner` (`rebind`, `bound_digest`, `register_session`), `MemoryFactory.reassess()`, `PARAMETERS`, `policy_identity()` in `policy.py`, and `read_memory_configuration()` in `configuration.py` |
| Budgets | `runner/budgets` |

### 2.3. Recovery domains

**ARCH-6.** Keep three failure domains distinct: renderer reload (view state;
the Rust shell survives), Rust shell death (process-group control, section 9),
and core death (reconciliation by idempotent keys, section 5). One mechanism
does not solve all three.

## 3. Bootstrap

**BOOT-1.** The canonical NR-02 entry is `python -m synapse` →
`synapse.__main__` → `synapse.cli.main()`. Machine mode is a subcommand of that
entry. Competing user-facing CLI, daemon, or sidecar entries into Gold are
forbidden. Internal adapter children such as `synapse/agents/native_worker.py`
remain controlled by the existing runtime; they are not UI launch commands
for Gold.

**BOOT-2.** The shell starts the core exclusively through that entry. Do not
create or retain alternative launch paths as fallbacks.

**BOOT-3.** Bootstrap states are `native` → connecting → `ready` or `fail`.
Connecting means the BOOT-7 handshake, not merely a responding transport.
Check that the bundled execution component exists, its version matches
(BOOT-9), and its canonical entry answers the machine check.

**BOOT-4.** Show failure honestly: error code, core stderr diagnostics, and
unsupported-environment information. Simulated readiness and hidden degradation
are forbidden. In particular, an unsupported termination environment (CRASH-14)
fails explicitly instead of replacing process-group control with killing one PID.

**BOOT-5.** Drain core stderr continuously throughout its lifetime into the
shell diagnostic log, separately from framed stdout. Apply secret exclusion and
volume limits before logging or display (SEC-9); errors must not leak sensitive
data.

**BOOT-6.** The prototype illustrates bootstrap presentation (`native` →
`ready`/`fail`) and honesty markers such as `demonstration:true`.
Its `connect-src 'none'` CSP illustrates an isolated, network-free demo, not the
production application's CSP (SEC-5). It does not define protocol semantics
(MODE-6).

### 3.1. Connection handshake

**BOOT-7.** Before domain commands, exchange protocol messages in this order:
(1) component identity and supported version ranges; (2) selection of a common
machine profile; (3) readiness confirmation; (4) permitted commands.
Names such as `hello`, `select`, and `ready` are implementation details; message
content and state consequences are normative. COMP-2 negotiation happens here.
Exact message schemas and profile contents remain open in section 16.

| Message | Direction | Contents | Resulting state |
|---|---|---|---|
| Announcement (`hello`) | Both ways | Component identity and supported protocol version ranges | No common profile; mutations forbidden |
| Selection (`select`) | Shell → core | Common profile: envelope version, frame limit, encoding | Profile proposed; commands still forbidden |
| Readiness (`ready`) | Core → shell | Confirmation of profile and own readiness | Profile-permitted command classes allowed |

**BOOT-8.** Reject mutations explicitly before a compatible profile is
confirmed; do not execute or queue them for later. A connected pipe or responsive
channel does not mean every domain has recovered or permits every operation.
Domain readiness is separately confirmed by its owner (SYN-5, section 10), not
by the screen. Incompatible versions produce `fail`, not best-effort operation.

**BOOT-9.** Finding Python means locating the execution component bundled with
the application. Do not require users to install Python or launch the first
`python` on the system PATH. Version and canonical-entry checks apply to the
bundled component.

**BOOT-10.** Before negotiation, use a finite bootstrap profile with known
framing, encoding, allowed messages, and `bootstrap_max_frame_bytes`. Ship it
with the application and enforce it before reading the first payload. The
negotiated limit cannot exceed either peer's supported limit. Negotiation itself
is bounded. Values belong to release configuration, not renderer input.

## 4. Framing and transport

**TR-1.** Core stdout is a sequence of frames: a big-endian u32 payload length
in bytes followed by UTF-8 JSON.

**TR-2.** Do not alter significant frame bytes, mix diagnostics into stdout,
or perform unspecified output transformations. Bounded byte buffers,
incremental parsing, and explicitly bounded queues are required and do not
distort content or conceal state. Use tauri-plugin-shell `set_raw_out(true)`
to preserve bytes without rewriting or reformatting core output.

**TR-3.** Wait for the remainder of an incomplete frame; parse multiple frames
in one read in order. Stream termination is an explicit transport event, not
silence.

**TR-4.** UI → core: Tauri `invoke` → Rust shell → core stdin, with the same
framing.

**TR-5.** Core → UI: Rust reads stdout and publishes through Tauri **Channels**.
Create a channel per subscription/session. A global event bus is forbidden.

**TR-6.** Bound the send queue. Overflow produces an explicit transport state
and UI signal; never silently discard frames.

**TR-7.** The adapter may drop Live frames under load and mark a gap in the
next delivery. StateSync and Evidential follow sections 7 and 10: no arbitrary
loss, although intermediate updates can be detectably lost (SYN-19, DEL-3).

**TR-8.** Rust owns trusted window context, Channels, client sessions, durable
pre-send records, and the byte connection. The Python machine handler owns the
server side of subscriptions and routes requests through approved roots; it
does not become a domain scheduler. Domain owners own snapshot bases, data
identities, revisions, authority, and result truth. Neither transport layer
invents a consistent cut that the owner cannot provide.

**TR-9.** Normal core shutdown follows the protocol (CRASH-15), not process
killing.

**TR-10.** Check the header's declared length against the negotiated frame
limit **before allocating payload memory**. Oversize is an explicit transport
event; do not accept the frame or grow its buffer. The value is a profile
parameter, but preallocation validation is a mandatory transport property:
u32 plus JSON alone does not prevent enormous advertised payloads.

**TR-11.** In the first profile, invalid UTF-8, malformed JSON, invalid length,
oversize, or mid-frame EOF terminate the transport session with an explicit
reason. Do not scan remaining bytes for a plausible next header: length alone
does not establish a recovered boundary. Send no further mutations until
section 9 recovery and a new BOOT-7 handshake. A schema error in fully parsed,
valid JSON rejects that message without execution; the otherwise valid framed
connection need not close. An unrecognized result or mutation is not proof of
nonexecution: reconcile by command identity.

**TR-12.** A slow renderer must not block domain execution on channel writes.
Bound request, result/control, and projection queues separately. Explicitly
reject overloaded mutations before acceptance. An accepted command survives
queue loss: its owner records establish results and effects. Lost StateSync
coverage requires a snapshot; reread Evidential; mark dropped Live with a gap.
Heartbeat, overload refusal, and shutdown responses cannot wait indefinitely
behind Live traffic. Capacities and intervals are release/acceptance parameters,
not unbounded buffers.

**TR-13.** Two protocols with four-byte headers are still distinct protocols.
`synapse/agents/stdio_adapter.py` and `native_worker.py` handle agent
request/result exchanges; the machine bridge handles long-lived command,
event, and snapshot sessions. Their schema IDs, limits, recovery, and lifecycles
cannot substitute for each other. The first UI/core profile opens no network
port. The existing per-invocation `ModelBroker` is a different connection with
its own contract.

## 5. Command lifecycle

### 5.1. Identity

**CMD-1.** Rust, the trusted client layer, generates `command_id` before the
first mutation send. The renderer does not invent it. Protocol identity must
distinguish retrying an action after losing its response (same command ID)
from a deliberate new action with identical parameters (new command ID).
The intent mechanism in CMD-20 supplies this distinction; parameters cannot.

**CMD-2.** The owner computes `params_hash` on acceptance, not the client.
Canonicalize JSON using JCS, RFC 8785.

**CMD-3.** Equal parameters do not establish equal intent: a user can knowingly
launch the same task twice. `command_id` is identity; the parameter hash is
part of binding. Use CMD-20 to distinguish retries from new intent.

**CMD-20.** At the React/Rust boundary, the shell issues an opaque `intent_id`
when the user action begins, before submitting a mutation. The renderer returns
it on submission and after response loss. The same intent resolves to the same
command ID and saved result; deliberate new intent gets new IDs even with equal
parameters. Before sending, Rust durably binds intent, scope, command content,
and command ID and restores that binding after restart. The renderer returns an
issued reference, not self-appointed authority. An equivalent mechanism is
allowed only if retries cannot duplicate execution and deliberate new actions
cannot be mistaken for retries. The distinction is normative, not its carrier.

### 5.2. Durable pre-send record

**CMD-4.** Before first sending a mutation, persist `intent_id`, `command_id`,
scope, recipient, and exact command content or a protected reference in
application data outside agent work directories.

**CMD-5.** This record means "command A's outcome is not yet established."
It is neither the operation result, an evidential log, nor a second execution
authority.

**CMD-6.** Store secrets and sensitive parameters only in the specified
protected representation, never in a plaintext pre-send log.

**CMD-7.** Restore the original ID after shell restart. The durable
intent/command binding prevents the sequence A executed → response lost →
application forgets A → newly generated B executes the same intent again.

**CMD-8.** Lost intent identity requires reconciliation using a saved command
ID or another durable reference to that specific intent. Parameter fingerprints
may enumerate candidates only; they never prove identity, create a claim, or
cause effects. Ambiguity prevents automatic relaunch. Expose unresolved identity
explicitly instead of hiding it behind a new ID.

**CMD-9.** `InvocationStore.claim()` is not a hash-based lost-command lookup.
It looks up the supplied invocation ID, creates CLAIMED if absent, compares
binding, returns a saved terminal result, or reports EXECUTION_STATE_UNKNOWN
for an unfinished claim.

### 5.3. Acceptance

**CMD-10.** `accepted` means durable acceptance by the domain owner under its
contract. Invocation claim serves agent calls. Approvals, messages, policy
changes, and other operations require their own authoritative acceptance
records. InvocationStore guarantees do not automatically cover the application.

**CMD-11.** Queue insertion or transport receipt is not domain acceptance.
If the durable write may have happened but acknowledgement is missing, the
outcome is uncertain, not guaranteed rejection or success.

### 5.4. Retries and conflicts

**CMD-12.** Redelivering a completed command returns the saved outcome with
`replayed:true` and no new effect. An accepted unfinished command returns its
confirmed current state without a second executor; otherwise show uncertainty.
`replayed` describes retrieval, not success. Command result, domain effect,
and system acceptance of that result are different facts. Never rename
EXECUTION_STATE_UNKNOWN to `failed`.

**CMD-13.** Reusing a command ID with different binding/content/hash is
BINDING_CONFLICT. Neither execute new content under the old ID nor blindly
return the old response. Stripe's documented idempotency layer compares
parameters and rejects mismatches to prevent accidental misuse.

**CMD-14.** Recovery branches: an owner's terminal result for A is replayed;
an unresolved claim yields EXECUTION_STATE_UNKNOWN and owner-defined recovery;
if the owner never saw A, resend that same A. Idempotent claim and binding
validation close the race.

### 5.5. Documented precedent

Stripe's idempotent-request API generates the key before the request, saves the
first execution result including HTTP 500 errors, returns it on retries,
retains keys for at least 24 hours, and rejects different parameters under the
same key. This supports CMD-1 through CMD-14. It does not supply the React/Rust
intent mechanism: CMD-20 is this bridge's contract. Stripe permits a new request
after key removal; this bridge must not revive an expired old command. Result
TTL does not turn an old command into new intent (open condition 10).

### 5.6. Memory policy acceptance commands

**CMD-15.** Policy reads and validation require response correlation, schema,
authority, and project checks; reading alone creates no memory mutation claim.
Preview has its own calculation identity, basis, and isolation limits (section
11), but is not policy acceptance. `memory.policy.accept` and
`memory.storage.accept` require the full durable mutation lifecycle, including
CMD-4 and CMD-10. The corresponding owner defines the acceptance record;
InvocationStore is not automatically assigned that role.

**CMD-16.** Existing `_reassessed()` in `consolidation.py` is conditional reuse,
not command identity. It checks `bound_digest()` against `configuration_sha256`
before finding a suitable saved reassessment; it does not match command IDs.
It handles repeating a configuration with an unchanged binding, not the
stronger external command guarantee.

**CMD-17.** If B is accepted, its reply is lost, C is then accepted, and B is
retried late, return B's **historical saved result** without rebinding C to B.
Display current policy separately from an old command result. Intentionally
moving from the current base to B requires new intent and a new command ID.
A stale-base intent is rejected with the current configuration; this is distinct
from BINDING_CONFLICT. Kubernetes resourceVersion/409 Conflict is a precedent
for rejecting lost updates from stale clients.

**CMD-18.** Policy acceptance is multistep. The existing
`synapse/memory_consolidation/court/consolidation.py::reassess()` path includes
conditional republication, calculation, `put_report`, `append_decision`,
`put_state_snapshot`, `put_boundary`, `retention_pass`, and `MemoryOwner.rebind`.
These are effect-inspection points, not instructions to reorder code to match
prose. Test recovery before and after every actual mutation, including
republication, every w1–w5 gap between report/decision/snapshot/boundary/
retention/rebind, after rebind, and on response loss. Early `apply_acts()` and
late `retention_pass()` are distinct. A report alone does not confirm accepted
policy. Reconcile unknown outcomes with the owner; the entire path forbids a
second independent effect.

**CMD-19.** Error or lost response during acceptance does not prove that nothing
happened. Documented etcd issue #16659 illustrates API versus apply quota checks:
an API-layer quota refusal prevents execution, but an apply-layer quota failure
after the API check may accompany a committed transaction. Establish outcome by
owner reconciliation, not by inferring nonexecution from an error (CMD-11).

**CMD-21.** Mutation binding includes method, semantic version, workspace,
target owner/object, expected base revision, and significant parameters.
The core derives trusted operator/scope membership from context, not arbitrary
client fields. An intent cannot change project, recipient, method, or content;
reject mismatches before effects. Represent a secret by a protected reference
to a specific secret version, never plaintext or a diagnostic hash of the secret.

**CMD-22.** Proposed `command.status` is a read by command ID, workspace, and
owner. It creates nothing and never calls `claim()`. Distinguish saved terminal
result, confirmed unfinished execution, unknown outcome, and no record within
the specified search scope. Absence permits only redelivery of the same command
through owner acceptance, not new intent. If the renderer loses its intent ID,
first enumerate shell exchanges it is authorized to recover. Text/parameter
matching cannot trigger automatic retries.

## 6. Approvals: grant, refusal, revocation, and single use

### 6.1. Three distinct semantics

**APR-1.** Grant, refuse, and revoke have different preconditions and results.
One discriminated command or several commands are allowed; semantics, not method
count, are normative. Draft form: `approval.respond {action: grant | refuse | revoke, …}`.

| Property | Grant | Refuse | Revoke |
|---|---|---|---|
| Meaning | User permits the proposal | User declines; no grant need exist | An existing grant ceases to authorize future action |
| Precondition | Approval request | Approval request, not a grant | Existing grant; `revoke_approval()` requires one and therefore cannot implement refusal |
| Result | `decision=granted`, full grant with request, issued_at, expires_at, grant_sha256 | `decision=refused`, **no grant_ref** | `decision=revoked`, grant_sha256, not a full issuance object |
| Existing code | `execute_approval_action()` → APPROVED | New authority-owner semantics required | `revoke_approval()` → REVOKED |

**APR-2.** Refusal and revocation are not interchangeable. Revoking a nonexistent
grant cannot implement refusal because its precondition is unsatisfied.

### 6.2. Approval is distinct from application

**APR-3.** An approval command ends with a decision. Grant references belong to
grant issuance; refusal and revocation have their own APR-1 results. The executor
separately confirms the domain effect through an application event referencing
the grant. Authorization of the first side effect by the core owner connects
decision and effect.

**APR-4.** Redelivery returns the saved decision with `replayed:true` (CMD-12).
Consent does not prove application, and application is not an approval response.

### 6.3. Single-use approval (`allow_once`)

**APR-5.** A single-use grant permits **one execution** of a specific approved
domain operation or explicitly described batch. It is not limited to one system
call, one file write, or one protocol message.

**APR-6.** Every action within a batch must belong to its approved contract.
A second independent execution under the grant is forbidden, regardless of
command ID or operation. Recovering the same execution is not new authorization
and cannot repeat completed effects.

| Situation | Required behavior |
|---|---|
| One approved batch changes two files | Both changes are allowed if included in the batch |
| Two independent executions compete for one grant | Only one acquires execution authority |
| A running operation's command is redelivered | Return its state; do not create another execution |
| Execution stops after the first file | Recover that operation under owner rules; do not blindly restart the batch |
| The proposal changes | Do not automatically carry consent to the new proposal |

**APR-7.** The unit of consent is the identity of the displayed, approved
proposal, not an operation type or arbitrary command. The demo's
`kind:patchRevision` ↔ seen_proposal_hash illustrates that identity.

**APR-8.** Before the first external effect, the authority owner durably and
exclusively binds the once-grant to a particular execution. Reject competitors.
Both current-validity checking (`authorize_first_side_effect()`) and this
durable binding are necessary; neither alone is sufficient.

**APR-9.** Failure does not erase or automatically release the binding.
Reconcile an uncertain effect in that operation. An unresponsive executor does
not permit assigning the grant to a new executor.

**APR-10.** The confirmed code boundary: `authorize_first_side_effect()` checks
the accepted plan against current repository revision, snapshot, policy,
admission, and compatibility; rechecks human approval when required
(`current=True`); and produces SideEffectAuthorization. It does not durably
consume a once-grant. Single use requires new authority-owner semantics, not a
label over the existing timed grant. Identify the durable-record owner from
the actual execution path, not a function name.

**APR-11.** Existing grants contain request, issue time, and expiry.
`review_request()` can find a current grant and produce another review receipt.
The current core contract does not consume consent after one effect or promise
single use.

**APR-12.** Keep four explicit links: displayed proposal identity (contract);
identity recorded in grant/receipt (contract); revalidation before effect
(confirmed code, APR-10); executor confirmation referencing the grant (contract).
`plan_revalidation.py` establishes an important link, not the entire chain.

### 6.4. Documented precedent

RFC 6749 section 4.1.2 requires an OAuth client not to reuse an authorization
code and the server to deny reuse. Revoking tokens previously issued under that
code is a SHOULD, not the MUST that applies to denying reuse. This documents a
single-use authority object with a defined consumption unit and mandatory
rejection, supporting APR-5 through APR-9. Single use belongs to the owner's
contract, not a count of technical operations.

## 7. State synchronization (StateSync)

### 7.1. Identities

**SYN-1.** Distinguish these identities and view parameters:

| Concept | Components |
|---|---|
| Data identity | Workspace, state domain, projection generation |
| Delivery identity | Session, subscription, synchronization attempt (epoch) |
| View parameters | Filter and permitted visibility fixed when subscribing |

The same domain name can exist in different projects. Workspace identity prevents
applying one project's reply to another. Project/filter changes never authorize
applying an old reply to a new subscription.

**SYN-2.** Each domain is an independent projection with its own snapshot/update
contract, not necessarily its own log. Use existing owner records; rebuildable
projections are not a second execution history. The domain registry remains open.

### 7.2. Positions and boundaries

**SYN-3.** A position has an order within one data identity. Use an ordinal for
`>`/`≥` comparisons and a separate opaque recovery cursor for resumption after
restart. An opaque token without comparison rules cannot serve both purposes.

**SYN-4.** `deliver_from` (P0) is exclusive: deliver changes strictly after it.
The producer confirms P0 in `subscription.confirm`, including a quiet project's
current durable position. The first incidentally delivered record is not the
opening boundary; a filtered record need not immediately follow the snapshot.

**SYN-5.** Snapshot Ps is inclusive: state as of Ps includes every effect at
positions ≤ Ps. Sequence: subscribe → confirm P0 → snapshot. Require Ps ≥ P0
in the same data identity; otherwise fail the attempt and open a new epoch.

**SYN-6.** etcd's revision-N read and explicit watch boundary demonstrate the
read-at-boundary/deliver-after-boundary pattern. etcd `start_revision` is
inclusive; this contract's P0 is exclusive and Ps inclusive. Establish the
boundary explicitly before delivery, never from the first event.

### 7.3. Applying updates

**SYN-7.** Apply only events with matching data identity, current delivery
identity, and confirmed coverage. Delivery can be out of order; application
cannot. Repeated delivery of an applied position is idempotent.

**SYN-8.** H is the domain's last fully applied position, initially Ps. Apply
updates with confirmation of coverage of the next range, then advance H only
after the entire range is applied. Apply a revision as a whole. Kafka's securely
stored committed recovery position is a precedent.

**SYN-9.** Repeating an accepted range does not change state. Contradictory
content under the same identity is an integrity error: stop and resynchronize.

### 7.4. Coverage

**SYN-10.** Each delivery confirms `(from, to]`, with SYN-3 ordering. Coverage,
not consecutive record numbers, establishes continuity. For `from ≤ H < to`,
skip positions ≤ H, apply the entire remainder, and set H = to. For `to ≤ H`,
do nothing. For `from > H`, wait for missing coverage in a bounded buffer;
unresolved gaps or overflow invoke SYN-15. Filtering may omit payload records,
but coverage covers the inspected range of the authorized source. Empty-range
confirmation cannot bypass an earlier undelivered record.

**SYN-11.** With reordered delivery, progress waits for unapplied-range
coverage or exposes the gap; it cannot jump over it.

### 7.5. Progress notifications

**SYN-12.** `state.progress {domain, identity, epoch, evaluated_up_to}` confirms
that the source was inspected through that position and no matching records
exist after the last delivered position through that boundary. Its guarantee
is an already delivered prefix, not merely that the server once scanned a log.
etcd documents this guarantee and load-dependent empty watch responses.

**SYN-13.** Required triggers are subscription confirmation and
`state.progress.request`; periodic notification is optional, with cadence open.
Proposed UI triggers are window refocus and snapshot application, cheaply
confirming freshness when no domain events occur.

### 7.6. Late replies versus current violations

**SYN-14.** Discard old-subscription/epoch messages without changing current
synchronization; record them in adapter diagnostics. Otherwise late responses
could repeatedly reset healthy subscriptions. Raft's stale-term handling is
a precedent for this distinction.

**SYN-15.** A data-identity or coverage violation in the current subscription
stops application and starts a new epoch for that domain. Replace its projection
with an authoritative snapshot. etcd cancels watches starting at compacted
revisions; retrying that same unavailable boundary is not recovery.

### 7.7. Filtering and deletion

**SYN-16.** Leaving the selected set requires an explicit tombstone or
subscription invalidation, including visibility-permission changes. Silence
is not deletion. Kafka's null-valued compaction tombstones, retained for
`delete.retention.ms`, demonstrate explicit removal delivery.

### 7.8. Composite snapshots and cross-domain references

**SYN-17.** Workspace snapshots are sets of `(domain, data identity, Ps, state)`
tuples. There is no global C or workspace-wide atomicity. Being "before another
domain's position" is meaningless and cannot justify discarding an event.

**SYN-18.** Do not claim global domain consistency. Task/proposal/approval links
carry explicit version references, including task and approval positions at
display/decision time. The core revalidates before effects
(`authorize_first_side_effect()`, `current=True`) regardless of screen state.

**SYN-19.** Intermediate StateSync loss is detectable through coverage gaps or
generation/epoch changes and repaired by authoritative replacement. Detectable
loss is compatible with the class; undetected loss is forbidden.

### 7.9. Protocol elements

| Message | Direction | Contents |
|---|---|---|
| `state.subscribe` | UI → core | Workspace, domains, filters, delivery identity |
| `subscription.confirm` | Core → UI | Data/delivery identities, confirmed filter/visibility, `deliver_from: P0` |
| `state.snapshot` | UI request / core reply | Data/delivery identities, request_id, `position: Ps`, state |
| `state.event` | Core → UI | Domain, identities, epoch, positioned payload entries, coverage from/to, typed updates/deletions or explicit gap |
| `state.progress` | Core → UI | Domain, identities, epoch, evaluated_up_to |
| `state.progress.request` | UI → core | Domains and delivery identity |

**SYN-20.** Rust/Python provide their TR-8 delivery responsibilities; owners
define snapshot bases and revisions. Snapshots carry project, domain,
generation, subscription, and attempt identity. Losing/changing any binding
prevents applying a reply to another view. After renderer reload, H alone is
insufficient: restore state and position as one verified pair or take a snapshot.

**SYN-21.** Provide `state.unsubscribe` and finite resource lifetimes. Closing
tabs, switching projects, and recreating webviews releases their channels and
buffers; repeated unsubscribe is safe. It does not cancel tasks, invocations,
or remote effects. Reconnect creates delivery identity, not domain execution.

**SYN-22.** Show process liveness separately from domain freshness. `core.ping`
does not prove tasks, approvals, or budgets fresh. Display last confirmed
position and synchronized/updating/stale/unavailable status per domain. Quiet
projects with valid progress are healthy. Expired deadlines mark data stale;
preserve local edits without automatically sending them as new commands.

## 8. Permission modes

### 8.1. Semantics

**MODE-1.** `auto`, `confirm`, and `partial`, their scope, and effective time
belong to core domain policy. RU/EN labels, icons, and colors are presentation.

**MODE-2.** Keep four states distinct:

| State | Meaning |
|---|---|
| `mode.requested` | Sent; policy may not yet have changed |
| `mode.accepted` | Owner recorded rule, scope, and version |
| `mode.effective` | Conditions for applying it to a particular execution hold |
| `mode.shown` | Confirmed state is displayed, beyond switch position |

### 8.2. Initial mode-change contract

**MODE-3.** Default changes affect new tasks. Running tasks retain launch-time
policy versions. Changing an existing task needs a separately supported core
procedure, not a React switch. That future extension is not promised initially.

**MODE-4.** Distinguish new-task defaults from the selected task's effective
rights. The switch shows requested mode; task status shows its fixed policy.

**MODE-5.** Specific grant revocation is a separate APR-1 operation, not a mode
change, and does not undo completed actions.

**MODE-6.** Demo HTML preserves appearance/interactions, not protocol semantics.
The approved specification and domain contracts define requirements and actions.

### 8.3. Vocabularies

**MODE-7.** Each vocabulary has one primary source. New protocol vocabularies
originate in JSON Schema draft 2020-12; generate TS types at build time. Existing
core vocabularies remain core-owned: derive schemas or verify exact agreement.
Different generation mechanisms are allowed, duplicate editable lists are not.

**MODE-8.** RU/EN localization keys off vocabulary values. The prototype shows
in-place switching without remounts or loss of drafts/focus: presentation,
not domain semantics.

### 8.4. Memory policy is separate

**MODE-9.** MODE-3 does not apply to memory policy. Memory acceptance reassesses
accumulated evidence (MEM-4) and changes future owner sessions (MEM-8). Memory,
agent confirmation, and model-call limits are separate settings; changing one
cannot expand another or weaken evidence requirements.

**MODE-10.** `PARAMETERS` and core configuration descriptions own memory
vocabulary. Derive the UI form without another editable structure. Decision
rules come from `DECISION_RULES`: `threshold`, `sprt`, `confidence_sequence`.
Their `t1_*`, `sprt.*`, and `cs.*` numeric controls use the same description.

## 9. Failure lifecycle

### 9.1. General rules

**CRASH-1.** Do not reconnect automatically to lost anonymous pipes or permit
a second writer in the same scope. Transport restart differs from recovery.

**CRASH-2.** Before admitting a new executor, establish that prior local
executors cannot continue effects in the resource scope. Otherwise deny new
mutations there. A mirror lock of the dead shell does not establish this.

**CRASH-3.** Opening a window, recovering, and starting effects are separate
rights. A lock held by the former shell is insufficient for the last.

**CRASH-4.** Assign cleanup in advance; never rely on dead-shell shutdown code.
Both supported profiles use operating-system cleanup mechanisms.

**CRASH-5.** Process control complements existing owner-defined store recovery.
`RunRecordRecovery.session()` exclusively recovers a specific run-record store;
it does not manage OS trees or globally lock the desktop app. Do not duplicate
its recovery algorithm or replace its owner.

**CRASH-6.** Stopping local descendants does not prove absence of remote effects.
External requests/services remain outside the group. Reconcile their operations
separately, using idempotent keys where supported remotely.

**CRASH-7.** Core death does not invalidate known terminal outcomes; retrieve
them through `InvocationStore.restore()`. Uncertainty is operation-specific.

**CRASH-8.** These mechanisms manage lifecycle, not arbitrary hostile processes.

### 9.2. Windows profile

**CRASH-9.** Use a separate Job Object for the core and managed local executors,
with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Closing the last handle terminates
associated processes, including child jobs in a nested hierarchy.

**CRASH-10.** No `JOB_OBJECT_LIMIT_BREAKAWAY_OK` or
`JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK`. Do not pass handles retaining group
lifetime to executors. Verify real adapter launch mechanisms: CreateProcess
children normally inherit membership, with Win32_Process.Create and breakaway
exceptions. Nested jobs are available from Windows 8 onward.

**CRASH-11.** Establish membership before domain work, for example suspended
creation → Job assignment → resume. Parenthood alone is insufficient.

### 9.3. Linux profile

**CRASH-12.** Launch Tauri as a systemd user service (`--user`), with Rust as
main process and the core/local executors in that unit's cgroup. Use
`ExitType=main`, `KillMode=control-group`, and no automatic application restart.
Main-process exit makes the manager stop remaining members, normally SIGTERM
then forced termination after `TimeoutStopSec`. Cleanup is performed by the OS
manager, not the dead shell.

**CRASH-13.** This adds neither a Gold executor nor a Synapse daemon. The OS
controls lifetime; the core still uses BOOT-1 and section 4 pipes.

**CRASH-14.** Require a working systemd user manager and cgroup v2. Other
configurations are not implicitly supported: fail explicitly rather than
silently substituting one-PID termination. `cgroup.events` `populated=0` means
no live members/descendants; inotify reports changes. `cgroup.kill` (Linux
≥ 5.14) explicitly kills a group but requires an initiator; it is no watchdog.

### 9.4. Application close

**CRASH-15.** Request protocol shutdown, await buffer/terminal-state confirmation
within a finite deadline, and expose forced close as a separate user action.
Reconcile unfinished operations after forced close. Window closure proves
neither successful task shutdown nor rollback.

## 10. Delivery classes and authority

**DEL-1.** Synchronized views gain no domain authority:

| Class | Purpose | Does not itself authorize |
|---|---|---|
| StateSync | Confirmed state at a version | Actions without core checks |
| Evidential | Source records, provenance, verification results | Admission just because a record was stored or delivered |
| Live | Operational observation | Acceptance of knowledge, results, or actions |

**DEL-2.** Users can decide using these data; automatic admission remains in
the core. Revalidation checks current truth before effects, not the old view.

**DEL-3.** Intermediate StateSync loss can be detected and repaired by
authoritative replacement (SYN-10/14/15). Zero loss is not promised.

**DEL-4.** Repeatable retained-source delivery does not guarantee source
availability. EventStream represents STARTED, COMPLETED, REFUSED, NOT_REACHED,
and GAP. `timestamp_unix_ns=None` distinguishes projection from execution time.
`chain_integrity=COMPLETE` does not erase missing phases or GAP.

**DEL-5.** Live allows marked gaps and cannot justify domain decisions.

**DEL-6.** "Decisions and rationale" aggregates several evidence sources.

**DEL-7.** Unavailable expected records differ from domain GAP events. Recheck
known missing records behind the cursor or invalidate ranges when availability
changes. Record 40 restored after reading to 100 must appear without a new source
event ID. Restored delivery does not erase an existing GAP. Paging after the
cursor alone cannot discover this; the read projection must handle it.

**DEL-8.** Deliver exact records or verifiable references, retaining source IDs
and hashes. Keep session/channel/order/read-status metadata outside domain content.

**DEL-9.** `retained` does not mean recovered after a crash; it can be an ordinary
first read. Equal event numbers from different sources do not establish causal
order: source identity and record content do.

## 11. Memory policy: settings, preview, and acceptance

This section defines **Settings → Memory and learning**. Operators prepare,
validate, inspect, and accept changes; the core retains decisions about
knowledge, habits, and actions. Appendix D identifies existing mechanisms and
gaps. See section 12.4 for surfaces, 5.6 for acceptance, and 8.4 for permission
mode separation.

**MEM-1.** Manage versioned policy, not memory records or manually assigned
"verified" habit status. Keep three actions separate: save a profile (versioned
template without changing active policy), validate admissibility/impact (without
acceptance), and accept a specific configuration version and preview basis.
Saving is not acceptance; preview is not future learning; restoring parameter
values is not memory rollback.

**MEM-2.** Existing mechanisms: `PARAMETERS` in `policy.py` declares parameters
and formulas; `policy_identity()` includes POLICY_V6, decision rule, parameters,
and digest; `resolve_parameters()` accepts declared keys, numeric types, and
nonnegative integer counters only. `read_memory_configuration()` rejects literal
NaN/Infinity/-Infinity through `parse_constant` and enforces a byte budget.
`decide.py::_reassess` returns `births: []`: reassessment creates no new habits.

**MEM-3.** Core validation covers more than keys/types: finite numbers on every
input path, field-specific zero validity, confidence ranges, probabilities,
divisors, threshold relationships, and the range implied by
`w_outcome·outcome_score + w_segment·segment_score`. Derive constraints from
formulas and domain contracts, not convenient sliders. Do not impose a common
sum-of-weights rule on unrelated coefficients: normalized signal mixtures and
`resources`/`norms` cost estimates have different meanings. Use one domain
validator for UI, CLI, and import. Never silently clamp, repair, or normalize.
Mark invalid fields, name violated rules, and disable application. Expert mode
reveals controls/explanations without disabling validation or admission.

**MEM-4.** Preview is a new core surface: dry reassessment of retained evidence
at a specified cut under a proposed configuration. It changes neither live
memory nor the external environment. Share domain evaluation/validation with
acceptance, through separate calculation/commit phases or fully isolated owner
contexts. Temporary isolated records and preview reports are allowed; writing
live journals, publications, evidence, or configuration binding is not. Do not
invoke external tools/models to fill missing evidence. Calling live `reassess()`
is not preview: it writes, maintains storage, and accepts. Show evaluated results
and mark unchecked dependencies unevaluated; never presume success.

**MEM-5.** The exam factory is not preview isolation. A/B/C exams read a fixed
complete memory snapshot, register no owner sessions, and run no court even
after failure. They require matching active configuration ("an exam reads a
memory bound to its own configuration"); exam existence does not make
`MemoryFactory.reassess()` read-only.

| Action | What it evaluates |
|---|---|
| Reassessment preview | Proposed configuration's consequences for a specified retained-memory basis |
| A/B/C exam | Modes of using fixed memory |
| Controlled learning run | Learning on chosen scenarios under a policy |

Use mechanisms for their actual purposes; none substitutes for the other two.

**MEM-6.** Bind preview to memory owner, current/proposed configurations,
policy and parameter-schema revisions, record cut, and verified evidence
availability. Profile names or a JSON-file hash alone are insufficient.
Changed bases invalidate previews; refresh before acceptance. Interrupted work
means "Assessment incomplete", not "No changes" or zero loss. A late result
for another configuration cannot replace the current result. Terraform's
"Saved plan is stale"/serial check is a precedent. Reassessment promises no
future skill births (`births: []`); republication of existing behavior is not
birth of a new skill.

**MEM-7.** Accept through the canonical route and existing owner.
`MemoryFactory.reassess()` accepts via `owner.rebind()`, recording `{from,to}`
and reassessment identity along with report, decision, snapshot, boundary,
and retention in the actual CMD-18 order. React/Rust never write active policy
directly. CMD-15–19 bind acceptance to configuration and preview basis; further
edits invalidate preview. Distinguish edits saved / validated / waiting for a
safe point / accepting / accepted / outcome unknown. Only the owner confirms
the final state. Moving a slider or closing settings never applies policy.

**MEM-8.** After acceptance, new sessions use the new configuration;
`register_session` records `configuration_sha256`. Do not rewrite old session
records. Check continuation/recovery of old sessions separately: concurrent
policies under one owner are not implied. `resolve_memory()` rejects a run
descriptor that conflicts with the active binding ("the run's memory owner is
bound to another configuration"). Accept at an owner-defined safe point after
checking active/recoverable sessions. If old obligations cannot be completed or
supported compatibly, block with a reason. Policy targets the selected project
memory owner; per-task courts within the same memory are not promised.

**MEM-9.** Keep three storage measurements distinct:

1. Durable disk memory: owner-accounted used bytes, active/target limits,
   utilization, unfinished-write reserves, medium availability, and measurement
   time. Usage and limit are different controls; reducing a limit does not
   reduce displayed usage.
2. Raw traces: `raw_capacity` counts traces; 0 means no declared limit, not bytes
   or gigabytes. Display it separately.
3. Memory RAM cache: a distinct limit whose measurement/enforcement contract
   is not established in verified surfaces. Do not present it as effective yet.

A target below usage does not delete data. Show the deficit and refuse activation
until a permissible reclamation plan is executed and verified. Preserve required
evidence; an archive label alone does not free bytes. OpenZFS's refusal to set a
quota below used space is a precedent, adapted here to verified reclamation.
Account categories without double counting and attribute shared blocks explicitly.
Without a finite limit, invent no usage percentage. At exhaustion the owner
refuses obligations exceeding budget and enters its defined state, retaining
space for result logs/recovery. Enforce actual limits with concurrent writes;
the UI cannot simulate physical enforcement.

**MEM-10.** The consequence chart compares profiles; it never admits actions.
Show active/proposed configurations. Verified contours are solid; estimates are
dashed and labeled "Estimate". Use labels/styles as well as color. Unknown
metrics mean "No estimate", not zero, 50 points, or an old value; do not connect
across unknown vertices. Each axis documents metric, unit, task scope, source,
normalization version, and measured/estimated/unavailable status. Keep one
scale for compared profiles; do not rescale min/max on slider movement.
Changing `resources`/`norms` policy coefficients cannot move measured economy:
they are not consumption measurements. Polygon area is not an overall score.
Selecting an axis shows benefits/risks/basis without hidden edits or execution.

**MEM-11.** Restoring historical parameter values creates a new configuration
draft with the same validation/acceptance lifecycle. It does not restore old
habit states, deleted experience details, or previous memory contents. Keep
historical reports unchanged and append a new reassessment. NixOS generations
illustrate historical configuration selection without rewriting history; do not
copy their semantics literally to memory.

**MEM-12.** Evidence provenance, established source independence, authority,
external-effect properties, and tool contracts are protected constraints, not
preferences. Their owners require separate change procedures, never a
"trust more" slider. Version 1 does not expose `birth_sources=0/1` as accelerated
settings: `independent_witnesses(required=1)` accepts one witness as independent,
and required=0 is trivial. Two witnesses alone do not guarantee truth; provenance
and all other criteria remain mandatory.

**MEM-13.** The following controls and MEM-14–18 are interface requirements,
not optional illustrations. The bridge defines their data, commands, and states;
the application's UI description defines the remaining layout.

| Element | Required form |
|---|---|
| Individual parameter | Horizontal scale/slider, exact value, minus/plus, active-value marker |
| Comparable weights | Vertical equalizer with numeric inputs |
| Related thresholds | Two handles on one scale with clear ordering constraints |
| Consequence chart | Four axes, active/proposed contours, benefits/risks/basis explanations |
| Memory volume | Measured utilization separate from manual limit control |

Correct commands with an arbitrary layout violate the contract just as a correct
layout with nonexistent commands does. Revise the UI description through its
own revision process and record discrepancies explicitly. This bridge document
does not replace the complete application requirements.

### 11.1. Controls and form data

**MEM-14.** Horizontal numeric sliders increase to the right, with editable
numbers, minus/plus, and a separate active-value marker. All controls edit one
local draft. Integer counters cannot have fractional steps. Obtain step,
bounds, unit, applicable rules, and constraints from the core description.
Arrow keys move one permitted step; Home/End use declared bounds. Scrolling
does not change values without explicit control focus. Preserve incomplete or
invalid input text and show its error; neither submit it nor replace it with
the nearest legal value.

| Field | Control | Behavior |
|---|---|---|
| Counter/coefficient | Slider, exact input, minus/plus | Explicit active → proposed difference |
| Ordered threshold pair | Shared scale, two handles, two inputs | Validate ordering; handles never swap roles |
| Fixed-sum shares | Movable split on one bar | Show both edits; link only where the specific contract requires it |
| Comparable weights | Vertical equalizer and exact inputs | Up increases weight; no hidden normalization |
| Decision method | `DECISION_RULES` selector | Show applicable fields; method change is a policy edit |
| Volume limit | Slider, input, minus/plus, unit | Changes target, not measured usage |

**MEM-15.** Proposed parameter descriptions contain technical key, type,
localizable name/explanation keys, unit, version-specific default, range, step,
applicable methods, related constraints, access class, editability, and refusal
reason. Active values carry owner/configuration identity. React cannot invent
ranges or another validity catalogue. Convert RU/EN input to typed values before
sending; wire representation and hashes are locale-independent. Without normative
metadata, show read-only values rather than invented editable ranges. Unbounded
parameters use exact input and an owner-defined limit mode, not arbitrary finite
slider maxima.

### 11.2. Layout and immediate feedback

**MEM-16.** Settings layout: memory usage/limits card at the top; grouped
controls left, chart and selected-metric explanation right; bottom actions
Reset edits, Save profile, Validate configuration, Assess impact, Accept.
This does not replace the workspace's persistent right conversation panel.
Movement immediately updates value, parameter difference, and known local
constraints. Heavy reassessment runs by explicit action with draft identity,
not on every movement. Debounced lightweight estimates may update without
accepting. Old calculations cannot overwrite new drafts; stopping preview
waiting is not cancellation of the main task.

**MEM-17.** Four chart rays: up = decision quality on checks; right = operating
speed; down = useful experience retention; left = resource economy. Farther
from center means better by the declared metric, independent of Cartesian sign.
Use MEM-10 metrics/normalization and the same task scope/basis for both profiles.
Show original units, gain/loss, and measured/estimated/no-estimate status below.
A calculated difference is not statistically verified improvement. Axis selection
explains contributing parameters, evidence, and limits. Dragging vertices does
not edit policy. Arbitrary game-like risk scales without a model/source are forbidden.

**MEM-18.** Storage cards separately show used bytes, active limit, target,
reserve, and actual free medium space. Calculate percentages only against a
finite active limit. Quota headroom is not reserved disk capacity. Distinguish
MB/GB from MiB/GiB; unit switching preserves exact bytes without silent rounding.
Show raw_capacity separately in traces, labeling 0 "No declared limit".
Separate memory-owner RAM cache from agent process memory. Missing measurement
means "Not measured"; missing enforcement means "Limit unsupported". Targets
below usage create the MEM-9 deficit plan; neither usage bar nor active quota
changes before verified storage maintenance.

## 12. Surface map

### 12.1. Surfaces confirmed directly in code

| Surface | Confirmed behavior | Limit of the guarantee |
|---|---|---|
| `execute_approval_action()` | Grant issuance or revocation; APPROVED/REVOKED | Not new-request refusal, single use, or application |
| `authorize_first_side_effect()` | Rechecks repository revision, snapshot, policy, admission, compatibility, and required current human approval | Name alone does not prove binding of any future displayed diff or grant consumption |
| `InvocationStore.claim()` | Binding comparison, retained terminal result, EXECUTION_STATE_UNKNOWN for unfinished claims | Invocation guarantee, not every future UI command |
| `InvocationStore.cancel()` | Stores cancellation for CLAIMED and returns bool | Not process/task termination or rollback |
| `RunRecordRecovery.session()` | Exclusive session/recovery for a particular run-record store | Not OS process-tree control or a global application lock |

These boundaries follow implementations, not manifest descriptions alone.

### 12.2. Reading the map

**MAP-1.** Separate existing function, approved composition route, and proposed
external operation. A permitted root does not mean its UI command exists; a
module's existence does not complete a map row.

**MAP-2.** Say "the public contract is not established in the inspected paths",
not "there is no owner."

**MAP-3.** Inspected paths do not establish public contracts for the main
conversation, consistent domain snapshots, whole-task stopping, or general
material reading/saving. SYN-17 defines composite snapshots. Lack of global
atomicity is not a missing function for the UI to invent.

### 12.3. Specific map entries

| Operation | Required boundary |
|---|---|
| `state.snapshot` | Domain owners supply bases/revisions/truth; adapters assemble but cannot invent consistent cuts. Subscription, heartbeat, queues remain adapter responsibilities |
| `project.tree.read`, `material.read` | Authorized general project/material reads before runs or C1 evidence exist. Neither C1 nor arbitrary renderer disk access; contract open |
| `c1.evidence.read` | Specialized C1EvidenceContext/read_c1_verification_evidence read; no writer/oracle execution |
| `invocation.cancel`, `task.control` | Specific invocation cancellation uses AgentExecutionPort/InvocationStore; whole-task coordination is a separate proposed contract. Cancellation request and restore reads do not prove termination. Explicitly refuse unsupported task operations instead of cancelling one invocation |
| `message.send` | Public dialogue-history contract open |
| `permissions.set_mode` | MODE-3: new tasks; existing tasks retain frozen versions |
| `budget.get` | Composite read: runner/budgets attempt admission, Stage 15 accounting/capture/reconciliation model-cost evidence, agents/policy runtime limits. Each metric has unit/scope/status; these do not imply a shared money reservation mechanism |
| `events.page` | Read-only Evidential cursor `assessment_sha256 + after_sequence`; only read methods exposed to UI consumers. DEL-7 handles restored records behind cursors |

### 12.4. Memory policy settings surfaces

Command names are drafts; MAP-1 status distinctions apply. Results implement
MEM-1–12, with controls defined by MEM-13–18.

| Command | Result | Live memory mutation | Existing basis / remaining work |
|---|---|---|---|
| `memory.policy.read` | Active configuration, metadata, versions, permitted edits | No | Active binding from `MemoryOwner.bound_configuration()`; file parser/PARAMETERS/policy_identity describe drafts, not active state. Command new |
| `memory.policy.validate` | Errors, warnings, change class/scope | No | resolve_parameters exists; mathematical invariants new |
| `memory.policy.preview` | Assessment at fixed basis and limitations | No; service records isolated | New core surface, MEM-4–6 |
| `memory.policy.accept` | Saved owner decision and verifiable outcome | Yes, canonical route | MemoryFactory.reassess → owner.rebind exists; command/identity new |
| `memory.history.read`, `memory.learning.read` | Accepted configurations, authors, bases, habits/candidates | No | Proposed |
| `memory.storage.usage` | Used bytes, accounting scope, utilization, reserves, medium availability, timestamp | No | Contract open |
| `memory.storage.plan` | Deficit, permitted actions, consequences, source-cut binding | No | Contract open |
| `memory.storage.accept` | Limit acceptance and permitted maintenance | Yes, bounded by contract | Contract open; no direct UI deletion |
| `memory.metrics.read` | Metrics, comparison scope, normalization, sources, measurement/estimate status | No | Contract open; chart does not execute |

Saving a local profile template belongs to the client, not policy acceptance.
Existing owners retain reassessment, publication, storage, and binding. Do not
introduce a competing memory owner for the form.

### 12.5. Functional integration routes

**MAP-4.** Distinguish five connections: UI machine adapter (commands/projections),
external AgentAdapter (admitted task/typed result), model connection (agent
ModelBroker versus language-runtime LLMGateway), MCP tool (McpToolPort, one tool
operation), and memory port (ProjectMemoryCourt/MemoryFactory to existing court
and owner). Similar cards do not make them one operation or transfer authority.

**MAP-5.** Read active policy from `MemoryOwner.bound_configuration()`. A file
configuration is a proposed draft until bound by the owner.

| UI area | Main route | New work |
|---|---|---|
| Agents | agents/configuration → registry → selected factory | Administration, probes, profile change procedure |
| Models | agents/model_broker or llm/gateway according to purpose | Unified presentation, protected secrets, local model management |
| Tools | ToolConfiguration → Gateway → McpToolTransport → McpToolPort | Controlled server/tool administration |
| Skills/instructions | Existing templates/domain contracts for their own purposes | Versioned user instruction contract; not replacement prompt_template semantics |
| Tasks/executors | CLI → runner composition → agent selection → Stage 10 → execution port | Machine commands, dialogue task submission, coordinated task control |
| Memory/learning | ProjectMemoryCourt, MemoryOwner, MemoryFactory, policy/court | Preview, acceptance identity, missing validation/quotas |
| Review/log | Typed outputs, C1, invocations, memory reports, Stage 15 | Authorized reads, live status, source-specific cursors |

Route: UI → canonical machine command → approved composition root → existing
owner → adapter/tool → retained result → UI. Appendix D is navigation to those
mechanisms, never permission for direct renderer access to internals.

### 12.6. Shared connection settings contract

Administrative commands, form descriptions, and durable records are proposed.
Existing Python functions are foundations, not ready-made IPC methods.

**CONN-1.** Each card declares type, purpose, and scope: external agent,
language-runtime model, agent-profile model, MCP tool, or instructions.
User labels do not replace identity. Distinguish project, profile, selected-run
usage, and new-run default. Project switching cannot transfer settings/probe
results into another project.

**CONN-2.** Separate local draft, saved definition, accepted configuration,
and actually used version. Saving cannot change a running process/frozen run.
Acceptance binds owner, expected revision, and validated draft. Concurrent
stale-base writes conflict; late repeats return historical results without
reactivating old configuration.

**CONN-3.** Connection health comprises independent facts:

| Fact | Establishes | Does not establish |
|---|---|---|
| Valid definition | Domain-valid structure/values | Safe plugin or responsive endpoint |
| Available environment | Required components/environment found | Admission for any task |
| Matching identity | Runtime/configuration match inspected definition | Unchanged hidden remote implementation |
| Completed probe | Exactly the declared scenario completed | Correctness of every future result |
| Retained admission | Admission with necessary basis | Eligibility for this task |
| Selected for run | Owner fixed this run's choice | Execution started/completed |

Each fact carries source, check scope, and configuration identity. Distinguish
unsupported check, refusal, unknown, and stale result.

**CONN-4.** Static definition validation imports no third-party executable code,
starts no process, and uses no network. Loading factories, starting agents, or
contacting MCP/models requires an explicit probe. Before it, show executable or
address, transmitted data, scope, limits, and potential cost. Use fixed nonsensitive
control material; never automatically attach project files, chat, or memory.
Apply the execution kind's domain restrictions. A probe does not invent admission.

**CONN-5.** Probes have identity, finite time/resources, and results bound to
the inspected definition. Timeout after sending does not imply zero cost/effect.
Retry under owner rules. Probe cancellation is not whole-task cancellation.
Results arriving after edits remain attached to the old definition and are
inapplicable to the new draft.

**CONN-6.** Accepted disabling excludes a profile from future selection, without
rewriting runs, deleting evidence, or revoking an existing grant. Stopping active
work requires a separate supported command. Before deleting definition/runtime,
show active/recovery dependencies and retain required components. Runtime drift
cannot silently retain admission or trigger another-agent fallback.

### 12.7. Settings → Agents

**AGT-1.** List admitted definitions separately from discovered factory entry
points. `discover_adapter_factories()` discovers without `.load()`. Do not use
`registry_from_configuration()` as an unconditional reader: it checks runtimes
and constructs adapters. Provide a bounded definition projection without plugin
execution.

**AGT-2.** Form groups and code bases:

| Group | Data | Basis |
|---|---|---|
| Identity | profile_id, agent_id/version, adapter_id/version | AgentProfile in agents/contracts.py |
| Connection | factory, transport, typed native section | agents/configuration.py, selected factory |
| Capabilities | Declared versus admitted subset, media/output profiles, effect classes | AgentProfile, CapabilityAdmission, AgentRequirements |
| Isolation | Network, isolation, workspace, read-only paths, image, devices | RuntimePolicy |
| Resources | Time, CPU, process memory/count, stdout/stderr, output, scratch | ResourceBudget |
| Basis | Runtime/dependency/configuration identities and acceptance evidence | capture_definition, verify_configuration_runtime |
| Selection | Ordered preferences and task eligibility | AgentRegistry.resolve, select_coding_agent |

Reorder by drag or up/down controls as a draft until acceptance. Editing displayed
capabilities cannot expand the admitted subset without owner decision/evidence.

**AGT-3.** Build `native` forms by factory: STDIO argument array, permitted
environment, protocol and optional model_access; Docling Python component and
installed model artifacts; ACP Python component and explicit agent argument
array; A2A pinned AgentCard, permitted endpoints, credential reference. Never
invoke a shell to "validate syntax". Do not carry inapplicable fields between
factories silently.

**AGT-4.** Distinct actions: Add definition, Save draft, Validate definition,
Check environment, Run probe, Present admission evidence, Accept for new runs,
Disable. Buttons may combine workflow presentation, not semantics.
`capture_definition()` records supplied runtime/evidence; it creates no successful
tests and does not evaluate their adequacy. Missing administrative ownership
cannot be bypassed with direct writes. Testing an unadmitted integration requires
a separately authorized bounded probe, not fake admission through the normal
port. Until that contract exists, the probe is unavailable; evidence can still
be presented through the existing procedure.

**AGT-5.** Show task eligibility: capability, result format, effects, information,
network, resources, and profile exclusion reasons. NO_ELIGIBLE_AGENT cannot
weaken requirements or choose an unverified fallback. Document adapters and
Gold coding consumers are not interchangeable. New domains need their own
contracts/checks/results in the same lifecycle, not documents disguised as patches.

**AGT-6.** Profiles, actual invocations, and internal Synapse roles are different
objects. The top strip shows real work instances, not installed-agent inventory.
Cards show name/role, confirmed state, sourced activity, task/attempt/invocation
links, and open details centrally. Agent completion and result acceptance are
separate. Unknown activity is labeled, never fabricated by timers.

### 12.8. Settings → Models and limits

**LLM-1.** Select language runtime (`llm/gateway.py`) or external-agent model
access (`agents/model_broker.py`) before editing. Similar configuration is not
interchangeable; never reroute between them for compatibility. Distinguish agent
provider_name, wire provider, endpoint, and model name.

**LLM-2.** ModelConnection exposes model, endpoint, credential_env,
timeout_seconds, and supported request profile. State current restrictions:
explicit Chat Completions endpoint, nonempty credential environment variable
when starting the broker, no streaming, and no `n ≠ 1`. Runtime gateway support
follows its own implementation. An endpoint text field does not promise every API.

**LLM-3.** Enter secrets through a separate protected action. Normal reads return
reference, purpose, and availability only. Exclude values from copied definitions,
exports, logs, StateSync, and ordinary parameter hashes. Deliver a secret only
to the authorized process/purpose; agents receive broker capability, not provider
keys. Secret replacement creates a version and cannot change already bound
command content. The owner defines effects on active calls.

**LLM-4.** Initially "local model" can mean an explicitly selected compatible
local server. It does not promise installation, weight downloads, a catalogue,
or GPU management. Future managed servers require runtime/model identities,
disk scope, process group, resource bounds, launch/readiness/shutdown, and download
permission. A user-owned server is not killed with Synapse without an ownership
contract. Finding a local address permits neither information-boundary bypass
nor probes containing work data.

**LLM-5.** Every limit has a scope and enforcement mechanism:

| Metric | Display | Boundary |
|---|---|---|
| Run budget | Accepted limits and owner-established remainder | runner/budgets checks between attempts |
| Agent memory/CPU/processes | Runtime bounds and observations | Not durable-memory quota or entire-model limit |
| Model tokens | Confirmed/unknown consumption and pending calls | Missing usage is not zero |
| Money estimate | Explicit price source/version | Not provider invoice or proven hard limit |
| Hard shared cap | Only an implemented reservation/refusal limit | A form field cannot supply enforcement |

Do not count one physical model call twice through separate projections.
Source/call identity governs accounting; capture redelivery adds no consumption.
Window closure does not determine an in-flight remote request's outcome.

### 12.9. Settings → Tools, skills, and instructions

**TOOL-1.** Show MCP servers and tools as two levels. Servers specify transport,
arguments, working directory, and protected environment references; tools specify
name, descriptor identity, input/output schemas, and domain contract. Bases are
memory_consolidation/tools/contracts.py, mcp_transport.py, and agents/mcp_tools.py.
Server inventory is not agent inventory.

**TOOL-2.** Reading saved definitions starts no server. Live inventory is an
explicit probe because it opens a connection/process. Compare descriptors with
admitted versions; changed schema/identity invalidates old probes/bindings and
requires an update decision. An enabled switch does not admit every discovered tool.

**TOOL-3.** Memory-bound execution uses Gateway.invoke → McpToolTransport.call
→ McpToolPort.invoke. Administration cannot call transport directly to bypass
external-action recording. UI guesses cannot define schemas, reason/action role,
effects, outside, or repeatability. Preserve uncertain outcomes; closing a
connection does not prove no effect occurred.

**TOOL-4.** Disabling applies to new permitted calls. Handle active calls and
recovery separately. Include spawned MCP servers in platform lifecycle acceptance.
A returned `close()` alone does not prove all server processes/requests ended.

**INST-1.** Instructions, agent capabilities, tools, and learned habits are
distinct. Text/scoping grants no tool, network, memory, or publication rights.
`prompt_template.py` substitutes templates; it is not a skills administration
registry. Design a versioned instruction registry/owner without inferring
ownership from a similar filename.

**INST-2.** Proposed instruction definitions include identity, immutable content
version, name, source, project/role/profile purpose, application order, allowed
variables, and recipient classification (local executor or allowed public model
part). Saving, template validation, and assigning a version to future runs are
different actions. Editors cannot replace active-run bindings. Show conflicting
assignments before acceptance; order cannot depend on UI name sorting.

**INST-3.** Show draft and active-version diff. Validate only declared variables;
an unknown required variable is a contract error, never a reason to insert local
memory. Template preview invokes no LLM. User instructions cannot extend
PublicConversation roots or local_edits outside their version/admission contracts.
"Connect skill" cannot convert text into an admitted habit.

### 12.10. Projects, materials, editor, and review

**READ-1.** Left panel: project/files/documents; center: tasks. Selecting a file
requests project.tree.read/material.read, not agent work, C1 execution, or model
submission. knowledge_environment connect/status operations do not supply a file
tree. C1 readers serve only their own evidence kind.

**READ-2.** Address workspace plus authorized reference/root-relative path.
Verify actual containment after resolution, object type, and available version;
string-prefix matching is insufficient. Account for symlinks and Windows/Linux
path behavior. Include origin, content identity/version, size, and type. Read
large files in bounded same-version ranges; detect changes instead of joining
different versions into one document.

**READ-3.** Distinguish immutable evidence, working file, and proposed change.
Never overwrite evidence from the editor. Working edits remain local until an
explicit Save. Proposed material.save carries expected base version/new data to
the established file-change owner. External changes produce conflict, not silent
overwrite. Before that route exists, do not simulate save success or write via
privileged shell APIs.

**READ-4.** Bind diff to base version, exact proposal, affected objects, and
verification/approval references. Monaco presents; it neither accepts nor applies
Gold results. Accept proposal and save own edit are different commands. Changed
base/proposal invalidates consent until revalidation. Keep agent completed,
candidate received, verified, and result accepted distinct.

**READ-5.** Open materials, large reports, and evidence centrally. Embedded views
execute no active document HTML or arbitrary shell commands. Unsupported formats
show reasons; external viewers/export are separately permitted actions. Tabs,
language changes, and evidence reads cannot change policy, execution cursor,
or agent state.

### 12.11. Machine surface contract

**MAP-6.** Before enabling each command, define action class, owner/scope,
approved root, input schema, verifiable pre/postconditions, effects,
authoritative acceptance/result records, retry/conflict/recovery, permitted
response projection, event source, and behavioral verification. Paths/symbols
are navigation, never permission to bypass owner entry points.

**MAP-7.** Administrative reads/static validation create no execution world.
Constructors that open writable stores, recover boundaries, import plugins,
or start processes are not safe readers because of their names. Use established
read boundaries; do not duplicate formulas, admission registries, or recovery.

**MAP-8.** UI recovery, settings opening, and draft restoration cannot
automatically trigger commands with effects or active-configuration changes.
Restore presentation and prior exchange identities. Saved drafts are not consent
to run again. Unsupported commands expose the specific limitation/project state,
without substituting demo results.

## 13. Security and content restrictions

**SEC-1.** Tauri capabilities are not Synapse permissions. Platform access
limits do not define domain authority or correct faulty scope checks in commands.

**SEC-2.** Validate every allowed method, authority, and trusted caller context.
One closed typed router with correct checks is valid; many unchecked functions
are not safer by number. Unchecked generic forwarding to internals is forbidden.

**SEC-3.** Window/channel identity comes from trusted platform context, never
an arbitrary JSON field.

**SEC-4.** Parse untrusted documents into a safe structured presentation tree,
not an iframe: Tauri on Linux cannot distinguish iframe requests from window
requests.

**SEC-5.** Configure mandatory production CSP in Tauri, permitting needed IPC
and denying unnecessary external requests. Tauri documents `ipc:` and
`http://ipc.localhost` connect sources. The demo's `connect-src 'none'` is for
an isolated offline page and must not be copied as production configuration.
Validate the actual policy with working product IPC (ACC-3); exact policy is open.

**SEC-6.** Secrets/sensitive parameters use protected representations (CMD-6).

**SEC-7.** The machine adapter enters Gold only through declared roots; direct
entry into project_court or runner/budgets bypassing their owners is forbidden.

**SEC-8.** User visibility of a document, memory record, report, or evidence
does not authorize sending it to an external model. Deliver task and permitted
local information separately to the executor (worker_bridge/STDIO); agent model
requests are limited to PublicConversation public data. Separate executor inputs
are not a second route to send local memory externally. This holds in every
permission mode and under all memory settings. General ownership rules alone
do not replace this boundary.

**SEC-9.** Before logging/display, diagnostics apply secret exclusion and volume
limits. Errors, stacks, parameters, and paths cannot bypass confidentiality.
The policy's exact form remains open.

**SEC-10.** Distinguish immutable source records from authorized UI projections.
If a source contains data unavailable to the window, return a permitted projection
or protected reference with a reason. Do not rewrite the source or assign a new
"original" identity. Changed visibility clears/invalidates views under SYN-16.
Knowing command_id, grant_sha256, or a path does not authorize full result access.

**SEC-11.** Connection probes use no current-project secrets, memory, or
materials as prompts. Use explicit CONN-4/5 probes through permitted processes
and specified addresses. Probe success does not permit redirects, extra
endpoints, or arbitrary network use. Extension-directory reads import no code;
document viewing does not activate embedded instructions.

## 14. Version compatibility

**COMP-1.** Every protocol message carries `schema_version`.

**COMP-2.** An older peer may ignore an optional, nonauthoritative extension
only if that version's schema explicitly declares it ignorable, with logging.
Removal, renaming, or semantic changes require a new schema version. Negotiate
ranges during BOOT-7. Silently ignoring anything else is forbidden (COMP-8).

**COMP-3.** JSON Schema draft 2020-12 owns the new external protocol. Python
performs schema and domain checks; UI runs Ajv validators compiled at build
time. Running a precompiled validator requires neither dynamic schema compilation
nor weakened CSP. Generated TS types do not replace incoming-data validation.

**COMP-4.** Each vocabulary has one primary source; existing core vocabularies
produce schemas or have verified exact correspondence (MODE-7).

**COMP-5.** Run one conformance corpus on both sides: valid/invalid messages,
unknown fields, duplicate keys, numeric boundaries, version mismatches, target
substitution, and incomplete results. Compare verdicts and absence of hidden
mutation. This establishes agreement on tested cases, not mathematical equivalence
of implementations. Explicitly configure `format` checks; annotations alone
cannot masquerade as validation.

**COMP-6.** Use JCS (RFC 8785) for parameter hashes.

**COMP-7.** Zod cannot be a second authoritative message contract. Use JSON
Schema, derived types, and corresponding runtime validation.

**COMP-8.** Never silently ignore new methods, effect restrictions, required
fields, or significant states. Unknown method/restriction means an explicit
"not understood" refusal. Executing after discarding an unfamiliar restriction
would perform something other than the authorized action.

**COMP-9.** Bridge envelope and nested domain-record versions are separate.
Carry historical records unchanged or use compatible readers. Record-schema
changes require explicit versioning/migration, not silent rewriting for UI ease.

**COMP-10.** Both sides validate every message without field removal, value
insertion, or hidden coercion. MEM-3 is the memory-specific part, not a
replacement. COMP-5 verifies both implementations' behavior.

**COMP-11.** JCS forbids duplicate property names and requires numbers
representable as IEEE 754 doubles; use strings for greater precision/longer
integers as recommended by RFC 8785 section 3.1. Canonicalization must preserve
the intended value before hashing: hashing after precision loss identifies the
wrong command. This governs the new envelope and does not alter existing
evidence-record canonicalization.

## 15. Acceptance

### 15.1. Three levels

**ACC-1.** Component acceptance (Vitest/Testing Library): vocabularies,
localization, panel projections, synchronization reducers (SYN-7–9), and
bootstrap/handshake state machines (BOOT-3/7).

**ACC-2.** Desktop build via tauri-driver/WebDriver: framing (TR-1–11),
handshake, Channels, core bootstrap/restart, loss/recovery (sections 5 and 9).

**ACC-3.** Installed product: bundled sidecar, actual installation, canonical
entry/machine mode, platform termination profiles, production CSP, and tested
binary identity bound to the build artifact (ACC-5).

**ACC-4.** State inspection (demo example: `window.SynapseDemo.snapshot()`) is
an auxiliary read, not a substitute for the real window and owner result.
Acceptance performs real-window actions, checks visible results, and links
them to canonical owner records. Never create a separate state solely to pass tests.

**ACC-5.** Record the build artifact's identity (for example its hash) and
verify that this exact artifact was exercised, beyond a process self-reporting
the expected version.

All techniques above belong to the external acceptance layer under
[ACCEPTANCE_RULES.md](ACCEPTANCE_RULES.md), never product source.

### 15.2. Initial end-to-end acceptance cases

Negative and recovery paths are mandatory: happy paths cannot distinguish the
properties they establish. Original case numbers are retained.

1. **Refuse a new request:** no grant_ref; separately revoke an existing grant
   with grant_sha256. Refusal cannot be implemented as revocation (APR-1/2).
2. **Two-file batch, one once-grant:** both writes are permitted within one
   approved batch execution (APR-5/6).
3. **Competing executions, one once-grant:** exactly one acquires authority;
   reject the competitor (APR-8).
4. **Redeliver a running operation:** return its state without another execution
   (APR-6, CMD-12).
5. **Interrupt after file one:** recover the same operation under owner rules,
   without blindly restarting the batch or repeating completed effects (APR-6/9).
6. **Changed proposal:** old consent does not transfer (APR-7).
7. **True single use:** two operations and two command IDs sharing one once-grant
   cannot execute independently twice. Same-ID deduplication alone is insufficient.
8. **Quiet/filtered project:** subscribe, snapshot, and establish freshness via
   progress without events; the first filtered record need not be adjacent to Ps.
9. **"Approvals-101" counterexample:** revocation is never lost because of
   domain read order; never compare positions across domains (SYN-17).
10. **Record leaves filter:** deliver tombstone or invalidate; silence is not
    deletion (SYN-16).
11. **Late epoch versus current gap:** discard the former without resync;
    stop/resync on the latter (SYN-14/15).
12. **Recover command ID:** lose reply after owner write, restart, restore A
    rather than B, replay result. Also lose reply before owner write and resend A.
13. **Acceptance moment:** transport receipt is not acceptance; a possible
    durable write without acknowledgement remains uncertain (CMD-10/11).
14. **Core death:** restore terminal results; only particular unfinished
    operations are uncertain (CRASH-7).
15. **Shell death:** admit no new executor until old local executors cannot
    continue; reconcile remote residual effects separately (CRASH-2/6).
16. **Mode change:** new tasks get the new profile; existing tasks keep their
    fixed version. Specific revocation is separate and not retroactive.
17. **B → C → retry B:** return B's historical result without changing C;
    show current policy separately (CMD-17).
18. **Acceptance crash/lost reply:** reconcile owner records; an error does not
    imply no effect. No second reassessment on retry; recover every multistep
    boundary to verifiable complete/incomplete states (CMD-18/19).
19. **Invalid UI/CLI/import parameters:** one domain refusal with field, rule,
    and remedy; no silent normalization. Existing literal nonfinite checks plus
    MEM-3 formula invariants apply.
20. **Preview:** no live-source changes, external calls, or repeated effects.
    Changed basis invalidates acceptance; interruption means incomplete,
    not no changes (MEM-4/6).
21. **Old-session binding after rebind:** refuse incompatibility, preserve history,
    let new sessions use new policy, and block acceptance that violates active
    or recoverable-session obligations (MEM-8).
22. **Limit below unavoidable usage:** refuse activation until verified
    reclamation; preserve required evidence, distinguish targets/active limits,
    and do not redraw lower usage on slider movement (MEM-9).
23. **Rust/React reply loss:** same intent returns the same command/result;
    deliberate identical-parameter action receives new intent/command IDs.
24. **Mutation before ready:** explicit refusal before profile confirmation;
    transport readiness is not domain readiness. After negotiation recover
    identity, never blindly duplicate (BOOT-7/8, CMD-8).
25. **Oversize/corrupt frame:** reject before excessive allocation; byte/syntax
    errors terminate the session without header scanning. Rehandshake/reconcile
    afterward. Valid JSON with invalid schema is separately refused without effect.
26. **Record restored behind cursor:** after reading through 100, restored 40
    becomes available through recheck/invalidation; do not present a convenient
    history omitting it (DEL-7/8).
27. **Local information boundary:** visible materials/memory/reports never enter
    external model requests in any mode/settings. Separate task/local_information
    executor inputs; model requests contain PublicConversation data only.

### 15.3. Administration and reading cases

28. **Discovery without loading:** inventory/static validation runs no entry
    point, process, or model (AGT-1, CONN-4).
29. **Explicit probe:** show destination/control material/bounds first; exclude
    local memory/chat; timeout does not mean zero cost (CONN-4/5, SEC-11).
30. **Profile drift:** changed runtime/config/tool schema invalidates earlier
    checks without replacing a frozen run (AGT-4/5, TOOL-2).
31. **Concurrent settings:** same-base acceptances cannot silently overwrite;
    old-command retries return historical outcomes without switching active state.
32. **Disable/delete:** future exclusion is not active-invocation termination;
    never silently delete recovery dependencies (CONN-6).
33. **Cancellation scope:** invocation.cancel targets that invocation;
    unsupported task.control cannot cancel an arbitrary first process instead.
34. **Secrets:** reads/export/history/StateSync/logs reveal no value; replacing
    a protected reference cannot change an already bound command (CMD-21, LLM-3).
35. **Model/tool roles:** agent model access cannot change runtime gateway;
    MCP is not an agent; user local servers are not owned children.
36. **Instructions:** new text grants no capabilities, cannot extend public roots
    with local memory, and cannot replace an active run's version (INST-1–3).
37. **Effect-free reading:** trees/cards/reports create no writable execution
    world; C1 reading does not verify/apply (MAP-7, READ-1).
38. **Changed material:** detect version changes between ranges; stale saves
    conflict; evidence is never overwritten (READ-2–4).
39. **Overlapping StateSync ranges:** skip the applied prefix, apply remainder
    wholly; progress cannot hide gaps; reject another subscription's late snapshot.
40. **Controls:** mouse/keyboard/input/minus-plus edit one draft; preserve invalid
    text; language switching neither changes numeric policy nor accepts it.
41. **Chart:** no invented missing vertices; cost-weight edits cannot improve a
    measured contour; stale calculations cannot replace new ones.
42. **Memory units:** distinguish bytes/traces/cache/process RAM; preserve bytes
    on unit changes; reducing target does not reduce measured usage.
43. **Unsubscribe/UI restart:** release channels without cancellation; restored
    drafts do not autosubmit; process heartbeat does not refresh stale domains.
44. **Bottom-panel sources:** separate unavailability/GAP, avoid double counting
    physical model calls, and preserve source records under safe projections.

Acceptance evaluates observable behavior, actual owner records, and absence of
duplicate effects. Command names, file counts, and special test-only state are
not evidence of those properties.

## 16. Open-condition register

For each capability distinguish specified contract, implemented mechanism, and
accepted behavior. An open condition prevents claiming that dependent capability
ready; enable product access after verifying its mandatory dependencies.
Implementation and local acceptance precede readiness. Dependencies are specific:
missing RAM-cache measurement does not block independent authorized project reads.

| No. | Open condition | Kind |
|---|---|---|
| 1 | Implement/test Windows Job and Linux systemd user profiles, actual adapter launch paths, and environment matrix; CRASH-2 otherwise cannot hold | Architecture/implementation |
| 2 | Locate durable once-grant/execution binding in the actual execution path; define proposal-identity record | Authority owner |
| 3 | Define state-domain registry, snapshot bases, position encoding, progress cadence, subscription buffer size/retention, multiple subscribers | Contract/implementation |
| 4 | Coordinate whole-task stopping separately from invocation cancellation | Contract |
| 5 | Define project.tree.read and material.read | Contract |
| 6 | Define main-agent conversation history and composite independently versioned snapshots without global atomicity | Contract |
| 7 | Define authoritative acceptance records for approvals/messages/policy changes, using invocation claim as a pattern | Contract |
| 8 | Reconcile lost command identity; protect sensitive pre-send content | Procedure |
| 9 | Define protocol core shutdown and confirmation (CRASH-15) | Contract |
| 10 | Define terminal-result retention/TTL; expiry never makes an old command new | Policy |
| 11 | Implement isolated dry reassessment, all mutable stores/references, report and basis binding (MEM-4–6) | Contract/implementation |
| 12 | Core mathematical invariants and common UI/CLI/import validation (MEM-3) | Contract |
| 13 | Authoritative memory acceptance records and late B→C→retry B reconciliation | Memory owner |
| 14 | Verify recovery at every acceptance effect: republication/report/decision/snapshot/boundary/retention/rebind/reply loss | Acceptance |
| 15 | Byte accounting, reserves, shared-block attribution, double-count prevention, raw_capacity category | Contract |
| 16 | Define/implement memory RAM-cache measurement and enforcement | Contract/implementation |
| 17 | Define consequence metrics, sources, and normalization | Contract |
| 18 | Exact hello/select/ready schemas, common profile, permitted command classes | Contract |
| 19 | Intent issuance/storage, durable scope/content/command binding, restart recovery | Contract/implementation |
| 20 | Numeric frame limits and exhaustion behavior | Contract |
| 21 | Secret exclusion and diagnostic log/display volume limits | Contract |
| 22 | Exact production CSP permitting IPC and excluding unnecessary networking | Configuration/acceptance |
| 23 | Evidential missing-record rechecks/range invalidation for restoration behind cursors | Contract/implementation |
| 24 | Agent-definition administration: accepted versions, admission, preferences, expected revision, disabling, command recovery | Contract/implementation |
| 25 | Safe bounded probes, no work data, cost/resource bounds, result sources, no hidden plugin loading during reads | Contract/acceptance |
| 26 | Model administration, versioned protected secret references, separate runtime/agent model access | Contract/implementation |
| 27 | Managed local model runtime ownership/files/readiness/download/GPU/memory/shutdown, only if this mode is enabled | Contract/implementation |
| 28 | MCP saved definitions, inventory/descriptor verification, contract updates, server shutdown | Contract/implementation |
| 29 | Versioned instruction registry, scope/order/variables, public-model restrictions | Contract/implementation |
| 30 | Own-material saves with expected version/conflict; evidence remains immutable | Contract/implementation |
| 31 | Derived control metadata, exact input/equalizer, volume units, scoped measurements, four-axis chart data | Contract/UI |
| 32 | Rust/Python delivery split, pre-negotiation bootstrap limit, damaged-stream shutdown, control queues, read-only command reconciliation | Contract/implementation |
| 33 | Real-executor card projections, domain freshness, unsubscribe without cancellation, distinct role/profile/invocation | Contract/implementation |

## Appendix A. Draft command catalogue

Status distinguishes confirmed surface, confirmed route with command proposed,
and contract not established. None of these names claims an implemented IPC API.

| Command | Class | Scope/owner | Status |
|---|---|---|---|
| `state.subscribe`, `state.unsubscribe` | Control | Python subscriptions, Rust delivery, domain-owned bases | Proposed; unsubscribe does not cancel work |
| `state.snapshot` | StateSync | Domain owners | Proposed |
| `state.progress.request` | Control | Owner's server projection | Proposed; response is not a domain event |
| `events.page` | Evidential | EventStream.page; assessment_sha256 + after_sequence | Surface exists; command proposed |
| `c1.evidence.read` | Evidential | C1EvidenceContext | Surface exists; command proposed |
| `task.submit` | Mutation | runner_composition.execute_gold_project_run | Route exists; command proposed |
| `invocation.cancel` | Mutation | AgentExecutionPort.cancel / InvocationStore | Surface exists; command proposed for one invocation |
| `task.control` | Mutation | Task/coordinator owner | Whole-task contract open; no invocation substitution |
| `approval.respond {grant / refuse / revoke}` | Mutation | stage10_composition | Grant/revoke exist; refuse semantics new |
| `permissions.set_mode` | Mutation | Policy owner | Proposed, MODE-3 |
| `message.send` | Mutation | Conversation history | Contract open |
| `project.tree.read`, `material.read` | Read | Reader contract to be established | Contract open |
| `budget.get` | Read | Admission/accounting owners, section 12.3 | Composite projection proposed; distinguish limits, measurements, reserves |
| `core.ping` | Control | Machine core process through shell | Proposed; not domain freshness |
| `command.status` | Read | Command's domain owner | Proposed; never creates claim |
| Handshake: identity/versions → profile → readiness | Control | Shell/core | Proposed, BOOT-7 |
| Protocol shutdown | Control | Core lifecycle | Proposed, CRASH-15 |
| `memory.policy.read` | Read | MemoryOwner.bound_configuration; metadata from configuration/policy | Basis exists; command proposed |
| `memory.policy.validate` | Read | resolve_parameters plus MEM-3 invariants | Partly supported; open condition 12 |
| `memory.policy.preview` | Isolated calculation | New core surface | Contract open |
| `memory.policy.accept` | Mutation | MemoryFactory.reassess → owner.rebind | Route exists; command proposed |
| `memory.history.read`, `memory.learning.read` | Read | Memory owners | Proposed |
| `memory.storage.usage`, `memory.storage.plan` | Read | Storage owner | Contract open |
| `memory.storage.accept` | Mutation | Storage owner | Contract open |
| `memory.metrics.read` | Read | Metric contract to be established | Contract open; never executes work |

### A.1. Connection administration

`*.read`/`*.validate` load no plugins; `*.probe` is bounded execution;
`*.accept` changes configuration through an established owner. Define durable
administrative records; do not arbitrarily assign this ownership to AgentRegistry,
LLMGateway, or Rust.

| Commands | Result/class | Existing basis | Remaining contract |
|---|---|---|---|
| `agents.discover` | Factory metadata without loading; read | discover_adapter_factories | Projection |
| `agents.profiles.read` | Authorized definitions, active config, separate admission facts | configuration, AgentProfile, CapabilityAdmission | Reader and administrator |
| `agents.profile.validate` | Structural/value errors without network/process | validate_configuration, profile_from_dict, RuntimePolicy | External command |
| `agents.profile.probe` | Definition-bound environment/scenario check | Relevant factory and approved execution/runtime path | Scenario, owner, bounds |
| `agents.configuration.accept` | Profiles/admission/preferences for future selection | capture_definition, runtime checks, registry assembly | Durable administration |
| `models.connections.read`, `models.connection.validate` | Explicit-target settings/restrictions | ModelConnection or LLMGatewayConfig | Projection/external validation |
| `models.connection.probe` | Explicit nonsensitive control scenario | Broker/capture or gateway by purpose | New scenario; not automatically llm/smoke.py |
| `models.configuration.accept` | Versioned connection/protected secret reference | Relevant model/profile owner's configuration | Administration |
| `secrets.put`, `secrets.status`, `secrets.remove` | Protected write/availability/removal; no value in ordinary reads | Application secret mechanism | Owner/mechanism; removal respects active references |
| `tools.configuration.read`, `tools.configuration.validate` | Saved servers/tools/contracts without startup | ToolConfiguration, ToolContract | Administration |
| `tools.server.probe` | Explicit inventory/descriptor verification | Bounded McpToolTransport/McpToolPort scenario | Does not automatically admit |
| `tools.configuration.accept` | Owner acceptance, including memory binding where relevant | MemoryConfiguration.tools, MemoryOwner/MemoryFactory | Separate acceptance, no MEM-7/8 bypass |
| `instructions.read`, `instructions.validate` | Definitions and template checks | prompt_template as low-level substitution only | Registry, mandatory fields, owner |
| `instructions.accept` | Immutable instruction version assigned to a scope | INST-1–3 | Proposed; grants no capability/habit |
| `material.save` | Own edit saved against expected base | READ-3; file-change owner separately established | Proposed; no direct renderer filesystem writes |

### A.2. Producer messages

subscription.confirm, snapshot replies, state.event, state.progress, command
results, and bootstrap confirmations are producer messages, not user domain
commands. Carry required session/project/request identities in one approved
schema set. Delivery creates neither another execution nor another decision.

## Appendix B. UI panel mapping

| Panel | Surfaces |
|---|---|
| Left: project/materials | project.tree.read, material.read (open); specialized c1.evidence.read |
| Center: task/execution/review | task.submit/control, specific invocation.cancel, StateSync, material.read/save, exact-proposal diff |
| Top: agent strip | Actual StateSync/Live work instances linked to task/run/attempt/invocation, not installed profiles |
| Right: main agent/permissions | message.send, approval.respond, permissions.set_mode and distinct mode states |
| Bottom: Decisions and rationale | Authorized reads of EventStream, C1, InvocationStore, Gateway, court reports, grants, capture; no invented global sequence |
| RU/EN | In-place presentation preserving drafts/focus |
| Memory and learning | memory.policy/storage/metrics; preview, durable acceptance, MEM-13–18 controls/chart |
| Agents | agents.*; definition/probe/admission/eligibility/selection, ordered preferences |
| Models and limits | models.*, protected secrets.*, budget.get; explicit target/enforcement |
| Tools | tools.*; servers versus tools, declared contracts versus probes |
| Skills and instructions | instructions.*; content versions/scope without rights granted by text |

## Appendix C. Open-source precedents

These precedents support the feasibility/form of requirements. They are not
dependencies imported into Synapse. Links identify the original public sources;
their guarantees remain limited as stated here.

| Rule | Public source | Relevant precedent and limits |
|---|---|---|
| SYN-4–6 | [etcd watch API](https://github.com/etcd-io/etcd/blob/main/api/etcdserverpb/rpc.proto) | Explicit revision boundary; etcd start_revision inclusive, this bridge P0 exclusive/Ps inclusive |
| SYN-12 | [etcd API guarantees](https://etcd.io/docs/v3.5/learning/api_guarantees/) | Progress establishes delivered prefix without events; server controls cadence |
| SYN-15 | [etcd watch API](https://github.com/etcd-io/etcd/blob/main/api/etcdserverpb/rpc.proto) | Compacted watch boundary cancels; resnapshot instead of restarting from the same unavailable revision |
| SYN-8 | [KafkaConsumer](https://kafka.apache.org/40/javadoc/org/apache/kafka/clients/consumer/KafkaConsumer.html) | Committed durable position is recovery basis |
| SYN-16 | [Kafka log compaction](https://kafka.apache.org/40/documentation/#compaction) | Null tombstones explicitly delete keys; finite delete.retention.ms |
| SYN-14 | [Raft paper, section 5.1](https://raft.github.io/raft.pdf) | Stale-term handling motivates discarding late epochs without resetting healthy state |
| CMD-1–14 | [Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests) | Pre-request key, saved result including 500, mismatch refusal, ≥24h retention. Does not establish CMD-20 intent identity; Stripe key removal permits reuse whereas this bridge forbids reviving old commands |
| APR-5–9 | [RFC 6749 section 4.1.2](https://www.rfc-editor.org/rfc/rfc6749#section-4.1.2) | Single-use authority; reuse denial MUST, prior-token revocation SHOULD |
| CMD-2, COMP-6 | [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785) | Deterministic JSON canonicalization |
| COMP-11 | [RFC 8785 section 3.1](https://www.rfc-editor.org/rfc/rfc8785#section-3.1) | No duplicate keys, IEEE 754 numbers, strings for higher precision; never hash after losing original precision |
| SEC-5 | [Tauri CSP](https://v2.tauri.app/security/csp/) | Permit required IPC and minimize other sources; demo connect-src none is not product policy |
| CRASH-9–11 | [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects) | Final-handle group termination, child inheritance with exceptions, nested jobs from Windows 8; verify actual launch paths |
| CRASH-12 | [systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html), [systemd.kill](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html) | ExitType=main (setting added v250), KillMode=control-group; manager performs SIGTERM/deadline/SIGKILL cleanup |
| CRASH-14 | [Linux cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html) | populated proves live membership absence; cgroup.kill requires invocation (Linux ≥5.14) |
| CMD-17 | [Kubernetes API concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/#updates-to-existing-resources) | resourceVersion detects stale updates and returns 409, not silent overwrite |
| CMD-19 | [etcd issue 16659](https://github.com/etcd-io/etcd/issues/16659) | API/apply quota checks can produce a client error after a committed write; reconcile outcome |
| MEM-6 | [Terraform saved-plan discussion](https://discuss.hashicorp.com/t/question-on-error-saved-plan-is-stale/52912) | Changed state serial invalidates a saved plan |
| MEM-9 | [Oracle ZFS quotas](https://docs.oracle.com/cd/E19253-01/819-5461/gazvb/index.html) | Reject quota below used/reserved space; Synapse additionally requires verified permissible reclamation |
| MEM-11 | [NixOS manual: rollbacks](https://nixos.org/manual/nixos/stable/#sec-rollback) | Configuration generations retain history; a precedent, not identical memory rollback semantics |

## Appendix D. Source-code integration map

The complete translated appendix, including object distinctions, execution
chains, settings, limitations, panel mapping, **all 130 file entries**, and the
file index, is in [UI_CODE_MAP_V1.md](UI_CODE_MAP_V1.md) and its linked documents.
The source's eight acceptance references are preserved separately as
[development-only sources](development-reference/ACCEPTANCE_SOURCES.md), outside
product UI integration and dependencies. This split clarifies the boundary
without omitting source content.
