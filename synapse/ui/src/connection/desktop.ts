import { Channel, invoke, isTauri } from "@tauri-apps/api/core";
import { decodeSnapshot, decodeFailure } from "../contracts/decode";
import type { BootstrapSnapshot } from "../contracts/generated/BootstrapSnapshot";
import type { DiagnosticCode } from "./model";

export function observeDesktop(
  onSnapshot: (value: BootstrapSnapshot) => void,
  onFailure: (code: DiagnosticCode) => void,
): () => void {
  let closed = false;
  if (!isTauri()) {
    onFailure("DESKTOP_REQUIRED");
    return () => {
      closed = true;
    };
  }
  const channel = new Channel<string>();
  const accept = (text: string, acknowledge = false) => {
    if (closed) return;
    try {
      const snapshot = decodeSnapshot(text);
      onSnapshot(snapshot);
      if (acknowledge) {
        void invoke("desktop_ack", { subscriptionId: snapshot.subscription_id, sequence: snapshot.sequence })
          .catch(() => { if (!closed) onFailure("PROTOCOL_ERROR"); });
      }
    } catch {
      closed = true;
      onFailure("PROTOCOL_ERROR");
    }
  };
  channel.onmessage = (text) => accept(text, true);
  invoke<string>("desktop_subscribe", { channel })
    .then((text) => accept(text))
    .then(() => {
      if (!closed) return invoke("desktop_start");
    })
    .catch((failure) => {
      if (!closed) onFailure(decodeFailure(failure));
    });
  return () => {
    closed = true;
  };
}

export async function startDesktop(): Promise<void> {
  await invoke("desktop_start");
}
export async function stopDesktop(): Promise<void> {
  await invoke("desktop_stop");
}
export async function forceCloseDesktop(): Promise<void> {
  await invoke("desktop_force_close");
}
