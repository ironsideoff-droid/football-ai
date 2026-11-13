import { useEffect, useState } from "react";

export const reducedMotion = () => typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** Counts a number up from 0 when it first appears. */
export function CountUp({ value, decimals = 0, suffix = "", ms = 1100 }: { value: number; decimals?: number; suffix?: string; ms?: number }) {
  const [v, setV] = useState(reducedMotion() ? value : 0);
  useEffect(() => {
    if (reducedMotion()) { setV(value); return; }
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const k = Math.min(1, (t - t0) / ms);
      setV(value * (1 - Math.pow(1 - k, 3)));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, ms]);
  return <>{v.toFixed(decimals)}{suffix}</>;
}

/* ------------------------------------------------------------------ 1X2 ring */
export function ProbabilityRing({ home, draw, away, homeName, awayName }: { home: number; draw: number; away: number; homeName: string; awayName: string }) {
  const r = 70, c = 2 * Math.PI * r;
  const segs = [
    { v: home, cls: "seg-home", label: `${homeName} win` },
    { v: draw, cls: "seg-draw", label: "Draw" },
    { v: away, cls: "seg-away", label: `${awayName} win` },
  ];
  const total = home + draw + away || 1;
  let start = 0;
  const lead = segs.reduce((a, b) => (b.v > a.v ? b : a));
  return (
    <svg className="ring" viewBox="0 0 200 200" role="img" aria-label={`${homeName} ${home}%, draw ${draw}%, ${awayName} ${away}%`}>
      <circle className="ring-orbit" cx="100" cy="100" r="92" />
      <circle className="ring-track" cx="100" cy="100" r={r} />
      {segs.map((s, i) => {
        const len = Math.max(0, (s.v / total) * c - 4);
        const el = (
          <circle key={i} className={`ring-seg ${s.cls}`} cx="100" cy="100" r={r} transform="rotate(-90 100 100)"
            style={{ ["--len" as string]: len, ["--c" as string]: c, animationDelay: `${i * 0.18}s` }}
            strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-start * c / total} />
        );
        start += s.v;
        return el;
      })}
      <text x="100" y="98" className="ring-big" textAnchor="middle"><CountUp value={lead.v} decimals={0} suffix="%" /></text>
      <text x="100" y="120" className="ring-small" textAnchor="middle">{lead.label}</text>
    </svg>
  );
}

/* ------------------------------------------------------------------ columns */
export interface Series { name: string; values: number[]; cls: string }
export function Columns({ series, labels, unit = "%" }: { series: Series[]; labels: string[]; unit?: string }) {
  const W = 420, H = 170, pad = 22, base = H - 24;
  const max = Math.max(...series.flatMap((s) => s.values), 1);
  const slot = (W - pad * 2) / labels.length;
  const bw = Math.min(22, (slot - 6) / series.length);
  return (
    <svg className="columns" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={series.map((s) => s.name).join(" and ") + " distribution"}>
      {[0.25, 0.5, 0.75, 1].map((g) => <line key={g} className="gridline" x1={pad} x2={W - pad} y1={base - (base - 18) * g} y2={base - (base - 18) * g} />)}
      {labels.map((l, i) => {
        const x0 = pad + i * slot + (slot - bw * series.length) / 2;
        return (
          <g key={l}>
            {series.map((s, k) => {
              const v = s.values[i] ?? 0, h = (v / max) * (base - 18);
              return (
                <g key={s.name}>
                  <rect className={`col ${s.cls}`} x={x0 + k * bw} y={base - h} width={bw - 2} height={Math.max(h, 0)} rx="3" style={{ animationDelay: `${i * 70 + k * 40}ms` }}>
                    <title>{`${s.name}: ${l} → ${v}${unit}`}</title>
                  </rect>
                  {v >= 4 && <text className="colval" x={x0 + k * bw + (bw - 2) / 2} y={base - h - 4} textAnchor="middle">{Math.round(v)}</text>}
                </g>
              );
            })}
            <text className="collab" x={pad + i * slot + slot / 2} y={H - 6} textAnchor="middle">{l}</text>
          </g>
        );
      })}
    </svg>
  );
}

