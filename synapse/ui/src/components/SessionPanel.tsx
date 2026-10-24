import type { BootstrapSnapshot } from "../contracts/generated/BootstrapSnapshot";
import { decodeEnvelope } from "../contracts/decode";
import { copy, type Language } from "../i18n";

export function SessionPanel({
  snapshot,
  language,
}: {
  snapshot: BootstrapSnapshot | null;
  language: Language;
}) {
  const t = copy[language];
  return (
    <aside className="session-panel">
      <h2>{t.session}</h2>
      <dl>
        <dt>{t.version}</dt>
        <dd>{snapshot?.core_version ?? "—"}</dd>
        <dt>{t.process}</dt>
        <dd>{snapshot?.process_id ?? "—"}</dd>
        <dt>{t.profile}</dt>
        <dd>{snapshot?.profile ?? "—"}</dd>
      </dl>
      <div className="session-note">
        <span className="network-mark" aria-hidden="true">
          ⌁
        </span>
        <p>{t.network}</p>
      </div>
      {snapshot?.protocol_message && (
        <details>
          <summary>{t.reply}</summary>
          <pre>
            {JSON.stringify(decodeEnvelope(snapshot.protocol_message), null, 2)}
          </pre>
        </details>
      )}
    </aside>
  );
}
