import { Channel, invoke, isTauri } from "@tauri-apps/api/core";
import { decodeSnapshot, decodeFailure } from "../contracts/decode";
import type { BootstrapSnapshot } from "../contracts/generated/BootstrapSnapshot";
import type { DiagnosticCode } from "./model";

export function observeDesktop(
  onSnapshot: (value: BootstrapSnapshot, initial: boolean) => void,
  onFailure: (code: DiagnosticCode) => void,
): () => void {
  let closed = false;
  let latest: BootstrapSnapshot | undefined;
  const fail = (code: DiagnosticCode) => {
    if (closed) return;
    closed = true;
    onFailure(code);
  };
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
      if (latest && (snapshot.session_id !== latest.session_id ||
        snapshot.subscription_id !== latest.subscription_id)) return;
      if (!latest || snapshot.sequence > latest.sequence) {
        const initial = latest === undefined;
        latest = snapshot;
        onSnapshot(snapshot, initial);
      }
      // Only channel deliveries occupy the native ACK slot. The subscribe
      // response can duplicate a delivery but must never release that slot.
      if (acknowledge) {
        void invoke("desktop_ack", { subscriptionId: snapshot.subscription_id, sequence: snapshot.sequence })
          .catch(() => fail("PROTOCOL_ERROR"));
      }
    } catch {
      fail("PROTOCOL_ERROR");
    }
  };
  channel.onmessage = (text) => accept(text, true);
  invoke<string>("desktop_subscribe", { channel })
    .then((text) => accept(text))
    .catch((failure) => {
      fail(decodeFailure(failure));
    });
  return () => {
    closed = true;
    // Native subscribe atomically replaces its one subscriber. Discard any
    // queued callback from this generation, including late ACK rejections.
    channel.onmessage = () => {};
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
