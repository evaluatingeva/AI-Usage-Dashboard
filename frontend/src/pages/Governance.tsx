import { useState } from "react";
import { useApi } from "../lib/useApi";
import { api } from "../lib/api";
import { Card, Loading } from "../components/ui";
import Directory from "./Directory";

type Audit = { id: number; ts: string; actor: string; action: string; resource: string | null; ip: string | null; hash: string };
type Tool = { id: string; name: string; vendor: string; category: string; billing_model: string; approved: boolean; data_classification: string; owner: string | null };
type Collectors = { enabled: string[]; kinds?: Record<string, string>; latest: { collector: string; status: string; events: number; error: string | null; started_at: string; finished_at: string | null }[] };
type Key = { id: number; name: string; prefix: string; allowed_sources: string; created_by: string; created_at: string; revoked: boolean };

export default function Governance({ role }: { role: string }) {
  const isAdmin = role === "admin";
  const canAudit = role === "admin" || role === "auditor";
  const tools = useApi<Tool[]>("/admin/tools");
  const audit = useApi<Audit[]>(canAudit ? "/admin/audit" : null, { limit: 100 });
  const [verify, setVerify] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const keys = useApi<Key[]>(isAdmin ? "/admin/ingest-keys" : null, { r: refresh });
  const [newKey, setNewKey] = useState<string | null>(null);
  const collectors = useApi<Collectors>(isAdmin ? "/admin/collectors" : null, { r: refresh });
  const runNow = async (name: string) => {
    await api(`/admin/collectors/${name}/run`, undefined, { method: "POST" });
    window.setTimeout(() => setRefresh((x) => x + 1), 4000);
  };

  const runVerify = async () => {
    const r = await api<{ valid: boolean; checked: number; broken_at_id?: number }>("/admin/audit/verify");
    setVerify(r.valid ? `✓ Chain intact: ${r.checked} records verified` : `✕ Chain broken at record ${r.broken_at_id}`);
  };
  const createKey = async () => {
    const name = window.prompt?.("Name for the new ingest key (e.g. tax-copilot-prod)");
    if (!name) return;
    const r = await api<{ api_key: string }>("/admin/ingest-keys", undefined, { method: "POST", body: JSON.stringify({ name }) });
    setNewKey(r.api_key);
    setRefresh((x) => x + 1);
  };

  return (
    <div className="grid">
      <Card title="AI tool inventory" sub="Every AI tool in use, its owner and data classification (feeds your AI risk register)">
        {tools.data ? (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Tool</th><th>Vendor</th><th>Category</th><th>Billing</th><th>Classification</th><th>Approved</th><th>Owner</th></tr></thead>
              <tbody>
                {tools.data.map((t) => (
                  <tr key={t.id}><td>{t.name}</td><td>{t.vendor}</td><td>{t.category}</td><td>{t.billing_model}</td><td>{t.data_classification}</td><td>{t.approved ? "Yes" : "No"}</td><td className="muted">{t.owner}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Loading error={tools.error} />
        )}
      </Card>

      {isAdmin && (
        <Card title="Data collectors" sub="Pull usage from vendor admin APIs, and people from your HR directory, on a schedule. A failed run is retried after 5, 15 and 30 minutes.">
          {collectors.data ? (
            collectors.data.enabled.length === 0 ? (
              <p className="muted">No collectors enabled. Add vendor admin keys to .env and restart the worker (README step 5).</p>
            ) : (
              <table className="data">
                <thead><tr><th>Collector</th><th>Last run (UTC)</th><th>Status</th><th className="num">Events / people</th><th>Message</th><th></th></tr></thead>
                <tbody>
                  {collectors.data.enabled.map((name) => {
                    const r = collectors.data!.latest.find((x) => x.collector === name);
                    return (
                      <tr key={name}>
                        <td>{name}{collectors.data!.kinds?.[name] === "directory" && <div className="muted" style={{ fontSize: 11 }}>HR directory sync</div>}</td>
                        <td className="mono">{r ? r.started_at.slice(0, 19).replace("T", " ") : "not run yet"}</td>
                        <td>{r ? <span className={`badge ${r.status === "ok" ? "ok" : r.status === "error" ? "over" : "warning"}`}>{r.status === "ok" ? "✓ OK" : r.status === "error" ? "✕ Error" : "… Running"}</span> : "–"}</td>
                        <td className="num">{r?.events ?? 0}</td>
                        <td style={{ maxWidth: 520, whiteSpace: "normal" }} className={r?.status === "error" ? "err" : "muted"}>{r?.error ?? ""}</td>
                        <td><button className="btn" onClick={() => runNow(name)}>Run now</button></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )
          ) : (
            <Loading error={collectors.error} />
          )}
        </Card>
      )}

      {isAdmin && <Directory />}

      {isAdmin && (
        <Card title="Ingest API keys" sub="Used by SDKs, gateways and RAG pipelines. Only a SHA-256 hash is stored." action={<button className="btn primary" onClick={createKey}>New key</button>}>
          {newKey && (
            <p>
              New key (copy now, it is shown once): <span className="mono">{newKey}</span>
            </p>
          )}
          {keys.data ? (
            <table className="data">
              <thead><tr><th>Name</th><th>Prefix</th><th>Sources</th><th>Created by</th><th>Status</th></tr></thead>
              <tbody>
                {keys.data.map((k) => (
                  <tr key={k.id}><td>{k.name}</td><td className="mono">{k.prefix}…</td><td>{k.allowed_sources}</td><td>{k.created_by}</td><td>{k.revoked ? "Revoked" : "Active"}</td></tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Loading error={keys.error} />
          )}
        </Card>
      )}

      {canAudit && (
        <Card title="Audit log" sub="Append-only and hash-chained: every login, export and configuration change" action={<button className="btn" onClick={runVerify}>Verify integrity</button>}>
          {verify && <p>{verify}</p>}
          {audit.data ? (
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Time (UTC)</th><th>Actor</th><th>Action</th><th>Resource</th><th>IP</th><th>Hash</th></tr></thead>
                <tbody>
                  {audit.data.map((a) => (
                    <tr key={a.id}><td className="mono">{a.ts.slice(0, 19).replace("T", " ")}</td><td>{a.actor}</td><td>{a.action}</td><td>{a.resource}</td><td className="mono">{a.ip}</td><td className="mono">{a.hash}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Loading error={audit.error} />
          )}
        </Card>
      )}
    </div>
  );
}
