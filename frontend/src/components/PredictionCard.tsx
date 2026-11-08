import type { AnalysisResult } from "../types";
import { Crest } from "./MatchSelector";

export default function PredictionCard({ result }: { result: AnalysisResult }) {
  const { match, prediction: p, simulation: s } = result;
  const pr = p.probabilities;
  return (
    <section className="panel result">
      <div className="matchline">
        <span className="team"><Crest src={match.home.crest} name={match.home.name} />{match.home.name}</span>
        <span className="scoreline-big" aria-label={`Predicted result ${p.predicted_score.home} to ${p.predicted_score.away}`}>
          {p.predicted_score.home}<span className="dash">–</span>{p.predicted_score.away}
        </span>
        <span className="team away"><Crest src={match.away.crest} name={match.away.name} />{match.away.name}</span>
      </div>
      <p className="muted center">Predicted result · {match.competition}</p>

      <div className="probbar" role="img" aria-label={`Home ${pr.home_win}%, draw ${pr.draw}%, away ${pr.away_win}%`}>
        <span className="p-home" style={{ flex: pr.home_win }}>{Math.round(pr.home_win)}%</span>
        <span className="p-draw" style={{ flex: pr.draw }}>{Math.round(pr.draw)}%</span>
        <span className="p-away" style={{ flex: pr.away_win }}>{Math.round(pr.away_win)}%</span>
      </div>
      <div className="probkey"><span>{match.home.name} win</span><span>Draw</span><span>{match.away.name} win</span></div>

      <div className="grid4">
        <div className="stat"><b>{p.over_under_2_5.over_2_5}% / {p.over_under_2_5.under_2_5}%</b><span>Over / under 2.5</span></div>
        <div className="stat"><b>{p.both_teams_to_score.yes}% / {p.both_teams_to_score.no}%</b><span>Both teams score, yes / no</span></div>
        <div className="stat"><b>{p.most_likely_scorelines.join(", ") || "n/a"}</b><span>Most likely scorelines</span></div>
        <div className="stat"><b>{s.avg_home_goals} – {s.avg_away_goals}</b><span>Average simulated goals</span></div>
      </div>

      <div className={`confidence ${p.confidence.level}`}>
        <div className="conf-head"><strong>Confidence: {p.confidence.level} ({p.confidence.score}/100)</strong></div>
        <p>{p.confidence.explanation}</p>
        {p.confidence.main_uncertainties.length > 0 && (
          <ul>{p.confidence.main_uncertainties.map((u) => <li key={u}>{u}</li>)}</ul>
        )}
      </div>
      {result.warnings.map((w) => <p className="warn" key={w}>{w}</p>)}
    </section>
  );
}
