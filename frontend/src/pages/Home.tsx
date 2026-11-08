import { useCallback, useEffect, useRef, useState } from "react";
import AgentStatus from "../components/AgentStatus";
import AnalysisPanel from "../components/AnalysisPanel";
import MatchSelector from "../components/MatchSelector";
import PredictionCard from "../components/PredictionCard";
import SimulationResults from "../components/SimulationResults";
import { ApiError, getFixtures, getHealth, getJob, startAnalysis } from "../services/api";
import type { AnalysisResult, Health, MatchSummary, Step } from "../types";

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [matches, setMatches] = useState<MatchSummary[]>([]);
  const [loadingFixtures, setLoadingFixtures] = useState(true);
  const [selected, setSelected] = useState<MatchSummary | null>(null);
  const [steps, setSteps] = useState<Step[] | null>(null);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fixtureError, setFixtureError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    getHealth().then(setHealth).catch((e: ApiError) => setFixtureError(e.message));
    getFixtures()
      .then(setMatches)
      .catch((e: ApiError) => setFixtureError(e.message))
      .finally(() => setLoadingFixtures(false));
    return () => { if (timer.current) window.clearTimeout(timer.current); };
  }, []);

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
          : <MatchSelector matches={matches} selectedId={selected?.id ?? null} onSelect={setSelected} disabled={running} />}
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

      {result && (
        <>
          <PredictionCard result={result} />
          <SimulationResults sim={result.simulation} home={result.match.home.name} away={result.match.away.name} />
          <AnalysisPanel result={result} />
        </>
      )}
    </main>
  );
}
