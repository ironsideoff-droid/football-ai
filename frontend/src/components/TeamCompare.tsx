import type { AnalysisResult, TeamForm, ViewContext } from "../types";
import { Radar, PointsLine } from "./charts";
import { Crest } from "./MatchSelector";

const pill = (r: string) => (r === "W" ? "win" : r === "D" ? "draw" : "loss");
const ppg = (s: { played: number; won: number; drawn: number }) => (s.played ? (s.won * 3 + s.drawn) / s.played : 0);

/** Radar axes, all derived from the real recent results. Goal-based axes are scaled against the other team; the rest are absolute. */
export function radarValues(h: TeamForm, a: TeamForm): { axes: string[]; home: number[]; away: number[] } {
  const gf = [h.avg_goals_for ?? 0, a.avg_goals_for ?? 0];
  const ga = [h.avg_goals_against ?? 0, a.avg_goals_against ?? 0];
  const maxGf = Math.max(...gf, 0.1), minGa = Math.min(...ga);
  const cs = (t: TeamForm) => (t.matches_considered ? t.clean_sheets / t.matches_considered : 0);
  const sc = (t: TeamForm) => (t.matches_considered ? 1 - t.failed_to_score / t.matches_considered : 0);
  return {
    axes: ["Attack", "Defence", "Form", "Venue record", "Clean sheets", "Scores often"],
    home: [gf[0] / maxGf, (minGa + 0.2) / (ga[0] + 0.2), (h.points_per_game ?? 0) / 3, ppg(h.home) / 3, Math.min(1, cs(h) * 1.6), sc(h)],
    away: [gf[1] / maxGf, (minGa + 0.2) / (ga[1] + 0.2), (a.points_per_game ?? 0) / 3, ppg(a.away) / 3, Math.min(1, cs(a) * 1.6), sc(a)],
  };
}

function FormColumn({ form, name, cls, crest, row }: { form: TeamForm; name: string; cls: string; crest?: string | null; row?: { position: number; points: number } | null }) {
  const recent = [...form.recent_matches].sort((x, y) => y.date.localeCompare(x.date)).slice(0, 5);
  return (
    <div className={`formcol ${cls}`}>
      <header><Crest src={crest} name={name} /><strong>{name}</strong>{row && <span className="chip">#{row.position} · {row.points} pts</span>}</header>
      <div className="pills" aria-label={`Recent form, oldest to newest: ${form.sequence_oldest_to_newest.split("").join(" ")}`}>
        {form.sequence_oldest_to_newest.split("").map((r, i) => <span key={i} className={`pill ${pill(r)}`} style={{ animationDelay: `${i * 90}ms` }}>{r}</span>)}
      </div>
      <PointsLine sequence={form.sequence_oldest_to_newest} cls={cls} />
      <p className="muted small">
        {form.matches_considered} matches · {form.points_per_game?.toFixed(2) ?? "–"} points per game · scored {form.avg_goals_for?.toFixed(2) ?? "–"}, conceded {form.avg_goals_against?.toFixed(2) ?? "–"} per game
        {form.rest_days != null ? ` · ${Math.round(form.rest_days)} days rest` : ""}
      </p>
      <ul className="recent">
        {recent.map((m, i) => (
          <li key={i}><span className={`pill sm ${pill(m.result)}`}>{m.result}</span><span className="rv">{m.venue === "H" ? "home" : "away"}</span>
            <span className="ro">{m.opponent}</span><b>{m.goals_for}–{m.goals_against}</b></li>
        ))}
      </ul>
    </div>
  );
}

export default function TeamCompare({ result, ctx }: { result: AnalysisResult; ctx: ViewContext }) {
  const { match } = result;
  const hf = ctx.home_form, af = ctx.away_form, h2h = ctx.head_to_head;
  if (!hf && !af && !h2h) return null;
  const radar = hf && af && hf.matches_considered && af.matches_considered ? radarValues(hf, af) : null;
  const tot = h2h ? h2h.upcoming_home_team_wins + h2h.draws + h2h.upcoming_away_team_wins : 0;
  return (
    <section className="panel">
      <h3>Team comparison</h3>
      <p className="muted small">Everything on this card comes from the real recent results and league table supplied by the data provider.</p>
      <div className="compare">
        {radar && hf && af && (
          <div className="radarbox">
            <Radar axes={radar.axes} a={radar.home} b={radar.away} aName={match.home.name} bName={match.away.name} />
            <div className="legend"><span><i className="sw sw-home" />{match.home.name}</span><span><i className="sw sw-away" />{match.away.name}</span></div>
            <p className="muted small center">Goal axes are scaled against the other team; form, venue record, clean sheets and scoring rate are absolute.</p>
          </div>
        )}
        <div className="forms">
          {hf && <FormColumn form={hf} name={match.home.name} cls="home" crest={match.home.crest} row={ctx.standings?.home_team_overall} />}
          {af && <FormColumn form={af} name={match.away.name} cls="away" crest={match.away.crest} row={ctx.standings?.away_team_overall} />}
        </div>
      </div>
      {h2h && tot > 0 && (
        <div className="h2h">
          <h4>Head to head · last {h2h.matches_considered} meetings</h4>
          <div className="probbar mini" role="img" aria-label={`Head to head: ${match.home.name} ${h2h.upcoming_home_team_wins} wins, ${h2h.draws} draws, ${match.away.name} ${h2h.upcoming_away_team_wins} wins`}>
            <span className="p-home" style={{ flex: h2h.upcoming_home_team_wins || 0.001 }}>{h2h.upcoming_home_team_wins || ""}</span>
            <span className="p-draw" style={{ flex: h2h.draws || 0.001 }}>{h2h.draws || ""}</span>
            <span className="p-away" style={{ flex: h2h.upcoming_away_team_wins || 0.001 }}>{h2h.upcoming_away_team_wins || ""}</span>
          </div>
          <div className="probkey"><span>{match.home.name} wins</span><span>Draws</span><span>{match.away.name} wins</span></div>
          <p className="muted small">Goals in those meetings: {match.home.name} {h2h.upcoming_home_team_goals}, {match.away.name} {h2h.upcoming_away_team_goals}.</p>
        </div>
      )}
    </section>
  );
}
