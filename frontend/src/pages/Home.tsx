import { useCallback, useEffect, useRef, useState } from "react";
import AgentStatus from "../components/AgentStatus";
import AnalysisPanel from "../components/AnalysisPanel";
import MatchSelector from "../components/MatchSelector";
import PredictionCard from "../components/PredictionCard";
import EvaluationPanel from "../components/EvaluationPanel";
import MatchPitch from "../components/MatchPitch";
import TeamCompare from "../components/TeamCompare";
import ScenarioPanel from "../components/ScenarioPanel";
import SimulationResults from "../components/SimulationResults";
import { ApiError, getFixtures, getHealth, getJob, startAnalysis } from "../services/api";
import type { AnalysisResult, Health, MatchSummary, Step } from "../types";

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [matches, setMatches] = useState<MatchSummary[]>([]);
  const [loadingFixtures, setLoadingFixtures] = useState(true);
  const [days, setDays] = useState(14);
  const [selected, setSelected] = useState<MatchSummary | null>(null);
  const [steps, setSteps] = useState<Step[] | null>(null);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fixtureError, setFixtureError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    getHealth().then(setHealth).catch((e: ApiError) => setFixtureError(e.message));
    return () => { if (timer.current) window.clearTimeout(timer.current); };
  }, []);

  useEffect(() => {
    let current = true;
    setLoadingFixtures(true); setFixtureError(null);
    getFixtures(days)
      .then((m) => { if (current) { setMatches(m); setSelected((s) => (s && m.some((x) => x.id === s.id) ? s : null)); } })
      .catch((e: ApiError) => { if (current) setFixtureError(e.message); })
      .finally(() => { if (current) setLoadingFixtures(false); });
    return () => { current = false; };
  }, [days]);

  const poll = useCallback(async (jobId: string) => {
    try {
      const job = await getJob(jobId);
      setSteps(job.steps);
      if (job.status === "running") {
        timer.current = window.setTimeout(() => poll(jobId), 800);
        return;
      }
      setRunning(false);
      if (job.status === "done") setResult(job.result);
      else setError(job.error ? `${job.error.agent ? job.error.agent + ": " : ""}${job.error.message}` : "The analysis failed.");
    } catch (e) {
      setRunning(false);
      setError((e as ApiError).message);
    }
  }, []);

  const analyze = async () => {
    if (!selected) return;
    setError(null); setResult(null); setRunning(true); setSteps(null);
    try {
      const { job_id } = await startAnalysis(selected.id);
      poll(job_id);
    } catch (e) {
      setRunning(false);
      setError((e as ApiError).message);
    }
  };

  const missing = health?.agents.filter((a) => !a.key_configured).map((a) => a.name) ?? [];

  return (
    <main>
      {health && (!health.football_api_configured || missing.length > 0) && (
        <p className="warn">
          Setup needed in backend/.env: {!health.football_api_configured && "FOOTBALL_API_KEY "}
          {missing.length > 0 && `Groq key for ${missing.join(", ")}`}
        </p>
      )}
      {health?.warnings.map((w) => <p className="warn" key={w}>{w}</p>)}

      <section className="panel">
        <h2>Choose a match</h2>
        {loadingFixtures ? <p className="muted">Loading fixtures…</p>
          : fixtureError ? <p className="warn">{fixtureError}</p>
          : <MatchSelector matches={matches} selectedId={selected?.id ?? null} onSelect={setSelected} disabled={running} days={days} onDaysChange={setDays} />}
        <button className="analyze" onClick={analyze} disabled={!selected || running}>
          {running ? "Analyzing…" : selected ? `Analyze ${selected.home.short_name ?? selected.home.name} v ${selected.away.short_name ?? selected.away.name}` : "Analyze match"}
        </button>
      </section>

      {steps && (
        <section className="panel">
          <h2>Analysis progress</h2>
          <AgentStatus steps={steps} />
        </section>
      )}
      {error && <p className="warn" role="alert">{error}</p>}

      {result && result.abstained && (
        <section className="panel">
          <h2>No prediction made</h2>
          <p>{result.abstain_reason}</p>
          <p className="muted small">The system declines to predict when the real data is too thin. Nothing was invented to fill the gap.</p>
          {result.data_quality && <ul className="small">{result.data_quality.issues.filter((i) => i.severity !== "info").map((i, k) => <li key={k}>{i.detail}</li>)}</ul>}
        </section>
      )}
      {result && !result.abstained && result.prediction && result.simulation && (
        <>
          <PredictionCard result={result} />
          <section className="panel">
            <h3>On the pitch</h3>
            <MatchPitch homeName={result.match.home.name} awayName={result.match.away.name} homeXg={result.simulation.input_xg_home}
              awayXg={result.simulation.input_xg_away} score={result.prediction.predicted_score} lineups={result.context?.lineups ?? null} />
          </section>
          {result.context && <TeamCompare result={result} ctx={result.context} />}
          <ScenarioPanel scenarios={result.simulation.scenarios} home={result.match.home.name} away={result.match.away.name} />
          <SimulationResults sim={result.simulation} home={result.match.home.name} away={result.match.away.name} />
          <AnalysisPanel result={result} />
        </>
      )}
      <EvaluationPanel />
    </main>
  );
}
