import { useState } from "react";
import { color, riskColor } from "../theme";
import { money, int, signedPct } from "../format";
import type { Lane, Store } from "../types";
import { Plot, TipRow, type Tip } from "./base";

/**
 * The store network drawn to scale from coordinates.
 * Circle area is forecast demand, shade is the number of sizes likely to sell out this week,
 * and each arrow is a transfer lane in the plan.
 */
type Placed = Store & { lat: number; lon: number };

export function StoreMap({ stores: all, lanes: allLanes, height: maxHeight = 380, maxLanes }: { stores: Store[]; lanes: Lane[]; height?: number; maxLanes?: number }) {
  const stores = all.filter((s): s is Placed => s.lat !== null && s.lon !== null);
  const lanes = maxLanes ? [...allLanes].sort((a, b) => b.units - a.units).slice(0, maxLanes) : allLanes;
  const [tip, setTip] = useState<Tip | null>(null);
  const [focus, setFocus] = useState<string | null>(null);

  return (
    <Plot height={maxHeight} aspect={0.86} tip={tip}>
      {(width, height) => {
        const compact = width < 480;
        const side = compact ? 58 : 84;
        const pad = { top: 30, right: side, bottom: 46, left: side };
        const lat0 = stores.reduce((a, s) => a + s.lat, 0) / stores.length;
        const kx = Math.cos((lat0 * Math.PI) / 180);
        const xs = stores.map((s) => s.lon * kx);
        const ys = stores.map((s) => s.lat);
        const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
        const scale = Math.min(
          (width - pad.left - pad.right) / Math.max(x1 - x0, 1e-6),
          (height - pad.top - pad.bottom) / Math.max(y1 - y0, 1e-6),
        );
        const ox = pad.left + (width - pad.left - pad.right - (x1 - x0) * scale) / 2;
        const oy = pad.top + (height - pad.top - pad.bottom - (y1 - y0) * scale) / 2;
        const px = (s: Placed) => ox + (s.lon * kx - x0) * scale;
        const py = (s: Placed) => oy + (y1 - s.lat) * scale;
        const maxDemand = Math.max(...stores.map((s) => s.forecast28));
        const maxRisk = Math.max(...stores.map((s) => s.riskCells ?? 0), 1);
        const radius = (s: Placed) => (compact ? 0.6 : 1) * (7 + 15 * Math.sqrt(s.forecast28 / maxDemand));
        const byName = new Map(stores.map((s) => [s.name, s]));
        const maxUnits = Math.max(...lanes.map((l) => l.units), 1);
        const kmPx = (scale / 111.32) * 5; // five kilometres in pixels

        // Place each label where it overlaps circles and earlier labels least.
        type Box = [number, number, number, number];
        type Spot = { x: number; y: number; anchor: "start" | "end" | "middle"; box: Box };
        const overlap = (a: Box, b: Box) => Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0])) * Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
        const circles: Box[] = stores.map((s) => [px(s) - radius(s) - 3, py(s) - radius(s) - 3, px(s) + radius(s) + 3, py(s) + radius(s) + 3]);
        const taken: Box[] = [];
        const labelAt = new Map<string, Spot>();
        const crowding = (s: Placed) => stores.filter((o) => Math.hypot(px(o) - px(s), py(o) - py(s)) < 110).length;
        [...stores]
          .sort((a, b) => crowding(b) - crowding(a))
          .forEach((s) => {
            const [cx, cy, r] = [px(s), py(s), radius(s)];
            const w = Math.max(s.name.length * 7.1, compact ? 30 : 62);
            const lh = compact ? 14 : 30; // label height: one line on narrow screens
            const side = (dir: 1 | -1, dy: number): Spot => ({
              x: cx + dir * (r + 8),
              y: cy - 2 + dy,
              anchor: dir === 1 ? "start" : "end",
              box: dir === 1 ? [cx + r + 6, cy - 14 + dy, cx + r + 8 + w, cy - 14 + lh + dy] : [cx - r - 8 - w, cy - 14 + dy, cx - r - 6, cy - 14 + lh + dy],
            });
            const stack = (up: boolean, shift: number): Spot => {
              const y = up ? cy - r - 20 : cy + r + 15;
              const x = cx + shift;
              return { x, y: up && compact ? y + 14 : y, anchor: "middle", box: [x - w / 2, y - 12, x + w / 2, y - 12 + lh] };
            };
            const options: Spot[] = [
              side(1, 0), side(-1, 0), stack(false, 0), stack(true, 0),
              stack(true, -w / 2), stack(true, w / 2), stack(false, -w / 2), stack(false, w / 2),
              side(1, -r), side(-1, -r), side(1, r), side(-1, r),
            ];
            const me = stores.indexOf(s);
            const cost = (o: Spot) =>
              circles.reduce((a, c, i) => a + (i === me ? 0 : overlap(o.box, c)), 0) +
              taken.reduce((a, t) => a + overlap(o.box, t), 0) +
              (o.box[0] < 2 || o.box[2] > width - 2 || o.box[1] < 2 || o.box[3] > height - 22 ? 1e6 : 0);
            const best = options.reduce((a, b) => (cost(b) < cost(a) - 1e-9 ? b : a));
            taken.push(best.box);
            labelAt.set(s.id, best);
          });

        return (
          <svg width={width} height={height} role="img" aria-label="Map of stores with the plan's transfer lanes">
            <defs>
              <marker id="arrow" viewBox="0 0 8 8" refX="6.5" refY="4" markerWidth="5.5" markerHeight="5.5" orient="auto-start-reverse">
                <path d="M0.5,0.8L7,4L0.5,7.2" fill="none" stroke={color.accent} strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
              </marker>
            </defs>
            {lanes.map((lane, i) => {
              const a = byName.get(lane.from);
              const b = byName.get(lane.to);
              if (!a || !b) return null;
              const [ax, ay, bx, by] = [px(a), py(a), px(b), py(b)];
              const dx = bx - ax;
              const dy = by - ay;
              const len = Math.hypot(dx, dy) || 1;
              const bend = Math.min(46, len * 0.22);
              const cx = (ax + bx) / 2 - (dy / len) * bend;
              const cy = (ay + by) / 2 + (dx / len) * bend;
              // Stop the stroke at the edge of each circle.
              const sx = ax + ((cx - ax) / Math.hypot(cx - ax, cy - ay)) * (radius(a) + 3);
              const sy = ay + ((cy - ay) / Math.hypot(cx - ax, cy - ay)) * (radius(a) + 3);
              const ex = bx + ((cx - bx) / Math.hypot(cx - bx, cy - by)) * (radius(b) + 5);
              const ey = by + ((cy - by) / Math.hypot(cx - bx, cy - by)) * (radius(b) + 5);
              const dim = focus !== null && focus !== lane.from && focus !== lane.to;
              const d = `M${sx},${sy}Q${cx},${cy} ${ex},${ey}`;
              return (
                <g key={i} opacity={dim ? 0.12 : 1}>
                  <path
                    className="arc-draw"
                    pathLength={1}
                    d={d}
                    fill="none"
                    stroke={color.accent}
                    strokeOpacity={0.5}
                    strokeWidth={0.9 + 1.9 * (lane.units / maxUnits)}
                    strokeLinecap="round"
                    markerEnd="url(#arrow)"
                    style={{ animationDelay: `${200 + i * 45}ms` }}
                  />
                  <path
                    d={d}
                    fill="none"
                    stroke="transparent"
                    strokeWidth={14}
                    onPointerMove={(e) => {
                      const r = e.currentTarget.ownerSVGElement!.getBoundingClientRect();
                      setTip({
                        x: e.clientX - r.left,
                        y: e.clientY - r.top,
                        content: (
                          <>
                            <div className="tt-title">
                              {lane.from} to {lane.to}
                            </div>
                            <TipRow label="Units" value={int(lane.units)} />
                            <TipRow label="Distance" value={`${lane.km.toFixed(1)} km`} />
                            <TipRow label="Cost" value={money(lane.cost)} />
                          </>
                        ),
                      });
                    }}
                    onPointerLeave={() => setTip(null)}
                  />
                </g>
              );
            })}
            {stores.map((s) => {
              const r = radius(s);
              const cx = px(s);
              const cy = py(s);
              const at = labelAt.get(s.id)!;
              const show = () => {
                setFocus(s.name);
                setTip({
                  x: cx,
                  y: cy,
                  content: (
                    <>
                      <div className="tt-title">
                        {s.name}, open {s.weeksOpen} weeks
                      </div>
                      <TipRow label="Forecast, next 28 days" value={`${int(s.forecast28)} units`} />
                      <TipRow label="Against last 28 days" value={signedPct(s.forecast28 / Math.max(s.demand28, 1) - 1)} />
                      {s.stockUnits !== null && <TipRow label="Stock on hand" value={`${int(s.stockUnits)} units`} />}
                      {s.riskCells !== null && <TipRow label="Sizes likely to sell out" value={int(s.riskCells)} />}
                    </>
                  ),
                });
              };
              const hide = () => {
                setFocus(null);
                setTip(null);
              };
              return (
                <g key={s.id} tabIndex={0} onPointerEnter={show} onPointerLeave={hide} onFocus={show} onBlur={hide} style={{ outline: "none" }}>
                  <circle cx={cx} cy={cy} r={Math.max(r + 6, 14)} fill="transparent" />
                  <circle cx={cx} cy={cy} r={r} fill={riskColor(0.12 + 0.88 * ((s.riskCells ?? 0) / maxRisk))} stroke="#fff" strokeWidth={2} />
                  {focus === s.name && <circle cx={cx} cy={cy} r={r + 3} fill="none" stroke={color.ink} strokeWidth={1.5} />}
                  <text className="strong halo" x={at.x} y={at.y} textAnchor={at.anchor}>
                    {s.name}
                  </text>
                  {!compact && (
                    <text className="halo" x={at.x} y={at.y + 14} textAnchor={at.anchor}>
                      {int(s.forecast28)} units
                    </text>
                  )}
                </g>
              );
            })}
            <g transform={`translate(${16},${height - 14})`}>
              <line x1={0} x2={kmPx} y1={0} y2={0} stroke={color.muted} />
              <line x1={0} x2={0} y1={-3} y2={3} stroke={color.muted} />
              <line x1={kmPx} x2={kmPx} y1={-3} y2={3} stroke={color.muted} />
              <text x={kmPx + 7} dy="0.32em">
                5 km
              </text>
            </g>
          </svg>
        );
      }}
    </Plot>
  );
}
