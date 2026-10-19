import { parse } from "lossless-json";
import { requireUniqueJsonKeys } from "./json-keys.ts";

const MAX_BYTES = 65536;
const MAX_DEPTH = 32;

export class InvalidCommandValue extends Error {
  constructor() { super("INVALID_COMMAND_VALUE"); }
}

function decimal(token: string): string {
  const match = /^(-?)([0-9]+)(?:\.([0-9]+))?(?:[eE]([+-]?[0-9]+))?$/.exec(token);
  if (!match) throw new InvalidCommandValue();
  const digits = (match[2] + (match[3] ?? "")).replace(/^0+/, "");
  if (!digits) return "0";
  const trimmed = digits.replace(/0+$/, "");
  const exponent = BigInt(match[4] ?? "0") + BigInt(digits.length - trimmed.length - (match[3]?.length ?? 0));
  return `${match[1]}${trimmed}e${exponent}`;
}

function string(value: string): string {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(++index);
      if (!(next >= 0xdc00 && next <= 0xdfff)) throw new InvalidCommandValue();
    } else if (unit >= 0xdc00 && unit <= 0xdfff) throw new InvalidCommandValue();
  }
  return JSON.stringify(value);
}

export function canonicalCommandText(value: unknown): string {
  let count = 0;
  function encode(item: unknown, depth: number): string {
    if (++count > MAX_BYTES) throw new InvalidCommandValue();
    if (item === null) return "null";
    if (typeof item === "string") return string(item);
    if (typeof item === "boolean") return item ? "true" : "false";
    if (typeof item === "number") {
      if (!Number.isFinite(item)) throw new InvalidCommandValue();
      return JSON.stringify(item);
    }
    if (typeof item !== "object" || depth >= MAX_DEPTH) throw new InvalidCommandValue();
    if (Array.isArray(item)) {
      if (Object.getPrototypeOf(item) !== Array.prototype || Object.getOwnPropertySymbols(item).length)
        throw new InvalidCommandValue();
      const properties = Object.getOwnPropertyDescriptors(item);
      if (Object.keys(properties).length !== item.length + 1) throw new InvalidCommandValue();
      const values: string[] = [];
      for (let index = 0; index < item.length; index += 1) {
        const property = properties[String(index)];
        if (!property || !property.enumerable || !("value" in property)) throw new InvalidCommandValue();
        values.push(encode(property.value, depth + 1));
      }
      return `[${values.join(",")}]`;
    }
    const prototype = Object.getPrototypeOf(item);
    if (prototype !== Object.prototype && prototype !== null) throw new InvalidCommandValue();
    const properties = Object.getOwnPropertyDescriptors(item);
    const values: string[] = [];
    // Default JS ordering compares UTF-16 code units. Build the output directly:
    // creating a sorted object would reorder integer-like keys again.
    for (const key of Object.keys(properties).sort()) {
      const property = properties[key];
      if (!property.enumerable || !("value" in property)) throw new InvalidCommandValue();
      values.push(`${string(key)}:${encode(property.value, depth + 1)}`);
    }
    if (Object.getOwnPropertySymbols(item).length) throw new InvalidCommandValue();
    return `{${values.join(",")}}`;
  }
  const text = encode(value, 0);
  if (new TextEncoder().encode(text).length > MAX_BYTES) throw new InvalidCommandValue();
  return text;
}

export function decodeCommandValue(text: string): unknown {
  if (!text.length || text.length > MAX_BYTES || new TextEncoder().encode(text).length > MAX_BYTES)
    throw new InvalidCommandValue();
  let depth = 0, quoted = false, escaped = false;
  for (const char of text) {
    if (quoted) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') quoted = false;
    } else if (char === '"') quoted = true;
    else if (char === "[" || char === "{") {
      if (++depth > MAX_DEPTH) throw new InvalidCommandValue();
    } else if (char === "]" || char === "}") depth -= 1;
  }
  try {
    requireUniqueJsonKeys(text);
    const value = parse(text, null, {
      parseNumber(token) {
        const number = Number(token);
        if (!Number.isFinite(number) || decimal(token) !== decimal(JSON.stringify(number)))
          throw new InvalidCommandValue();
        return number;
      },
    });
    canonicalCommandText(value);
    return value;
  } catch {
    throw new InvalidCommandValue();
  }
}
