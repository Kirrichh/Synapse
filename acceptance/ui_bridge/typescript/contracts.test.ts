import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { decodeEnvelope, decodeSnapshot, decodeFailure } from "../../../synapse/ui/src/contracts/decode.ts";
const corpus = JSON.parse(readFileSync(new URL("../corpus.json", import.meta.url), "utf8"));
for (const item of corpus) test(item.behavior, () => {
  if (item.valid) assert.deepEqual(decodeEnvelope(item.raw), item.value);
  else assert.throws(() => decodeEnvelope(item.raw));
});
const base = { schema_version:"1.0", session_id:"7be93210-4107-4444-9777-aabbaa998877",subscription_id:"7be93210-4107-4444-9777-aabbaa998877",sequence:0,
  state:"native",domain_ready:false,diagnostics:[] };
test("snapshots refuse domain authority, extension fields, bad diagnostics and corrupt nested replies", () => {
  assert.deepEqual(decodeSnapshot(JSON.stringify(base)), base);
  for (const fields of [{domain_ready:true}, {state:"running"}, {unknown:true}, {diagnostics:["secret=value"]},
    {sequence:-1}, {protocol_message:'{"type":"ready"}'}, {core_version:"x".repeat(129)}]) {
    assert.throws(() => decodeSnapshot(JSON.stringify({...base,...fields})));
  }
  assert.throws(() => decodeSnapshot(JSON.stringify(base).replace('"sequence":0','"sequence":0,"sequence":1')));
});
test("UTF-8 byte bounds apply before decoding even when UTF-16 length is small", () => {
  const text = JSON.stringify({schema_version:"1.0", type:"request",request_id:"r1",method:"machine.ping",params:{text:"🙂".repeat(17000)}});
  assert.throws(() => decodeEnvelope(text));
});
test("native diagnostic codes preserve overload without leaking unknown exception content", () => {
  assert.equal(decodeFailure("BUSY"), "BUSY");
  assert.equal(decodeFailure("CORE_EXITED"), "CORE_EXITED");
  assert.equal(decodeFailure("secret credential"), "INTERNAL_ERROR");
  assert.equal(decodeFailure({message:"secret"}), "INTERNAL_ERROR");
});
