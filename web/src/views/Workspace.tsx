import { useEffect, useMemo, useRef, useState } from "react";
import { glance, opening } from "../agent/intro";
import { ask, chatReady, ChatError, type ApiMessage, type Step } from "../agent/run";
import { describe, Tools } from "../agent/tools";
import { Markdown } from "../components/Markdown";
import { Icon, SizeRun } from "../components/parts";
import { openPair, type Model } from "../data";
import { pct } from "../format";

export interface Turn {
  id: number;
  question: string;
  steps: Step[];
  answer?: string;
  error?: string;
  pending: boolean;
  lookups: { product: string; store?: string }[];
}

export interface Conversation {
  turns: Turn[];
  messages: ApiMessage[];
}

export const emptyConversation = (): Conversation => ({ turns: [], messages: [] });

function ProductCard({ m, k, s }: { m: Model; k: number; s: number }) {
  const sku = m.d.skus[k];
  const p = m.pair(s, k);
  if (!sku || !p) return null;
  const sizes = m.d.meta.sizes;
  return (
    <button className="pcard" onClick={() => openPair(s, k)} title="Open this product in the report">
      <span className="pcard-head">
        <span className="pcard-name">{sku.name}</span>
        <span className="pcard-sub">
          {m.has.stores ? `${m.d.stores[s].name} · ` : ""}
          {p.stock ? "on hand by size, shaded by chance of selling out" : "share of demand by size"}
        </span>
      </span>
      {p.stock && p.riskWeek ? (
        <SizeRun sizes={sizes} stock={p.stock} risk={p.riskWeek} order={p.order} compact />
      ) : (
        <span className="pcard-mix">
          {sizes.map((z, i) => (
            <span key={z} className="mix-cell">
              <span className="mix-bar" style={{ height: `${Math.max(4, 44 * p.share[i])}px` }} />
              <span className="mix-size">{z}</span>
              <span className="mix-pct">{pct(p.share[i])}</span>
            </span>
          ))}
        </span>
      )}
    </button>
  );
}

