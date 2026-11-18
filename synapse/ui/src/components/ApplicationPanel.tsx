import { useEffect, useState } from "react";
import { useApplication } from "../application/useApplication";
import { canonicalCommandText } from "../contracts/jcs";
import { ApplicationError } from "../contracts/application-decode";
import type { ApplicationResult } from "../application/session";
import { observe } from "../application/observations";

type Observation = Extract<ApplicationResult, { invocation_id: string; revision: string }>;
type Pending = Extract<ApplicationResult, { intents: unknown }>;

export function ApplicationPanel({ connected }: { connected: boolean }) {
  const model = useApplication(connected);
  const [selection, setSelection] = useState("");
  const [observed, setObserved] = useState<Observation | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [outcome, setOutcome] = useState<ApplicationResult | null>(null);
  const [connectionObserved, setConnectionObserved] = useState(false);
  const grant = model.grants.find(item => item.grant_id === selection);
  useEffect(() => { setSelection(""); setObserved(null); setPending(null); setOutcome(null); setConnectionObserved(false); }, [model.grants]);

  async function inspect(id: string) {
    if (!grant) return;
    setObserved(null); setOutcome(null); setConnectionObserved(false);
    const result = await model.perform(async current => {
      if (grant.methods.includes("invocation.snapshot")) await observe(current, grant.grant_id, id, "invocation.state");
      const record = await observe(current, grant.grant_id, id, "invocation.record");
      const health = await observe(current, grant.grant_id, id, "connection.health");
      if (record.type !== "delivery.data" || !("revision" in record.payload)
          || health.type !== "delivery.data" || !("core" in health.payload)) throw new ApplicationError("PROTOCOL_ERROR");
      return { record: record.payload, responding: health.payload.core === "RESPONDING" };
    });
    if (result) { setObserved(result.record); setConnectionObserved(result.responding); }
  }
  async function loadPending(after?: string) {
    if (!grant) return;
    const result = await model.request({ action: "pending", grant_id: grant.grant_id, ...(after ? { after_intent: after } : {}) });
    if (result && "intents" in result) setPending(result);
  }
  async function cancel() {
    if (!grant || !observed) return;
    setObserved(null);
    const result = await model.perform(async current => {
      const issued = await current.run({ action: "issue", grant_id: grant.grant_id });
      if (!("intent_id" in issued)) throw new ApplicationError("PROTOCOL_ERROR");
      const draft = canonicalCommandText({ method: "invocation.cancel", method_version: "1",
        recipient: observed.invocation_id, expected_revision: observed.revision,
        params: { invocation_id: observed.invocation_id } });
      return current.run({ action: "submit", grant_id: grant.grant_id, intent_id: issued.intent_id, draft });
    });
    if (result) setOutcome(result);
  }
  async function recover(intent: string, resend: boolean) {
    if (!grant) return;
    const result = await model.request({ action: "recover", grant_id: grant.grant_id,
      intent_id: intent, resend_if_absent: resend });
    if (result) setOutcome(result);
  }

  return <section className="application-panel" aria-label="Authorized invocation access">
    <h2>Invocation access</h2>
    <p>Access is granted by the installation administrator. Connecting to the core grants no workspace rights.</p>
    {model.key && <details><summary>Application registration key</summary><code className="application-key">{model.key}</code></details>}
    <button disabled={!connected || model.busy} onClick={() => void model.refresh()}>Refresh access</button>
    {model.error && <p role="alert">Access or delivery needs attention: {model.error}. Refresh access and recover an existing command before requesting it again.</p>}
    {model.loaded && model.grants.length === 0 && <p>No active workspace grants.</p>}
    {model.grants.length > 0 && <label>Workspace grant
      <select disabled={model.busy} value={selection} onChange={event => {
        setSelection(event.target.value); setObserved(null); setPending(null); setOutcome(null); setConnectionObserved(false);
      }}>
        <option value="">Select granted access</option>
        {model.grants.map(item => <option key={item.grant_id} value={item.grant_id}>{item.workspace} · {item.grant_id}</option>)}
      </select>
    </label>}
    {grant && <>
      <p>Allowed operations: {grant.methods.join(", ")}</p>
      <ul>{grant.objects.map(id => <li key={id}><code>{id}</code>{" "}
        <button disabled={model.busy || !grant.methods.includes("invocation.inspect")} onClick={() => void inspect(id)}>Inspect</button>
      </li>)}</ul>
      <button disabled={model.busy} onClick={() => void loadPending()}>Recover saved commands</button>
    </>}
    {observed && <div>
      {connectionObserved && <p>The core responded at the last refresh. This does not establish that the invocation is running.</p>}
      <p>{observed.invocation_id}: {observed.state}. Cancellation requested: {observed.cancel_requested ? "yes" : "no"}.</p>
      {observed.state === "TERMINAL" && <details><summary>Retained owner record</summary><pre>{observed.record}</pre></details>}
      <button disabled={model.busy || observed.cancel_requested || !grant?.methods.includes("invocation.cancel")}
        onClick={() => void cancel()}>Request cancellation</button>
    </div>}
    {pending && <div><h3>Saved command identities</h3>
      {pending.intents.length === 0 && <p>No saved commands on this page.</p>}
      <ul>{pending.intents.map(intent => <li key={intent.intent_id}>
        <code>{intent.command_id}</code> — {intent.prepared ? "Prepared" : "Not submitted"}{" "}
        <button disabled={model.busy} onClick={() => void recover(intent.intent_id, false)}>Check outcome</button>
      </li>)}</ul>
      {pending.next_after && <button disabled={model.busy} onClick={() => void loadPending(pending.next_after!)}>Next page</button>}
    </div>}
    {outcome && "outcome" in outcome && <p role="status">{outcome.outcome} · {outcome.command_id}{outcome.replayed ? " · retained result" : ""}</p>}
    {outcome && "state" in outcome && outcome.state === "ABSENT" && "intent_id" in outcome && <div>
      <p>The owner confirmed no acceptance for this saved command.</p>
      <button disabled={model.busy} onClick={() => void recover(outcome.intent_id, true)}>Resend the same command</button>
    </div>}
    {outcome && "state" in outcome && outcome.state === "UNPREPARED" && <p>This intent has no submitted command.</p>}
  </section>;
}
