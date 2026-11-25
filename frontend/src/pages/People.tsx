import { useState } from "react";
import { useApi } from "../lib/useApi";
import { fmtNum, fmtUsd, fmtUsdShort } from "../lib/api";
import { Card, Loading } from "../components/ui";
import { RankedBars } from "../components/charts";
import Profile from "./Profile";

type Row = { key: string; label: string; cost_usd: number; input_tokens: number; output_tokens: number; requests: number; users: number; value: number };
const DIMS = [
  ["department_id", "Department"],
  ["team_id", "Team"],
  ["user_id", "User"],
  ["project", "Engagement / project"],
] as const;
type Crumb = { dimension: string; key: string; label: string };

export default function People({ params }: { params: Record<string, string> }) {
  const [dim, setDim] = useState<string>("department_id");
  const [path, setPath] = useState<Crumb[]>([]);
  const { data, error } = useApi<Row[]>("/analytics/breakdown", { ...params, dimension: dim, metric: "cost", limit: 50 });
  const open = (dimension: string, key: string, label: string) => setPath((p) => [...p, { dimension, key, label }]);
  const current = path[path.length - 1];
  const unattributed = data?.find((r) => r.key === "unattributed");
  const total = data?.reduce((s, r) => s + r.cost_usd, 0) ?? 0;

  return (
    <>
      <div className="filters">
        <div className="seg" role="group" aria-label="Group by">
          {DIMS.map(([k, l]) => (
            <button key={k} className={dim === k && !current ? "on" : ""} onClick={() => (setDim(k), setPath([]))}>{l}</button>
          ))}
        </div>
      </div>
      {current ? (
        <>
          <div className="crumbs">
            <button onClick={() => setPath([])}>All {DIMS.find((d) => d[0] === dim)![1].toLowerCase()}s</button>
            {path.map((c, i) => (
              <span key={i}>
                › {i === path.length - 1 ? <b>{c.label}</b> : <button onClick={() => setPath(path.slice(0, i + 1))}>{c.label}</button>}
              </span>
            ))}
          </div>
          <Profile dimension={current.dimension} id={current.key} params={params} onOpen={open} />
        </>
      ) : (
        <>
          {unattributed && dim === "department_id" && total > 0 && unattributed.cost_usd / total > 0.05 && (
            <p className="note">
              {((unattributed.cost_usd / total) * 100).toFixed(0)}% of spend ({fmtUsd(unattributed.cost_usd, 2)}) has no department yet. Connect your HR directory
              (Workday, Entra ID or a CSV export) under Governance → People directory, or add the person's tool ids as aliases.
            </p>
          )}
          <div className="grid two">
            <Card title={`Spend by ${DIMS.find((d) => d[0] === dim)![1].toLowerCase()}`} sub="Top 12, USD">
              {data ? <RankedBars rows={data.slice(0, 12).map((r) => ({ label: r.label, value: r.cost_usd }))} fmt={fmtUsdShort} /> : <Loading error={error} />}
            </Card>
            <Card title="Detail" sub="Click a row to drill down">
              {data ? (
                <div className="table-wrap">
                  <table className="data">
                    <thead><tr><th>Name</th><th className="num">Users</th><th className="num">Requests</th><th className="num">Tokens</th><th className="num">Spend</th></tr></thead>
                    <tbody>
                      {data.map((r) => (
                        <tr key={r.key} className="click" onClick={() => open(dim, r.key, r.label)}>
                          <td>{r.label}</td><td className="num">{r.users}</td><td className="num">{fmtNum(r.requests)}</td>
                          <td className="num">{fmtNum(r.input_tokens + r.output_tokens)}</td><td className="num">{fmtUsd(r.cost_usd, 2)}</td>
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
        </>
      )}
    </>
  );
}
