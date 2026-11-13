import { reducedMotion } from "./charts";
import type { LineupPlayer, TeamLineup } from "../types";

interface Props {
  homeName: string; awayName: string;
  homeXg: number; awayXg: number;
  score?: { home: number; away: number } | null;
  lineups?: { home: TeamLineup; away: TeamLineup } | null;
}

const rank = (pos?: string | null) => {
  const p = pos ?? "";
  if (/mid/i.test(p)) return 1;
  if (/def|back/i.test(p)) return 0;
  if (/off|att|forw|wing|strik/i.test(p)) return 2;
  return 1;
};

interface Placed { p: LineupPlayer; x: number; y: number; row: number }

/** Lays out a real starting eleven. The goalkeeper goes first; the ten outfield players keep the order of the
 *  supplied position groups and are split into rows by the supplied formation (e.g. 4-2-3-1). */
function layout(t: TeamLineup, away: boolean): Placed[] {
  const gkIdx = Math.max(0, t.starters.findIndex((p) => /goal|keeper/i.test(p.position ?? "")));
  const gk = t.starters[gkIdx];
  let field = t.starters.filter((_, i) => i !== gkIdx);
  if (field.every((p) => p.position)) field = [...field].sort((a, b) => rank(a.position) - rank(b.position));
  let rows = (t.formation ?? "").split("-").map(Number).filter((n) => n > 0);
  if (rows.length < 2 || rows.reduce((a, b) => a + b, 0) !== field.length) {
    const counts = [0, 0, 0];
    field.forEach((p) => counts[rank(p.position)]++);
    rows = counts.filter((n) => n > 0);
  }
  const out: Placed[] = [{ p: gk, x: 5.5, y: 34, row: -1 }];
  let k = 0;
  rows.forEach((n, r) => {
    const x = 16 + r * (28 / Math.max(rows.length - 1, 1));
    for (let i = 0; i < n; i++) out.push({ p: field[k++], x, y: (68 * (i + 1)) / (n + 1), row: r });
  });
  return out.map((o) => (away ? { ...o, x: 105 - o.x, y: 68 - o.y } : o));
}

const surname = (n: string) => {
  const parts = n.trim().split(/\s+/);
  return parts.length > 1 && parts[parts.length - 1].length > 2 ? parts[parts.length - 1] : n;
};

function Players({ t, away }: { t: TeamLineup; away: boolean }) {
  const placed = layout(t, away);
  const lines: [Placed, Placed][] = [];
  for (const a of placed) {
    const next = placed.filter((b) => b.row === a.row + 1);
    if (!next.length) continue;
    const near = next.reduce((m, b) => (Math.abs(b.y - a.y) < Math.abs(m.y - a.y) ? b : m));
    lines.push([a, near]);
  }
  return (
    <g className={away ? "team-away" : "team-home"}>
      {lines.map(([a, b], i) => <line key={i} className="passline" x1={a.x} y1={a.y} x2={b.x} y2={b.y} />)}
      {placed.map((o, i) => (
        <g key={i} transform={`translate(${o.x} ${o.y})`}>
          <g className="player" style={{ animationDelay: `${(i * 0.37) % 3}s` }}>
            <circle className="player-halo" r="3.2" style={{ animationDelay: `${(i * 0.23) % 2}s` }} />
            <circle className="player-dot" r="2.25" />
            <text className="player-num" y="0.8" textAnchor="middle">{o.p.shirt_number ?? ""}</text>
            <text className="player-name" y="5.4" textAnchor="middle">{surname(o.p.name)}</text>
          </g>
        </g>
      ))}
    </g>
  );
}

