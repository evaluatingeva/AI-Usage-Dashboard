import { useApi } from "../lib/useApi";
import { label } from "../lib/api";

export type FilterState = { days: number; provider: string; tool_id: string; department_id: string };

export function toParams(f: FilterState) {
  const end = new Date();
  const start = new Date(end.getTime() - (f.days - 1) * 86400000);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return { start: iso(start), end: iso(end), provider: f.provider, tool_id: f.tool_id, department_id: f.department_id };
}

type Opts = { providers: string[]; tools: { id: string; name: string }[]; departments: { id: string; name: string }[] };

export function Filters({ value, onChange }: { value: FilterState; onChange: (f: FilterState) => void }) {
  const { data } = useApi<Opts>("/analytics/filters");
  const set = (patch: Partial<FilterState>) => onChange({ ...value, ...patch });
  return (
    <div className="filters">
      <div className="seg" role="group" aria-label="Date range">
        {[7, 30, 90].map((d) => (
          <button key={d} className={value.days === d ? "on" : ""} onClick={() => set({ days: d })}>
            {d}d
          </button>
        ))}
      </div>
      <select value={value.provider} onChange={(e) => set({ provider: e.target.value })} aria-label="Provider">
        <option value="">All providers</option>
        {data?.providers.map((p) => (
          <option key={p} value={p}>
            {label(p)}
          </option>
        ))}
      </select>
      <select value={value.tool_id} onChange={(e) => set({ tool_id: e.target.value })} aria-label="Tool">
        <option value="">All tools</option>
        {data?.tools.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name}
          </option>
        ))}
      </select>
      {data && data.departments.length > 1 && (
        <select value={value.department_id} onChange={(e) => set({ department_id: e.target.value })} aria-label="Department">
          <option value="">All departments</option>
          {data.departments.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
