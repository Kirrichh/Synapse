# Machine control profile v1

Version 1.0. Implementation contract for the first P1 control session.
This refines BOOT/TR/COMP and the control portion of OC-18/20/32. It does not
close those conditions for future domain commands or installed-product evidence.

## Authority and scope

The product schema is `synapse/machine/schemas/envelope.schema.json` (JSON Schema
2020-12). `synapse/machine/release_profile.json` owns release resource values.
Both are packaged product contracts, independent of acceptance fixtures.
Python uses the schema directly; the frontend derives types and standalone Ajv
validators at build time. Rust validates the same schema at its native boundary.

Entry: `python -m synapse machine`, through `synapse.__main__` and `synapse.cli`.
Binary stdin/stdout carry frames; stderr carries bounded nonsecret diagnostic
codes. There is no TCP server, Gold entry, domain scheduler, user-supplied schema,
or renderer-selected interpreter. The installed shell must use its bundled core.

Profile `synapse.control.v1`, protocol version 1, envelope `schema_version: "1.0"`
advertises only the `control` command class. `domain_ready` is always false.
This is an explicit capability boundary: no task, model, project, memory, approval,
or agent command is authorized by this control profile. The separately opened
application profile below supplies its own authentication and owner enforcement.

## Scoped application profile v1

After control readiness, the same canonical byte pipe accepts the closed
`synapse.machine.domain/v1` envelope. `access.open` explicitly requests
`synapse.application/v1`; it issues a single-use installation/connection/nonce
challenge, not workspace authority. The native instance proves its registered
key with Ed25519. `access.grants` lists current exact delegations. The control
`ready` message remains control-only: it never authenticates an application.

`domain.schema.json` owns this extension and references the command owner's
binding and reconciliation schemas. The endpoint exposes invocation inspection,
snapshot/changes, cancellation, scoped status and delivery; there is no generic
owner forwarding. Every read, acceptance, replay and delivery rechecks current
authority and actual invocation membership. See [access ownership](WORKSPACE_ACCESS_CONTRACT.md).

The native instance owns protected intents and their original command IDs.
Prepared resubmissions reconcile status before sending; only scoped absence and
an explicit resend allow the original command to be sent. Domain data travels
through a separate one-outstanding-result Channel with exact subscription and
request acknowledgment. Renderer replacement discards delivery resources, not
accepted commands or saved intents. Control and application admission have
separate bounded native queues; at most one bounded application operation
precedes queued stop/close, and heartbeat has an independent deadline.

The core fixes source classes: `invocation.state` is StateSync,
`invocation.record` is Evidential, and `connection.health` is Live. A subscription
confirms its core-issued session, epoch, scope/filter and generation/P0 where
applicable. State requires a snapshot Ps >= P0 before collecting changes; all
subsequent ranges come from the owner's journal. Collection and consumption are
separate bounded operations. StateSync loss requires a new snapshot/epoch,
Evidential loss requires rereading, and Live loss is marked. Each consumption
rechecks authorization before writing its response. Unsubscription and native
Channel replacement release only delivery. A lost response never means a
command was absent. Oversized domain results produce a named bounded refusal;
the owner's durable result remains available for authorized reconciliation.

The current UI performs explicit fresh observations, using a new scoped snapshot
for each refresh. It distinguishes last observed connection response from
invocation execution state. This does not implement the complete P2 continuous
multi-domain reducer, overlap reconciliation, or all future domain sources.

## Wire representation and resource bounds

Each frame is a four-byte unsigned big-endian payload length, then UTF-8 JSON.
No newline separator, BOM, stdout logging, delimiter search, or damaged-stream
resynchronization is permitted. Partial reads/writes and multiple frames per
read are ordinary byte-stream behavior.

| Parameter | Released value and reason |
|---|---|
| Bootstrap / maximum payload | 65,536 bytes; bounded control metadata, not bulk documents |
| Minimum negotiated maximum | 1,024 bytes; enough for every fixed control response |
| JSON nesting | 32 containers, checked before JSON object construction |
| Incoming / result-control queue | 16 bounded frames each; independently bounded from projection storage |
| Projection / registered-stream capacity | 16 payloads / 32 streams; scheduler limits, not advertised domain capabilities in this control profile |
| Control scheduling burst | At most 8 queued control items before an available projection/loss notice; one in-flight write remains subject to the output deadline |
| Handshake deadline | 5 seconds total, never extended by invalid requests |
| Individual output deadline | 2 seconds; an unread output pipe cannot hang the process indefinitely |
| Shell shutdown deadline | 5 seconds; expiry requires explicit forced-close handling |

