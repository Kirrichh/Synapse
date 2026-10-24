import { useEffect, useRef, useState } from "react";
import {
  observeDesktop,
  startDesktop,
  stopDesktop,
  forceCloseDesktop,
} from "./desktop";
import { connectionFailure, deliveryFailure, initialConnection, receiveSnapshot } from "./model";
import { decodeFailure } from "../contracts/decode";

export function useConnection() {
  const [connection, setConnection] = useState(initialConnection);
  const [pending, setPending] = useState(false);
  const [generation, setGeneration] = useState(0);
  const dispose = useRef(() => {});
  const started = useRef(false);
  useEffect(() => {
    let active = true;
    const close = observeDesktop(
      (snapshot, initial) => {
        setConnection((current) => receiveSnapshot(current, snapshot, initial));
        // Initial application startup only. Reconnecting a view never starts
        // a process; remounting a stopped/failed session does not restart it.
        if (generation === 0 && !started.current && snapshot.state === "native") {
          started.current = true;
          void startDesktop().catch((failure) => {
            if (active) setConnection((current) => connectionFailure(current, decodeFailure(failure)));
          });
        }
      },
      (code) => setConnection((current) => deliveryFailure(current, code)),
    );
    dispose.current = () => { active = false; close(); };
    return dispose.current;
  }, [generation]);
  function reconnect() {
    dispose.current();
    setConnection((current) => ({ ...current, error: null, delivery: "connecting" }));
    setGeneration((current) => current + 1);
  }
  async function act(operation: () => Promise<void>) {
    if (pending) return;
    setPending(true);
    try {
      await operation();
    } catch (failure) {
      setConnection((current) => connectionFailure(current, decodeFailure(failure)));
    } finally {
      setPending(false);
    }
  }
  return {
    connection,
    pending,
    reconnect,
    start: () => act(startDesktop),
    stop: () => act(stopDesktop),
    forceClose: () => act(forceCloseDesktop),
  };
}
