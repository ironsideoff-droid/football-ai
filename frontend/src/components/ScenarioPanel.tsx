import type { ScenarioResult } from "../types";

const sign = (v: number) => `${v >= 0 ? "+" : ""}${Math.round(v * 100)}%`;

export default function ScenarioPanel({ scenarios, home, away }: { scenarios: ScenarioResult[]; home: string; away: string }) {
  const ordered = [...scenarios].sort((a, b) => b.assigned_probability_pct - a.assigned_probability_pct);
  return (
    <section className="panel">
      <h3>Scenarios</h3>
      <p className="muted">
        Each simulated match belongs to exactly one scenario, in proportion to its estimated probability. Scenario effects change the
        scoring rates only; red cards and goal timing are simulated separately.
      </p>
      <div className="scenarios">
        {ordered.map((s, i) => (
          <article className="scenario" key={`${s.name}-${i}`}>
            <header>
              <h4>{s.name}</h4>
              <span className="chip">{s.assigned_probability_pct}% · {s.simulated_n.toLocaleString()} sims</span>
            </header>
            <div className="bar-track wide"><span className="bar-fill" style={{ width: `${s.assigned_probability_pct}%` }} /></div>
            <p>{s.description}</p>
            {s.impact_on_teams && <p className="muted small">Impact: {s.impact_on_teams}</p>}
            {(s.home_log_effect !== 0 || s.away_log_effect !== 0) && (
              <p className="muted small">Scoring rate change: {home} {sign(s.home_log_effect)}, {away} {sign(s.away_log_effect)} · basis: {s.evidence}</p>
            )}
            <div className="probbar mini" role="img" aria-label={`In this scenario: home ${s.home_win_pct}%, draw ${s.draw_pct}%, away ${s.away_win_pct}%`}>
              <span className="p-home" style={{ flex: s.home_win_pct }}>{Math.round(s.home_win_pct)}%</span>
              <span className="p-draw" style={{ flex: s.draw_pct }}>{Math.round(s.draw_pct)}%</span>
              <span className="p-away" style={{ flex: s.away_win_pct }}>{Math.round(s.away_win_pct)}%</span>
            </div>
            <p className="muted small">
              Goals {s.avg_home_goals} – {s.avg_away_goals} · both score {s.btts_pct}% · over 2.5 {s.over_2_5_pct}% · likely scores:{" "}
              {s.top_scorelines.slice(0, 3).map((x) => `${x.score} (${x.pct}%)`).join(", ")}
            </p>
            {s.assumptions.length > 0 && <ul className="small">{s.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>}
            {s.flags.map((f) => <p className="warn" key={f}>{f}</p>)}
          </article>
        ))}
      </div>
    </section>
  );
}
