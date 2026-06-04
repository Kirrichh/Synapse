# RFC: PolicyDef Structural Wrapper in CVM (v2.2.0-alpha3d4)

STATUS: IMPLEMENTED in alpha3d4

## 1. Goals and Non-Goals

Goal: compile `PolicyDef` and `PolicyRule` as structural runtime wrappers so policy scopes can participate in CVM snapshots, transition hashes, and parity traces.

Non-goals: policy evaluation, enforcement, conflict resolution, messaging policy binding, capability mutation, async/promise behavior, LLM calls, and cognitive primitive execution.

## 2. PolicyDef as Structural Runtime Primitive

`PolicyDef` is compiled as structural scope markers:

```text
POLICY_ENTER(name, metadata)
<body/rules>
POLICY_EXIT(name)
```

The CVM does not inspect or mutate the governance registry.

## 3. PolicyRule Wrapper Semantics

`PolicyRule` is compiled as a nested structural wrapper:

```text
POLICY_RULE_ENTER(name, metadata)
<compiled expression/control-flow body>
POLICY_RULE_EXIT(name)
```

The rule value is compiled as ordinary CVM expression code and discarded. No specialized `EVAL_POLICY_RULE` opcode exists.

## 4. VMState.policy_stack

`VMState.policy_stack` records the durable LIFO policy/rule scope stack. It is serialized in `VMState.to_dict()` and restored in `VMState.from_dict()`.

## 5. CallFrame.policy_stack_snapshot

Each `CallFrame` captures `policy_stack_snapshot` at function entry. On `RETURN`, any policy scopes above that snapshot are unwound through the bridge before the caller state is restored.

## 6. Bridge Dispatch Contract

The structural symbols are:

- `SYS_POLICY_ENTER`
- `SYS_POLICY_EXIT`
- `SYS_POLICY_RULE_ENTER`
- `SYS_POLICY_RULE_EXIT`

They are `VM_STRUCTURAL_RUNTIME`: bridge-dispatched, not capability-gated, and never implemented as CVM-native governance logic.

## 7. Governance Runtime Parity

The bridge emits canonical host events:

- `policy_entered`
- `policy_exited`
- `policy_rule_entered`
- `policy_rule_exited`

Events are appended to host `execution_history` with normal event IDs and emitted through `emit_runtime_event()` when available.

## 8. Snapshot / Restore Invariants

Snapshots include `policy_stack`. Restore hydrates host policy stack state from `vm.state.policy_stack` without emitting new events. `transition_hash` includes `tuple(policy_stack)`.

## 9. Exception and RETURN Unwind

Policy unwind is best-effort and no-throw. It must not mask `OutOfEnergy`, assertion failures, or primary VM errors. `unwind_reason` is recorded on host exit events for `function_return`, exception, or halted safety-net cleanup.

## 10. Coverage Target

`PolicyDef` and `PolicyRule` must disappear from the static corpus fallback report. Expected total fallback decreases from 150 to approximately 135 and corpus coverage rises to at least 0.883.

## 11. Hard Boundaries

D4 does not add messaging, async/promise semantics, LLM execution, policy enforcement, actor mailbox ordering, or cognitive primitive behavior.
