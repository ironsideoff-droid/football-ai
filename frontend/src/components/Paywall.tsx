import { useState } from "react";
import { ApiError, createCheckout } from "../services/api";
import { PRICE_LABEL } from "../pages/Landing";

export default function Paywall({ cancelled }: { cancelled: boolean }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const subscribe = async () => {
    setBusy(true); setError(null);
    try {
      const { url } = await createCheckout();
      window.location.assign(url); // Stripe-hosted checkout: card details never touch this app
    } catch (e) {
      setError((e as ApiError).message);
      setBusy(false);
    }
  };

  return (
    <section className="panel paywall">
      <h2>Unlock full analyses</h2>
      <ul className="ticks">
        <li>Three-agent analysis of any fixture, with the 50,000-match simulation and scenarios.</li>
        <li>Sources, news findings and data-quality checks behind every prediction.</li>
        <li>Matches already analysed are instant and never count against your daily allowance.</li>
      </ul>
      {cancelled && <p className="muted">Checkout was cancelled. You have not been charged.</p>}
      {error && <p className="warn" role="alert">{error}</p>}
      <button className="analyze" onClick={subscribe} disabled={busy}>{busy ? "Opening checkout…" : `Subscribe · ${PRICE_LABEL}`}</button>
      <p className="muted small">Secure payment by Stripe. Cancel any time from “Manage billing”.</p>
    </section>
  );
}
