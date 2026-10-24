import { decodeCommandValue } from "./jcs";
import { validateApplicationDelivery } from "./generated/validators.js";
import { applicationErrors } from "./generated/application-errors";
import type { ApplicationDelivery, ErrorCode } from "./generated/ApplicationDelivery";

export type ApplicationFailure = ErrorCode | "DESKTOP_REQUIRED";
export class ApplicationError extends Error {
  constructor(readonly code: ApplicationFailure) { super(code); }
}
export function applicationFailure(value: unknown): ApplicationError {
  if (value instanceof ApplicationError) return value;
  return new ApplicationError(typeof value === "string" && applicationErrors.some(code => code === value)
    ? value as ErrorCode : "PROTOCOL_ERROR");
}
export function decodeApplicationDelivery(text: string): ApplicationDelivery {
  let value: unknown;
  try { value = decodeCommandValue(text); } catch { throw new ApplicationError("PROTOCOL_ERROR"); }
  if (!validateApplicationDelivery(value)) throw new ApplicationError("PROTOCOL_ERROR");
  return value as ApplicationDelivery;
}
