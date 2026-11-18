import { ApplicationError } from "../contracts/application-decode";
import { canonicalCommandText } from "../contracts/jcs";
import type { ApplicationResult, ApplicationSession } from "./session";

type Source = "invocation.state" | "invocation.record" | "connection.health";
type Confirmation = Extract<ApplicationResult, { type: "subscription.confirm" }>;
type Snapshot = Extract<ApplicationResult, { type: "state.snapshot" }>;
type Data = Extract<ApplicationResult, { type: "delivery.data" }>;
type Client = Pick<ApplicationSession, "run">;

/** One scoped observation. No automatic retry, command or inferred task liveness.
 * Later refreshes establish a new epoch rather than reusing unavailable coverage.
 */
export async function observe(client: Client, grant: string, invocation: string, source: Source): Promise<Snapshot | Data> {
  const opened = await client.run({ action: "read", grant_id: grant, method: "delivery.subscribe",
    params: { source, invocation_ids: [invocation] } });
  if (!("type" in opened) || opened.type !== "subscription.confirm") throw new ApplicationError("PROTOCOL_ERROR");
  const confirm: Confirmation = opened;
  const identity = confirm.identity;
  const params = { subscription_id: identity.subscription_id };
  try {
    if (identity.grant_id !== grant || identity.source !== source || identity.invocation_ids.length !== 1
        || identity.invocation_ids[0] !== invocation) throw new ApplicationError("PROTOCOL_ERROR");
    if (source === "invocation.state") {
      const reply = await client.run({ action: "delivery", method: "delivery.snapshot", params });
      if (!("type" in reply) || reply.type !== "state.snapshot"
          || canonicalCommandText(reply.identity) !== canonicalCommandText(identity)
          || identity.delivery_class !== "StateSync" || confirm.deliver_from === null
          || reply.snapshot.generation !== identity.generation
          || reply.snapshot.position < confirm.deliver_from
          || reply.snapshot.items.length !== 1 || reply.snapshot.items[0].invocation_id !== invocation) throw new ApplicationError("PROTOCOL_ERROR");
      return reply;
    }
    const collected = await client.run({ action: "delivery", method: "delivery.collect", params });
    if (!("type" in collected) || collected.type !== "delivery.collected"
        || collected.subscription_id !== identity.subscription_id) throw new ApplicationError("PROTOCOL_ERROR");
    if (!collected.queued) throw new ApplicationError("CURSOR_UNAVAILABLE");
    const reply = await client.run({ action: "delivery", method: "delivery.next", params: {} });
    if (!("type" in reply) || reply.type !== "delivery.data"
        || canonicalCommandText(reply.identity) !== canonicalCommandText(identity)) throw new ApplicationError("PROTOCOL_ERROR");
    if (source === "invocation.record" && (!("invocation_id" in reply.payload)
        || reply.payload.invocation_id !== invocation)) throw new ApplicationError("PROTOCOL_ERROR");
    return reply;
  } finally {
    // Disposal never cancels an invocation or recreates a failed connection.
    await client.run({ action: "delivery", method: "delivery.unsubscribe", params }).catch(() => {});
  }
}
