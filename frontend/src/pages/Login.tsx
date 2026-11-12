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
