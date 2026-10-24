import assert from "node:assert/strict";
import { test } from "node:test";
import { initialConnection, receiveSnapshot, deliveryFailure } from "../../../synapse/ui/src/connection/model.ts";
import { decodeSnapshot } from "../../../synapse/ui/src/contracts/decode.ts";
const snapshot = (subscription: string) => decodeSnapshot(JSON.stringify({schema_version:"1.0",
  session_id:"7be93210-4107-4444-9777-aabbaa998877",subscription_id:subscription,
  sequence:7,state:"ready",process_id:123,domain_ready:false,diagnostics:[]}));

test("a fresh observer accepts unchanged native state without trusting the old subscription", () => {
  const old = snapshot("7be93210-4107-4444-9777-aabbaa998877");
  const next = snapshot("aa993210-4107-4444-9777-aabbaa998877");
  const failed = deliveryFailure(receiveSnapshot(initialConnection, old), "PROTOCOL_ERROR");
  assert.equal(failed.delivery, "failed");
  assert.equal(failed.snapshot?.state, "ready");
  const recovered = receiveSnapshot(failed, next, true);
  assert.equal(recovered.delivery, "connected");
  assert.equal(recovered.error, null);
  assert.equal(recovered.snapshot, next);
  assert.deepEqual(receiveSnapshot(recovered, {...old, sequence:99}), recovered);
});
