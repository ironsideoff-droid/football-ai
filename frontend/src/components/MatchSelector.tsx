import { useEffect, useMemo, useState } from "react";
import type { MatchSummary } from "../types";

interface Props {
  matches: MatchSummary[];
  selectedId: number | null;
  onSelect: (m: MatchSummary) => void;
  disabled?: boolean;
  days: number;
  onDaysChange: (d: number) => void;
}

export function Crest({ src, name }: { src?: string | null; name: string }) {
  return src ? <img className="crest" src={src} alt="" loading="lazy" /> : <span className="crest crest-empty">{name.slice(0, 1)}</span>;
}

function countdown(iso: string, now: number): string {
  const ms = new Date(iso).getTime() - now;
  if (ms <= 0) return "kick-off";
  const s = Math.floor(ms / 1000), d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  const pad = (n: number) => String(n).padStart(2, "0");
  return d > 0 ? `${d}d ${pad(h)}h ${pad(m)}m` : `${pad(h)}:${pad(m)}:${pad(s % 60)}`;
}

const fmt = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

export default function MatchSelector({ matches, selectedId, onSelect, disabled, days, onDaysChange }: Props) {
  const [comp, setComp] = useState("all");
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(t); }, []);
  const comps = useMemo(() => Array.from(new Set(matches.map((m) => m.competition ?? "Other"))), [matches]);
  const shown = matches.filter((m) => comp === "all" || (m.competition ?? "Other") === comp);

  const picker = (
    <div className="filters">
      <label className="field">
        <span>Show matches in the next</span>
        <select value={days} onChange={(e) => onDaysChange(Number(e.target.value))} disabled={disabled}>
          {[7, 14, 21, 30].map((d) => <option key={d} value={d}>{d} days</option>)}
        </select>
      </label>
      {matches.length > 0 && (
        <label className="field">
          <span>Competition</span>
          <select value={comp} onChange={(e) => setComp(e.target.value)} disabled={disabled}>
            <option value="all">All competitions</option>
            {comps.map((c) => <option key={c}>{c}</option>)}
          </select>
        </label>
      )}
    </div>
  );

  if (!matches.length) return <div>{picker}<p className="muted">No fixtures in the next {days} days. Try a longer period.</p></div>;
  return (
    <div>
      {picker}
      <ul className="fixtures" role="listbox" aria-label="Fixtures">
        {shown.map((m) => (
          <li key={m.id}>
            <button
              className={`fixture ${m.id === selectedId ? "selected" : ""}`}
              role="option"
              aria-selected={m.id === selectedId}
              disabled={disabled}
              onClick={() => onSelect(m)}
            >
              <span className="when">{fmt(m.utc_date)}<small className="cd">{countdown(m.utc_date, now)}</small></span>
              <span className="side home"><span>{m.home.short_name ?? m.home.name}</span><Crest src={m.home.crest} name={m.home.name} /></span>
              <span className="vs">v</span>
              <span className="side away"><Crest src={m.away.crest} name={m.away.name} /><span>{m.away.short_name ?? m.away.name}</span></span>
              <span className="comp">{m.competition}</span>
            </button>
          </li>
        ))}
      </ul>
      <p className="muted small">
        International matches appear here when a World Cup or European Championship match is scheduled. The free football data plan has no other
        international fixtures (no Nations League, qualifiers or friendlies).
      </p>
    </div>
  );
}
