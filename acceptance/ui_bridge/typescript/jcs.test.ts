import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { canonicalCommandText, decodeCommandValue } from "../../../synapse/ui/src/contracts/jcs.ts";

const corpus = JSON.parse(readFileSync(new URL("../jcs-corpus.json", import.meta.url), "utf8"));
for (const item of corpus) test(`JCS: ${item.case}`, () => {
  if ("canonical" in item) {
    const canonical = canonicalCommandText(decodeCommandValue(item.raw));
    assert.equal(canonical, item.canonical);
    assert.equal(canonicalCommandText(decodeCommandValue(canonical)), canonical);
  } else assert.throws(() => decodeCommandValue(item.raw), /^Error: INVALID_COMMAND_VALUE$/);
});

test("JCS refuses non-data values, accessors, cycles and exhaustion", () => {
  const cycle: unknown[] = []; cycle.push(cycle);
  for (const value of [NaN, Infinity, undefined, 1n, new Date(), cycle, { a: undefined }, [ , ],
    { get a() { throw Error("must not evaluate getter"); } }, { [Symbol("a")]: 1 }, "x".repeat(65536)]) {
    assert.throws(() => canonicalCommandText(value), /^Error: INVALID_COMMAND_VALUE$/);
  }
  assert.throws(() => decodeCommandValue("[".repeat(33) + "0" + "]".repeat(33)));
  assert.throws(() => decodeCommandValue(" ".repeat(65537)));
  assert.equal(canonicalCommandText(decodeCommandValue("[".repeat(32) + "0" + "]".repeat(32))),
    "[".repeat(32) + "0" + "]".repeat(32));
  assert.equal(canonicalCommandText({ "2": 2, "10": 10 }), '{"10":10,"2":2}');
});

test("array commands cannot hide properties or execute accessors while hashing", () => {
  let reads = 0;
  const accessor = [1];
  Object.defineProperty(accessor, "0", { enumerable: true, get() { reads++; return 1; } });
  const hidden = [1];
  Object.defineProperty(hidden, "constraint", { value: "required", enumerable: false });
  const nonEnumerable = [1];
  Object.defineProperty(nonEnumerable, "0", { value: 1, enumerable: false });
  class Derived extends Array {}
  for (const value of [accessor, hidden, nonEnumerable, new Derived(1),
    Object.assign([1], { constraint: "required" }), Object.assign([1], { [Symbol("constraint")]: true })]) {
    assert.throws(() => canonicalCommandText(value), /^Error: INVALID_COMMAND_VALUE$/);
  }
  assert.equal(reads, 0);
  assert.equal(canonicalCommandText(Object.freeze([1, { a: true }])), '[1,{"a":true}]');
});
