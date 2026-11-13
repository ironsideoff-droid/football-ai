import { useState } from "react";
import { getEvaluation, refreshResults, ApiError } from "../services/api";
import type { EvalReport, Evaluation } from "../types";
import { CalibrationChart } from "./charts";

const pct = (v?: number) => (v === undefined || v === null ? "–" : `${(v * 100).toFixed(1)}%`);
const num = (v?: number) => (v === undefined || v === null ? "–" : v.toFixed(3));

function Row({ label, r }: { label: string; r: EvalReport | null }) {
  if (!r || !r.n) return <tr><th scope="row">{label}</th><td colSpan={4} className="muted">no finished matches yet</td></tr>;
  return (
    <tr><th scope="row">{label}</th><td>{r.n}</td><td>{pct(r.outcome_1x2?.accuracy)}</td><td>{num(r.outcome_1x2?.brier)}</td><td>{num(r.outcome_1x2?.log_loss)}</td></tr>
  );
}

export default function EvaluationPanel() {
  const [data, setData] = useState<Evaluation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async (refresh: boolean) => {
    setBusy(true); setError(null);
    try {
      if (refresh) await refreshResults();
      setData(await getEvaluation());
    } catch (e) { setError((e as ApiError).message); }
    setBusy(false);
  };

  return (
    <details className="panel" onToggle={(e) => { if ((e.target as HTMLDetailsElement).open && !data) void load(false); }}>
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
          {data.chief_predictor.calibration && data.chief_predictor.calibration.length > 1 && (
            <div className="chartbox narrow">
              <h4>Calibration</h4>
              <CalibrationChart bins={data.chief_predictor.calibration} />
              <p className="muted small">Points on the dashed line mean that when the model said 70%, it happened about 70% of the time.</p>
            </div>
          )}
          <p className="muted small">{data.storage_note}</p>
        </>
      )}
      <button className="analyze secondary" disabled={busy} onClick={() => void load(true)}>{busy ? "Checking…" : "Check finished matches"}</button>
    </details>
  );
}
