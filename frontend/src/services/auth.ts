import { createClient, type SupabaseClient } from "@supabase/supabase-js";

export interface PublicConfig {
  auth_disabled: boolean;
  supabase_url: string;
  supabase_anon_key: string;
  price: { amount: number; currency: string; interval: string };
}

let client: SupabaseClient | null = null;

/** Public settings come from the backend at runtime, so no secrets or build-time env are needed. */
export async function loadConfig(): Promise<PublicConfig> {
  const res = await fetch("/api/config");
  if (!res.ok) throw new Error("Cannot reach the backend.");
  const cfg = (await res.json()) as PublicConfig;
  if (!cfg.auth_disabled) {
    if (!cfg.supabase_url || !cfg.supabase_anon_key) throw new Error("Login is not configured on the server (SUPABASE_URL / SUPABASE_ANON_KEY).");
    client = createClient(cfg.supabase_url, cfg.supabase_anon_key);
  }
  return cfg;
}

export const supabase = () => client;

export async function accessToken(): Promise<string | null> {
  if (!client) return null;
  const { data } = await client.auth.getSession(); // refreshes an expired token automatically
  return data.session?.access_token ?? null;
}
