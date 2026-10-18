import type { BootstrapSnapshot } from "../contracts/generated/BootstrapSnapshot";

export type DiagnosticCode = NonNullable<BootstrapSnapshot["code"]>;
export type Connection = {
  snapshot: BootstrapSnapshot | null;
  error: DiagnosticCode | null;
  delivery: "connecting" | "connected" | "failed";
};
export const initialConnection: Connection = { snapshot: null, error: null, delivery: "connecting" };

export function receiveSnapshot(
  current: Connection,
  next: BootstrapSnapshot,
  initial = false,
): Connection {
  const previous = current.snapshot;
  if (
    !initial && previous &&
    (previous.session_id !== next.session_id ||
      previous.subscription_id !== next.subscription_id ||
      next.sequence <= previous.sequence)
  )
    return current;
  return { snapshot: next, error: next.code ?? null, delivery: "connected" };
}

export function deliveryFailure(current: Connection, code: DiagnosticCode): Connection {
  return { ...current, error: code, delivery: "failed" };
}

export function connectionFailure(
  current: Connection,
  code: DiagnosticCode,
): Connection {
  return { ...current, error: code };
}
