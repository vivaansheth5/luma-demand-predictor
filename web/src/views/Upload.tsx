import { useEffect, useRef, useState } from "react";
import { int } from "../format";
import type { Dashboard } from "../types";
import { analyse, downloadTemplate, missing, pick, ROLE_LABELS, UploadError, type Picked, type Role } from "../upload";

const CURRENCIES = ["USD", "EUR", "GBP", "INR", "AUD", "CAD", "SGD", "AED", "JPY"];
const STAGES = [
  "Reading your files",
  "Correcting for sizes that sold out",
  "Learning how each product sells",
  "Testing past forecasts against what happened",
  "Checking every style and colour for trends",
  "Writing the summary",
];

function guessCurrency(): string {
  try {
    const region = new Intl.Locale(navigator.language).maximize().region;
    const map: Record<string, string> = { IN: "INR", GB: "GBP", AU: "AUD", CA: "CAD", SG: "SGD", AE: "AED", JP: "JPY" };
    const euro = ["DE", "FR", "ES", "IT", "NL", "IE", "PT", "BE", "AT", "FI", "GR"];
    return region ? (map[region] ?? (euro.includes(region) ? "EUR" : "USD")) : "USD";
  } catch {
    return "USD";
  }
}

export function Upload({ onDone, onSample }: { onDone: (d: Dashboard) => void; onSample: () => void }) {
  const [files, setFiles] = useState<Picked[]>([]);
  const [brand, setBrand] = useState("");
  const [currency, setCurrency] = useState(guessCurrency);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [busy]);

  const add = async (list: FileList | null) => {
    if (!list) return;
    setError(null);
    const picked = await Promise.all(
      [...list].filter((f) => /\.(csv|txt)$/i.test(f.name) || f.type === "text/csv").map(pick),
    );
    if (picked.length < list.length) setError("Only CSV files can be read. Export Excel sheets as CSV first.");
    setFiles((prev) => [...prev.filter((p) => !picked.some((q) => q.file.name === p.file.name)), ...picked]);
  };

  const setRole = (name: string, role: Role) => setFiles((prev) => prev.map((f) => (f.file.name === name ? { ...f, role } : f)));
  const remove = (name: string) => setFiles((prev) => prev.filter((f) => f.file.name !== name));
  const needs = missing(files);
  const isShopify = files.some((f) => f.role === "shopify_orders");

  const run = async () => {
    setBusy(true);
    setError(null);
    setElapsed(0);
    try {
      const result = await analyse(files, { brand: brand.trim() || undefined, currency: isShopify ? undefined : currency });
      onDone(result);
    } catch (err) {
      setError(err instanceof UploadError ? err.message : "Something went wrong while analysing the data. Try again.");
      setBusy(false);
    }
  };

  if (busy) {
    return (
      <div className="upload-page">
        <div className="working" role="status" aria-live="polite">
          <h1>Luma is reading your sales</h1>
          <p className="working-lede">This usually takes one to two minutes. Keep this tab open.</p>
          <div className="working-bar" aria-hidden="true">
            <span />
          </div>
          <p className="working-time">{elapsed} seconds</p>
          <h2>What it's doing</h2>
          <ul className="stage-list">
            {STAGES.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </div>
      </div>
    );
  }

  return (
    <div className="upload-page">
      <div className="upload-main">
        <h1>Upload your sales</h1>
        <p className="upload-lede">
          Add a sales file and a products file, or Shopify's orders export. Luma reads them in memory and keeps nothing.
        </p>

        <div
          className={`drop${over ? " over" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            void add(e.dataTransfer.files);
          }}
        >
          <p>Drop CSV files here</p>
          <button className="btn ghost" onClick={() => input.current?.click()}>
            Choose files
          </button>
          <input
            ref={input}
            type="file"
            accept=".csv,text/csv"
            multiple
            hidden
            onChange={(e) => {
              void add(e.target.files);
              e.target.value = "";
            }}
          />
        </div>

        {files.length > 0 && (
          <ul className="picked" aria-label="Files added">
            {files.map((f) => (
              <li key={f.file.name}>
                <div className="picked-name">
                  <span>{f.file.name}</span>
                  <small>
                    {int(f.rows)} rows, {f.columns.length} columns
                  </small>
                </div>
                <label className="visually-hidden" htmlFor={`role-${f.file.name}`}>
                  What this file is
                </label>
                <select id={`role-${f.file.name}`} value={f.role} onChange={(e) => setRole(f.file.name, e.target.value as Role)}>
                  {(Object.keys(ROLE_LABELS) as Role[]).map((r) => (
                    <option key={r} value={r}>
                      {ROLE_LABELS[r]}
                    </option>
                  ))}
                </select>
                <button className="icon-btn" aria-label={`Remove ${f.file.name}`} onClick={() => remove(f.file.name)}>
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}

        <div className="upload-fields">
          <label>
            <span>Store name</span>
            <input value={brand} onChange={(e) => setBrand(e.target.value)} placeholder="Your brand" maxLength={60} />
          </label>
          {!isShopify && (
            <label>
              <span>Currency</span>
              <select value={currency} onChange={(e) => setCurrency(e.target.value)}>
                {CURRENCIES.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </label>
          )}
        </div>

        {error && (
          <p className="upload-error" role="alert">
            {error}
          </p>
        )}

        <div className="upload-go">
          <button className="btn big" disabled={files.length === 0 || needs.length > 0} onClick={() => void run()}>
            Analyse my sales
          </button>
          <span className="upload-hint">
            {files.length === 0
              ? "Add your files to start."
              : needs.length
                ? `Still needed: ${needs.join(" and ")}.`
                : "Ready."}
          </span>
        </div>
      </div>

      <aside className="upload-help">
        <h2>Which files?</h2>
        <h3>From Shopify</h3>
        <p>
          In your Shopify admin, open <b>Orders</b>, choose <b>Export</b>, select all orders and download the CSV. Do the same
          from <b>Products</b> for prices, sizes and stock. Upload both as they are.
        </p>
        <h3>From anywhere else</h3>
        <p>
          A <b>sales</b> file with a date, product ID, size and units sold on each row, and a <b>products</b> file with
          each product's ID, name and price. Extra columns such as category, style or colour make the trends richer.
        </p>
        <p className="help-templates">
          Templates:{" "}
          <button className="linklike" onClick={() => downloadTemplate("sales.csv")}>
            sales
          </button>
          ,{" "}
          <button className="linklike" onClick={() => downloadTemplate("products.csv")}>
            products
          </button>
          ,{" "}
          <button className="linklike" onClick={() => downloadTemplate("stock.csv")}>
            stock
          </button>
        </p>
        <h3>Add stock to get a restock plan</h3>
        <p>A file of units on hand by product and size lets Luma tell you what will sell out and how much to reorder.</p>
        <p className="help-sample">
          No data to hand?{" "}
          <button className="linklike" onClick={onSample}>
            Try the sample store
          </button>
          .
        </p>
      </aside>
    </div>
  );
}
