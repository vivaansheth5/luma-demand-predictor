import { useState } from "react";
import { scaleLinear } from "d3-scale";
import { area, line } from "d3-shape";
import { color } from "../theme";
import { int, shortDate, weekStart } from "../format";
import { axisNumber, Plot, TipRow, YGrid, type Tip } from "./base";

export interface ForecastFan {
  mean: number[];
  p10: number[];
  p90: number[];
  p25?: number[];
  p75?: number[];
}

/**
 * Weekly history followed by the forecast.
 * History: demand as a line, with sales as a quiet line or as bars.
 * Forecast: expected demand with its 80% interval (and the middle 50% when given).
 */
export function DemandChart({
  weeks,
  demand,
  sales,
  forecast,
  events = [],
  height = 300,
  salesAsBars = false,
  demandLabel = "Demand",
  startAt = 0,
}: {
  weeks: string[];
  demand: number[];
  sales?: number[];
  forecast: ForecastFan;
  events?: { week: number; name: string }[];
  height?: number;
  salesAsBars?: boolean;
  demandLabel?: string;
  /** First history week to draw; earlier weeks are before the product went on sale. */
  startAt?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [tip, setTip] = useState<Tip | null>(null);
  const nHist = demand.length;
  const n = weeks.length;
  const m = { top: 28, right: 58, bottom: 26, left: 46 };

  return (
    <Plot height={height} tip={tip}>
      {(width) => {
        const x = scaleLinear().domain([0, n - 1]).range([m.left + 6, width - m.right]);
        const top = Math.max(...demand, ...(sales ?? [0]), ...forecast.p90, ...forecast.mean, 1);
        const y = scaleLinear().domain([0, top * 1.08]).nice(5).range([height - m.bottom, m.top]);
        const fx = (i: number) => x(nHist + i);
        const histLine = line<number>().defined((_, i) => i >= startAt).x((_, i) => x(i)).y((d) => y(d));
        const meanPts: [number, number][] = [[x(nHist - 1), y(demand[nHist - 1])], ...forecast.mean.map((d, i): [number, number] => [fx(i), y(d)])];
        const band = (lo: number[], hi: number[]) =>
          area<number>().x((_, i) => fx(i)).y0((_, i) => y(lo[i])).y1((_, i) => y(hi[i]))(lo) ?? "";
        const step = n > 1 ? x(1) - x(0) : 20;
        const barW = Math.max(2, Math.min(10, step * 0.5));
        const divider = (x(nHist - 1) + x(nHist)) / 2;

        let lastRight = -Infinity;
        const labels = events
          .filter((e, i) => i === 0 || events[i - 1].name !== e.name || e.week - events[i - 1].week > 6)
          .map((e) => ({ ...e, cx: x(e.week), w: e.name.length * 6.1 }))
          .filter((e) => {
            const left = Math.max(m.left, e.cx - e.w / 2);
            if (left < lastRight + 10 || e.cx + e.w / 2 > width - 4) return false;
            lastRight = left + e.w;
            return true;
          });

        const monthTicks = weeks
          .map((w, i) => ({ i, d: new Date(`${w}T00:00:00Z`) }))
          .filter(({ d }) => d.getUTCDate() <= 7);

        const onMove = (e: React.PointerEvent<SVGRectElement>) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const px = e.clientX - rect.left + m.left;
          const i = Math.max(startAt, Math.min(n - 1, Math.round(x.invert(px))));
          setHover(i);
          const title = `${shortDate(weekStart(weeks[i]))} to ${shortDate(weeks[i])}`;
          const content =
            i < nHist ? (
              <>
                <div className="tt-title">{title}</div>
                <TipRow color="#fff" label={demandLabel} value={int(demand[i])} />
                {sales && <TipRow color={color.quiet} label="Sales" value={int(sales[i])} />}
                {sales && demand[i] - sales[i] >= 1 && (
                  <div className="tt-note">{int(demand[i] - sales[i])} units lost to stockouts</div>
                )}
              </>
            ) : (
              <>
                <div className="tt-title">{title}, forecast</div>
                <TipRow color="#E58DA0" label="Expected" value={int(forecast.mean[i - nHist])} />
                <div className="tt-note">
                  80% interval {int(forecast.p10[i - nHist])} to {int(forecast.p90[i - nHist])}
                </div>
              </>
            );
          setTip({ x: x(i), y: y(i < nHist ? demand[i] : forecast.mean[i - nHist]), content });
        };

        return (
          <svg width={width} height={height} role="img" aria-label="Weekly demand history and forecast">
            <YGrid scale={y} left={m.left} right={width - m.right} ticks={y.ticks(5)} format={axisNumber} />
            {monthTicks.map(({ i, d }) => (
              <text key={i} x={x(i)} y={height - 7} textAnchor="middle">
                {d.toLocaleString("en-GB", { month: "short", timeZone: "UTC" })}
              </text>
            ))}
            {labels.map((e) => (
              <g key={`${e.week}-${e.name}`}>
                <line x1={e.cx} x2={e.cx} y1={m.top - 8} y2={m.top - 2} stroke={color.axis} />
                <text className="label" x={Math.max(m.left + e.w / 2, e.cx)} y={m.top - 13} textAnchor="middle">
                  {e.name}
                </text>
              </g>
            ))}
            <line x1={divider} x2={divider} y1={m.top} y2={height - m.bottom} stroke={color.axis} />
            <text className="label" x={divider + 7} y={height - m.bottom - 8}>
              Forecast
            </text>

            {sales && salesAsBars &&
              sales.map((d, i) => i >= startAt && (
                <path
                  key={i}
                  d={roundedBar(x(i) - barW / 2, y(d), barW, y(0) - y(d), Math.min(3, barW / 2))}
                  fill={color.quiet}
                  opacity={hover === i ? 1 : 0.62}
                />
              ))}
            {sales && !salesAsBars && (
              <path d={histLine(sales) ?? ""} fill="none" stroke={color.quiet} strokeWidth={1.5} strokeLinejoin="round" />
            )}
            <path d={band(forecast.p10, forecast.p90)} fill={color.accent} opacity={0.11} />
            {forecast.p25 && forecast.p75 && <path d={band(forecast.p25, forecast.p75)} fill={color.accent} opacity={0.14} />}
            <path d={histLine(demand) ?? ""} fill="none" stroke={color.ink} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            <path
              d={line<[number, number]>().x((d) => d[0]).y((d) => d[1])(meanPts) ?? ""}
              fill="none"
              stroke={color.accent}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            {forecast.mean.map((d, i) => (
              <circle key={i} cx={fx(i)} cy={y(d)} r={i === forecast.mean.length - 1 ? 4.5 : 3} fill={color.accent} stroke="#fff" strokeWidth={2} />
            ))}
            <text className="strong" x={fx(forecast.mean.length - 1) + 9} y={y(forecast.mean[forecast.mean.length - 1])} dy="0.32em">
              {int(forecast.mean[forecast.mean.length - 1])}
            </text>
            {hover !== null && (
              <line x1={x(hover)} x2={x(hover)} y1={m.top} y2={height - m.bottom} stroke={color.ink} strokeOpacity={0.35} />
            )}
            <rect
              x={m.left}
              y={m.top}
              width={Math.max(0, width - m.left - m.right + 12)}
              height={height - m.top - m.bottom}
              fill="transparent"
              onPointerMove={onMove}
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

/** A column with rounded data-end and a square baseline. */
export function roundedBar(x: number, y: number, w: number, h: number, r: number): string {
  if (h <= 0) return "";
  const rr = Math.min(r, h, w / 2);
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

/** A horizontal bar with rounded data-end and a square baseline at x. */
export function roundedBarH(x: number, y: number, w: number, h: number, r: number): string {
  if (w === 0) return "";
  const dir = w > 0 ? 1 : -1;
  const rr = Math.min(r, Math.abs(w), h / 2);
  const end = x + w;
  return `M${x},${y}H${end - dir * rr}Q${end},${y} ${end},${y + rr}V${y + h - rr}Q${end},${y + h} ${end - dir * rr},${y + h}H${x}Z`;
}
