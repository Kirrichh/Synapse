# Universal agent execution in Gold

Implementation status: integration work in progress. Adapter transport support
is not evidence that every output domain is admitted by Gold.

## Ownership

The canonical application action is `python -m synapse project run`. Gold owns
the governing task, source resolution, knowledge admission and replay, accepted
plan, context construction, persistence and fresh execution authorization.
Only after those steps does Stage 10 call `AgentExecutionPort`. The adapter
receives the exact task and separate local-information channel built by Gold.
The adapter neither builds a replacement task nor approves its own output.

The coding flow remains:

1. Gold builds and persists the plan and worker context from its admitted evidence.
2. Gold revalidates the plan and issues `SideEffectAuthorization`.
3. The Stage 10 bridge validates the persisted context, plan and authorization
   again, binds their exact bytes to the execution journal, and calls the port.
4. The selected adapter produces a typed `PatchCandidate`.
5. Stage 10 verifies delivery; existing C1, Stage 12, publication and memory
   owners independently consume the retained candidate.

An adapter's `COMPLETED` means that execution completed. It does not mean Gold
`FULL`, task correctness, knowledge admission or memory publication.

There is no artifact-task shortcut from the CLI to an external agent. Docling
cannot become a runnable Gold task merely by installing its package. Its domain
needs an admitted Gold task/output/verification profile in the same lifecycle.
An unsupported domain is refused rather than encoded as a coding patch.

## Contracts and runtime components

| Concern | Implementation |
| --- | --- |
| Selection | Exact operator-admitted profile and capabilities; deterministic preference, then profile ID |
| Serialization | Strict JSON; duplicate keys and non-finite numbers refused |
| Output shape | `jsonschema` Draft 2020-12, with external schema resolution disabled |
| Typed coding output | `PatchCandidate` |
| Typed document output | `DocumentObservationSet`, with per-source identity and failure accounting |
| Additional domain schemas | `SchemaOutputCodec`; built-in codecs cannot be overridden |
| Retention | SQLite terminal publication, content hashes, retained outputs and process events |
| Invocation ownership | `filelock`; an unfinished invocation is not silently dispatched again |
| Local runtime | Shared subprocess supervisor, `psutil` observations, timeout, bounded streams and tree termination |
| Third-party isolation | Bubblewrap/libseccomp/prlimit or a digest-pinned OCI image |
| A2A | Official `a2a-sdk` 1.1.2 client; pinned AgentCard and endpoint, retained task ID, explicit reconciliation |
| ACP | Official `agent-client-protocol` 0.12.1; scoped filesystem callbacks inside the isolated runtime |
| MCP tools | Official `mcp` 2.2.0; admitted tool descriptors and input/output schemas |
| Documents | `docling-agent` 0.6.0 and Docling 2.126.0 in an isolated child process |

The private Synapse STDIO framing is a four-byte length followed by UTF-8 JSON.
It is not called MCP or ACP. Their official SDKs own their protocol framing.
Library stdout is redirected to diagnostics while the native driver writes its
single protocol response separately.

Mini remains a trusted existing integration for its local broker and local-edit
boundary. New third-party STDIO/ACP/Docling factories cannot select that trusted
host-process mode. Declared read-only artifacts are not arbitrary filesystem
access. Network access for local third-party profiles is denied by the runtime.

## Operator configuration

Install only the runtimes needed by the project:

```sh
python -m pip install -e '.[gold-worker]'
python -m pip install -e '.[agent-a2a,agent-acp,agent-mcp,agent-docling]'
```

The second command does not install Mini. The Docling extra is enabled on
Python 3.11 and newer. Offline model weights must be installed, fingerprinted
and explicitly mounted before a Docling execution. An unavailable sandbox or
model is a refusal/failure, not permission to use an unisolated fallback.

`synapse.agent.configuration/v1` contains explicit `profiles` and ordered
`preferred_profiles`. Each definition binds:

- The adapter factory, native configuration and their hash.
- The executable, trusted adapter sources and installed dependency identities.
- The concrete `AgentProfile`, runtime policy and resource limits.
- A separate capability-admission record, its admitted subset and retained
  evidence references.

`capture_definition` fingerprints already supplied runtime and evidence files.
It does not run tests, invent acceptance evidence or automatically approve a
new capability. Discovered entry points in `synapse.agent_adapters` do not enter
the resolver until operator configuration admits their exact identity.

For coding, experiment input v4 replaces `worker` with `agents`. Its frozen
input v7 binds selection and the same automatic target resolution, planning and
project-memory owners used by current Gold. Historical experiment and worker
schemas retain their meanings. The legacy Mini configuration is translated by
`MiniAgentAdapter`; Gold has no direct Mini dispatch.

An existing automatic coding declaration can be migrated explicitly during
operator configuration. The evidence file must come from the operator's own
runtime acceptance; this example does not create or certify that evidence:

