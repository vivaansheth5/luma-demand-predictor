/**
 * The agent's tools, run in the browser against the loaded analysis.
 *
 * Claude decides which tool to call (the definitions live in api/chat.ts); the browser runs it
 * here and sends the result back. Results are small, plain JSON with rounded numbers and
 * product names, so the model can quote them directly.
 */
import type { Model } from "../data";
import { money } from "../format";
import type { Pair, Sku } from "../types";

type Args = Record<string, unknown>;
type Result = Record<string, unknown>;

const r0 = (v: number) => Math.round(v);
const r1 = (v: number) => Math.round(v * 10) / 10;
const pctChange = (next: number, last: number) => (last >= 1 ? r0((100 * (next - last)) / last) : null);
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
const str = (v: unknown) => (typeof v === "string" ? v.trim() : "");
const lim = (v: unknown, fallback: number, max: number) =>
  Math.max(1, Math.min(max, Number.isFinite(Number(v)) && Number(v) > 0 ? Math.round(Number(v)) : fallback));

const STATUS_MEANING: Record<string, string> = {
  Emerging: "outside interest is rising and sales have only started to follow; an early signal",
  Rising: "steadily gaining share of demand",
  Peaking: "still selling well, but interest is turning down",
  Fading: "losing share of demand",
  Stable: "not moving beyond normal ups and downs",
};

function confidence(q: number): string {
  if (q < 0.01) return "very sure";
  if (q < 0.05) return "sure";
  if (q <= 0.1) return "fairly sure";
  return "not sure yet";
}

const NO_STOCK =
  "No stock levels were uploaded, so Luma cannot tell what will sell out or what to reorder. A stock file with units on hand by product and size adds this.";

/** Last 28 days and next 28 days for one product in one store. */
function pairTotals(p: Pair) {
  return { last: sum(p.demand.slice(-4)), next: sum(p.mean) };
}

function sizeMap(sizes: string[], values: number[] | undefined, digits = 0): Record<string, number> | undefined {
  if (!values) return undefined;
  return Object.fromEntries(sizes.map((z, i) => [z, digits ? r1(values[i]) : r0(values[i])]));
}

function percentBySize(sizes: string[], values: number[] | undefined): Record<string, number> | undefined {
  if (!values) return undefined;
  return Object.fromEntries(sizes.map((z, i) => [z, r0(100 * values[i])]));
}

export class Tools {
  constructor(private m: Model) {}

  private get d() {
    return this.m.d;
  }

  private store(text: unknown): number {
    const q = str(text).toLowerCase();
    if (!q) return -1;
    const exact = this.m.storeIndex(q);
    if (exact >= 0) return exact;
    return this.d.stores.findIndex((s) => s.name.toLowerCase().includes(q) || q.includes(s.name.toLowerCase()));
  }

  private category(text: unknown): string | null {
    const q = str(text).toLowerCase();
    if (!q) return null;
    const hit = this.d.meta.categories.find(
      (c) => c.key.toLowerCase() === q || c.label.toLowerCase() === q || c.label.toLowerCase().includes(q) || q.includes(c.label.toLowerCase()),
    );
    return hit?.key ?? null;
  }

  private categoryLabel(key: string): string {
    return this.d.meta.categories.find((c) => c.key === key)?.label ?? key;
  }

  /** Products ranked by how well they match free text: an ID, a name, or words from a name. */
  private match(text: string): { k: number; score: number }[] {
    const q = text.toLowerCase().trim();
    const words = q.split(/[^a-z0-9]+/).filter((w) => w.length > 1);
    return this.d.skus
      .map((s, k) => {
        const name = s.name.toLowerCase();
        const hay = `${name} ${s.id.toLowerCase()} ${Object.values(s.attributes).join(" ").toLowerCase()}`;
        let score = 0;
        if (s.id.toLowerCase() === q) score = 100;
        else if (name === q) score = 95;
        else if (name.includes(q)) score = 80;
        else if (words.length) {
          const found = words.filter((w) => hay.includes(w)).length;
          score = found === words.length ? 60 + found : (40 * found) / words.length;
        }
        return { k, score };
      })
      .filter((x) => x.score >= 20)
      .sort((a, b) => b.score - a.score || this.d.skus[b.k].forecast28 - this.d.skus[a.k].forecast28);
  }

  private pairsOf(k: number, s = -1): Pair[] {
    return this.d.pairs.filter((p) => p.k === k && (s < 0 || p.s === s));
  }

