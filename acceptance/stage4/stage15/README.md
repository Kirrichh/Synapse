# Stage 15 acceptance contract

Stage 15 implements normative §§30–31 and S4-ACC-RECON-01. Production owners
live in `synapse/experiments/gold/stage15/`; module boundaries follow source
contracts, lifecycle and dependency direction, with no numeric LOC gate.
This file is the maintained acceptance contract, not the disposable research plan.

## Canonical execution and capture

The entry point remains `python -m synapse` → `synapse.cli.main`. Existing
`project run`, `approve --resume-run` and `project resume` drive the same run
controller. The experiment input v2 selects one accounted worker profile:
`mini-2.4.6-litellm-openai-chat/v1`. Install it with
`python -m pip install -e '.[gold-worker]'`.

The input's `worker.accounting` contains `profile`, the provider `endpoint`
(`/v1/chat/completions`) and `credential_env`. `worker.command` names the exact
installed Mini console executable. Credentials stay in the parent process and
are not frozen in experiment records. Mini 2.4.6, LiteLLM 1.100.0 and OpenAI
2.54.0 are pinned; their installed source identities are frozen for each run.
The parent-owned loopback transport exists only inside that worker invocation.
It is neither another entry point nor an independently operated service.

Capture persists a physical-call start before HTTP dispatch and raw response
plus canonical observation before delivery to the worker. Mini's actual model
plugin binds logical queries to physical attempts. Retries create distinct call
IDs; equal prompts are never deduplicated into one call. The retained original
trajectory and the existing Stage 3A/C1 aggregate are independent comparison
sources. No aggregate is expanded into fictional per-call records.

The supported execution profile is non-streaming OpenAI Chat. Parsers for
Gemini and Anthropic declare their inclusion rules; parser support alone is not
an executable provider profile. Adding a transport requires real interception
acceptance and a new frozen profile. The Stage 3A writer remains unchanged.

## Evidence and authority

| Evidence | Required behavior |
| --- | --- |
| Telemetry report | Physical inventory, raw responses, canonical calls, actual Mini trajectory and durable C1 aggregate agree; missing usage remains unknown. |
| Artifact report | Original Stage 14 reconstruction plus C1/oracle source revalidation; validate physical hashes, endpoints and mandatory lineage dependencies. |
| Snapshot report | Read historical index/integrity roots, committed input boundary, retained lifecycle/provenance/taint prefixes, policy/bindings and repository revision. |
| Outcome | Correctness is decided by the existing verifier; v6 attaches an immutable pre-outcome telemetry assessment. Later loss produces new findings without rewriting that outcome. |
| Observation manifest | An exact retained cut with expected calls, observations and events; independent report statuses remain visible. |
| EventStream | Deterministic sequence, hash chain, typed external payload refs, detectable gaps and cursor binding; no command, lifecycle, admission or publication capability. |

Token input/output counts include their declared cache/reasoning subsets.
Provider-reported totals are stored separately from component sums. Stage 3A
worker totals are comparison evidence and are never added to physical totals.
Provider cache, deterministic replay and Stage 13 semantic reuse remain separate.
The existing Stage 13 `MechanismUseRecord` remains the only owner of observed reuse.

Provider capture has a process clock domain and monotonic durations. Historical
replay observations and derived phase events explicitly declare unavailable
execution clocks. Verification observations retain actual command durations and
oracle duration where C1 reports them. Infrastructure axes stay separate:
unknown CPU, money, I/O or wall time are not zeros. Current retained capture bytes
are a storage measurement, not cumulative write I/O or economic savings.

EventStream is a retained projection of domain transitions. It does not assert
that projection order is wall-clock order or provide live phase notifications.
NOT_REACHED requires terminal domain evidence; missing evidence produces GAP.

## Recovery and retention

The capture journal and raw CAS sources are retained with the run; there is no
independent telemetry GC. Original library retention roots remain authoritative.
Historical index/integrity commitments are retained as physical metadata.
Read-only owners never initialize, repair, truncate, quarantine or republish.
Historical run stores without Stage 15 namespaces can still be read without
creating those directories.

A crash after an external effect may leave UNKNOWN usage. Recovery repairs only
an abandoned storage suffix and does not replay the provider or worker. The
observation suffix can be regenerated deterministically by the existing run
owner. Its expected manifest is not a commit certificate: missing events or
lineage remain incomplete until their actual bytes are present. An unavailable
required capture prevents dispatch; a later observation failure does not erase
a correct domain result.

## Executable acceptance

Each heavy scenario has its own file and GitHub Actions matrix shard. Tests use
the installed Mini and SDK against a controlled HTTP provider; no commercial
network call is required. The fixture supplies provider responses and operator
inputs only. Product modules never import the acceptance layer.

- `test_provider_capture_acceptance.py`: real Mini process and retry boundaries.
- `test_capture_reconciliation_acceptance.py`: physical token/source discrepancies.
- `test_capture_recovery_acceptance.py`: process death after HTTP, no replay,
  and failed capture before dispatch.
- `test_canonical_observability_acceptance.py`: canonical CLI and credential-free
  completed-run resume.
- `test_publication_observability_acceptance.py`: actual patch, C1, oracle,
  publication and lost verification source.
- `test_observation_recovery_acceptance.py`: missing event/graph, read-only gaps,
  deterministic reconstruction and unchanged outcome.
- `test_observation_inventory_acceptance.py`: a self-consistent forged manifest
  cannot omit actual physical calls.
- `test_telemetry_degradation_acceptance.py`: mismatched provider totals preserve
  the independent correctness outcome and never repeat the effect.
- `test_artifact_reconciliation_acceptance.py`: all five artifact statuses.
- `test_snapshot_reconciliation_acceptance.py`: all five snapshot statuses.
- Lightweight accounting and event contracts run in `gold-fast`; the Stage 3A
  fresh-process import tripwire is part of that gate.

Run one file with `python -m pytest -q acceptance/stage4/stage15/<file>.py`.

## Export and Stage 16 boundary

OpenTelemetry mapping is attributes-only and explicitly lossy. It pins internal
telemetry v1 and GenAI semantic-conventions revision
`94f432d7126f5884d30a2cdde6f4e89908ebb6fd` (Development). It does not represent each
physical retry as the normative logical client span and emits no token metrics.
Canonical retained evidence remains the accounting source of truth.

The design follows the primary contracts for
[Mini model plugins](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/models/__init__.py),
[OpenAI Chat usage](https://developers.openai.com/api/reference/resources/chat),
[Anthropic cache accounting](https://platform.claude.com/docs/en/build-with-claude/prompt-caching),
[Gemini usage metadata](https://ai.google.dev/api/generate-content#UsageMetadata),
and the pinned
[OpenTelemetry GenAI conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/94f432d7126f5884d30a2cdde6f4e89908ebb6fd/docs/gen-ai/gen-ai-spans.md).

Paired arms, counterfactual token credits, confidence estimates, statistical
claims and economic savings belong to Stage 16. Stage 15 reports
`STAGE16_NOT_EVALUATED`; complete call accounting is not an economic result.
