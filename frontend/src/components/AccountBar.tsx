import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { ApiError, createPortal } from "../services/api";
import type { BillingStatus } from "../types";

export default function AccountBar({ billing }: { billing: BillingStatus | null }) {
  const { session, signOut } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const manage = async () => {
    setBusy(true); setError(null);
    try { window.location.assign((await createPortal()).url); }
    catch (e) { setError((e as ApiError).message); setBusy(false); }
  };

  const renews = billing?.current_period_end ? new Date(billing.current_period_end).toLocaleDateString() : null;
  const plan = !billing ? "" : billing.active
    ? (billing.cancel_at_period_end ? `Subscribed · ends ${renews}` : `Subscribed${renews ? ` · renews ${renews}` : ""}`)
    : "Free account";

  return (
    <div className="accountbar">
      <span className="muted small">{session?.user.email}{plan && <> · <span className={billing?.active ? "plan on" : "plan"}>{plan}</span></>}</span>
      <span className="accountbtns">
        {billing && billing.status !== "none" && billing.status !== "dev" && <button className="linklike" onClick={manage} disabled={busy}>Manage billing</button>}
        <button className="linklike" onClick={() => void signOut()}>Sign out</button>
      </span>
      {error && <p className="warn" role="alert">{error}</p>}
    </div>
  );
}
