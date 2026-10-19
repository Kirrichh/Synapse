import { parse } from "lossless-json";
import { requireUniqueJsonKeys } from "./json-keys.ts";
import { validateBootstrap, validateEnvelope } from "./generated/validators.js";
import type { MachineEnvelope } from "./generated/MachineEnvelope";
import type { BootstrapSnapshot } from "./generated/BootstrapSnapshot";
import { profile } from "./generated/profile";
import { diagnosticCodes } from "./generated/diagnostics";

export class ContractError extends Error {
  constructor() {
    super("PROTOCOL_ERROR");
  }
}

export function decodeFailure(value: unknown): NonNullable<BootstrapSnapshot["code"]> {
  return typeof value === "string" && diagnosticCodes.some((code) => code === value)
    ? value as NonNullable<BootstrapSnapshot["code"]> : "INTERNAL_ERROR";
}

function inspectText(text: string): void {
  if (text.length > profile.max_frame_bytes || new TextEncoder().encode(text).length > profile.max_frame_bytes)
    throw new ContractError();
  let depth = 0,
    quoted = false,
    escaped = false;
  for (const char of text) {
    if (quoted) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') quoted = false;
    } else if (char === '"') quoted = true;
    else if (char === "[" || char === "{") {
      depth += 1;
      if (depth > profile.max_json_depth) throw new ContractError();
    } else if (char === "]" || char === "}") depth -= 1;
  }
}

function validUnicode(text: string): boolean {
  for (let index = 0; index < text.length; index += 1) {
    const unit = text.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = text.charCodeAt(++index);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
  }
  return true;
}

function strictJson(text: string): unknown {
  inspectText(text);
  try { requireUniqueJsonKeys(text); } catch { throw new ContractError(); }
  const value = parse(text, null, {
    parseNumber(token) {
      if (token === "-0" || !/^-?(0|[1-9][0-9]*)$/.test(token)) throw new ContractError();
      const number = Number(token);
      if (!Number.isSafeInteger(number)) throw new ContractError();
      return number;
    },
  });
  const pending: unknown[] = [value];
  while (pending.length) {
    const item = pending.pop();
    if (typeof item === "string" && !validUnicode(item))
      throw new ContractError();
    if (item && typeof item === "object") {
      for (const [key, child] of Object.entries(item)) {
        if (!validUnicode(key)) throw new ContractError();
        pending.push(child);
      }
    }
  }
  return value;
}

export function decodeEnvelope(text: string): MachineEnvelope {
  const value = strictJson(text);
  if (!validateEnvelope(value)) throw new ContractError();
  return value as MachineEnvelope;
}

export function decodeSnapshot(text: string): BootstrapSnapshot {
  const value = strictJson(text);
  if (!validateBootstrap(value)) throw new ContractError();
  const snapshot = value as BootstrapSnapshot;
  if (snapshot.protocol_message) decodeEnvelope(snapshot.protocol_message);
  return snapshot;
}
