import { useState } from "react";
import { scaleLinear } from "d3-scale";
import { color } from "../theme";
import { signedPct } from "../format";
import { Plot, type Tip } from "./base";
import { roundedBarH } from "./DemandChart";

/**
 * What moves a forecast away from the run-rate, one bar per driver.
 * Polarity is the message, so the bars diverge from zero: indigo lifts, ember lowers.
 */
export function DriverBars({ names, factors }: { names: string[]; factors: number[] }) {
  const rows = names
    .map((name, i) => ({ name, effect: factors[i] - 1 }))
    .filter((r) => r.name !== "Forecast window")
    .sort((a, b) => Math.abs(b.effect) - Math.abs(a.effect));
  const rowH = 30;
  const height = rows.length * rowH + 8;
  const [tip, setTip] = useState<Tip | null>(null);
  return (
    <Plot height={height} tip={tip}>
      {(width) => {
        const left = 158;
        const pad = 46;
        const max = Math.max(0.1, ...rows.map((r) => Math.abs(r.effect)));
        const x = scaleLinear().domain([-max, max]).range([left + pad, width - pad]);
        return (
          <svg width={width} height={height} role="img" aria-label="Forecast drivers">
            <line x1={x(0)} x2={x(0)} y1={0} y2={height} stroke={color.axis} />
            {rows.map((r, i) => {
              const y = i * rowH + 6;
              const w = x(r.effect) - x(0);
              const small = Math.abs(r.effect) < 0.005;
              return (
                <g
                  key={r.name}
                  onPointerMove={() =>
                    setTip({
                      x: x(r.effect),
                      y: y + 4,
                      content: (
                        <>
                          <div className="tt-title">{r.name}</div>
                          <div>
                            {small ? "No measurable effect on this forecast." : `${r.effect > 0 ? "Raises" : "Lowers"} the 28-day forecast by ${Math.abs(100 * r.effect).toFixed(0)}%.`}
                          </div>
                        </>
                      ),
                    })
                  }
                  onPointerLeave={() => setTip(null)}
                >
                  <rect x={0} y={y - 4} width={width} height={rowH} fill="transparent" />
                  <text className="label" x={left} y={y + 9} dy="0.32em" textAnchor="end">
                    {r.name}
                  </text>
                  {!small && <path d={roundedBarH(x(0), y, w, 18, 4)} fill={r.effect > 0 ? color.accent : color.counter} />}
                  <text className="strong" x={x(r.effect) + (r.effect >= 0 ? 7 : -7)} y={y + 9} dy="0.32em" textAnchor={r.effect >= 0 ? "start" : "end"}>
                    {small ? "0%" : signedPct(r.effect)}
                  </text>
                </g>
              );
            })}
          </svg>
        );
      }}
    </Plot>
  );
}

export interface BarItem {
  label: string;
  value: number;
  emphasis?: boolean;
  note?: string;
}

/**
 * Ranked horizontal bars with one item emphasised and an optional reference line.
 * Every bar is the same measure, so the others stay grey and only the subject takes colour.
 */
export function RankBars({
  items,
  format,
  reference,
  max,
}: {
  items: BarItem[];
  format: (v: number) => string;
  reference?: { value: number; label: string };
  max?: number;
}) {
  const rowH = 34;
  const top = reference ? 24 : 4;
  const height = items.length * rowH + top + 4;
  const [tip, setTip] = useState<Tip | null>(null);
  return (
    <Plot height={height} tip={tip}>
      {(width) => {
        const left = Math.min(190, Math.max(120, width * 0.34));
        const hi = max ?? Math.max(...items.map((d) => d.value), reference?.value ?? 0) * 1.02;
        const x = scaleLinear().domain([0, hi]).range([left, width - 56]);
        return (
          <svg width={width} height={height} role="img">
            {items.map((d, i) => {
              const y = top + i * rowH + 7;
              return (
                <g
                  key={d.label}
                  onPointerMove={() =>
                    setTip({
                      x: x(d.value),
                      y,
                      content: (
                        <>
                          <div className="tt-title">{d.label}</div>
                          <div>
                            <b>{format(d.value)}</b>
                          </div>
                          {d.note && <div className="tt-note">{d.note}</div>}
                        </>
                      ),
                    })
                  }
                  onPointerLeave={() => setTip(null)}
                >
                  <rect x={0} y={y - 6} width={width} height={rowH} fill="transparent" />
                  <text className={d.emphasis ? "strong" : "label"} x={left - 10} y={y + 9} dy="0.32em" textAnchor="end">
                    {d.label}
                  </text>
                  <path d={roundedBarH(x(0), y, x(d.value) - x(0), 18, 4)} fill={d.emphasis ? color.accent : color.quiet} />
                  <text className={d.emphasis ? "strong" : "label"} x={x(d.value) + 7} y={y + 9} dy="0.32em">
                    {format(d.value)}
                  </text>
                </g>
              );
            })}
            <line x1={x(0)} x2={x(0)} y1={top} y2={height - 2} stroke={color.axis} />
            {reference && (
              <g>
                <line x1={x(reference.value)} x2={x(reference.value)} y1={top - 4} y2={height - 2} stroke={color.ink} strokeWidth={1.5} />
                <text className="label" x={x(reference.value)} y={11} textAnchor="middle">
                  {reference.label}
                </text>
              </g>
            )}
          </svg>
        );
      }}
    </Plot>
  );
}
