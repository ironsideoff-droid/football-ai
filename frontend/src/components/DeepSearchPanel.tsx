import { useState } from "react";
import type { DeepSearchSummary } from "../types";
import { CountUp } from "./charts";

const TOPIC: Record<string, string> = {
  injuries: "Injuries", suspensions: "Suspensions", lineups: "Lineups & formations", tactics: "Tactics", player_stats: "Player output",
  form_records: "Form & records", manager_press: "Manager & press", transfers: "Transfers", referee_conditions: "Referee & conditions",
  stakes_schedule: "Stakes & schedule", previews_predictions: "Previews & predictions", general: "Other",
};
const ENGINE: Record<string, string> = { statistical: "Statistical Researcher", intelligence: "Football Intelligence", tactical: "Agent 4 · Data Scout", context: "Chief Predictor" };

/** The deep search: how many real, unique, recent headlines each role-based engine read, by topic, with every link. */
export default function DeepSearchPanel({ deep, home, away }: { deep: DeepSearchSummary; home: string; away: string }) {
  const [engine, setEngine] = useState<string>("all");
  const [shown, setShown] = useState(30);
  const max = Math.max(...deep.engines.map((e) => e.found), 1);
  const topics = Object.entries(deep.topics).sort((a, b) => b[1] - a[1]);
  const tmax = Math.max(...topics.map(([, n]) => n), 1);
  const items = deep.items.filter((i) => engine === "all" || i.engine === engine);
  return (
    <section className="panel deep">
      <h3>Deep search</h3>
      <div className="deep-total">
        <strong><CountUp value={deep.total} /></strong>
        <span>unique headlines from the last 30 days · {deep.queries - deep.failed_queries} of {deep.queries} searches succeeded · target {deep.minimum_target}–{deep.maximum}</span>
      </div>
      {deep.below_target && <p className="warn">Fewer than {deep.minimum_target} relevant headlines exist for this match right now. Nothing was padded or invented.</p>}
      <div className="deep-engines">
        {deep.engines.map((e) => (
          <button key={e.key} type="button" className={`deep-engine ${engine === e.key ? "on" : ""}`} onClick={() => setEngine(engine === e.key ? "all" : e.key)} aria-pressed={engine === e.key}>
            <b>{ENGINE[e.key] ?? e.label}</b>
            <span className="muted small">{e.focus}</span>
            <span className="bar-track wide"><span className="bar-fill" style={{ width: `${(e.found / max) * 100}%` }} /></span>
            <span className="small">{e.found} headlines · {e.queries - e.failed_queries}/{e.queries} searches</span>
          </button>
        ))}
      </div>
      <div className="deep-topics" aria-label="Headlines by topic">
        {topics.map(([t, n]) => (
          <div className="bar-row" key={t} style={{ gridTemplateColumns: "150px 1fr 36px" }}>
            <span className="bar-k">{TOPIC[t] ?? t}</span>
            <span className="bar-track"><span className="bar-fill" style={{ width: `${(n / tmax) * 100}%` }} /></span>
            <span className="bar-v">{n}</span>
          </div>
        ))}
      </div>
      <p className="muted small">
        The language models cannot read hundreds of headlines inside their token limits, so each agent receives a digest of its own engine (topic counts and
        its 24 freshest, most reliable headlines). Headlines are not verified facts.
      </p>
      <details>
        <summary>All {items.length} headlines{engine !== "all" ? ` · ${ENGINE[engine]}` : ""}</summary>
        <ul className="deep-list">
          {items.slice(0, shown).map((i) => (
            <li key={i.item_id}>
              <span className="chip">{TOPIC[i.topic] ?? i.topic}</span>
              <a href={i.link} target="_blank" rel="noreferrer noopener">{i.title}</a>
              <span className="muted small"> · {i.source}{i.age_days != null ? ` · ${i.age_days < 1 ? "today" : `${Math.round(i.age_days)}d ago`}` : ""} · {i.team === "home" ? home : i.team === "away" ? away : "both"}</span>
            </li>
          ))}
        </ul>
        {shown < items.length && <button type="button" className="ghost" onClick={() => setShown(shown + 60)}>Show more</button>}
      </details>
    </section>
  );
}
