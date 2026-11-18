import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { decodeSnapshot, decodeFailure } from "../../../synapse/ui/src/contracts/decode.ts";
import { errors } from "../../../synapse/ui/src/i18n.ts";
const schema = JSON.parse(readFileSync(new URL("../../../synapse/machine/schemas/envelope.schema.json", import.meta.url), "utf8"));
test("declared machine refusals reach the renderer without replacing their code", () => {
  for (const code of schema.$defs.errorCode.enum) {
    const snapshot = decodeSnapshot(JSON.stringify({schema_version:"1.0",
      session_id:"7be93210-4107-4444-9777-aabbaa998877",subscription_id:"7be93210-4107-4444-9777-aabbaa998877",
      sequence:1,state:"ready",domain_ready:false,diagnostics:[],code}));
    assert.equal(snapshot.code, code);
    assert.equal(decodeFailure(code), code);
    assert.ok(errors.en[snapshot.code!]);
    assert.ok(errors.ru[snapshot.code!]);
  }
  assert.equal(decodeFailure("PRIVATE_VALUE"), "INTERNAL_ERROR");
});
