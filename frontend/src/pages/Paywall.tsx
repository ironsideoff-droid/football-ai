import { useState } from "react";
import { ApiError, openPortal, startCheckout, type Me } from "../services/api";
import type { PublicConfig } from "../services/auth";

export default function Paywall({ me, cfg, checking }: { me: Me; cfg: PublicConfig; checking: boolean }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async (fn: () => Promise<{ url: string }>) => {
    setBusy(true); setError(null);
    try { window.location.href = (await fn()).url; } catch (e) { setError((e as ApiError).message); setBusy(false); }
  };
  const fmt = (v: number) => new Intl.NumberFormat(undefined, { style: "currency", currency: cfg.price.currency }).format(v);
  const price = fmt(cfg.price.display_amount ?? cfg.price.amount);
  const base = fmt(cfg.price.amount);
  const past = me.has_billing_account;
  return (
    <main>
      <section className="panel authbox">
        <h2>{past ? "Your subscription is not active" : "Unlock TACTIQ"}</h2>
        <p className="price">{price}<span className="muted small"> / month</span></p>
        <p className="muted small">{base} + applicable taxes. The exact total depends on your country and is shown by Stripe before you pay.</p>
        <ul className="small">
          <li>Three AI agents analyse every fixture</li>
          <li>50,000 simulated matches per prediction</li>
          <li>Honest evaluation: Brier score, log loss, calibration</li>
          <li>Cancel any time from the billing portal</li>
        </ul>
        {checking && <p className="muted">Confirming your payment…</p>}
        <button className="analyze" disabled={busy || checking} onClick={() => go(startCheckout)}>{busy ? "Redirecting…" : `Subscribe — ${price}/month`}</button>
        {past && <button className="analyze secondary" disabled={busy} onClick={() => go(openPortal)}>Manage billing</button>}
        {error && <p className="warn" role="alert">{error}</p>}
        <p className="muted small">Secure payment by Stripe. Predictions are statistical estimates, not betting advice or guarantees.</p>
      </section>
    </main>
  );
}
