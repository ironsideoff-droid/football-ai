import { useMemo, useState } from "react";
import type { MatchSummary } from "../types";

interface Props {
  matches: MatchSummary[];
  selectedId: number | null;
  onSelect: (m: MatchSummary) => void;
  disabled?: boolean;
}

export function Crest({ src, name }: { src?: string | null; name: string }) {
  return src ? <img className="crest" src={src} alt="" loading="lazy" /> : <span className="crest crest-empty">{name.slice(0, 1)}</span>;
}

const fmt = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

export default function MatchSelector({ matches, selectedId, onSelect, disabled }: Props) {
  const [comp, setComp] = useState("all");
  const comps = useMemo(() => Array.from(new Set(matches.map((m) => m.competition ?? "Other"))), [matches]);
  const shown = matches.filter((m) => comp === "all" || (m.competition ?? "Other") === comp);

  if (!matches.length) return <p className="muted">No fixtures found for the next days. Try again later.</p>;
  return (
    <div>
      <label className="field">
        <span>Competition</span>
        <select value={comp} onChange={(e) => setComp(e.target.value)} disabled={disabled}>
          <option value="all">All competitions</option>
          {comps.map((c) => <option key={c}>{c}</option>)}
        </select>
      </label>
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
              <span className="when">{fmt(m.utc_date)}</span>
              <span className="side home"><span>{m.home.short_name ?? m.home.name}</span><Crest src={m.home.crest} name={m.home.name} /></span>
              <span className="vs">v</span>
              <span className="side away"><Crest src={m.away.crest} name={m.away.name} /><span>{m.away.short_name ?? m.away.name}</span></span>
              <span className="comp">{m.competition}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