  private productRow(sku: Sku, last: number, next: number) {
    return {
      name: sku.name,
      id: sku.id,
      category: this.categoryLabel(sku.category),
      last_28_days: r0(last),
      next_28_days: r0(next),
      change_pct: pctChange(next, last),
      expected_revenue: money(next * sku.price),
      ...(sku.trend && sku.trend !== "Stable" ? { trend: `${this.m.trendBy}: ${sku.trendValue} (${sku.trend.toLowerCase()})` } : {}),
    };
  }

  /** The product (and store) a lookup refers to, for the card shown under an answer. */
  resolve(product: string, store?: string): { k: number; s: number } | null {
    const hit = this.match(product)[0];
    if (!hit) return null;
    let s = this.store(store);
    if (s < 0) {
      const best = this.pairsOf(hit.k).sort((a, b) => sum(b.mean) - sum(a.mean))[0];
      s = best?.s ?? 0;
    }
    return { k: hit.k, s };
  }

  run(name: string, args: Args): Result {
    switch (name) {
      case "overview":
        return this.overview();
      case "trends":
        return this.trends(args);
      case "top_products":
        return this.topProducts(args);
      case "product_detail":
        return this.productDetail(args);
      case "stock_risks":
        return this.stockRisks(args);
      case "restock_plan":
        return this.restockPlan(args);
      case "slow_movers":
        return this.slowMovers(args);
      case "stores":
        return this.stores(args);
      case "unusual_days":
        return this.unusualDays();
      case "size_mix":
        return this.sizeMix(args);
      case "forecast_accuracy":
        return this.accuracy();
      default:
        return { error: `There is no tool called ${name}.` };
    }
  }

  overview(): Result {
    const { d, m } = this;
    const hist = d.network.sales.length;
    const upcoming = [...new Set(d.network.events.filter((e) => e.week >= hist).map((e) => e.name))];
    const moving = d.trends.filter((t) => t.status !== "Stable");
    return {
      brand: d.meta.brand,
      data: m.has.truth ? "a simulated sample store, not a real brand" : "uploaded by the brand",
      as_of: d.meta.asOf,
      history_days: d.meta.counts.days,
      stores: d.stores.length,
      store_names: d.stores.slice(0, 12).map((s) => s.name),
      products: d.meta.counts.skus,
      sizes: d.meta.sizes,
      currency: d.meta.currency,
      stock_uploaded: m.has.stock,
      sales_corrected_for_stockouts: m.has.stockHistory,
      demand_last_28_days: d.network.demand28,
      expected_sales_next_28_days: d.network.forecast28,
      change_pct: pctChange(d.network.forecast28, d.network.demand28),
      expected_revenue_next_28_days: money(d.network.revenue28),
      categories: d.categories.map((c) => ({
        name: c.label,
        last_28_days: r0(c.demand28),
        next_28_days: r0(c.forecast28),
        change_pct: pctChange(c.forecast28, c.demand28),
      })),
      upcoming_events: upcoming,
      trends: {
        rising: moving.filter((t) => t.status === "Rising" || t.status === "Emerging").slice(0, 4).map((t) => `${t.value} (${t.attribute})`),
        fading: moving.filter((t) => t.status === "Fading" || t.status === "Peaking").slice(0, 4).map((t) => `${t.value} (${t.attribute})`),
      },
      ...(d.riskWeek
        ? { sizes_likely_to_sell_out_this_week: d.riskWeek.cells, revenue_at_risk_this_week: money(d.riskWeek.lostRevenue) }
        : {}),
    };
  }

