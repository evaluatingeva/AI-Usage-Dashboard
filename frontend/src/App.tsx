import { useEffect, useMemo, useState } from "react";
import { api, clearSession, getUser, Me, setSession } from "./lib/api";
import { useApi } from "./lib/useApi";
import { Filters, FilterState, toParams } from "./components/Filters";
import Overview from "./pages/Overview";
import Tokens from "./pages/Tokens";
import Rag from "./pages/Rag";
import Licenses from "./pages/Licenses";
import People from "./pages/People";
import Budgets from "./pages/Budgets";
import Governance from "./pages/Governance";

const ORG_WIDE = ["admin", "finance", "auditor"];

type Page = { id: string; title: string; sub: string; roles?: string[]; filters?: boolean };
const PAGES: Page[] = [
  { id: "overview", title: "Overview", sub: "AI consumption and spend across every tool and provider", filters: true },
  { id: "tokens", title: "Tokens & models", sub: "Input, output and cached tokens by model", filters: true },
  { id: "rag", title: "RAG & retrieval", sub: "Vector search, retrieved documents and embeddings", filters: true },
  { id: "licenses", title: "Seats & non-token", sub: "Licence utilisation, idle seats and unit-based usage", roles: ["admin", "finance", "auditor", "dept_manager"], filters: true },
  { id: "people", title: "Departments & users", sub: "Who uses what: chargeback by department, team, user and engagement", filters: true },
  { id: "budgets", title: "Budgets & chargeback", sub: "Month-to-date spend against budgets", roles: ["admin", "finance", "auditor", "dept_manager"] },
  { id: "governance", title: "Governance", sub: "AI inventory, ingest keys and tamper-evident audit trail", roles: ["admin", "auditor", "finance"] },
];

function Login({ onLogin }: { onLogin: (u: Me) => void }) {
  const cfg = useApi<{ mode: string }>("/auth/config");
  const users = useApi<{ id: string; name: string; role: string; department_id: string }[]>(cfg.data?.mode === "dev" ? "/auth/dev-users" : null);
  const [err, setErr] = useState<string | null>(null);
  const login = async (id: string) => {
    try {
      const r = await api<{ access_token: string; user: Me }>("/auth/dev-login", undefined, { method: "POST", body: JSON.stringify({ user_id: id }) });
      setSession(r.access_token, r.user);
      onLogin(r.user);
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <div className="login card">
      <h2 style={{ marginTop: 0 }}>AI Usage Monitor</h2>
      {cfg.data?.mode === "oidc" ? (
        <p>Single sign-on is enabled. Integrate MSAL (see README, step 9) to obtain a token from your identity provider.</p>
      ) : (
        <>
          <p className="muted">Development sign-in: pick a demo persona to see role-based views. Disabled when AUTH_MODE=oidc.</p>
          {users.data?.map((u) => (
            <button key={u.id} className="btn user" onClick={() => login(u.id)}>
              <span>{u.name}</span>
              <span className="muted">{u.role}{u.department_id ? ` · ${u.department_id}` : ""}</span>
            </button>
          ))}
          {(users.error || cfg.error) && <p className="err">Backend not reachable: is the API running on :8000?</p>}
        </>
      )}
      {err && <p className="err">{err}</p>}
    </div>
  );
}

export default function App() {
  const [me, setMe] = useState<Me | null>(getUser());
  const [page, setPage] = useState("overview");
  const [filters, setFilters] = useState<FilterState>({ days: 30, provider: "", tool_id: "", department_id: "" });
  const params = useMemo(() => toParams(filters), [filters]);

  const [updatedAt, setUpdatedAt] = useState(new Date());
  const refresh = () => {
    window.dispatchEvent(new Event("aiu-refresh"));
    setUpdatedAt(new Date());
  };
  useEffect(() => {
    if (!me) return;
    const id = window.setInterval(refresh, 60_000); // dashboard auto-refreshes every minute
    return () => window.clearInterval(id);
  }, [me]);

  useEffect(() => {
    const out = () => setMe(null);
    window.addEventListener("aiu-logout", out);
    return () => window.removeEventListener("aiu-logout", out);
  }, []);

  if (!me) return <Login onLogin={setMe} />;
  const visible = PAGES.filter((p) => !p.roles || p.roles.includes(me.role));
  const current = visible.find((p) => p.id === page) ?? visible[0];
  const orgWide = ORG_WIDE.includes(me.role);
  const scopeNote = orgWide ? "Firm-wide view" : me.role === "dept_manager" ? `Scoped to department: ${me.department_id}` : "Showing your own usage";

  return (
    <div className="shell">
      <nav className="side" aria-label="Sections">
        <div className="brand">
          AI Usage Monitor<small>{scopeNote}</small>
        </div>
        {visible.map((p) => (
          <button key={p.id} className={`nav-btn ${p.id === current.id ? "active" : ""}`} onClick={() => setPage(p.id)}>
            {p.title}
          </button>
        ))}
        <div className="me">
          <div>{me.name}</div>
          <div>{me.role}</div>
          <button className="btn" style={{ marginTop: 8 }} onClick={() => (clearSession(), setMe(null))}>Sign out</button>
        </div>
      </nav>
      <main className="main">
        <h1 className="page-title">{current.title}</h1>
        <p className="page-sub">
          {current.sub}
          <span className="muted" style={{ marginLeft: 12, fontSize: 12 }}>
            Updated {updatedAt.toLocaleTimeString()} · auto-refreshes every minute{" "}
            <button className="btn" style={{ padding: "2px 8px", fontSize: 12 }} onClick={refresh}>Refresh</button>
          </span>
        </p>
        {current.filters && <Filters value={filters} onChange={setFilters} />}
        {current.id === "overview" && <Overview params={params} />}
        {current.id === "tokens" && <Tokens params={params} />}
        {current.id === "rag" && <Rag params={params} />}
        {current.id === "licenses" && <Licenses params={params} canSeeOrg={orgWide} canEdit={me.role === "admin" || me.role === "finance"} />}
        {current.id === "people" && <People params={params} />}
        {current.id === "budgets" && <Budgets params={params} canExport={orgWide} />}
        {current.id === "governance" && <Governance role={me.role} />}
      </main>
    </div>
  );
}
