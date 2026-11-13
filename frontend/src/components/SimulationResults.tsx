import type { Simulation } from "../types";
import { Columns, XgBars } from "./charts";

function ScoreGrid({ matrix, home, away }: { matrix: number[][]; home: string; away: string }) {
  const max = Math.max(...matrix.flat(), 1);
  const label = (i: number) => (i === matrix.length - 1 ? `${i}+` : String(i));
  return (
    <div className="gridwrap">
      <table className="scoregrid" aria-label={`Score probabilities: rows ${home} goals, columns ${away} goals`}>
        <thead><tr><th scope="col" className="corner">{home} ↓ · {away} →</th>{matrix[0].map((_, j) => <th key={j} scope="col">{label(j)}</th>)}</tr></thead>
        <tbody>
          {matrix.map((row, i) => (
            <tr key={i}>
              <th scope="row">{label(i)}</th>
              {row.map((v, j) => (
                <td key={j} style={{ background: `color-mix(in srgb, var(--amber) ${Math.round((v / max) * 85)}%, transparent)` }}
                    title={`${label(i)}-${label(j)}: ${v}%`}>{v >= 1 ? Math.round(v) : v > 0 ? "<1" : ""}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const NAMES: Record<string, string> = {
  home_xg: "Home expected goals", away_xg: "Away expected goals", g: "League goals per team", home_attack: "Home attack",
  home_goals_conceded: "Home goals conceded", away_attack: "Away attack", away_goals_conceded: "Away goals conceded", home_advantage: "Home advantage",
};

/** Eight columns: 0..6 goals and "7+" (everything from 7 up, so the tail is never dropped). */
const fold = (v: number[]) => [...Array.from({ length: 7 }, (_, i) => v[i] ?? 0), Math.round(v.slice(7).reduce((a, b) => a + b, 0) * 10) / 10];

export default function SimulationResults({ sim, home, away }: { sim: Simulation; home: string; away: string }) {
  const ev = sim.events;
  const se = sim.uncertainty.monte_carlo_standard_error_pct;
  const model = sim.config?.model ?? "poisson";
  return (
    <section className="panel">
      <h3>Simulation</h3>
      <p className="sim-head"><strong>{sim.n_simulations.toLocaleString()} simulations completed</strong> · seed {sim.seed} · goal model: {model}</p>
      <p className="muted small">{sim.model}</p>

      <h4>Score probabilities</h4>
      <ScoreGrid matrix={sim.score_matrix_pct} home={home} away={away} />
      <p className="muted small">
        Most frequent: {sim.top_scorelines.slice(0, 5).map((s) => `${s.score} (${s.pct}%)`).join(", ")}. Clean sheets: {home} {sim.home_clean_sheet_pct}%,
        {" "}{away} {sim.away_clean_sheet_pct}%. Over 1.5 / 2.5 / 3.5: {sim.over_1_5_pct}% / {sim.over_2_5_pct}% / {sim.over_3_5_pct}%.
      </p>

      <div className="chartgrid">
      <div>
      <h4>Goals scored</h4>
      <div className="chartbox">
        <Columns labels={["0", "1", "2", "3", "4", "5", "6", "7+"]}
          series={[{ name: home, values: fold(sim.goal_distribution.home), cls: "c-home" }, { name: away, values: fold(sim.goal_distribution.away), cls: "c-away" }]} />
        <div className="legend"><span><i className="sw sw-home" />{home}</span><span><i className="sw sw-away" />{away}</span></div>
      </div>
      </div>
      <div>
      <h4>Total goals in the match</h4>
      <div className="chartbox"><Columns labels={["0", "1", "2", "3", "4", "5", "6", "7+"]} series={[{ name: "Total goals", values: fold(sim.goal_distribution.total), cls: "c-total" }]} /></div>
      </div>
      </div>
      <h4>Expected goals</h4>
      <XgBars rows={[
        { name: home, value: sim.input_xg_home, lo: sim.uncertainty.xg_home_90pct_interval[0], hi: sim.uncertainty.xg_home_90pct_interval[1], cls: "c-home" },
        { name: away, value: sim.input_xg_away, lo: sim.uncertainty.xg_away_90pct_interval[0], hi: sim.uncertainty.xg_away_90pct_interval[1], cls: "c-away" }]} />

      <h4>Parameter uncertainty</h4>
      <p className="muted small">Every simulation draws its own parameters, so the outcome percentages already include this uncertainty.</p>
      <table className="ptable">
        <thead><tr><th scope="col">Parameter</th><th scope="col">Average</th><th scope="col">90% range</th></tr></thead>
        <tbody>
          {Object.entries(sim.parameter_uncertainty).map(([k, v]) => (
            <tr key={k}><th scope="row">{NAMES[k] ?? k}</th><td>{v.mean}</td><td>{v.p05} – {v.p95}</td></tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">Sampling error of the home / draw / away percentages: ±{se.home} / ±{se.draw} / ±{se.away} points (one standard error).</p>

      {sim.applied_adjustments.length > 0 && (
        <>
          <h4>Adjustments proposed by the Chief Predictor</h4>
          <ul className="adjust">
            {sim.applied_adjustments.map((a, i) => (
              <li key={i}>
                <span className={`chip ${a.status}`}>{a.status}</span> {a.target.replace(/_/g, " ")} {a.applied_log_effect >= 0 ? "+" : ""}{Math.round(a.applied_log_effect * 100)}%
                {" "}({a.evidence}): {a.justification}{a.reason && <em> · {a.reason}</em>}
              </li>
            ))}
          </ul>
        </>
      )}

      <h4>Match events (simulated explicitly)</h4>
      <p className="muted small">
        Red card: {home} {ev.home_red_card_pct}%, {away} {ev.away_red_card_pct}% · goal before 30 min {ev.goal_before_30_min_pct}% · goal after 75 min {ev.goal_after_75_min_pct}% ·
        half-time: {home} ahead {ev.home_leads_ht_pct}%, level {ev.draw_ht_pct}%, {away} ahead {ev.away_leads_ht_pct}%.
      </p>
      {sim.warnings.map((w) => <p className="warn" key={w}>{w}</p>)}
      <p className="muted small">{sim.disclaimer}</p>
    </section>
  );
}
