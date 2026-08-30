# Stage 4 Patch 9 - Library ProgramArtifact Prerequisite 1B Evidence

Status: `IMPLEMENTED / PRODUCT ACCEPTANCE RECORDED`

This record covers the Library-owned immutable ProgramArtifact lifecycle required
before PR #99 can execute a behavior whose canonical program is a hash-bound
artifact. It also records the removal of the parallel replay-only artifact
binding. It does not claim completion of PR #99 as a whole.

## Anchors and scope

| Item | Value |
|---|---|
| PR #99 audited head | `aa99099794ce6116732fb7e5a94851f2434b427d` |
| implementation branch | `codex/pr99-patch9-prerequisites-impl` |
| protected-core prerequisite | commit `811f513` (separate 1A procedure) |
| Library ownership | `library.py` plus `library_program_artifacts.py` adapter |
| Behavior ownership | `behavior.py` plus `behavior_program_artifacts.py` adapter |
| production composition | `library_composition.py` |

The production implementation does not create a second Library, a replay-owned
artifact store, a separate garbage collector, or a fourth replay adapter.

## Production lifecycle

1. The composition root constructs the existing `BehaviorLibrary` with the
   Library-owned ProgramArtifact lifecycle port.
2. A sealed, Library-bound `ProgramArtifactWriteAuthority` is minted separately
   from behavior publication admission.
3. `ingest_program_artifact` validates every exact reference field, validates the
   SHA-256 and byte length, decodes a `BytecodeProgram`, requires an exact object
   round trip, and requires canonical transport bytes before writing.
4. Ingestion writes only to the temporary ingestion root under the existing
   Library transaction and mutation fence. Those bytes are not publicly openable.
5. `put_behavior` still requires the existing section 22 publication admission.
   Inside that same Library transaction it promotes the exact program before the
   behavior journal `BEGIN`; the behavior manifest carries the mandatory
   `ArtifactProgram.artifact_ref` in `artifact_refs`.
6. A crash before behavior commit may leave an immutable, unretained program, but
   it cannot expose a behavior or make the program publicly readable. Restart
   recovery validates staged objects, and a retry deduplicates or completes the
   publication.
7. `open_program_artifact` / `open_artifact` returns bytes only when an exact
   committed behavior manifest retains the same full `HashBoundRef`.
8. The existing quarantine record and mutation-fence mechanisms handle collision,
   tamper, non-regular entries and recovery corruption.
9. The existing Library GC planner is extended with
   `BehaviorManifest -> ProgramArtifact`; no independent GC state exists.

A caller-substituted `byte_length` is treated as a reference mismatch rather than
object corruption when the stored bytes still match their content address. This
prevents a forged reference from quarantining a valid CAS object.

## Canonical binding convergence

The old replay-only `ArtifactProgramBinding`, its private seal, and
`replay_program_binding_from_artifact` were removed. Artifact resolution now:

```text
Library exact reader
  -> replay canonical decoder and capability derivation
  -> behavior_program_artifacts.bind_artifact_behavior_unit
  -> CompilerBinding
  -> replay_program_binding
```

The inline compiler path already ends in the same `CompilerBinding` and
`replay_program_binding`. Downstream replay no longer has two producer-binding
models. The replay decoder also rejects noncanonical JSON and any decoded
program that does not round-trip exactly.

## Acceptance-world correction

The former dictionary-backed test `ArtifactStore` was demoted to
`ArtifactFixtureSource`. It has no `open_artifact` method and cannot satisfy the
runtime resolver port. It supplies only pre-publication bytes. Product acceptance
then ingests those bytes through the production composition root and replay reads
them back through the actual `BehaviorLibrary` exact reader.

This keeps tests as callers and observers of product code. They do not implement
the production artifact store, resolver, retention rule, recovery path or binding
path. No test was added for line count, file count, test count, or patch volume.

## Falsification coverage

| Regression | Product evidence |
|---|---|
| free or foreign write authority | ingestion refuses before a write |
| substituted ref field | exact ingestion/read refuses; valid bytes remain usable |
| noncanonical program bytes | ingestion and replay decoder refuse |
| collision at immutable address | address is quarantined; behavior is not visible |
| post-publication tamper | exact read records durable quarantine |
| staged-write crash | restart recovers the durable ingestion stage |
| crash after promotion before journal begin | no behavior becomes visible; retry succeeds |
| missing manifest retention edge | `BehaviorCore` validation refuses the artifact behavior |
| program not reachable from manifest | GC plan marks it as a deletion candidate |
| fake test resolver used at runtime | fixture has no resolver method; governed replay uses Library |
| parallel replay artifact binding | removed symbols have no remaining definitions or call sites |

## Verification

Final-state checks:

```text
python -m py_compile <changed production and acceptance modules>
PASS

python scripts/stage9_ownership_dag.py
forbidden edges: none

git diff --check
PASS

python -m pytest -q -p no:randomly tests/test_stage4_gold_library_program_artifact.py
21 passed in 7.25s

python -m pytest -q -p no:randomly   tests/test_stage4_gold_library_program_artifact.py   tests/test_stage4_gold_behavior.py   tests/test_stage4_gold_architecture.py   tests/test_stage4_gold_dependency_direction.py
257 passed, 4 skipped in 28.35s

python -m pytest -q -p no:randomly   tests/test_stage4_gold_replay.py::test_artifact_decoder_rejects_hash_bound_but_noncanonical_program_bytes   tests/test_stage4_gold_replay.py::test_governed_replay_resolves_durable_record_and_injects_exact_stored_bytes
2 passed in 35.84s

python -m pytest -q -p no:randomly   tests/test_stage4_gold_compatibility.py   tests/test_stage4_gold_consumption_evidence.py
55 passed in 235.09s
```

An additional full-file replay diagnostic was intentionally stopped after
`65 passed in 1418.85s` with no observed failures. It is not reported as a full
suite pass. The exact positive and negative replay paths changed by this
prerequisite are covered by the explicit two-test command above.

## Non-claims

This prerequisite does not approve the protected-core 1A human gate, choose the
remaining structural-opcode erratum, complete production activity provenance,
move the CognitiveVM replay adapter, or claim that every remaining PR #99 audit
finding is closed.
