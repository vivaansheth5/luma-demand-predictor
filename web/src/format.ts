const intFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

/** Whole number with Indian digit grouping: 12,34,567. */
export const int = (v: number): string => intFmt.format(Math.round(v));

/** Rupees, compact in lakh and crore: ₹18,498 / ₹12.3L / ₹1.18Cr. */
export function inr(v: number): string {
  const a = Math.abs(v);
  const sign = v < 0 ? "−" : "";
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)}Cr`;
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(a >= 1e6 ? 1 : 2)}L`;
  return `${sign}₹${intFmt.format(Math.round(a))}`;
}

export const pct = (v: number, digits = 0): string => `${(100 * v).toFixed(digits)}%`;

/** Signed percentage with a true minus sign: +12% / −8%. */
export function signedPct(v: number, digits = 0): string {
  const n = 100 * v;
  const body = Math.abs(n).toFixed(digits);
  if (Number(body) === 0) return `${body}%`;
  return `${n > 0 ? "+" : "−"}${body}%`;
}

export const num = (v: number, digits = 1): string =>
  Number.isInteger(v) ? intFmt.format(v) : v.toFixed(digits);

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

function parse(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

/** 15 Nov */
export function shortDate(iso: string): string {
  const d = parse(iso);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
}

/** Sunday 15 November 2026 */
export function longDate(iso: string): string {
  const d = parse(iso);
  const month = new Intl.DateTimeFormat("en-GB", { month: "long", timeZone: "UTC" }).format(d);
  return `${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${month} ${d.getUTCFullYear()}`;
}

export function addDays(iso: string, days: number): string {
  const d = parse(iso);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** First day of the week that ends on `iso`. */
export const weekStart = (iso: string): string => addDays(iso, -6);

export const titleCase = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);
