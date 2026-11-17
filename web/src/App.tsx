import { useCallback, useEffect, useMemo, useState } from "react";
import { buildModel, go } from "./data";
import { count, shortDate } from "./format";
import { chooseSource, loadSample, loadUpload, savedSource, saveUpload, setPending, takePending, type Source } from "./store";
import type { Dashboard } from "./types";
import { Brief } from "./views/Brief";
import { Evidence } from "./views/Evidence";
import { Forecast } from "./views/Forecast";
import { GITHUB, Home } from "./views/Home";
import { Plan } from "./views/Plan";
import { Signals } from "./views/Signals";
import { Trace } from "./views/Trace";
import { Upload } from "./views/Upload";
import { emptyConversation, Workspace, type Conversation } from "./views/Workspace";

const REPORT = [
  { path: "brief", label: "Overview" },
  { path: "forecast", label: "Forecast" },
  { path: "plan", label: "Plan" },
  { path: "signals", label: "Trends" },
  { path: "evidence", label: "Accuracy" },
  { path: "trace", label: "Agent steps" },
];

/** Parse #view, #forecast-2 or #forecast-2-17 into [view, store position, product position]. */
function useRoute(): string[] {
  const read = () => decodeURIComponent(window.location.hash.replace(/^#\/?/, "")).split(/[-/]/).filter(Boolean).slice(0, 3);
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function App() {
  const route = useRoute();
  const view = route[0] ?? "home";
  const inReport = REPORT.some((r) => r.path === view);
  const needsData = view === "chat" || inReport;

  const [source, setSource] = useState<Source | null>(savedSource);
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Record<Source, Conversation>>({
    sample: emptyConversation(),
    upload: emptyConversation(),
  });
  const [pending, setPendingQuestion] = useState<string | null>(null);

  // Load whichever data the visitor chose; the sample store when they have not chosen yet.
  useEffect(() => {
    if (!needsData) return;
    const want: Source = source === "upload" && loadUpload() ? "upload" : "sample";
    if (want === "upload") {
      setData(loadUpload());
      return;
    }
    let live = true;
    loadSample().then(
      (d) => live && setData(d),
      (e: Error) => live && setError(e.message),
    );
    return () => {
      live = false;
    };
  }, [needsData, source]);

  useEffect(() => {
    if (view === "chat") setPendingQuestion(takePending());
  }, [view]);

  const model = useMemo(() => (data ? buildModel(data) : null), [data]);
  const active: Source = source === "upload" && loadUpload() ? "upload" : "sample";

  const openSample = useCallback((question?: string) => {
    chooseSource("sample");
    setSource("sample");
    if (question) setPending(question);
    go("chat");
  }, []);

  const finishUpload = useCallback((d: Dashboard) => {
    saveUpload(d);
    setConversations((c) => ({ ...c, upload: emptyConversation() }));
    setData(d);
    setSource("upload");
    go("chat");
  }, []);

  const setConversation = useCallback(
    (fn: (c: Conversation) => Conversation) => setConversations((all) => ({ ...all, [active]: fn(all[active]) })),
    [active],
  );

  const topbar = (
    <header className="topbar">
      <div className="topbar-inner">
        <a className="brand" href="#home" aria-label="Luma, home">
          <span className="brand-mark">luma</span>
        </a>
        {needsData && model && (
          <>
            <span className={`dataset ${active}`}>{active === "sample" ? "Sample store" : model.d.meta.brand}</span>
            <nav className="nav tabs" aria-label="Views">
              <a href="#chat" aria-current={view === "chat" ? "page" : undefined}>
                Chat
              </a>
              <a href="#brief" aria-current={inReport ? "page" : undefined}>
                Report
              </a>
            </nav>
          </>
        )}
        <span className="topbar-end">
          {needsData && active === "upload" && (
            <button className="linklike quiet" onClick={() => openSample()}>
              Sample store
            </button>
          )}
          {view !== "upload" && (
            <button className="btn small" onClick={() => go("upload")}>
              {active === "upload" && needsData ? "New upload" : "Upload your sales"}
            </button>
          )}
          {!needsData && (
            <a className="quiet-link" href={GITHUB} target="_blank" rel="noreferrer">
              GitHub
            </a>
          )}
        </span>
      </div>
    </header>
  );

  if (view === "upload") {
    return (
      <>
        {topbar}
        <main className="page narrow">
          <Upload onDone={finishUpload} onSample={() => openSample()} />
        </main>
      </>
    );
  }

  if (!needsData) {
    return (
      <>
        {topbar}
        <main className="page home-page">
          <Home onSample={openSample} onUpload={() => go("upload")} />
        </main>
      </>
    );
  }

  if (error) {
    return (
      <>
        {topbar}
        <div className="failure">
          <div>
            <h1>The sample store didn't load</h1>
            <p>Refresh the page to try again. ({error})</p>
          </div>
        </div>
      </>
    );
  }
  if (!model) {
    return (
      <>
        {topbar}
        <div className="loading">Loading</div>
      </>
    );
  }

  if (view === "chat") {
    return (
      <>
        {topbar}
        <main className="page chat-page">
          <Workspace
            key={active}
            m={model}
            conversation={conversations[active]}
            setConversation={setConversation}
            pending={pending}
          />
        </main>
      </>
    );
  }

  return (
    <>
      {topbar}
      <div className="subnav">
        <nav className="nav" aria-label="Report sections">
          {REPORT.map((n) => (
            <a key={n.path} href={`#${n.path}`} aria-current={view === n.path ? "page" : undefined}>
              {n.label}
            </a>
          ))}
        </nav>
        <span className="asof">
          As of {shortDate(model.d.meta.asOf)} {model.d.meta.asOf.slice(0, 4)}
        </span>
      </div>
      <main className="page">
        {view === "brief" && <Brief m={model} />}
        {view === "forecast" && <Forecast m={model} store={route[1]} product={route[2]} />}
        {view === "plan" && <Plan m={model} />}
        {view === "signals" && <Signals m={model} />}
        {view === "evidence" && <Evidence m={model} />}
        {view === "trace" && <Trace m={model} />}
        <footer className="footer">
          <span>
            {count(model.d.meta.counts.stores, "store")}, {count(model.d.meta.counts.skus, "product")},{" "}
            {model.d.meta.counts.days} days of {model.has.truth ? "simulated sales" : "sales"}
          </span>
          <span>Forecasts are ranges. Decisions are checked before they are committed.</span>
        </footer>
      </main>
    </>
  );
}
