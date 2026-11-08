import type { Simulation } from "../types";

function Bars({ title, values }: { title: string; values: number[] }) {
  const max = Math.max(...values, 1);
  return (
    <div className="dist">
      <h4>{title}</h4>
      {values.slice(0, 8).map((v, goals) => (
        <div className="bar-row" key={goals}>
          <span className="bar-k">{goals}{goals === 7 && values.length > 8 ? "+" : ""}</span>
          <span className="bar-track"><span className="bar-fill" style={{ width: `${(v / max) * 100}%` }} /></span>
          <span className="bar-v">{v}%</span>
        </div>
      ))}
    </div>
  );
}

export default function SimulationResults({ sim, home, away }: { sim: Simulation; home: string; away: string }) {
  const p = sim.params_applied;
  return (
    <section className="panel">
      <h3>Simulation results</h3>
      <p className="muted">
        {sim.n_simulations.toLocaleString()} simulated matches. Expected goals used: {home} {p.home_xg}, {away} {p.away_xg}
        (uncertainty {p.uncertainty_sigma}, correlation {p.goal_correlation}).
      </p>
      <h4>Most frequent scorelines</h4>
      <div className="scorelines">
        {sim.top_scorelines.slice(0, 6).map((s) => (
          <div className="scoreline" key={s.score}><strong>{s.score}</strong><span>{s.pct}%</span><small>{s.count} of {sim.n_simulations.toLocaleString()}</small></div>
        ))}
      </div>
      <div className="dists">
        <Bars title={`${home} goals`} values={sim.goal_distribution.home} />
        <Bars title={`${away} goals`} values={sim.goal_distribution.away} />
        <Bars title="Total goals" values={sim.goal_distribution.total} />
      </div>
      <p className="muted small">Simulated clean sheets: {home} {sim.home_clean_sheet_pct}%, {away} {sim.away_clean_sheet_pct}%. Over 1.5: {sim.over_1_5_pct}%, over 3.5: {sim.over_3_5_pct}%.</p>
      {sim.param_warnings.map((w) => <p className="warn" key={w}>{w}</p>)}
      <p className="muted small">{sim.disclaimer}</p>
    </section>
  );
}
