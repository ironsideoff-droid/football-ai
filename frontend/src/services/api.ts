import type { Evaluation, Health, Job, MatchSummary } from "../types";

export class ApiError extends Error {
  constructor(message: string, public code?: string, public agent?: string | null) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { headers: { "Content-Type": "application/json" }, ...init });
  } catch {
    throw new ApiError("Cannot reach the backend. Start it with: uvicorn main:app --reload (in the backend folder).");
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const e = body?.error;
    throw new ApiError(e?.message ?? body?.detail ?? `Request failed (${res.status}).`, e?.code, e?.agent);
  }
  return body as T;
}

export const getHealth = () => request<Health>("/health");
export const getFixtures = (days = 14) =>
  request<{ matches: MatchSummary[] }>(`/fixtures?days=${days}`).then((r) => r.matches);
export const startAnalysis = (matchId: number) =>
  request<{ job_id: string }>("/analyze/start", { method: "POST", body: JSON.stringify({ match_id: matchId }) });
export const getJob = (jobId: string) => request<Job>(`/jobs/${jobId}`);
export const getEvaluation = () => request<Evaluation>("/evaluation");
export const refreshResults = () => request<{ results_recorded: number[] }>("/evaluation/refresh", { method: "POST" });
