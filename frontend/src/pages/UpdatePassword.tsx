import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";

/** Shown after the user follows the link in a password-reset email. */
export default function UpdatePassword() {
  const { updatePassword } = useAuth();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (password.length < 8) { setError("Use a password of at least 8 characters."); return; }
    setBusy(true);
    setError(await updatePassword(password));
    setBusy(false);
  };

  return (
    <main>
      <section className="panel authbox">
        <h2>Choose a new password</h2>
        <form onSubmit={submit} className="authform">
          <label className="field"><span>New password</span>
            <input type="password" required minLength={8} autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          {error && <p className="warn" role="alert">{error}</p>}
          <button className="analyze" type="submit" disabled={busy}>{busy ? "Saving…" : "Save password"}</button>
        </form>
      </section>
    </main>
  );
}
