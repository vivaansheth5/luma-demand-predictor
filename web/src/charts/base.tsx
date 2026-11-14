import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import type { ScaleLinear } from "d3-scale";

/** Width of an element, kept current with ResizeObserver. */
export function useWidth(): [RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => setWidth(Math.floor(el.getBoundingClientRect().width));
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

export interface Tip {
  x: number;
  y: number;
  content: ReactNode;
}

/**
 * A responsive plot area. Children receive the measured width and the height to draw at;
 * the tooltip floats above. With `aspect`, height shrinks on narrow screens (width x aspect).
 */
export function Plot({
  height,
  aspect,
  tip,
  children,
}: {
  height: number;
  aspect?: number;
  tip?: Tip | null;
  children: (width: number, height: number) => ReactNode;
}) {
  const [ref, width] = useWidth();
  const h = aspect && width > 0 ? Math.min(height, Math.round(width * aspect)) : height;
  const flip = tip ? tip.x > width - 250 : false;
  return (
    <div className="plot" ref={ref} style={{ minHeight: h }}>
      {width > 0 && children(width, h)}
      {tip && (
        <div
          className="tooltip"
          role="status"
          style={{
            left: flip ? undefined : tip.x + 14,
            right: flip ? width - tip.x + 14 : undefined,
            top: Math.max(0, tip.y - 12),
          }}
        >
          {tip.content}
        </div>
      )}
    </div>
  );
}

export function TipRow({ color, label, value }: { color?: string; label: string; value: string }) {
  return (
    <div className="tt-row">
      <span>
        {color && <i className="key key-line" style={{ borderColor: color }} />}
        {label}
      </span>
      <b>{value}</b>
    </div>
  );
}

export interface TableData {
  columns: string[];
  rows: (string | number)[][];
}

/** A chart on its own white surface, with a title, a legend and a table view of the same data. */
export function Sheet({
  title,
  subtitle,
  legend,
  table,
  foot,
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  legend?: ReactNode;
  table?: TableData;
  foot?: ReactNode;
  children: ReactNode;
}) {
  const [asTable, setAsTable] = useState(false);
  return (
    <section className="sheet">
      <div className="sheet-head">
        <div>
          <h3>{title}</h3>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {table && (
          <button className="linkish" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}>
            {asTable ? "View as chart" : "View as table"}
          </button>
        )}
      </div>
      {asTable && table ? (
        <div className="table-wrap" style={{ maxHeight: 360, overflowY: "auto" }}>
          <table className="data">
            <thead>
              <tr>
                {table.columns.map((c, i) => (
                  <th key={c} className={i === 0 ? undefined : "r"}>
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => (
                    <td key={j} className={j === 0 ? undefined : "r"}>
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <>
          {legend && <div className="legend">{legend}</div>}
          {children}
        </>
      )}
      {foot && <div className="sheet-foot">{foot}</div>}
    </section>
  );
}

export function Key({ kind, color: c, children }: { kind: "line" | "rect" | "dot"; color: string; children: ReactNode }) {
  const style = kind === "line" ? { borderColor: c } : { background: c };
  return (
    <span>
      <i className={`key key-${kind}`} style={style} />
      {children}
    </span>
  );
}

/** Horizontal gridlines with value labels on the left. */
export function YGrid({
  scale,
  left,
  right,
  ticks,
  format,
}: {
  scale: ScaleLinear<number, number>;
  left: number;
  right: number;
  ticks: number[];
  format: (v: number) => string;
}) {
  return (
    <g>
      {ticks.map((t) => (
        <g key={t} transform={`translate(0,${scale(t)})`}>
          <line x1={left} x2={right} stroke={t === 0 ? "#CFC7CA" : "#ECE7E9"} />
          <text x={left - 8} dy="0.32em" textAnchor="end">
            {format(t)}
          </text>
        </g>
      ))}
    </g>
  );
}

/** Compact axis number: 0 / 500 / 1,500 / 12K. */
export function axisNumber(v: number): string {
  if (Math.abs(v) >= 10000) return `${Math.round(v / 1000)}K`;
  return new Intl.NumberFormat("en-IN").format(v);
}
