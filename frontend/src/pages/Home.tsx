import { useCallback, useEffect, useRef, useState } from "react";
import AgentStatus from "../components/AgentStatus";
import AnalysisPanel from "../components/AnalysisPanel";
import MatchSelector from "../components/MatchSelector";
import PredictionCard from "../components/PredictionCard";
import EvaluationPanel from "../components/EvaluationPanel";
import ScenarioPanel from "../components/ScenarioPanel";
import SimulationResults from "../components/SimulationResults";
import AccountBar from "../components/AccountBar";
import Paywall from "../components/Paywall";
import { ApiError, getBillingStatus, getFixtures, getHealth, getJob, startAnalysis } from "../services/api";
import type { AnalysisResult, BillingStatus, Health, MatchSummary, Step } from "../types";

/** After Stripe redirects back, the subscription becomes active when the webhook lands: poll for it (up to ~60 s). */
const CHECKOUT_POLL_MS = 2000, CHECKOUT_POLL_MAX = 30;

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
  const [billing, setBilling] = useState<BillingStatus | null>(null);
  const [checkout, setCheckout] = useState<"success" | "cancelled" | null>(
    () => { const c = new URLSearchParams(window.location.search).get("checkout"); return c === "success" || c === "cancelled" ? c : null; });
  const timer = useRef<number | null>(null);

  const refreshBilling = useCallback(async () => {
    try { const b = await getBillingStatus(); setBilling(b); return b; } catch { return null; }
  }, []);

  useEffect(() => {
    getHealth().then(setHealth).catch((e: ApiError) => setFixtureError(e.message));
    void refreshBilling();
    return () => { if (timer.current) window.clearTimeout(timer.current); };
  }, [refreshBilling]);

  useEffect(() => {
    if (checkout) window.history.replaceState(null, "", window.location.pathname); // drop ?checkout= from the address bar
    if (checkout !== "success") return;
    let tries = 0, stopped = false;
    const tick = async () => {
      const b = await refreshBilling();
      if (stopped || b?.active || ++tries >= CHECKOUT_POLL_MAX) { if (!stopped) setCheckout(null); return; }
      window.setTimeout(tick, CHECKOUT_POLL_MS);
    };
    void tick();
    return () => { stopped = true; };
  }, [checkout, refreshBilling]);

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

  const failWith = (e: ApiError) => {
    setRunning(false);
    setError(e.message);
    if (e.code === "subscription_required") void refreshBilling(); // e.g. the subscription ended while the page was open
    if (e.code === "usage_limit") void refreshBilling();
  };

  const analyze = async () => {
    if (!selected) return;
    setError(null); setResult(null); setRunning(true); setSteps(null);
    try {
      const { job_id } = await startAnalysis(selected.id);
      poll(job_id);
    } catch (e) {
      failWith(e as ApiError);
    }
  };

  const missing = health?.agents?.filter((a) => !a.key_configured).map((a) => a.name) ?? [];
  const noFootballKey = health?.football_api_configured === false;
  const subscribed = billing?.active ?? false;
  const activating = checkout === "success" && !subscribed;

  return (
    <main>
      <AccountBar billing={billing} />
      {activating && <p className="ok" role="status">Payment received. Activating your subscription…</p>}
      {billing && !subscribed && !activating && <Paywall cancelled={checkout === "cancelled"} />}
      {(noFootballKey || missing.length > 0) && (
        <p className="warn">
          Setup needed in backend/.env: {noFootballKey && "FOOTBALL_API_KEY "}
          {missing.length > 0 && `Groq key for ${missing.join(", ")}`}
        </p>
      )}
      {health?.warnings?.map((w) => <p className="warn" key={w}>{w}</p>)}

      <section className="panel">
        <h2>Choose a match</h2>
        {loadingFixtures ? <p className="muted">Loading fixtures…</p>
          : fixtureError ? <p className="warn">{fixtureError}</p>
          : <MatchSelector matches={matches} selectedId={selected?.id ?? null} onSelect={setSelected} disabled={running} days={days} onDaysChange={setDays} />}
        <button className="analyze" onClick={analyze} disabled={!selected || running || !subscribed}>
          {running ? "Analyzing…" : !subscribed ? "Subscribe to analyze" : selected ? `Analyze ${selected.home.short_name ?? selected.home.name} v ${selected.away.short_name ?? selected.away.name}` : "Analyze match"}
        </button>
        {subscribed && billing && billing.daily_limit > 0 && (
          <p className="muted small">{billing.used_today} of {billing.daily_limit} new analyses used today. Matches that were already analysed are free to open.</p>
        )}
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
          <ScenarioPanel scenarios={result.simulation.scenarios} home={result.match.home.name} away={result.match.away.name} />
          <SimulationResults sim={result.simulation} home={result.match.home.name} away={result.match.away.name} />
          <AnalysisPanel result={result} />
        </>
      )}
      <EvaluationPanel />
    </main>
  );
}
