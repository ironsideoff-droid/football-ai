import { useEffect, useState } from "react";
import { getEvaluation, ApiError } from "../services/api";
import type { EvalReport, Evaluation } from "../types";

const pct = (v?: number) => (v === undefined || v === null ? "–" : `${(v * 100).toFixed(1)}%`);
const num = (v?: number) => (v === undefined || v === null ? "–" : v.toFixed(3));

function Row({ label, r }: { label: string; r: EvalReport | null }) {
  if (!r || !r.n) return <tr><th scope="row">{label}</th><td colSpan={4} className="muted">no finished matches yet</td></tr>;
  return (
    <tr><th scope="row">{label}</th><td>{r.n}</td><td>{pct(r.outcome_1x2?.accuracy)}</td><td>{num(r.outcome_1x2?.brier)}</td><td>{num(r.outcome_1x2?.log_loss)}</td></tr>
  );
}

export default function EvaluationPanel({ defaultOpen = false }: { defaultOpen?: boolean }) {
  const [data, setData] = useState<Evaluation | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setError(null);
    try { setData(await getEvaluation()); } catch (e) { setError((e as ApiError).message); }
  };
  useEffect(() => { if (defaultOpen) void load(); }, [defaultOpen]);

  return (
    <details className="panel" open={defaultOpen || undefined} onToggle={(e) => { if ((e.target as HTMLDetailsElement).open && !data) void load(); }}>
      <summary>Track record</summary>
      {error && <p className="warn">{error}</p>}
      {data && (
        <>
          <p className="muted">{data.stored_predictions} stored predictions · {data.evaluated} finished and evaluated. Stored predictions are never edited.</p>
          {data.tampered_match_ids.length > 0 && <p className="warn">Stored records failed their integrity check for matches: {data.tampered_match_ids.join(", ")}.</p>}
          {data.chief_predictor.sample_warning && <p className="warn">{data.chief_predictor.sample_warning}</p>}
          <table className="ptable">
            <thead><tr><th scope="col">1X2 forecast</th><th scope="col">Matches</th><th scope="col">Accuracy</th><th scope="col">Brier</th><th scope="col">Log loss</th></tr></thead>
            <tbody>
              <Row label="Chief Predictor" r={data.chief_predictor} />
              <Row label="Simulation only" r={data.simulation_only} />
              <Row label="Always the league base rates" r={data.baseline_constant_frequencies} />
            </tbody>
          </table>
          {data.chief_predictor.n > 0 && (
            <p className="muted small">
              Goal error (average): home {data.chief_predictor.goals_mae?.home.toFixed(2)}, away {data.chief_predictor.goals_mae?.away.toFixed(2)} ·
              both teams to score {pct(data.chief_predictor.btts?.accuracy)} · over/under 2.5 {pct(data.chief_predictor.over_2_5?.accuracy)} ·
              exact score {pct(data.chief_predictor.exact_score?.accuracy)}. {data.chief_predictor.roi?.available === false && "Betting return: not available (no odds recorded)."}
            </p>
          )}
          <p className="muted small">{data.storage_note}</p>
        </>
      )}
    </details>
  );
}
