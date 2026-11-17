/**
 * POST /api/chat: one turn of the Luma agent.
 *
 * The browser holds the brand's analysis and runs the tools against it. This function only
 * talks to Claude: it adds the system prompt and the tool definitions, which the browser
 * cannot change, and keeps the API key on the server. When Claude asks for a tool, the browser
 * runs it and sends the result back in the next call.
 *
 * GET /api/chat reports whether chat is switched on, without calling the model.
 */

const MODEL = process.env.LUMA_MODEL || "claude-sonnet-5-5";
const MAX_TOKENS = 1400;
const MAX_MESSAGES = 60;
const MAX_BODY_CHARS = 300_000;
const MAX_TEXT_CHARS = 4_000;
const MAX_RESULT_CHARS = 24_000;

const SYSTEM = `You are Luma, an AI agent that helps fashion brands decide what to stock. You answer questions about one brand's sales using tools. The tools read an analysis Luma has already run on the brand's data: demand forecasts for the next four weeks by product, store and size; trends in product attributes such as style or colour; unusual days; and, when the brand uploaded stock levels, the chance each size sells out and a restock plan.

How to answer:
- Lead with the answer in one or two plain sentences. Then give at most four short supporting points.
- Call tools before answering. Use only numbers that come from a tool result. Never invent products, figures or dates.
- Use few numbers, rounded (about 40 units, not 39.6). Prefer plain comparisons such as "about twice last month".
- Name the products, sizes and stores. Say "expected to sell" rather than "forecast" where you can.
- No statistical jargon: say "likely", "a range of 30 to 45", "we are fairly sure". Never say quantile, q-value, WAPE or P90.
- Use short bullet lists, not tables. Bold at most two key phrases. Keep answers under 150 words unless the user asks for detail.
- If the data cannot answer the question, say what is missing and which file would add it. Stock questions need a stock file.
- Forecasts are estimates. Say so when the user is about to commit a large order.
- Stay on this brand's products, sales, trends, stock and how Luma works. Politely decline anything else.`;

const TOOLS = [
  {
    name: "overview",
    description:
      "Start here. The brand's data at a glance: history covered, stores, products, sizes, currency, whether stock was uploaded, demand in the last 28 days against the next 28, category changes, upcoming events and how many trends are moving.",
    input_schema: { type: "object", properties: {} },
  },
  {
    name: "trends",
    description:
      "Product attribute values (style, colour, fit, type and so on) gaining or losing share of demand, with how sure the test is, where each trend is strongest and example products.",
    input_schema: {
      type: "object",
      properties: {
        attribute: { type: "string", description: "Only this attribute, such as 'style' or 'color'." },
        include_stable: { type: "boolean", description: "Also list values that are not moving." },
      },
    },
  },
  {
    name: "top_products",
    description:
      "Rank products by expected sales over the next 28 days, by growth or decline against the last 28 days, or by expected revenue. Optionally within one category or store.",
    input_schema: {
      type: "object",
      properties: {
        sort: { type: "string", enum: ["forecast", "growth", "decline", "revenue"] },
        category: { type: "string" },
        store: { type: "string" },
        limit: { type: "integer", minimum: 1, maximum: 20 },
      },
      required: ["sort"],
    },
  },
  {
    name: "product_detail",
    description:
      "Everything about one product: expected sales by week with a likely range, sales by store, size mix, what drives the forecast, its trend, and stock, sell-out risk and the recommended order by size when stock was uploaded. Accepts a name, part of a name or a product ID; returns candidates when the match is unclear.",
    input_schema: {
      type: "object",
      properties: {
        product: { type: "string" },
        store: { type: "string", description: "Limit to one store." },
      },
      required: ["product"],
    },
  },
  {
    name: "stock_risks",
    description:
      "Products and sizes most likely to sell out in the next week, ranked by the revenue at risk. Needs uploaded stock levels.",
    input_schema: {
      type: "object",
      properties: { store: { type: "string" }, limit: { type: "integer", minimum: 1, maximum: 25 } },
    },
  },
  {
    name: "restock_plan",
    description:
      "Luma's recommended supplier orders (units by size, cost, why) and transfers between stores, within the budget, with lines that need a person's approval. Needs uploaded stock levels.",
    input_schema: {
      type: "object",
      properties: { store: { type: "string" }, limit: { type: "integer", minimum: 1, maximum: 25 } },
    },
  },
  {
    name: "slow_movers",
    description:
      "Products with more stock than expected demand needs (when stock was uploaded), and products whose demand is falling fastest. Candidates for markdowns or moving stock.",
    input_schema: {
      type: "object",
      properties: { store: { type: "string" }, limit: { type: "integer", minimum: 1, maximum: 25 } },
    },
  },
  {
    name: "stores",
    description:
      "Every store's demand last 28 days and next 28, revenue, stock and sizes at risk, and what each store sells more of than average. Pass a store for just that one.",
    input_schema: { type: "object", properties: { store: { type: "string" } } },
  },
  {
    name: "unusual_days",
    description:
      "Recent days when sales were far above or below what was expected, for the whole business, a store or a product, with a known cause where there is one.",
    input_schema: { type: "object", properties: {} },
  },
  {
    name: "size_mix",
    description: "How demand splits across sizes, for one product, one category, one store or everything.",
    input_schema: {
      type: "object",
      properties: { product: { type: "string" }, category: { type: "string" }, store: { type: "string" } },
    },
  },
  {
    name: "forecast_accuracy",
    description:
      "How well Luma's forecasts did on earlier dates in this data, compared with simply repeating the last 28 days. Use it when the user asks how far to trust the numbers.",
    input_schema: { type: "object", properties: {} },
  },
];
const TOOL_NAMES = new Set(TOOLS.map((t) => t.name));