export default function MatchPitch({ homeName, awayName, homeXg, awayXg, score, lineups }: Props) {
  const calm = reducedMotion();
  const glow = (xg: number) => 5 + Math.min(xg, 4) * 3.2;
  return (
    <figure className="pitchfig">
      <div className="pitchscroll">
      <svg className="pitch" viewBox="-3 -3 111 74" role="img"
        aria-label={`Football pitch. ${homeName} expected goals ${homeXg.toFixed(2)}, ${awayName} ${awayXg.toFixed(2)}.${lineups ? " Confirmed lineups shown." : ""}`}>
        <defs>
          <radialGradient id="hglow"><stop offset="0" stopColor="var(--amber)" stopOpacity=".55" /><stop offset="1" stopColor="var(--amber)" stopOpacity="0" /></radialGradient>
          <radialGradient id="aglow"><stop offset="0" stopColor="var(--teal)" stopOpacity=".55" /><stop offset="1" stopColor="var(--teal)" stopOpacity="0" /></radialGradient>
          <linearGradient id="turf" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#0b3a3a" /><stop offset="1" stopColor="#07283a" /></linearGradient>
          <clipPath id="pclip"><rect x="0" y="0" width="105" height="68" rx="1.2" /></clipPath>
        </defs>
        <rect x="-3" y="-3" width="111" height="74" rx="4" className="pitch-frame" />
        <g clipPath="url(#pclip)">
          <rect width="105" height="68" fill="url(#turf)" />
          {Array.from({ length: 10 }).map((_, i) => <rect key={i} className="stripe" x={i * 10.5} width="10.5" height="68" opacity={i % 2 ? 0.07 : 0} />)}
          <rect className="scan" x="0" width="14" height="68" />
        </g>
        <g className="lines">
          <rect x="0" y="0" width="105" height="68" rx="1.2" />
          <line x1="52.5" y1="0" x2="52.5" y2="68" />
          <circle cx="52.5" cy="34" r="9.15" /><circle className="spot" cx="52.5" cy="34" r="0.5" />
          <rect x="0" y="13.84" width="16.5" height="40.32" /><rect x="88.5" y="13.84" width="16.5" height="40.32" />
          <rect x="0" y="24.84" width="5.5" height="18.32" /><rect x="99.5" y="24.84" width="5.5" height="18.32" />
          <circle className="spot" cx="11" cy="34" r="0.5" /><circle className="spot" cx="94" cy="34" r="0.5" />
          <path d="M16.5 26.69 A9.15 9.15 0 0 1 16.5 41.31" /><path d="M88.5 26.69 A9.15 9.15 0 0 0 88.5 41.31" />
          <rect x="-1.6" y="30.34" width="1.6" height="7.32" /><rect x="105" y="30.34" width="1.6" height="7.32" />
        </g>

        {/* expected-goal threat from the simulation: each side's pressure builds towards the opponent's goal */}
        <ellipse className="threat" cx="91" cy="34" rx={glow(homeXg)} ry={glow(homeXg) * 1.15} fill="url(#hglow)" />
        <ellipse className="threat" cx="14" cy="34" rx={glow(awayXg)} ry={glow(awayXg) * 1.15} fill="url(#aglow)" style={{ animationDelay: "1.2s" }} />
        {!lineups && (
          <g className="arrows">
            <path className="arrow arrow-h" d={`M${60} 34 H${60 + Math.min(homeXg, 3.5) * 8}`} />
            <path className="arrow arrow-a" d={`M${45} 34 H${45 - Math.min(awayXg, 3.5) * 8}`} />
          </g>
        )}

        {lineups ? (<><Players t={lineups.home} away={false} /><Players t={lineups.away} away /></>) : (
          <>
            <text className="xg-big xg-h" x="26" y="26" textAnchor="middle">{homeXg.toFixed(2)}</text>
            <text className="xg-cap" x="26" y="31" textAnchor="middle">expected goals</text>
            <text className="xg-big xg-a" x="79" y="26" textAnchor="middle">{awayXg.toFixed(2)}</text>
            <text className="xg-cap" x="79" y="31" textAnchor="middle">expected goals</text>
          </>
        )}

        {score && (
          <g className="scorebadge">
            <circle cx="52.5" cy="34" r="5.8" />
            <text x="52.5" y="35.9" textAnchor="middle">{score.home}–{score.away}</text>
          </g>
        )}
        {!calm && (
          <g className="ball" aria-hidden>
            <circle r="1.25" />
            <animateMotion dur="9s" repeatCount="indefinite" path="M14 44 C30 18 42 52 58 30 S 84 18 92 36 S 66 54 40 46 S 20 50 14 44" />
          </g>
        )}
        <text className="side-name" x="2" y="-0.8">{homeName}{lineups?.home.formation ? ` · ${lineups.home.formation}` : ""}</text>
        <text className="side-name side-away" x="103" y="-0.8" textAnchor="end">{awayName}{lineups?.away.formation ? ` · ${lineups.away.formation}` : ""}</text>
      </svg>
      </div>
      <figcaption className="muted small">
        {lineups
          ? "Confirmed starting elevens from the data provider. Rows follow the supplied formation and position groups; exact positions on the pitch are approximate."
          : "No confirmed lineups are available for this match from the data provider, so no players are drawn and none are guessed. The glow shows each team’s simulated scoring threat towards the opposite goal."}
      </figcaption>
    </figure>
  );
}
