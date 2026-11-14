import { RankBars } from "../charts/Bars";
import { Key, Sheet } from "../charts/base";
import { DemandChart } from "../charts/DemandChart";
import { StoreMap } from "../charts/StoreMap";
import { Spark } from "../charts/Signals";
import { Icon, RiskKey, SizeRun, TrendTag } from "../components/parts";
import { openPair, type Model } from "../data";
import { count, int, label, longDate, money, pct, shortDate, signedPct } from "../format";
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

const AGENT = "Luma";
const TRAILING = "Trailing 28-day sales";

export function Brief({ m }: { m: Model }) {
  const { d } = m;
  const { agent, plan, network, evaluation } = d;
  const summary = plan.summary;
  const policy = evaluation.policy?.headline;
  const accuracy = evaluation.forecast;
  const review = plan.orders.filter((o) => o.approval === "review").length;
  const shownLanes = Math.min(10, plan.lanes.length);
  const moving = d.trends.filter((t) => t.status !== "Stable");
  const rising = moving.filter((t) => t.status === "Rising" || t.status === "Emerging");
  // Products whose forecast differs most from their last 28 days, shown when there is no stock to rank by.
  const movers = d.skus
    .map((sku, k) => ({ sku, k, change: sku.forecast28 - sku.demand28 }))
    .filter((r) => r.sku.demand28 + r.sku.forecast28 >= 8)
    .sort((a, b) => Math.abs(b.change) - Math.abs(a.change))
    .slice(0, 7);
  const firstStoreOf = (k: number) => d.pairs.find((p) => p.k === k)?.s ?? 0;
  const change = (next: number, last: number) => signedPct(next / Math.max(last, 1) - 1);
  const { demand28, forecast28, revenue28 } = network;
  const histWeeks = network.demand.length;

  return (
    <>
      <section className="hero">
        <div>
          <p className="hero-date">
            {longDate(d.meta.asOf)}, {summary ? "this week's decision" : "the next four weeks"}
          </p>
          <h1>{agent.brief.headline}</h1>
          <div className="hero-actions">
            {summary ? (
              <a className="btn" href="#plan">
                Review the plan
              </a>
            ) : (
              <a className="btn" href="#signals">
                See the trends
              </a>
            )}
            <a className="btn ghost" href="#trace">
              See how it was decided
            </a>
          </div>
          <p className="hero-meta">
            Luma took {agent.steps.length} steps.{" "}
            {plan.orders.length > 0 && (
              <>
                {int(plan.orders.length - review)} order lines are approved automatically and {int(review)} wait for a
                person.
              </>
            )}{" "}
            {m.has.truth
              ? "This is the demo: a simulated retailer, so every forecast and decision can be scored against true demand."
              : `Built from ${d.meta.counts.days} days of ${d.meta.brand} sales${m.has.stock ? " and the current stock position" : ""}.`}
          </p>
        </div>
        {m.has.map ? (
          <Sheet
            title="Every store, the next 28 days"
            subtitle={`Circle size is forecast demand. ${m.has.stock ? "Darker circles have more sizes about to sell out. " : ""}${
              plan.lanes.length > 0
                ? `Arrows are the ${shownLanes === plan.lanes.length ? "" : `${shownLanes} largest of the `}${plan.lanes.length} transfer lanes in this plan.`
                : ""
            }`}
          >
            <StoreMap stores={d.stores} lanes={plan.lanes} maxLanes={10} height={400} />
          </Sheet>
        ) : m.has.stores ? (
          <Sheet title="Forecast by store" subtitle="Expected units over the next 28 days, with the change against the last 28.">
            <RankBars
              items={[...d.stores]
                .sort((a, b) => b.forecast28 - a.forecast28)
                .slice(0, 10)
                .map((st) => ({ label: st.name, value: st.forecast28, note: `${change(st.forecast28, st.demand28)} on the last 28 days` }))}
              format={(v) => int(v)}
            />
          </Sheet>
        ) : (
          <Sheet title="Largest products, next 28 days" subtitle="Expected units, with the change against the last 28 days.">
            <RankBars
              items={[...d.skus]
                .sort((a, b) => b.forecast28 - a.forecast28)
                .slice(0, 8)
                .map((sku) => ({ label: sku.name, value: sku.forecast28, note: `${change(sku.forecast28, sku.demand28)} on the last 28 days` }))}
              format={(v) => int(v)}
            />
          </Sheet>
        )}
      </section>

      <div className="figures">
        <div className="figure">
          <div className="figure-label">Forecast, next 28 days</div>
          <div className="figure-value">{int(forecast28)} units</div>
          <div className="figure-note">
            {signedPct(forecast28 / demand28 - 1)} against the last 28 days. About {money(revenue28)} of revenue.
          </div>
        </div>
        {d.riskWeek ? (
          <div className="figure">
            <div className="figure-label">Sales at risk this week</div>
            <div className="figure-value">{money(d.riskWeek.lostRevenue)}</div>
            <div className="figure-note">
              {count(d.riskWeek.cells, "size")} {d.riskWeek.cells === 1 ? "is" : "are"} more likely than not to sell out before a
              supplier order can land.
            </div>
          </div>
        ) : (
          <div className="figure">
            <div className="figure-label">Trends in motion</div>
            <div className="figure-value">{int(moving.length)}</div>
            <div className="figure-note">
              {moving.length > 0
                ? `${int(rising.length)} gaining share of demand and ${int(moving.length - rising.length)} losing it, after a false-discovery check.`
                : "No attribute value is gaining or losing share beyond chance."}{" "}
              <a href="#signals">Signals</a>
            </div>
          </div>
        )}
        {summary ? (
          <div className="figure">
            <div className="figure-label">Supplier orders</div>
            <div className="figure-value">{money(summary.supplier_orders.cost)}</div>
            <div className="figure-note">
              {int(summary.supplier_orders.units)} units on {int(summary.supplier_orders.lines)} lines.{" "}
              {summary.supplier_orders.budget_binding
                ? `The ${money(d.meta.policy.budget)} budget is binding.`
                : `Within the ${money(d.meta.policy.budget)} budget.`}
            </div>
          </div>
        ) : (
          <div className="figure">
            <div className="figure-label">Products forecast</div>
            <div className="figure-value">{int(d.meta.counts.skus)}</div>
            <div className="figure-note">
              {int(d.meta.counts.pairs)} product and store pairs, each split across {count(d.meta.counts.sizes, "size")}.
            </div>
          </div>
        )}
        {policy ? (
          <div className="figure">
            <div className="figure-label">Fill rate over 28 hidden days</div>
            <div className="figure-value">{pct(policy.agent.fill_rate, 1)}</div>
            <div className="figure-note">
              The status quo reached {pct(policy.status_quo.fill_rate, 1)} on the same demand. Luma held{" "}
              {pct(Math.abs(policy.stock_value_change))} {policy.stock_value_change < 0 ? "less" : "more"} stock.{" "}
              <a href="#evidence">Evidence</a>
            </div>
          </div>
        ) : accuracy ? (
          <div className="figure">
            <div className="figure-label">Forecast error on past dates</div>
            <div className="figure-value">{pct(accuracy.overall[AGENT].wape, 1)}</div>
            <div className="figure-note">
              A trailing 28-day average scores {pct(accuracy.overall[TRAILING].wape, 1)} on the same{" "}
              {count(accuracy.folds.length, "forecast date")}. <a href="#evidence">Evidence</a>
            </div>
          </div>
        ) : (
          <div className="figure">
            <div className="figure-label">Forecast error on past dates</div>
            <div className="figure-value">Not yet</div>
            <div className="figure-note">The history is too short to replay an earlier forecast. About 20 weeks are needed.</div>
          </div>
        )}
      </div>

      <div className="block">
        <Sheet
          title={`${m.has.stockHistory ? "Demand" : "Sales"}${m.has.stores ? " across all stores" : ""}, by week`}
          subtitle={`History to ${shortDate(d.meta.asOf)}, then the forecast with its 80% interval.${m.has.stockHistory ? " Demand is sales plus the estimate of what stockouts hid." : ""}`}
          legend={
            <>
              <Key kind="line" color={color.ink}>
                {m.has.stockHistory ? "Demand" : "Sales"}
              </Key>
              {m.has.stockHistory && (
                <Key kind="line" color={color.quiet}>
                  Sales
                </Key>
              )}
              <Key kind="line" color={color.accent}>
                Forecast
              </Key>
              <Key kind="rect" color="#EAE9F9">
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
          <DemandChart
            weeks={network.weeks}
            demand={network.demand}
            sales={m.has.stockHistory ? network.sales : undefined}
            demandLabel={m.has.stockHistory ? "Demand" : "Sales"}
            forecast={network.forecast}
            events={network.events}
            height={320}
          />
        </Sheet>
      </div>

      <div className="block grid-main">
        <div>
          <div className="block-head">
            <h2>What Luma found</h2>
          </div>
          <div className="findings">
            {agent.brief.findings.map((f) => (
              <article className="finding" key={f.title}>
                <span className="finding-icon">
                  <Icon name={FINDING_ICON[f.kind]} size={16} />
                </span>
                <div>
                  <h3>{f.title}</h3>
                  <p>{f.body}</p>
                </div>
              </article>
            ))}
          </div>
          {agent.brief.narrative && (
            <div className="block">
              <div className="block-head">
                <h2>In Luma's words</h2>
              </div>
              <p className="narrative">{agent.brief.narrative}</p>
            </div>
          )}
        </div>

        <div className="stack">
          {d.risks.length > 0 ? (
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
                      <th>Stock by size</th>
                      <th className="r">At risk</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.risks.slice(0, 7).map((r) => {
                      const p = m.pair(r.s, r.k);
                      return (
                        <tr key={`${r.s}-${r.k}`} className="clickable" onClick={() => openPair(r.s, r.k)}>
                          <td>
                            <div className="cell-main">{r.product}</div>
                            <div className="cell-sub">
                              {r.store}, expects {r.expected_demand.toFixed(0)} this week
                            </div>
                          </td>
                          <td>{p?.stock && p.riskWeek && <SizeRun sizes={d.meta.sizes} stock={p.stock} risk={p.riskWeek} compact />}</td>
                          <td className="r">{money(r.expected_lost_revenue)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Sheet>
          ) : (
            <Sheet
              title="Products changing fastest"
              subtitle={`Forecast for the next 28 days against demand over the last 28${m.has.stores ? ", across all stores" : ""}.`}
            >
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Product</th>
                      <th className="r">Last 28</th>
                      <th className="r">Next 28</th>
                      <th className="r">Change</th>
                    </tr>
                  </thead>
                  <tbody>
                    {movers.map(({ sku, k }) => (
                      <tr key={sku.id} className="clickable" onClick={() => openPair(firstStoreOf(k), k)}>
                        <td>
                          <div className="cell-main">{sku.name}</div>
                          <div className="cell-sub" style={{ display: "flex", gap: 8, alignItems: "center" }}>
                            {m.trendBy && sku.trendValue && (
                              <span>
                                {label(m.trendBy)}: {sku.trendValue}
                              </span>
                            )}
                            {sku.trend && sku.trend !== "Stable" && <TrendTag status={sku.trend} />}
                          </div>
                        </td>
                        <td className="r">{int(sku.demand28)}</td>
                        <td className="r">{int(sku.forecast28)}</td>
                        <td className="r">{change(sku.forecast28, sku.demand28)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Sheet>
          )}

          <Sheet title="Forecast by category" subtitle={`Demand over the last 28 days against the next 28. The line shows ${histWeeks} weeks of history and the 4 forecast weeks.`}>
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
                    <td className="r">{change(c.forecast28, c.demand28)}</td>
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
