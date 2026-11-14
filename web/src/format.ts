let currency = "INR";
let intFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

const SYMBOLS: Record<string, string> = {
  INR: "₹",
  USD: "$",
  EUR: "€",
  GBP: "£",
  JPY: "¥",
  AUD: "A$",
  CAD: "C$",
  SGD: "S$",
  AED: "AED ",
};

/** Set the currency of the loaded run. Rupees group digits the Indian way and use lakh and crore. */
export function setCurrency(code: string): void {
  currency = code.toUpperCase();
  intFmt = new Intl.NumberFormat(currency === "INR" ? "en-IN" : "en-US", { maximumFractionDigits: 0 });
}

/** Whole number with the grouping of the run's locale: 1,234,567 or 12,34,567. */
export const int = (v: number): string => intFmt.format(Math.round(v));

/** Money, compact above ten thousand: $8,498 / $12.3K / $1.20M, or ₹12.3L / ₹1.18Cr for rupees. */
export function money(v: number): string {
  const a = Math.abs(v);
  const sign = v < 0 ? "−" : "";
  const sym = SYMBOLS[currency] ?? `${currency} `;
  if (currency === "INR") {
    if (a >= 1e7) return `${sign}${sym}${(a / 1e7).toFixed(2)}Cr`;
    if (a >= 1e5) return `${sign}${sym}${(a / 1e5).toFixed(a >= 1e6 ? 1 : 2)}L`;
    return `${sign}${sym}${intFmt.format(Math.round(a))}`;
  }
  if (a >= 1e9) return `${sign}${sym}${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}${sym}${(a / 1e6).toFixed(2)}M`;
  if (a >= 1e4) return `${sign}${sym}${(a / 1e3).toFixed(1)}K`;
  return `${sign}${sym}${intFmt.format(Math.round(a))}`;
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

/** A column name as a label: "product_type" reads "Product type". */
export const label = (s: string): string => titleCase(s.replace(/_/g, " "));

const WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"];

/** A count that can open a sentence: "Four forecasts", "12 forecasts". */
export const countWord = (n: number, noun: string): string => `${WORDS[n] ?? int(n)} ${noun}${n === 1 ? "" : "s"}`;

/** "1 store" or "8 stores". */
export const count = (n: number, noun: string): string => `${int(n)} ${noun}${n === 1 ? "" : "s"}`;
