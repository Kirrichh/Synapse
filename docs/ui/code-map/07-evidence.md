# Execution records, evidence, results, and replay

Version 1.0. English translation of Appendix D.6.

[Code map and boundaries](../UI_CODE_MAP_V1.md). Existing foundations do not imply implemented UI commands.

## `synapse/experiments/gold/runner/controller.py`

- **Symbols:** GoldRunController.
- **Role:** Controls Gold attempts/phases.
- **UI use:** Actual task progress rather than demo timers.
- **Boundary:** No parallel UI scheduler.
- **Basis:** runner_composition construction and manifest.

## `synapse/experiments/gold/runner/models.py`

- **Symbols:** GoldRunConfig; GoldRunManifest.
- **Role:** Fixed run parameters and identities.
- **UI use:** Task/profile/budget association with a run.
- **Boundary:** Never rewrite frozen run configuration.
- **Basis:** runner_composition imports and manifest.

## `synapse/experiments/gold/runner/records.py`

- **Symbols:** RunRecordStore; RecordKind.
- **Role:** Immutable content-addressed run records.
- **UI use:** Read projections/references.
- **Boundary:** Reading receives no mutation fence.
- **Basis:** stage15/events and run_recovery code.

## `synapse/experiments/gold/runner/run_recovery.py`

- **Symbols:** RunRecordRecovery.session; RunRecordSession.
- **Role:** Exclusive recovery/mutation session for a particular run-record store.
- **UI use:** Post-crash result reconciliation.
- **Boundary:** Neither OS supervision nor an application-wide process lock.
- **Basis:** Definition/call in the supplied map.

## `synapse/experiments/gold/runner/run_progress.py`

- **Symbols:** AttemptProgressPhase; load_attempt_progress; require_progress_payload.
- **Role:** Physical phase boundaries and attempt checkpoint chain.
- **UI use:** Potential task-StateSync source.
- **Boundary:** Checkpoints alone do not establish a consistent UI snapshot.
- **Basis:** budgets imports and manifest.

## `synapse/experiments/gold/runner/state_machine.py`

- **Symbols:** load_run_state.
- **Role:** Restores state and assembles final run result.
- **UI use:** Confirmed results and recovery state.
- **Boundary:** Do not infer state heuristically from log text.
- **Basis:** run_observability/run_recovery imports.

## `synapse/experiments/gold/runner/vocabulary.py`

- **Symbols:** GoldRunFailureCode; AttemptOutcome; closed outcomes/decisions.
- **Role:** Primary run outcome/refusal vocabularies.
- **UI use:** Generate/verify TS representations and localization.
- **Boundary:** No second independently editable enum.
- **Basis:** Imports and manifest.

## `synapse/experiments/gold/runner/c1_boundary.py`

- **Symbols:** C1AttemptBoundary; C1EvidenceContext.read; read_c1_verification_evidence.
- **Role:** Verified Stage 10 dispatch into C1 and exact C1 evidence reads.
- **UI use:** Diff/verification views and c1.evidence.read.
- **Boundary:** Not general project reading; readers receive no writer/oracle and viewing never applies changes.
- **Basis:** Definition/call in the supplied map.

## `synapse/experiments/applied_verification/gold_runner.py`

- **Symbols:** run_gold_attempt.
- **Role:** Existing controlled-change/C1 attempt implementation used by the C1 boundary.
- **UI use:** Actual coding-candidate application/verification.
- **Boundary:** Internal canonical route, not a renderer direct-call button.
- **Basis:** Inspected c1_boundary imports.

## `synapse/experiments/applied_verification/gold_attempt_writer.py`

- **Symbols:** GoldAttemptWriter.
- **Role:** Retains exact C1 result supporting subsequent decisions.
- **UI use:** Original C1 report rather than an agent paraphrase.
- **Boundary:** A returned Python object cannot substitute for the retained record.
- **Basis:** c1_boundary imports/docstring.

## `synapse/experiments/gold/stage12/outcome.py`

