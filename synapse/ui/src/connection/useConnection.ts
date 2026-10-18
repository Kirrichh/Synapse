import { useEffect, useState } from "react";
import {
  observeDesktop,
  startDesktop,
  stopDesktop,
  forceCloseDesktop,
} from "./desktop";
import { connectionFailure, initialConnection, receiveSnapshot } from "./model";
import { decodeFailure } from "../contracts/decode";

export function useConnection() {
  const [connection, setConnection] = useState(initialConnection);
  const [pending, setPending] = useState(false);
  useEffect(
    () =>
      observeDesktop(
        (snapshot) =>
          setConnection((current) => receiveSnapshot(current, snapshot)),
        (code) => setConnection((current) => connectionFailure(current, code)),
      ),
    [],
  );
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
    start: () => act(startDesktop),
    stop: () => act(stopDesktop),
    forceClose: () => act(forceCloseDesktop),
  };
}