  trends(args: Args): Result {
    const want = str(args.attribute).toLowerCase();
    const order: Record<string, number> = { Emerging: 0, Rising: 1, Peaking: 2, Fading: 3, Stable: 4 };
    let list = this.d.trends.filter((t) => !want || t.attribute.toLowerCase().includes(want) || want.includes(t.attribute.toLowerCase()));
    if (!args.include_stable) list = list.filter((t) => t.status !== "Stable");
    list = [...list].sort((a, b) => order[a.status] - order[b.status] || Math.abs(b.change_4w) - Math.abs(a.change_4w)).slice(0, 18);
    const skuName = (id: string) => this.d.skus.find((s) => s.id === id)?.name ?? id;
    if (!this.d.trends.length) {
      return { trends: [], note: "The catalog has no attribute columns such as style or colour, so there is nothing to test for trends." };
    }
    return {
      attributes_tested: [...new Set(this.d.trends.map((t) => t.attribute))],
      trends: list.map((t) => ({
        attribute: t.attribute,
        value: t.value,
        status: t.status,
        meaning: STATUS_MEANING[t.status],
        share_of_demand_pct: r1(100 * t.share),
        change_last_4_weeks_pct: r0(100 * t.change_4w),
        confidence: confidence(t.q_value),
        ...(this.m.has.stores && t.status !== "Stable" ? { strongest_in: t.leading_stores.slice(0, 3) } : {}),
        example_products: t.top_skus.slice(0, 3).map(skuName),
        ...(t.buzz_momentum != null ? { outside_interest_change_pct: r0(100 * t.buzz_momentum) } : {}),
      })),
      ...(list.length === 0 ? { note: "Nothing is gaining or losing share beyond normal ups and downs." } : {}),
    };
  }

  topProducts(args: Args): Result {
    const sort = ["forecast", "growth", "decline", "revenue"].includes(str(args.sort)) ? str(args.sort) : "forecast";
    const s = this.store(args.store);
    const cat = this.category(args.category);
    const limit = lim(args.limit, 8, 20);
    let rows = this.d.skus.map((sku, k) => {
      if (s < 0) return { sku, last: sku.demand28, next: sku.forecast28 };
      const t = this.pairsOf(k, s).map(pairTotals);
      return { sku, last: sum(t.map((x) => x.last)), next: sum(t.map((x) => x.next)) };
    });
    if (cat) rows = rows.filter((x) => x.sku.category === cat);
    if (sort === "growth" || sort === "decline") rows = rows.filter((x) => x.last + x.next >= 8);
    const key = {
      forecast: (x: (typeof rows)[0]) => -x.next,
      revenue: (x: (typeof rows)[0]) => -x.next * x.sku.price,
      growth: (x: (typeof rows)[0]) => -(x.next - x.last) / Math.max(x.last, 4),
      decline: (x: (typeof rows)[0]) => (x.next - x.last) / Math.max(x.last, 4),
    }[sort]!;
    rows.sort((a, b) => key(a) - key(b));
    return {
      ranked_by: sort,
      ...(s >= 0 ? { store: this.d.stores[s].name } : {}),
      ...(cat ? { category: this.categoryLabel(cat) } : {}),
      ...(str(args.store) && s < 0 ? { note: `No store matches "${str(args.store)}"; showing all stores.` } : {}),
      products: rows.slice(0, limit).map((x) => this.productRow(x.sku, x.last, x.next)),
    };
  }

