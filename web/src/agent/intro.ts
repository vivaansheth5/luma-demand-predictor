/**
 * What Luma says before anyone asks: a short, plain summary of the loaded data, starter
 * questions, and the side panel lists. All of it is computed here, without calling the model.
 */
import type { Model } from "../data";
import { int } from "../format";

export interface GlanceItem {
  label: string;
  note?: string;
  tone?: "up" | "down" | "flat";
  question: string;
}

export interface Glance {
  trending: GlanceItem[];
  best: GlanceItem[];
  low: GlanceItem[];
  lowTitle: string;
}

const list = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

export function opening(m: Model): { text: string; suggestions: string[] } {
  const d = m.d;
  const weeks = Math.round(d.meta.counts.days / 7);
  const lines: string[] = [];
  if (m.has.truth) {
    lines.push(
      `This is a **sample store**: a made-up fashion retailer with ${d.stores.length} stores and ${d.meta.counts.skus} products. I've read its last ${weeks} weeks of sales. Here's what stands out.`,
    );
  } else {
    lines.push(
      `I've read ${weeks} weeks of ${d.meta.brand} sales: ${int(d.meta.counts.skus)} products${m.has.stores ? ` across ${d.stores.length} stores` : ""}. Here's what stands out.`,
    );
  }
  const by = m.trendBy;
  const moving = d.trends.filter((t) => t.status !== "Stable").sort((a, b) => (a.attribute === by ? -1 : 0) - (b.attribute === by ? -1 : 0));
  const up = moving.filter((t) => t.status === "Rising" || t.status === "Emerging").slice(0, 2);
  const down = moving.filter((t) => t.status === "Fading" || t.status === "Peaking").slice(0, 2);
  const bullets: string[] = [];
  if (up.length || down.length) {
    const parts = [];
    if (up.length) parts.push(`${list(up.map((t) => `**${t.value}**`))} ${up.length > 1 ? "are" : "is"} catching on`);
    if (down.length) parts.push(`${list(down.map((t) => `**${t.value}**`))} ${down.length > 1 ? "are" : "is"} fading`);
    bullets.push(`${parts.join(", while ")}.`);
  } else if (d.trends.length) {
    bullets.push("No style, colour or fit is clearly gaining or losing ground right now.");
  }
  const best = [...d.skus].sort((a, b) => b.forecast28 - a.forecast28)[0];
  if (best) bullets.push(`Your best seller over the next four weeks should be **${best.name}**.`);
  if (m.has.stock && d.risks.length) {
    const top = d.risks[0];
    bullets.push(
      `A few sizes may sell out this week. The most urgent is **${top.product}**${m.has.stores ? ` at ${top.store}` : ""}.`,
    );
  } else if (!m.has.stock) {
    bullets.push("Add a stock file and I can tell you what will sell out and what to reorder.");
  }
  const capital = (b: string) => b.replace(/^(\**)(\w)/, (_, stars: string, c: string) => stars + c.toUpperCase());
  lines.push(bullets.map((b) => `- ${capital(b)}`).join("\n"));
  lines.push("Ask me anything, or start with one of these.");
  const suggestions = m.has.stock
    ? ["What should I restock this week?", "Which styles are trending?", "What will sell best next month?", "What isn't selling?"]
    : ["Which styles are trending?", "What will sell best next month?", "Which products are slowing down?", "How do my sizes split?"];
  return { text: lines.join("\n\n"), suggestions };
}

export function glance(m: Model): Glance {
  const d = m.d;
  const by = m.trendBy;
  const order: Record<string, number> = { Emerging: 0, Rising: 1, Peaking: 2, Fading: 3 };
  const trending = d.trends
    .filter((t) => t.status !== "Stable")
    .sort((a, b) => (a.attribute === by ? 0 : 1) - (b.attribute === by ? 0 : 1) || order[a.status] - order[b.status])
    .slice(0, 5)
    .map((t) => ({
      label: t.value.charAt(0).toUpperCase() + t.value.slice(1),
      note: ({ Emerging: "Starting", Rising: "Rising", Peaking: "Peaking", Fading: "Fading" } as Record<string, string>)[t.status],
      tone: (t.status === "Fading" || t.status === "Peaking" ? "down" : "up") as GlanceItem["tone"],
      question: `Tell me about the ${t.value} trend. What should I do about it?`,
    }));
  const best = [...d.skus]
    .sort((a, b) => b.forecast28 - a.forecast28)
    .slice(0, 5)
    .map((s) => ({ label: s.name, question: `How is ${s.name} expected to sell over the next month?` }));
  let low: GlanceItem[];
  let lowTitle: string;
  if (m.has.stock && d.risks.length) {
    lowTitle = "Running low";
    // One row per product, naming the stores where it is short.
    const byProduct = new Map<string, string[]>();
    d.risks.forEach((r) => byProduct.set(r.product, [...(byProduct.get(r.product) ?? []), r.store]));
    low = [...byProduct.entries()].slice(0, 4).map(([product, stores]) => ({
      label: product,
      note: m.has.stores ? (stores.length > 1 ? `${stores.length} stores` : stores[0]) : undefined,
      question: `What should I do about ${product}${m.has.stores ? ` at ${list(stores.slice(0, 3))}` : ""}? It looks like it could sell out.`,
    }));
  } else {
    lowTitle = "Slowing down";
    low = d.skus
      .filter((s) => s.demand28 + s.forecast28 >= 8)
      .map((s) => ({ s, change: (s.forecast28 - s.demand28) / Math.max(s.demand28, 4) }))
      .sort((a, b) => a.change - b.change)
      .slice(0, 4)
      .map(({ s }) => ({ label: s.name, question: `Why is ${s.name} slowing down, and what should I do?` }));
  }
  return { trending, best, low, lowTitle };
}
