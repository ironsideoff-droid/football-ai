import type { AnalysisResult } from "../types";
import { CountUp, ProbabilityRing } from "./charts";
import { Crest } from "./MatchSelector";

const when = (iso?: string) => (iso ? new Date(iso).toLocaleString() : "");

function recordNote(r: AnalysisResult["prediction_record"]): string | null {
  switch (r.status) {
    case "saved": return `Saved ${when(r.created_at)}. A stored prediction is never changed.`;
    case "already_exists": return r.note ?? "An earlier prediction for this match is already stored and stays unchanged.";
    case "not_saved": return r.reason ?? "Not stored.";
    case "storage_error": return `Could not store the prediction: ${r.reason ?? "unknown error"}`;
    default: return null;
  }
}

export default function PredictionCard({ result }: { result: AnalysisResult }) {
  const { match, prediction: p, simulation: s } = result;
  if (!p || !s) return null;
  const pr = p.probabilities;
  const note = recordNote(result.prediction_record);
  return (
    <section className="panel result glowframe">
      <div className="matchline">
        <span className="team"><Crest src={match.home.crest} name={match.home.name} />{match.home.name}</span>
        <span className="scoreline-big" aria-label={`Most likely result ${p.predicted_score.home} to ${p.predicted_score.away}`}>
          <span className="digit">{p.predicted_score.home}</span><span className="dash">–</span><span className="digit" style={{ animationDelay: ".12s" }}>{p.predicted_score.away}</span>
        </span>
        <span className="team away"><Crest src={match.away.crest} name={match.away.name} />{match.away.name}</span>
      </div>
      <p className="muted center">Most likely score · {match.competition}</p>

      <div className="ringrow">
        <ProbabilityRing home={pr.home_win} draw={pr.draw} away={pr.away_win} homeName={match.home.name} awayName={match.away.name} />
        <ul className="ringkey">
          <li><i className="sw sw-home" /><span>{match.home.name} win</span><b><CountUp value={pr.home_win} suffix="%" /></b></li>
          <li><i className="sw sw-draw" /><span>Draw</span><b><CountUp value={pr.draw} suffix="%" /></b></li>
          <li><i className="sw sw-away" /><span>{match.away.name} win</span><b><CountUp value={pr.away_win} suffix="%" /></b></li>
        </ul>
      </div>

      <div className="grid4">
        <div className="stat"><b><CountUp value={s.avg_home_goals} decimals={2} /> – <CountUp value={s.avg_away_goals} decimals={2} /></b><span>Expected goals (simulated average)</span></div>
        <div className="stat"><b><CountUp value={s.home_scores_pct} />% / <CountUp value={s.away_scores_pct} />%</b><span>Home scores / away scores</span></div>
        <div className="stat"><b><CountUp value={p.both_teams_to_score.yes} />%</b><span>Both teams to score</span></div>
        <div className="stat"><b><CountUp value={p.over_under_2_5.over_2_5} />% / <CountUp value={p.over_under_2_5.under_2_5} />%</b><span>Over / under 2.5 goals</span></div>
      </div>
      <p className="muted small">Likely scorelines: {p.most_likely_scorelines.join(", ") || s.top_scorelines.slice(0, 3).map((x) => x.score).join(", ")}</p>

      <div className={`confidence ${p.confidence.level}`}>
        <div className="conf-head"><strong>Confidence: {p.confidence.level} ({p.confidence.score}/100)</strong></div>
        <div className="meter" role="img" aria-label={`Confidence ${p.confidence.score} out of 100`}><span style={{ width: `${Math.max(2, Math.min(100, p.confidence.score))}%` }} /></div>
        <p>{p.confidence.explanation}</p>
        <p className="muted small">
          Parameter uncertainty: home expected goals {s.uncertainty.xg_home_90pct_interval[0]}–{s.uncertainty.xg_home_90pct_interval[1]},
          away {s.uncertainty.xg_away_90pct_interval[0]}–{s.uncertainty.xg_away_90pct_interval[1]} (90% range across simulations).
        </p>
        {p.confidence.main_uncertainties.length > 0 && <ul>{p.confidence.main_uncertainties.map((u) => <li key={u}>{u}</li>)}</ul>}
      </div>
      {result.warnings.map((w) => <p className="warn" key={w}>{w}</p>)}
      {note && <p className="muted small">{note}</p>}
    </section>
  );
}
