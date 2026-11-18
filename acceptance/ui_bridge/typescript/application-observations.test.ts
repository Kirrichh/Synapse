import test from "node:test";
import assert from "node:assert/strict";
import { observe } from "../../../synapse/ui/src/application/observations.ts";
import { decodeApplicationDelivery } from "../../../synapse/ui/src/contracts/application-decode.ts";
import type { ApplicationRequest, ApplicationResult } from "../../../synapse/ui/src/application/session.ts";

const uuid = (last: number) => `f1000000-0000-4000-8000-${String(last).padStart(12, "0")}`;
const grant = uuid(1);
const identity = {session_id:uuid(2),subscription_id:uuid(3),epoch:uuid(4),workspace:"workspace-one",
  grant_id:grant,grant_version:1,source:"invocation.state",delivery_class:"StateSync",invocation_ids:["invocation-one"],generation:uuid(5)};
function checked(result: unknown): ApplicationResult {
  const value = decodeApplicationDelivery(JSON.stringify({schema_version:"synapse.application.delivery/v1",type:"result",
    subscription_id:uuid(8),request_id:uuid(9),result}));
  assert.equal(value.type,"result");
  return (value as Extract<typeof value,{type:"result"}>).result;
}
function client(results: unknown[]) {
  const calls: ApplicationRequest[] = [];
  return {calls, async run(request: ApplicationRequest) {
    calls.push(request);
    if (request.action === "delivery" && request.method === "delivery.unsubscribe") return checked({type:"delivery.closed",subscription_id:uuid(3)});
    const value = results.shift();
    if (value instanceof Error) throw value;
    return checked(value);
  }};
}
const confirm = (id = identity, position: number | null = 3) => ({type:"subscription.confirm",identity:id,deliver_from:position});
const snapshot = (position=4, id=identity, generation=uuid(5)) => ({type:"state.snapshot",identity:id,snapshot:{generation,position,items:[{invocation_id:"invocation-one",state:"ABSENT"}]}});

test("snapshot follows confirmation and disposal releases only delivery", async () => {
  const peer=client([confirm(),snapshot()]);
  const reply=await observe(peer,grant,"invocation-one","invocation.state");
  assert.equal(reply.type,"state.snapshot");
  assert.deepEqual(peer.calls.map(x=>"method" in x ? x.method : x.action),["delivery.subscribe","delivery.snapshot","delivery.unsubscribe"]);
});

test("wrong grant, filter, source, epoch, generation and backwards snapshot are refused", async () => {
  for (const responses of [
    [confirm({...identity,grant_id:uuid(7)}),snapshot(4,{...identity,grant_id:uuid(7)})],
    [confirm({...identity,invocation_ids:["other"]}),snapshot(4,{...identity,invocation_ids:["other"]})],
    [confirm(),snapshot(2)],
    [confirm(),snapshot(4,{...identity,epoch:uuid(7)})],
    [confirm(),snapshot(4,identity,uuid(7))],
    [confirm(),{...snapshot(),snapshot:{...snapshot().snapshot,items:[{invocation_id:"other",state:"ABSENT"}]}}],
  ]) {
    const peer=client(responses);
    await assert.rejects(observe(peer,grant,"invocation-one","invocation.state"),/PROTOCOL_ERROR/);
    assert.equal((peer.calls.at(-1) as any).method,"delivery.unsubscribe");
  }
});

test("retained record and live observation use their own class and fixed source", async () => {
  for (const source of ["invocation.record","connection.health"] as const) {
    const {generation:_,...base}=identity;
    const id={...base,source,delivery_class:source==="invocation.record"?"Evidential":"Live"};
    const payload=source==="invocation.record"?{state:"EXECUTION_STATE_UNKNOWN",invocation_id:"invocation-one",revision:"a".repeat(64),cancel_requested:false}:{core:"RESPONDING",monotonic_ms:42};
    const peer=client([{type:"subscription.confirm",identity:id,deliver_from:null},
      {type:"delivery.collected",subscription_id:id.subscription_id,queued:true},
      {type:"delivery.data",identity:id,gap:false,payload}]);
    assert.deepEqual((await observe(peer,grant,"invocation-one",source) as any).payload,payload);
    assert.equal(peer.calls.length,4);
    assert.throws(()=>checked({type:"delivery.data",identity:id,gap:false,payload:{generation:uuid(5),from:0,to:0,changes:[]}}),/PROTOCOL_ERROR/);
  }
});

test("collection loss and a lost reply never trigger a retry or a mutation", async () => {
  const {generation:_,...base}=identity;
  const id={...base,source:"invocation.record",delivery_class:"Evidential"};
  for (const response of [{type:"delivery.collected",subscription_id:id.subscription_id,queued:false},new Error("lost reply")]) {
    const peer=client([{type:"subscription.confirm",identity:id,deliver_from:null},response]);
    await assert.rejects(observe(peer,grant,"invocation-one","invocation.record"));
    assert.deepEqual(peer.calls.map(x=>(x as any).method),["delivery.subscribe","delivery.collect","delivery.unsubscribe"]);
  }
});