  productDetail(args: Args): Result {
    const { d, m } = this;
    const query = str(args.product);
    const hits = this.match(query);
    if (!hits.length) {
      return { error: `No product matches "${query}".`, try_one_of: this.match(query.split(" ")[0] ?? "").slice(0, 5).map((h) => d.skus[h.k].name) };
    }
    const close = hits.filter((h) => h.score >= hits[0].score - 1);
    if (hits[0].score < 90 && close.length > 1) {
      return {
        note: "Several products match. Ask which one, or call again with an ID.",
        candidates: close.slice(0, 6).map((h) => ({ name: d.skus[h.k].name, id: d.skus[h.k].id })),
      };
    }
    const k = hits[0].k;
    const sku = d.skus[k];
    const s = this.store(args.store);
    const pairs = this.pairsOf(k, s);
    const sizes = d.meta.sizes;
    const weights = pairs.map((p) => sum(p.mean));
    const total = sum(weights) || 1;
    const share = sizes.map((_, i) => sum(pairs.map((p, j) => p.share[i] * weights[j])) / total);
    const names = d.meta.model.driverNames;
    const drivers = names
      // Drivers are multipliers on the recent run-rate: 1.2 lifts the forecast by 20%.
      .map((name, i) => ({ name, effect: sum(pairs.map((p, j) => ((p.drivers[i] ?? 1) - 1) * weights[j])) / total }))
      .filter((x) => x.name !== "Forecast window" && Number.isFinite(x.effect) && Math.abs(x.effect) >= 0.03)
      .sort((a, b) => Math.abs(b.effect) - Math.abs(a.effect))
      .slice(0, 3)
      .map((x) => ({ driver: x.name, effect_pct: r0(100 * x.effect) }));
    const weekly = [0, 1, 2, 3].map((w) => r0(sum(pairs.map((p) => p.mean[w]))));
    const last = sum(pairs.map((p) => pairTotals(p).last));
    const orders = d.plan.orders.filter((o) => o.sku_id === sku.id && (s < 0 || o.store === d.stores[s].name));
    const moves = d.plan.transfers.filter((t) => t.sku_id === sku.id);
    return {
      product: sku.name,
      id: sku.id,
      category: this.categoryLabel(sku.category),
      price: money(sku.price),
      attributes: sku.attributes,
      weeks_on_sale: sku.weeksLive,
      ...(sku.trend ? { trend: `${m.trendBy} ${sku.trendValue}: ${sku.trend.toLowerCase()} (${STATUS_MEANING[sku.trend]})` } : {}),
      scope: s >= 0 ? d.stores[s].name : m.has.stores ? "all stores" : "the store",
      last_28_days: r0(last),
      next_28_days: r0(sum(weekly)),
      change_pct: pctChange(sum(weekly), last),
      expected_by_week: weekly,
      size_mix_pct: percentBySize(sizes, share),
      what_moves_the_forecast: drivers,
      by_store: pairs
        .sort((a, b) => sum(b.mean) - sum(a.mean))
        .slice(0, 8)
        .map((p) => {
          const t = pairTotals(p);
          return {
            store: d.stores[p.s].name,
            last_28_days: r0(t.last),
            next_28_days: r0(t.next),
            likely_at_most_next_28_days: r0(p.cumP90[3]),
            ...(p.stock
              ? {
                  on_hand_by_size: sizeMap(sizes, p.stock),
                  arriving_by_size: p.inbound && sum(p.inbound) > 0 ? sizeMap(sizes, p.inbound) : undefined,
                  chance_to_sell_out_within_14_days_pct: percentBySize(sizes, p.risk14),
                  recommended_order_by_size: p.order && sum(p.order) > 0 ? sizeMap(sizes, p.order) : "none needed",
                  days_of_stock_left: p.cover == null ? null : r0(p.cover),
                }
              : {}),
          };
        }),
      ...(orders.length ? { in_the_restock_plan: orders.map((o) => ({ store: o.store, units: o.units, sizes: o.sizes, needs_approval: o.approval === "review" })) } : {}),
      ...(moves.length ? { transfers_in_the_plan: moves.slice(0, 6).map((t) => `${t.units} × ${t.size} from ${t.from} to ${t.to}`) } : {}),
      ...(!m.has.stock ? { stock: "not uploaded" } : {}),
    };
  }

  stockRisks(args: Args): Result {
    const { d, m } = this;
    if (!m.has.stock) return { error: NO_STOCK };
    const s = this.store(args.store);
    const sizes = d.meta.sizes;
    const rows = d.risks.filter((x) => s < 0 || x.s === s).slice(0, lim(args.limit, 10, 25));
    return {
      ...(d.riskWeek ? { sizes_likely_to_sell_out_this_week: d.riskWeek.cells, revenue_at_risk_this_week: money(d.riskWeek.lostRevenue) } : {}),
      ...(s >= 0 ? { store: d.stores[s].name } : {}),
      at_risk: rows.map((x) => {
        const p = m.pair(x.s, x.k);
        const risky = p?.riskWeek ? sizes.filter((_, i) => p.riskWeek![i] >= 0.5) : [];
        return {
          product: x.product,
          store: x.store,
          chance_to_sell_out_this_week_pct: r0(100 * x.max_size_risk),
          sizes_most_at_risk: risky.length ? risky : sizes.filter((_, i) => (p?.riskWeek?.[i] ?? 0) === Math.max(...(p?.riskWeek ?? [0]))),
          on_hand_by_size: sizeMap(sizes, p?.stock),
          expected_sales_this_week: r0(x.expected_demand),
          revenue_at_risk: money(x.expected_lost_revenue),
        };
      }),
    };
  }

