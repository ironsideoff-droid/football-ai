import { useCallback, useEffect, useState } from "react";
import Home from "./pages/Home";
import Login from "./pages/Login";
import Paywall from "./pages/Paywall";
import { ApiError, getMe, openPortal, syncBilling, type Me } from "./services/api";
import { loadConfig, supabase, type PublicConfig } from "./services/auth";

type Phase = "loading" | "error" | "signedOut" | "ready";

export default function App() {
  const [cfg, setCfg] = useState<PublicConfig | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [error, setError] = useState<string | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [checking, setChecking] = useState(false);

  const refreshMe = useCallback(async () => {
    try { const m = await getMe(); setMe(m); setPhase("ready"); return m; }
    catch (e) {
      const err = e as ApiError;
      if (err.code === "auth_required") { setMe(null); setPhase("signedOut"); }
      else { setError(err.message); setPhase("error"); }
      return null;
    }
  }, []);

  // After Stripe redirects back, the webhook may lag a few seconds: poll, then fall back to asking Stripe directly.
  const confirmPayment = useCallback(async () => {
    setChecking(true);
    for (let i = 0; i < 8; i++) {
      const m = await refreshMe();
      if (m?.subscribed) break;
      if (i === 3) { try { await syncBilling(); } catch { /* keep polling */ } }
      await new Promise((r) => setTimeout(r, 2000));
    }
    setChecking(false);
    window.history.replaceState({}, "", window.location.pathname);
  }, [refreshMe]);

  useEffect(() => {
    let sub: { unsubscribe: () => void } | undefined;
    loadConfig().then(async (c) => {
      setCfg(c);
      const sb = supabase();
      if (sb) {
        sub = sb.auth.onAuthStateChange((event) => {
          if (event === "SIGNED_OUT") { setMe(null); setPhase("signedOut"); }
          else if (event === "SIGNED_IN" || event === "TOKEN_REFRESHED") void refreshMe();
        }).data.subscription;
      }
      if (new URLSearchParams(window.location.search).get("checkout") === "success") await confirmPayment();
      else await refreshMe();
    }).catch((e: Error) => { setError(e.message); setPhase("error"); });
    return () => sub?.unsubscribe();
  }, [refreshMe, confirmPayment]);

  const portal = async () => { try { window.location.href = (await openPortal()).url; } catch (e) { setError((e as ApiError).message); } };

  return (
    <div className="app">
      <div className="ambient" aria-hidden><i /><i /><i /><span className="ballroll" /></div>
      <header className="top">
        <div className="topbar">
          <h1 className="wordmark"><img className="brand" src="/brand/logo-dark.png" alt="TACTIQ" /></h1>
          {me && cfg && !cfg.auth_disabled && (
            <nav className="account small">
              <span className="muted">{me.email}{me.is_admin && " · admin"}</span>
              {me.subscribed && me.has_billing_account && <button className="linkish" onClick={portal}>Billing</button>}
              <button className="linkish" onClick={() => supabase()?.auth.signOut()}>Sign out</button>
            </nav>
          )}
        </div>
        <p className="tagline">Three AI agents and 50,000 simulated matches for every fixture.</p>
      </header>
      {phase === "loading" && <main><p className="muted">Loading…</p></main>}
      {phase === "error" && <main><p className="warn" role="alert">{error}</p></main>}
      {phase === "signedOut" && <div className="authwrap"><Login /></div>}
      {phase === "ready" && me && cfg && (me.subscribed ? <Home /> : <div className="authwrap"><Paywall me={me} cfg={cfg} checking={checking} /></div>)}
    </div>
  );
}
