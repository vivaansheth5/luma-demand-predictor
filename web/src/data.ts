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
}

export async function loadDashboard(): Promise<Dashboard> {
  if (window.__DASHBOARD__) return window.__DASHBOARD__;
  const response = await fetch("./data/dashboard.json");
  if (!response.ok) throw new Error(`dashboard.json returned ${response.status}`);
  return (await response.json()) as Dashboard;
}

export function buildModel(d: Dashboard): Model {
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
  };
}

/** Tool output writes rupees as "Rs"; the interface shows the rupee sign. */
export const rupees = (text: string): string => text.replace(/Rs\s?/g, "₹");

/**
 * Navigate to a view. Routes are plain hash names made of letters, digits and hyphens
 * (#plan, #forecast-KOR-OUT-018) so they survive any host that restricts URL fragments.
 */
export function go(path: string): void {
  window.location.hash = path.replace(/^\//, "").replace(/\//g, "-");
  window.scrollTo({ top: 0 });
}
