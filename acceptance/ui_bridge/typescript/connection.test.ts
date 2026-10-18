import assert from "node:assert/strict";
import { test } from "node:test";
import { initialConnection, receiveSnapshot, connectionFailure } from "../../../synapse/ui/src/connection/model.ts";
import { decodeSnapshot } from "../../../synapse/ui/src/contracts/decode.ts";
const snapshot = (sequence: number, fields = {}) => decodeSnapshot(JSON.stringify({schema_version:"1.0",
  session_id:"7be93210-4107-4444-9777-aabbaa998877",subscription_id:"7be93210-4107-4444-9777-aabbaa998877",sequence,state:"native",domain_ready:false,diagnostics:[],...fields}));
test("foreign and stale deliveries cannot overwrite current bootstrap state", () => {
  const current = receiveSnapshot(initialConnection,snapshot(4));
  for (const next of [snapshot(3,{state:"fail",code:"CORE_EXITED"}), snapshot(4,{state:"fail",code:"CORE_EXITED"}), snapshot(5,{session_id:"aa993210-4107-4444-9777-aabbaa998877"})]) {
    assert.deepEqual(receiveSnapshot(current,next),current);
  }
  const fresh = snapshot(5,{state:"ready"});
  assert.deepEqual(receiveSnapshot(current,fresh),{snapshot:fresh,error:null,delivery:"connected"});
});
test("failures preserve native identity; a newer native result clears a transient error", () => {
  const current = receiveSnapshot(initialConnection,snapshot(1));
  const failed = connectionFailure(current,"BUSY");
  assert.equal(failed.snapshot,current.snapshot);
  assert.equal(failed.error,"BUSY");
  assert.equal(receiveSnapshot(failed,snapshot(2)).error,null);
  assert.equal(receiveSnapshot(current,snapshot(2,{state:"fail",code:"CORE_EXITED"})).error,"CORE_EXITED");
});
