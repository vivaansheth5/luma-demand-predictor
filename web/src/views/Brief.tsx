import { Key, Sheet } from "../charts/base";
import { DemandChart } from "../charts/DemandChart";
import { StoreMap } from "../charts/StoreMap";
import { Spark } from "../charts/Signals";
import { Icon, RiskKey, SizeRun } from "../components/parts";
import { go, rupees, type Model } from "../data";
import { inr, int, longDate, pct, shortDate, signedPct } from "../format";
import { color } from "../theme";
import type { Finding } from "../types";

const FINDING_ICON: Record<Finding["kind"], string> = {
  demand: "up",
  risk: "warn",
  trend: "spark",
  anomaly: "eye",
  plan: "check",
  watch: "flat",
};

export function Brief({ m }: { m: Model }) {
  const { d } = m;
  const { agent, plan, network, evaluation } = d;
  const summary = plan.summary;
  const policy = evaluation.policy?.headline;
  const review = plan.orders.filter((o) => o.approval === "review").length;
  const riskTotal = d.riskWeek.lostRevenue;
  const riskCells = d.riskWeek.cells;
  const shownLanes = Math.min(10, plan.lanes.length);
  const demand28 = d.stores.reduce((a, s) => a + s.demand28, 0);
  const forecast28 = d.stores.reduce((a, s) => a + s.forecast28, 0);
  const revenue28 = d.stores.reduce((a, s) => a + s.revenue28, 0);
  const histWeeks = network.demand.length;

  return (
    <>
      <section className="hero">
        <div>
          <p className="hero-date">{longDate(d.meta.asOf)}, this week's decision</p>
          <h1>{rupees(agent.brief.headline)}</h1>
          <div className="hero-actions">
            <a className="btn" href="#plan">
              Review the plan
            </a>
            <a className="btn ghost" href="#trace">
              See how it was decided
            </a>
          </div>
          <p className="hero-meta">
            The agent took {agent.steps.length} steps.{" "}
            {plan.orders.length > 0 && (
              <>
                {int(plan.orders.length - review)} order lines are approved automatically and {int(review)} wait for a
                person.
              </>
            )}{" "}
            All figures come from a simulated Bangalore network, so every decision can be scored against true demand.
          </p>
        </div>
        <Sheet
          title="The network tonight"
          subtitle={`Circle size is forecast demand for the next 28 days. Darker circles have more sizes about to sell out. Arrows are the ${shownLanes === plan.lanes.length ? "" : `${shownLanes} largest of `}tonight's ${plan.lanes.length} transfer lanes.`}
        >
          <StoreMap stores={d.stores} lanes={plan.lanes} maxLanes={10} height={400} />
        </Sheet>
      </section>

      <div className="figures">
        <div className="figure">
          <div className="figure-label">Forecast, next 28 days</div>
          <div className="figure-value">{int(forecast28)} units</div>
          <div className="figure-note">
            {signedPct(forecast28 / demand28 - 1)} against the last 28 days. About {inr(revenue28)} of revenue.
          </div>
        </div>
        <div className="figure">
          <div className="figure-label">Sales at risk this week</div>
          <div className="figure-value">{inr(riskTotal)}</div>
          <div className="figure-note">
            {int(riskCells)} sizes are more likely than not to sell out before a supplier order can land.
          </div>
        </div>
        <div className="figure">
          <div className="figure-label">Supplier orders</div>
          <div className="figure-value">{summary ? inr(summary.supplier_orders.cost) : "None"}</div>
          <div className="figure-note">
            {summary && (
              <>
                {int(summary.supplier_orders.units)} units on {int(summary.supplier_orders.lines)} lines.{" "}
                {summary.supplier_orders.budget_binding
                  ? `The ${inr(d.meta.policy.budget)} weekly budget is binding.`
                  : `Within the ${inr(d.meta.policy.budget)} weekly budget.`}
              </>
            )}
          </div>
        </div>
        <div className="figure">
          <div className="figure-label">Fill rate over 28 hidden days</div>
          <div className="figure-value">{policy ? pct(policy.agent.fill_rate, 1) : "Not run"}</div>
          <div className="figure-note">
            {policy && (
              <>
                The status quo reached {pct(policy.status_quo.fill_rate, 1)} on the same demand. The agent held{" "}
                {pct(Math.abs(policy.stock_value_change))} {policy.stock_value_change < 0 ? "less" : "more"} stock.{" "}
                <a href="#evidence">Evidence</a>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="block">
        <Sheet
          title="Demand across all stores, by week"
          subtitle={`History to ${shortDate(d.meta.asOf)}, then the forecast with its 80% interval. Demand is sales plus the estimate of what stockouts hid.`}
          legend={
            <>
              <Key kind="line" color={color.ink}>
                Demand
              </Key>
              <Key kind="line" color={color.quiet}>
                Sales
              </Key>
              <Key kind="line" color={color.accent}>
                Forecast
              </Key>
              <Key kind="rect" color="#F1DEE2">
                80% interval
              </Key>
            </>
          }
          table={{
            columns: ["Week ending", "Demand", "Sales", "Forecast", "P10", "P90"],
            rows: network.weeks.map((w, i) =>
              i < histWeeks
                ? [w, int(network.demand[i]), int(network.sales[i]), "", "", ""]
                : [w, "", "", int(network.forecast.mean[i - histWeeks]), int(network.forecast.p10[i - histWeeks]), int(network.forecast.p90[i - histWeeks])],
            ),
          }}
        >
          <DemandChart weeks={network.weeks} demand={network.demand} sales={network.sales} forecast={network.forecast} events={network.events} height={320} />
        </Sheet>
      </div>

      <div className="block grid-main">
        <div>
          <div className="block-head">
            <h2>What the agent found</h2>
          </div>
          <div className="findings">
            {agent.brief.findings.map((f) => (
              <article className="finding" key={f.title}>
                <span className="finding-icon">
                  <Icon name={FINDING_ICON[f.kind]} size={16} />
                </span>
                <div>
                  <h3>{rupees(f.title)}</h3>
                  <p>{rupees(f.body)}</p>
                </div>
              </article>
            ))}
          </div>
          {agent.brief.narrative && (
            <div className="block">
              <div className="block-head">
                <h2>In the agent's words</h2>
              </div>
              <p className="narrative">{agent.brief.narrative}</p>
            </div>
          )}
        </div>

        <div className="stack">
          <Sheet
            title="Where a sale will be lost first"
            subtitle="Ranked by expected lost revenue this week. Each cell is a size: units on hand, shaded by its chance of selling out."
            foot={<RiskKey />}
          >
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Product and store</th>
                    <th>Sizes S to XL</th>
                    <th className="r">At risk</th>
                  </tr>
                </thead>
                <tbody>
                  {d.risks.slice(0, 7).map((r) => {
                    const p = m.pair(r.s, r.k);
                    return (
                      <tr key={`${r.s}-${r.k}`} className="clickable" onClick={() => go(`/forecast/${d.stores[r.s].id}/${r.sku_id}`)}>
                        <td>
                          <div className="cell-main">{r.product}</div>
                          <div className="cell-sub">
                            {r.store}, expects {r.expected_demand.toFixed(0)} this week
                          </div>
                        </td>
                        <td>{p && <SizeRun sizes={d.meta.sizes} stock={p.stock} risk={p.riskWeek} compact />}</td>
                        <td className="r">{inr(r.expected_lost_revenue)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Sheet>

          <Sheet title="Forecast by category" subtitle="Demand over the last 28 days against the next 28. The line shows 26 weeks of history and the 4 forecast weeks.">
            <table className="data">
              <thead>
                <tr>
                  <th>Category</th>
                  <th className="r">Last 28</th>
                  <th className="r">Next 28</th>
                  <th className="r">Change</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {d.categories.map((c) => (
                  <tr key={c.key}>
                    <td className="cell-main">{c.label}</td>
                    <td className="r">{int(c.demand28)}</td>
                    <td className="r">{int(c.forecast28)}</td>
                    <td className="r">{signedPct(c.forecast28 / Math.max(c.demand28, 1) - 1)}</td>
                    <td style={{ width: 120, paddingRight: 0 }}>
                      <Spark values={[...c.history, ...c.forecast.mean]} emphasis={5} width={116} height={30} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Sheet>
        </div>
      </div>
    </>
  );
}
