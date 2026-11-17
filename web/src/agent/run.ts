/**
 * The agent loop. Each call to /api/chat is one turn of Claude; when it asks for tools, the
 * browser runs them on the loaded analysis and sends the results back, until Claude answers.
 */
import { stepLabel, type Tools } from "./tools";

export type Block =
  | { type: "text"; text: string }
  | { type: "tool_use"; id: string; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; tool_use_id: string; content: string; is_error?: boolean };

export type ApiMessage = { role: "user" | "assistant"; content: string | Block[] };

export interface Step {
  name: string;
  label: string;
  args: Record<string, unknown>;
}

export class ChatError extends Error {
  constructor(
    message: string,
    readonly code: string,
  ) {
    super(message);
  }
}

const MAX_ROUNDS = 8;
const KEEP_TURNS = 6;
const MAX_RESULT_CHARS = 16_000;

/** Is chat switched on? False when the API is unreachable (for example in local development). */
export async function chatReady(): Promise<boolean> {
  try {
    const response = await fetch("/api/chat", { cache: "no-store" });
    if (!response.ok) return false;
    const body = (await response.json()) as { ready?: boolean };
    return Boolean(body.ready);
  } catch {
    return false;
  }
}

/** Keep the last few questions, and shorten tool results from earlier questions to save tokens. */
function compact(messages: ApiMessage[]): ApiMessage[] {
  const starts = messages.flatMap((m, i) => (m.role === "user" && typeof m.content === "string" ? [i] : []));
  const from = starts.length > KEEP_TURNS ? starts[starts.length - KEEP_TURNS] : 0;
  const current = starts[starts.length - 1] ?? 0;
  return messages.slice(from).map((m, i) => {
    if (from + i >= current || typeof m.content === "string") return m;
    return {
      role: m.role,
      content: m.content.map((b) => (b.type === "tool_result" ? { ...b, content: "(earlier result, left out to save space)" } : b)),
    };
  });
}

export interface Answer {
  text: string;
  steps: Step[];
  messages: ApiMessage[];
  /** Products the agent looked up, by position in the catalog, for the cards under the answer. */
  lookups: { product: string; store?: string }[];
}

export async function ask(
  question: string,
  history: ApiMessage[],
  tools: Tools,
  context: string,
  onStep: (step: Step) => void,
): Promise<Answer> {
  const messages: ApiMessage[] = [...history, { role: "user", content: question }];
  const steps: Step[] = [];
  const lookups: Answer["lookups"] = [];
  for (let round = 0; round < MAX_ROUNDS; round++) {
    let response: Response;
    try {
      response = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ messages: compact(messages), context }),
      });
    } catch {
      throw new ChatError("Luma could not be reached. Check your connection and try again.", "network");
    }
    const body = (await response.json().catch(() => ({}))) as {
      content?: Block[];
      stop_reason?: string;
      error?: string;
      code?: string;
    };
    if (!response.ok || !body.content) {
      throw new ChatError(body.error ?? "Luma could not answer just now. Try again.", body.code ?? "error");
    }
    const content = body.content.filter((b) => b.type === "text" || b.type === "tool_use");
    messages.push({ role: "assistant", content });
    const calls = content.filter((b): b is Extract<Block, { type: "tool_use" }> => b.type === "tool_use");
    if (body.stop_reason !== "tool_use" || calls.length === 0) {
      const text = content
        .filter((b): b is Extract<Block, { type: "text" }> => b.type === "text")
        .map((b) => b.text)
        .join("\n\n")
        .trim();
      return { text: text || "I could not find an answer to that in this data.", steps, messages, lookups };
    }
    const results: Block[] = calls.map((call) => {
      const step = { name: call.name, label: stepLabel(call.name, call.input), args: call.input };
      steps.push(step);
      onStep(step);
      if (call.name === "product_detail" && typeof call.input.product === "string") {
        lookups.push({ product: call.input.product, store: typeof call.input.store === "string" ? call.input.store : undefined });
      }
      try {
        const result = JSON.stringify(tools.run(call.name, call.input ?? {}));
        return { type: "tool_result", tool_use_id: call.id, content: result.slice(0, MAX_RESULT_CHARS) };
      } catch (err) {
        return { type: "tool_result", tool_use_id: call.id, content: `The tool failed: ${(err as Error).message}`, is_error: true };
      }
    });
    messages.push({ role: "user", content: results });
  }
  return {
    text: "That took more steps than I allow for one question. Try asking something narrower.",
    steps,
    messages,
    lookups,
  };
}
