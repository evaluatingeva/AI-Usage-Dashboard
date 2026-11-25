import { useState } from "react";
import { useApi } from "../lib/useApi";
import { api, getToken } from "../lib/api";
import { Card, Loading } from "../components/ui";

type U = { id: string; display_name: string; department_id: string | null; team_id: string | null; role: string; aliases: string[]; active: boolean };
type Dir = { total: number; without_department: number; departments: { id: string; name: string }[]; users: U[] };
const ROLES = ["viewer", "dept_manager", "finance", "auditor", "admin"];

/** Admin: who is in which department (from HR) and who may see what (roles). */
export default function Directory() {
  const [q, setQ] = useState("");
  const [r, setR] = useState(0);
  const [msg, setMsg] = useState<string | null>(null);
  const dir = useApi<Dir>("/admin/users", { q, limit: 300, r });

  const upload = async (file: File) => {
    setMsg("Importing…");
    const fd = new FormData();
    fd.append("file", file);
    const token = getToken();
    const res = await fetch("/api/admin/directory/import", { method: "POST", body: fd, headers: token ? { Authorization: `Bearer ${token}` } : {} });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) return setMsg(`Import failed: ${body.detail ?? res.status}`);
    setMsg(`Imported ${body.people} people (${body.created} new, ${body.updated} changed), ${body.departments} new departments, ${body.teams} new teams; ${body.events_reattributed} past usage events now attributed${body.skipped ? `; ${body.skipped} rows without email skipped` : ""}.`);
    setR((x) => x + 1);
  };
  const save = async (u: U, patch: Partial<U>) => {
    try {
      const out = await api<{ events_reattributed: number }>(`/admin/users/${encodeURIComponent(u.id)}`, undefined, { method: "PUT", body: JSON.stringify(patch) });
      setMsg(`Saved ${u.display_name}${out.events_reattributed ? `; ${out.events_reattributed} events re-attributed` : ""}.`);
      setR((x) => x + 1);
    } catch (e) {
      setMsg((e as Error).message);
    }
  };

  return (
    <Card
      title="People directory"
      sub="Who is in which department and team (synced from Workday / Entra ID, or a CSV export from any HR system) and what each person may see. Roles are set here, never by HR data."
      action={
        <label className="btn primary" style={{ cursor: "pointer", whiteSpace: "nowrap" }}>
          Import HR CSV
          <input type="file" accept=".csv" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        </label>
      }
    >
      {msg && <p className="note">{msg}</p>}
      {dir.data ? (
        <>
          <div className="filters" style={{ marginBottom: 8 }}>
            <input placeholder="Search name or email" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search people" />
            <span className="muted" style={{ fontSize: 12 }}>
              {dir.data.total} people · {dir.data.departments.length} departments · {dir.data.without_department} without a department
            </span>
          </div>
          <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
            <table className="data">
              <thead><tr><th>Name</th><th>Email / id</th><th>Department</th><th>Role</th><th>Tool aliases</th><th>Status</th></tr></thead>
              <tbody>
                {dir.data.users.map((u) => (
                  <tr key={u.id}>
                    <td>{u.display_name}</td>
                    <td className="mono">{u.id}</td>
                    <td>
                      <select value={u.department_id ?? ""} onChange={(e) => save(u, { department_id: e.target.value || null })} aria-label={`Department of ${u.display_name}`}>
                        <option value="">(none)</option>
                        {dir.data!.departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                      </select>
                    </td>
                    <td>
                      <select value={u.role} onChange={(e) => save(u, { role: e.target.value })} aria-label={`Role of ${u.display_name}`}>
                        {ROLES.map((x) => <option key={x} value={x}>{x}</option>)}
                      </select>
                    </td>
                    <td>{u.aliases.slice(0, 3).map((a) => <span key={a} className="chip">{a}</span>)}{u.aliases.length > 3 && <span className="muted"> +{u.aliases.length - 3}</span>}</td>
                    <td className={u.active ? "" : "muted"}>{u.active ? "Active" : "Inactive"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
            CSV headers: email, name, department, department_code, team, cost_center, employee_id, aliases (separated by ;), active. A dept_manager sees only their own department.
          </p>
        </>
      ) : (
        <Loading error={dir.error} />
      )}
    </Card>
  );
}
