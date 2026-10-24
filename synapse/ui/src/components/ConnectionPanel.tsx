import { copy, errors, type Language } from "../i18n";
import type { Connection } from "../connection/model";

type Props = {
  language: Language;
  connection: Connection;
  pending: boolean;
  start: () => void;
  reconnect: () => void;
  stop: () => void;
  forceClose: () => void;
};

export function ConnectionPanel({
  language,
  connection,
  pending,
  start,
  reconnect,
  stop,
  forceClose,
}: Props) {
  const t = copy[language];
  const snapshot = connection.snapshot;
  const connected = connection.delivery === "connected";
  const state = connection.delivery === "failed" ? "fail" : (snapshot?.state ?? "native");
  const hint = connection.error
    ? errors[language][connection.error]
    : state === "ready"
      ? t.readyHint
      : state === "connecting"
        ? t.connectingHint
        : state === "stopping"
          ? t.stoppingHint
          : state === "stopped"
            ? t.stoppedHint
            : t.nativeHint;
  const coreStarted = snapshot?.process_id !== undefined;
  return (
    <section className="connection-panel">
      <div className="panel-heading">
        <span className="eyebrow">{t.eyebrow}</span>
        <span className="local-chip">{t.local}</span>
      </div>
      <h1>{t.title}</h1>
      <p className="intro">{t.intro}</p>
      <div
        className={`connection-card ${state}`}
        role="status"
        aria-live="polite"
      >
        <div className="status-mark" aria-hidden="true">
          {state === "ready" ? "✓" : state === "fail" ? "!" : "↔"}
        </div>
        <div className="status-content">
          <h2>{connection.delivery === "connecting" ? t.reconnecting : t[state]}</h2>
          <p>{hint}</p>
          {connection.error && (
            <code className="error-code">{connection.error}</code>
          )}
        </div>
      </div>
      <div className="connection-steps">
        <div>
          <span className={`step-dot ${connected ? "complete" : ""}`} />
          <strong>{t.desktop}</strong>
          <small>{connected ? t.verified : t.waiting}</small>
        </div>
        <div>
          <span className={`step-dot ${connected && snapshot?.state === "ready" ? "complete" : ""}`} />
          <strong>{t.core}</strong>
          <small>{connected && snapshot?.state === "ready" ? t.verified : t.waiting}</small>
        </div>
        <div>
          <span className="step-dot" />
          <strong>{t.permissions}</strong>
          <small>{t.noGrant}</small>
        </div>
      </div>
      <div className="actions">
        {connection.delivery === "failed" && connection.error !== "DESKTOP_REQUIRED" && (
          <button className="primary" disabled={pending} onClick={reconnect}>
            {t.reconnect}
          </button>
        )}
        {connected && (state === "fail" || snapshot?.state === "native") &&
          !coreStarted &&
          connection.error !== "DESKTOP_REQUIRED" && (
            <button className="primary" disabled={pending} onClick={start}>
              {t.retry}
            </button>
          )}
        {connected && snapshot?.state === "ready" && (
          <button disabled={pending} onClick={stop}>
            {t.stop}
          </button>
        )}
        {connected && snapshot?.state === "fail" && coreStarted && (
          <button
            className="danger"
            disabled={pending}
            onClick={() => {
              if (window.confirm(t.forceWarning)) forceClose();
            }}
          >
            {t.force}
          </button>
        )}
      </div>
      <div className="privacy-note">
        <span aria-hidden="true">◇</span>
        <div>
          <strong>{t.privacy}</strong>
          <p>{t.privacyHint}</p>
        </div>
      </div>
    </section>
  );
}
