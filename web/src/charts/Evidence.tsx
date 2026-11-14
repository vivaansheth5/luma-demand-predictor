import { useState } from "react";
import { scaleLinear } from "d3-scale";
import { line } from "d3-shape";
import { color } from "../theme";
import { money, int, pct } from "../format";
import type { PolicyResult } from "../types";
import { axisNumber, Plot, TipRow, YGrid, type Tip } from "./base";
import { roundedBar } from "./DemandChart";

/**
 * Service against inventory for both policies.
 * Each point is one setting of a policy's dial, run through the same 28 hidden days.
 * A better policy is a curve further up and to the left.
 */
export function Frontier({ results, height = 380 }: { results: PolicyResult[]; height?: number }) {
  const [tip, setTip] = useState<Tip | null>(null);
  const baseline = results.filter((r) => r.kind === "baseline").sort((a, b) => a.avg_stock_value - b.avg_stock_value);
  const sweep = results.filter((r) => r.kind === "agent_orders").sort((a, b) => a.avg_stock_value - b.avg_stock_value);
  const agent = results.find((r) => r.kind === "agent");
  const all = [...baseline, ...sweep, ...(agent ? [agent] : [])];
  const m = { top: 18, right: 28, bottom: 42, left: 50 };
  return (
    <Plot height={height} tip={tip}>
      {(width) => {
        const xs = all.map((r) => r.avg_stock_value / 1e5);
        const ys = all.map((r) => r.fill_rate);
        const x = scaleLinear().domain([Math.min(...xs) * 0.96, Math.max(...xs) * 1.02]).nice(6).range([m.left, width - m.right]);
        const y = scaleLinear().domain([Math.min(...ys) - 0.012, 1]).nice(5).range([height - m.bottom, m.top]);
        const path = line<PolicyResult>().x((r) => x(r.avg_stock_value / 1e5)).y((r) => y(r.fill_rate));
        const point = (r: PolicyResult, c: string, big = false) => {
          const cx = x(r.avg_stock_value / 1e5);
          const cy = y(r.fill_rate);
          return (
            <g
              key={r.name}
              onPointerEnter={() =>
                setTip({
                  x: cx,
                  y: cy,
                  content: (
                    <>
                      <div className="tt-title">{r.name}</div>
                      <TipRow label="Fill rate" value={pct(r.fill_rate, 1)} />
                      <TipRow label="Average stock" value={money(r.avg_stock_value)} />
                      <TipRow label="Lost revenue" value={money(r.lost_revenue)} />
                    </>
                  ),
                })
              }
              onPointerLeave={() => setTip(null)}
            >
              <circle cx={cx} cy={cy} r={14} fill="transparent" />
              <circle cx={cx} cy={cy} r={big ? 7 : 4.5} fill={c} stroke="#fff" strokeWidth={2} />
            </g>
          );
        };
        const today = baseline.find((r) => r.name === "Status quo");
        return (
          <svg width={width} height={height} role="img" aria-label="Fill rate against average stock value for each policy">
            <YGrid scale={y} left={m.left} right={width - m.right} ticks={y.ticks(5)} format={(v) => pct(v)} />
            {x.ticks(6).map((t) => (
              <text key={t} x={x(t)} y={height - m.bottom + 16} textAnchor="middle">
                ₹{t}L
              </text>
            ))}
            <text className="label" x={(m.left + width - m.right) / 2} y={height - 6} textAnchor="middle">
              Average stock held, at cost
            </text>
            <path d={path(baseline) ?? ""} fill="none" stroke={color.quiet} strokeWidth={2} strokeLinejoin="round" />
            <path d={path(sweep) ?? ""} fill="none" stroke={color.accent} strokeWidth={2} strokeLinejoin="round" />
            {baseline.map((r) => point(r, color.quiet))}
            {sweep.map((r) => point(r, color.accent))}
            {agent && point(agent, color.brand, true)}
            {today && (
              <text className="label" x={x(today.avg_stock_value / 1e5) + 9} y={y(today.fill_rate) + 15}>
                Status quo today ({today.dial})
              </text>
            )}
            {baseline.length > 0 && (
              <text className="label" x={x(baseline[baseline.length - 1].avg_stock_value / 1e5)} y={y(baseline[baseline.length - 1].fill_rate) - 12} textAnchor="end">
                {baseline[baseline.length - 1].dial} of cover
              </text>
            )}
            {agent &&
              (x(agent.avg_stock_value / 1e5) > m.left + 150 ? (
                <text className="strong halo" x={x(agent.avg_stock_value / 1e5) - 12} y={y(agent.fill_rate) - 10} textAnchor="end">
                  Luma with transfers
                </text>
              ) : (
                <text className="strong halo" x={x(agent.avg_stock_value / 1e5) - 4} y={y(agent.fill_rate) - 14}>
                  Luma with transfers
                </text>
              ))}
          </svg>
        );
      }}
    </Plot>
  );
}

