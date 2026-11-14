import { useState } from "react";
import { Key, Sheet } from "../charts/base";
import { AnomalyChart, QuotientGrid, SeasonPanel, Spark } from "../charts/Signals";
import { TrendTag } from "../components/parts";
import type { Model } from "../data";
import { int, label, pct, shortDate, signedPct, titleCase } from "../format";
import { color } from "../theme";

export function Signals({ m }: { m: Model }) {
  const { d } = m;
  const [by, setBy] = useState<"category" | "trend">("category");
  const trendBy = m.trendBy;
  const trendName = trendBy ? label(trendBy) : "";
  const main = d.trends.filter((t) => t.attribute === trendBy);
  const others = d.trends.filter((t) => t.attribute !== trendBy && t.status !== "Stable");
  const recent = d.anomalies.filter((a) => a.day >= d.anomalySeries.dates.length - 60);
  const unexplained = recent.filter((a) => !a.cause).length;

  return (
    <>
      <header className="view-head">
        <h1>Trends, anomalies and where demand lives</h1>
        <p>
          {m.has.stores ? "Three" : "Two"} detectors feed the agent. One tests whether a{" "}
          {d.meta.attributes.length > 0 ? d.meta.attributes.slice(0, 3).map((a) => a.replace(/_/g, " ")).join(", ") : "product attribute"}{" "}
          or any other catalog attribute is gaining share of demand. One flags days that the calendar cannot explain.
          {m.has.stores ? " One measures how each store differs from the rest." : ""}
        </p>
      </header>

      <div className="block">
        <div className="block-head">
          <h2>{trendBy ? `${trendName} trends` : "Trends"}</h2>
          <p>Share of demand over 20 weeks, with category seasonality removed. The last 8 weeks are tested.</p>
        </div>
        {!trendBy && (
          <p className="note">
            The catalog has no attribute columns, so there is nothing to test for a trend. Add text columns such as style,
            color, fit or fabric to the catalog and run Luma again.
          </p>
        )}
        {trendBy && (
        <div className="sheet">
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>{trendName}</th>
                  <th>Status</th>
                  <th>Share of demand</th>
                  <th className="r">4-week change</th>
                  {m.has.buzz && <th>Outside interest</th>}
                  {m.has.buzz && <th className="r">Interest, 7 days against 28</th>}
                  <th className="r">q-value</th>
                  {m.has.stores && <th>Strongest in</th>}
                </tr>
              </thead>
              <tbody>
                {main.map((t) => (
                  <tr key={t.value}>
                    <td className="cell-main">{titleCase(t.value)}</td>
                    <td>
                      <TrendTag status={t.status} />
                    </td>
                    <td>
                      <Spark values={t.series} emphasis={8} stroke={t.status === "Fading" ? color.counter : t.status === "Stable" ? color.ink : color.accent} />
                    </td>
                    <td className="r">{signedPct(t.change_4w)}</td>
                    {m.has.buzz && <td>{t.buzz_series && <Spark values={t.buzz_series} emphasis={0} stroke={color.ink} width={110} />}</td>}
                    {m.has.buzz && <td className="r">{t.buzz_momentum === null ? "" : signedPct(t.buzz_momentum)}</td>}
                    <td className="r">{t.q_value < 0.001 ? "<0.001" : t.q_value.toFixed(3)}</td>
                    {m.has.stores && <td>{t.status === "Stable" ? "" : t.leading_stores.slice(0, 2).join(", ")}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="sheet-foot">
            A value is rising or fading when its trend survives a Mann-Kendall test at a 10% false discovery rate, so a
            list of twenty values does not produce two false alarms by chance.
            {m.has.buzz
              ? " It is emerging when outside interest is up more than 10% and sales have only started to follow."
              : " Add a buzz table of search or social interest to catch a trend before sales move."}
          </div>
        </div>
        )}
        {others.length > 0 && (
          <div className="sheet" style={{ marginTop: 20 }}>
            <div className="sheet-head">
              <h3>Also moving, across every other attribute</h3>
            </div>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Attribute</th>
                    <th>Value</th>
                    <th>Status</th>
                    <th>Share of demand</th>
                    <th className="r">4-week change</th>
                    <th className="r">Share now</th>
                    <th className="r">q-value</th>
                  </tr>
                </thead>
                <tbody>
                  {others.map((t) => (
                    <tr key={`${t.attribute}-${t.value}`}>
                      <td>{label(t.attribute)}</td>
                      <td className="cell-main">{titleCase(t.value)}</td>
                      <td>
                        <TrendTag status={t.status} />
                      </td>
                      <td>
                        <Spark values={t.series} emphasis={8} stroke={t.status === "Fading" ? color.counter : color.accent} />
                      </td>
                      <td className="r">{signedPct(t.change_4w)}</td>
                      <td className="r">{pct(t.share, 1)}</td>
                      <td className="r">{t.q_value < 0.001 ? "<0.001" : t.q_value.toFixed(3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      <div className="block">
        <div className="block-head">
          <h2>Anomalies</h2>
          <p>
            {int(recent.length)} in the last 60 days, {int(unexplained)} without a known cause
          </p>
        </div>
        <div className="grid-main">
          <Sheet
            title="Total demand by day"
            subtitle="Expected demand comes from the previous four weeks only, so a flag can be raised the same evening."
            legend={
              <>
                <Key kind="line" color={color.ink}>
                  Demand
                </Key>
                <Key kind="line" color={color.quiet}>
                  Expected
                </Key>
                <Key kind="dot" color={color.accent}>
                  Flagged, no known cause
                </Key>
                <span>
                  <i className="key key-dot" style={{ background: "#fff", boxShadow: `inset 0 0 0 1.75px ${color.ink}` }} />
                  Flagged, explained
                </span>
              </>
            }
            table={{
              columns: ["Date", "Demand", "Expected", "Event"],
              rows: d.anomalySeries.dates.map((dt, i) => [dt, int(d.anomalySeries.actual[i]), int(d.anomalySeries.expected[i]), d.anomalySeries.events[i]]),
            }}
          >
            <AnomalyChart dates={d.anomalySeries.dates} actual={d.anomalySeries.actual} expected={d.anomalySeries.expected} anomalies={d.anomalies} events={d.anomalySeries.events} />
          </Sheet>
          <Sheet
            title="Flagged in the last 60 days"
            subtitle={`${m.has.stores ? "Stores are judged against the total and products" : "Products are judged"} against their category, so one cause is reported once.`}
          >
            <div className="table-wrap" style={{ maxHeight: 330, overflowY: "auto" }}>
              <table className="data">
                <thead>
                  <tr>
                    <th>What</th>
                    <th className="r">Units</th>
                    <th>Cause</th>
                  </tr>
                </thead>
                <tbody>
                  {recent.map((a, i) => (
                    <tr key={i}>
                      <td>
                        <div className="cell-main">{a.name}</div>
                        <div className="cell-sub">
                          {shortDate(a.start)}
                          {a.days > 1 ? `, ${a.days} days` : ""}, {a.direction}
                        </div>
                      </td>
                      <td className="r" style={{ whiteSpace: "nowrap" }}>
                        {int(a.actual)} <span className="cell-sub">vs {int(a.expected)}</span>
                      </td>
                      <td>{a.cause ?? <span className="tag up">No known cause</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Sheet>
        </div>
      </div>

      {m.has.stores && (
      <div className="block">
        <div className="block-head">
          <h2>What each store sells more of</h2>
          {trendBy && (
            <div className="chips" role="group" aria-label="Compare by">
              <button className="chip" aria-pressed={by === "category"} onClick={() => setBy("category")}>
                By category
              </button>
              <button className="chip" aria-pressed={by === "trend"} onClick={() => setBy("trend")}>
                By {trendName.toLowerCase()}
              </button>
            </div>
          )}
        </div>
        <Sheet
          title="Location quotient, last 28 days"
          subtitle="A store's share of demand in a column divided by the share across all stores. 1.00 is the average; 1.30 means the store sells 30% more of it than the business as a whole."
          foot={
            <span className="ramp-key">
              Sells less than average
              <i style={{ width: 120, background: `linear-gradient(90deg, ${color.counter}, #ECEBF0, ${color.accent})` }} />
              Sells more
            </span>
          }
        >
          <QuotientGrid
            rows={d.stores.map((s) => s.name)}
            columns={by === "category" || !trendBy ? d.meta.categories.map((c) => c.label) : d.meta.styles.map(titleCase)}
            values={d.stores.map((s) => (by === "category" || !trendBy ? s.lqCategory : s.lqStyle))}
          />
        </Sheet>
      </div>
      )}

      <div className="block">
        <div className="block-head">
          <h2>Seasonal shape by category</h2>
          <p>Weekly demand per live product{m.has.stores ? " and store" : ""}, so launches{m.has.stores ? " and new stores" : ""} do not read as growth. Average is 100.</p>
        </div>
        <div className="grid-3">
          {d.meta.categories.map((c) => (
            <Sheet key={c.key} title={c.label}>
              <SeasonPanel values={d.seasonality.index[c.key]} weeks={d.seasonality.weeks} label={c.label} />
            </Sheet>
          ))}
          <Sheet title={m.has.stores ? "Weekend lift by store" : "Weekend lift"}>
            <table className="data">
              <thead>
                <tr>
                  <th>Store</th>
                  <th className="r">Saturday</th>
                  <th className="r">Sunday</th>
                </tr>
              </thead>
              <tbody>
                {d.stores.map((s, i) => (
                  <tr key={s.id}>
                    <td>{s.name}</td>
                    <td className="r">{signedPct(d.seasonality.weekday[i][5] - 1)}</td>
                    <td className="r">{signedPct(d.seasonality.weekday[i][6] - 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="note" style={{ marginTop: 8 }}>
              Against each store's own daily average over 8 weeks. {pct(0)} is an average day.
            </p>
          </Sheet>
        </div>
      </div>
    </>
  );
}
