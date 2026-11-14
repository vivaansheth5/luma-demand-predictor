import { useState } from "react";
import { Key, Sheet } from "../charts/base";
import { AnomalyChart, QuotientGrid, SeasonPanel, Spark } from "../charts/Signals";
import { TrendTag } from "../components/parts";
import type { Model } from "../data";
import { int, pct, shortDate, signedPct, titleCase } from "../format";
import { color } from "../theme";

export function Signals({ m }: { m: Model }) {
  const { d } = m;
  const [by, setBy] = useState<"category" | "style">("category");
  const styles = d.trends.filter((t) => t.attribute === "style");
  const others = d.trends.filter((t) => t.attribute !== "style" && t.status !== "Stable");
  const recent = d.anomalies.filter((a) => a.day >= d.anomalySeries.dates.length - 60);
  const unexplained = recent.filter((a) => !a.cause).length;

  return (
    <>
      <header className="view-head">
        <h1>Trends, anomalies and where demand lives</h1>
        <p>
          Three detectors feed the agent. One tests whether a style is gaining share. One flags days that the calendar
          and the weather cannot explain. One measures how each neighbourhood differs from the city.
        </p>
      </header>

      <div className="block">
        <div className="block-head">
          <h2>Style trends</h2>
          <p>Share of demand over 20 weeks, with category seasonality removed. The last 8 weeks are tested.</p>
        </div>
        <div className="sheet">
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Style</th>
                  <th>Status</th>
                  <th>Share of demand</th>
                  <th className="r">4-week change</th>
                  <th>Social buzz</th>
                  <th className="r">Buzz, 7 days against 28</th>
                  <th className="r">q-value</th>
                  <th>Strongest in</th>
                </tr>
              </thead>
              <tbody>
                {styles.map((t) => (
                  <tr key={t.value}>
                    <td className="cell-main">{titleCase(t.value)}</td>
                    <td>
                      <TrendTag status={t.status} />
                    </td>
                    <td>
                      <Spark values={t.series} emphasis={8} stroke={t.status === "Fading" ? color.counter : t.status === "Stable" ? color.ink : color.accent} />
                    </td>
                    <td className="r">{signedPct(t.change_4w)}</td>
                    <td>{t.buzz_series && <Spark values={t.buzz_series} emphasis={0} stroke={color.ink} width={110} />}</td>
                    <td className="r">{t.buzz_momentum === null ? "" : signedPct(t.buzz_momentum)}</td>
                    <td className="r">{t.q_value < 0.001 ? "<0.001" : t.q_value.toFixed(3)}</td>
                    <td>{t.status === "Stable" ? "" : t.leading_stores.slice(0, 2).join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="sheet-foot">
            A style is rising or fading when its trend survives a Mann-Kendall test at a 10% false discovery rate. It is
            emerging when buzz is up more than 10% and sales have only started to follow. Buzz leads sales by about ten days.
          </div>
        </div>
        {others.length > 0 && (
          <p className="note" style={{ marginTop: 14 }}>
            Also moving: {others.map((t) => `${t.value} ${t.attribute === "subtype" ? "" : t.attribute + " "}(${t.status.toLowerCase()}, ${signedPct(t.change_4w)})`).join("; ")}.
          </p>
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
            title="City demand by day"
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
          <Sheet title="Flagged in the last 60 days" subtitle="Stores are judged against the city and products against their category, so one cause is reported once.">
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

      <div className="block">
        <div className="block-head">
          <h2>What each neighbourhood buys</h2>
          <div className="chips" role="group" aria-label="Compare by">
            <button className="chip" aria-pressed={by === "category"} onClick={() => setBy("category")}>
              By category
            </button>
            <button className="chip" aria-pressed={by === "style"} onClick={() => setBy("style")}>
              By style
            </button>
          </div>
        </div>
        <Sheet
          title="Location quotient, last 28 days"
          subtitle="A store's share of demand in a column divided by the city's share. 1.00 is the city average; 1.30 means the store sells 30% more of it than the city does."
          foot={
            <span className="ramp-key">
              Sells less than the city
              <i style={{ width: 120, background: `linear-gradient(90deg, ${color.counter}, #EFEBEC, ${color.accent})` }} />
              Sells more
            </span>
          }
        >
          <QuotientGrid
            rows={d.stores.map((s) => s.name)}
            columns={by === "category" ? d.meta.categories.map((c) => c.label) : d.meta.styles.map(titleCase)}
            values={d.stores.map((s) => (by === "category" ? s.lqCategory : s.lqStyle))}
          />
        </Sheet>
      </div>

      <div className="block">
        <div className="block-head">
          <h2>Seasonal shape by category</h2>
          <p>Weekly demand per live product and store, so new stores and launches do not read as growth. Average is 100.</p>
        </div>
        <div className="grid-3">
          {d.meta.categories.map((c) => (
            <Sheet key={c.key} title={c.label}>
              <SeasonPanel values={d.seasonality.index[c.key]} weeks={d.seasonality.weeks} label={c.label} />
            </Sheet>
          ))}
          <Sheet title="Weekend lift by store">
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
