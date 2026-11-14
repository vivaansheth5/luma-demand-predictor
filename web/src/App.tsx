import { useEffect, useMemo, useState } from "react";
import { buildModel, loadDashboard } from "./data";
import { shortDate } from "./format";
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

/** Parse #view, #forecast-STORE or #forecast-STORE-PRODUCT-ID into [view, store, product]. */
function useRoute(): string[] {
  const read = () => {
    const [view, store, ...product] = decodeURIComponent(window.location.hash.replace(/^#\/?/, "")).split(/[-/]/).filter(Boolean);
    return [view, store, product.join("-")].filter(Boolean);
  };
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
            Run <code>demand-agent run</code>, then copy <code>artifacts/dashboard.json</code> to{" "}
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
          <a className="brand" href="#brief" aria-label="vyln demand agent, home">
            <span className="brand-mark">vyln</span>
            <span className="brand-product">demand agent</span>
          </a>
          <nav className="nav" aria-label="Sections">
            {NAV.map((n) => (
              <a key={n.path} href={`#${n.path}`} aria-current={view === n.path ? "page" : undefined}>
                {n.label}
              </a>
            ))}
          </nav>
          <span className="asof">
            As of {shortDate(model.d.meta.asOf)} {model.d.meta.asOf.slice(0, 4)}, simulated data
          </span>
        </div>
      </header>
      <main className="page">
        {view === "brief" && <Brief m={model} />}
        {view === "forecast" && <Forecast m={model} storeId={route[1]} skuId={route[2]} />}
        {view === "plan" && <Plan m={model} />}
        {view === "signals" && <Signals m={model} />}
        {view === "evidence" && <Evidence m={model} />}
        {view === "trace" && <Trace m={model} />}
        <footer className="footer">
          <span>
            {model.d.meta.counts.stores} stores, {model.d.meta.counts.skus} products, {model.d.meta.counts.days} days of simulated history, seed {model.d.meta.seed}
          </span>
          <span>Forecasts are ranges. Decisions are checked before they are committed.</span>
        </footer>
      </main>
    </>
  );
}