```python
import json
from pathlib import Path
from synapse.agents.configuration import AGENT_CONFIGURATION_V1, capture_definition
from synapse.agents.mini_adapter import MiniAgentAdapter
from synapse.experiments.gold.run_inputs import EXPERIMENT_INPUT_SCHEMA_V4
from synapse.experiments.gold.stage10_composition import decode_worker_configuration

path = Path("experiment.json")
declaration = json.loads(path.read_text())
native = declaration.pop("worker")
native["input_profile"] = "mini-2.4.6-local-edit-proposals/v4"
profile = MiniAgentAdapter(config=decode_worker_configuration(native)).profile
definition = capture_definition(
    factory="mini", profile=profile, native=native,
    distributions=("mini-swe-agent", "litellm", "openai"),
    evidence_paths=(Path("admission/mini-runtime.json").resolve(),),
    capabilities=("repository.edit",),
)
declaration["schema_version"] = EXPERIMENT_INPUT_SCHEMA_V4
declaration["agents"] = {
    "schema_version": AGENT_CONFIGURATION_V1,
    "profiles": [definition], "preferred_profiles": [],
}
path.write_text(json.dumps(declaration))
```

The configured declaration still enters the existing `project run` action and
its existing operator approval step. Selection of the profile does not authorize
execution. No Mini profile is added when the operator has supplied other profiles.

## Boundaries still requiring domain integration

The current canonical Gold consumer is a governed coding consumer. Its C1
verification and outcome proof are specific to controlled repository changes.
Generic document results must not be sent through that proof as fake patches
or admitted through a parallel document runner.

Docling 0.6.0 also ignores arbitrary extra extraction arguments. This integration
therefore declares private local information unsupported instead of pretending
that passing an ignored keyword uses memory. Its extraction task must contain
an explicit structured template. Per-file failures are read from the native
results; a textual extraction report is not used as proof of success.

Remote A2A and ACP profiles currently refuse the local-information channel.
A remote task's cancellation is cooperative; an unconfirmed outcome remains
`EXECUTION_STATE_UNKNOWN`. Resume reconciles a retained remote task ID and does
not send a replacement task. Missing provider token observations remain
unavailable, never zero-cost evidence.

New frozen profiles retain actual provider captures when their adapter supplies
the existing capture owner. The new input version does not yet claim the complete
Stage 15 resource measurement profile: absent operation measurements keep economic
claims blocked. Local process resource events remain in the execution journal.
Bubblewrap combines per-process OS limits with sampled process-tree observations;
those samples are not proof of a hard aggregate cgroup limit. OCI uses container
memory/process limits and a one-core wall-clock CPU bound.

## Verification boundary

Tests and scenario programs live in the acceptance layer. Product modules do
not import them, embed fixture branches or use test outcomes to make Gold
correctness decisions. A transport acceptance result does not establish a Gold
outcome or production sign-off.

Observed during this implementation:

- 10 existing Stage 10/11 delivery and recovery scenarios passed after fixes.
- Three existing real-Mini scenarios passed: automatic task, multiple targets
  with independent C1, and project-memory lifecycle.
- The new admitted-profile scenario passed through canonical Gold, independent
  C1, committed publication and resume without duplicate effects (180.99 seconds).
- Both official A2A and MCP SDK acceptance files passed (1.00 second combined).
- Four journal/selection acceptance cases passed: retained artifact restore,
  unknown dispatch without retry/fallback, capability/memory refusal and concurrent
  invocation ownership (0.19 second).
- 549 existing ownership/dependency checks passed. The ownership DAG checker
  found no forbidden edges.
- Bubblewrap could not establish its required namespaces in the current work
  environment. No unisolated Docling/ACP fallback was used to manufacture a
  passing runtime result.

Further verification results and domain readiness must be recorded as observed,
not inferred from installed SDKs or successful imports.

Heavy acceptance stays outside the product in independently runnable files:

```sh
python -m pytest -q acceptance/stage4/stage16/test_agent_profile_gold_acceptance.py
python -m pytest -q acceptance/agents/test_a2a_sdk_acceptance.py
python -m pytest -q acceptance/agents/test_mcp_sdk_acceptance.py
```

CI schedules the Gold profile, A2A and MCP files as separate matrix jobs with
`fail-fast: false`. Each uses its own temporary state and loopback port, so they
can run concurrently. Fast journal/contract cases remain in the fast job.
These results do not establish live isolated Docling/ACP execution or a Gold
document-domain verification profile.

## Upstream integration references

- [A2A specification](https://a2a-protocol.org/latest/specification/) and
  [official Python SDK](https://github.com/a2aproject/a2a-python).
- [ACP introduction](https://agentclientprotocol.com/get-started/introduction).
- [MCP tool protocol](https://modelcontextprotocol.io/specification/2026-07-28/server/tools).
- [Docling-Agent implementation](https://github.com/docling-project/docling-agent).