- **Symbols:** StructuredOutcome; final-status matrix.
- **Role:** Authoritative final result status.
- **UI use:** Separate agent COMPLETED from system acceptance.
- **Boundary:** Process exit cannot imply FULL.
- **Basis:** Manifest and runner_composition result.structured_outcome.

## `synapse/experiments/gold/stage12/verification.py`

- **Symbols:** Domain verification of C1/C2, plan, and binding.
- **Role:** Checks retained result evidence.
- **UI use:** Verification tab/refusal explanation.
- **Boundary:** Correct JSON shape is not domain verification.
- **Basis:** Ownership manifest.

## `synapse/experiments/gold/stage13/publication.py`

- **Symbols:** Independent publication authority.
- **Role:** Admits publication of verified candidates.
- **UI use:** Publication state/basis.
- **Boundary:** Adapters cannot publish their own results.
- **Basis:** Ownership manifest.

## `synapse/experiments/gold/stage13/publication_store.py`

- **Symbols:** Atomic write sets and committed visibility.
- **Role:** Durable publication and undo recovery.
- **UI use:** Read visibility of accepted results.
- **Boundary:** Preview records cannot be copied as live publication.
- **Basis:** Ownership manifest.

## `synapse/experiments/gold/stage14/reconstruction.py`

- **Symbols:** Physical lineage reconstruction.
- **Role:** Reopens evidence sources and verifies lineage.
- **UI use:** Inspect why a result was accepted.
- **Boundary:** UI graphs cannot invent missing-source evidence.
- **Basis:** Ownership manifest.

## `synapse/experiments/gold/stage14/read_traversal.py`

- **Symbols:** Bounded read traversal.
- **Role:** Bounds reconstruction lifetime while reading retained roots.
- **UI use:** Large proof trees without unlimited GUI caches.
- **Boundary:** This component does not establish a user RAM limit.
- **Basis:** Ownership manifest.

## `synapse/experiments/gold/stage15/events.py`

- **Symbols:** GoldEvent; EventStream.page; open_event_stream.
- **Role:** Read-only evidence events for a fixed assessment, chain, and cursors.
- **UI use:** One of several bottom-panel sources.
- **Boundary:** Not a live bus for all agents. Restored records behind cursors need recheck/invalidation.
- **Basis:** Definition/call in the supplied map.

## `synapse/experiments/gold/stage15/run_observability.py`

- **Symbols:** observe_completed_run; inspect_observability.
- **Role:** Post-completion observability projection and physical read-only reinspection.
- **UI use:** Historical reports/integrity discrepancies.
- **Boundary:** No invented live timestamps; inspect does not repair history.
- **Basis:** Definition/call in the supplied map.

## `synapse/experiments/gold/replay.py`

- **Symbols:** Replay-lifecycle owner.
- **Role:** Evidential replay through replay_composition.
- **UI use:** Replay and its confirmed outcome.
- **Boundary:** Replay, history reading, and new execution are distinct.
- **Basis:** Ownership manifest; full GUI contract proposed.

## `synapse/experiments/gold/replay_vm_adapter.py`

- **Symbols:** Replay-to-VM adapter.
- **Role:** Physical replay/CVM integration boundary.
- **UI use:** Internal dependency of trustworthy replay.
- **Boundary:** No direct UI-to-CVM calls.
- **Basis:** Manifest owner_adapters.

## `synapse/golden_replay.py`

- **Symbols:** replay_mock_artifact; CacheOnlyLLMBackend; DeterministicReplayError; ReplayArtifactError.
- **Role:** Existing language-runtime golden-artifact replay, used by CLI replay --mock.
- **UI use:** Runtime history reads/comparisons in that profile.
- **Boundary:** Not automatically the same API as Gold replay.
- **Basis:** CLI imports/calls and LLM gateway acceptance.

## `synapse/debugger_core.py`

- **Symbols:** ForkRegistry; GoldenArtifactTraceAdapter; find_trace_divergence.
- **Role:** Debugging, trace comparison, and bounded process-scoped fork lifecycle.
- **UI use:** Possible read basis for replay-trace comparisons.
- **Boundary:** Process-scoped debug registry cannot replace durable UI workspace/pending commands.
- **Basis:** Confirmed CLI calls/limits.