function TurnView({ turn, m, tools }: { turn: Turn; m: Model; tools: Tools }) {
  const [open, setOpen] = useState(false);
  const cards = useMemo(() => {
    const seen = new Set<string>();
    return turn.lookups
      .map((l) => tools.resolve(l.product, l.store))
      .filter((x): x is { k: number; s: number } => {
        if (!x || seen.has(`${x.k}-${x.s}`)) return false;
        seen.add(`${x.k}-${x.s}`);
        return true;
      })
      .slice(0, 2);
  }, [turn.lookups, tools]);
  return (
    <div className="turn">
      <div className="msg-user">{turn.question}</div>
      <div className="msg-luma">
        {turn.steps.length > 0 && (
          <div className="steps-line">
            {turn.pending ? (
              <span className="working-step">
                <span className="dot" aria-hidden="true" />
                {turn.steps[turn.steps.length - 1].label}
              </span>
            ) : (
              <button className="steps-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
                {turn.steps.map((s) => s.label).filter((v, i, a) => a.indexOf(v) === i).join(" · ")}
              </button>
            )}
          </div>
        )}
        {open && (
          <ol className="steps-detail">
            {turn.steps.map((s, i) => (
              <li key={i}>
                <code>
                  {s.name}({Object.keys(s.args).length ? JSON.stringify(s.args) : ""})
                </code>
              </li>
            ))}
          </ol>
        )}
        {turn.pending && turn.steps.length === 0 && (
          <span className="working-step">
            <span className="dot" aria-hidden="true" />
            Thinking
          </span>
        )}
        {turn.answer && <Markdown text={turn.answer} />}
        {turn.error && <p className="msg-error">{turn.error}</p>}
        {cards.length > 0 && (
          <div className="pcards">
            {cards.map((c) => (
              <ProductCard key={`${c.k}-${c.s}`} m={m} k={c.k} s={c.s} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function Workspace({
  m,
  conversation,
  setConversation,
  pending,
}: {
  m: Model;
  conversation: Conversation;
  setConversation: (fn: (c: Conversation) => Conversation) => void;
  pending: string | null;
}) {
  const tools = useMemo(() => new Tools(m), [m]);
  const context = useMemo(() => describe(m), [m]);
  const intro = useMemo(() => opening(m), [m]);
  const side = useMemo(() => glance(m), [m]);
  const [ready, setReady] = useState<boolean | null>(null);
  const [draft, setDraft] = useState("");
  const busy = conversation.turns.some((t) => t.pending);
  const end = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const asked = useRef(false);

  useEffect(() => {
    let live = true;
    void chatReady().then((ok) => live && setReady(ok));
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [conversation.turns]);

  const send = async (question: string) => {
    const q = question.trim();
    if (!q || busy) return;
    setDraft("");
    const id = Date.now();
    const update = (fn: (t: Turn) => Turn) =>
      setConversation((c) => ({ ...c, turns: c.turns.map((t) => (t.id === id ? fn(t) : t)) }));
    setConversation((c) => ({ ...c, turns: [...c.turns, { id, question: q, steps: [], pending: true, lookups: [] }] }));
    try {
      const result = await ask(q, conversation.messages, tools, context, (step) => update((t) => ({ ...t, steps: [...t.steps, step] })));
      setConversation((c) => ({
        messages: result.messages,
        turns: c.turns.map((t) => (t.id === id ? { ...t, answer: result.text, steps: result.steps, lookups: result.lookups, pending: false } : t)),
      }));
    } catch (err) {
      const message =
        err instanceof ChatError && err.code === "no_key"
          ? "Chat isn't switched on for this site yet, so Luma can't answer questions. You can still explore the report."
          : err instanceof ChatError
            ? err.message
            : "Luma could not answer just now. Try again.";
      update((t) => ({ ...t, error: message, pending: false }));
    }
  };

  useEffect(() => {
    if (pending && !asked.current) {
      asked.current = true;
      void send(pending);
    }
  }, [pending]);

  const sideList = (title: string, items: ReturnType<typeof glance>["best"]) =>
    items.length > 0 && (
      <section className="glance-group">
        <h3>{title}</h3>
        <ul>
          {items.map((item) => (
            <li key={item.label + (item.note ?? "")}>
              <button disabled={busy} onClick={() => void send(item.question)}>
                {item.tone && (
                  <span className={`tone ${item.tone}`}>
                    <Icon name={item.tone} size={12} />
                  </span>
                )}
                <span className="glance-label">{item.label}</span>
                {item.note && <span className="glance-note">{item.note}</span>}
              </button>
            </li>
          ))}
        </ul>
      </section>
    );

  return (
    <div className="workspace">
      <div className="chat">
        <div className="thread">
          <div className="msg-luma intro">
            <Markdown text={intro.text} />
            <div className="suggest">
              {intro.suggestions.map((s) => (
                <button key={s} className="ask-chip" disabled={busy} onClick={() => void send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
          {conversation.turns.map((t) => (
            <TurnView key={t.id} turn={t} m={m} tools={tools} />
          ))}
          <div ref={end} />
        </div>
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            void send(draft);
          }}
        >
          {ready === false && (
            <p className="offline">Chat isn't switched on for this site yet. The report still works.</p>
          )}
          <div className="composer-box">
            <label className="visually-hidden" htmlFor="ask">
              Ask Luma
            </label>
            <textarea
              id="ask"
              ref={box}
              rows={1}
              value={draft}
              placeholder="Ask Luma about your sales"
              onChange={(e) => {
                setDraft(e.target.value);
                e.target.style.height = "auto";
                e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send(draft);
                }
              }}
            />
            <button className="btn send" type="submit" disabled={busy || !draft.trim()}>
              Ask
            </button>
          </div>
          <p className="composer-note">
            Answers come from {m.has.truth ? "the sample store's" : "your"} numbers and can be wrong. Check large orders.
          </p>
        </form>
      </div>
      <aside className="glance" aria-label="At a glance">
        {sideList("What's moving", side.trending)}
        {sideList("Best sellers next month", side.best)}
        {sideList(side.lowTitle, side.low)}
        <p className="glance-foot">Click anything to ask Luma about it.</p>
      </aside>
    </div>
  );
}
