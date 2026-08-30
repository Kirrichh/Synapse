# Stage 4 Patch 9 — Protected-Core Prerequisite 1A Evidence

Status: `IMPLEMENTED / TEST EVIDENCE RECORDED / POST-IMPLEMENTATION HUMAN APPROVAL PENDING`

This record covers only the narrow CognitiveVM prerequisite required before PR
#99 may consume the existing asynchronous host-call lifecycle. It is not Stage 4
replay implementation evidence.

## Authorization and anchors

| Item | Value |
|---|---|
| PR #99 audited head | `aa99099794ce6116732fb7e5a94851f2434b427d` |
| implementation branch | `codex/pr99-patch9-prerequisites-impl` |
| implementation authorization | `YES — repository owner explicitly requested the exact argc/executed_ip repair on 2026-08-26` |
| post-implementation human approval | `PENDING` |

The implementation request is prior authorization for the exact whitelist below.
It is not post-implementation approval of the resulting diff or test evidence.
This file is produced by an implementation agent and cannot self-approve the
protected core. An explicit human review or merge remains required.

## Exact change whitelist

The 1A component may change only:

```text
synapse/cvm.py
  CognitiveVM.step()
  LLM_REQUEST branch
  existing _make_pending_host_call_envelope(...) invocation only

tests/test_cvm_llm_request_pending_envelope.py
  focused falsification, serialization and compatibility coverage

docs/evidence/STAGE4_PATCH9_PROTECTED_CORE_PREREQUISITE_1A.md
  this scope and evidence record
```

The only approved production-code delta is:

```text
argc=4
executed_ip=executed_ip
```

Explicitly outside the whitelist:

```text
structural opcode callbacks or unwind behavior
Stage 4 contracts, imports, stores, policy or authority logic
VMBridge dispatch or provider behavior
HOST_ABI symbols or version
pending_schema_version or envelope field vocabulary
snapshot migration
activity or replay persistence
compiler, parser, lexer or AST changes
```

Other prerequisites developed on the same branch require their own ownership and
evidence records; they do not widen this component whitelist.

## Implementation effect

Before this repair, executing the first-class `LLM_REQUEST` opcode called
`_make_pending_host_call_envelope()` without its required `argc` and
`executed_ip` keyword arguments. Python raised `TypeError` after the prompt
envelope had been popped and the instruction pointer pre-incremented, before a
durable pending envelope could be installed.

The repair supplies:

- `argc=4`, matching the four serialized logical host-call arguments (prompt
  envelope, schema hash, engine parameters, cache policy);
- `executed_ip=executed_ip`, binding deterministic host-call identity to the
  instruction that executed rather than the pre-incremented `ip_after_call`.

No Stage 4 code or policy enters the protected core.

## Backward-compatibility analysis

- Envelope schema remains `pending_schema_version = "1"`; no field is added,
  removed or renamed.
- `HOST_ABI_VERSION` and the VM-visible symbol set are unchanged; no ABI bump is
  required.
- Generic `CALL_HOST` construction is untouched and remains covered by
  `tests/test_cvm_alpha3d1_pending_host_call.py`.
- Existing snapshot decoding is unchanged. A repaired LLM pending envelope uses
  the already-supported schema-v1 reader and resumes without re-executing
  `LLM_REQUEST`.
- `ip_after_call` remains the pre-incremented continuation address. Only
  identity input receives the actual executed IP.
- No previously successful first-class `LLM_REQUEST` call identity changes at
  the audited base: that path raised before producing a pending envelope.
  Generic host-call identities are unchanged.
- No history or snapshot migration is introduced.

The repaired pause path may expose independent downstream bridge/provider defects
that the pre-existing `TypeError` made unreachable. This component makes no full
LLM production-path claim and does not authorize widening the protected-core
patch to address such findings.

## Falsification matrix

| Mutant / regression | Killing evidence |
|---|---|
| omit either required keyword | direct `LLM_REQUEST` step cannot reach `STATUS_PAUSED_HOST_CALL` |
| record stack arity `1` instead of logical host arity `4` | `test_mutant_llm_request_uses_stack_arity_instead_of_host_arity_is_killed` |
| use pre-incremented `self.state.ip` in identity | `test_mutant_llm_request_uses_ip_after_call_for_identity_is_killed` |
| lose or reorder one of the four serialized arguments | exact argument-vector assertion in the arity mutant test |
| make the repaired envelope incompatible with schema-v1 restore/resume | `test_llm_request_pending_envelope_snapshot_roundtrip_and_resume_is_compatible` |
| couple `cvm.py` to Stage 4 Gold | `test_protected_core_does_not_import_stage4_gold` |
| regress generic D1 host-call lifecycle | existing `tests/test_cvm_alpha3d1_pending_host_call.py` suite |

## Verification

```text
python -m pytest -q tests/test_cvm_llm_request_pending_envelope.py
4 passed in 0.16s

python -m pytest -q tests/test_cvm_alpha3d1_pending_host_call.py tests/test_cvm_llm_bridge_alpha3e.py
43 passed in 0.28s

python -m pytest -q tests/test_cvm_*.py
271 passed in 1.39s

git diff --check
PASS
```

## Human approval gate

Post-implementation approval remains open until a human reviews:

```text
[ ] the production diff is exactly the two whitelisted keyword arguments
[ ] no structural callback or unwind path changed
[ ] no Stage 4 dependency entered cvm.py
[ ] all listed falsification and compatibility tests passed
[ ] no ABI or schema migration was introduced
```

Until those boxes are approved by a human reviewer, this evidence record does not
close the protected-core procedure.

## Non-claims

This prerequisite does not claim completion of structural opcode semantics,
governed activity replay, artifact lifecycle, production provenance, Library
retention, or PR #99 as a whole.
