import { FormEvent, useState } from "react";
import { supabase } from "../services/auth";

export default function Login() {
  const [mode, setMode] = useState<"in" | "up">("in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const sb = supabase();
    if (!sb) return;
    setBusy(true); setError(null); setNotice(null);
    if (mode === "in") {
      const { error } = await sb.auth.signInWithPassword({ email, password });
      if (error) setError(error.message);
    } else {
      const { data, error } = await sb.auth.signUp({ email, password, options: { emailRedirectTo: window.location.origin } });
      if (error) setError(error.message);
      else if (!data.session) setNotice("Check your inbox and click the confirmation link, then sign in.");
    }
    setBusy(false);
  };

  const google = async () => {
    const sb = supabase();
    if (!sb) return;
    setError(null);
    const { error } = await sb.auth.signInWithOAuth({ provider: "google", options: { redirectTo: window.location.origin } });
    if (error) setError(error.message.includes("not enabled") ? "Google sign-in is not switched on yet. Use email for now." : error.message);
  };

  const reset = async () => {
    const sb = supabase();
    if (!sb || !email) { setError("Enter your email first."); return; }
    setError(null);
    const { error } = await sb.auth.resetPasswordForEmail(email, { redirectTo: window.location.origin });
    if (error) setError(error.message); else setNotice("If that address has an account, a reset link is on its way.");
  };

  return (
    <main>
      <section className="panel authbox">
        <h2>{mode === "in" ? "Sign in" : "Create your account"}</h2>
        <button type="button" className="analyze secondary googlebtn" onClick={google}>
          <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.9 6.1C12.4 13.6 17.7 9.5 24 9.5z"/><path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.5 5.8c4.4-4.1 7.1-10.1 7.1-17.5z"/><path fill="#FBBC05" d="M10.5 28.7c-.5-1.5-.8-3-.8-4.7s.3-3.2.8-4.7l-7.9-6.1C.9 16.4 0 20.1 0 24s.9 7.6 2.6 10.8l7.9-6.1z"/><path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.5-5.8c-2.1 1.4-4.9 2.3-8.4 2.3-6.3 0-11.6-4.1-13.5-9.8l-7.9 6.1C6.5 42.6 14.6 48 24 48z"/></svg>
          Continue with Google
        </button>
        <p className="muted small center orline">or</p>
        <form onSubmit={submit}>
          <label>Email<input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
          <label>Password<input type="password" required minLength={8} autoComplete={mode === "in" ? "current-password" : "new-password"} value={password} onChange={(e) => setPassword(e.target.value)} /></label>
          <button className="analyze" disabled={busy}>{busy ? "Please wait…" : mode === "in" ? "Sign in" : "Sign up"}</button>
        </form>
        {error && <p className="warn" role="alert">{error}</p>}
        {notice && <p className="muted">{notice}</p>}
        <p className="muted small">
          {mode === "in" ? <>No account? <a href="#" onClick={(e) => { e.preventDefault(); setMode("up"); setError(null); }}>Sign up</a> · <a href="#" onClick={(e) => { e.preventDefault(); reset(); }}>Forgot password?</a></>
            : <>Already registered? <a href="#" onClick={(e) => { e.preventDefault(); setMode("in"); setError(null); }}>Sign in</a></>}
        </p>
      </section>
    </main>
  );
}
