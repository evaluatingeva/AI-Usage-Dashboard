import { useState } from "react";
import { useApi } from "../lib/useApi";
import { fmtNum, fmtUsd, label } from "../lib/api";
import { Card, Kpi, Loading } from "../components/ui";
import { Lines } from "../components/charts";
import Profile from "./Profile";

type App = {
  application: string; status: "running" | "idle" | "inactive"; last_seen: string; queries: number; documents: number;
  embedding_tokens: number; llm_tokens: number; llm_calls: number; cost_usd: number; users: number;
  avg_retrieval_latency_ms: number; error_rate_pct: number; vector_stores: string[]; models: string[];
  environments: string[]; departments: string[];
};

const STATUS: Record<string, [string, string, string]> = {
  running: ["ok", "●", "Running"],
  idle: ["warning", "◐", "Idle"],
  inactive: ["over", "○", "Inactive"],
};

function ago(iso: string) {
  const m = Math.max(0, (Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 60) return `${Math.round(m)} min ago`;
  if (m < 48 * 60) return `${Math.round(m / 60)} h ago`;
  return `${Math.round(m / 1440)} days ago`;
}

function RagApps({ params }: { params: Record<string, string> }) {
  const { data, error } = useApi<App[]>("/analytics/rag/apps", params);
  const [sel, setSel] = useState<string | null>(null);
  const running = data?.filter((a) => a.status === "running").length ?? 0;
  return (
    <>
      <Card title="RAG applications" sub={data ? `${data.length} applications reporting · ${running} running now (usage in the last hour). Click one for its users, models and trend.` : undefined}>
        {!data ? (
          <Loading error={error} />
        ) : data.length === 0 ? (
          <p className="muted">
            No RAG application has reported yet. Each app sends its retrieval, embedding and LLM events with an <span className="mono">application</span> name
            through the aiusage SDK, the LangChain callback or the LiteLLM gateway (README, "Instrument your RAG apps").
          </p>
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr><th>Application</th><th>Status</th><th>Last activity</th><th className="num">Users</th><th className="num">Queries</th><th className="num">Docs / query</th>
                  <th className="num">Embedding tok</th><th className="num">LLM tok</th><th className="num">Latency</th><th className="num">Errors</th><th className="num">Spend</th><th>Stack</th></tr>
              </thead>
              <tbody>
                {data.map((a) => {
                  const [cls, icon, text] = STATUS[a.status];
                  return (
                    <tr key={a.application} className={`click ${sel === a.application ? "sel" : ""}`} onClick={() => setSel(sel === a.application ? null : a.application)}>
                      <td>{a.application}{a.environments.length > 0 && <div className="muted" style={{ fontSize: 11 }}>{a.environments.join(", ")}{a.departments.length ? ` · ${a.departments.join(", ")}` : ""}</div>}</td>
                      <td><span className={`badge ${cls}`}><span aria-hidden>{icon}</span>{text}</span></td>
                      <td className="muted">{ago(a.last_seen)}</td>
                      <td className="num">{a.users}</td>
                      <td className="num">{fmtNum(a.queries)}</td>
                      <td className="num">{a.queries ? (a.documents / a.queries).toFixed(1) : "–"}</td>
                      <td className="num">{fmtNum(a.embedding_tokens)}</td>
                      <td className="num">{fmtNum(a.llm_tokens)}</td>
                      <td className="num">{a.avg_retrieval_latency_ms ? `${Math.round(a.avg_retrieval_latency_ms)} ms` : "–"}</td>
                      <td className="num">{a.error_rate_pct ? `${a.error_rate_pct}%` : "0%"}</td>
                      <td className="num">{fmtUsd(a.cost_usd, 2)}</td>
                      <td>{[...a.vector_stores.map(label), ...a.models].slice(0, 4).map((x) => <span key={x} className="chip">{x}</span>)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {sel && (
        <div style={{ marginTop: 16 }}>
          <Profile dimension="application" id={sel} params={params} onOpen={() => undefined} />
        </div>
      )}
    </>
  );
}

type RagData = {
  stores: { vector_store: string; index: string | null; queries: number; documents: number; avg_latency_ms: number; cost_usd: number }[];
  embeddings: { provider: string; model: string; tokens: number; cost_usd: number }[];
  daily: { date: string; queries: number; documents: number }[];
};

export default function Rag({ params }: { params: Record<string, string> }) {
  const { data, error } = useApi<RagData>("/analytics/rag", params);
  if (!data) return <Loading error={error} />;
  const q = data.stores.reduce((s, r) => s + r.queries, 0);
  const docs = data.stores.reduce((s, r) => s + r.documents, 0);
  const embTok = data.embeddings.reduce((s, r) => s + r.tokens, 0);
  const cost = data.stores.reduce((s, r) => s + r.cost_usd, 0) + data.embeddings.reduce((s, r) => s + r.cost_usd, 0);
  return (
    <>
      <RagApps params={params} />
      <div className="grid kpis" style={{ marginTop: 16 }}>
        <Kpi label="Retrieval queries" value={fmtNum(q)} />
        <Kpi label="Documents retrieved" value={fmtNum(docs)} />
        <Kpi label="Avg docs / query" value={q ? (docs / q).toFixed(1) : "–"} />
        <Kpi label="Embedding tokens" value={fmtNum(embTok)} />
        <Kpi label="RAG spend (search + embeddings)" value={fmtUsd(cost)} />
      </div>
      <div className="grid" style={{ marginTop: 16 }}>
        <Card title="Retrieval queries per day" sub="Across all vector stores">
          <Lines data={data.daily} series={[{ key: "queries", name: "Queries" }]} fmt={fmtNum} />
        </Card>
      </div>
      <div className="grid two" style={{ marginTop: 16 }}>
        <Card title="Vector stores & indexes">
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr><th>Store</th><th>Index</th><th className="num">Queries</th><th className="num">Docs</th><th className="num">Avg latency</th><th className="num">Spend</th></tr>
              </thead>
              <tbody>
                {data.stores.map((r) => (
                  <tr key={r.vector_store + r.index}>
                    <td>{label(r.vector_store)}</td><td className="mono">{r.index}</td>
                    <td className="num">{fmtNum(r.queries)}</td><td className="num">{fmtNum(r.documents)}</td>
                    <td className="num">{r.avg_latency_ms} ms</td><td className="num">{fmtUsd(r.cost_usd, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
        <Card title="Embedding models" sub="Indexing + query-time embeddings">
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr><th>Provider</th><th>Model</th><th className="num">Tokens</th><th className="num">Spend</th></tr>
              </thead>
              <tbody>
                {data.embeddings.map((r) => (
                  <tr key={r.provider + r.model}>
                    <td>{label(r.provider)}</td><td>{r.model}</td><td className="num">{fmtNum(r.tokens)}</td><td className="num">{fmtUsd(r.cost_usd, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </>
  );
}
