import { useApi } from "../lib/useApi";
import { fmtNum, fmtUsd, fmtUsdShort } from "../lib/api";
import { Card, Kpi, Loading } from "../components/ui";
import { RankedBars, StackedDaily } from "../components/charts";

type Summary = {
  current: Record<string, number>;
  change_pct: Record<string, number | null>;
  seat_cost_monthly_usd: number;
  period: { days: number };
};
type Row = { key: string; label: string; value: number; cost_usd: number; users: number; requests: number };

export default function Overview({ params }: { params: Record<string, string> }) {
  const s = useApi<Summary>("/analytics/summary", params);
  const ts = useApi<{ date: string; key: string; value: number }[]>("/analytics/timeseries", { ...params, metric: "cost", group_by: "provider" });
  const dept = useApi<Row[]>("/analytics/breakdown", { ...params, dimension: "department_id", metric: "cost" });
  const tools = useApi<Row[]>("/analytics/breakdown", { ...params, dimension: "tool_id", metric: "cost" });
  const apps = useApi<Row[]>("/analytics/breakdown", { ...params, dimension: "application", metric: "cost", limit: 8 });

  const c = s.data?.current;
  const d = s.data?.change_pct ?? {};
  return (
    <>
      <div className="grid kpis">
        {c ? (
          <>
            <Kpi label="Usage-based spend" value={fmtUsd(c.cost_usd)} delta={d.cost_usd} />
            <Kpi label="Tokens (in + out)" value={fmtNum(c.input_tokens + c.output_tokens)} delta={d.input_tokens} />
            <Kpi label="Model requests" value={fmtNum(c.requests)} delta={d.requests} />
            <Kpi label="RAG queries" value={fmtNum(c.rag_queries)} delta={d.rag_queries} />
            <Kpi label="Active AI users" value={fmtNum(c.active_users)} delta={d.active_users} />
            {s.data!.seat_cost_monthly_usd > 0 && <Kpi label="Seat licences / month" value={fmtUsd(s.data!.seat_cost_monthly_usd)} />}
          </>
        ) : (
          <Loading error={s.error} />
        )}
      </div>
      <div className="grid" style={{ marginTop: 16 }}>
        <Card title="Daily spend by provider" sub="USD, all usage-based AI services incl. RAG and per-seat daily share">
          {ts.data ? <StackedDaily rows={ts.data} fmt={fmtUsdShort} /> : <Loading error={ts.error} />}
        </Card>
      </div>
      <div className="grid two" style={{ marginTop: 16 }}>
        <Card title="Spend by department" sub="USD in period">
          {dept.data ? <RankedBars rows={dept.data.map((r) => ({ label: r.label, value: r.value }))} fmt={fmtUsdShort} /> : <Loading error={dept.error} />}
        </Card>
        <Card title="Spend by AI tool" sub="USD in period">
          {tools.data ? <RankedBars rows={tools.data.map((r) => ({ label: r.label, value: r.value }))} fmt={fmtUsdShort} /> : <Loading error={tools.error} />}
        </Card>
      </div>
      <div className="grid" style={{ marginTop: 16 }}>
        <Card title="Top internal AI applications" sub="Applications reporting via SDK / gateway">
          {apps.data ? (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr><th>Application</th><th className="num">Users</th><th className="num">Requests</th><th className="num">Spend</th></tr>
                </thead>
                <tbody>
                  {apps.data.map((r) => (
                    <tr key={r.key}><td>{r.label}</td><td className="num">{r.users}</td><td className="num">{fmtNum(r.requests)}</td><td className="num">{fmtUsd(r.cost_usd, 2)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Loading error={apps.error} />
          )}
        </Card>
      </div>
    </>
  );
}
