import { useState } from "react";
import type { AnalysisResult } from "../types";

const List = ({ title, items }: { title: string; items: string[] }) =>
  items.length ? <><h4>{title}</h4><ul>{items.map((i) => <li key={i}>{i}</li>)}</ul></> : null;

export default function AnalysisPanel({ result }: { result: AnalysisResult }) {
  const [tab, setTab] = useState<"chief" | "researcher" | "tactical">("chief");
  const r = result.researcher, t = result.tactical, p = result.prediction, a = result.chief_plan.report_assessment;
  const avail = Object.entries(result.data_availability).filter(([, v]) => v.startsWith("unavailable"));

  return (
    <section className="panel">
      <h3>AI analysis</h3>
      <div className="tabs" role="tablist">
        {([["chief", "Chief Predictor"], ["researcher", "Researcher"], ["tactical", "Tactical Analyst"]] as const).map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>

      {tab === "chief" && (
        <div className="prose">
          <p>{p.explanation}</p>
          <h4>How the simulation was read</h4><p>{p.simulation_interpretation}</p>
          <p className="muted small">Simulation rationale: {result.simulation.params_requested.rationale}</p>
          <List title="Accepted from the other agents" items={[...a.accepted_conclusions, ...p.accepted_conclusions]} />
          <List title="Rejected or discounted" items={[...a.rejected_conclusions, ...p.rejected_conclusions]} />
          <List title="Contradictions found" items={[...a.contradictions, ...p.contradictions_found]} />
          <List title="Unsupported claims" items={[...a.unsupported_claims, ...p.unsupported_claims]} />
        </div>
      )}

      {tab === "researcher" && (
        <div className="prose">
          <List title="Key findings" items={r.key_findings} />
          {[r.home_team, r.away_team].map((tm) => (
            <div key={tm.name}>
              <h4>{tm.name}{tm.league_position ? ` (league position ${tm.league_position})` : ""}</h4>
              <p>{tm.recent_form}</p><p>{tm.home_away_performance}</p>
              <p className="muted small">Player availability: {tm.player_availability.status}. {tm.player_availability.notes}</p>
            </div>
          ))}
          <h4>Head to head</h4><p>{r.head_to_head}</p>
          <p className="muted small">Data quality: {r.data_quality.level}. {r.data_quality.notes}</p>
          <List title="Uncertainties" items={r.uncertainties} />
          {avail.length > 0 && <List title="Data that was not available" items={avail.map(([k, v]) => `${k.replace(/_/g, " ")}: ${v.replace("unavailable: ", "")}`)} />}
        </div>
      )}

      {tab === "tactical" && (
        <div className="prose">
          {[["Home", t.home_profile], ["Away", t.away_profile]].map(([side, prof]) => {
            const pf = prof as typeof t.home_profile;
            return (
              <div key={side as string}>
                <h4>{side as string}: {pf.style}{pf.formation ? ` (${pf.formation})` : ""}</h4>
                <p className="muted small">Based on: {pf.basis.replace("_", " ")}</p>
                <List title="Strengths" items={pf.strengths} /><List title="Weaknesses" items={pf.weaknesses} />
              </div>
            );
          })}
          <p>{t.offensive_analysis}</p><p>{t.defensive_analysis}</p>
          <List title="Key matchups" items={t.key_matchups} />
          <List title="Tactical advantages" items={t.tactical_advantages} />
          <h4>Match dynamics</h4><p>{t.expected_match_dynamics}</p>
          <p>Expected goals: {t.scoring_expectations.home_goals} – {t.scoring_expectations.away_goals}. {t.scoring_expectations.reasoning}</p>
          <List title="Scenarios" items={t.scenarios.map((s) => `${s.name}: ${s.description}${s.likelihood ? ` (${s.likelihood})` : ""}`)} />
          <p className="muted small">Confidence: {t.confidence.level}. {t.confidence.uncertainties.join(" ")}</p>
        </div>
      )}
      <p className="muted small">Models: {Object.entries(result.models).map(([k, v]) => `${k.replace("_", " ")} ${v}`).join(" · ")}</p>
    </section>
  );
}