  restockPlan(args: Args): Result {
    const { d, m } = this;
    if (!m.has.stock || !d.plan.summary) return { error: NO_STOCK };
    const s = this.store(args.store);
    const sum_ = d.plan.summary;
    const storeName = s >= 0 ? d.stores[s].name : null;
    const limit = lim(args.limit, 10, 25);
    const orders = d.plan.orders
      .filter((o) => !storeName || o.store === storeName)
      .sort((a, b) => b.risk_before * b.units - a.risk_before * a.units);
    const transfers = d.plan.transfers.filter((t) => !storeName || t.from === storeName || t.to === storeName);
    return {
      supplier_orders: {
        units: sum_.supplier_orders.units,
        lines: sum_.supplier_orders.lines,
        cost: money(sum_.supplier_orders.cost),
        budget: money(sum_.supplier_orders.budget ?? d.meta.policy.budget),
        budget_limited: sum_.supplier_orders.budget_binding,
        cost_to_meet_every_target: money(sum_.supplier_orders.cost_without_budget),
        lead_time_days: d.meta.policy.leadTimeDays,
      },
      ...(m.has.transfers ? { transfers_between_stores: { units: sum_.transfers.units, trips: sum_.transfers.lanes, cost: money(sum_.transfers.cost) } } : {}),
      sales_protected_next_14_days: money(sum_.expected_impact.revenue_protected),
      lines_waiting_for_approval: d.plan.orders.filter((o) => o.approval === "review").length,
      ...(storeName ? { store: storeName } : {}),
      top_orders: orders.slice(0, limit).map((o) => ({
        product: o.product,
        store: o.store,
        units_by_size: Object.fromEntries(Object.entries(o.sizes).filter(([, v]) => v > 0)),
        cost: money(o.cost),
        chance_to_sell_out_before_pct: r0(100 * o.risk_before),
        after_pct: r0(100 * o.risk_after),
        why: o.reason,
        ...(o.approval === "review" ? { needs_approval: o.review_reason ?? "large or uncertain" } : {}),
      })),
      ...(transfers.length
        ? { top_transfers: transfers.sort((a, b) => b.units - a.units).slice(0, 8).map((t) => `${t.units} × ${t.product} (${t.size}) from ${t.from} to ${t.to}`) }
        : {}),
    };
  }

  slowMovers(args: Args): Result {
    const { d, m } = this;
    const s = this.store(args.store);
    const limit = lim(args.limit, 8, 25);
    const falling = this.topProducts({ sort: "decline", store: args.store, limit: Math.min(limit, 6) }).products;
    if (!m.has.stock) return { overstock: NO_STOCK, demand_falling_fastest: falling };
    const rows = d.pairs
      .filter((p) => (s < 0 || p.s === s) && p.stock && sum(p.stock) >= 6)
      .map((p) => ({ p, stock: sum(p.stock!), next: sum(p.mean) }))
      .filter((x) => x.stock > 1.6 * x.next + 4)
      .sort((a, b) => (b.stock - b.next) * d.skus[b.p.k].price - (a.stock - a.next) * d.skus[a.p.k].price)
      .slice(0, limit);
    return {
      more_stock_than_demand: rows.map((x) => ({
        product: d.skus[x.p.k].name,
        store: d.stores[x.p.s].name,
        units_in_stock: x.stock,
        expected_sales_next_28_days: r0(x.next),
        weeks_of_stock: x.next > 0.5 ? r1((4 * x.stock) / x.next) : "more than a year",
        stock_value_at_cost: money(x.stock * d.skus[x.p.k].price * 0.45),
      })),
      demand_falling_fastest: falling,
    };
  }

  stores(args: Args): Result {
    const { d, m } = this;
    const s = this.store(args.store);
    const styles = d.meta.styles;
    const cats = d.meta.categories;
    const list = s >= 0 ? [d.stores[s]] : d.stores;
    return {
      stores: list.map((st) => ({
        store: st.name,
        last_28_days: st.demand28,
        next_28_days: st.forecast28,
        change_pct: pctChange(st.forecast28, st.demand28),
        expected_revenue: money(st.revenue28),
        ...(st.stockUnits != null ? { units_in_stock: st.stockUnits, sizes_at_risk_of_selling_out: st.riskCells } : {}),
        ...(m.has.stores
          ? {
              sells_more_than_average: [
                ...cats.filter((_, i) => (st.lqCategory?.[i] ?? 1) > 1.12).map((c) => c.label),
                ...styles.filter((_, i) => (st.lqStyle?.[i] ?? 1) > 1.15),
              ],
              sells_less_than_average: [
                ...cats.filter((_, i) => (st.lqCategory?.[i] ?? 1) < 0.88).map((c) => c.label),
                ...styles.filter((_, i) => (st.lqStyle?.[i] ?? 1) < 0.85),
              ],
            }
          : {}),
      })),
      ...(str(args.store) && s < 0 ? { note: `No store matches "${str(args.store)}".` } : {}),
    };
  }

