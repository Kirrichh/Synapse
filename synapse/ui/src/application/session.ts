import { ApplicationError, applicationFailure, decodeApplicationDelivery } from "../contracts/application-decode";
import type { ApplicationDelivery } from "../contracts/generated/ApplicationDelivery";
import { profile } from "../contracts/generated/profile";

export type ApplicationResult = Extract<ApplicationDelivery, { type: "result" }>["result"];
export type ApplicationRequest =
  | { action: "grants" }
  | { action: "read"; grant_id: string; method: "invocation.inspect" | "invocation.snapshot" | "invocation.changes" | "command.status" | "delivery.subscribe"; params: unknown }
  | { action: "delivery"; method: "delivery.snapshot" | "delivery.collect" | "delivery.next" | "delivery.unsubscribe"; params: unknown }
  | { action: "issue"; grant_id: string }
  | { action: "pending"; grant_id: string; after_intent?: string }
  | { action: "submit"; grant_id: string; intent_id: string; draft: string }
  | { action: "recover"; grant_id: string; intent_id: string; resend_if_absent: boolean };

export interface ApplicationPort {
  subscribe(receive: (text: string) => void): Promise<string>;
  request(subscription: string, request: ApplicationRequest): Promise<string>;
  acknowledge(subscription: string, request: string): Promise<void>;
  unsubscribe(subscription: string): Promise<void>;
}
type Pending = {
  id?: string;
  early?: ApplicationDelivery;
  accepting: boolean;
  resolve: (value: ApplicationResult) => void;
  reject: (error: ApplicationError) => void;
  timer: ReturnType<typeof setTimeout>;
};

/** One operation at a time. Timeout/close never resends or invents an intent. */
export class ApplicationSession {
  private subscription?: string;
  private ready: Promise<string>;
  private pending?: Pending;
  private closed = false;

  constructor(private readonly port: ApplicationPort) {
    this.ready = port.subscribe(text => this.receive(text)).then(id => {
      this.subscription = id;
      if (this.closed) void port.unsubscribe(id).catch(() => {});
      return id;
    });
    // A rejected startup is observed by run(); never become an unhandled promise.
    void this.ready.catch(() => {});
  }

  async run(request: ApplicationRequest): Promise<ApplicationResult> {
    const subscription = await this.ready.catch(error => { throw applicationFailure(error); });
    if (this.closed) throw new ApplicationError("DELIVERY_FAILED");
    if (this.pending) throw new ApplicationError("BUSY");
    return new Promise<ApplicationResult>((resolve, reject) => {
      const pending: Pending = { resolve, reject, accepting: false,
        timer: setTimeout(() => this.fail(new ApplicationError("DELIVERY_TIMEOUT")), profile.application_timeout_seconds * 1000) };
      this.pending = pending;
      Promise.resolve().then(() => this.port.request(subscription, request)).then(id => {
        if (this.pending !== pending || this.closed) return;
        pending.id = id;
        if (pending.early) void this.accept(pending.early);
      }).catch(error => {
        if (this.pending !== pending) return;
        clearTimeout(pending.timer);
        this.pending = undefined;
        reject(applicationFailure(error));
      });
    });
  }

  private receive(text: string): void {
    if (this.closed) return;
    let value: ApplicationDelivery;
    try { value = decodeApplicationDelivery(text); } catch { this.fail(new ApplicationError("PROTOCOL_ERROR")); return; }
    if (value.subscription_id !== this.subscription) return;
    const pending = this.pending;
    if (!pending) return;
    if (!pending.id) {
      if (pending.early) this.fail(new ApplicationError("PROTOCOL_ERROR"));
      else pending.early = value;
      return;
    }
    void this.accept(value);
  }

  private async accept(value: ApplicationDelivery): Promise<void> {
    const pending = this.pending;
    if (!pending || this.closed) return;
    if (pending.id !== value.request_id || pending.accepting) {
      this.fail(new ApplicationError("PROTOCOL_ERROR")); return;
    }
    pending.accepting = true;
    try { await this.port.acknowledge(value.subscription_id, value.request_id); }
    catch { this.fail(new ApplicationError("DELIVERY_FAILED")); return; }
    if (this.pending !== pending || this.closed) return;
    clearTimeout(pending.timer);
    this.pending = undefined;
    if (value.type === "error") pending.reject(new ApplicationError(value.code));
    else pending.resolve(value.result);
  }

  private fail(error: ApplicationError): void {
    const pending = this.pending;
    this.pending = undefined;
    if (pending) { clearTimeout(pending.timer); pending.reject(error); }
    this.close();
  }
  close(): void {
    if (this.closed) return;
    this.closed = true;
    const pending = this.pending;
    this.pending = undefined;
    if (pending) { clearTimeout(pending.timer); pending.reject(new ApplicationError("DELIVERY_FAILED")); }
    if (this.subscription) void this.port.unsubscribe(this.subscription).catch(() => {});
  }
}
