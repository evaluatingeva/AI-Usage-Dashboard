import { useState } from "react";
import { useApi } from "../lib/useApi";
import { download, fmtUsd } from "../lib/api";
import { Card, Loading, Meter, StatusBadge } from "../components/ui";

type B = { id: number; scope_type: string; scope_id: string; limit_usd: number; spent_usd: number; forecast_usd: number; used_pct: number; alert_threshold_pct: number; state: string; owner_email: string | null };

const STATE_COLOR: Record<string, string> = { ok: "var(--good)", at_risk: "var(--warning)", warning: "var(--warning)", over: "var(--critical)" };

export default function Budgets({ params, canExport }: { params: Record<string, string>; canExport: boolean }) {
  const { data, error } = useApi<B[]>("/analytics/budgets");
  const [busy, setBusy] = useState(false);
  const exportCsv = async () => {
    setBusy(true);
    try {
      await download("/analytics/chargeback.csv", params, `chargeback_${params.start}_${params.end}.csv`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="grid">
      <Card title="Budgets: month to date" sub="Forecast = month-to-date spend extrapolated linearly to month end"
        action={canExport && <button className="btn primary" disabled={busy} onClick={exportCsv}>{busy ? "Exporting…" : "Export chargeback CSV"}</button>}>
        {data ? (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr><th>Scope</th><th>Status</th><th>Used</th><th className="num">Spent</th><th className="num">Forecast</th><th className="num">Limit</th><th>Owner</th></tr>
              </thead>
              <tbody>
                {data.map((b) => (
                  <tr key={b.id}>
                    <td><span className="muted">{b.scope_type}</span> <b>{b.scope_id}</b></td>
                    <td><StatusBadge state={b.state} /></td>
                    <td style={{ minWidth: 150 }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                        <Meter pct={b.used_pct} color={STATE_COLOR[b.state]} /><span className="mono">{b.used_pct}%</span>
                      </div>
                    </td>
                    <td className="num">{fmtUsd(b.spent_usd)}</td><td className="num">{fmtUsd(b.forecast_usd)}</td><td className="num">{fmtUsd(b.limit_usd)}</td>
                    <td className="muted">{b.owner_email}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Loading error={error} />
        )}
      </Card>
    </div>
  );
}
