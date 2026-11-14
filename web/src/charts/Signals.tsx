import { useState } from "react";
import { scaleLinear } from "d3-scale";
import { area, line } from "d3-shape";
import { color, divergingColor, inkOn } from "../theme";
import { int, shortDate } from "../format";
import type { Anomaly } from "../types";
import { axisNumber, Plot, TipRow, YGrid, type Tip } from "./base";

/** A small line with its last `emphasis` points in colour and an end dot. */
export function Spark({ values, emphasis = 0, width = 132, height = 36, stroke = color.accent }: { values: number[]; emphasis?: number; width?: number; height?: number; stroke?: string }) {
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const x = scaleLinear().domain([0, values.length - 1]).range([2, width - 6]);
  const y = scaleLinear().domain([lo, hi === lo ? lo + 1 : hi]).range([height - 4, 5]);
  const path = line<number>().x((_, i) => x(i)).y((d) => y(d));
  const split = emphasis > 0 ? values.length - emphasis : 0;
  return (
    <svg width={width} height={height} aria-hidden="true" style={{ display: "block" }}>
      {split > 0 && <path d={path(values.slice(0, split + 1)) ?? ""} fill="none" stroke={color.quiet} strokeWidth={1.5} />}
      <path
        d={line<number>().x((_, i) => x(i + split)).y((d) => y(d))(values.slice(split)) ?? ""}
        fill="none"
        stroke={stroke}
        strokeWidth={2}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle cx={x(values.length - 1)} cy={y(values[values.length - 1])} r={3.5} fill={stroke} stroke="#fff" strokeWidth={1.5} />
    </svg>
  );
}

/**
 * Location quotient: how much more or less of something a store sells than the city does.
 * 1.0 is the city average, so the scale diverges from a neutral midpoint.
 */
export function QuotientGrid({ rows, columns, values }: { rows: string[]; columns: string[]; values: number[][] }) {
  const [tip, setTip] = useState<Tip | null>(null);
  const cellH = 34;
  const head = 62;
  const height = head + rows.length * cellH + 4;
  return (
    <Plot height={height} tip={tip}>
      {(width) => {
        const left = 104;
        const cw = (width - left) / columns.length;
        return (
          <svg width={width} height={height} role="img" aria-label="Location quotient by store">
            {columns.map((c, j) => (
              <text key={c} className="label" x={left + j * cw + cw / 2} y={head - 10} textAnchor={cw < 78 ? "start" : "middle"} transform={cw < 78 ? `rotate(-32,${left + j * cw + cw / 2},${head - 10})` : undefined}>
                {c}
              </text>
            ))}
            {rows.map((r, i) => (
              <g key={r}>
                <text className="label" x={left - 10} y={head + i * cellH + cellH / 2} dy="0.32em" textAnchor="end">
                  {r}
                </text>
                {columns.map((c, j) => {
                  const v = values[i][j];
                  const fill = divergingColor(Math.max(-1, Math.min(1, Math.log(Math.max(v, 0.05)) / Math.log(1.7))));
                  const cx = left + j * cw;
                  const cy = head + i * cellH;
                  return (
                    <g
                      key={c}
                      onPointerEnter={() =>
                        setTip({
                          x: cx + cw / 2,
                          y: cy,
                          content: (
                            <>
                              <div className="tt-title">
                                {c} in {r}
                              </div>
                              <div>
                                {Math.abs(v - 1) < 0.03
                                  ? "Sells at the average of all stores."
                                  : `Sells ${Math.abs(Math.round((v - 1) * 100))}% ${v > 1 ? "more" : "less"} than the average of all stores, as a share of the store's demand.`}
                              </div>
                            </>
                          ),
                        })
                      }
                      onPointerLeave={() => setTip(null)}
                    >
                      <rect x={cx + 1} y={cy + 1} width={Math.max(cw - 2, 1)} height={cellH - 2} rx={4} fill={fill} />
                      {cw >= 40 && (
                        <text x={cx + cw / 2} y={cy + cellH / 2} dy="0.32em" textAnchor="middle" style={{ fill: inkOn(fill), fontSize: 12, fontWeight: 550 }}>
                          {v.toFixed(2)}
                        </text>
                      )}
                    </g>
                  );
                })}
              </g>
            ))}
          </svg>
        );
      }}
    </Plot>
  );
}

