import { useEffect, useRef, useState } from "react";
import { applicationFailure } from "../contracts/application-decode";
import { applicationPublicKey, openApplication } from "./desktop";
import type { ApplicationRequest, ApplicationResult, ApplicationSession } from "./session";
import type { ResultAccessGrants } from "../contracts/generated/ApplicationDelivery";

/** Renderer lifetime only. Authority and durable intent lifetime belong elsewhere. */
export function useApplication(connected: boolean) {
  const session = useRef<ApplicationSession | null>(null);
  const generation = useRef(0);
  const occupied = useRef(false);
  const [key, setKey] = useState<string | null>(null);
  const [grants, setGrants] = useState<ResultAccessGrants["grants"]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void applicationPublicKey().then(value => { if (active) setKey(value); }).catch(() => {});
    return () => { active = false; };
  }, []);
  useEffect(() => {
    generation.current += 1;
    occupied.current = false;
    setGrants([]); setLoaded(false); setError(null); setBusy(false);
    session.current = connected ? openApplication() : null;
    return () => { generation.current += 1; session.current?.close(); session.current = null; };
  }, [connected]);

  async function perform<T>(operation: (current: ApplicationSession) => Promise<T>): Promise<T | undefined> {
    const current = session.current;
    if (!current || occupied.current) return;
    const version = generation.current;
    occupied.current = true;
    setBusy(true); setError(null);
    try {
      const result = await operation(current);
      return version === generation.current ? result : undefined;
    } catch (error) {
      if (version === generation.current) {
        setError(applicationFailure(error).code);
        setGrants([]); setLoaded(false);
      }
      return undefined;
    } finally { if (version === generation.current) { occupied.current = false; setBusy(false); } }
  }

  async function refresh() {
    if (!connected || occupied.current) return;
    session.current?.close();
    session.current = openApplication();
    generation.current += 1;
    setGrants([]); setLoaded(false);
    const value = await perform(current => current.run({ action: "grants" }));
    if (value && "grants" in value) { setGrants(value.grants); setLoaded(true); }
  }

  const request = (value: ApplicationRequest): Promise<ApplicationResult | undefined> =>
    perform(current => current.run(value));
  return { key, grants, loaded, busy, error, refresh, request, perform };
}
