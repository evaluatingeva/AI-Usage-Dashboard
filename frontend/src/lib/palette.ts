// Categorical palette (fixed order, validated for colour-vision deficiency).
// Colours are CSS variables so light/dark steps swap in one place (see styles.css).
// Colour follows the ENTITY, not its rank: each key gets a stable slot.

export const SERIES = Array.from({ length: 8 }, (_, i) => `var(--series-${i + 1})`);
export const OTHER = "var(--series-other)";

const FIXED: Record<string, number> = {
  azure_openai: 0,
  anthropic: 1,
  openai: 2,
  bedrock: 3,
  google: 4,
  "azure-ai-search": 5,
  pgvector: 6,
  github: 7,
  microsoft: 0,
  input_tokens: 0,
  output_tokens: 1,
  cached_input_tokens: 2,
  queries: 0,
};

const assigned = new Map<string, number>();

export function colorFor(key: string): string {
  if (key === "Other") return OTHER;
  if (key in FIXED) return SERIES[FIXED[key]];
  if (!assigned.has(key)) assigned.set(key, assigned.size % SERIES.length);
  return SERIES[assigned.get(key)!];
}

/** Pivot [{date,key,value}] into [{date, k1, k2, ..., Other}] keeping the top N keys. */
export function pivot(rows: { date: string; key: string; value: number }[], top = 6) {
  const totals = new Map<string, number>();
  rows.forEach((r) => totals.set(r.key, (totals.get(r.key) ?? 0) + r.value));
  const keys = [...totals.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
  const kept = keys.slice(0, top);
  const hasOther = keys.length > top;
  const byDate = new Map<string, Record<string, number | string>>();
  rows.forEach((r) => {
    const row = byDate.get(r.date) ?? { date: r.date };
    const k = kept.includes(r.key) ? r.key : "Other";
    row[k] = ((row[k] as number) ?? 0) + r.value;
    byDate.set(r.date, row);
  });
  const data = [...byDate.values()].sort((a, b) => String(a.date).localeCompare(String(b.date)));
  data.forEach((d) => [...kept, ...(hasOther ? ["Other"] : [])].forEach((k) => (d[k] ??= 0)));
  return { data, keys: hasOther ? [...kept, "Other"] : kept };
}
