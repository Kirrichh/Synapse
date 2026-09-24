# Universal external-agent execution boundary

## Status

This document describes the product boundary introduced on the active Gold branch.
It does not claim that every third-party agent or protocol has already passed
acceptance. Historical Gold run schemas keep their original meaning.

## Architectural rule

Synapse has one neutral external-agent execution port. Agent-specific runtimes
translate at adapters; they do not acquire Gold authority.

```text
Gold/task consumer
      |
      v
AgentExecutionRequest
      |
      v
AgentRegistry -- deterministic capability/output/media eligibility
      |
      v
AgentExecutionPort
      |
      +--> StdioAgentAdapter --> local protocol-conforming agent process
      |         (LOCAL_BROKER profiles reach their model only through
      |          Synapse's per-invocation model broker)
      |
      +--> installed plugin adapter --> arbitrary native/A2A/OCI runtime
      |
      v
AgentExecutionResult + typed AgentOutputEnvelope
      |
      v
consumer-specific verification/admission/publication
```

The universal part is lifecycle and transport evidence, not output meaning.
`PatchCandidate`, document observations, measurements, source collections and
future output types remain separately versioned schemas.

## Coding agents

Every coding agent is an ordinary admitted profile; Synapse contains no built-in
coding agent and no agent-specific Gold path. The historical Stage 10
`WorkerInvocation` and `WorkerCandidateResult` records survive as the coding
consumer's boundary, translated by `AgentBackedWorkerTransport`.

A coding agent for Stage 10 supplies one admitted registry profile with:

- `repository.edit` capability;
- `synapse.agent.output.patch-candidate/v1` output support.

A local STDIO profile may declare the Synapse local-edit protocol it speaks
(`native.protocol`). Such an agent returns only its model's raw proposal;
Synapse interprets it over the delivered bytes and decides every route. A
profile with network `LOCAL_BROKER` declares `native.model_access`; Synapse
opens a loopback model broker for each invocation, keeps the provider
credential, captures every physical call through the caller's accounting port
and, while local information is delivered, forwards only the protocol's public
conversation. Such a profile runs as an operator-admitted `TRUSTED_PROCESS`.

Runs frozen with the retired built-in worker declaration remain readable as
records. Their agent no longer exists, so they are refused for dispatch or
resume with an explicit configuration error.

## Neutral contracts

`AgentProfile` describes the admitted execution identity:

- agent and adapter identity/version;
- transport kind;
- verified capability vocabulary;
- accepted artifact media types;
- supported typed output profiles;
- local-information policy;
- effect classes;
- provider/model identity when relevant.

An agent's self-declared capability is not automatically an admitted capability.
Only profiles explicitly placed in `AgentRegistry` are selectable.

`AgentExecutionRequest` keeps separate channels for:

- task bytes;
- local-information bytes;
- read-only physical artifact references;
- required capabilities/output profile;
- consumer scope where one exists.

A document/CAD/data task may have an empty repository scope. Physical artifacts
carry exact SHA-256 and byte length bindings.

`AgentExecutionResult` reports execution facts only. Status values such as
`COMPLETED`, `ERROR`, `TIMEOUT` or `REFUSED` do not mean Gold success or verified
truth. The result contains typed output envelopes, usage, diagnostics, report and
request-bound delivery evidence.

## Deterministic selection

`AgentRegistry.select()` requires all of the following:

1. requested capabilities are a subset of the admitted profile capabilities;
2. the requested typed output profile is supported;
3. local information is accepted by policy when present;
4. every artifact media type is accepted.

If no profile qualifies, selection fails. There is no implicit fallback agent.
Registry ordering provides a deterministic tie-break for an already operator-
admitted set. A future policy owner may narrow/rank the set before registry
construction without moving authority into the adapter.

## Plugin discovery

Installed Python adapter packages may expose factories through the standard PyPA
entry-point group:

```toml
[project.entry-points."synapse.agent_adapters"]
my_agent = "my_synapse_adapter:Factory"
```

Discovery is not admission. `discover_adapter_factories()` returns metadata only.
`load_admitted_adapter()` loads exactly one entry-point name explicitly selected
by frozen/operator-approved configuration and validates the returned adapter.

Third-party packages therefore do not become executable merely because they are
installed.

## Generic local STDIO profile

`StdioAgentAdapter` is the reusable local process transport. It sends one exact
UTF-8 JSON request to stdin and expects one exact UTF-8 JSON response on stdout.
Child logs belong on stderr. The adapter:

- launches exact argv tokens;
- uses a bounded timeout;
- passes a reduced environment plus explicit configured values;
- validates invocation identity;
- bounds stdout;
- validates base64url output payloads;
- refuses output schemas outside the admitted profile;
- records delivery evidence itself rather than trusting the child to mint it.

This transport is process isolation, not an OS/network sandbox. An OCI adapter
may provide stronger filesystem/network/resource isolation behind the same
`AgentAdapter` contract.

## A2A and MCP

A2A is treated as an external-agent transport/profile concern, not as Gold's
internal authority schema. An A2A adapter should translate A2A Agent Card/task/
artifact semantics into the neutral Synapse contracts.

MCP is a tool/resource boundary for agents. MCP tools do not become agents and do
not receive Gold task/admission authority merely because an agent can call them.

Neither external protocol is allowed to redefine Synapse task success, admission,
publication, replay or memory ownership.

## Adding a new agent

A normal new integration should require only:

1. install/freeze the third-party runtime;
2. implement an `AgentAdapter` (or use the generic STDIO adapter through a thin
   native wrapper);
3. expose an exact `AgentProfile`;
4. validate and admit the profile under operator policy;
5. register it;
6. let a consumer request capabilities and a typed output profile.

Gold core must not grow an `if agent == ...` branch for each integration.

## Current boundary

New Gold runs select their coding agent through experiment input v4 (`agents`),
independently of whether task targets are explicit records or resolved
automatically. Older frozen-input schemas stay readable for replay and memory;
their built-in worker declaration is not executable.

Document/CAD/browser task profiles should use new versioned task/input schemas
rather than reinterpret historical coding-worker fields. This preserves replay
and avoids a second canonical execution path.
