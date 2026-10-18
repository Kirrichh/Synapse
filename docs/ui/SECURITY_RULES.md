# Continuous security and bypass prevention

Version: 1.0. Status: mandatory throughout UI and bridge implementation.

This document operationalizes the owner's requirement to check security while
writing code. The [bridge specification](SYNAPSE_UI_BRIDGE_SPEC_V1.md) owns
product semantics; these checks do not authorize changing those semantics.

Use the [UI security design](UI_SECURITY_DESIGN.md) for the researched threat
model, platform-specific controls, implementation decisions, and developer-only
security acceptance scenarios. It includes the limits of renderer-based consent
and does not claim that proposed controls are already implemented.

## 1. Review at each boundary change

Before coding, identify who supplies each input, who may act on it, which state
is trusted, which effect is possible, and which record proves acceptance. During
coding, exercise malformed, stale, duplicated, cross-project, and unauthorized
inputs. Before completion, review the final path and retain acceptance/mutation
evidence for the controls it relies on.

Check the entire action path, including a shortcut, restore, retry, preview,
diagnostic, or administrative reader that could bypass the normal enforcement.
An error message alone is not proof that no forbidden effect occurred.

## 2. Authority and context

- Obtain window/channel identity from trusted Tauri context.
- Treat renderer content, local documents, agent output, and provider responses
  as untrusted inputs to the relevant boundary.
- Verify method, workspace, owner, object, capability, and expected revision for
  every permitted operation. Knowledge of an ID or path is not authorization.
- Keep presentation state distinct from authority. Revalidate with the domain
  owner at the actual effect boundary, including after time spent reviewing UI.
- Enter Gold only through the declared composition roots. Do not create an
  unrestricted forwarding command or alternative daemon/runner path.
- Preserve the distinction between Tauri capabilities and Synapse permissions.

## 3. Protocol and availability

- Apply the bootstrap frame bound before allocating the first payload.
- Validate u32 lengths, UTF-8, JSON syntax, duplicate keys, schema version,
  numerical representation, and command semantics in the required order.
- Close a corrupted byte stream explicitly; do not search for a plausible next
  header in untrusted remaining bytes.
- Keep stderr out of stdout framing. Limit and sanitize diagnostics before
  persistence and display.
- Bound queues separately so Live traffic cannot starve shutdown, heartbeat,
  refusal, or command-result handling.
- A slow/disconnected renderer must not hold domain execution hostage.
- Reject unknown authoritative fields/methods/constraints according to the
  version contract. No silent downgrade or constraint stripping.

## 4. Commands, consent, and recovery

- Persist intent-to-command bindings before sending mutations. Preserve the
  binding across renderer/shell/core failures and enforce conflicts before effects.
- A missing reply, timeout, or error does not prove that execution did not occur.
- Status reconciliation is read-only and must not create an invocation claim.
- Grant, refusal, and revocation retain their distinct prerequisites and results.
- Bind once-grants durably and exclusively before the first effect; competing
  executions and changed proposals must fail. Crash recovery never releases a
  consumed grant merely because its executor stopped answering.
- Record unknown external outcomes accurately. Terminating local processes does
  not undo remote requests or prove their absence.
- Test crashes around every meaningful multi-write acceptance boundary,
  especially memory reassessment, publication, retention, and rebind.

## 5. State and evidence

- Bind projections to workspace, domain, generation, subscription, and epoch.
- Apply only fully covered ranges. Distinguish an old delivery from a current
  integrity violation and handle each according to the specification.
- Preserve tombstones/invalidation when visibility or filters change.
- Keep delivery metadata separate from immutable source evidence.
- Redact or restrict the UI projection without forging a new source identity.
- Preserve the difference between an unavailable record and a domain GAP.
- Do not turn retained assessment events into invented live activity or compare
  unrelated domains' sequence numbers as a global execution order.

## 6. Files, documents, and content

- Resolve paths against the authorized workspace and verify actual containment,
  object type, symlinks, platform path semantics, and content version.
- Bound reads and bind all chunks to one version. Reject stale writes explicitly.
- Keep immutable evidence read-only. A local edit and acceptance of a proposed
  patch have different commands and authority.
- Render untrusted documents through a safe structured representation. Do not
  execute active HTML or rely on iframe identity separation in Tauri on Linux.
- Product CSP permits required IPC and blocks unnecessary external access.
  Never copy the demo's offline CSP blindly into the installed application.

## 7. Secrets and local information

- Use protected, versioned secret references in commands and pre-send storage.
- Ordinary settings reads, exports, StateSync, logs, hashes of ordinary command
  parameters, and error stacks must not disclose secret values.
- Make credential delivery specific to the authorized process and purpose.
- An agent receives its broker capability, not the provider credential.
- A material being visible to the user does not authorize sending it to an LLM.
- Keep task and local-information channels distinct. Preserve PublicConversation
  restrictions for agent model requests and the runtime gateway's own contract.
- Connection probes use declared nonsecret control material and explicit bounds;
  they do not silently include project content, chat history, or memory.

## 8. Administrative surfaces and memory

- Discovery and static validation must not load plugin code or launch processes.
- Probes are explicit executions, bound to the tested version, with their own
  authority, limits, result records, and unknown-outcome handling.
- A capability checkbox or successful endpoint response cannot mint admission.
- Recheck runtime/descriptor drift before use; do not silently substitute an
  unadmitted profile or relax requirements after NO_ELIGIBLE_AGENT.
- Preview memory policy in an isolated context. The existing live
  reassess call and exam modes do not supply that isolation automatically.
- Validate all mathematical invariants, finite values, thresholds, and applicable
  counters in the owner shared by UI, CLI, and imports.
- Protect independent corroboration and provenance. Expert controls cannot
  bypass mandatory evidence requirements.
- New storage quotas require actual measurement, reservation, enforcement, and
  safe reclamation. A slider must not simulate physical enforcement.

## 9. Processes and installation

- Verify containment with the real child-launch mechanisms, including adapters
  and MCP servers, on each supported platform.
- Windows Job membership must be established before domain execution, with
  breakaway and inherited lifetime handles controlled as specified.
- The initial Linux profile uses the user systemd service/cgroup contract.
  Unsupported environments fail explicitly.
- Before a replacement executor may affect the same resources, establish that
  the previous local executors can no longer continue those effects.
- Keep resource-store recovery with its owner; process containment is additional.
- Ordinary shutdown uses the protocol and a bounded wait. A forced close is an
  explicit action and leaves unfinished outcomes for reconciliation.
- Identify the actual installed artifact under test; self-reported version text
  alone does not prove which binary was exercised.

## 10. Record and resolve findings

For each finding, record the affected requirement, attack/failure scenario,
actual impact, reproducing acceptance case, fix, and mutation that checks the
control. Mark unverified or blocked results honestly. Do not close a task by
weakening a check, hiding uncertainty, adding a skip, or creating a test-only
product path.
