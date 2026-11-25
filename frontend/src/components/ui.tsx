import { ReactNode } from "react";
import { colorFor } from "../lib/palette";
import { label } from "../lib/api";

export function Card({ title, sub, children, action }: { title: string; sub?: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h3>{title}</h3>
          {sub && <div className="sub">{sub}</div>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function Kpi({ label: l, value, delta }: { label: string; value: string; delta?: number | null }) {
  let d = "";
  if (delta !== undefined && delta !== null) {
    const arrow = delta > 0 ? "▲" : delta < 0 ? "▼" : "•";
    d = `${arrow} ${Math.abs(delta).toFixed(1)}% vs previous period`;
  }
  return (
    <div className="card kpi">
      <div className="label">{l}</div>
      <div className="value">{value}</div>
      {d && <div className="delta">{d}</div>}
    </div>
  );
}

export function Legend({ keys }: { keys: string[] }) {
  if (keys.length < 2) return null;
  return (
    <div className="legend" role="list">
      {keys.map((k) => (
        <span key={k} role="listitem">
          <i style={{ background: colorFor(k) }} />
          {label(k)}
        </span>
      ))}
    </div>
  );
}

type TipProps = { active?: boolean; payload?: { name: string; value: number; color?: string }[]; label?: string; fmt: (v: number) => string; titleFmt?: (l: string) => string };
export function ChartTip({ active, payload, label: l, fmt, titleFmt }: TipProps) {
  if (!active || !payload?.length) return null;
  const total = payload.reduce((s, p) => s + (Number(p.value) || 0), 0);
  return (
    <div className="tip">
      <div className="t">{titleFmt ? titleFmt(String(l)) : l}</div>
      {[...payload].reverse().map((p) => (
        <div className="row" key={p.name}>
          <span>
            <i style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: p.color, marginRight: 5 }} />
            {label(p.name)}
          </span>
          <b>{fmt(Number(p.value))}</b>
        </div>
      ))}
      {payload.length > 1 && (
        <div className="row" style={{ borderTop: "1px solid var(--border)", marginTop: 4, paddingTop: 4 }}>
          <span>Total</span>
          <b>{fmt(total)}</b>
        </div>
      )}
    </div>
  );
}

export function StatusBadge({ state }: { state: string }) {
  const map: Record<string, [string, string]> = {
    ok: ["✓", "On track"],
    at_risk: ["◔", "Forecast over"],
    warning: ["!", "Near limit"],
    over: ["✕", "Over budget"],
  };
  const [icon, text] = map[state] ?? ["•", state];
  return (
    <span className={`badge ${state}`}>
      <span aria-hidden>{icon}</span>
      {text}
    </span>
  );
}

export function Meter({ pct, color = "var(--series-1)" }: { pct: number; color?: string }) {
  return (
    <div className="bar-track" aria-label={`${pct.toFixed(0)}%`}>
      <div className="bar-fill" style={{ width: `${Math.min(pct, 100)}%`, background: color }} />
    </div>
  );
}

export function Loading({ error }: { error?: string | null }) {
  return <div className={error ? "err" : "muted"}>{error ? `Could not load: ${error}` : "Loading…"}</div>;
}
