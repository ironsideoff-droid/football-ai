import { getAccessToken } from "../lib/supabase";
import type { BillingStatus, Evaluation, Health, Job, MatchSummary } from "../types";

export class ApiError extends Error {
  constructor(message: string, public code?: string, public agent?: string | null, public status?: number) {
    super(message);
  }
}

const API_BASE = `${(import.meta.env.VITE_API_URL ?? "").replace(/\/+$/, "")}/api`;

let onUnauthorized: (() => void) | null = null;
/** Called when the server rejects the session (expired or revoked), so the app can send the user back to the login. */
export const setUnauthorizedHandler = (fn: (() => void) | null) => { onUnauthorized = fn; };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = await getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers: { ...headers, ...(init?.headers as Record<string, string> | undefined) } });
  } catch {
    throw new ApiError("Cannot reach the server. Check your connection and try again.");
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const e = body?.error;
    if (res.status === 401 && token && onUnauthorized) onUnauthorized();
    throw new ApiError(e?.message ?? body?.detail ?? `Request failed (${res.status}).`, e?.code, e?.agent, res.status);
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
export const getBillingStatus = () => request<BillingStatus>("/billing/status");
export const createCheckout = () => request<{ url: string }>("/billing/checkout", { method: "POST" });
export const createPortal = () => request<{ url: string }>("/billing/portal", { method: "POST" });
