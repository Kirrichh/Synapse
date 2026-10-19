import test from "node:test";
import assert from "node:assert/strict";
import { ApplicationSession, type ApplicationPort } from "../../../synapse/ui/src/application/session.ts";
import { decodeApplicationDelivery } from "../../../synapse/ui/src/contracts/application-decode.ts";

const subscription = "f1000000-0000-4000-8000-000000000001";
const requestId = "f1000000-0000-4000-8000-000000000002";
const old = "f1000000-0000-4000-8000-000000000003";
function reply(overrides: object = {}) {
  return JSON.stringify({ schema_version: "synapse.application.delivery/v1", type: "result",
    subscription_id: subscription, request_id: requestId, result: { grants: [] }, ...overrides });
}
function port() {
  let receive = (_text: string) => {};
  const calls: unknown[] = [];
  const value: ApplicationPort = {
    async subscribe(callback) { receive = callback; return subscription; },
    async request(id, request) { calls.push([id, request]); return requestId; },
    async acknowledge(id, request) { calls.push(["ack", id, request]); },
    async unsubscribe(id) { calls.push(["unsubscribe", id]); },
  };
  return { value, calls, emit: (text: string) => receive(text) };
}
const turn = () => new Promise<void>(resolve => setImmediate(resolve));

test("early Channel result waits for the native request identity and acknowledges exactly once", async () => {
  const peer = port();
  peer.value.request = async () => { peer.emit(reply()); return requestId; };
  const session = new ApplicationSession(peer.value);
  assert.deepEqual(await session.run({ action: "grants" }), { grants: [] });
  assert.deepEqual(peer.calls, [["ack", subscription, requestId]]);
  session.close();
});

test("unacknowledged result prevents another request and an old subscription is ignored", async t => {
  const peer = port();
  let acknowledge!: () => void;
  peer.value.acknowledge = () => new Promise<void>(resolve => { acknowledge = resolve; });
  const session = new ApplicationSession(peer.value);
  t.after(() => session.close());
  const pending = session.run({ action: "grants" });
  void pending.catch(() => {});
  await turn();
  peer.emit(reply({ subscription_id: old }));
  await turn();
  assert.equal(acknowledge, undefined);
  await assert.rejects(session.run({ action: "grants" }), /BUSY/);
  peer.emit(reply());
  await turn();
  await assert.rejects(session.run({ action: "grants" }), /BUSY/);
  acknowledge();
  assert.deepEqual(await pending, { grants: [] });
  assert.equal(peer.calls.length, 1);
  session.close();
});

test("wrong correlation closes delivery without a second request or automatic retry", async () => {
  const peer = port();
  const session = new ApplicationSession(peer.value);
  const pending = session.run({ action: "grants" });
  await turn();
  peer.emit(reply({ request_id: old }));
  await assert.rejects(pending, /PROTOCOL_ERROR/);
  assert.equal(peer.calls.filter(call => Array.isArray(call) && call[0] === subscription).length, 1);
  assert.deepEqual(peer.calls.at(-1), ["unsubscribe", subscription]);
  await assert.rejects(session.run({ action: "grants" }), /DELIVERY_FAILED/);
});

test("synchronous refusal releases the slot and raw exceptions cannot enter user diagnostics", async () => {
  const peer = port();
  peer.value.request = () => { throw new Error("private-path-canary"); };
  const session = new ApplicationSession(peer.value);
  await assert.rejects(session.run({ action: "grants" }), /^Error: PROTOCOL_ERROR$/);
  peer.value.request = async () => { peer.emit(reply()); return requestId; };
  assert.deepEqual(await session.run({ action: "grants" }), { grants: [] });
  session.close();
});

test("closed vocabulary, equal duplicate keys and oversized Channel data are refused", () => {
  for (const text of [reply({ extra: true }), reply({ result: { authorized: true } }),
    reply().replace('"type":"result"', '"type":"result","type":"result"'),
    reply({ result: { grants: [], secret: "x".repeat(65536) } })]) {
    assert.throws(() => decodeApplicationDelivery(text), /PROTOCOL_ERROR/);
  }
  const error = { schema_version: "synapse.application.delivery/v1", type: "error",
    subscription_id: subscription, request_id: requestId, code: "ACCESS_DENIED" };
  assert.equal(decodeApplicationDelivery(JSON.stringify(error)).type, "error");
  assert.throws(() => decodeApplicationDelivery(JSON.stringify({ ...error, code: "private-path-canary" })), /PROTOCOL_ERROR/);
});