/** One category's seasonal shape as a small panel (demand per live product-store, mean = 100). */
export function SeasonPanel({ values, weeks, label, height = 132 }: { values: number[]; weeks: string[]; label: string; height?: number }) {
  const [tip, setTip] = useState<Tip | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const m = { top: 8, right: 8, bottom: 20, left: 30 };
  const n = values.length;
  return (
    <Plot height={height} tip={tip}>
      {(width) => {
        const x = scaleLinear().domain([0, n - 1]).range([m.left, width - m.right]);
        const y = scaleLinear().domain([0, 240]).range([height - m.bottom, m.top]).clamp(true);
        const years = weeks.map((w, i) => ({ i, w })).filter(({ w }) => w.slice(5, 7) === "01" && Number(w.slice(8)) <= 7);
        const quarter = weeks.map((w, i) => ({ i, d: new Date(`${w}T00:00:00Z`) })).filter(({ d }) => d.getUTCDate() <= 7 && d.getUTCMonth() % 3 === 0);
        return (
          <svg width={width} height={height} role="img" aria-label={`${label} seasonal index`}>
            {[100, 200].map((t) => (
              <g key={t}>
                <line x1={m.left} x2={width - m.right} y1={y(t)} y2={y(t)} stroke={t === 100 ? color.axis : color.grid} />
                <text x={m.left - 6} y={y(t)} dy="0.32em" textAnchor="end">
                  {t}
                </text>
              </g>
            ))}
            {years.map(({ i }) => (
              <line key={i} x1={x(i)} x2={x(i)} y1={m.top} y2={height - m.bottom} stroke={color.grid} />
            ))}
            {quarter.map(({ i, d }) => (
              <text key={i} x={x(i)} y={height - 5} textAnchor="middle">
                {d.toLocaleString("en-GB", { month: "short", timeZone: "UTC" })}
              </text>
            ))}
            <path d={area<number>().x((_, i) => x(i)).y0(y(0)).y1((d) => y(d))(values) ?? ""} fill={color.ink} opacity={0.06} />
            <path d={line<number>().x((_, i) => x(i)).y((d) => y(d))(values) ?? ""} fill="none" stroke={color.ink} strokeWidth={1.75} strokeLinejoin="round" />
            {hover !== null && <circle cx={x(hover)} cy={y(values[hover])} r={4} fill={color.ink} stroke="#fff" strokeWidth={2} />}
            <rect
              x={m.left}
              y={0}
              width={Math.max(0, width - m.left - m.right)}
              height={height - m.bottom}
              fill="transparent"
              onPointerMove={(e) => {
                const r = e.currentTarget.getBoundingClientRect();
                const i = Math.max(0, Math.min(n - 1, Math.round(x.invert(e.clientX - r.left + m.left))));
                setHover(i);
                setTip({ x: x(i), y: y(values[i]), content: (
                  <>
                    <div className="tt-title">{label}, week ending {shortDate(weeks[i])}</div>
                    <TipRow label="Index" value={String(Math.round(values[i]))} />
                  </>
                ) });
              }}
              onPointerLeave={() => {
                setHover(null);
                setTip(null);
              }}
            />
          </svg>
        );
      }}
    </Plot>
  );
}

/** City demand by day against its expected level, with flagged days marked. */
export function AnomalyChart({ dates, actual, expected, anomalies, events, height = 280 }: { dates: string[]; actual: number[]; expected: number[]; anomalies: Anomaly[]; events: string[]; height?: number }) {
  const [tip, setTip] = useState<Tip | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const m = { top: 14, right: 16, bottom: 26, left: 46 };
  const n = dates.length;
  const flagged = new Map<number, Anomaly>();
  anomalies
    .filter((a) => a.scope === "city")
    .forEach((a) => {
      for (let d = 0; d < a.days; d++) if (a.day + d >= 0 && a.day + d < n) flagged.set(a.day + d, a);
    });
  return (
    <Plot height={height} tip={tip}>
      {(width) => {
        const x = scaleLinear().domain([0, n - 1]).range([m.left + 4, width - m.right]);
        const y = scaleLinear().domain([0, Math.max(...actual) * 1.08]).nice(4).range([height - m.bottom, m.top]);
        const path = (vals: number[]) => line<number>().defined((d) => d > 0).x((_, i) => x(i)).y((d) => y(d))(vals) ?? "";
        const ticks = dates.map((d, i) => ({ i, d })).filter(({ d }) => d.endsWith("-01") || d.endsWith("-15"));
        return (
          <svg width={width} height={height} role="img" aria-label="Daily total demand against expectation">
            <YGrid scale={y} left={m.left} right={width - m.right} ticks={y.ticks(4)} format={axisNumber} />
            {ticks.map(({ i, d }) => (
              <text key={i} x={x(i)} y={height - 7} textAnchor="middle">
                {shortDate(d)}
              </text>
            ))}
            <path d={path(expected)} fill="none" stroke={color.quiet} strokeWidth={1.5} />
            <path d={path(actual)} fill="none" stroke={color.ink} strokeWidth={1.75} strokeLinejoin="round" />
            {[...flagged.entries()].map(([i, a]) => (
              <circle key={i} cx={x(i)} cy={y(actual[i])} r={4.5} fill={a.cause ? color.surface : color.accent} stroke={a.cause ? color.ink : "#fff"} strokeWidth={a.cause ? 1.75 : 2} />
            ))}
            {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={m.top} y2={height - m.bottom} stroke={color.ink} strokeOpacity={0.35} />}
            <rect
              x={m.left}
              y={m.top}
              width={Math.max(0, width - m.left - m.right)}
              height={height - m.top - m.bottom}
              fill="transparent"
              onPointerMove={(e) => {
                const r = e.currentTarget.getBoundingClientRect();
                const i = Math.max(0, Math.min(n - 1, Math.round(x.invert(e.clientX - r.left + m.left))));
                const a = flagged.get(i);
                setHover(i);
                setTip({
                  x: x(i),
                  y: y(actual[i]),
                  content: (
                    <>
                      <div className="tt-title">{shortDate(dates[i])}</div>
                      <TipRow color="#fff" label="Demand" value={int(actual[i])} />
                      {expected[i] > 0 && <TipRow color={color.quiet} label="Expected" value={int(expected[i])} />}
                      {a && <div className="tt-note">Flagged. {a.cause ? `Cause: ${a.cause}.` : "No known cause."}</div>}
                      {!a && events[i] && <div className="tt-note">{events[i]}</div>}
                    </>
                  ),
                });
              }}
              onPointerLeave={() => {
                setHover(null);
                setTip(null);
              }}
            />
          </svg>
        );
      }}
    </Plot>
  );
}
