import { setCurrency } from "./format";
import type { Dashboard, Pair } from "./types";

declare global {
  interface Window {
    __DASHBOARD__?: Dashboard;
  }
}

/** The dashboard document, with lookups the views share. */
export interface Model {
  d: Dashboard;
  pair: (s: number, k: number) => Pair | undefined;
  storeIndex: (idOrName: string) => number;
  skuIndex: (id: string) => number;
  /** Week-ending dates of the history weeks, then the four forecast weeks. */
  weeks: string[];
  /** What this run's data supports. */
  has: { stock: boolean; stockHistory: boolean; map: boolean; buzz: boolean; transfers: boolean; truth: boolean; stores: boolean };
  /** The catalog column that trends are read on, such as "style"; null when the catalog has no attributes. */
  trendBy: string | null;
}

export async function loadDashboard(): Promise<Dashboard> {
  if (window.__DASHBOARD__) return window.__DASHBOARD__;
  const response = await fetch("./data/dashboard.json");
  if (!response.ok) throw new Error(`dashboard.json returned ${response.status}`);
  return (await response.json()) as Dashboard;
}

export function buildModel(d: Dashboard): Model {
  setCurrency(d.meta.currency);
  const pairs = new Map<number, Pair>();
  d.pairs.forEach((p) => pairs.set(p.s * 10000 + p.k, p));
  const stores = new Map<string, number>();
  d.stores.forEach((s, i) => {
    stores.set(s.id.toLowerCase(), i);
    stores.set(s.name.toLowerCase(), i);
  });
  const skus = new Map<string, number>();
  d.skus.forEach((s, i) => skus.set(s.id.toLowerCase(), i));
  return {
    d,
    pair: (s, k) => pairs.get(s * 10000 + k),
    storeIndex: (v) => stores.get(v.toLowerCase()) ?? -1,
    skuIndex: (v) => skus.get(v.toLowerCase()) ?? -1,
    weeks: d.network.weeks,
    has: {
      stock: d.meta.hasStock,
      stockHistory: d.meta.hasStockHistory,
      map: d.meta.hasMap,
      buzz: d.meta.hasBuzz,
      transfers: d.meta.canTransfer,
      truth: d.meta.simulated,
      stores: d.stores.length > 1,
    },
    trendBy: d.meta.trendAttribute,
  };
}

/**
 * Navigate to a view. Routes are plain hash names made of letters, digits and hyphens
 * (#plan, #forecast-2-17, where the numbers are the store and product positions) so they survive any host that restricts URL fragments.
 */
export function go(path: string): void {
  window.location.hash = path.replace(/^\//, "").replace(/\//g, "-");
  window.scrollTo({ top: 0 });
}

/** Open the forecast of one product in one store. */
export const openPair = (s: number, k: number): void => go(`/forecast/${s}/${k}`);