/** Stated quantile against the share of outcomes that fell at or below it. */
export function Calibration({ nominal, raw, calibrated, height = 300 }: { nominal: number[]; raw: number[]; calibrated: number[]; height?: number }) {
  const [tip, setTip] = useState<Tip | null>(null);
  const m = { top: 14, right: 20, bottom: 40, left: 46 };
  return (
    <Plot height={height} tip={tip}>
      {(width) => {
        const x = scaleLinear().domain([0, 1]).range([m.left, width - m.right]);
        const y = scaleLinear().domain([0, 1]).range([height - m.bottom, m.top]);
        const path = (vals: number[]) => line<number>().x((_, i) => x(nominal[i])).y((d) => y(d))(vals) ?? "";
        const dots = (vals: number[], c: string, name: string) =>
          vals.map((v, i) => (
            <g
              key={`${name}${i}`}
              onPointerEnter={() =>
                setTip({
                  x: x(nominal[i]),
                  y: y(v),
                  content: (
                    <>
                      <div className="tt-title">
                        P{Math.round(nominal[i] * 100)}, {name}
                      </div>
                      <TipRow label="Outcomes at or below" value={pct(v, 1)} />
                      <TipRow label="Target" value={pct(nominal[i])} />
                    </>
                  ),
                })
              }
              onPointerLeave={() => setTip(null)}
            >
              <circle cx={x(nominal[i])} cy={y(v)} r={12} fill="transparent" />
              <circle cx={x(nominal[i])} cy={y(v)} r={4.5} fill={c} stroke="#fff" strokeWidth={2} />
            </g>
          ));
        return (
          <svg width={width} height={height} role="img" aria-label="Quantile calibration">
            <YGrid scale={y} left={m.left} right={width - m.right} ticks={[0, 0.25, 0.5, 0.75, 1]} format={(v) => pct(v)} />
            {nominal.map((q, i) => {
              const tight = i > 0 && x(q) - x(nominal[i - 1]) < 30;
              const tightNext = i < nominal.length - 1 && x(nominal[i + 1]) - x(q) < 30;
              return (
                <text key={q} x={x(q)} y={height - m.bottom + 16} textAnchor={tight ? "start" : tightNext ? "end" : "middle"} dx={tight ? -4 : tightNext ? 4 : 0}>
                  P{Math.round(q * 100)}
                </text>
              );
            })}
            <text className="label" x={(m.left + width - m.right) / 2} y={height - 5} textAnchor="middle">
              Quantile the model states
            </text>
            <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} stroke={color.ink} strokeWidth={1} />
            <text className="label" x={x(0.62)} y={y(0.62) + 18} transform={`rotate(${(-Math.atan2(y(0) - y(1), x(1) - x(0)) * 180) / Math.PI},${x(0.62)},${y(0.62) + 18})`}>
              Perfect calibration
            </text>
            <path d={path(raw)} fill="none" stroke={color.quiet} strokeWidth={2} />
            <path d={path(calibrated)} fill="none" stroke={color.accent} strokeWidth={2} />
            {dots(raw, color.quiet, "before calibration")}
            {dots(calibrated, color.accent, "after calibration")}
          </svg>
        );
      }}
    </Plot>
  );
}

/** Weekly demand hidden by stockouts: the simulator's truth as bars, the estimate as a line. */
export function LostDemand({ truth, sales, estimate, weeks, height = 280 }: { truth: number[]; sales: number[]; estimate: number[]; weeks: string[]; height?: number }) {
  const [tip, setTip] = useState<Tip | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const lost = truth.map((t, i) => Math.max(t - sales[i], 0));
  const found = estimate.map((e, i) => Math.max(e - sales[i], 0));
  const m = { top: 12, right: 16, bottom: 26, left: 46 };
  const n = lost.length;
  return (
    <Plot height={height} tip={tip}>
      {(width) => {
        const x = scaleLinear().domain([0, n - 1]).range([m.left + 8, width - m.right - 4]);
        const y = scaleLinear().domain([0, Math.max(...lost, ...found) * 1.08]).nice(4).range([height - m.bottom, m.top]);
        const bw = Math.max(2, Math.min(9, (x(1) - x(0)) * 0.56));
        const months = weeks.map((w, i) => ({ i, d: new Date(`${w}T00:00:00Z`) })).filter(({ d }) => d.getUTCDate() <= 7 && d.getUTCMonth() % 2 === 0);
        return (
          <svg width={width} height={height} role="img" aria-label="Weekly demand lost to stockouts, true and estimated">
            <YGrid scale={y} left={m.left} right={width - m.right} ticks={y.ticks(4)} format={axisNumber} />
            {months.map(({ i, d }) => (
              <text key={i} x={x(i)} y={height - 7} textAnchor="middle">
                {d.toLocaleString("en-GB", { month: "short", year: "2-digit", timeZone: "UTC" })}
              </text>
            ))}
            {lost.map((d, i) => (
              <path key={i} d={roundedBar(x(i) - bw / 2, y(d), bw, y(0) - y(d), 3)} fill={color.quiet} opacity={hover === i ? 1 : 0.7} />
            ))}
            <path d={line<number>().x((_, i) => x(i)).y((d) => y(d))(found) ?? ""} fill="none" stroke={color.accent} strokeWidth={2} strokeLinejoin="round" />
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
                setHover(i);
                setTip({
                  x: x(i),
                  y: y(Math.max(lost[i], found[i])),
                  content: (
                    <>
                      <div className="tt-title">Week ending {weeks[i]}</div>
                      <TipRow color={color.quiet} label="Truly lost" value={`${int(lost[i])} units`} />
                      <TipRow color="#A5A0EC" label="Estimated" value={`${int(found[i])} units`} />
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
