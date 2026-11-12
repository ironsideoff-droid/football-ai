import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";
import EvaluationPanel from "../components/EvaluationPanel";

export const PRICE_LABEL = import.meta.env.VITE_PRICE_LABEL || "€9.99 / month";

type Mode = "signin" | "signup" | "reset";

export default function Landing() {
  const { signIn, signUp, resetPassword } = useAuth();
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const switchMode = (m: Mode) => { setMode(m); setError(null); setNotice(null); };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null); setNotice(null);
    if (mode !== "reset" && password.length < 8) { setError("Use a password of at least 8 characters."); return; }
    setBusy(true);
    if (mode === "signin") {
      const err = await signIn(email.trim(), password);
      if (err) setError(err);
    } else if (mode === "signup") {
      const r = await signUp(email.trim(), password);
      if (r.error) setError(r.error);
      else if (r.needsConfirmation) setNotice("Check your inbox and click the confirmation link, then sign in.");
    } else {
      const err = await resetPassword(email.trim());
      if (err) setError(err); else setNotice("If that address has an account, a reset link is on its way.");
    }
    setBusy(false);
  };

  return (
    <main>
      <section className="panel hero">
        <h2>Every fixture, analysed three ways</h2>
        <ul className="ticks">
          <li>Three independent AI agents read the statistics and the news.</li>
          <li>A 50,000-match simulation turns their findings into probabilities.</li>
          <li>Every prediction is stored before kickoff and can never be edited. The track record below is public.</li>
        </ul>
        <p className="muted">Create a free account to browse upcoming fixtures. Full analyses are {PRICE_LABEL}, cancel any time.</p>
      </section>

      <section className="panel authbox">
        <h2>{mode === "signin" ? "Sign in" : mode === "signup" ? "Create your account" : "Reset your password"}</h2>
        <form onSubmit={submit} className="authform">
          <label className="field"><span>Email</span>
            <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          {mode !== "reset" && (
            <label className="field"><span>Password</span>
              <input type="password" required minLength={8} autoComplete={mode === "signin" ? "current-password" : "new-password"}
                value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
          )}
          {error && <p className="warn" role="alert">{error}</p>}
          {notice && <p className="ok" role="status">{notice}</p>}
          <button className="analyze" type="submit" disabled={busy}>
            {busy ? "One moment…" : mode === "signin" ? "Sign in" : mode === "signup" ? "Create account" : "Send reset link"}
          </button>
        </form>
        <p className="muted small authlinks">
          {mode !== "signin" && <button className="linklike" onClick={() => switchMode("signin")}>I already have an account</button>}
          {mode !== "signup" && <button className="linklike" onClick={() => switchMode("signup")}>Create an account</button>}
          {mode === "signin" && <button className="linklike" onClick={() => switchMode("reset")}>Forgot password?</button>}
        </p>
      </section>

      <EvaluationPanel defaultOpen />
    </main>
  );
}
