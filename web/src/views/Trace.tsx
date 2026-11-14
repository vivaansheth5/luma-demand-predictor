import { useEffect, useRef, useState } from "react";
import { rupees, type Model } from "../data";

const KIND: Record<string, string> = {
  draft_plan: "plan",
  check_guardrails: "check",
  commit_plan: "commit",
};

export function Trace({ m }: { m: Model }) {
  const { agent } = m.d;
  const total = agent.steps.length;
  const [shown, setShown] = useState(total);
  const [playing, setPlaying] = useState(false);
  const timer = useRef<number>();

  useEffect(() => {
    if (!playing) return;
    if (shown >= total) {
      setPlaying(false);
      return;
    }
    timer.current = window.setTimeout(() => setShown((n) => n + 1), 650);
    return () => window.clearTimeout(timer.current);
  }, [playing, shown, total]);

  const replay = () => {
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (still) return;
    setShown(0);
    setPlaying(true);
  };

  return (
    <>
      <header className="view-head">
        <h1>How the agent decided</h1>
        <p>
          Every run is recorded: what the agent was thinking, which tool it called, and what came back. What it looks at
          and how often it redrafts depend on what the tools return.{" "}
          {agent.mode === "llm"
            ? `This run was driven by ${agent.model}.`
            : "This run used the built-in planner. With an API key, Claude drives the same tools under the same checks."}
        </p>
      </header>

      <div className="block grid-main">
        <div>
          <div className="block-head">
            <h2>
              {total} steps, {agent.status === "committed" ? "plan committed" : "stopped for a person"}
            </h2>
            <button className="btn ghost" onClick={replay} disabled={playing}>
              {playing ? "Replaying" : "Replay the run"}
            </button>
          </div>
          <ol className="steps">
            {agent.steps.map((s, i) => (
              <li key={s.n} className={`step kind-${KIND[s.tool] ?? "read"}${i >= shown ? " hidden" : ""}${playing && i === shown - 1 ? " entering" : ""}`}>
                <div className="step-n">{s.n}</div>
                <div>
                  <p className="step-thought">{rupees(s.thought)}</p>
                  <div className="step-call">
                    <code>
                      {s.tool}({Object.entries(s.args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")})
                    </code>
                    <span>{s.seconds < 0.1 ? "under 0.1s" : `${s.seconds.toFixed(1)}s`}</span>
                  </div>
                  <p className="step-obs">{rupees(s.observation)}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>

        <div className="stack">
          <section className="sheet">
            <div className="sheet-head">
              <div>
                <h3>Tools the agent can call</h3>
                <p>Reading tools, a planner, a checker and a commit. The commit refuses any draft with a failed check.</p>
              </div>
            </div>
            <ul className="tools-list">
              {agent.tools.map((t) => (
                <li key={t.name}>
                  <code>{t.name}</code>
                  {t.description}
                </li>
              ))}
            </ul>
          </section>
        </div>
      </div>
    </>
  );
}
