import { useEffect, useMemo, useState } from "react";
import { buildModel, loadDashboard } from "./data";
import { count, shortDate } from "./format";
import type { Dashboard } from "./types";
import { Brief } from "./views/Brief";
import { Evidence } from "./views/Evidence";
import { Forecast } from "./views/Forecast";
import { Plan } from "./views/Plan";
import { Signals } from "./views/Signals";
import { Trace } from "./views/Trace";

const NAV = [
  { path: "brief", label: "Brief" },
  { path: "forecast", label: "Forecast" },
  { path: "plan", label: "Plan" },
  { path: "signals", label: "Signals" },
  { path: "evidence", label: "Evidence" },
  { path: "trace", label: "Trace" },
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
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const route = useRoute();
  const view = NAV.some((n) => n.path === route[0]) ? route[0] : "brief";

  useEffect(() => {
    loadDashboard().then(setData, (e: Error) => setError(e.message));
  }, []);
  const model = useMemo(() => (data ? buildModel(data) : null), [data]);

  if (error) {
    return (
      <div className="failure">
        <div>
          <h1>The dashboard has no data yet</h1>
          <p>
            Run <code>luma run</code>, then copy <code>artifacts/dashboard.json</code> to{" "}
            <code>web/public/data/</code>. ({error})
          </p>
        </div>
      </div>
    );
  }
  if (!model) return <div className="loading">Loading the latest run</div>;

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <a className="brand" href="#brief" aria-label={`Luma, ${model.d.meta.brand}, home`}>
            <span className="brand-mark">luma</span>
            <span className="brand-product">{model.d.meta.brand}</span>
          </a>
          <nav className="nav" aria-label="Sections">
            {NAV.map((n) => (
              <a key={n.path} href={`#${n.path}`} aria-current={view === n.path ? "page" : undefined}>
                {n.label}
              </a>
            ))}
          </nav>
          <span className="asof">
            As of {shortDate(model.d.meta.asOf)} {model.d.meta.asOf.slice(0, 4)}
            {model.has.truth ? ", simulated data" : ""}
          </span>
        </div>
      </header>
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
            {model.d.meta.counts.days} days of {model.has.truth ? `simulated history, seed ${model.d.meta.seed}` : "sales history"}
          </span>
          <span>Forecasts are ranges. Decisions are checked before they are committed.</span>
        </footer>
      </main>
    </>
  );
}
