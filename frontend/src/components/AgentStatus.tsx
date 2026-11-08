import type { Step } from "../types";

const LABEL: Record<Step["status"], string> = { pending: "Waiting", running: "Working", done: "Done", error: "Failed" };

export default function AgentStatus({ steps }: { steps: Step[] }) {
  return (
    <ol className="steps" aria-live="polite">
      {steps.map((s) => (
        <li key={s.key} className={`step ${s.status}`}>
          <span className="dot" aria-hidden />
          <span className="step-label">{s.label}</span>
          <span className="step-state">{LABEL[s.status]}</span>
          {s.message && <span className="step-msg">{s.message}</span>}
        </li>
      ))}
    </ol>
  );
}
