import { useEffect, useState } from "react";
import { useConnection } from "./connection/useConnection";
import { ConnectionPanel } from "./components/ConnectionPanel";
import { SessionPanel } from "./components/SessionPanel";
import { copy, type Language } from "./i18n";
import "./styles/theme.css";
import "./styles/layout.css";

export function App() {
  const [language, setLanguage] = useState<Language>("en");
  const model = useConnection();
  const t = copy[language];
  const snapshot = model.connection.snapshot;
  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);
  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            ✣
          </span>
          <strong>Synapse</strong>
          <span>{t.tagline}</span>
        </div>
        <nav aria-label={t.workspace}>
          <span className="active-tab">{t.connection}</span>
        </nav>
        <div className="language-switch" aria-label="Language">
          {(["en", "ru"] as const).map((value) => (
            <button
              key={value}
              aria-pressed={language === value}
              onClick={() => setLanguage(value)}
            >
              {value.toUpperCase()}
            </button>
          ))}
        </div>
      </header>
      <div className="workspace-grid">
        <aside className="workspace-sidebar">
          <h2>{t.project}</h2>
          <div className="empty-project">
            <span aria-hidden="true">▱</span>
            <strong>{t.noProject}</strong>
            <p>{t.projectHint}</p>
          </div>
          <div className="sidebar-bottom">
            <span aria-hidden="true">◇</span>
            {t.private}
          </div>
        </aside>
        <main>
          <ConnectionPanel language={language} {...model} />
          <section className="agents-panel">
            <div className="section-label">
              <span>{t.agents}</span>
              <span className="count">0</span>
            </div>
            <div className="agent-empty">
              <span className="agent-glyph" aria-hidden="true">
                ⌘
              </span>
              <div>
                <strong>{t.noAgents}</strong>
                <p>{t.agentsHint}</p>
              </div>
            </div>
          </section>
        </main>
        <SessionPanel language={language} snapshot={snapshot} />
        <section className="diagnostics-panel">
          <h2>{t.diagnostics}</h2>
          {snapshot?.diagnostics.length ? (
            <ul>
              {snapshot.diagnostics.map((code, index) => (
                <li key={`${index}:${code}`}>
                  <code>{code}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p>{t.noDiagnostics}</p>
          )}
        </section>
      </div>
      <footer>
        <span>
          <i
            className={
              snapshot?.state === "ready" && !model.connection.error
                ? "online"
                : ""
            }
          />
          {t.bottom}
        </span>
        <span>{t.footer}</span>
      </footer>
    </div>
  );
}
