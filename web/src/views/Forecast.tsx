import { useMemo, useState } from "react";
import { Key, Sheet } from "../charts/base";
import { DriverBars } from "../charts/Bars";
import { DemandChart } from "../charts/DemandChart";
import { RiskKey, SizeRun, TrendTag } from "../components/parts";
import { go, type Model } from "../data";
import { inr, int, pct, shortDate } from "../format";
import { color } from "../theme";
import type { Pair } from "../types";

type Sort = "risk" | "forecast" | "name";

const sum = (a: number[]) => a.reduce((x, y) => x + y, 0);

export function Forecast({ m, storeId, skuId }: { m: Model; storeId?: string; skuId?: string }) {
  const { d } = m;
  const first = d.risks[0];
  const s = storeId ? Math.max(m.storeIndex(storeId), 0) : (first?.s ?? 0);
  const [category, setCategory] = useState("all");
  const [sort, setSort] = useState<Sort>("risk");
  const [query, setQuery] = useState("");

  const inStore = useMemo(() => d.pairs.filter((p) => p.s === s), [d, s]);
  const list = useMemo(() => {
    const q = query.trim().toLowerCase();
    const rows = inStore.filter((p) => {
      const sku = d.skus[p.k];
      return (category === "all" || sku.category === category) && (!q || sku.name.toLowerCase().includes(q) || sku.id.toLowerCase().includes(q));
    });
    const lost = (p: Pair) => sum(p.riskWeek.map((r, i) => r * p.exp14[i])) * d.skus[p.k].price;
    if (sort === "risk") rows.sort((a, b) => lost(b) - lost(a));
    else if (sort === "forecast") rows.sort((a, b) => sum(b.mean) - sum(a.mean));
    else rows.sort((a, b) => d.skus[a.k].name.localeCompare(d.skus[b.k].name));
    return rows;
  }, [inStore, category, sort, query, d]);

  const wanted = skuId ? m.skuIndex(skuId) : -1;
  const pair = (wanted >= 0 ? m.pair(s, wanted) : undefined) ?? (storeId ? list[0] : first ? m.pair(first.s, first.k) : list[0]) ?? inStore[0];
  if (!pair) return <p className="note">No products are live in this store.</p>;
  const sku = d.skus[pair.k];
  const store = d.stores[pair.s];
  const histWeeks = pair.demand.length;
  const next28 = sum(pair.mean);
  const orderUnits = sum(pair.order);
  const select = (p: Pair) => go(`/forecast/${d.stores[p.s].id}/${d.skus[p.k].id}`);

  return (
    <>
      <header className="view-head">
        <h1>Every product, every store, every size</h1>
        <p>
          The model forecasts each product in each store as a range, then splits it into sizes. Pick a product to see
          its forecast, what is driving it, and whether the shelf can meet it.
        </p>
      </header>

      <div className="block explorer">
        <aside className="sheet picker" style={{ padding: "16px 0 6px" }}>
          <div className="picker-controls">
            <select aria-label="Store" value={store.id} onChange={(e) => go(`/forecast/${e.target.value}`)}>
              {d.stores.map((st) => (
                <option key={st.id} value={st.id}>
                  {st.name}
                </option>
              ))}
            </select>
            <select aria-label="Category" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="all">All categories</option>
              {d.meta.categories.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.label}
                </option>
              ))}
            </select>
            <select aria-label="Sort" value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
              <option value="risk">Sort by sales at risk</option>
              <option value="forecast">Sort by forecast</option>
              <option value="name">Sort by name</option>
            </select>
            <input type="search" placeholder="Search products" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
          <ul className="picker-list">
            {list.map((p) => (
              <li key={p.k}>
                <button aria-current={p.k === pair.k} onClick={() => select(p)}>
                  <span style={{ minWidth: 0 }}>
                    <span className="picker-name" style={{ display: "block" }}>
                      {d.skus[p.k].name}
                    </span>
                    <span className="picker-sub">{int(sum(p.mean))} units forecast</span>
                  </span>
                  <SizeRun sizes={d.meta.sizes} stock={p.stock} risk={p.riskWeek} compact />
                </button>
              </li>
            ))}
            {list.length === 0 && <li className="note" style={{ padding: 12 }}>No product matches. Clear the search or pick another category.</li>}
          </ul>
        </aside>

        <div className="stack">
          <div>
            <h2 className="pair-title">{sku.name}</h2>
            <div className="pair-meta">
              <span>{store.name}</span>
              <span className="tag">Class {pair.abc}</span>
              <span className="tag">{inr(sku.price)}</span>
              <span className="tag">{pair.age < 28 ? `${pair.age} days on sale` : `${Math.floor(pair.age / 7)} weeks on sale`}</span>
              <span>Style: {sku.style}</span>
              <TrendTag status={sku.trend} />
            </div>
          </div>

          <Sheet
            title={`${int(next28)} units expected over the next 28 days`}
            subtitle={`Recent run-rate is ${pair.level.toFixed(2)} units a day. Bars are weekly sales; the line adds demand lost to stockouts.`}
            legend={
              <>
                <Key kind="rect" color={color.quiet}>
                  Sales
                </Key>
                <Key kind="line" color={color.ink}>
                  Demand
                </Key>
                <Key kind="line" color={color.accent}>
                  Forecast
                </Key>
                <Key kind="rect" color="#E9CDD3">
                  Middle 50%
                </Key>
                <Key kind="rect" color="#F4E4E8">
                  80% interval
                </Key>
              </>
            }
            table={{
              columns: ["Week ending", "Sales", "Demand", "Forecast", "P10", "P50", "P90"],
              rows: m.weeks.map((w, i) =>
                i < histWeeks
                  ? [w, pair.sales[i], pair.demand[i].toFixed(1), "", "", "", ""]
                  : [w, "", "", pair.mean[i - histWeeks].toFixed(1), pair.p10[i - histWeeks].toFixed(1), pair.p50[i - histWeeks].toFixed(1), pair.p90[i - histWeeks].toFixed(1)],
              ),
            }}
          >
            <DemandChart
              weeks={m.weeks}
              demand={pair.demand}
              sales={pair.sales}
              salesAsBars
              startAt={Math.max(0, histWeeks - Math.ceil(pair.age / 7))}
              forecast={{ mean: pair.mean, p10: pair.p10, p90: pair.p90, p25: pair.p25, p75: pair.p75 }}
              height={300}
            />
          </Sheet>

          <div className="grid-2">
            <Sheet
              title="Stock by size"
              subtitle={`What is on the shelf against expected demand to ${shortDate(m.weeks[histWeeks + 1])}, the 14 days one order must cover.`}
              foot={
                orderUnits > 0 ? (
                  <>
                    The agent orders {orderUnits} units. Highest stockout risk falls from {pct(Math.max(...pair.risk14))} to{" "}
                    {pct(Math.max(...pair.riskAfter))}.
                  </>
                ) : (
                  <>No order this week{pair.cover !== null && pair.cover > 60 ? `: the shelf holds about ${Math.round(pair.cover / 7)} weeks of cover` : ""}.</>
                )
              }
            >
              <div style={{ margin: "6px 0 14px", display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
                <SizeRun sizes={d.meta.sizes} stock={pair.stock} risk={pair.risk14} order={pair.order} />
                <RiskKey />
              </div>
              <table className="data">
                <thead>
                  <tr>
                    <th>Size</th>
                    <th className="r">Share</th>
                    <th className="r">On hand</th>
                    <th className="r">Expected</th>
                    <th className="r">Stockout risk</th>
                    <th className="r">Order</th>
                  </tr>
                </thead>
                <tbody>
                  {d.meta.sizes.map((z, i) => (
                    <tr key={z}>
                      <td className="cell-main">{z}</td>
                      <td className="r">{pct(pair.share[i])}</td>
                      <td className="r">
                        {pair.stock[i]}
                        {pair.inbound[i] > 0 ? ` + ${pair.inbound[i]}` : ""}
                      </td>
                      <td className="r">{pair.exp14[i].toFixed(1)}</td>
                      <td className="r">{pct(pair.risk14[i])}</td>
                      <td className="r">{pair.order[i] > 0 ? `+${pair.order[i]}` : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Sheet>

            <Sheet
              title="What moves this forecast"
              subtitle="Each bar is one family of signals and how far it pushes the 28-day forecast up or down, from exact Shapley values of the model."
              legend={
                <>
                  <Key kind="rect" color={color.accent}>
                    Raises the forecast
                  </Key>
                  <Key kind="rect" color={color.counter}>
                    Lowers it
                  </Key>
                </>
              }
            >
              <DriverBars names={d.meta.model.driverNames} factors={pair.drivers} />
            </Sheet>
          </div>
        </div>
      </div>
    </>
  );
}
