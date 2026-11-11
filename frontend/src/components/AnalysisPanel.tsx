import { useState } from "react";
import type { AnalysisResult, NewsFinding } from "../types";

const List = ({ title, items }: { title: string; items: string[] }) =>
  items.length ? <><h4>{title}</h4><ul>{items.map((i) => <li key={i}>{i}</li>)}</ul></> : null;

const CLAIM: Record<string, string> = {
  confirmed_fact: "Confirmed fact", credible_report: "Credible report", opinion: "Opinion", rumour: "Rumour", unverified: "Unverified",
};
const day = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "date unknown");

function Finding({ f }: { f: NewsFinding }) {
  return (
    <li className="finding">
      <span className={`chip claim-${f.claim_type}`}>{CLAIM[f.claim_type] ?? f.claim_type}</span>{" "}
      <span>{f.summary}</span>
      {f.exact_quote && <blockquote>“{f.exact_quote}”{f.quote_verified === false && " (not found in the source text)"}</blockquote>}
      <div className="muted small">
        {f.link ? <a href={f.link} target="_blank" rel="noreferrer noopener">{f.source ?? "source"}</a> : f.source} · published {day(f.published_at)}
        {f.event_date && ` · event ${f.event_date}`} · reliability {Math.round(f.reliability_score * 100)}% · uncertainty {f.uncertainty}
        {f.relevance && ` · relevance: ${f.relevance}`}
      </div>
    </li>
  );
}

export default function AnalysisPanel({ result }: { result: AnalysisResult }) {
  const [tab, setTab] = useState<"chief" | "researcher" | "intelligence" | "quality">("chief");
  const r = result.researcher, intel = result.intelligence, p = result.prediction, plan = result.chief_plan, q = result.data_quality;
  const a = plan?.report_assessment;
  const tabs = [["chief", "Chief Predictor"], ["researcher", "Statistical Researcher"], ["intelligence", "Football Intelligence"], ["quality", "Data quality"]] as const;

  return (
    <section className="panel">
      <h3>Agent reports</h3>
      <div className="tabs" role="tablist">
        {tabs.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>

      {tab === "chief" && p && (
        <div className="prose">
          <p>{p.explanation}</p>
          {p.simulation_interpretation && <><h4>How the simulation was read</h4><p>{p.simulation_interpretation}</p></>}
          {p.scenario_commentary && <><h4>Scenarios</h4><p>{p.scenario_commentary}</p></>}
          {(p.parameter_commentary || plan?.parameter_notes) && <><h4>Parameters</h4><p>{p.parameter_commentary || plan?.parameter_notes}</p></>}
          <List title="Accepted from the other agents" items={[...(a?.accepted_conclusions ?? []), ...p.accepted_conclusions]} />
          <List title="Rejected or discounted" items={[...(a?.rejected_conclusions ?? []), ...p.rejected_conclusions]} />
          <List title="Contradictions found" items={[...(a?.contradictions ?? []), ...p.contradictions_found]} />
          <List title="Unsupported claims" items={[...(a?.unsupported_claims ?? []), ...p.unsupported_claims]} />
          <List title="Missing information" items={a?.missing_information ?? []} />
        </div>
      )}

      {tab === "researcher" && r && (
        <div className="prose">
          <h4>Key findings</h4>
          <ul>{r.key_findings.map((f, i) => (
            <li key={i}>{f.statement} <span className="muted small">[{f.source_id ?? "no source"} · {f.reliability}]</span></li>
          ))}</ul>
          {[r.home_team, r.away_team].map((tm) => (
            <div key={tm.name}>
              <h4>{tm.name}{tm.league_position ? ` (league position ${tm.league_position})` : ""}</h4>
              <p>{tm.recent_form}</p><p>{tm.home_away_performance}</p>
              {tm.rest_and_congestion && <p>Rest and fixtures: {tm.rest_and_congestion}</p>}
              <p className="muted small">Player availability: {tm.player_availability.status}. {tm.player_availability.notes}</p>
            </div>
          ))}
          <h4>Head to head</h4><p>{r.head_to_head}</p>
          {r.advanced_stats_status && <p className="muted small">Advanced statistics (xG, shots, possession): {r.advanced_stats_status}</p>}
          <p className="muted small">Data quality: {r.data_quality.level}. {r.data_quality.notes}</p>
          <List title="Uncertainties" items={r.uncertainties} />
        </div>
      )}

      {tab === "intelligence" && (
        <div className="prose">
          {!intel || intel.status === "unavailable" ? (
            <p className="muted">No usable news was available for this match, so no off-the-pitch analysis was made. {intel?.caveats.join(" ")}</p>
          ) : (
            <>
              {intel.overall_relevance && <p>{intel.overall_relevance}</p>}
              {[intel.home, intel.away].map((t) => (
                <div key={t.team}>
                  <h4>{t.team}</h4>
                  {t.summary && <p>{t.summary}</p>}
                  {t.morale_and_pressure && <p className="muted small">Morale and pressure: {t.morale_and_pressure}</p>}
                  <List title="Availability news" items={t.availability_news} />
                  {t.findings.length > 0 && <ul className="findings">{t.findings.map((f, i) => <Finding key={`${f.item_id}-${i}`} f={f} />)}</ul>}
                </div>
              ))}
              <List title="Affecting both teams" items={intel.cross_cutting} />
              <List title="Caveats" items={intel.caveats} />
            </>
          )}
          <p className="muted small">Headlines come from a news aggregator and are not independently verified. Negative news is not treated as proof of poor performance.</p>
        </div>
      )}

      {tab === "quality" && q && (
        <div className="prose">
          <p><strong>Data quality: {q.level} ({Math.round(q.score * 100)}%)</strong></p>
          <ul>{q.issues.map((i, k) => <li key={k}><span className={`chip sev-${i.severity}`}>{i.kind}</span> {i.detail}</li>)}</ul>
          <h4>Sources used</h4>
          <ul className="small">{result.sources.map((s) => (
            <li key={s.id}>{s.url ? <a href={s.url} target="_blank" rel="noreferrer noopener">{s.name}</a> : s.name}
              {" "}· reliability {s.reliability} · {s.freshness} · retrieved {new Date(s.retrieved_at).toLocaleTimeString()}{s.note ? ` · ${s.note}` : ""}</li>
          ))}</ul>
        </div>
      )}
      <p className="muted small">Models: {Object.entries(result.models).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ")}</p>
    </section>
  );
}