  unusualDays(): Result {
    const { d } = this;
    return {
      unusual_days: d.anomalies.slice(0, 12).map((a) => ({
        when: a.start === a.end ? a.start : `${a.start} to ${a.end}`,
        where: a.scope === "city" ? "the whole business" : a.name,
        what: a.direction === "spike" ? "far more sales than expected" : "far fewer sales than expected",
        actual: a.actual,
        expected: a.expected,
        cause: a.cause ?? "no known cause",
      })),
      ...(d.anomalies.length ? {} : { note: "No unusual days in the last 60 days." }),
    };
  }

  sizeMix(args: Args): Result {
    const { d } = this;
    const sizes = d.meta.sizes;
    let pairs = d.pairs;
    let scope = "everything";
    if (str(args.product)) {
      const hit = this.match(str(args.product))[0];
      if (!hit) return { error: `No product matches "${str(args.product)}".` };
      pairs = pairs.filter((p) => p.k === hit.k);
      scope = d.skus[hit.k].name;
    }
    const cat = this.category(args.category);
    if (cat) {
      pairs = pairs.filter((p) => d.skus[p.k].category === cat);
      scope = `${scope === "everything" ? "" : `${scope}, `}${this.categoryLabel(cat)}`;
    }
    const s = this.store(args.store);
    if (s >= 0) {
      pairs = pairs.filter((p) => p.s === s);
      scope += ` in ${d.stores[s].name}`;
    }
    const weights = pairs.map((p) => sum(p.mean));
    const total = sum(weights) || 1;
    return {
      scope,
      share_of_demand_pct: percentBySize(
        sizes,
        sizes.map((_, i) => sum(pairs.map((p, j) => p.share[i] * weights[j])) / total),
      ),
      note: "Shares come from days when every size was in stock, pooled with similar products when data is thin.",
    };
  }

  accuracy(): Result {
    const f = this.d.evaluation.forecast;
    if (!f) return { note: "The history is too short to test forecasts on earlier dates. About 20 weeks of sales are needed." };
    const luma = f.overall["Luma"]?.wape;
    const simple = f.overall["Trailing 28-day sales"]?.wape;
    const i90 = f.coverage.nominal.indexOf(0.9);
    return {
      tested_on: `${f.folds.length} earlier dates, each forecast using only data from before that date`,
      compared_with: f.against,
      luma_error_pct: luma != null ? r0(100 * luma) : null,
      repeating_the_last_28_days_error_pct: simple != null ? r0(100 * simple) : null,
      error_explained:
        "How far forecasts missed, as a share of what sold, product by product and store by store. Single products are noisy, so 25 to 40% is normal at this level; totals are far more accurate.",
      upper_estimate_held_pct: i90 >= 0 ? r0(100 * f.coverage.calibrated[i90]) : null,
      upper_estimate_explained: "How often actual sales stayed below Luma's high estimate. The target is 90%.",
    };
  }
}

/** A short description of the loaded data, added to the agent's instructions. */
export function describe(m: Model): string {
  const d = m.d;
  return [
    `Brand: ${d.meta.brand}${m.has.truth ? " (a simulated sample store, not a real brand)" : ""}.`,
    `Analysis as of ${d.meta.asOf}; "this week" means the 7 days after that. History: ${d.meta.counts.days} days.`,
    `${d.stores.length} store(s), ${d.meta.counts.skus} products, sizes ${d.meta.sizes.join(", ")}. Currency ${d.meta.currency}.`,
    `Stock levels uploaded: ${m.has.stock ? "yes" : "no"}. Product attributes: ${d.meta.attributes.join(", ") || "none"}.`,
  ].join("\n");
}

/** A few words for the step list while a tool runs, such as "Checking trends". */
export function stepLabel(name: string, args: Args): string {
  const what = str(args.product) || str(args.store) || str(args.category);
  const labels: Record<string, string> = {
    overview: "Reading the overview",
    trends: "Checking trends",
    top_products: "Ranking products",
    product_detail: `Looking up ${what || "a product"}`,
    stock_risks: "Checking what could sell out",
    restock_plan: "Reading the restock plan",
    slow_movers: "Finding slow sellers",
    stores: what ? `Looking at ${what}` : "Comparing stores",
    unusual_days: "Checking unusual days",
    size_mix: "Working out the size mix",
    forecast_accuracy: "Checking past accuracy",
  };
  return labels[name] ?? name;
}
