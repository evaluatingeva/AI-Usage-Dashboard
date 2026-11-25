import { useApi } from "../lib/useApi";
import { fmtNum, fmtUsd, fmtUsdShort, label } from "../lib/api";
import { Card, Kpi, Loading } from "../components/ui";
import { Lines, RankedBars } from "../components/charts";

type R = { key: string; label: string; cost_usd: number; tokens: number; requests: number; users: number };
export type ProfileData = {
  dimension: string;
  key: string;
  info: { label: string; department?: string | null; department_id?: string | null; team?: string | null; cost_center?: string | null; headcount?: number; active?: boolean; seats?: { tool_id: string; last_active_at: string | null }[] };
  totals: { cost_usd: number; input_tokens: number; output_tokens: number; requests: number; active_users: number; documents_retrieved: number; last_activity: string | null };
  by_tool: R[];
  by_model: R[];
  by_application: R[];
  daily: { date: string; cost_usd: number; tokens: number }[];
  top_users?: R[];
  by_team?: R[];
};

const NOUN: Record<string, string> = { user_id: "User", department_id: "Department", team_id: "Team", project: "Engagement", application: "Application" };

function Table({ rows, onPick, first }: { rows: R[]; onPick?: (r: R) => void; first: string }) {
  if (!rows.length) return <p className="muted">No usage in this period.</p>;
  return (
    <div className="table-wrap">
      <table className="data">
        <thead><tr><th>{first}</th><th className="num">Requests</th><th className="num">Tokens</th><th className="num">Spend</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className={onPick ? "click" : ""} onClick={() => onPick?.(r)}>
              <td>{label(r.label)}</td><td className="num">{fmtNum(r.requests)}</td><td className="num">{fmtNum(r.tokens)}</td><td className="num">{fmtUsd(r.cost_usd, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Everything about one user, department, team, engagement or application. */
export default function Profile({ dimension, id, params, onOpen }: { dimension: string; id: string; params: Record<string, string>; onOpen: (dimension: string, key: string, label: string) => void }) {
  const { data, error } = useApi<ProfileData>("/analytics/profile", { ...params, dimension, key: id });
  if (!data) return <Loading error={error} />;
  const t = data.totals;
  const i = data.info;
  const facts = [
    i.department && `Department: ${i.department}`,
    i.team && `Team: ${i.team}`,
    i.cost_center && `Cost centre: ${i.cost_center}`,
    i.headcount !== undefined && `Headcount (HR): ${i.headcount}`,
    t.last_activity && `Last activity: ${t.last_activity.slice(0, 16).replace("T", " ")} UTC`,
  ].filter(Boolean) as string[];
  return (
    <div className="grid">
      <div>
        <h2 style={{ margin: "4px 0" }}>{NOUN[dimension]}: {i.label}</h2>
        <div>{facts.map((f) => <span key={f} className="chip">{f}</span>)}</div>
      </div>
      <div className="grid kpis">
        <Kpi label="Spend" value={fmtUsd(t.cost_usd, 2)} />
        <Kpi label="Tokens (in + out)" value={fmtNum(t.input_tokens + t.output_tokens)} />
        <Kpi label="Requests" value={fmtNum(t.requests)} />
        {dimension !== "user_id" ? <Kpi label="Active users" value={fmtNum(t.active_users)} /> : <Kpi label="Documents retrieved (RAG)" value={fmtNum(t.documents_retrieved)} />}
      </div>
      <Card title="Daily spend" sub="USD per day">
        <Lines data={data.daily} series={[{ key: "cost_usd", name: "Spend" }]} fmt={fmtUsdShort} height={200} />
      </Card>
      <div className="grid two">
        <Card title="By AI tool">
          <RankedBars rows={data.by_tool.map((r) => ({ label: r.label, value: r.cost_usd }))} fmt={fmtUsdShort} />
        </Card>
        <Card title="By model"><Table rows={data.by_model} first="Model" /></Card>
        <Card title="By application" sub="Internal apps and RAG assistants (from the SDK / gateway)">
          <Table rows={data.by_application} first="Application" onPick={(r) => r.key !== "unattributed" && onOpen("application", r.key, r.label)} />
        </Card>
        {data.by_team && data.by_team.length > 0 && (
          <Card title="By team"><Table rows={data.by_team} first="Team" onPick={(r) => r.key !== "unattributed" && onOpen("team_id", r.key, r.label)} /></Card>
        )}
        {data.top_users && data.top_users.length > 0 && (
          <Card title="Top users" sub="Click a person for their own view">
            <Table rows={data.top_users} first="User" onPick={(r) => r.key !== "unattributed" && onOpen("user_id", r.key, r.label)} />
          </Card>
        )}
        {i.seats && i.seats.length > 0 && (
          <Card title="Licensed seats">
            <table className="data">
              <thead><tr><th>Tool</th><th>Last active</th></tr></thead>
              <tbody>{i.seats.map((s) => <tr key={s.tool_id}><td>{label(s.tool_id)}</td><td className="mono">{s.last_active_at ? s.last_active_at.slice(0, 10) : "never"}</td></tr>)}</tbody>
            </table>
          </Card>
        )}
      </div>
    </div>
  );
}