/* ------------------------------------------------------------------ radar */
export function Radar({ axes, a, b, aName, bName }: { axes: string[]; a: number[]; b: number[]; aName: string; bName: string }) {
  const S = 300, cx = S / 2, cy = S / 2, R = 96, n = axes.length;
  const pt = (i: number, v: number) => [cx + R * v * Math.cos(-Math.PI / 2 + (i * 2 * Math.PI) / n), cy + R * v * Math.sin(-Math.PI / 2 + (i * 2 * Math.PI) / n)];
  const poly = (vals: number[]) => vals.map((v, i) => pt(i, Math.max(0.04, Math.min(1, v))).map((x) => x.toFixed(1)).join(",")).join(" ");
  return (
    <svg className="radar" viewBox={`-40 0 ${S + 80} ${S}`} role="img" aria-label={`Team comparison: ${aName} against ${bName}`}>
      {[0.25, 0.5, 0.75, 1].map((g) => <polygon key={g} className="radar-grid" points={axes.map((_, i) => pt(i, g).join(",")).join(" ")} />)}
      {axes.map((_, i) => <line key={i} className="radar-axis" x1={cx} y1={cy} x2={pt(i, 1)[0]} y2={pt(i, 1)[1]} />)}
      <polygon className="radar-poly radar-b" points={poly(b)} />
      <polygon className="radar-poly radar-a" points={poly(a)} style={{ animationDelay: "0.15s" }} />
      {axes.map((l, i) => {
        const [x, y] = pt(i, 1.2);
        return <text key={l} className="radar-label" x={x} y={y} textAnchor={x < cx - 5 ? "end" : x > cx + 5 ? "start" : "middle"} dominantBaseline="middle">{l}</text>;
      })}
    </svg>
  );
}

/* ------------------------------------------------------------------ sparkline of cumulative points */
export function PointsLine({ sequence, cls }: { sequence: string; cls: string }) {
  const pts: number[] = [0];
  for (const ch of sequence) pts.push(pts[pts.length - 1] + (ch === "W" ? 3 : ch === "D" ? 1 : 0));
  const W = 140, H = 44, max = Math.max(pts[pts.length - 1], 3);
  const xy = pts.map((p, i) => [4 + (i * (W - 8)) / Math.max(pts.length - 1, 1), H - 5 - (p / max) * (H - 12)]);
  const d = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const [lx, ly] = xy[xy.length - 1];
  return (
    <svg className={`spark ${cls}`} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Points over the last ${sequence.length} matches`}>
      <path className="spark-area" d={`${d} L${lx} ${H} L${xy[0][0]} ${H} Z`} />
      <path className="spark-line" d={d} pathLength={1} />
      <circle className="spark-dot" cx={lx} cy={ly} r="3.2" />
      <circle className="spark-pulse" cx={lx} cy={ly} r="3.2" />
    </svg>
  );
}

/* ------------------------------------------------------------------ expected goals with interval */
export function XgBars({ rows }: { rows: { name: string; value: number; lo: number; hi: number; cls: string }[] }) {
  const max = Math.max(...rows.map((r) => r.hi), 0.5) * 1.08;
  return (
    <div className="xg">
      {rows.map((r) => (
        <div className="xg-row" key={r.name}>
          <span className="xg-name">{r.name}</span>
          <span className="xg-track">
            <span className={`xg-bar ${r.cls}`} style={{ width: `${(r.value / max) * 100}%` }} />
            <span className="xg-range" style={{ left: `${(r.lo / max) * 100}%`, width: `${((r.hi - r.lo) / max) * 100}%` }} title="90% range across simulations" />
          </span>
          <span className="xg-val"><CountUp value={r.value} decimals={2} /></span>
        </div>
      ))}
      <p className="muted small">Bars: average expected goals. The pale band is the 90% range across the 50,000 simulated parameter sets.</p>
    </div>
  );
}

/* ------------------------------------------------------------------ calibration (reliability) diagram */
export function CalibrationChart({ bins }: { bins: { mean_predicted: number; observed_frequency: number; n: number }[] }) {
  const S = 220, p = 26, L = S - p * 2;
  const X = (v: number) => p + v * L, Y = (v: number) => S - p - v * L;
  const maxN = Math.max(...bins.map((b) => b.n), 1);
  return (
    <svg className="calib" viewBox={`0 0 ${S} ${S}`} role="img" aria-label="Calibration: predicted probability against observed frequency">
      <rect className="calib-bg" x={p} y={p} width={L} height={L} rx="6" />
      {[0.25, 0.5, 0.75].map((g) => <g key={g}><line className="gridline" x1={X(g)} x2={X(g)} y1={p} y2={S - p} /><line className="gridline" y1={Y(g)} y2={Y(g)} x1={p} x2={S - p} /></g>)}
      <line className="calib-diag" x1={X(0)} y1={Y(0)} x2={X(1)} y2={Y(1)} />
      <polyline className="calib-line" points={bins.map((b) => `${X(b.mean_predicted)},${Y(b.observed_frequency)}`).join(" ")} />
      {bins.map((b, i) => <circle key={i} className="calib-pt" cx={X(b.mean_predicted)} cy={Y(b.observed_frequency)} r={3 + 4 * (b.n / maxN)}><title>{`predicted ${(b.mean_predicted * 100).toFixed(0)}% → happened ${(b.observed_frequency * 100).toFixed(0)}% (${b.n} forecasts)`}</title></circle>)}
      <text className="collab" x={S / 2} y={S - 5} textAnchor="middle">predicted</text>
      <text className="collab" x="9" y={S / 2} textAnchor="middle" transform={`rotate(-90 9 ${S / 2})`}>happened</text>
    </svg>
  );
}
