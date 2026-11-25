import { useState } from "react";
import { useApi } from "../lib/useApi";
import { api, fmtNum, fmtUsd, label } from "../lib/api";
import { Card, Kpi, Loading, Meter } from "../components/ui";

type Lic = { tool_id: string; tool: string; sku: string; seats_purchased: number; seats_assigned: number; seats_active_30d: number; utilization_pct: number; monthly_cost_usd: number; idle_cost_usd: number; renewal_date: string | null };
type NonTok = { tool: string; provider: string; unit_type: string; quantity: number; requests: number; cost_usd: number };
type Idle = { user_id: string; name: string; department_id: string | null; last_active_at: string | null };

const UNIT_LABELS: Record<string, string> = {
  image: "Images generated", audio_minute: "Audio minutes", query: "Search queries", seat_day: "Seat-days",
  code_suggestion: "Code suggestions", tts_character: "TTS characters", audio_second: "Audio seconds", document: "Documents",
};

function AddLicence({ onDone }: { onDone: () => void }) {
  const tools = useApi<{ id: string; name: string; billing_model: string }[]>("/admin/tools");
  const [f, setF] = useState({ tool_id: "", sku: "", seats_purchased: "", price_per_seat_month_usd: "", renewal_date: "" });
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    try {
      await api("/admin/licenses", undefined, { method: "POST", body: JSON.stringify({
        ...f, seats_purchased: Number(f.seats_purchased), price_per_seat_month_usd: Number(f.price_per_seat_month_usd),
        renewal_date: f.renewal_date || null }) });
      setF({ tool_id: "", sku: "", seats_purchased: "", price_per_seat_month_usd: "", renewal_date: "" });
      setErr(null);
      onDone();
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  const ok = f.tool_id && f.sku && Number(f.seats_purchased) >= 0 && f.seats_purchased !== "" && f.price_per_seat_month_usd !== "";
  return (
    <div className="filters" style={{ marginTop: 12 }}>
      <select value={f.tool_id} onChange={(e) => setF({ ...f, tool_id: e.target.value })} aria-label="Tool">
        <option value="">Tool…</option>
        {tools.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
      </select>
      <input placeholder="SKU / plan" value={f.sku} onChange={(e) => setF({ ...f, sku: e.target.value })} />
      <input placeholder="Seats" type="number" min={0} style={{ width: 90 }} value={f.seats_purchased} onChange={(e) => setF({ ...f, seats_purchased: e.target.value })} />
      <input placeholder="$ / seat / month" type="number" min={0} step="0.01" style={{ width: 140 }} value={f.price_per_seat_month_usd} onChange={(e) => setF({ ...f, price_per_seat_month_usd: e.target.value })} />
      <input type="date" aria-label="Renewal date" value={f.renewal_date} onChange={(e) => setF({ ...f, renewal_date: e.target.value })} />
      <button className="btn primary" disabled={!ok} onClick={save}>Add licence</button>
      {err && <span className="err">{err}</span>}
    </div>
  );
}

export default function Licenses({ params, canSeeOrg, canEdit }: { params: Record<string, string>; canSeeOrg: boolean; canEdit: boolean }) {
  const lic = useApi<Lic[]>("/analytics/licenses");
  const nt = useApi<NonTok[]>("/analytics/non-token", params);
  const [tool, setTool] = useState<string | null>(null);
  const idle = useApi<Idle[]>(tool ? "/analytics/inactive-seats" : null, { tool_id: tool ?? "" });

  const total = lic.data?.reduce((s, l) => s + l.monthly_cost_usd, 0) ?? 0;
  const wasted = lic.data?.reduce((s, l) => s + l.idle_cost_usd, 0) ?? 0;

  return (
    <>
      {lic.data && canSeeOrg && (
        <div className="grid kpis">
          <Kpi label="Seat licences / month" value={fmtUsd(total)} />
          <Kpi label="Idle seat cost / month" value={fmtUsd(wasted)} />
          <Kpi label="Seats purchased" value={fmtNum(lic.data.reduce((s, l) => s + l.seats_purchased, 0))} />
          <Kpi label="Active in last 30 days" value={fmtNum(lic.data.reduce((s, l) => s + l.seats_active_30d, 0))} />
        </div>
      )}
      <div className="grid" style={{ marginTop: 16 }}>
        <Card title="Seat-based AI tools" sub="Utilisation = seats active in the last 30 days ÷ seats purchased. Click a row to list idle seats.">
          {lic.data ? (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr><th>Tool</th><th className="num">Purchased</th><th className="num">Assigned</th><th className="num">Active 30d</th><th>Utilisation</th><th className="num">Monthly</th><th className="num">Idle cost</th><th>Renewal</th></tr>
                </thead>
                <tbody>
                  {lic.data.map((l) => (
                    <tr key={l.tool_id} onClick={() => setTool(l.tool_id)} style={{ cursor: "pointer" }}>
                      <td><b>{l.tool}</b><div className="muted">{l.sku}</div></td>
                      <td className="num">{l.seats_purchased}</td><td className="num">{l.seats_assigned}</td><td className="num">{l.seats_active_30d}</td>
                      <td style={{ minWidth: 140 }}>
                        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                          <Meter pct={l.utilization_pct} /> <span className="mono">{l.utilization_pct}%</span>
                        </div>
                      </td>
                      <td className="num">{fmtUsd(l.monthly_cost_usd)}</td><td className="num">{fmtUsd(l.idle_cost_usd)}</td><td>{l.renewal_date}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Loading error={lic.error} />
          )}
          {lic.data && lic.data.length === 0 && <p className="muted">No seat licences yet. Add your contracts below.</p>}
          {canEdit && <AddLicence onDone={() => window.dispatchEvent(new Event("aiu-refresh"))} />}
        </Card>
        {tool && (
          <Card title={`Idle seats: ${lic.data?.find((l) => l.tool_id === tool)?.tool}`} sub="No activity in 30 days: candidates for reclamation" action={<button className="btn" onClick={() => setTool(null)}>Close</button>}>
            {idle.data ? (
              <div className="table-wrap">
                <table className="data">
                  <thead><tr><th>User</th><th>Department</th><th>Last active</th></tr></thead>
                  <tbody>
                    {idle.data.map((u) => (
                      <tr key={u.user_id}><td>{u.name}</td><td>{u.department_id}</td><td>{u.last_active_at ? u.last_active_at.slice(0, 10) : "Never"}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Loading error={idle.error} />
            )}
          </Card>
        )}
        <Card title="Non-token usage" sub="Consumption measured in units other than tokens">
          {nt.data ? (
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Tool</th><th>Provider</th><th>Unit</th><th className="num">Quantity</th><th className="num">Requests</th><th className="num">Spend</th></tr></thead>
                <tbody>
                  {nt.data.map((r) => (
                    <tr key={r.tool + r.unit_type}>
                      <td>{r.tool}</td><td>{label(r.provider)}</td><td>{UNIT_LABELS[r.unit_type] ?? r.unit_type}</td>
                      <td className="num">{fmtNum(r.quantity)}</td><td className="num">{fmtNum(r.requests)}</td><td className="num">{fmtUsd(r.cost_usd, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Loading error={nt.error} />
          )}
        </Card>
      </div>
    </>
  );
}
