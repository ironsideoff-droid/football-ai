import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Session } from "@supabase/supabase-js";
import { authConfigured, supabase } from "../lib/supabase";
import { setUnauthorizedHandler } from "../services/api";

interface AuthState {
  /** False only in local development without Supabase configured: there is no login at all. */
  enabled: boolean;
  loading: boolean;
  session: Session | null;
  recovering: boolean;
  signIn: (email: string, password: string) => Promise<string | null>;
  signUp: (email: string, password: string) => Promise<{ error: string | null; needsConfirmation: boolean }>;
  resetPassword: (email: string) => Promise<string | null>;
  updatePassword: (password: string) => Promise<string | null>;
  signOut: () => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(authConfigured);
  const [recovering, setRecovering] = useState(false);

  useEffect(() => {
    if (!supabase) return;
    supabase.auth.getSession().then(({ data }) => { setSession(data.session); setLoading(false); });
    const { data } = supabase.auth.onAuthStateChange((event, s) => {
      setSession(s);
      if (event === "PASSWORD_RECOVERY") setRecovering(true);
      if (event === "SIGNED_OUT") setRecovering(false);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  const signOut = useCallback(async () => { await supabase?.auth.signOut(); }, []);
  useEffect(() => { setUnauthorizedHandler(() => void signOut()); return () => setUnauthorizedHandler(null); }, [signOut]);

  const value = useMemo<AuthState>(() => ({
    enabled: authConfigured, loading, session, recovering, signOut,
    signIn: async (email, password) => (await supabase!.auth.signInWithPassword({ email, password })).error?.message ?? null,
    signUp: async (email, password) => {
      const { data, error } = await supabase!.auth.signUp({ email, password, options: { emailRedirectTo: window.location.origin } });
      return { error: error?.message ?? null, needsConfirmation: !error && !data.session };
    },
    resetPassword: async (email) => (await supabase!.auth.resetPasswordForEmail(email, { redirectTo: window.location.origin })).error?.message ?? null,
    updatePassword: async (password) => {
      const { error } = await supabase!.auth.updateUser({ password });
      if (!error) setRecovering(false);
      return error?.message ?? null;
    },
  }), [loading, session, recovering, signOut]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth must be used inside AuthProvider");
  return v;
}
