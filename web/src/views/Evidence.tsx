import { Key, Sheet } from "../charts/base";
import { RankBars } from "../charts/Bars";
import { Calibration, Frontier, LostDemand } from "../charts/Evidence";
import { TrendTag } from "../components/parts";
import type { Model } from "../data";
import { inr, int, pct, shortDate, signedPct, titleCase } from "../format";
import { color } from "../theme";
import type { PolicyResult } from "../types";

const AGENT = "Demand agent";
const TRAILING = "Trailing 28-day sales";
const ORACLE = "Oracle (noise floor)";

export function Evidence({ m }: { m: Model }) {
  const { d } = m;
  const ev = d.evaluation;
  const f = ev.forecast;
  const policy = ev.policy;
  const h = policy?.headline;
  const methods = f.methods.filter((name) => name !== ORACLE);
  const liveFold = f.folds.find((x) => x.live);
  const i90 = f.coverage.nominal.indexOf(0.9);
  const p90 = f.coverage.calibrated[i90];
  const p90Raw = f.coverage.raw[i90];
  const row = (r: PolicyResult, label: string, emph = false) => (
    <tr key={label} className={emph ? "emph" : undefined}>
      <td>{label}</td>
      <td className="r">{pct(r.fill_rate, 1)}</td>
      <td className="r">{inr(r.lost_revenue)}</td>
      <td className="r">{inr(r.avg_stock_value)}</td>
      <td className="r">{r.transfer_units ? `${int(r.transfer_units)} units, ${inr(r.transfer_cost)}` : "None"}</td>
      <td className="r">{inr(r.net_contribution)}</td>
    </tr>
  );

  return (
    <>
      <header className="view-head">
        <h1>Does it work, and how do we know</h1>
        <p>
          The network is simulated, which gives one thing real data never can: the true demand, including every sale
          lost to an empty shelf. Each part of the system is scored against that truth, and the agent is judged by what
          happens when its decisions run.
        </p>
      </header>

      {policy && h && (
        <div className="block">
          <div className="block-head">
            <h2>The test that matters: 28 days the agent never saw</h2>
          </div>
          <p className="block-note">
            Both policies start from the same shelves on {shortDate(d.meta.asOf)}, order every Sunday and sell into the same hidden
            demand. Each Sunday the agent sees only the sales its own decisions produced, re-forecasts and re-plans.
            Nothing is retrained.
          </p>
          <div className="grid-main">
            <Sheet
              title="Service bought per rupee of stock"
              subtitle="Each point is one setting of a policy's dial. Buying more stock always buys more service, so the comparison is between whole curves."
              legend={
                <>
                  <Key kind="line" color={color.quiet}>
                    Status quo, by days of cover
                  </Key>
                  <Key kind="line" color={color.accent}>
                    Agent, orders only, by service level
                  </Key>
                  <Key kind="dot" color={color.maroon}>
                    Agent with transfers, under the weekly budget
                  </Key>
                </>
              }
              table={{
                columns: ["Policy", "Setting", "Fill rate", "Average stock", "Lost revenue"],
                rows: policy.results.map((r) => [r.name, r.dial, pct(r.fill_rate, 1), inr(r.avg_stock_value), inr(r.lost_revenue)]),
              }}
            >
              <Frontier results={policy.results} />
            </Sheet>
            <div>
              <p className="statement">
                The agent lost {pct(h.lost_revenue_cut)} less revenue to stockouts than the status quo, while holding{" "}
                {pct(Math.abs(h.stock_value_change))} {h.stock_value_change < 0 ? "less" : "more"} stock.
              </p>
              <p className="statement-sub">
                Fill rate rose from {pct(h.status_quo.fill_rate, 1)} to {pct(h.agent.fill_rate, 1)}.{" "}
                {h.same_service_reached
                  ? `The status quo needs ${h.same_service_baseline.dial} of cover and ${inr(h.same_service_baseline.avg_stock_value)} of stock to match that.`
                  : `Even at ${h.same_service_baseline.dial} of cover and ${inr(h.same_service_baseline.avg_stock_value)} of stock, the status quo reaches only ${pct(h.same_service_baseline.fill_rate, 1)}.`}
              </p>
              <p className="statement-sub">
                Transfers add {inr(h.transfer_contribution)} of net contribution in four weeks. Against the status quo the
                agent adds {inr(h.net_contribution_gain)}.
              </p>
            </div>
          </div>
          <div style={{ marginTop: 20 }}>
            <Sheet title="Same 28 days, three policies" subtitle={`Net contribution is gross margin minus transfer and holding costs. Both agent runs work within the ${inr(policy.weekly_budget)} weekly supplier budget.`}>
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Policy</th>
                      <th className="r">Fill rate</th>
                      <th className="r">Lost revenue</th>
                      <th className="r">Average stock</th>
                      <th className="r">Transfers</th>
                      <th className="r">Net contribution</th>
                    </tr>
                  </thead>
                  <tbody>
                    {row(h.status_quo, "Status quo: 21 days of cover on in-stock sales")}
                    {row(h.agent_orders_only, "Agent, supplier orders only")}
                    {row(h.agent, "Agent with transfers", true)}
                  </tbody>
                </table>
              </div>
            </Sheet>
          </div>
        </div>
      )}

      <div className="block">
        <div className="block-head">
          <h2>Forecast accuracy</h2>
        </div>
        <p className="block-note">
          Five forecasts made 28 days apart, each trained only on data before its own date, scored against true demand
          for every product, store and window ({int(f.rows)} forecasts). Error is total absolute miss over total demand.
        </p>
        <div className="grid-2">
          <Sheet
            title={`The agent removes ${pct(f.skill_vs_trailing)} of the avoidable error`}
            subtitle="At product and store level most error is chance: a product that sells three a week will sell one or five. The oracle knows the true expected demand, so its error is the floor."
            table={{
              columns: ["Method", "Error", "Bias"],
              rows: f.methods.map((name) => [name, pct(f.overall[name].wape, 1), signedPct(f.overall[name].bias, 1)]),
            }}
          >
            <RankBars
              items={methods
                .map((name) => ({ label: name, value: f.overall[name].wape, emphasis: name === AGENT, note: `Bias ${signedPct(f.overall[name].bias, 1)}` }))
                .sort((a, b) => a.value - b.value)}
              format={(v) => pct(v, 1)}
              reference={{ value: f.noise_floor, label: `Noise floor ${pct(f.noise_floor, 1)}` }}
            />
          </Sheet>
          <Sheet title="Error falls as forecasts are added up" subtitle="Random misses cancel when products are summed; what remains is the error that planning decisions feel. 28-day window.">
            <table className="data">
              <thead>
                <tr>
                  <th>Level</th>
                  <th className="r">Demand agent</th>
                  <th className="r">Trailing 28-day sales</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(f.levels["28 days"]).map(([level, v]) => (
                  <tr key={level}>
                    <td>{level.replace(" x ", " and ").toLowerCase().replace(/^./, (c) => c.toUpperCase())}</td>
                    <td className="r" style={{ fontWeight: 600 }}>{pct(v[AGENT], 1)}</td>
                    <td className="r">{pct(v[TRAILING], 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <table className="data" style={{ marginTop: 18 }}>
              <thead>
                <tr>
                  <th>Product history in the store</th>
                  <th className="r">Demand agent</th>
                  <th className="r">Trailing 28-day sales</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(f.by_age).map(([age, v]) => (
                  <tr key={age}>
                    <td>{age}</td>
                    <td className="r" style={{ fontWeight: 600 }}>{pct(v[AGENT], 1)}</td>
                    <td className="r">{pct(v[TRAILING], 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Sheet>
        </div>
      </div>

      <div className="block">
        <div className="block-head">
          <h2>Are the ranges honest</h2>
        </div>
        <p className="block-note">
          Stock decisions read the top of the forecast range, so a P90 has to mean 90%. Each quantile is shifted by the
          amount that would have corrected the spread of earlier forecasts, once their 28 days had passed.
        </p>
        <div className="grid-2">
          <Sheet
            title={`A stated P90 holds ${pct(p90, 1)} of outcomes below it`}
            subtitle={`Before calibration it held ${pct(p90Raw, 1)}. The 80% interval covers ${pct(f.interval80.calibrated, 1)}: its low end runs high on slow sellers, where a count of zero or one leaves little room.`}
            legend={
              <>
                <Key kind="line" color={color.quiet}>
                  Before calibration
                </Key>
                <Key kind="line" color={color.accent}>
                  After calibration
                </Key>
              </>
            }
            table={{
              columns: ["Quantile", "Before", "After"],
              rows: f.coverage.nominal.map((q, i) => [`P${Math.round(q * 100)}`, pct(f.coverage.raw[i], 1), pct(f.coverage.calibrated[i], 1)]),
            }}
          >
            <Calibration nominal={f.coverage.nominal} raw={f.coverage.raw} calibrated={f.coverage.calibrated} />
          </Sheet>
          <Sheet
            title={`Uncensoring recovers ${pct(ev.uncensoring.recovery_ratio)} of the demand stockouts hid`}
            subtitle={`Raw sales understate demand by ${pct(Math.abs(ev.uncensoring.bias_sales), 1)}. After correction the total is within ${pct(Math.abs(ev.uncensoring.bias_uncensored), 1)} of the truth.`}
            legend={
              <>
                <Key kind="rect" color={color.quiet}>
                  Units truly lost
                </Key>
                <Key kind="line" color={color.accent}>
                  Units the model estimates
                </Key>
              </>
            }
          >
            <LostDemand truth={ev.uncensoring.weekly_true} sales={ev.uncensoring.weekly_sales} estimate={ev.uncensoring.weekly_estimate} weeks={d.seasonality.weeks} />
          </Sheet>
        </div>
      </div>

      <div className="block">
        <div className="block-head">
          <h2>Supporting models</h2>
        </div>
        <div className="grid-3">
          <Sheet title="Size curves" subtitle="Average error in a size's share, against the true mix. Pooling helps most where data is thin.">
            <table className="data">
              <thead>
                <tr>
                  <th>Units sold</th>
                  <th className="r">Raw</th>
                  <th className="r">Pooled</th>
                </tr>
              </thead>
              <tbody>
                {ev.size_curves.buckets.map((b) => (
                  <tr key={b.bucket}>
                    <td>
                      {b.bucket} <span className="cell-sub">({b.pairs})</span>
                    </td>
                    <td className="r">{(100 * b.raw_mae).toFixed(1)} pts</td>
                    <td className="r" style={{ fontWeight: 600 }}>{(100 * b.pooled_mae).toFixed(1)} pts</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Sheet>
          <Sheet title="Trend detection" subtitle="What the detector said on the as-of date, next to how each style's true pull moved in the 4 weeks after.">
            <table className="data">
              <thead>
                <tr>
                  <th>Style</th>
                  <th>Called</th>
                  <th className="r">True, next 4 weeks</th>
                </tr>
              </thead>
              <tbody>
                {ev.trends.map((t) => (
                  <tr key={t.style}>
                    <td>{titleCase(t.style)}</td>
                    <td>
                      <TrendTag status={t.status} />
                    </td>
                    <td className="r">{signedPct(t.true_change_next_4w)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Sheet>
          <Sheet title="Anomaly detection" subtitle="Shocks the simulator injected, and how many the detector found without a known cause to hide behind.">
            <table className="data">
              <thead>
                <tr>
                  <th>Shock</th>
                  <th className="r">Found</th>
                  <th className="r">Precision</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(ev.anomalies).map(([kind, a]) => (
                  <tr key={kind}>
                    <td>{kind === "viral_spike" ? "Viral product spikes" : "One-day store outages"}</td>
                    <td className="r">
                      {a.detected} of {a.injected}
                    </td>
                    <td className="r">{a.precision === null ? "" : pct(a.precision)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="note" style={{ marginTop: 10 }}>
              Precision is the share of unexplained flags that were real shocks.
            </p>
          </Sheet>
        </div>
      </div>

      <div className="block">
        <div className="block-head">
          <h2>What is real and what is simulated</h2>
        </div>
        <p className="lede">
          The stores, products, sales and stock are generated by a simulator with festivals, weather, product lifecycles,
          style trends and stockouts. The models see only what a real system would record: sales, stock, the calendar,
          planned discounts and a buzz index. They never see true demand. Results on real data will differ, and the
          same pipeline runs on real tables without changes. This run used {int(d.meta.counts.salesRows)} sales rows,{" "}
          {d.meta.model.features} features and {int(d.meta.model.trainRows)} training rows
          {liveFold ? `, and the live forecast was made on ${shortDate(liveFold.origin)}` : ""}.
        </p>
      </div>
    </>
  );
}
