import type { AnalysisResult, TeamAdvanced, ViewContext } from "../types";

const STATUS: Record<string, string> = { injured: "Injured", suspended: "Suspended", doubtful: "Doubtful", unknown: "Reported" };

function Row({ label, h, a, digits = 2, suffix = "" }: { label: string; h?: number | null; a?: number | null; digits?: number; suffix?: string }) {
  if (h == null && a == null) return null;
  const hv = h ?? 0, av = a ?? 0, tot = hv + av || 1;
  return (
    <div className="scout-row">
      <b>{h != null ? h.toFixed(digits) + suffix : "–"}</b>
      <div><span className="muted small">{label}</span>
        <div className="scout-bar"><i className="h" style={{ width: `${(hv / tot) * 100}%` }} /><i className="a" style={{ width: `${(av / tot) * 100}%` }} /></div></div>
      <b>{a != null ? a.toFixed(digits) + suffix : "–"}</b>
    </div>
  );
}

const src = (x?: TeamAdvanced | null) => (x ? `${x.source} (last ${x.matches})` : "not found");

/** Everything the Data Scout (agent 4) found, with its source, and an honest list of what it did not find. */
export default function ScoutPanel({ result, ctx }: { result: AnalysisResult; ctx: ViewContext }) {
  const h = ctx.home_advanced, a = ctx.away_advanced, abs = ctx.absences ?? [];
  const av = result.data_availability ?? {};
  if (!h && !a && !abs.length) {
    return (
      <section className="panel scout">
        <h3>Agent 4 · Data Scout</h3>
        <p className="muted small">No scraped data was found for this match. {["injuries_suspensions", "expected_goals_xg", "advanced_stats", "lineups_formations"].map((k) => av[k]).filter(Boolean).join(" · ")}</p>
      </section>
    );
  }
  const side = (s: "home" | "away") => abs.filter((x) => x.team === s);
  return (
    <section className="panel scout">
      <h3>Agent 4 · Data Scout</h3>
      {(h || a) && (
        <>
          <div className="scout-head"><strong>{result.match.home.name}</strong><span className="muted small">recent matches</span><strong>{result.match.away.name}</strong></div>
          <Row label="Expected goals for (xG)" h={h?.xg_for_avg} a={a?.xg_for_avg} />
          <Row label="Expected goals against (xGA)" h={h?.xg_against_avg} a={a?.xg_against_avg} />
          <Row label="Shots" h={h?.shots_avg} a={a?.shots_avg} digits={1} />
          <Row label="Shots on target" h={h?.shots_on_target_avg} a={a?.shots_on_target_avg} digits={1} />
          <Row label="Possession" h={h?.possession_avg} a={a?.possession_avg} digits={0} suffix="%" />
          <p className="muted small">Sources: {result.match.home.name} {src(h)} · {result.match.away.name} {src(a)}. Scraped averages, not estimates.</p>
        </>
      )}
      {abs.length > 0 && (
        <div className="scout-abs">
          {(["home", "away"] as const).map((s) => (
            <div key={s}>
              <strong>{s === "home" ? result.match.home.name : result.match.away.name}</strong>
              {side(s).length === 0 ? <p className="muted small">No absences found.</p> : (
                <ul>{side(s).map((x, i) => (
                  <li key={i}><span className={`chip st-${x.status}`}>{STATUS[x.status]}</span> {x.player}{x.detail ? ` – ${x.detail}` : ""}
                    {x.url && <> <a href={x.url} target="_blank" rel="noreferrer noopener" className="small">{x.source}</a></>}</li>
                ))}</ul>
              )}
            </div>
          ))}
          <p className="muted small">Absences come from web pages found by the research agent. They are unverified until confirmed by the clubs.</p>
        </div>
      )}
    </section>
  );
}
