import assert from "node:assert/strict";
import { test } from "node:test";
import { decodeCommandMutation, decodeCommandReconciliation } from "../../../synapse/ui/src/contracts/command-decode.ts";

const id = "12345678-1234-4321-8123-123456789abc";
const command = {command_id:id, method:"invocation.cancel", method_version:"1",
  recipient:"example-invocation", expected_revision:"a".repeat(64), params:{invocation_id:"example-invocation"}};
const absent = {schema_version:"synapse.command.status/v1", state:"ABSENT", command_id:id,
  owner:"agent.invocations", principal:"example-operator", workspace:"example-workspace", recipient:"example-invocation"};
const completed = {schema_version:"synapse.invocation.cancel-result/v1", state:"COMPLETED", command_id:id,
  binding_sha256:"a".repeat(64), params_hash:"b".repeat(64), invocation_id:"example-invocation",
  outcome:"CANCEL_REQUESTED", replayed:true};

test("shared command schemas admit exact commands and distinguish cooperative results from absence", () => {
  assert.deepEqual(decodeCommandMutation(JSON.stringify(command)), command);
  assert.deepEqual(decodeCommandReconciliation(JSON.stringify(absent)), absent);
  for (const outcome of ["CANCEL_REQUESTED", "ALREADY_TERMINAL"]) {
    const result = {...completed, outcome};
    assert.deepEqual(decodeCommandReconciliation(JSON.stringify(result)), result);
  }
});

test("command admission refuses forged authority, malformed UUIDs, changed targets and unsupported methods", () => {
  for (const changed of [
    {command_id:"x".repeat(36)}, {command_id:id + "\n"}, {method:"task.stop"}, {method_version:1},
    {recipient:"other"}, {expected_revision:"a".repeat(63) + "\n"},
    {params:{invocation_id:"example-invocation", authority:"admin"}}, {principal:"admin"}, {workspace:"foreign"},
  ]) assert.throws(() => decodeCommandMutation(JSON.stringify({...command, ...changed})));
});

test("reconciliation refuses ambiguous or extended results without silently changing input", () => {
  for (const changed of [{command_id:"x".repeat(36)}, {owner:"memory"}, {principal:""},
    {state:"UNKNOWN"}, {replayed:true}, {workspace:"x".repeat(257)}]) {
    assert.throws(() => decodeCommandReconciliation(JSON.stringify({...absent, ...changed})));
  }
  for (const changed of [{outcome:"STOPPED"}, {replayed:"true"}, {params_hash:"b".repeat(63)}, {authority:true}]) {
    assert.throws(() => decodeCommandReconciliation(JSON.stringify({...completed, ...changed})));
  }
});

test("duplicate, lossy numeric and invalid Unicode input is refused before schema validation", () => {
  for (const decode of [decodeCommandMutation, decodeCommandReconciliation]) {
    for (const raw of ['{"x":1,"x":2}', '{"x":9007199254740993}', '{"x":"\\ud800"}']) {
      assert.throws(() => decode(raw));
    }
  }
  assert.throws(() => decodeCommandMutation(JSON.stringify(command).replace('"method_version":"1"',
    '"method_version":"1","method_version":"1"')));
});
