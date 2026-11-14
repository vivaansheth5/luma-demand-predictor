import { useMemo, useState } from "react";
import { Sheet } from "../charts/base";
import { StoreMap } from "../charts/StoreMap";
import { Icon, Pager, StatusTag } from "../components/parts";
import { go, rupees, type Model } from "../data";
import { inr, int, pct } from "../format";

const PAGE = 12;

export function Plan({ m }: { m: Model }) {
  const { d } = m;
  const { plan } = d;
  const s = plan.summary;
  const [store, setStore] = useState("All stores");
  const [only, setOnly] = useState<"all" | "review">("all");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [movePage, setMovePage] = useState(0);

  const orders = useMemo(() => {
    const q = query.trim().toLowerCase();
    return plan.orders.filter(
      (o) =>
        (store === "All stores" || o.store === store) &&
        (only === "all" || o.approval === "review") &&
        (!q || o.product.toLowerCase().includes(q) || o.sku_id.toLowerCase().includes(q)),
    );
  }, [plan.orders, store, only, query]);
  const moves = useMemo(() => plan.transfers.filter((t) => store === "All stores" || t.from === store || t.to === store), [plan.transfers, store]);

  if (!s) {
    return (
      <header className="view-head">
        <h1>No plan was committed</h1>
        <p>A guardrail check failed and the agent could not repair the draft. Open the trace to see which check failed, then run the agent again.</p>
      </header>
    );
  }
  const review = plan.orders.filter((o) => o.approval === "review");
  const used = s.supplier_orders.cost / d.meta.policy.budget;
  const pages = Math.max(1, Math.ceil(orders.length / PAGE));
  const current = Math.min(page, pages - 1);
  const movePages = Math.max(1, Math.ceil(moves.length / PAGE));
  const currentMove = Math.min(movePage, movePages - 1);
  const pick = (name: string) => {
    setStore(name);
    setPage(0);
    setMovePage(0);
  };

  return (
    <>
      <header className="view-head">
        <h1>Tonight's plan</h1>
        <p>
          Transfers go first because they land tomorrow morning. Supplier orders follow and take {d.meta.policy.leadTimeDays} days,
          so each order has to cover {d.meta.policy.protectionDays} days of demand. Nothing is committed until it passes
          every check below.
        </p>
      </header>

      <div className="block grid-main">
        <Sheet title="What the plan does" subtitle="Expected effect over the next 14 days, computed from the forecast before anything ships.">
          <dl className="kv" style={{ margin: "14px 0 6px" }}>
            <div>
              <dt>Supplier orders</dt>
              <dd>
                {inr(s.supplier_orders.cost)} <small>{int(s.supplier_orders.units)} units</small>
              </dd>
            </div>
            <div>
              <dt>Transfers between stores</dt>
              <dd>
                {int(s.transfers.units)} units <small>{s.transfers.lanes} trips</small>
              </dd>
            </div>
            <div>
              <dt>Sales protected</dt>
              <dd>{inr(s.expected_impact.revenue_protected)}</dd>
            </div>
            <div>
              <dt>Sizes likely to sell out</dt>
              <dd>
                {int(s.expected_impact.cells_above_50pct_risk[0])} to {int(s.expected_impact.cells_above_50pct_risk[1])}
              </dd>
            </div>
            <div>
              <dt>Unmet demand, 14 days</dt>
              <dd>
                {int(s.expected_impact.lost_units_14d[0])} to {int(s.expected_impact.lost_units_14d[1])} <small>units</small>
              </dd>
            </div>
            <div>
              <dt>Transfer cost and return</dt>
              <dd>
                {inr(s.transfers.cost)} <small>recovers {inr(s.transfers.expected_margin_recovered)} margin</small>
              </dd>
            </div>
          </dl>
          <div style={{ marginTop: 18 }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, color: "var(--secondary)", marginBottom: 6 }}>
              <span>Weekly supplier budget used</span>
              <span className="num">
                {inr(s.supplier_orders.cost)} of {inr(d.meta.policy.budget)}
              </span>
            </div>
            <div className="meter" role="img" aria-label={`${pct(used)} of the weekly budget used`}>
              <i style={{ width: `${Math.min(100, 100 * used)}%` }} />
            </div>
            {s.supplier_orders.budget_binding && (
              <p className="note" style={{ marginTop: 10 }}>
                Meeting every service target would cost {inr(s.supplier_orders.cost_without_budget)}. The agent kept the units with the
                highest expected margin per rupee and deferred {inr(s.supplier_orders.cost_without_budget - s.supplier_orders.cost)}.
              </p>
            )}
          </div>
        </Sheet>

        <Sheet title="Checks the plan passed" subtitle="A failed check blocks the commit, and the agent has to redraft.">
          <ul className="checks">
            {plan.guardrails.map((g) => (
              <li key={g.check}>
                <span style={{ color: g.status === "pass" ? "var(--good)" : g.status === "warn" ? "var(--warning)" : "var(--critical)", marginTop: 3 }}>
                  <Icon name={g.status === "pass" ? "check" : g.status === "warn" ? "warn" : "cross"} size={15} />
                </span>
                <div>
                  <b>{g.check}</b> <span>{g.status === "warn" ? "Warning. " : ""}{rupees(g.detail)}</span>
                </div>
              </li>
            ))}
          </ul>
        </Sheet>
      </div>

      <div className="block">
        <div className="block-head">
          <h2>Supplier orders</h2>
          <p>
            {int(plan.orders.length - review.length)} lines approved automatically, {int(review.length)} waiting for a person
          </p>
        </div>
        <div className="filters">
          <div className="chips" role="group" aria-label="Store">
            {["All stores", ...d.stores.map((st) => st.name)].map((name) => (
              <button key={name} className="chip" aria-pressed={store === name} onClick={() => pick(name)}>
                {name}
              </button>
            ))}
          </div>
        </div>
        <div className="filters">
          <div className="chips" role="group" aria-label="Approval">
            <button className="chip" aria-pressed={only === "all"} onClick={() => { setOnly("all"); setPage(0); }}>
              All lines
            </button>
            <button className="chip" aria-pressed={only === "review"} onClick={() => { setOnly("review"); setPage(0); }}>
              Needs approval
            </button>
          </div>
          <label className="field">
            <input type="search" placeholder="Search products" value={query} onChange={(e) => { setQuery(e.target.value); setPage(0); }} />
          </label>
        </div>
        <div className="sheet">
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Product and store</th>
                  <th className="r">On hand</th>
                  <th className="r">Expected, 14 days</th>
                  <th>Order by size</th>
                  <th className="r">Cost</th>
                  <th className="r">Stockout risk</th>
                  <th>Approval</th>
                </tr>
              </thead>
              <tbody>
                {orders.slice(current * PAGE, current * PAGE + PAGE).map((o) => (
                  <tr key={`${o.store}-${o.sku_id}`} className="clickable" onClick={() => go(`/forecast/${d.stores[m.storeIndex(o.store)].id}/${o.sku_id}`)}>
                    <td>
                      <div className="cell-main">{o.product}</div>
                      <div className="cell-sub">
                        {o.store}, class {o.class}. {o.reason}
                      </div>
                    </td>
                    <td className="r">{o.on_hand}</td>
                    <td className="r">{o.expected_14d.toFixed(1)}</td>
                    <td className="num" style={{ whiteSpace: "nowrap" }}>
                      {Object.entries(o.sizes).map(([z, q]) => `${z} ${q}`).join(", ")}
                    </td>
                    <td className="r">{inr(o.cost)}</td>
                    <td className="r" style={{ whiteSpace: "nowrap" }}>
                      {pct(o.risk_before)} to {pct(o.risk_after)}
                    </td>
                    <td>
                      {o.approval === "auto" ? (
                        <StatusTag status="pass">Approved</StatusTag>
                      ) : (
                        <span title={o.review_reason ?? ""}>
                          <StatusTag status="warn">Needs approval</StatusTag>
                          <div className="cell-sub" style={{ marginTop: 3, maxWidth: 190 }}>{rupees(o.review_reason ?? "")}</div>
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
                {orders.length === 0 && (
                  <tr>
                    <td colSpan={7} className="note">
                      No order lines match these filters.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <Pager page={current} pages={pages} onPage={setPage} total={orders.length} noun="order lines" />
        </div>
      </div>

      <div className="block">
        <div className="block-head">
          <h2>Transfers</h2>
          <p>Chosen by a mixed-integer programme: {s.transfers.solver === "optimal" ? "solved to optimality" : s.transfers.solver} in {s.transfers.solve_seconds}s</p>
        </div>
        <div className="grid-2">
          <Sheet
            title={`${int(moves.length)} moves${store === "All stores" ? "" : ` in or out of ${store}`}`}
            subtitle="A unit moves only when it is more likely to sell where it is going than where it is, by enough to pay for the trip."
          >
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Product</th>
                    <th>Size</th>
                    <th>From</th>
                    <th>To</th>
                    <th className="r">Units</th>
                    <th className="r">Donor keeps</th>
                    <th className="r">Receiver risk</th>
                  </tr>
                </thead>
                <tbody>
                  {moves.slice(currentMove * PAGE, currentMove * PAGE + PAGE).map((t, i) => (
                    <tr key={i}>
                      <td className="cell-main">{t.product}</td>
                      <td>{t.size}</td>
                      <td>{t.from}</td>
                      <td>{t.to}</td>
                      <td className="r">{t.units}</td>
                      <td className="r">{t.donor_keeps}</td>
                      <td className="r">{pct(t.receiver_risk_before)}</td>
                    </tr>
                  ))}
                  {moves.length === 0 && (
                    <tr>
                      <td colSpan={7} className="note">
                        No transfers touch this store tonight.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <Pager page={currentMove} pages={movePages} onPage={setMovePage} total={moves.length} noun="moves" />
          </Sheet>
          <Sheet title="Lanes in use" subtitle={`${plan.lanes.length} rider trips. A trip carries at most 45 units.`}>
            <StoreMap stores={d.stores} lanes={plan.lanes} height={440} />
          </Sheet>
        </div>
      </div>

      {d.overstock.length > 0 && (
        <div className="block">
          <div className="block-head">
            <h2>Stock that is not moving</h2>
            <p>More than 12 weeks of cover at the forecast rate. No orders are placed for these.</p>
          </div>
          <div className="sheet">
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Product and store</th>
                    <th className="r">On hand</th>
                    <th className="r">Weeks of cover</th>
                    <th className="r">Stock value</th>
                    <th>Suggestion</th>
                  </tr>
                </thead>
                <tbody>
                  {d.overstock.slice(0, 10).map((o) => (
                    <tr key={`${o.s}-${o.k}`} className="clickable" onClick={() => go(`/forecast/${d.stores[o.s].id}/${o.sku_id}`)}>
                      <td>
                        <div className="cell-main">{o.product}</div>
                        <div className="cell-sub">{o.store}</div>
                      </td>
                      <td className="r">{o.on_hand}</td>
                      <td className="r">{o.weeks_of_cover.toFixed(0)}</td>
                      <td className="r">{inr(o.stock_value)}</td>
                      <td>{o.suggestion}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
