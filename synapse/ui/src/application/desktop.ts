import { Channel, invoke, isTauri } from "@tauri-apps/api/core";
import { canonicalCommandText } from "../contracts/jcs";
import { ApplicationError } from "../contracts/application-decode";
import { ApplicationSession, type ApplicationPort } from "./session";

export function openApplication(): ApplicationSession {
  const channel = new Channel<string>();
  const port: ApplicationPort = {
    async subscribe(receive) {
      if (!isTauri()) throw new ApplicationError("DESKTOP_REQUIRED");
      channel.onmessage = receive;
      return invoke<string>("desktop_application_subscribe", { channel });
    },
    request: (subscriptionId, request) => invoke<string>("desktop_application", {
      subscriptionId, request: canonicalCommandText(request),
    }),
    acknowledge: (subscriptionId, requestId) => invoke("desktop_application_ack", { subscriptionId, requestId }),
    unsubscribe: async subscriptionId => {
      channel.onmessage = () => {};
      await invoke("desktop_application_unsubscribe", { subscriptionId });
    },
  };
  return new ApplicationSession(port);
}

export async function applicationPublicKey(): Promise<string> {
  if (!isTauri()) throw new ApplicationError("DESKTOP_REQUIRED");
  const key = await invoke<unknown>("desktop_identity");
  if (typeof key !== "string" || key.length !== 64 || /[^0-9a-f]/.test(key))
    throw new ApplicationError("PROTOCOL_ERROR");
  return key;
}
