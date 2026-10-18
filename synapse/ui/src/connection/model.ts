import type { BootstrapSnapshot } from "../contracts/generated/BootstrapSnapshot";

export type DiagnosticCode = NonNullable<BootstrapSnapshot["code"]>;
export type Connection = {
  snapshot: BootstrapSnapshot | null;
  error: DiagnosticCode | null;
};
export const initialConnection: Connection = { snapshot: null, error: null };

export function receiveSnapshot(
  current: Connection,
  next: BootstrapSnapshot,
): Connection {
  const previous = current.snapshot;
  if (
    previous &&
    (previous.session_id !== next.session_id ||
      next.sequence <= previous.sequence)
  )
    return current;
  return { snapshot: next, error: next.code ?? null };
}

export function connectionFailure(
  current: Connection,
  code: DiagnosticCode,
): Connection {
  return { ...current, error: code };
}