Selection may lower the frame maximum but cannot exceed either announcement.
Enforce the header limit before allocating the payload; also check prefetched
frames against the selected limit. Queue saturation applies bounded backpressure.
The shared scheduler has separate result/control and projection storage. Live
overflow marks the next delivery for that stream with a gap. StateSync overflow
invalidates the epoch and requires a snapshot; Evidential overflow requires a
reread. An invalidated stream cannot silently resume. Loss notices have bounded
per-stream slots independent of the full payload queue. Unsubscription releases
delivery resources only. These are scheduling contracts, not authority or owner
acceptance. This control profile exposes no domain subscriptions; negotiated
domain routing and end-to-end recovery remain required for TR-12 completion.

Reject invalid UTF-8, malformed JSON, duplicate keys at every nesting level,
unpaired Unicode surrogates, nonfinite constants, and excessive nesting. This
control profile uses only JSON integer tokens within ±9,007,199,254,740,991;
decimal/exponent tokens and negative zero are rejected before conversion. Zero
has the single representation `0`. This deliberately narrow
numeric contract prevents precision loss and is not a general JCS implementation
for future domain parameters. Hashing and decimal domain semantics remain CMD/P3
work. Strings and document contents are not interpreted as numbers or instructions.

## Handshake and state

1. The core sends `hello` identifying its canonical runtime version, profile,
   protocol range, and maximum frame length. The shell sends its own `hello`.
2. The core accepts only the shell role, the supported profile, and a range
   containing version 1. No request receives execution authority at this point.
3. The shell sends `select` with profile, version, frame limit, `utf-8`, and
   `u32be`. The core checks the selection against both peers' bounds.
4. The core sends `ready` with the selected values, `command_classes: ["control"]`,
   and `domain_ready: false`. Only then may control requests succeed.

An early request is refused with `NOT_READY` and is not queued for later execution.
An incompatible profile/version or a wrong handshake sequence closes the session.
The shell checks the core identity against the bundled artifact manifest; a process
self-report is not the installed-artifact identity required by ACC-5.

## Requests and responses

Each request has `schema_version`, `type: "request"`, a bounded `request_id`,
`method`, and `params`. Control methods require an empty parameter object. The
request ID correlates a control reply only; it is not a domain intent, command ID,
invocation claim, authorization token, or retry mechanism for effects.

| Method | Result and lifecycle |
|---|---|
| `machine.ping` | `{ "status": "alive" }`; transport liveness only |
| `machine.status` | Selected profile/limit, control state `ready`, and `domain_ready: false`; no store creation or domain inspection |
| `machine.shutdown` | Flush `{ "status": "stopped", "pending_commands": 0 }`, then exit successfully; zero describes pending machine-handler commands, not completion or rollback of previously accepted domain work |

Every result repeats the request ID and method. Unknown methods return
`METHOD_UNSUPPORTED`; nonempty control parameters return `PARAMS_INVALID`.
There is no generic forwarding or fallback. Repeated ping/status requests are
read-only; there is no durable domain deduplication claim in this profile.

Every error carries a declared code and `fatal`; it contains no raw input,
exception text, file path, or secret. Echo a request ID only after its envelope
was validated. A schema-invalid complete JSON message is refused without an
effect; the connection remains usable. An unsupported envelope version is fatal.
Framing/JSON corruption is fatal and no subsequent frame is dispatched. Clean
input EOF without protocol shutdown is `PIPE_CLOSED`, not a successful shutdown.

The process returns 0 after acknowledged protocol shutdown and 2 after a protocol
or transport failure. If a peer no longer reads, an error reply is best effort
within the output deadline; stderr still reports only its nonsecret error code.
The shell must retain uncertainty about future domain operations independently.

The native shell validates error envelopes and their correlation before displaying
the declared code. During a request, only its exact `request_id` can establish a
refusal; an unbound or foreign error fails the session as `PROTOCOL_ERROR`.
Negotiation accepts only unbound errors and terminates negotiation with their
declared code. A nonfatal, correlated refusal retains the control session and is
published alongside native `ready`; it is neither a successful result nor a
shutdown acknowledgment. A fatal refusal closes the session with its code.

Ordinary Stop/Close still requires an acknowledged shutdown and successful process
exit. A refusal leaves the window open and permits another explicit Stop. Protocol
failure and process loss retain their own codes; a transport or process-exit timeout during
shutdown maps to `SHUTDOWN_TIMEOUT`. After session loss, a later Close preserves the
known failure and does not infer a successful shutdown from process disappearance.
Confirmed shutdown, or closing before launch, can still exit normally.

## Acceptance and remaining integration

External acceptance exercises exact bytes, fragmentation, malformed streams,
negotiation, refusal, bounded deadlines, status, shutdown, and canonical CLI use.
One development-only corpus checks Python, Rust, and generated frontend validators.
It is never a product resource or a runtime status source.

Windows Job containment, Linux user-service containment, bundle identity, shell
caller identity, production CSP, and real-window behavior require their own
evidence. This control contract does not waive any P1 or P7 platform requirement.