// Requests per address, per running instance. Instances are reused, so this stops bursts from
// one visitor; a firewall rule in the Vercel project does the same across instances.
const WINDOW_MS = 10 * 60 * 1000;
const PER_ADDRESS = 40;
const PER_INSTANCE = 400;
const hits = new Map<string, number[]>();
let recent: number[] = [];

function limited(address: string): boolean {
  const now = Date.now();
  recent = recent.filter((t) => now - t < WINDOW_MS);
  const mine = (hits.get(address) ?? []).filter((t) => now - t < WINDOW_MS);
  if (mine.length >= PER_ADDRESS || recent.length >= PER_INSTANCE) return true;
  mine.push(now);
  recent.push(now);
  hits.set(address, mine);
  if (hits.size > 5000) hits.clear();
  return false;
}

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

type Block = Record<string, unknown>;
type Message = { role: "user" | "assistant"; content: string | Block[] };

/** Keep only well-formed messages made of the block types this agent uses. */
function clean(input: unknown): Message[] | null {
  if (!Array.isArray(input) || input.length === 0 || input.length > MAX_MESSAGES) return null;
  const out: Message[] = [];
  for (const m of input) {
    if (!m || (m.role !== "user" && m.role !== "assistant")) return null;
    if (typeof m.content === "string") {
      if (m.content.length > MAX_TEXT_CHARS) return null;
      out.push({ role: m.role, content: m.content });
      continue;
    }
    if (!Array.isArray(m.content) || m.content.length === 0 || m.content.length > 20) return null;
    const blocks: Block[] = [];
    for (const b of m.content) {
      if (b?.type === "text" && typeof b.text === "string" && b.text.length <= MAX_TEXT_CHARS * 2) {
        blocks.push({ type: "text", text: b.text });
      } else if (m.role === "assistant" && b?.type === "tool_use" && TOOL_NAMES.has(b.name) && typeof b.id === "string") {
        blocks.push({ type: "tool_use", id: b.id, name: b.name, input: b.input ?? {} });
      } else if (m.role === "user" && b?.type === "tool_result" && typeof b.tool_use_id === "string") {
        const content = typeof b.content === "string" ? b.content.slice(0, MAX_RESULT_CHARS) : "";
        blocks.push({ type: "tool_result", tool_use_id: b.tool_use_id, content, ...(b.is_error ? { is_error: true } : {}) });
      } else {
        return null;
      }
    }
    out.push({ role: m.role, content: blocks });
  }
  return out[0].role === "user" ? out : null;
}

export function GET(): Response {
  return reply(200, { ready: Boolean(process.env.ANTHROPIC_API_KEY), model: MODEL });
}

export async function POST(request: Request): Promise<Response> {
  const key = process.env.ANTHROPIC_API_KEY;
  if (!key) return reply(503, { code: "no_key", error: "Chat is not switched on yet." });

  const address = (request.headers.get("x-forwarded-for") ?? "").split(",")[0].trim() || "unknown";
  if (limited(address)) {
    return reply(429, { code: "rate_limited", error: "That is a lot of questions in a short time. Try again in a few minutes." });
  }

  const raw = await request.text();
  if (raw.length > MAX_BODY_CHARS) return reply(413, { code: "too_large", error: "This conversation is too long. Start a new one." });
  let body: { messages?: unknown; context?: unknown };
  try {
    body = JSON.parse(raw);
  } catch {
    return reply(400, { code: "bad_request", error: "The request was not valid JSON." });
  }
  const messages = clean(body.messages);
  if (!messages) return reply(400, { code: "bad_request", error: "The conversation was not in the expected format." });
  const context = typeof body.context === "string" ? body.context.slice(0, 2000) : "";

  const upstream = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: { "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json" },
    body: JSON.stringify({
      model: MODEL,
      max_tokens: MAX_TOKENS,
      // The fixed prompt and tools are cached, so repeat calls pay for them once.
      system: [
        { type: "text", text: SYSTEM, cache_control: { type: "ephemeral" } },
        { type: "text", text: `About the data loaded now:\n${context}` },
      ],
      tools: TOOLS,
      messages,
    }),
  });
  if (!upstream.ok) {
    const detail = await upstream.text();
    console.error("chat: upstream", upstream.status, detail.slice(0, 500));
    const busy = upstream.status === 429 || upstream.status === 529;
    return reply(busy ? 503 : 502, {
      code: busy ? "busy" : "upstream",
      error: busy ? "Luma is busy right now. Try again in a minute." : "Luma could not answer just now. Try again.",
    });
  }
  const data = (await upstream.json()) as { content: Block[]; stop_reason: string; usage?: Record<string, number> };
  console.log("chat", JSON.stringify({ stop: data.stop_reason, usage: data.usage }));
  return reply(200, { content: data.content, stop_reason: data.stop_reason });
}
