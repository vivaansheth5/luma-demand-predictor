import type { ReactNode } from "react";
import { inkOn, riskColor } from "../theme";

const ICONS: Record<string, ReactNode> = {
  up: <path d="M3 9l3-4 2 2 3-4M8.5 3H11v2.5" />,
  down: <path d="M3 4l3 4 2-2 3 4M8.5 10H11V7.5" />,
  spark: <path d="M7 2v3M7 9v3M2 7h3M9 7h3M3.8 3.8l1.7 1.7M8.5 8.5l1.7 1.7" />,
  flat: <path d="M3 7h8" />,
  check: <path d="M3 7.5l2.6 2.6L11 4.4" />,
  warn: <path d="M7 3.2v4.4M7 10.2v.2" />,
  cross: <path d="M4 4l6 6M10 4l-6 6" />,
  eye: <path d="M1.8 7s2-3.6 5.2-3.6S12.2 7 12.2 7s-2 3.6-5.2 3.6S1.8 7 1.8 7zM7 8.4a1.4 1.4 0 100-2.8 1.4 1.4 0 000 2.8z" />,
};

export function Icon({ name, size = 14 }: { name: keyof typeof ICONS | string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 14 14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {ICONS[name] ?? ICONS.flat}
    </svg>
  );
}

const TREND: Record<string, { tone: string; icon: string }> = {
  Emerging: { tone: "up", icon: "spark" },
  Rising: { tone: "up", icon: "up" },
  Peaking: { tone: "warn", icon: "flat" },
  Fading: { tone: "down", icon: "down" },
  Stable: { tone: "", icon: "flat" },
};

/** Trend status: always an icon and a word, never colour alone. */
export function TrendTag({ status }: { status: string }) {
  const t = TREND[status] ?? TREND.Stable;
  return (
    <span className={`tag ${t.tone}`}>
      <Icon name={t.icon} size={12} />
      {status}
    </span>
  );
}

export function StatusTag({ status, children }: { status: "pass" | "warn" | "fail"; children: ReactNode }) {
  const tone = status === "pass" ? "good" : status === "warn" ? "warn" : "bad";
  const icon = status === "pass" ? "check" : status === "warn" ? "warn" : "cross";
  return (
    <span className={`tag ${tone}`}>
      <Icon name={icon} size={12} />
      {children}
    </span>
  );
}

/**
 * The size run: one cell per size, shaded by its stockout risk, showing units on hand.
 * A badge shows units the agent is ordering for that size.
 */
export function SizeRun({
  sizes,
  stock,
  risk,
  order,
  compact,
}: {
  sizes: string[];
  stock: number[];
  risk: number[];
  order?: number[];
  compact?: boolean;
}) {
  return (
    <span className={`sizerun${compact ? " compact" : ""}`} role="img" aria-label={sizes
      .map((z, i) => `${z}: ${stock[i]} on hand, ${Math.round(100 * risk[i])}% stockout risk`)
      .join("; ")}
    >
      {sizes.map((z, i) => {
        const fill = riskColor(risk[i]);
        return (
          <span key={z} className="sr-cell" style={{ background: fill, color: inkOn(fill) }} title={`${z}: ${stock[i]} on hand, ${Math.round(100 * risk[i])}% chance of selling out`}>
            <span className="sr-size">{z}</span>
            <span className="sr-num">{stock[i]}</span>
            {order && order[i] > 0 && <span className="sr-plus">+{order[i]}</span>}
          </span>
        );
      })}
    </span>
  );
}

export function RiskKey() {
  return (
    <span className="ramp-key">
      Low risk
      <i style={{ background: `linear-gradient(90deg, ${riskColor(0)}, ${riskColor(0.5)}, ${riskColor(1)})` }} />
      Certain to sell out
    </span>
  );
}

export function Pager({ page, pages, onPage, total, noun }: { page: number; pages: number; onPage: (p: number) => void; total: number; noun: string }) {
  if (pages <= 1) return <div className="pager"><span>{total} {noun}</span></div>;
  return (
    <div className="pager">
      <span>
        {total} {noun}. Page {page + 1} of {pages}
      </span>
      <span style={{ display: "flex", gap: 6 }}>
        <button onClick={() => onPage(page - 1)} disabled={page === 0}>
          Previous
        </button>
        <button onClick={() => onPage(page + 1)} disabled={page >= pages - 1}>
          Next
        </button>
      </span>
    </div>
  );
}
